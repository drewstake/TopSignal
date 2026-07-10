from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

TRADING_TZ = ZoneInfo("America/New_York")
TRADING_DAY_ROLLOVER_HOUR = 18

_REGULAR_SESSION_OPEN = time(hour=18)
_REGULAR_SESSION_CLOSE = time(hour=17)
_EQUITY_HALT_START = time(hour=16, minute=15)
_EQUITY_HALT_END = time(hour=16, minute=30)
_EARLY_CLOSE = time(hour=13)
_EARLY_CLOSE_OLD = time(hour=11, minute=30)
_EARLY_CLOSE_1215_CT = time(hour=13, minute=15)
_GOOD_FRIDAY_CLOSE = time(hour=9, minute=15)

# ProjectX symbols can be short roots (``MNQ``), provider symbols
# (``F.US.MNQ``), or full contract ids (``CON.F.US.MNQ.M26``).  Unknown
# symbols use the equity schedule because that is TopSignal's primary market;
# known non-equity roots retain the common Globex week and maintenance break
# without incorrectly borrowing equity-specific holiday close times.
_CME_EQUITY_ROOTS = frozenset(
    {
        "ES",
        "MES",
        "NQ",
        "MNQ",
        "YM",
        "MYM",
        "RTY",
        "M2K",
        "EMD",
        "MME",
        "NKD",
        "NIY",
    }
)
_KNOWN_NON_EQUITY_ROOTS = frozenset(
    {
        "CL",
        "MCL",
        "NG",
        "QG",
        "GC",
        "MGC",
        "SI",
        "SIL",
        "HG",
        "MHG",
        "ZB",
        "ZN",
        "ZF",
        "ZT",
        "6A",
        "6B",
        "6C",
        "6E",
        "6J",
    }
)


@dataclass(frozen=True)
class FuturesHolidaySchedule:
    """A deterministic CME equity holiday exception for one trading date.

    ``full_close`` means the session that would normally start at 18:00 ET on
    the prior calendar date never opens.  ``early_close`` is an ET wall-clock
    close on ``trading_date``; the next trading date can still open at 18:00 ET.
    """

    name: str
    trading_date: date
    full_close: bool = False
    early_close: time | None = None


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def trading_day_date(value: datetime) -> date:
    local_time = as_utc(value).astimezone(TRADING_TZ)
    trading_date = local_time.date()
    if local_time.hour >= TRADING_DAY_ROLLOVER_HOUR:
        trading_date = trading_date + timedelta(days=1)
    return trading_date


def trading_day_key(value: datetime) -> str:
    return trading_day_date(value).isoformat()


def trading_day_bounds_utc(value: date) -> tuple[datetime, datetime]:
    # A trading day runs 6:00 PM ET -> 5:59:59.999999 PM ET next day.
    start_local = datetime.combine(
        value - timedelta(days=1),
        time(hour=TRADING_DAY_ROLLOVER_HOUR),
        tzinfo=TRADING_TZ,
    )
    end_local = start_local + timedelta(days=1) - timedelta(microseconds=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def futures_holiday_schedule(
    value: date,
    *,
    symbol: str | None = None,
) -> FuturesHolidaySchedule | None:
    """Return the CME equity holiday exception for a trading date, if any.

    Holiday hours are product-family specific.  The equity calendar is used for
    CME equity roots and when no root is available.  Known non-equity roots use
    only the common Globex weekly schedule until a dedicated product calendar is
    added, avoiding false claims about their special close times.
    """

    if not _uses_cme_equity_schedule(symbol):
        return None
    return _cme_equity_holiday_schedule(value)


def futures_session_intervals_utc(
    value: date,
    *,
    symbol: str | None = None,
) -> tuple[tuple[datetime, datetime], ...]:
    """Return authoritative open intervals for one futures trading date.

    Intervals are half-open UTC ranges.  A normal CME equity trading date has
    two ranges because the 16:15-16:30 ET equity halt is excluded.  Weekends,
    full holidays, the daily 17:00-18:00 maintenance period, and holiday hours
    therefore never appear as expected candle time.
    """

    return _futures_session_intervals_utc_cached(value, _uses_cme_equity_schedule(symbol))


def is_futures_market_open(at: datetime, *, symbol: str | None = None) -> bool:
    """Return whether ``at`` is inside a scheduled CME futures session."""

    instant = as_utc(at)
    session_date = trading_day_date(instant)
    return any(
        session_open <= instant < session_close
        for session_open, session_close in futures_session_intervals_utc(
            session_date,
            symbol=symbol,
        )
    )


def is_expected_futures_candle(
    timestamp: datetime,
    close_time: datetime,
    *,
    symbol: str | None = None,
) -> bool:
    """Return whether a candle bucket overlaps scheduled trading time.

    The bucket is treated as ``[timestamp, close_time)``.  This interval-based
    test handles larger bars that straddle a scheduled halt without inventing a
    candle for a bucket wholly contained in a weekend, holiday, or maintenance
    period.  It only describes whether a bar may be expected; it never creates
    or interpolates market data.
    """

    start_utc = as_utc(timestamp)
    end_utc = as_utc(close_time)
    if end_utc <= start_utc:
        return False

    start_local_date = start_utc.astimezone(TRADING_TZ).date()
    end_local_date = end_utc.astimezone(TRADING_TZ).date()
    # A session beginning after 18:00 belongs to the following trading date, so
    # inspect one date beyond the local end.  API/backtest ranges are bounded;
    # iterating dates avoids scanning every missing second across a weekend.
    final_session_date = _add_date_safely(end_local_date, days=1)
    session_date = start_local_date
    while session_date <= final_session_date:
        for session_open, session_close in futures_session_intervals_utc(
            session_date,
            symbol=symbol,
        ):
            if session_open < end_utc and session_close > start_utc:
                return True
        if session_date == date.max:
            break
        session_date += timedelta(days=1)
    return False


@lru_cache(maxsize=8_192)
def _futures_session_intervals_utc_cached(
    value: date,
    equity_schedule: bool,
) -> tuple[tuple[datetime, datetime], ...]:
    if value.weekday() >= 5:
        return ()

    holiday = _cme_equity_holiday_schedule(value) if equity_schedule else None
    if holiday is not None and holiday.full_close:
        return ()

    open_local = datetime.combine(
        value - timedelta(days=1),
        _REGULAR_SESSION_OPEN,
        tzinfo=TRADING_TZ,
    )
    close_at = (
        holiday.early_close
        if holiday is not None and holiday.early_close is not None
        else _REGULAR_SESSION_CLOSE
    )
    close_local = datetime.combine(value, close_at, tzinfo=TRADING_TZ)
    if close_local <= open_local:
        return ()

    local_intervals: list[tuple[datetime, datetime]] = []
    if equity_schedule:
        halt_start = datetime.combine(value, _EQUITY_HALT_START, tzinfo=TRADING_TZ)
        halt_end = datetime.combine(value, _EQUITY_HALT_END, tzinfo=TRADING_TZ)
        first_close = min(close_local, halt_start)
        if open_local < first_close:
            local_intervals.append((open_local, first_close))
        if halt_end < close_local:
            local_intervals.append((halt_end, close_local))
    else:
        local_intervals.append((open_local, close_local))

    return tuple(
        (interval_open.astimezone(timezone.utc), interval_close.astimezone(timezone.utc))
        for interval_open, interval_close in local_intervals
    )


@lru_cache(maxsize=1_024)
def _cme_equity_holiday_schedule(value: date) -> FuturesHolidaySchedule | None:
    """Contemporary CME Globex equity holiday rules in Eastern time.

    CME publishes product-specific schedules annually.  These deterministic
    rules encode the recurring equity-index schedule and its documented regime
    changes; unusual exchange notices can be added as explicit date overrides
    without changing gap-detection behavior elsewhere.
    """

    year = value.year

    new_year = date(year, 1, 1)
    observed_new_year = new_year + timedelta(days=1) if new_year.weekday() == 6 else new_year
    if value == observed_new_year:
        return FuturesHolidaySchedule("New Year's Day", value, full_close=True)

    christmas = _nearest_weekday(date(year, 12, 25))
    if value == christmas:
        return FuturesHolidaySchedule("Christmas", value, full_close=True)

    good_friday = _easter_sunday(year) - timedelta(days=2)
    if value == good_friday:
        if year == 2022 or (year < 2021 and year not in {2010, 2012, 2015}):
            return FuturesHolidaySchedule("Good Friday", value, full_close=True)
        return FuturesHolidaySchedule(
            "Good Friday",
            value,
            early_close=_GOOD_FRIDAY_CLOSE,
        )

    early_close_name: str | None = None
    early_close_at: time | None = None

    if year >= 1998 and value == _nth_weekday(year, 1, weekday=0, occurrence=3):
        early_close_name = "Martin Luther King Jr. Day"
        early_close_at = _EARLY_CLOSE if year >= 2015 else _EARLY_CLOSE_OLD
    elif value == _nth_weekday(year, 2, weekday=0, occurrence=3):
        early_close_name = "Presidents Day"
        early_close_at = _EARLY_CLOSE if year >= 2015 else _EARLY_CLOSE_OLD
    elif value == _last_weekday(year, 5, weekday=0):
        early_close_name = "Memorial Day"
        early_close_at = _EARLY_CLOSE if year >= 2014 else _EARLY_CLOSE_OLD
    elif year >= 2022 and value == _nearest_weekday(date(year, 6, 19)):
        early_close_name = "Juneteenth"
        early_close_at = _EARLY_CLOSE
    elif value == _nearest_weekday(date(year, 7, 4)):
        early_close_name = "Independence Day"
        early_close_at = _EARLY_CLOSE if year >= 2014 else _EARLY_CLOSE_OLD
    elif value == _pre_independence_early_close(year):
        early_close_name = "Independence Day Eve"
        early_close_at = _EARLY_CLOSE_1215_CT
    elif value == _nth_weekday(year, 9, weekday=0, occurrence=1):
        early_close_name = "Labor Day"
        early_close_at = _EARLY_CLOSE if year >= 2014 else _EARLY_CLOSE_OLD
    else:
        thanksgiving = _nth_weekday(year, 11, weekday=3, occurrence=4)
        if value == thanksgiving:
            early_close_name = "Thanksgiving Day"
            early_close_at = _EARLY_CLOSE if year >= 2014 else _EARLY_CLOSE_OLD
        elif value == thanksgiving + timedelta(days=1):
            early_close_name = "Day After Thanksgiving"
            early_close_at = _EARLY_CLOSE_1215_CT
        elif year >= 1993 and value == date(year, 12, 24) and value.weekday() < 5:
            early_close_name = "Christmas Eve"
            early_close_at = _EARLY_CLOSE_1215_CT

    if early_close_name is None or early_close_at is None:
        return None
    return FuturesHolidaySchedule(
        early_close_name,
        value,
        early_close=early_close_at,
    )


def _uses_cme_equity_schedule(symbol: str | None) -> bool:
    if symbol is None or not str(symbol).strip():
        return True
    tokens = {token for token in re.split(r"[^A-Z0-9]+", str(symbol).upper()) if token}
    if tokens & _CME_EQUITY_ROOTS:
        return True
    if tokens & _KNOWN_NON_EQUITY_ROOTS:
        return False
    return True


def _nth_weekday(year: int, month: int, *, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, *, weekday: int) -> date:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _nearest_weekday(value: date) -> date:
    if value.weekday() == 5:
        return value - timedelta(days=1)
    if value.weekday() == 6:
        return value + timedelta(days=1)
    return value


def _pre_independence_early_close(year: int) -> date | None:
    independence_day = date(year, 7, 4)
    if independence_day.weekday() not in {1, 2, 3, 4}:
        return None
    return independence_day - timedelta(days=1)


def _easter_sunday(year: int) -> date:
    """Gregorian Easter (Meeus/Jones/Butcher), used to derive Good Friday."""

    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _add_date_safely(value: date, *, days: int) -> date:
    try:
        return value + timedelta(days=days)
    except OverflowError:
        return date.max if days >= 0 else date.min
