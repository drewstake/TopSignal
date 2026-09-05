"""Attach observed position excursions without assigning a whole position to every fill."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any

from sqlalchemy.orm import Session

from ..models import PositionLifecycle, ProjectXTradeEvent


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    result = float(value)
    return result if isfinite(result) else None


def attach_trade_excursions(
    db: Session, *, user_id: str, account_id: int, trades: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """One bounded batch query per table; only unambiguous one-fill positions feed trade metrics.

    Position timestamps are provider observations, not guaranteed fill timestamps. A
    one-second boundary tolerance permits timestamp rounding, never nearest-position
    matching. Multiple fills retain position-level values with an explicit scope.
    """
    for trade in trades:
        trade.update(mae=None, mfe=None, excursion_scope="unavailable", position_mae=None,
                     position_mfe=None, excursion_source=None)
    if not trades:
        return trades
    ends = [_utc(trade["exit_time"]) for trade in trades]
    tolerance = timedelta(seconds=1)
    lives = (db.query(PositionLifecycle)
             .filter(PositionLifecycle.user_id == user_id, PositionLifecycle.account_id == account_id,
                     PositionLifecycle.contract_id.in_({trade["contract_id"] for trade in trades}),
                     PositionLifecycle.opened_at <= max(ends) + tolerance,
                     PositionLifecycle.closed_at >= min(ends) - tolerance)
             .order_by(PositionLifecycle.opened_at).all())
    if not lives:
        return trades
    # Count all stored closing fills, including ones outside this API page/filter.
    events = (db.query(ProjectXTradeEvent.id, ProjectXTradeEvent.contract_id, ProjectXTradeEvent.trade_timestamp)
              .filter(ProjectXTradeEvent.user_id == user_id, ProjectXTradeEvent.account_id == account_id,
                      ProjectXTradeEvent.pnl.isnot(None),
                      ProjectXTradeEvent.contract_id.in_({life.contract_id for life in lives}),
                      ProjectXTradeEvent.trade_timestamp >= min(_utc(life.opened_at) for life in lives) - tolerance,
                      ProjectXTradeEvent.trade_timestamp <= max(_utc(life.closed_at) for life in lives) + tolerance)
              .all())
    for trade in trades:
        end = _utc(trade["exit_time"])
        start = _utc(trade["entry_time"]) if trade.get("entry_time") else None
        candidates = [life for life in lives if life.contract_id == trade["contract_id"]
                      and _utc(life.opened_at) - tolerance <= end <= _utc(life.closed_at) + tolerance
                      and (start is None or _utc(life.opened_at) - tolerance <= start <= _utc(life.closed_at))]
        if len(candidates) != 1:
            if candidates:
                trade["excursion_scope"] = "ambiguous"
            continue
        life = candidates[0]
        mae, mfe = _number(life.mae_usd), _number(life.mfe_usd)
        if mae is None and mfe is None:
            continue
        if mae == 0 and mfe == 0 and all(value is None or _utc(value) == _utc(life.opened_at)
                                         for value in (life.mae_timestamp, life.mfe_timestamp)):
            # The tracker initializes zeros before any mark arrives. Without a
            # later excursion timestamp they cannot establish observed coverage.
            continue
        trade.update(position_mae=mae, position_mfe=mfe, excursion_scope="position",
                     excursion_source="observed_stream")
        closed_ids = {event.id for event in events if event.contract_id == life.contract_id
                      and _utc(life.opened_at) - tolerance <= _utc(event.trade_timestamp) <= _utc(life.closed_at) + tolerance}
        single = (closed_ids == {trade["id"]} and start is not None
                  and abs((start - _utc(life.opened_at)).total_seconds()) <= 1
                  and abs((end - _utc(life.closed_at)).total_seconds()) <= 1
                  and abs(float(trade["size"]) - float(life.max_qty)) < 1e-9)
        if single:
            trade.update(mae=mae, mfe=mfe, excursion_scope="trade")
    return trades
