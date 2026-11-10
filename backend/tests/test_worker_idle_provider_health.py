"""Entry pauses preserve real provider checks without allowing evaluations."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import bot_worker as worker
from app.models import BotConfig, BotRun
from app.services import topbot_depth, topbot_session, topbot_strategy
from test_bot_worker import session_factory, _runtime, _seed_run


def test_instrument_pause_survives_new_worker_and_preserves_provider_probes(session_factory, monkeypatch):
    _seed_run(session_factory, dry_run=False)
    now = datetime.now(timezone.utc)
    with session_factory() as db:
        config = db.get(BotConfig, 1)
        config.strategy_type = "topbot_adaptive"
        config.strategy_params = {**topbot_strategy.DEFAULTS, "practice_auto_resume": True}
        run = db.get(BotRun, 1)
        run.raw_state = {**run.raw_state, "instrument_pause": {
            "contract_id": config.contract_id, "rejected_at": now.isoformat(),
            "retry_at": (now + timedelta(minutes=5)).isoformat(), "rejections": 4,
        }}
        db.commit()
    runtime = _runtime(session_factory)
    reads = []
    def accounts(**kw):
        reads.append(kw)
        return [{"id": 101, "simulated": True, "can_trade": True, "status": "ACTIVE"}]
    monkeypatch.setattr(runtime, "_client_for_user", lambda _: SimpleNamespace(list_accounts=accounts))
    monkeypatch.setattr(worker, "_background_live_execution_enabled", lambda: True)
    protection_checks = []
    def protection(*args):
        protection_checks.append(args)
        return {"trigger": None}
    monkeypatch.setattr("app.services.topbot_profit_guard.poll", protection)
    monkeypatch.setattr(topbot_session, "runner_entry_pause_reason", lambda *a, **kw: None)
    monkeypatch.setattr(topbot_depth.feed, "ensure", lambda *a, **kw: None)
    monkeypatch.setattr(topbot_depth.feed, "snapshot", lambda *a, **kw: pytest.fail("paused entry observed depth"))
    monkeypatch.setattr(worker, "evaluate_bot_config", lambda *a, **kw: pytest.fail("paused entry evaluated"))
    with session_factory() as db:
        worker._disarm_recovered_live_runs(db, now=now)
        db.commit()
    result = runtime._process_run(run_id=1, now=now, startup_recovery=True,
        blocked_accounts=set(), probe_provider=True, probed_users=set())
    assert result == {"evaluated": False, "provider_ok": True}
    assert len(reads) == 1
    assert len(protection_checks) == 1
    with session_factory() as db:
        run = db.get(BotRun, 1)
        assert run.status == "running" and db.get(BotConfig, 1).enabled
        assert run.raw_state["instrument_pause"]["rejections"] == 4


@pytest.mark.parametrize("blocked", [False, True])
@pytest.mark.parametrize("probe", [False, True])
def test_entry_pause_keeps_probe_cadence_and_never_evaluates(session_factory, monkeypatch, blocked, probe):
    _seed_run(session_factory, dry_run=False)
    with session_factory() as db:
        config = db.get(BotConfig, 1)
        config.strategy_type = "topbot_adaptive"
        config.strategy_params = dict(topbot_strategy.DEFAULTS)
        db.commit()
    runtime = _runtime(session_factory)
    reads = []
    def list_accounts(**kwargs):
        reads.append(kwargs)
        return [{"id": 101, "simulated": True}]
    monkeypatch.setattr(runtime, "_client_for_user", lambda _: SimpleNamespace(list_accounts=list_accounts))
    monkeypatch.setattr(worker, "_evaluation_is_due", lambda *a, **kw: True)
    monkeypatch.setattr(worker, "_background_live_execution_enabled", lambda: False)
    monkeypatch.setattr(topbot_session, "runner_entry_pause_reason", lambda *a, **kw: "exchange_session_closed")
    monkeypatch.setattr(topbot_depth.feed, "ensure", lambda *a, **kw: None)
    monkeypatch.setattr(topbot_depth.feed, "snapshot", lambda *a, **kw: pytest.fail("paused entry observed depth"))
    monkeypatch.setattr(worker, "evaluate_bot_config", lambda *a, **kw: pytest.fail("paused entry evaluated"))
    probed = set()
    result = runtime._process_run(run_id=1, now=datetime.now(timezone.utc), startup_recovery=False,
        blocked_accounts={("user-a", 101)} if blocked else set(), probe_provider=probe, probed_users=probed)
    assert result == {"evaluated": False, "provider_ok": probe}
    assert len(reads) == int(probe)
    assert probed == ({"user-a"} if probe else set())
    with session_factory() as db:
        assert db.get(BotRun, 1).status == "running"
        assert db.get(BotConfig, 1).enabled


@pytest.mark.parametrize("fail", [False, True])
def test_blocked_account_probe_updates_cycle_health_and_honors_failure_backoff(session_factory, monkeypatch, fail):
    _seed_run(session_factory)
    runtime = _runtime(session_factory)
    calls = []
    def list_accounts(**kwargs):
        calls.append(kwargs)
        if fail:
            raise RuntimeError("provider unavailable")
        return [{"id": 101, "simulated": True}]
    monkeypatch.setattr(runtime, "_client_for_user", lambda _: SimpleNamespace(list_accounts=list_accounts))
    monkeypatch.setattr(runtime, "_sync_archive_collection", lambda: None)
    monkeypatch.setattr(runtime, "_sync_account_streams", lambda _: None)
    monkeypatch.setattr(worker, "_background_live_execution_enabled", lambda: False)
    monkeypatch.setattr(worker, "evaluate_bot_config", lambda *a, **kw: pytest.fail("blocked entry evaluated"))
    original = runtime._process_run
    def blocked_run(**kwargs):
        kwargs["blocked_accounts"] = {("user-a", 101)}
        return original(**kwargs)
    monkeypatch.setattr(runtime, "_process_run", blocked_run)
    runtime._replace_snapshot(provider_status="unknown")
    result = runtime._run_cycle(startup_recovery=False)
    assert result["provider_status"] == ("error" if fail else "ok")
    assert (result["last_provider_success_at"] is not None) is (not fail)
    assert result["evaluated_runs"] == 0
    assert len(calls) == 1
    if fail:
        runtime._run_cycle(startup_recovery=False)
        assert len(calls) == 1
