from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import ProjectXTradeDaySync, ProjectXTradeEvent
from .projectx_client import ProjectXClient
from .projectx_metrics import TradeMetricSample, compute_daily_pnl_calendar, compute_trade_summary

logger = logging.getLogger(__name__)

_DEFAULT_INITIAL_LOOKBACK_DAYS = 365
_DEFAULT_SYNC_CHUNK_DAYS = 90
_DEFAULT_DAY_SYNC_LIMIT = 1000
_DEFAULT_YESTERDAY_REFRESH_MINUTES = 180
_INCREMENTAL_OVERLAP = timedelta(minutes=5)
_MAX_DAY_SYNC_PAGES = 200
_SYNC_STATUS_PARTIAL = "partial"
_SYNC_STATUS_COMPLETE = "complete"


@dataclass(frozen=True)
class _DayFetchResult:
    events: list[dict[str, Any]]
    page_count: int
    is_truncated: bool
    truncation_count: int


def ensure_trade_cache_for_request(
    db: Session,
    *,
    account_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
    refresh: bool = False,
    client_factory: Callable[[], ProjectXClient],
) -> None:
    request_day = _single_day_request_utc_date(start=start, end=end)
    if request_day is None:
        if refresh or not has_local_trades(db, account_id):
            refresh_account_trades(
                db,
                client_factory(),
                account_id=account_id,
                start=start,
                end=end,
            )
        return

    _sync_single_trade_day_if_needed(
        db,
        client_factory=client_factory,
        account_id=account_id,
        trade_day=request_day,
        refresh=refresh,
    )


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
    try:
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
                db.commit()
    except Exception:
        db.rollback()
        raise

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

    events_sorted = sorted(
        non_voided_events,
        key=lambda item: (_as_utc(item["timestamp"]), str(item.get("order_id") or "")),
    )

    account_ids = sorted({int(event["account_id"]) for event in events_sorted})
    timestamps = [_as_utc(event["timestamp"]) for event in events_sorted]
    source_ids = sorted(
        {
            source_id
            for source_id in (_normalized_optional_text(event.get("source_trade_id")) for event in events_sorted)
            if source_id
        }
    )

    existing_by_source: dict[tuple[int, str], ProjectXTradeEvent] = {}
    if source_ids:
        source_rows = (
            db.query(ProjectXTradeEvent)
            .filter(ProjectXTradeEvent.account_id.in_(account_ids))
            .filter(ProjectXTradeEvent.source_trade_id.in_(source_ids))
            .all()
        )
        existing_by_source = {
            (int(row.account_id), str(row.source_trade_id)): row
            for row in source_rows
            if row.source_trade_id is not None
        }

    min_ts = min(timestamps)
    max_ts = max(timestamps)
    fallback_rows = (
        db.query(ProjectXTradeEvent)
        .filter(ProjectXTradeEvent.account_id.in_(account_ids))
        .filter(ProjectXTradeEvent.trade_timestamp >= min_ts)
        .filter(ProjectXTradeEvent.trade_timestamp <= max_ts)
        .all()
    )
    existing_by_fallback = {
        (int(row.account_id), str(row.order_id), _as_utc(row.trade_timestamp)): row
        for row in fallback_rows
    }

    inserted_count = 0
    for event in events_sorted:
        account_id = int(event["account_id"])
        timestamp = _as_utc(event["timestamp"])
        order_id = str(event["order_id"])
        source_trade_id = _normalized_optional_text(event.get("source_trade_id"))

        row: ProjectXTradeEvent | None = None
        if source_trade_id:
            row = existing_by_source.get((account_id, source_trade_id))

        if row is None:
            row = existing_by_fallback.get((account_id, order_id, timestamp))

        if row is None:
            row = ProjectXTradeEvent(
                account_id=account_id,
                contract_id=str(event["contract_id"]),
                side="UNKNOWN",
                size=0.0,
                price=0.0,
                trade_timestamp=timestamp,
                fees=0.0,
                order_id=order_id,
            )
            db.add(row)
            inserted_count += 1

        _apply_event_to_trade_row(row, event)

        fallback_key = (int(row.account_id), str(row.order_id), _as_utc(row.trade_timestamp))
        existing_by_fallback[fallback_key] = row
        if row.source_trade_id:
            existing_by_source[(int(row.account_id), str(row.source_trade_id))] = row

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


def _sync_single_trade_day_if_needed(
    db: Session,
    *,
    client_factory: Callable[[], ProjectXClient],
    account_id: int,
    trade_day: date,
    refresh: bool,
) -> None:
    now_utc = datetime.now(timezone.utc)
    today_utc = now_utc.date()
    yesterday_utc = today_utc - timedelta(days=1)
    sync_row = _get_trade_day_sync(db, account_id=account_id, trade_day=trade_day)

    if trade_day == today_utc:
        logger.info("[trades] refresh today account=%s day=%s", account_id, trade_day.isoformat())
        _sync_trade_day_from_provider(
            db,
            client_factory=client_factory,
            account_id=account_id,
            trade_day=trade_day,
            allow_complete=False,
        )
        return

    if trade_day == yesterday_utc:
        if refresh or _should_refresh_yesterday(sync_row, now_utc=now_utc):
            logger.info("[trades] refresh yesterday account=%s day=%s", account_id, trade_day.isoformat())
            _sync_trade_day_from_provider(
                db,
                client_factory=client_factory,
                account_id=account_id,
                trade_day=trade_day,
                allow_complete=True,
            )
            return

        logger.info("[trades] cache hit account=%s day=%s source=db", account_id, trade_day.isoformat())
        return

    if not refresh and sync_row is not None and sync_row.sync_status == _SYNC_STATUS_COMPLETE:
        logger.info("[trades] cache hit account=%s day=%s source=db", account_id, trade_day.isoformat())
        return

    logger.info("[trades] cache miss account=%s day=%s source=provider", account_id, trade_day.isoformat())
    _sync_trade_day_from_provider(
        db,
        client_factory=client_factory,
        account_id=account_id,
        trade_day=trade_day,
        allow_complete=True,
    )


def _sync_trade_day_from_provider(
    db: Session,
    *,
    client_factory: Callable[[], ProjectXClient],
    account_id: int,
    trade_day: date,
    allow_complete: bool,
) -> None:
    day_start, day_end = _utc_day_bounds(trade_day)
    page_limit = _read_int_env("PROJECTX_DAY_SYNC_LIMIT", _DEFAULT_DAY_SYNC_LIMIT)
    last_synced_at = datetime.now(timezone.utc)

    try:
        fetch_result = _fetch_trade_day_all_pages(
            client_factory(),
            account_id=account_id,
            start=day_start,
            end=day_end,
            limit=page_limit,
        )
    except Exception:
        _mark_trade_day_partial(
            db,
            account_id=account_id,
            trade_day=trade_day,
            last_synced_at=last_synced_at,
        )
        logger.exception("[trades] partial sync / sync failed account=%s day=%s", account_id, trade_day.isoformat())
        raise

    sync_status = _SYNC_STATUS_COMPLETE
    if fetch_result.is_truncated or not allow_complete:
        sync_status = _SYNC_STATUS_PARTIAL

    try:
        store_trade_events(db, fetch_result.events)
        row_count = _count_trade_events_for_day(db, account_id=account_id, trade_day=trade_day)
        _upsert_trade_day_sync(
            db,
            account_id=account_id,
            trade_day=trade_day,
            sync_status=sync_status,
            last_synced_at=last_synced_at,
            row_count=row_count,
        )
        db.commit()
    except Exception:
        db.rollback()
        _mark_trade_day_partial(
            db,
            account_id=account_id,
            trade_day=trade_day,
            last_synced_at=last_synced_at,
        )
        logger.exception("[trades] partial sync / sync failed account=%s day=%s", account_id, trade_day.isoformat())
        raise


def _fetch_trade_day_all_pages(
    client: ProjectXClient,
    *,
    account_id: int,
    start: datetime,
    end: datetime,
    limit: int,
) -> _DayFetchResult:
    page_limit = max(1, int(limit))
    offset = 0
    page_count = 0
    events: list[dict[str, Any]] = []
    seen_signatures: set[tuple[str, ...]] = set()

    while True:
        page_rows = client.fetch_trade_history(
            account_id=account_id,
            start=start,
            end=end,
            limit=page_limit,
            offset=offset,
        )
        page_count += 1
        page_size = len(page_rows)
        signature = _trade_page_signature(page_rows)

        if page_size == page_limit and offset > 0 and signature is not None and signature in seen_signatures:
            logger.warning(
                "[trades] warning truncation account=%s day=%s count=%s limit=%s, not marking complete",
                account_id,
                _as_utc(start).date().isoformat(),
                page_size,
                page_limit,
            )
            deduped = _dedupe_trade_events(events)
            return _DayFetchResult(
                events=deduped,
                page_count=page_count,
                is_truncated=True,
                truncation_count=page_size,
            )

        if signature is not None:
            seen_signatures.add(signature)

        events.extend(page_rows)

        if page_size < page_limit:
            break

        offset += page_limit
        if page_count >= _MAX_DAY_SYNC_PAGES:
            logger.warning(
                "[trades] warning truncation account=%s day=%s count=%s limit=%s, not marking complete",
                account_id,
                _as_utc(start).date().isoformat(),
                page_size,
                page_limit,
            )
            deduped = _dedupe_trade_events(events)
            return _DayFetchResult(
                events=deduped,
                page_count=page_count,
                is_truncated=True,
                truncation_count=page_size,
            )

    deduped = _dedupe_trade_events(events)
    return _DayFetchResult(events=deduped, page_count=page_count, is_truncated=False, truncation_count=0)


def _trade_page_signature(events: list[dict[str, Any]]) -> tuple[str, ...] | None:
    if not events:
        return None
    keys = [_event_identity_key(event) for event in events]
    return tuple(keys)


def _dedupe_trade_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for event in events:
        by_key[_event_identity_key(event)] = event
    deduped = list(by_key.values())
    deduped.sort(key=lambda item: (_as_utc(item["timestamp"]), str(item.get("order_id") or "")))
    return deduped


def _event_identity_key(event: dict[str, Any]) -> str:
    account_id = int(event["account_id"])
    source_trade_id = _normalized_optional_text(event.get("source_trade_id"))
    if source_trade_id:
        return f"{account_id}:source:{source_trade_id}"
    timestamp = _as_utc(event["timestamp"]).isoformat()
    order_id = str(event.get("order_id") or "")
    return f"{account_id}:fallback:{order_id}:{timestamp}"


def _should_refresh_yesterday(sync_row: ProjectXTradeDaySync | None, *, now_utc: datetime) -> bool:
    if sync_row is None:
        return True
    if sync_row.sync_status != _SYNC_STATUS_COMPLETE:
        return True
    if sync_row.last_synced_at is None:
        return True

    max_age_minutes = _read_int_env("PROJECTX_YESTERDAY_REFRESH_MINUTES", _DEFAULT_YESTERDAY_REFRESH_MINUTES)
    last_synced_at = _as_utc(sync_row.last_synced_at)
    return last_synced_at < (now_utc - timedelta(minutes=max_age_minutes))


def _get_trade_day_sync(db: Session, *, account_id: int, trade_day: date) -> ProjectXTradeDaySync | None:
    return (
        db.query(ProjectXTradeDaySync)
        .filter(ProjectXTradeDaySync.account_id == account_id)
        .filter(ProjectXTradeDaySync.trade_date == trade_day)
        .one_or_none()
    )


def _upsert_trade_day_sync(
    db: Session,
    *,
    account_id: int,
    trade_day: date,
    sync_status: str,
    last_synced_at: datetime,
    row_count: int | None = None,
) -> None:
    sync_row = _get_trade_day_sync(db, account_id=account_id, trade_day=trade_day)
    if sync_row is None:
        sync_row = ProjectXTradeDaySync(
            account_id=account_id,
            trade_date=trade_day,
            sync_status=sync_status,
            last_synced_at=last_synced_at,
            row_count=row_count,
            updated_at=last_synced_at,
        )
        db.add(sync_row)
        return

    sync_row.sync_status = sync_status
    sync_row.last_synced_at = last_synced_at
    sync_row.row_count = row_count
    sync_row.updated_at = last_synced_at


def _mark_trade_day_partial(
    db: Session,
    *,
    account_id: int,
    trade_day: date,
    last_synced_at: datetime,
) -> None:
    try:
        row_count = _count_trade_events_for_day(db, account_id=account_id, trade_day=trade_day)
        _upsert_trade_day_sync(
            db,
            account_id=account_id,
            trade_day=trade_day,
            sync_status=_SYNC_STATUS_PARTIAL,
            last_synced_at=last_synced_at,
            row_count=row_count,
        )
        db.commit()
    except Exception:
        db.rollback()


def _single_day_request_utc_date(*, start: datetime | None, end: datetime | None) -> date | None:
    if start is None or end is None:
        return None

    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    if start_utc > end_utc:
        return None
    if start_utc.date() != end_utc.date():
        return None

    return start_utc.date()


def _utc_day_bounds(value: date) -> tuple[datetime, datetime]:
    start = datetime.combine(value, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1) - timedelta(microseconds=1)
    return start, end


def _count_trade_events_for_day(db: Session, *, account_id: int, trade_day: date) -> int:
    day_start, day_end = _utc_day_bounds(trade_day)
    count = (
        db.query(func.count(ProjectXTradeEvent.id))
        .filter(ProjectXTradeEvent.account_id == account_id)
        .filter(_non_voided_trade_event_expr())
        .filter(ProjectXTradeEvent.trade_timestamp >= day_start)
        .filter(ProjectXTradeEvent.trade_timestamp <= day_end)
        .scalar()
    )
    return int(count or 0)


def _apply_event_to_trade_row(row: ProjectXTradeEvent, event: dict[str, Any]) -> None:
    row.account_id = int(event["account_id"])
    row.contract_id = str(event["contract_id"])
    row.symbol = event.get("symbol")
    row.side = str(event.get("side") or "UNKNOWN")
    row.size = float(event.get("size") or 0.0)
    row.price = float(event.get("price") or 0.0)
    row.trade_timestamp = _as_utc(event["timestamp"])
    row.fees = float(event.get("fees") or 0.0)
    row.pnl = float(event["pnl"]) if event.get("pnl") is not None else None
    row.order_id = str(event["order_id"])
    source_trade_id = _normalized_optional_text(event.get("source_trade_id"))
    if source_trade_id:
        row.source_trade_id = source_trade_id
    status = _normalized_optional_text(event.get("status"))
    if status:
        row.status = status
    row.raw_payload = event.get("raw_payload")


def _normalized_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


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
        order_id=row.order_id,
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
