from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import ProjectXTradeEvent
from .projectx_client import ProjectXClient
from .projectx_metrics import TradeMetricSample, compute_trade_summary


def refresh_account_trades(
    db: Session,
    client: ProjectXClient,
    account_id: int,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    lookback_days: int = 30,
) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    end_utc = _as_utc(end) if end is not None else now

    if start is None:
        latest = get_latest_trade_timestamp(db, account_id)
        if latest is not None:
            start_utc = latest - timedelta(minutes=5)
        else:
            start_utc = now - timedelta(days=lookback_days)
    else:
        start_utc = _as_utc(start)

    if start_utc > end_utc:
        raise ValueError("start must be before end")

    events = client.fetch_trade_history(account_id=account_id, start=start_utc, end=end_utc)
    inserted_count = store_trade_events(db, events)
    return {
        "fetched_count": len(events),
        "inserted_count": inserted_count,
    }


def has_local_trades(db: Session, account_id: int) -> bool:
    existing = (
        db.query(ProjectXTradeEvent.id)
        .filter(ProjectXTradeEvent.account_id == account_id)
        .limit(1)
        .first()
    )
    return existing is not None


def get_latest_trade_timestamp(db: Session, account_id: int) -> datetime | None:
    value = (
        db.query(func.max(ProjectXTradeEvent.trade_timestamp))
        .filter(ProjectXTradeEvent.account_id == account_id)
        .scalar()
    )
    if value is None:
        return None
    return _as_utc(value)


def store_trade_events(db: Session, events: list[dict[str, Any]]) -> int:
    if not events:
        return 0

    account_ids = sorted({int(event["account_id"]) for event in events})
    timestamps = [_as_utc(event["timestamp"]) for event in events]
    min_ts = min(timestamps)
    max_ts = max(timestamps)

    existing_rows = (
        db.query(
            ProjectXTradeEvent.account_id,
            ProjectXTradeEvent.order_id,
            ProjectXTradeEvent.trade_timestamp,
        )
        .filter(ProjectXTradeEvent.account_id.in_(account_ids))
        .filter(ProjectXTradeEvent.trade_timestamp >= min_ts)
        .filter(ProjectXTradeEvent.trade_timestamp <= max_ts)
        .all()
    )

    existing_keys = {
        (int(row.account_id), str(row.order_id), _as_utc(row.trade_timestamp))
        for row in existing_rows
    }

    inserted_count = 0

    for event in sorted(events, key=lambda item: (_as_utc(item["timestamp"]), str(item["order_id"]))):
        timestamp = _as_utc(event["timestamp"])
        dedupe_key = (int(event["account_id"]), str(event["order_id"]), timestamp)
        if dedupe_key in existing_keys:
            continue

        db.add(
            ProjectXTradeEvent(
                account_id=int(event["account_id"]),
                contract_id=str(event["contract_id"]),
                symbol=event.get("symbol"),
                side=str(event.get("side") or "UNKNOWN"),
                size=float(event.get("size") or 0.0),
                price=float(event.get("price") or 0.0),
                trade_timestamp=timestamp,
                fees=float(event.get("fees") or 0.0),
                pnl=float(event["pnl"]) if event.get("pnl") is not None else None,
                order_id=str(event["order_id"]),
                source_trade_id=event.get("source_trade_id"),
                raw_payload=event.get("raw_payload"),
            )
        )
        existing_keys.add(dedupe_key)
        inserted_count += 1

    if inserted_count > 0:
        db.commit()

    return inserted_count


def list_trade_events(
    db: Session,
    account_id: int,
    *,
    limit: int,
    start: datetime | None = None,
    end: datetime | None = None,
    symbol_query: str | None = None,
) -> list[ProjectXTradeEvent]:
    query = db.query(ProjectXTradeEvent).filter(ProjectXTradeEvent.account_id == account_id)

    if start is not None:
        query = query.filter(ProjectXTradeEvent.trade_timestamp >= _as_utc(start))
    if end is not None:
        query = query.filter(ProjectXTradeEvent.trade_timestamp <= _as_utc(end))
    if symbol_query:
        normalized = symbol_query.strip().lower()
        if normalized:
            symbol_expr = func.lower(func.coalesce(ProjectXTradeEvent.symbol, ProjectXTradeEvent.contract_id))
            query = query.filter(symbol_expr.contains(normalized))

    return (
        query.order_by(ProjectXTradeEvent.trade_timestamp.desc(), ProjectXTradeEvent.id.desc())
        .limit(limit)
        .all()
    )


def summarize_trade_events(
    db: Session,
    account_id: int,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, float | int]:
    query = db.query(ProjectXTradeEvent).filter(ProjectXTradeEvent.account_id == account_id)
    if start is not None:
        query = query.filter(ProjectXTradeEvent.trade_timestamp >= _as_utc(start))
    if end is not None:
        query = query.filter(ProjectXTradeEvent.trade_timestamp <= _as_utc(end))

    rows = query.order_by(ProjectXTradeEvent.trade_timestamp.asc(), ProjectXTradeEvent.id.asc()).all()
    samples = [
        TradeMetricSample(
            timestamp=_as_utc(row.trade_timestamp),
            pnl=float(row.pnl) if row.pnl is not None else None,
            fees=float(row.fees) if row.fees is not None else 0.0,
            symbol=row.symbol or row.contract_id,
            side=row.side,
            size=float(row.size) if row.size is not None else None,
            price=float(row.price) if row.price is not None else None,
        )
        for row in rows
    ]
    return compute_trade_summary(samples)


def serialize_trade_event(row: ProjectXTradeEvent) -> dict[str, Any]:
    symbol = row.symbol or row.contract_id
    return {
        "id": int(row.id),
        "account_id": int(row.account_id),
        "contract_id": row.contract_id,
        "symbol": symbol,
        "side": row.side,
        "size": float(row.size),
        "price": float(row.price),
        "timestamp": _as_utc(row.trade_timestamp),
        "fees": float(row.fees) if row.fees is not None else 0.0,
        "pnl": float(row.pnl) if row.pnl is not None else None,
        "order_id": row.order_id,
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
