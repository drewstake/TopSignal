from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

TRADING_TZ = ZoneInfo("America/New_York")
TRADING_DAY_ROLLOVER_HOUR = 18


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
