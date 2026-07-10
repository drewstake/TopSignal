from __future__ import annotations

import math
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Event, Lock
from typing import Any, Callable

from .projectx_client import ProjectXClientError
from .trading_day import is_expected_futures_candle


_CANDLE_CLOSE_GRACE = timedelta(seconds=2)
_MAX_AUDIT_SLOTS = 25_000
_MAX_REPAIR_WINDOWS = 8
_PROVIDER_ATTEMPTS = 3
_PROVIDER_RETRY_DELAYS = (0.0, 0.05, 0.15)
_SINGLEFLIGHT_WAIT_SECONDS = 30.0
_FAILED_REPAIR_COOLDOWN = timedelta(seconds=5)

_UNIT_SECONDS = {
    "second": 1,
    "minute": 60,
    "hour": 60 * 60,
    "day": 24 * 60 * 60,
    "week": 7 * 24 * 60 * 60,
}


@dataclass(frozen=True)
class CandleRepairRange:
    start: datetime
    end: datetime
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CandleIntegrityReport:
    valid_rows: tuple[Any, ...]
    invalid_rows: tuple[Any, ...]
    stale_partial_rows: tuple[Any, ...]
    current_partial_rows: tuple[Any, ...]
    duplicate_rows: tuple[Any, ...]
    overlapping_rows: tuple[Any, ...]
    missing_ranges: tuple[CandleRepairRange, ...]
    repair_ranges: tuple[CandleRepairRange, ...]
    issue_codes: tuple[str, ...]

    @property
    def is_complete(self) -> bool:
        return not self.issue_codes

    @property
    def bad_rows(self) -> tuple[Any, ...]:
        rows: list[Any] = []
        seen: set[int] = set()
        for row in (
            *self.invalid_rows,
            *self.stale_partial_rows,
            *self.duplicate_rows,
            *self.overlapping_rows,
        ):
            marker = id(row)
            if marker in seen:
                continue
            seen.add(marker)
            rows.append(row)
        return tuple(rows)


@dataclass
class _ProviderFlight:
    event: Event
    result: list[dict[str, Any]] | None = None
    error: Exception | None = None


_FLIGHT_GUARD = Lock()
_ACTIVE_FLIGHTS: dict[tuple[Any, ...], _ProviderFlight] = {}
_FAILED_REPAIR_UNTIL: dict[tuple[Any, ...], datetime] = {}


def candle_interval(*, unit: str, unit_number: int) -> timedelta | None:
    seconds = _UNIT_SECONDS.get(str(unit).strip().lower())
    if seconds is None:
        return None
    return timedelta(seconds=seconds * max(1, int(unit_number)))


def candle_close_time(timestamp: datetime, *, unit: str, unit_number: int) -> datetime:
    value = _as_utc(timestamp)
    normalized_unit = str(unit).strip().lower()
    if normalized_unit == "month":
        return _add_months(value, max(1, int(unit_number)))
    interval = candle_interval(unit=normalized_unit, unit_number=unit_number)
    if interval is None:
        raise ValueError(f"unsupported candle unit: {unit}")
    return value + interval


def validate_candle_ohlcv(row: Any) -> str | None:
    values: dict[str, float] = {}
    for field in ("open", "high", "low", "close", "volume"):
        raw_value = _row_value(row, field)
        try:
            value = float(raw_value)
        except (TypeError, ValueError, OverflowError):
            return f"invalid_{field}"
        if not math.isfinite(value):
            return f"non_finite_{field}"
        values[field] = value

    if min(values["open"], values["high"], values["low"], values["close"]) <= 0:
        return "non_positive_ohlc"
    if values["high"] < max(values["open"], values["close"], values["low"]):
        return "invalid_candle_high"
    if values["low"] > min(values["open"], values["close"], values["high"]):
        return "invalid_candle_low"
    if values["volume"] < 0:
        return "negative_candle_volume"
    return None


def audit_candle_rows(
    rows: Iterable[Any],
    *,
    start: datetime,
    end: datetime,
    unit: str,
    unit_number: int,
    limit: int,
    include_partial_bar: bool,
    symbol: str | None = None,
    as_of: datetime | None = None,
) -> CandleIntegrityReport:
    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    if end_utc < start_utc:
        raise ValueError("start must be before end")
    now = _as_utc(as_of or datetime.now(timezone.utc))
    interval = candle_interval(unit=unit, unit_number=unit_number)

    timestamped: list[tuple[datetime, Any]] = []
    invalid_rows: list[Any] = []
    issue_codes: set[str] = set()
    for row in rows:
        timestamp = _row_timestamp(row)
        if timestamp is None:
            invalid_rows.append(row)
            issue_codes.add("invalid_candle_timestamp")
            continue
        timestamp_utc = _as_utc(timestamp)
        if timestamp_utc < start_utc or timestamp_utc > end_utc:
            continue
        timestamped.append((timestamp_utc, row))

    grouped: dict[datetime, list[Any]] = {}
    for timestamp, row in timestamped:
        grouped.setdefault(timestamp, []).append(row)

    canonical: list[tuple[datetime, Any]] = []
    duplicate_rows: list[Any] = []
    for timestamp in sorted(grouped):
        candidates = grouped[timestamp]
        winner = max(candidates, key=_canonical_row_rank)
        canonical.append((timestamp, winner))
        if len(candidates) > 1:
            issue_codes.add("duplicate_candle_timestamp")
            duplicate_rows.extend(row for row in candidates if row is not winner)

    usable: list[tuple[datetime, Any, bool]] = []
    stale_partial_rows: list[Any] = []
    current_partial_rows: list[Any] = []
    closed_through = min(end_utc, now - _CANDLE_CLOSE_GRACE)
    for timestamp, row in canonical:
        invalid_code = validate_candle_ohlcv(row)
        if invalid_code is not None:
            invalid_rows.append(row)
            issue_codes.add(invalid_code)
            continue
        try:
            close_time = candle_close_time(timestamp, unit=unit, unit_number=unit_number)
        except ValueError:
            invalid_rows.append(row)
            issue_codes.add("unsupported_candle_unit")
            continue

        declared_partial = _row_is_partial(row)
        effectively_partial = declared_partial and close_time > closed_through
        if declared_partial and not effectively_partial:
            stale_partial_rows.append(row)
            issue_codes.add("stale_partial_candle")
            continue
        is_current_partial = declared_partial or effectively_partial
        if is_current_partial:
            current_partial_rows.append(row)
            if not include_partial_bar:
                continue
        usable.append((timestamp, row, is_current_partial))

    overlapping_rows: list[Any] = []
    non_overlapping: list[tuple[datetime, Any, bool]] = []
    for candidate in usable:
        if not non_overlapping or interval is None:
            non_overlapping.append(candidate)
            continue
        previous = non_overlapping[-1]
        previous_close = candle_close_time(previous[0], unit=unit, unit_number=unit_number)
        if candidate[0] >= previous_close:
            non_overlapping.append(candidate)
            continue

        issue_codes.add("overlapping_candles")
        preferred = max(
            (previous, candidate),
            key=lambda item: _overlap_rank(item[0], item[1], item[2], interval),
        )
        rejected = candidate if preferred is previous else previous
        overlapping_rows.append(rejected[1])
        if preferred is candidate:
            non_overlapping[-1] = candidate

    valid_rows = [item[1] for item in non_overlapping]
    closed_rows = [item for item in non_overlapping if not item[2]]
    missing_ranges: list[CandleRepairRange] = []
    if interval is not None and str(unit).strip().lower() in {"second", "minute", "hour"}:
        missing_ranges = _find_missing_ranges(
            closed_rows,
            start=start_utc,
            end=end_utc,
            closed_through=closed_through,
            interval=interval,
            limit=max(1, int(limit)),
            symbol=symbol,
        )
        if missing_ranges:
            issue_codes.add("missing_candle")

    bad_ranges: list[CandleRepairRange] = []
    for code, bad_rows in (
        ("invalid_candle", invalid_rows),
        ("stale_partial_candle", stale_partial_rows),
        ("duplicate_candle_timestamp", duplicate_rows),
        ("overlapping_candles", overlapping_rows),
    ):
        for row in bad_rows:
            timestamp = _row_timestamp(row)
            if timestamp is None:
                continue
            timestamp_utc = _as_utc(timestamp)
            padding = interval or timedelta(days=1)
            bad_ranges.append(
                CandleRepairRange(
                    start=max(start_utc, timestamp_utc - padding),
                    end=min(end_utc, candle_close_time(timestamp_utc, unit=unit, unit_number=unit_number) + padding),
                    reasons=(code,),
                )
            )

    repair_ranges = _merge_repair_ranges([*missing_ranges, *bad_ranges], maximum=_MAX_REPAIR_WINDOWS)
    valid_rows.sort(key=lambda row: _as_utc(_row_timestamp(row) or start_utc))
    return CandleIntegrityReport(
        valid_rows=tuple(valid_rows[-max(1, int(limit)) :]),
        invalid_rows=tuple(invalid_rows),
        stale_partial_rows=tuple(stale_partial_rows),
        current_partial_rows=tuple(current_partial_rows),
        duplicate_rows=tuple(duplicate_rows),
        overlapping_rows=tuple(overlapping_rows),
        missing_ranges=tuple(missing_ranges),
        repair_ranges=tuple(repair_ranges),
        issue_codes=tuple(sorted(issue_codes)),
    )


def normalize_provider_bars(
    bars: Iterable[dict[str, Any]],
    *,
    unit: str,
    unit_number: int,
    request_start: datetime | None = None,
    request_end: datetime,
    include_partial_bar: bool,
    as_of: datetime | None = None,
) -> list[dict[str, Any]]:
    now = _as_utc(as_of or datetime.now(timezone.utc))
    request_start_utc = _as_utc(request_start) if request_start is not None else None
    request_end_utc = _as_utc(request_end)
    closed_through = min(request_end_utc, now - _CANDLE_CLOSE_GRACE)
    candidates: list[dict[str, Any]] = []
    for raw_bar in bars:
        if not isinstance(raw_bar, dict):
            continue
        timestamp = raw_bar.get("timestamp")
        if not isinstance(timestamp, datetime):
            continue
        timestamp_utc = _as_utc(timestamp)
        if timestamp_utc > request_end_utc or (
            request_start_utc is not None and timestamp_utc < request_start_utc
        ):
            continue
        try:
            close_time = candle_close_time(timestamp_utc, unit=unit, unit_number=unit_number)
        except ValueError:
            continue
        normalized = {**raw_bar, "timestamp": timestamp_utc}
        inferred_partial = (
            bool(raw_bar.get("is_partial"))
            or close_time > request_end_utc
            or (include_partial_bar and close_time > closed_through)
        )
        normalized["is_partial"] = inferred_partial
        if validate_candle_ohlcv(normalized) is not None:
            continue
        if inferred_partial and not include_partial_bar:
            continue
        candidates.append(normalized)

    if not candidates:
        return []
    start = min(_as_utc(row["timestamp"]) for row in candidates)
    end = max(candle_close_time(row["timestamp"], unit=unit, unit_number=unit_number) for row in candidates)
    report = audit_candle_rows(
        candidates,
        start=start,
        end=end,
        unit=unit,
        unit_number=unit_number,
        limit=len(candidates),
        include_partial_bar=include_partial_bar,
        as_of=now,
    )
    return [dict(row) for row in report.valid_rows]


def retrieve_bars_singleflight(
    *,
    key: tuple[Any, ...],
    retrieve: Callable[[], list[dict[str, Any]]],
    attempts: int = _PROVIDER_ATTEMPTS,
) -> list[dict[str, Any]]:
    owner = False
    with _FLIGHT_GUARD:
        flight = _ACTIVE_FLIGHTS.get(key)
        if flight is None:
            flight = _ProviderFlight(event=Event())
            _ACTIVE_FLIGHTS[key] = flight
            owner = True

    if not owner:
        if not flight.event.wait(timeout=_SINGLEFLIGHT_WAIT_SECONDS):
            raise ProjectXClientError("Timed out waiting for an identical candle request.", status_code=504)
        if flight.error is not None:
            raise flight.error
        return [dict(row) for row in (flight.result or [])]

    try:
        result = _retrieve_with_bounded_retries(retrieve, attempts=attempts)
        flight.result = [dict(row) for row in result]
        return [dict(row) for row in result]
    except Exception as exc:
        flight.error = exc
        raise
    finally:
        flight.event.set()
        with _FLIGHT_GUARD:
            if _ACTIVE_FLIGHTS.get(key) is flight:
                _ACTIVE_FLIGHTS.pop(key, None)


def repair_request_in_cooldown(key: tuple[Any, ...], *, now: datetime | None = None) -> bool:
    current = _as_utc(now or datetime.now(timezone.utc))
    with _FLIGHT_GUARD:
        expires_at = _FAILED_REPAIR_UNTIL.get(key)
        if expires_at is None:
            return False
        if expires_at <= current:
            _FAILED_REPAIR_UNTIL.pop(key, None)
            return False
        return True


def note_failed_repair(key: tuple[Any, ...], *, now: datetime | None = None) -> None:
    current = _as_utc(now or datetime.now(timezone.utc))
    with _FLIGHT_GUARD:
        _FAILED_REPAIR_UNTIL[key] = current + _FAILED_REPAIR_COOLDOWN


def clear_failed_repair(key: tuple[Any, ...]) -> None:
    with _FLIGHT_GUARD:
        _FAILED_REPAIR_UNTIL.pop(key, None)


def _reset_request_state_for_tests() -> None:
    with _FLIGHT_GUARD:
        _ACTIVE_FLIGHTS.clear()
        _FAILED_REPAIR_UNTIL.clear()


def _retrieve_with_bounded_retries(
    retrieve: Callable[[], list[dict[str, Any]]],
    *,
    attempts: int,
) -> list[dict[str, Any]]:
    bounded_attempts = max(1, min(int(attempts), _PROVIDER_ATTEMPTS))
    for attempt in range(bounded_attempts):
        try:
            return retrieve()
        except ProjectXClientError as exc:
            if attempt + 1 >= bounded_attempts or not _is_transient_provider_error(exc):
                raise
            delay = _PROVIDER_RETRY_DELAYS[min(attempt + 1, len(_PROVIDER_RETRY_DELAYS) - 1)]
            if delay > 0:
                time.sleep(delay)
    return []


def _is_transient_provider_error(exc: ProjectXClientError) -> bool:
    status_code = exc.status_code
    return status_code is None or status_code == 429 or status_code >= 500


def _find_missing_ranges(
    closed_rows: list[tuple[datetime, Any, bool]],
    *,
    start: datetime,
    end: datetime,
    closed_through: datetime,
    interval: timedelta,
    limit: int,
    symbol: str | None,
) -> list[CandleRepairRange]:
    if closed_through < start:
        return []
    timestamps = [row[0] for row in closed_rows]
    missing: list[datetime] = []
    broad_ranges: list[CandleRepairRange] = []
    scanned = 0

    wall_slots = max(0, int((closed_through - start) // interval) + 1)
    if wall_slots <= limit:
        candidate = _ceil_timestamp(start, interval)
        first = timestamps[0] if timestamps else None
        while candidate < (first or closed_through + interval) and candidate + interval <= closed_through:
            scanned += 1
            if scanned > _MAX_AUDIT_SLOTS:
                break
            if is_expected_futures_candle(candidate, candidate + interval, symbol=symbol):
                missing.append(candidate)
            candidate += interval

    for left, right in zip(timestamps, timestamps[1:]):
        candidate = left + interval
        candidate_slots = max(0, math.ceil((right - candidate) / interval))
        if scanned + candidate_slots > _MAX_AUDIT_SLOTS:
            if candidate < right and is_expected_futures_candle(candidate, right, symbol=symbol):
                broad_ranges.append(
                    CandleRepairRange(
                        start=candidate,
                        end=right,
                        reasons=("missing_candle",),
                    )
                )
            scanned = _MAX_AUDIT_SLOTS + 1
            break
        while candidate < right:
            scanned += 1
            if scanned > _MAX_AUDIT_SLOTS:
                break
            if candidate + interval <= closed_through and is_expected_futures_candle(
                candidate,
                candidate + interval,
                symbol=symbol,
            ):
                missing.append(candidate)
            candidate += interval
        if scanned > _MAX_AUDIT_SLOTS:
            break

    if scanned <= _MAX_AUDIT_SLOTS:
        candidate = (timestamps[-1] + interval) if timestamps else _ceil_timestamp(start, interval)
        tail_slots = max(0, int((closed_through - candidate) // interval))
        if scanned + tail_slots > _MAX_AUDIT_SLOTS:
            if candidate < closed_through and is_expected_futures_candle(
                candidate,
                closed_through,
                symbol=symbol,
            ):
                broad_ranges.append(
                    CandleRepairRange(
                        start=candidate,
                        end=closed_through,
                        reasons=("missing_candle",),
                    )
                )
            scanned = _MAX_AUDIT_SLOTS + 1
        while candidate + interval <= closed_through:
            if scanned > _MAX_AUDIT_SLOTS:
                break
            scanned += 1
            if scanned > _MAX_AUDIT_SLOTS:
                break
            if is_expected_futures_candle(candidate, candidate + interval, symbol=symbol):
                missing.append(candidate)
            candidate += interval
    elif timestamps:
        candidate = timestamps[-1] + interval
        if candidate + interval <= closed_through and is_expected_futures_candle(
            candidate,
            closed_through,
            symbol=symbol,
        ):
            broad_ranges.append(
                CandleRepairRange(
                    start=candidate,
                    end=closed_through,
                    reasons=("missing_candle",),
                )
            )

    if not missing:
        return broad_ranges
    unique = sorted(set(missing))
    ranges: list[CandleRepairRange] = []
    range_start = unique[0]
    previous = unique[0]
    for timestamp in unique[1:]:
        if timestamp == previous + interval:
            previous = timestamp
            continue
        ranges.append(
            CandleRepairRange(start=range_start, end=previous + interval, reasons=("missing_candle",))
        )
        range_start = timestamp
        previous = timestamp
    ranges.append(CandleRepairRange(start=range_start, end=previous + interval, reasons=("missing_candle",)))
    return _merge_repair_ranges([*ranges, *broad_ranges], maximum=_MAX_REPAIR_WINDOWS)


def _merge_repair_ranges(
    ranges: Iterable[CandleRepairRange],
    *,
    maximum: int,
) -> list[CandleRepairRange]:
    ordered = sorted(ranges, key=lambda item: (item.start, item.end))
    merged: list[CandleRepairRange] = []
    for item in ordered:
        if item.end <= item.start:
            continue
        if not merged or item.start > merged[-1].end:
            merged.append(item)
            continue
        previous = merged[-1]
        merged[-1] = CandleRepairRange(
            start=previous.start,
            end=max(previous.end, item.end),
            reasons=tuple(sorted(set((*previous.reasons, *item.reasons)))),
        )

    maximum = max(1, int(maximum))
    while len(merged) > maximum:
        pair_index = min(
            range(len(merged) - 1),
            key=lambda index: merged[index + 1].start - merged[index].end,
        )
        left = merged[pair_index]
        right = merged[pair_index + 1]
        merged[pair_index : pair_index + 2] = [
            CandleRepairRange(
                start=left.start,
                end=right.end,
                reasons=tuple(sorted(set((*left.reasons, *right.reasons)))),
            )
        ]
    return merged


def _canonical_row_rank(row: Any) -> tuple[Any, ...]:
    valid = validate_candle_ohlcv(row) is None
    partial = _row_is_partial(row)
    fetched_at = _row_fetched_at(row)
    fetched_epoch = fetched_at.timestamp() if fetched_at is not None else 0.0
    numeric: list[float] = []
    for field in ("open", "high", "low", "close", "volume"):
        try:
            numeric.append(float(_row_value(row, field)))
        except (TypeError, ValueError, OverflowError):
            numeric.append(float("-inf"))
    return (int(valid), int(not partial), fetched_epoch, *numeric)


def _overlap_rank(
    timestamp: datetime,
    row: Any,
    is_partial: bool,
    interval: timedelta,
) -> tuple[Any, ...]:
    aligned = _floor_timestamp(timestamp, interval) == timestamp
    fetched_at = _row_fetched_at(row)
    return (
        int(not is_partial),
        int(aligned),
        fetched_at.timestamp() if fetched_at is not None else 0.0,
        -timestamp.timestamp(),
    )


def _row_timestamp(row: Any) -> datetime | None:
    value = row.get("timestamp") if isinstance(row, Mapping) else getattr(row, "candle_timestamp", None)
    return value if isinstance(value, datetime) else None


def _row_value(row: Any, field: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(field)
    return getattr(row, f"{field}_price" if field != "volume" else "volume", None)


def _row_is_partial(row: Any) -> bool:
    if isinstance(row, Mapping):
        return bool(row.get("is_partial"))
    return bool(getattr(row, "is_partial", False))


def _row_fetched_at(row: Any) -> datetime | None:
    value = row.get("fetched_at") if isinstance(row, Mapping) else getattr(row, "fetched_at", None)
    return _as_utc(value) if isinstance(value, datetime) else None


def _floor_timestamp(value: datetime, interval: timedelta) -> datetime:
    value_utc = _as_utc(value)
    seconds = max(1, int(interval.total_seconds()))
    epoch = int(value_utc.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=timezone.utc)


def _ceil_timestamp(value: datetime, interval: timedelta) -> datetime:
    floor = _floor_timestamp(value, interval)
    return floor if floor >= _as_utc(value) else floor + interval


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=value.tzinfo)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=value.tzinfo)
    month_start = datetime(year, month, 1, tzinfo=value.tzinfo)
    last_day = (next_month - month_start).days
    return value.replace(year=year, month=month, day=min(value.day, last_day))
