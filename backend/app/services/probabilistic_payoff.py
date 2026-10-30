"""Versioned payoff timing. Legacy artifacts remain readable, never upgraded in place."""
from datetime import datetime, timedelta, timezone
import math

LEGACY_PAYOFF = 'decision_open_three_bars_v1'
PAPER_PAYOFF = 'post_record_open_fixed_deadline_v2'
BAR = timedelta(minutes=5)


def execution_window(close, recorded_at):
    """Strictly future open and the original close+15m deadline, including late records."""
    if close.tzinfo is None or recorded_at.tzinfo is None:
        raise ValueError('Timing requires timezone-aware timestamps')
    if close.second or close.microsecond or close.minute % 5 or recorded_at < close:
        raise ValueError('Invalid decision close or recording time')
    entry = datetime.fromtimestamp((math.floor(recorded_at.timestamp()/300)+1)*300, timezone.utc)
    deadline = close + 3*BAR
    offset = int((entry-close)/BAR)
    return dict(entry_at=entry.isoformat(), deadline=deadline.isoformat(),
                entry_offset_bars=offset, exposure_bars=max(0, 3-offset))


def timing_matches(forecast, close, recorded_at):
    try:
        window = execution_window(close, recorded_at)
        return (forecast.get('payoff_semantics') == PAPER_PAYOFF
                and window['exposure_bars'] > 0
                and all(forecast.get(k) == v for k, v in window.items()))
    except (ValueError, TypeError, AttributeError):
        return False
