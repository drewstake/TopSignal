from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app.bot_worker as worker
from app.models import BotConfig, BotRun
from app.services import topbot_profit_guard as guard, topbot_strategy, topbot_depth, forward_results, market_archive
from test_bot_worker import session_factory, _runtime, _seed_run


@pytest.mark.parametrize("result", ["daily_profit_target_reached", "daily_loss_limit_reached", "unavailable"])
def test_worker_checks_profit_protection_without_waiting_for_entry_signal(session_factory, monkeypatch, result):
    _seed_run(session_factory, dry_run=False)
    with session_factory() as db:
        config = db.get(BotConfig, 1)
        config.strategy_type = "topbot_adaptive"
        config.strategy_params = dict(topbot_strategy.DEFAULTS)
        db.commit()
    runtime = _runtime(session_factory)
    monkeypatch.setattr(worker, "_background_live_execution_enabled", lambda: True)
    monkeypatch.setattr(runtime, "_client_for_user", lambda owner: object())
    monkeypatch.setattr(worker, "_evaluation_is_due", lambda *a, **kw: False)
    monkeypatch.setattr(topbot_depth.feed, "ensure", lambda *a, **kw: None)
    monkeypatch.setattr(topbot_depth.feed, "snapshot", lambda *a, **kw: {"ready": False})
    calls = []
    def poll(*args, **kwargs):
        calls.append(args[:2])
        if result == "unavailable": raise ValueError("No account P&L")
        return {"trigger": result}
    monkeypatch.setattr(guard, "poll", poll)
    monkeypatch.setattr(worker, "evaluate_bot_config", lambda *a, **kw: pytest.fail("Must not evaluate entries"))
    runtime._process_run(run_id=1, now=datetime(2026, 11, 3, 15, tzinfo=timezone.utc), startup_recovery=False,
                         blocked_accounts=set(), probe_provider=False, probed_users=set())
    assert calls == [("user-a", 101)]
    with session_factory() as db:
        assert db.get(BotRun, 1).status == "running"
        assert db.get(BotConfig, 1).enabled


def test_next_session_can_evaluate_without_restarting_the_run(session_factory, monkeypatch):
    _seed_run(session_factory, dry_run=False)
    with session_factory() as db:
        config = db.get(BotConfig, 1)
        config.strategy_type = "topbot_adaptive"
        config.strategy_params = dict(topbot_strategy.DEFAULTS)
        db.commit()
    runtime = _runtime(session_factory)
    monkeypatch.setattr(worker, "_background_live_execution_enabled", lambda: True)
    monkeypatch.setattr(runtime, "_client_for_user", lambda owner: object())
    monkeypatch.setattr(worker, "_evaluation_is_due", lambda *a, **kw: True)
    monkeypatch.setattr(topbot_depth.feed, "ensure", lambda *a, **kw: None)
    monkeypatch.setattr(topbot_depth.feed, "snapshot", lambda *a, **kw: {"ready": False})
    monkeypatch.setattr(guard, "poll", lambda *a, **kw: {"trigger": None})
    monkeypatch.setattr(market_archive, "prepare_decision", lambda result: None)
    monkeypatch.setattr(forward_results, "prepare_committed_record", lambda result: None)
    evaluated = []
    def evaluate(db, **kwargs):
        evaluated.append(kwargs["run"].id)
        return SimpleNamespace(run=kwargs["run"], risk_events=[], status="held")
    monkeypatch.setattr(worker, "evaluate_bot_config", evaluate)
    runtime._process_run(run_id=1, now=datetime(2026, 11, 3, 15, tzinfo=timezone.utc), startup_recovery=False,
                         blocked_accounts=set(), probe_provider=False, probed_users=set())
    assert evaluated == [1]
