from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from test_bot_worker import session_factory, _seed_run, _runtime
from app import bot_worker as worker
from app.models import Account, BotConfig, BotRun
from app.services.topbot_strategy import DEFAULTS, normalize_params


def seed(session_factory):
    _seed_run(session_factory, dry_run=False)
    with session_factory() as db:
        config = db.get(BotConfig, 1)
        config.strategy_type = "topbot_adaptive"
        config.strategy_params = {**DEFAULTS, "practice_auto_resume": True}
        run = db.get(BotRun, 1)
        run.raw_state = {**run.raw_state, "dom_episode_evaluated": "prior-episode"}
        db.commit()


def recover(session_factory):
    with session_factory() as db:
        worker._disarm_recovered_live_runs(db, now=datetime.now(timezone.utc))
        db.commit()


def test_opted_in_run_survives_recovery_with_same_identity(session_factory):
    seed(session_factory)
    recover(session_factory)
    with session_factory() as db:
        run = db.get(BotRun, 1)
        assert run.status == "running" and db.get(BotConfig, 1).enabled
        assert run.raw_state["practice_auto_resume_pending"] is True
        assert run.raw_state["live_routing_confirmed"] is True
        assert run.raw_state["dom_episode_evaluated"] == "prior-episode"
        assert db.query(BotRun).count() == 1


@pytest.mark.parametrize("fault", ["opt_out", "legacy", "disabled", "unconfirmed", "manual_stop", "wrong_account"])
def test_recovery_never_resurrects_unauthorized_runs(session_factory, fault):
    seed(session_factory)
    with session_factory() as db:
        run, config = db.get(BotRun, 1), db.get(BotConfig, 1)
        if fault == "opt_out": config.strategy_params = {**DEFAULTS, "practice_auto_resume": False}
        if fault == "legacy": config.strategy_type = "sma_cross"
        if fault == "disabled": config.enabled = False
        if fault == "unconfirmed": run.raw_state = {**run.raw_state, "live_routing_confirmed": False}
        if fault == "manual_stop": worker.transition_bot_run(run, "stopped", reason="manual_stop")
        if fault == "wrong_account": config.account_id = 102
        db.commit()
    recover(session_factory)
    with session_factory() as db:
        run = db.get(BotRun, 1)
        assert run.status == "stopped"
        assert not run.raw_state.get("practice_auto_resume_pending")
        if fault == "manual_stop": assert run.stop_reason == "manual_stop"


@pytest.mark.parametrize("provider", [None, {"simulated": False}, {"simulated": None},
                                     {"can_trade": False}, {"status": "INACTIVE"}])
def test_resume_requires_fresh_provider_verification(session_factory, provider):
    seed(session_factory)
    recover(session_factory)
    rows = [] if provider is None else [dict(id=101, simulated=True, can_trade=True, status="ACTIVE") | provider]
    client = SimpleNamespace(list_accounts=lambda **kwargs: rows)
    runtime = _runtime(session_factory)
    with pytest.raises(ValueError, match="account_unverified"):
        runtime._validate_practice_resume(1, "user-a", 101, client, datetime.now(timezone.utc))
    with session_factory() as db:
        assert db.get(BotRun, 1).raw_state["practice_auto_resume_pending"] is True
    # A later successful probe recovers without an operator start or a new run.
    client.list_accounts = lambda **kwargs: [dict(id=101, simulated=True, can_trade=True, status="ACTIVE")]
    assert runtime._validate_practice_resume(1, "user-a", 101, client, datetime.now(timezone.utc))
    with session_factory() as db:
        run = db.get(BotRun, 1)
        assert not run.raw_state.get("practice_auto_resume_pending")
        assert run.raw_state["practice_auto_resumed_at"]
        assert db.query(Account).one().provider_simulated is True


def test_manual_stop_during_provider_probe_wins(session_factory):
    seed(session_factory)
    recover(session_factory)
    def probe(**kwargs):
        with session_factory() as db:
            worker.transition_bot_run(db.get(BotRun, 1), "stopped", reason="manual_stop")
            db.get(BotConfig, 1).enabled = False
            db.commit()
        return [dict(id=101, simulated=True, can_trade=True, status="ACTIVE")]
    assert not _runtime(session_factory)._validate_practice_resume(
        1, "user-a", 101, SimpleNamespace(list_accounts=probe), datetime.now(timezone.utc))
    with session_factory() as db:
        assert db.get(BotRun, 1).stop_reason == "manual_stop"


def test_auto_resume_setting_is_boolean_and_opt_in():
    assert normalize_params()["practice_auto_resume"] is False
    assert normalize_params({**DEFAULTS, "practice_auto_resume": True})["practice_auto_resume"] is True
    with pytest.raises(ValueError): normalize_params({**DEFAULTS, "practice_auto_resume": "true"})


def test_recovering_worker_does_not_evaluate_before_practice_check(session_factory, monkeypatch):
    seed(session_factory)
    recover(session_factory)
    runtime = _runtime(session_factory)
    client = SimpleNamespace(list_accounts=lambda **kwargs: [])
    monkeypatch.setattr(runtime, "_client_for_user", lambda user: client)
    monkeypatch.setattr(worker, "evaluate_bot_config", lambda *a, **kw: pytest.fail("unverified recovery must not evaluate"))
    with pytest.raises(ValueError, match="account_unverified"):
        runtime._process_run(run_id=1, now=datetime.now(timezone.utc), startup_recovery=True,
                             blocked_accounts=set(), probe_provider=True, probed_users=set())
