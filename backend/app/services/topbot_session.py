"""Shared entry/exit boundaries for the registered MNQ regular-session rule."""
from datetime import datetime, time, timedelta, timezone

from .trading_day import TRADING_TZ, as_utc, futures_holiday_schedule, futures_session_is_open, trading_day_date

HORIZON = timedelta(minutes=15)
CLOSE_BUFFER = timedelta(minutes=2)
TOPSTEP_FLAT_TIME = time(16, 10)  # Topstep terms: 15:10 America/Chicago.


def flat_deadline(now: datetime, *, symbol: str = "MNQ") -> datetime:
    day = trading_day_date(now)
    holiday = futures_holiday_schedule(day, symbol=symbol)
    close = holiday.early_close if holiday and holiday.early_close else time(17)
    if holiday and holiday.full_close:
        close = time(0)
    return datetime.combine(day, min(close, TOPSTEP_FLAT_TIME), TRADING_TZ).astimezone(timezone.utc)


def entry_boundary_reason(now: datetime, *, symbol: str = "MNQ", regular_only: bool = True) -> str | None:
    now = as_utc(now)
    if now + HORIZON + CLOSE_BUFFER > flat_deadline(now, symbol=symbol):
        return "entry_too_close_to_session_close"
    if not futures_session_is_open(now, symbol=symbol):
        return "exchange_session_closed"
    local = now.astimezone(TRADING_TZ)
    end = (now + HORIZON).astimezone(TRADING_TZ)
    if regular_only and (not time(9, 35) <= local.time() <= time(15, 30)
                         or end.date() != local.date() or end.time() > time(15, 45)):
        return "outside_research_session"
    return None


def exit_deadline(now: datetime, *, decision_at: datetime | None = None, symbol: str = "MNQ") -> datetime:
    # Align with the researched third-bar close, not delayed submission time.
    return min(as_utc(decision_at or now) + HORIZON, flat_deadline(now, symbol=symbol) - CLOSE_BUFFER)
