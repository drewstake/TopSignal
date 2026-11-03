"""Scheduled entry pauses must not manufacture blocked BUY/SELL activity."""
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import app.bot_worker as worker
from app.models import BotConfig, BotRun
from app.services import forward_results, market_archive, topbot_depth, topbot_profit_guard, topbot_strategy
from test_bot_worker import session_factory, _runtime, _seed_run
from test_bot_execution_safety import db_session
from test_topbot_dom import observation

ET = ZoneInfo("America/New_York")
CASES = [
    ("2026-11-03T16:07:59", False),
    ("2026-11-03T16:08:00", True),
    ("2026-11-03T16:10:00", True),
    ("2026-11-03T16:45:00", True),
    ("2026-11-03T17:59:59", True),
    ("2026-11-03T18:00:00", False),
    ("2026-11-06T18:00:00", True),  # Friday evening is still closed.
    ("2026-11-07T10:00:00", True),
    ("2026-11-08T17:59:59", True),
    ("2026-11-08T18:00:00", False),
    ("2026-11-26T12:58:00", True),  # Holiday flatten buffer.
    ("2026-11-26T13:00:00", True),
    ("2026-11-26T18:00:00", False),
    ("2026-12-25T10:00:00", True),
    ("2026-07-07T16:10:00", True),  # Daylight time uses the same local hours.
    ("2026-07-07T18:00:00", False),
]


@pytest.mark.parametrize("value,paused", CASES)
@pytest.mark.parametrize("imbalance", [-.2, .2])
def test_qualified_depth_holds_during_session_pause(value, paused, imbalance):
    now = datetime.fromisoformat(value).replace(tzinfo=ET).astimezone(timezone.utc)
    signal = topbot_strategy.evaluate([], observation=observation(now, imbalance), now=now)
    assert signal.action == ("HOLD" if paused else "BUY" if imbalance < 0 else "SELL")
    assert signal.raw_payload["live_routing_allowed"] is not paused
    if paused:
        assert signal.raw_payload["hold_reason"] in {"exchange_session_closed", "entry_too_close_to_session_close"}
        assert signal.price is None
        assert signal.candle_timestamp is None


def test_closed_session_evaluation_does_not_record_a_blocked_trade(db_session, monkeypatch):
    from app.services import bot_service
    from test_bot_execution_safety import USER_A, RecordingClient, _add_account_and_config

    closed_at = datetime(2026, 11, 3, 16, 10, tzinfo=ET)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return closed_at.astimezone(tz) if tz else closed_at.replace(tzinfo=None)
    monkeypatch.setattr(topbot_strategy, "datetime", Clock)
    monkeypatch.setattr(topbot_depth.feed, "ensure", lambda *a, **kw: None)
    monkeypatch.setattr(topbot_depth.feed, "snapshot", lambda *a, **kw: observation(closed_at))
    account, config = _add_account_and_config(db_session)
    config.strategy_type = "topbot_adaptive"
    config.strategy_params = dict(topbot_strategy.DEFAULTS)
    db_session.commit()
    client = RecordingClient()
    result = bot_service.evaluate_bot_config(db_session, user_id=USER_A, config=config,
                                            account=account, client=client, dry_run=True)
    assert result.status == "held"
    assert result.decision.action == "HOLD"
    assert result.order_attempt is None
    assert result.risk_events == []
    assert client.place_order_calls == []


@pytest.mark.parametrize("dry_run", [True, False])
def test_worker_stays_armed_and_resumes_without_consuming_closed_session_episodes(
    session_factory, monkeypatch, dry_run,
):
    _seed_run(session_factory, dry_run=dry_run)
    with session_factory() as db:
        config = db.get(BotConfig, 1)
        config.strategy_type = "topbot_adaptive"
        config.strategy_params = dict(topbot_strategy.DEFAULTS)
        run = db.get(BotRun, 1)
        run.raw_state = {**run.raw_state, "dom_episode_evaluated": "previous-episode"}
        db.commit()
    runtime = _runtime(session_factory)
    monkeypatch.setattr(runtime, "_client_for_user", lambda owner: object())
    monkeypatch.setattr(worker, "_background_live_execution_enabled", lambda: True)
    monkeypatch.setattr(worker, "_evaluation_is_due", lambda *a, **kw: True)
    monkeypatch.setattr(topbot_depth.feed, "ensure", lambda *a, **kw: None)
    monkeypatch.setattr(market_archive, "prepare_decision", lambda result: None)
    monkeypatch.setattr(forward_results, "prepare_committed_record", lambda result: None)
    protection_polls = []
    def poll(*args):
        protection_polls.append(args[:2])
        return {"trigger": None}
    monkeypatch.setattr(topbot_profit_guard, "poll", poll)
    evaluations = []
    def evaluate(db, **kwargs):
        evaluations.append(kwargs["run"].id)
        return SimpleNamespace(run=kwargs["run"], risk_events=[], status="held")
    monkeypatch.setattr(worker, "evaluate_bot_config", evaluate)
    for value in ["2026-11-03T16:08", "2026-11-03T16:10", "2026-11-03T17:59:59", "2026-11-03T18:00"]:
        now = datetime.fromisoformat(value).replace(tzinfo=ET)
        monkeypatch.setattr(topbot_depth.feed, "snapshot", lambda *a, **kw: observation(now))
        reopened = now.hour == 18
        result = runtime._process_run(run_id=1, now=now, startup_recovery=False,
                                     blocked_accounts=set(), probe_provider=False, probed_users=set())
        assert result["evaluated"] is reopened
        with session_factory() as db:
            run = db.get(BotRun, 1)
            assert run.status == "running"
            assert db.get(BotConfig, 1).enabled
            assert run.raw_state["dom_episode_evaluated"] == (
                observation(now)["episode_at"] if reopened else "previous-episode")
    assert evaluations == [1]
    assert len(protection_polls) == (0 if dry_run else 4)
