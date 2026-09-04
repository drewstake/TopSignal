from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Iterator, Mapping, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..models import DatabentoInstrument, DatabentoOhlcv1m, DatabentoRollSchedule
from .databento_ingestion import MNQ_HISTORY_START_UTC, SUPPORTED_DATASET
from .trading_day import as_utc, futures_session_is_open, trading_day_bounds_utc


ROLL_POLICY_VERSION = "volume_previous_completed_session_v1"
PRICE_SCALE = 1_000_000_000
REPLAY_QUERY_CHUNK_SIZE = 8_192


class DatabentoMarketDataError(ValueError):
    pass


@dataclass(frozen=True)
class RolloverContract:
    instrument_id: int
    raw_symbol: str
    activation: datetime | None
    expiration: datetime | None


@dataclass(frozen=True)
class RolloverDecision:
    root_symbol: str
    trading_date: date
    dataset: str
    instrument_id: int
    raw_symbol: str
    decision_session_date: date | None
    from_instrument_id: int | None
    current_volume: int | None
    candidate_volume: int | None
    reason: str
    policy_version: str = ROLL_POLICY_VERSION


@dataclass(frozen=True, slots=True)
class _RawContinuousBar:
    timestamp: datetime
    instrument_id: int
    raw_symbol: str
    open_nano: int
    high_nano: int
    low_nano: int
    close_nano: int
    volume: int
    source_file_sha256: str
    roll_policy_version: str = ROLL_POLICY_VERSION


@dataclass(slots=True)
class DatabentoReplayCandle:
    """Lightweight, global Databento bar projected into the replay candle contract."""

    user_id: Any
    contract_id: str
    symbol: str
    live: bool
    unit: str
    unit_number: int
    candle_timestamp: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int
    is_partial: bool
    raw_payload: None
    fetched_at: None
    source: str
    source_instrument_id: int
    source_raw_symbol: str
    source_file_sha256: str
    roll_policy_version: str
    nominal_close_time: datetime


def rebuild_volume_roll_schedule(
    db: Session,
    *,
    root_symbol: str,
    dataset: str = SUPPORTED_DATASET,
) -> list[RolloverDecision]:
    """Recompute a prefix-invariant schedule from completed-session volumes."""

    root = str(root_symbol).strip().upper()
    contracts = [
        RolloverContract(
            instrument_id=int(row.instrument_id),
            raw_symbol=str(row.raw_symbol),
            activation=_optional_utc(row.activation),
            expiration=_optional_utc(row.expiration),
        )
        for row in db.execute(
            select(DatabentoInstrument)
            .where(DatabentoInstrument.dataset == dataset)
            .where(DatabentoInstrument.root_symbol == root)
            .where(DatabentoInstrument.instrument_class == "F")
        ).scalars()
    ]
    if not contracts:
        raise DatabentoMarketDataError(f"databento_contracts_missing:{root}")

    daily_volumes: dict[date, dict[int, int]] = {}
    volume_rows = db.execute(
        select(
            DatabentoOhlcv1m.trading_date,
            DatabentoOhlcv1m.instrument_id,
            func.sum(DatabentoOhlcv1m.volume),
        )
        .join(
            DatabentoInstrument,
            (DatabentoInstrument.dataset == DatabentoOhlcv1m.dataset)
            & (DatabentoInstrument.instrument_id == DatabentoOhlcv1m.instrument_id),
        )
        .where(DatabentoOhlcv1m.dataset == dataset)
        .where(DatabentoInstrument.root_symbol == root)
        .where(DatabentoInstrument.instrument_class == "F")
        .group_by(DatabentoOhlcv1m.trading_date, DatabentoOhlcv1m.instrument_id)
        .order_by(DatabentoOhlcv1m.trading_date, DatabentoOhlcv1m.instrument_id)
    )
    for session_date, instrument_id, volume in volume_rows:
        daily_volumes.setdefault(session_date, {})[int(instrument_id)] = int(volume or 0)
    decisions = build_volume_roll_schedule(
        root_symbol=root,
        dataset=dataset,
        contracts=contracts,
        daily_volumes=daily_volumes,
    )
    if not decisions:
        raise DatabentoMarketDataError(f"databento_bars_missing:{root}")

    db.execute(
        delete(DatabentoRollSchedule).where(
            DatabentoRollSchedule.root_symbol == root
        )
    )
    db.flush()
    rows = [decision.__dict__ for decision in decisions]
    table = DatabentoRollSchedule.__table__
    dialect = db.get_bind().dialect.name if db.get_bind() is not None else ""
    if dialect == "postgresql":
        # Rows were deleted above; a plain insert avoids dialect-specific no-op
        # update expressions while keeping bulk execution efficient.
        db.execute(postgresql_insert(table).values(rows))
    elif dialect == "sqlite":
        db.execute(sqlite_insert(table).values(rows))
    else:  # pragma: no cover
        db.add_all(DatabentoRollSchedule(**row) for row in rows)
    db.flush()
    return decisions


def build_volume_roll_schedule(
    *,
    root_symbol: str,
    dataset: str,
    contracts: Sequence[RolloverContract],
    daily_volumes: Mapping[date, Mapping[int, int]],
) -> list[RolloverDecision]:
    """Pure no-lookahead rollover algorithm used by production and tests.

    Session D can only inspect the last completed session in the input prefix.
    A tie keeps the current contract, and delivery selection only moves toward a
    later expiration.
    """

    sessions = sorted(day for day in daily_volumes if day >= date(2019, 5, 6))
    if not sessions:
        return []
    normalized = sorted(
        contracts,
        key=lambda item: (
            item.expiration or datetime.max.replace(tzinfo=timezone.utc),
            item.raw_symbol,
            item.instrument_id,
        ),
    )
    current: RolloverContract | None = None
    prior_session: date | None = None
    output: list[RolloverDecision] = []

    for session_date in sessions:
        session_start, _session_end = trading_day_bounds_utc(session_date)
        eligible = [
            contract
            for contract in normalized
            if (contract.activation is None or as_utc(contract.activation) <= session_start)
            and (contract.expiration is None or as_utc(contract.expiration) > session_start)
        ]
        if not eligible:
            prior_session = session_date
            continue

        before = current
        current_volume: int | None = None
        candidate_volume: int | None = None
        if current is None:
            current = eligible[0]
            reason = "initial_front_contract"
        elif current not in eligible:
            later = _later_contracts(current, eligible)
            current = later[0] if later else eligible[0]
            reason = "expiration_fallback"
        else:
            later = _later_contracts(current, eligible)
            candidate = later[0] if later else None
            prior = daily_volumes.get(prior_session, {}) if prior_session is not None else {}
            current_volume = int(prior.get(current.instrument_id, 0)) if prior_session else None
            candidate_volume = (
                int(prior.get(candidate.instrument_id, 0))
                if prior_session is not None and candidate is not None
                else None
            )
            if (
                candidate is not None
                and candidate_volume is not None
                and current_volume is not None
                and candidate_volume > current_volume
            ):
                current = candidate
                reason = "next_contract_volume_exceeded_current"
            else:
                reason = "kept_current_contract"

        output.append(
            RolloverDecision(
                root_symbol=root_symbol,
                trading_date=session_date,
                dataset=dataset,
                instrument_id=int(current.instrument_id),
                raw_symbol=current.raw_symbol,
                decision_session_date=prior_session,
                from_instrument_id=(int(before.instrument_id) if before is not None else None),
                current_volume=current_volume,
                candidate_volume=candidate_volume,
                reason=reason,
            )
        )
        prior_session = session_date
    return output


def iter_databento_replay_candles(
    db: Session,
    *,
    user_id: str,
    contract_id: str,
    root_symbol: str,
    unit: str,
    unit_number: int,
    start: datetime,
    end: datetime,
    closed_by: datetime,
    dataset: str = SUPPORTED_DATASET,
) -> Iterator[DatabentoReplayCandle]:
    root = str(root_symbol).strip().upper()
    start_utc = max(as_utc(start), MNQ_HISTORY_START_UTC)
    end_utc = min(as_utc(end), as_utc(closed_by))
    if end_utc <= start_utc:
        return
    _validate_resample_timeframe(unit, unit_number)

    statement = (
        select(
            DatabentoOhlcv1m.ts_event,
            DatabentoOhlcv1m.instrument_id,
            DatabentoInstrument.raw_symbol,
            DatabentoOhlcv1m.open_nano,
            DatabentoOhlcv1m.high_nano,
            DatabentoOhlcv1m.low_nano,
            DatabentoOhlcv1m.close_nano,
            DatabentoOhlcv1m.volume,
            DatabentoOhlcv1m.source_file_sha256,
            DatabentoRollSchedule.policy_version,
        )
        .join(
            DatabentoRollSchedule,
            (DatabentoRollSchedule.dataset == DatabentoOhlcv1m.dataset)
            & (DatabentoRollSchedule.trading_date == DatabentoOhlcv1m.trading_date)
            & (DatabentoRollSchedule.instrument_id == DatabentoOhlcv1m.instrument_id)
            & (DatabentoRollSchedule.root_symbol == root),
        )
        .join(
            DatabentoInstrument,
            (DatabentoInstrument.dataset == DatabentoOhlcv1m.dataset)
            & (DatabentoInstrument.instrument_id == DatabentoOhlcv1m.instrument_id),
        )
        .where(DatabentoOhlcv1m.dataset == dataset)
        .where(
            DatabentoOhlcv1m.trading_date
            >= _trading_date_for_timestamp(start_utc)
        )
        .where(
            DatabentoOhlcv1m.trading_date
            <= _trading_date_for_timestamp(end_utc - timedelta(microseconds=1))
        )
        .where(DatabentoOhlcv1m.ts_event >= start_utc)
        .where(DatabentoOhlcv1m.ts_event < end_utc)
        .order_by(DatabentoOhlcv1m.trading_date, DatabentoOhlcv1m.ts_event)
        .execution_options(stream_results=True, yield_per=REPLAY_QUERY_CHUNK_SIZE)
    )
    raw_rows = (
        _RawContinuousBar(
            timestamp=as_utc(row.ts_event),
            instrument_id=int(row.instrument_id),
            raw_symbol=str(row.raw_symbol),
            open_nano=int(row.open_nano),
            high_nano=int(row.high_nano),
            low_nano=int(row.low_nano),
            close_nano=int(row.close_nano),
            volume=int(row.volume),
            source_file_sha256=str(row.source_file_sha256),
            roll_policy_version=str(row.policy_version),
        )
        for row in db.execute(statement)
    )
    yield from resample_databento_bars(
        raw_rows,
        user_id=user_id,
        contract_id=contract_id,
        root_symbol=root,
        unit=unit,
        unit_number=unit_number,
        closed_by=end_utc,
    )


def load_databento_replay_candles(
    db: Session,
    *,
    max_rows: int,
    **kwargs: Any,
) -> list[DatabentoReplayCandle]:
    rows: list[DatabentoReplayCandle] = []
    for row in iter_databento_replay_candles(db, **kwargs):
        rows.append(row)
        if len(rows) > max(1, int(max_rows)):
            raise DatabentoMarketDataError(
                "databento_replay_memory_budget_exceeded: resampled history exceeds the "
                f"configured {int(max_rows):,}-bar in-memory replay ceiling"
            )
    return rows


def resample_databento_bars(
    rows: Iterable[_RawContinuousBar],
    *,
    user_id: str,
    contract_id: str,
    root_symbol: str,
    unit: str,
    unit_number: int,
    closed_by: datetime,
) -> Iterator[DatabentoReplayCandle]:
    _validate_resample_timeframe(unit, unit_number)
    cutoff = as_utc(closed_by)
    bucket: dict[str, Any] | None = None
    prior_timestamp: datetime | None = None
    for row in rows:
        timestamp = as_utc(row.timestamp)
        if prior_timestamp is not None and timestamp < prior_timestamp:
            raise DatabentoMarketDataError("databento_rows_not_monotonic")
        prior_timestamp = timestamp
        bucket_start, bucket_end = _bucket_bounds(
            timestamp,
            unit=unit,
            unit_number=unit_number,
        )
        key = (bucket_start, row.instrument_id)
        if bucket is not None and bucket["key"] != key:
            candle = _finish_bucket(
                bucket,
                user_id=user_id,
                contract_id=contract_id,
                root_symbol=root_symbol,
                unit=unit,
                unit_number=unit_number,
                cutoff=cutoff,
            )
            if candle is not None:
                yield candle
            bucket = None
        if bucket is None:
            bucket = {
                "key": key,
                "start": bucket_start,
                "end": bucket_end,
                "instrument_id": row.instrument_id,
                "raw_symbol": row.raw_symbol,
                "open": row.open_nano,
                "high": row.high_nano,
                "low": row.low_nano,
                "close": row.close_nano,
                "volume": row.volume,
                "source_hash": row.source_file_sha256,
                "roll_policy_version": row.roll_policy_version,
                "timestamps": [timestamp],
            }
        else:
            if row.raw_symbol != bucket["raw_symbol"]:
                raise DatabentoMarketDataError("databento_instrument_mapping_changed_within_bucket")
            bucket["high"] = max(int(bucket["high"]), row.high_nano)
            bucket["low"] = min(int(bucket["low"]), row.low_nano)
            bucket["close"] = row.close_nano
            bucket["volume"] = int(bucket["volume"]) + row.volume
            if row.source_file_sha256 != bucket["source_hash"]:
                bucket["source_hash"] = "multiple"
            if row.roll_policy_version != bucket["roll_policy_version"]:
                raise DatabentoMarketDataError(
                    "databento_roll_policy_changed_within_bucket"
                )
            bucket["timestamps"].append(timestamp)
    if bucket is not None:
        candle = _finish_bucket(
            bucket,
            user_id=user_id,
            contract_id=contract_id,
            root_symbol=root_symbol,
            unit=unit,
            unit_number=unit_number,
            cutoff=cutoff,
        )
        if candle is not None:
            yield candle


def databento_history_bounds(
    db: Session,
    *,
    root_symbol: str,
    dataset: str = SUPPORTED_DATASET,
) -> tuple[datetime, datetime] | None:
    root = str(root_symbol).strip().upper()
    base = (
        select(DatabentoOhlcv1m.ts_event)
        .join(
            DatabentoRollSchedule,
            (DatabentoRollSchedule.dataset == DatabentoOhlcv1m.dataset)
            & (DatabentoRollSchedule.trading_date == DatabentoOhlcv1m.trading_date)
            & (DatabentoRollSchedule.instrument_id == DatabentoOhlcv1m.instrument_id)
            & (DatabentoRollSchedule.root_symbol == root),
        )
        .where(DatabentoOhlcv1m.dataset == dataset)
    )
    first = db.execute(
        base.order_by(
            DatabentoOhlcv1m.trading_date,
            DatabentoOhlcv1m.ts_event,
        ).limit(1)
    ).scalar_one_or_none()
    last = db.execute(
        base.order_by(
            DatabentoOhlcv1m.trading_date.desc(),
            DatabentoOhlcv1m.ts_event.desc(),
        ).limit(1)
    ).scalar_one_or_none()
    if first is None or last is None:
        return None
    return as_utc(first), as_utc(last) + timedelta(minutes=1)


def _finish_bucket(
    bucket: Mapping[str, Any],
    *,
    user_id: str,
    contract_id: str,
    root_symbol: str,
    unit: str,
    unit_number: int,
    cutoff: datetime,
) -> DatabentoReplayCandle | None:
    if as_utc(bucket["end"]) > cutoff:
        return None
    if not _bucket_has_complete_open_minute_coverage(
        bucket,
        root_symbol=root_symbol,
    ):
        return None
    return DatabentoReplayCandle(
        user_id=user_id,
        contract_id=contract_id,
        symbol=root_symbol,
        live=False,
        unit=unit,
        unit_number=int(unit_number),
        candle_timestamp=as_utc(bucket["start"]),
        open_price=int(bucket["open"]) / PRICE_SCALE,
        high_price=int(bucket["high"]) / PRICE_SCALE,
        low_price=int(bucket["low"]) / PRICE_SCALE,
        close_price=int(bucket["close"]) / PRICE_SCALE,
        volume=int(bucket["volume"]),
        is_partial=False,
        raw_payload=None,
        fetched_at=None,
        source="databento",
        source_instrument_id=int(bucket["instrument_id"]),
        source_raw_symbol=str(bucket["raw_symbol"]),
        source_file_sha256=str(bucket["source_hash"]),
        roll_policy_version=str(bucket["roll_policy_version"]),
        nominal_close_time=as_utc(bucket["end"]),
    )


def _bucket_has_complete_open_minute_coverage(
    bucket: Mapping[str, Any],
    *,
    root_symbol: str,
) -> bool:
    actual = tuple(as_utc(value) for value in bucket.get("timestamps", ()))
    if not actual or len(actual) != len(set(actual)):
        return False

    expected: list[datetime] = []
    cursor = as_utc(bucket["start"])
    end = as_utc(bucket["end"])
    while cursor < end:
        if futures_session_is_open(cursor, symbol=root_symbol):
            expected.append(cursor)
        cursor += timedelta(minutes=1)
    return actual == tuple(expected)


def _bucket_bounds(
    timestamp: datetime,
    *,
    unit: str,
    unit_number: int,
) -> tuple[datetime, datetime]:
    session_start, session_end_inclusive = trading_day_bounds_utc(
        _trading_date_for_timestamp(timestamp)
    )
    session_end = session_end_inclusive + timedelta(microseconds=1)
    if unit == "day":
        return session_start, session_end
    seconds = _timeframe_seconds(unit, unit_number)
    assert seconds is not None
    elapsed = max(0, int((as_utc(timestamp) - session_start).total_seconds()))
    start = session_start + timedelta(seconds=(elapsed // seconds) * seconds)
    return start, min(start + timedelta(seconds=seconds), session_end)


def _trading_date_for_timestamp(timestamp: datetime) -> date:
    # Avoid importing a second calendar implementation: trading_day_bounds_utc
    # is the inverse of the shared 18:00 America/New_York boundary.
    from .trading_day import trading_day_date

    return trading_day_date(timestamp)


def _validate_resample_timeframe(unit: str, unit_number: int) -> None:
    number = int(unit_number)
    if number <= 0:
        raise DatabentoMarketDataError("databento_timeframe_must_be_positive")
    if unit == "second":
        if number < 60 or number % 60 != 0:
            raise DatabentoMarketDataError(
                "databento_ohlcv_1m_cannot_resample_below_one_minute"
            )
        return
    if unit in {"minute", "hour"}:
        return
    if unit == "day" and number == 1:
        return
    raise DatabentoMarketDataError(
        f"unsupported_databento_resample_timeframe:{unit}:{number}"
    )


def _timeframe_seconds(unit: str, unit_number: int) -> int | None:
    base = {"second": 1, "minute": 60, "hour": 3600}.get(unit)
    return base * int(unit_number) if base is not None else None


def _later_contracts(
    current: RolloverContract,
    eligible: Sequence[RolloverContract],
) -> list[RolloverContract]:
    current_expiration = current.expiration or datetime.max.replace(tzinfo=timezone.utc)
    return [
        item
        for item in eligible
        if (item.expiration or datetime.max.replace(tzinfo=timezone.utc)) > current_expiration
    ]


def _optional_utc(value: datetime | None) -> datetime | None:
    return as_utc(value) if value is not None else None
