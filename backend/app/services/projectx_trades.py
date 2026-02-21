from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import ProjectXTradeEvent
from .projectx_client import ProjectXClient
from .projectx_metrics import TradeMetricSample, compute_daily_pnl_calendar, compute_trade_summary

_DEFAULT_INITIAL_LOOKBACK_DAYS = 365
_DEFAULT_SYNC_CHUNK_DAYS = 90
_INCREMENTAL_OVERLAP = timedelta(minutes=5)


def refresh_account_trades(
    db: Session,
    client: ProjectXClient,
    account_id: int,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    lookback_days: int = _DEFAULT_INITIAL_LOOKBACK_DAYS,
) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    end_utc = _as_utc(end) if end is not None else now
    effective_lookback_days = _read_int_env("PROJECTX_INITIAL_LOOKBACK_DAYS", lookback_days)
    chunk_days = _read_int_env("PROJECTX_SYNC_CHUNK_DAYS", _DEFAULT_SYNC_CHUNK_DAYS)
    latest = get_latest_trade_timestamp(db, account_id) if start is None else None
    earliest = get_earliest_trade_timestamp(db, account_id) if start is None else None

    windows = _build_sync_windows(
        start=start,
        end=end_utc,
        now=now,
        latest_local=latest,
        earliest_local=earliest,
        lookback_days=effective_lookback_days,
    )

    fetched_count = 0
    inserted_count = 0
    for window_start, window_end in windows:
        for chunk_start, chunk_end in _iter_time_chunks(
            window_start,
            window_end,
            chunk_days=chunk_days,
        ):
            events = client.fetch_trade_history(
                account_id=account_id,
                start=chunk_start,
                end=chunk_end,
            )
            fetched_count += len(events)
            inserted_count += store_trade_events(db, events)

    return {
        "fetched_count": fetched_count,
        "inserted_count": inserted_count,
    }


def has_local_trades(db: Session, account_id: int) -> bool:
    existing = (
        db.query(ProjectXTradeEvent.id)
        .filter(ProjectXTradeEvent.account_id == account_id)
        .filter(_non_voided_trade_event_expr())
        .limit(1)
        .first()
    )
    return existing is not None


def get_latest_trade_timestamp(db: Session, account_id: int) -> datetime | None:
    value = (
        db.query(func.max(ProjectXTradeEvent.trade_timestamp))
        .filter(ProjectXTradeEvent.account_id == account_id)
        .filter(_non_voided_trade_event_expr())
        .scalar()
    )
    if value is None:
        return None
    return _as_utc(value)


def get_earliest_trade_timestamp(db: Session, account_id: int) -> datetime | None:
    value = (
        db.query(func.min(ProjectXTradeEvent.trade_timestamp))
        .filter(ProjectXTradeEvent.account_id == account_id)
        .filter(_non_voided_trade_event_expr())
        .scalar()
    )
    if value is None:
        return None
    return _as_utc(value)


def store_trade_events(db: Session, events: list[dict[str, Any]]) -> int:
    if not events:
        return 0

    non_voided_events = [event for event in events if not _event_is_voided(event)]
    if not non_voided_events:
        return 0

    account_ids = sorted({int(event["account_id"]) for event in non_voided_events})
    timestamps = [_as_utc(event["timestamp"]) for event in non_voided_events]
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

    for event in sorted(non_voided_events, key=lambda item: (_as_utc(item["timestamp"]), str(item["order_id"]))):
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
    query = (
        db.query(ProjectXTradeEvent)
        .filter(ProjectXTradeEvent.account_id == account_id)
        .filter(_non_voided_trade_event_expr())
        # Topstep day journal rows are closed trades only.
        .filter(ProjectXTradeEvent.pnl.isnot(None))
    )

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
    samples = _load_trade_metric_samples(db, account_id=account_id, start=start, end=end)
    return compute_trade_summary(samples)


def get_trade_event_pnl_calendar(
    db: Session,
    account_id: int,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict[str, str | int | float]]:
    samples = _load_trade_metric_samples(db, account_id=account_id, start=start, end=end)
    return compute_daily_pnl_calendar(samples)


def serialize_trade_event(row: ProjectXTradeEvent) -> dict[str, Any]:
    symbol = row.symbol or row.contract_id
    fees = _normalized_trade_fees(row)
    return {
        "id": int(row.id),
        "account_id": int(row.account_id),
        "contract_id": row.contract_id,
        "symbol": symbol,
        "side": row.side,
        "size": float(row.size),
        "price": float(row.price),
        "timestamp": _as_utc(row.trade_timestamp),
        "fees": fees,
        "pnl": float(row.pnl) if row.pnl is not None else None,
        "order_id": row.order_id,
        "source_trade_id": row.source_trade_id,
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _build_sync_windows(
    *,
    start: datetime | None,
    end: datetime,
    now: datetime,
    latest_local: datetime | None,
    earliest_local: datetime | None,
    lookback_days: int,
) -> list[tuple[datetime, datetime]]:
    end_utc = _as_utc(end)
    effective_lookback_days = max(1, int(lookback_days))

    if start is not None:
        start_utc = _as_utc(start)
        if start_utc > end_utc:
            raise ValueError("start must be before end")
        return [(start_utc, end_utc)]

    history_floor = _as_utc(now) - timedelta(days=effective_lookback_days)
    if latest_local is None:
        if history_floor > end_utc:
            raise ValueError("start must be before end")
        return [(history_floor, end_utc)]

    windows: list[tuple[datetime, datetime]] = []
    latest_utc = _as_utc(latest_local)
    earliest_utc = _as_utc(earliest_local) if earliest_local is not None else None

    # Backfill older history if the earliest local event is newer than the lookback floor.
    if earliest_utc is not None and earliest_utc > history_floor:
        windows.append((history_floor, earliest_utc))

    windows.append((latest_utc - _INCREMENTAL_OVERLAP, end_utc))

    normalized_windows: list[tuple[datetime, datetime]] = []
    for window_start, window_end in windows:
        if window_start > window_end:
            continue
        normalized_windows.append((window_start, window_end))
    return normalized_windows


def _iter_time_chunks(
    start: datetime,
    end: datetime,
    *,
    chunk_days: int,
) -> list[tuple[datetime, datetime]]:
    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    if start_utc > end_utc:
        return []

    span_days = max(1, int(chunk_days))
    span = timedelta(days=span_days)
    chunks: list[tuple[datetime, datetime]] = []
    cursor = start_utc

    while cursor <= end_utc:
        chunk_end = min(end_utc, cursor + span)
        chunks.append((cursor, chunk_end))
        if chunk_end >= end_utc:
            break
        cursor = chunk_end + timedelta(microseconds=1)

    return chunks


def _read_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _load_trade_metric_samples(
    db: Session,
    *,
    account_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[TradeMetricSample]:
    query = (
        db.query(ProjectXTradeEvent)
        .filter(ProjectXTradeEvent.account_id == account_id)
        .filter(_non_voided_trade_event_expr())
    )
    if start is not None:
        query = query.filter(ProjectXTradeEvent.trade_timestamp >= _as_utc(start))
    if end is not None:
        query = query.filter(ProjectXTradeEvent.trade_timestamp <= _as_utc(end))

    rows = query.order_by(ProjectXTradeEvent.trade_timestamp.asc(), ProjectXTradeEvent.id.asc()).all()
    return [_to_metric_sample(row) for row in rows]


def _to_metric_sample(row: ProjectXTradeEvent) -> TradeMetricSample:
    return TradeMetricSample(
        timestamp=_as_utc(row.trade_timestamp),
        pnl=float(row.pnl) if row.pnl is not None else None,
        fees=_normalized_trade_fees(row),
        symbol=row.symbol or row.contract_id,
        side=row.side,
        size=float(row.size) if row.size is not None else None,
        price=float(row.price) if row.price is not None else None,
    )


def _normalized_trade_fees(row: ProjectXTradeEvent) -> float:
    fees = float(row.fees) if row.fees is not None else 0.0
    # ProjectX Trade/search reports fees per fill leg. For rows with realized
    # PnL (the closing leg), mirror Topstep's per-trade fee by including both
    # entry and exit sides.
    if row.pnl is not None:
        fees *= 2
    return fees


def _event_is_voided(event: dict[str, Any]) -> bool:
    raw_payload = event.get("raw_payload")
    if isinstance(raw_payload, dict):
        if _is_truthy(raw_payload.get("voided")):
            return True

    if _is_truthy(event.get("voided")):
        return True

    return False


def _non_voided_trade_event_expr():
    # ProjectX marks canceled/invalid rows as `raw_payload.voided = true`.
    # Excluding these keeps local metrics aligned with Topstep's day journal.
    voided_text = func.lower(func.coalesce(ProjectXTradeEvent.raw_payload.op("->>")("voided"), "false"))
    return voided_text != "true"


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False
