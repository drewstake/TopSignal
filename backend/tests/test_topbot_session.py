from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.topbot_session import entry_boundary_reason, exit_deadline

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
