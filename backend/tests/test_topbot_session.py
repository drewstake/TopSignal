from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.topbot_session import CLOSE_BUFFER, entry_boundary_reason, exit_deadline, flat_deadline, runner_entry_boundary_reason

ET = ZoneInfo("America/New_York")


@pytest.mark.parametrize("value", ["2026-09-24T16:50", "2026-09-25T16:50", "2026-11-26T12:50"])
def test_horizon_must_finish_before_close_with_buffer(value):
    assert entry_boundary_reason(datetime.fromisoformat(value).replace(tzinfo=ET)) == "entry_too_close_to_session_close"


@pytest.mark.parametrize("hour,minute", [(2, 0), (20, 0), (15, 35)])
def test_unresearched_hours_abstain(hour, minute):
    assert entry_boundary_reason(datetime(2026, 9, 24, hour, minute, tzinfo=ET)) == "outside_research_session"


def test_deadline_uses_decision_clock_and_early_close():
    decision = datetime(2026, 9, 24, 10, 0, tzinfo=ET)
    assert exit_deadline(decision+timedelta(seconds=100), decision_at=decision) == decision+timedelta(minutes=15)
    late = datetime(2026, 11, 26, 12, 50, tzinfo=ET)
    assert exit_deadline(late).astimezone(ET).strftime("%H:%M") == "12:58"


@pytest.mark.parametrize("value,deadline", [
    ("2026-11-01T18:00", "2026-11-02T16:08"),
    ("2026-11-02T02:00", "2026-11-02T16:08"),
    ("2026-11-25T20:00", "2026-11-26T12:58"),
])
def test_overnight_runner_uses_next_trading_day_flatten_cutoff(value, deadline):
    now = datetime.fromisoformat(value).replace(tzinfo=ET)
    cutoff = datetime.fromisoformat(deadline).replace(tzinfo=ET)
    assert flat_deadline(now) - CLOSE_BUFFER == cutoff
    assert runner_entry_boundary_reason(now) is None
    assert runner_entry_boundary_reason(cutoff) == "entry_too_close_to_session_close"
