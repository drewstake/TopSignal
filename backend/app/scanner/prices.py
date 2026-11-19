from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from statistics import mean
from zoneinfo import ZoneInfo

from .sources import stamp

EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Rules:
    percent: float = 1.0
    rvol: float = 2.0
    window: int = 1
    baseline: int = 20
    cooldown: int = 15


@dataclass(frozen=True)
class Bar:
    start: int
    open: float
    close: float
    volume: int

    @classmethod
    def alpaca(cls, row):
        return cls(int(stamp(row["t"])), row["o"], row["c"], row["v"])


def session_window(bars, end, length):
    """Exact bar opens, regular hours, same New York session. No interpolation."""
    last = datetime.fromtimestamp(end - 60, UTC).astimezone(EASTERN)
    first = last - timedelta(minutes=length - 1)
    if (
        last.weekday() > 4
        or first.date() != last.date()
        or (first.hour, first.minute) < (9, 30)
        or last.hour >= 16
    ):
        return []
    result = []
    for minute in range(end - length * 60, end, 60):
        b = bars.get(minute)
        if (
            not b
            or b.start != minute
            or not all(isfinite(x) and x > 0 for x in (b.open, b.close, b.volume))
        ):
            return []
        result.append(b)
    return result


def evaluate(bars, benchmark, end, now, rules):
    if end % 60 or not 0 <= now - end <= 75:
        return None
    history = session_window(bars, end, rules.window + rules.baseline)
    if not history:
        return None
    sample = history[-rules.window :]
    pct = 100 * (sample[-1].close / sample[0].open - 1)
    rv = mean(b.volume for b in sample) / mean(
        b.volume for b in history[: -rules.window]
    )
    sector = session_window(benchmark, end, rules.window)
    sector_pct = 100 * (sector[-1].close / sector[0].open - 1) if sector else None
    return dict(
        start=sample[0].start,
        end=end,
        minutes=rules.window,
        percent=pct,
        rvol=rv,
        sector_percent=sector_pct,
        qualifies=abs(pct) >= rules.percent and rv >= rules.rvol,
    )
