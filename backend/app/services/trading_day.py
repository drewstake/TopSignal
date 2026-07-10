from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

TRADING_TZ = ZoneInfo("America/New_York")
TRADING_DAY_ROLLOVER_HOUR = 18

_EQUITY_HALT_START = time(hour=16, minute=15)
_EQUITY_HALT_END = time(hour=16, minute=30)
_EARLY_CLOSE = time(hour=13)
_DAY_AFTER_THANKSGIVING_CLOSE = time(hour=13, minute=15)
_GOOD_FRIDAY_CLOSE = time(hour=9, minute=15)
_CME_EQUITY_ROOTS = frozenset(
    {"ES", "MES", "NQ", "MNQ", "YM", "MYM", "RTY", "M2K", "EMD", "MME", "NKD", "NIY"}
)


@dataclass(frozen=True)
class FuturesHolidaySchedule:
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


def futures_session_is_open(value: datetime, *, symbol: str | None = None) -> bool:
    """Return whether a timestamp falls inside the scheduled CME futures session.

    The common Globex week is used for unknown products. Known equity-index
    roots additionally honor the daily 16:15-16:30 ET halt and modern recurring
    CME holiday closures, avoiding false candle-gap repairs during closures.
    """

    local = as_utc(value).astimezone(TRADING_TZ)
    weekday = local.weekday()
    local_time = local.time()
    if weekday == 5:
        return False
    if weekday == 6 and local_time < time(hour=18):
        return False
    if weekday == 4 and local_time >= time(hour=17):
        return False
    if time(hour=17) <= local_time < time(hour=18):
        return False
    if not _uses_cme_equity_schedule(symbol):
        return True

    trading_date = trading_day_date(value)
    holiday = _cme_equity_holiday_schedule(trading_date)
    if holiday is not None and holiday.full_close:
        return False
    if local.date() == trading_date:
        close_time = holiday.early_close if holiday and holiday.early_close else time(hour=17)
        if local_time >= close_time:
            return False
        if _EQUITY_HALT_START <= local_time < _EQUITY_HALT_END:
            return False
    return True


def futures_holiday_schedule(value: date, *, symbol: str | None = None) -> FuturesHolidaySchedule | None:
    if not _uses_cme_equity_schedule(symbol):
        return None
    return _cme_equity_holiday_schedule(value)


def _uses_cme_equity_schedule(symbol: str | None) -> bool:
    if symbol is None or not str(symbol).strip():
        return False
    tokens = {token for token in re.split(r"[^A-Z0-9]+", str(symbol).upper()) if token}
    return bool(tokens & _CME_EQUITY_ROOTS)


@lru_cache(maxsize=1_024)
def _cme_equity_holiday_schedule(value: date) -> FuturesHolidaySchedule | None:
    """Recurring CME equity-index exceptions for the modern schedule regime."""

    year = value.year
    new_year = date(year, 1, 1)
    observed_new_year = new_year + timedelta(days=1) if new_year.weekday() == 6 else new_year
    if value == observed_new_year:
        return FuturesHolidaySchedule("New Year's Day", value, full_close=True)

    if value == _nearest_weekday(date(year, 12, 25)):
        return FuturesHolidaySchedule("Christmas", value, full_close=True)

    if value == _easter_sunday(year) - timedelta(days=2):
        return FuturesHolidaySchedule("Good Friday", value, early_close=_GOOD_FRIDAY_CLOSE)

    early_close_name: str | None = None
    if value == _nth_weekday(year, 1, weekday=0, occurrence=3):
        early_close_name = "Martin Luther King Jr. Day"
    elif value == _nth_weekday(year, 2, weekday=0, occurrence=3):
        early_close_name = "Presidents Day"
    elif value == _last_weekday(year, 5, weekday=0):
        early_close_name = "Memorial Day"
    elif year >= 2022 and value == _nearest_weekday(date(year, 6, 19)):
        early_close_name = "Juneteenth"
    elif value == _nearest_weekday(date(year, 7, 4)):
        early_close_name = "Independence Day"
    elif value == _nth_weekday(year, 9, weekday=0, occurrence=1):
        early_close_name = "Labor Day"
    else:
        thanksgiving = _nth_weekday(year, 11, weekday=3, occurrence=4)
        if value == thanksgiving:
            early_close_name = "Thanksgiving Day"
        elif value == thanksgiving + timedelta(days=1):
            return FuturesHolidaySchedule(
                "Day After Thanksgiving",
                value,
                early_close=_DAY_AFTER_THANKSGIVING_CLOSE,
            )
        elif value == date(year, 12, 24) and value.weekday() < 5:
            return FuturesHolidaySchedule(
                "Christmas Eve",
                value,
                early_close=_DAY_AFTER_THANKSGIVING_CLOSE,
            )

    if early_close_name is None:
        return None
    return FuturesHolidaySchedule(early_close_name, value, early_close=_EARLY_CLOSE)


def _nth_weekday(year: int, month: int, *, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, *, weekday: int) -> date:
    next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _nearest_weekday(value: date) -> date:
    if value.weekday() == 5:
        return value - timedelta(days=1)
    if value.weekday() == 6:
        return value + timedelta(days=1)
    return value


def _easter_sunday(year: int) -> date:
    # Anonymous Gregorian algorithm.
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
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)
