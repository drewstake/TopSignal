import asyncio
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.bot_worker as worker_module
from app.bot_worker import (
    BotWorkerRuntime,
    BotWorkerSettings,
    continuous_start_availability,
    inspect_bot_runtime,
    latest_closed_candle_boundary,
)
from app.db import Base
from app.models import (
    Account,
    AccountEmergencyAction,
    BotConfig,
    BotOrderAttempt,
    BotRun,
    BotRuntimeLease,
)
from app.services.projectx_client import ProjectXClientError


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def _settings(**overrides):
    values = {
        "enabled": True,
        "poll_seconds": 1,
        "candle_close_grace_seconds": 0,
        "lease_ttl_seconds": 45,
        "lease_heartbeat_seconds": 10,
        "provider_probe_seconds": 60,
        "max_schedule_jitter_seconds": 0,
        "max_retry_backoff_seconds": 300,
        "shutdown_timeout_seconds": 1,
    }
    values.update(overrides)
    return BotWorkerSettings(**values)


def _runtime(session_factory, *, owner="unused"):
    return BotWorkerRuntime(
        session_factory=session_factory,
        client_factory=lambda *_args, **_kwargs: owner,
        settings=_settings(),
    )


def _seed_run(
    session_factory,
    *,
    config_id=1,
    run_id=1,
    account_id=101,
    dry_run=True,
    continuous=True,
    started_at=None,
):
    user_id = "user-a"
    with session_factory() as db:
        if db.query(Account).filter(Account.external_id == str(account_id)).first() is None:
            db.add(
                Account(
                    user_id=user_id,
                    provider="projectx",
                    external_id=str(account_id),
                    trade_data_source="projectx",
                    name="Practice",
                    account_state="ACTIVE",
                    can_trade=True,
                    is_visible=True,
                )
            )
        db.add(
            BotConfig(
                id=config_id,
                user_id=user_id,
                account_id=account_id,
                name=f"bot-{config_id}",
                enabled=True,
                execution_mode="dry_run" if dry_run else "live",
                strategy_type="sma_cross",
                contract_id="CON.F.US.MNQ.H26",
                timeframe_unit="minute",
                timeframe_unit_number=5,
                lookback_bars=25,
                fast_period=2,
                slow_period=3,
                order_size=1,
                max_contracts=1,
                max_daily_loss=100,
                max_trades_per_day=1,
                max_open_position=1,
                allowed_contracts=["CON.F.US.MNQ.H26"],
            )
        )
        db.add(
            BotRun(
                id=run_id,
                user_id=user_id,
                bot_config_id=config_id,
                account_id=account_id,
                status="running",
                dry_run=dry_run,
                started_at=started_at or datetime.now(timezone.utc),
                last_heartbeat_at=datetime.now(timezone.utc),
                raw_state={
                    "source": "manual_start",
                    "continuous": continuous,
                    "execution_mode": "dry_run" if dry_run else "live",
                    "live_routing_confirmed": not dry_run,
                },
            )
        )
        db.commit()


def test_database_lease_allows_only_one_owner_and_expired_takeover(session_factory):
    first = _runtime(session_factory)
    second = _runtime(session_factory)

    assert first._acquire_or_renew_lease() is True
    assert second._acquire_or_renew_lease() is False

    with session_factory() as db:
        lease = db.query(BotRuntimeLease).one()
        lease.heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=2)
        lease.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    assert second._acquire_or_renew_lease() is True


def test_continuous_start_rejects_disabled_worker_without_mutating_runs(session_factory):
    runtime = BotWorkerRuntime(
        session_factory=session_factory,
        client_factory=lambda *_args, **_kwargs: None,
        settings=_settings(enabled=False),
    )
    with session_factory() as db:
        assert continuous_start_availability(db, runtime=runtime) == (
            False,
            "bot_worker_disabled",
        )
        assert db.query(BotRun).count() == 0


def test_idle_runtime_status_exposes_real_worker_and_lease_capability(session_factory):
    disabled_runtime = BotWorkerRuntime(
        session_factory=session_factory,
        client_factory=lambda *_args, **_kwargs: None,
        settings=_settings(enabled=False),
    )
    with session_factory() as db:
        disabled = inspect_bot_runtime(db, runtime=disabled_runtime)

    # An API-only process may be healthy while idle, but its status must not
    # advertise a worker or lease that does not exist to the arming UI.
    assert disabled.ready is True
    assert disabled.checks["worker_enabled"] is False
    assert disabled.checks["worker_task_healthy"] is False
    assert disabled.checks["lease_healthy"] is False
    assert disabled.checks["live_gate"] is False

    enabled_runtime = _runtime(session_factory)
    with session_factory() as db:
        before_lease = inspect_bot_runtime(db, runtime=enabled_runtime)
    assert before_lease.ready is False
    assert before_lease.checks["worker_enabled"] is True
    assert before_lease.checks["lease_healthy"] is False

    assert enabled_runtime._acquire_or_renew_lease() is True
    with session_factory() as db:
        after_lease = inspect_bot_runtime(db, runtime=enabled_runtime)
    assert after_lease.ready is False
    assert after_lease.checks["lease_healthy"] is True
    assert after_lease.checks["worker_task_healthy"] is False
    assert after_lease.checks["live_gate"] is False

    enabled_runtime._runner_task = SimpleNamespace(done=lambda: False)
    enabled_runtime._touch_runner_heartbeat()
    with session_factory() as db:
        with_live_task = inspect_bot_runtime(db, runtime=enabled_runtime)
    assert with_live_task.ready is True
    assert with_live_task.checks["worker_task_healthy"] is True


def test_continuous_admission_rejects_completed_or_stale_local_worker_task(session_factory):
    runtime = _runtime(session_factory)
    assert runtime._acquire_or_renew_lease() is True

    runtime._runner_task = SimpleNamespace(done=lambda: True)
    with session_factory() as db:
        assert continuous_start_availability(db, runtime=runtime) == (
            False,
            "bot_worker_unhealthy",
        )

    runtime._runner_task = SimpleNamespace(done=lambda: False)
    # A separate lease-heartbeat task may still be renewing successfully while
    # the evaluator runner itself is hung. Its fresh snapshot heartbeat must
    # not mask the stale runner-only heartbeat.
    runtime._replace_snapshot(
        state="running",
        last_heartbeat_at=datetime.now(timezone.utc),
    )
    runtime._touch_runner_heartbeat(
        now=datetime.now(timezone.utc) - timedelta(seconds=46)
    )
    with session_factory() as db:
        assert continuous_start_availability(db, runtime=runtime) == (
            False,
            "bot_worker_unhealthy",
        )

    runtime._touch_runner_heartbeat()
    with session_factory() as db:
        assert continuous_start_availability(db, runtime=runtime) == (True, None)


def test_runtime_status_surfaces_only_latest_account_emergency_latch(session_factory):
    runtime = BotWorkerRuntime(
        session_factory=session_factory,
        client_factory=lambda *_args, **_kwargs: None,
        settings=_settings(enabled=False),
    )
    now = datetime.now(timezone.utc)
    with session_factory() as db:
        db.add(
            AccountEmergencyAction(
                user_id="user-a",
                account_id=101,
                status="unconfirmed",
                confirmed_flat=False,
                request_payload={},
                completed_at=now,
            )
        )
        db.commit()
        blocked = inspect_bot_runtime(db, runtime=runtime, user_id="user-a")
        assert blocked.checks["account_emergency_clear"] is False
        assert blocked.counts["unresolved_account_emergency_actions"] == 1

        db.add(
            AccountEmergencyAction(
                user_id="user-a",
                account_id=101,
                status="confirmed_account_flat",
                confirmed_flat=True,
                request_payload={},
                result_payload={"confirmed_flat": True},
                completed_at=now + timedelta(seconds=1),
            )
        )
        db.commit()
        cleared = inspect_bot_runtime(db, runtime=runtime, user_id="user-a")

    assert cleared.checks["account_emergency_clear"] is True
    assert cleared.counts["unresolved_account_emergency_actions"] == 0


def test_continuous_live_admission_requires_independent_worker_live_gate(
    session_factory, monkeypatch
):
    runtime = _runtime(session_factory)
    monkeypatch.setenv("TOPSIGNAL_LIVE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION", "false")

    with session_factory() as db:
        assert continuous_start_availability(
            db,
            runtime=runtime,
            requested_live=True,
        ) == (False, "bot_worker_live_execution_disabled")
        assert db.query(BotRun).count() == 0


def test_worker_never_adopts_a_noncontinuous_or_terminal_run(session_factory):
    _seed_run(session_factory, continuous=False)
    runtime = _runtime(session_factory)
    runtime._run_cycle(startup_recovery=True)

    with session_factory() as db:
        run = db.query(BotRun).one()
        assert run.status == "blocked"
        assert run.stop_reason == "run_not_armed_for_continuous_execution"


def test_worker_closes_running_authorization_when_config_was_disabled(session_factory):
    _seed_run(session_factory)
    with session_factory() as db:
        db.get(BotConfig, 1).enabled = False
        db.commit()

    runtime = _runtime(session_factory)
    runtime._run_cycle(startup_recovery=True)

    with session_factory() as db:
        run = db.get(BotRun, 1)
        assert run.status == "stopped"
        assert run.stop_reason == "config_disabled"


def test_worker_retains_a_stream_that_did_not_finish_stopping(session_factory, monkeypatch):
    _seed_run(session_factory, dry_run=False)
    runtime = _runtime(session_factory)

    class StoppingRuntime:
        start_calls = 0
        stop_calls = 0

        def start(self):
            self.start_calls += 1

        def stop(self):
            self.stop_calls += 1
            return False

    stopping = StoppingRuntime()
    key = ("user-a", 101)
    runtime._account_streams[key] = stopping  # type: ignore[assignment]
    monkeypatch.setattr(
        worker_module,
        "create_streaming_runtime",
        lambda **_kwargs: pytest.fail("a still-live stream must not be replaced"),
    )

    runtime._sync_account_streams([])
    assert runtime._account_streams[key] is stopping
    assert stopping.stop_calls == 1

    runtime._sync_account_streams([1])
    assert runtime._account_streams[key] is stopping
    assert stopping.start_calls == 1


def test_worker_evaluates_at_most_once_for_a_closed_boundary(session_factory, monkeypatch):
    _seed_run(session_factory)
    evaluations = []
    evaluation_options = []

    class Client:
        def list_accounts(self, **_kwargs):
            return [{"id": 101}]

    runtime = BotWorkerRuntime(
        session_factory=session_factory,
        client_factory=lambda *_args, **_kwargs: Client(),
        settings=_settings(),
    )
    monkeypatch.setattr(runtime, "_sync_account_streams", lambda _run_ids: None)

    def evaluate(db, **kwargs):
        evaluations.append(kwargs["run"].id)
        evaluation_options.append(kwargs)
        return SimpleNamespace(run=kwargs["run"], risk_events=[])

    monkeypatch.setattr(worker_module, "evaluate_bot_config", evaluate)
    first_cycle = runtime._run_cycle(startup_recovery=True)
    second_cycle = runtime._run_cycle(startup_recovery=False)

    assert evaluations == [1]
    assert first_cycle["errors"] == 0
    assert second_cycle["errors"] == 0
    assert evaluation_options[0]["preserve_run_on_transient_pre_routing_error"] is True
    lease_token = evaluation_options[0]["worker_lease_token"]
    assert lease_token.lease_name == worker_module._LEASE_NAME
    assert lease_token.owner_id == runtime.owner_id
    assert lease_token.lease_ttl_seconds == runtime.settings.lease_ttl_seconds


def test_stale_cycle_cannot_overwrite_lease_lost_snapshot(session_factory, monkeypatch):
    runtime = _runtime(session_factory)

    def stale_cycle(_startup_recovery):
        runtime._replace_snapshot(
            state="lease_lost",
            owns_lease=False,
            last_error_code="worker_lease_lost",
        )
        runtime._stop_event.set()
        return {
            "errors": 0,
            "last_error_code": None,
            "provider_status": "ok",
            "last_provider_success_at": datetime.now(timezone.utc),
            "last_provider_check_at": datetime.now(timezone.utc),
            "active_runs": 1,
            "evaluated_runs": 1,
            "retrying_runs": 0,
            "unresolved": 0,
        }

    monkeypatch.setattr(runtime, "_acquire_or_renew_lease", lambda: True)
    monkeypatch.setattr(runtime, "_run_cycle", stale_cycle)
    monkeypatch.setattr(runtime, "_stop_account_streams", lambda: None)

    asyncio.run(runtime._run_forever())

    snapshot = runtime.snapshot()
    assert snapshot.state == "lease_lost"
    assert snapshot.owns_lease is False
    assert snapshot.last_error_code == "worker_lease_lost"


def test_latest_closed_boundary_uses_cme_trading_day_rollover():
    # 2026-07-06 18:05 America/New_York is 22:05 UTC (EDT). The daily
    # scheduling boundary is the CME 18:00 local rollover, not UTC midnight.
    observed = datetime(2026, 7, 6, 22, 5, tzinfo=timezone.utc)

    assert latest_closed_candle_boundary(unit="day", unit_number=1, now=observed) == datetime(
        2026, 7, 6, 22, 0, tzinfo=timezone.utc
    )


def test_daily_boundary_skips_a_full_cme_equity_holiday():
    observed = datetime(2026, 12, 25, 17, 0, tzinfo=timezone.utc)

    assert latest_closed_candle_boundary(
        unit="day",
        unit_number=1,
        symbol="MNQ",
        now=observed,
    ) == datetime(2026, 12, 23, 23, 0, tzinfo=timezone.utc)


def test_provider_429_uses_shared_user_backoff_without_cycle_retry_storm(
    session_factory, monkeypatch
):
    _seed_run(session_factory, config_id=1, run_id=1, account_id=101)
    _seed_run(session_factory, config_id=2, run_id=2, account_id=102)
    list_calls = []

    class Client:
        def list_accounts(self, **_kwargs):
            list_calls.append(True)
            raise ProjectXClientError("throttled", status_code=429)

    runtime = BotWorkerRuntime(
        session_factory=session_factory,
        client_factory=lambda *_args, **_kwargs: Client(),
        settings=_settings(),
    )
    monkeypatch.setattr(runtime, "_sync_account_streams", lambda _run_ids: None)
    monkeypatch.setattr(
        worker_module,
        "evaluate_bot_config",
        lambda *_args, **_kwargs: pytest.fail("evaluation must not run after a failed probe"),
    )

    cycle = runtime._run_cycle(startup_recovery=True)

    assert len(list_calls) == 1
    assert cycle["errors"] == 1
    assert cycle["provider_status"] == "throttled"
    assert cycle["retrying_runs"] == 1


def test_provider_success_cannot_mask_another_runs_outage(session_factory, monkeypatch):
    _seed_run(session_factory, config_id=1, run_id=1, account_id=101)
    _seed_run(session_factory, config_id=2, run_id=2, account_id=102)
    runtime = _runtime(session_factory)
    monkeypatch.setattr(runtime, "_sync_account_streams", lambda _ids: None)
    calls = []

    def process(**kwargs):
        calls.append(kwargs["run_id"])
        if len(calls) == 1:
            raise ProjectXClientError("fixture outage", status_code=503)
        return {"evaluated": True, "provider_ok": True}

    monkeypatch.setattr(runtime, "_process_run", process)
    assert runtime._run_cycle(startup_recovery=False)["provider_status"] == "error"
    # The failed run remains in backoff while a healthy peer evaluates again.
    assert runtime._run_cycle(startup_recovery=False)["provider_status"] == "error"


def test_backoff_honors_provider_retry_after_beyond_local_maximum():
    assert worker_module._retry_delay_seconds(
        ProjectXClientError("throttled", status_code=429, retry_after_seconds=600),
        failures=1, key="fixture", maximum=300,
    ) == 600


def test_duplicate_legacy_running_rows_are_closed_before_selection(session_factory):
    _seed_run(session_factory, run_id=1, started_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    engine = session_factory.kw["bind"]
    with engine.begin() as connection:
        connection.execute(text("drop index uq_bot_runs_one_running_per_config"))
        connection.exec_driver_sql(
                "insert into bot_runs "
                "(id,user_id,bot_config_id,account_id,status,dry_run,started_at,last_heartbeat_at,raw_state) "
                "values (2,'user-a',1,101,'running',1,'2026-01-02','2026-01-02',"
                "'{\"source\":\"manual_start\",\"continuous\":true,\"execution_mode\":\"dry_run\"}')"
        )
    with session_factory() as db:
        selected = worker_module._select_latest_running_run_ids(
            db, now=datetime(2026, 1, 3, tzinfo=timezone.utc)
        )
        db.commit()

    assert selected == [2]
    with session_factory() as db:
        assert db.get(BotRun, 1).status == "stopped"


def test_readiness_surfaces_unresolved_submission_and_classification_wait(session_factory):
    _seed_run(session_factory, dry_run=False)
    runtime = _runtime(session_factory)
    assert runtime._acquire_or_renew_lease()
    with session_factory() as db:
        db.add(
            BotOrderAttempt(
                user_id="user-a",
                bot_config_id=1,
                bot_run_id=1,
                account_id=101,
                contract_id="CON.F.US.MNQ.H26",
                execution_mode="live",
                side="BUY",
                order_type="market",
                size=1,
                status="submission_unknown",
            )
        )
        db.commit()
        status = inspect_bot_runtime(db, runtime=runtime, user_id="user-a")

    assert status.ready is False
    assert status.checks["submissions_reconciled"] is False
    assert status.checks["account_classification_fresh"] is False
    assert status.counts["live_runs_awaiting_account_classification"] == 1


def test_unexpected_runner_failure_is_visible_in_snapshot(session_factory):
    runtime = _runtime(session_factory)

    async def exercise():
        task = asyncio.create_task(_explode())
        await asyncio.sleep(0)
        runtime._runner_finished(task)

    async def _explode():
        raise RuntimeError("boom")

    asyncio.run(exercise())
    assert runtime.snapshot().state == "crashed"
    assert runtime.snapshot().last_error_code == "worker_task_crashed"
    with session_factory() as db:
        readiness = inspect_bot_runtime(db, runtime=runtime)
    assert readiness.ready is False
    assert readiness.checks["worker_task_healthy"] is False


def test_worker_recovery_disarms_routing_runs_before_streams_or_provider_access(session_factory, monkeypatch):
    _seed_run(session_factory, dry_run=False)
    runtime = _runtime(session_factory)
    streamed = []
    monkeypatch.setattr(runtime, "_sync_account_streams", lambda ids: streamed.extend(ids))
    monkeypatch.setattr(runtime, "_client_for_user", lambda user: pytest.fail("recovery must not contact provider"))

    cycle = runtime._run_cycle(startup_recovery=True)

    assert streamed == []
    assert cycle["active_runs"] == 0
    with session_factory() as db:
        run = db.get(BotRun, 1)
        assert run.status == "stopped"
        assert run.stop_reason == "worker_restart_requires_rearm"
        assert run.raw_state["live_routing_confirmed"] is False
        assert db.get(BotConfig, 1).execution_mode == "live"
        assert db.get(BotConfig, 1).enabled is True


def test_worker_shutdown_timeout_retains_inflight_task_and_does_not_release_lease(session_factory, monkeypatch):
    from threading import Event

    runtime = _runtime(session_factory)
    runtime.settings = _settings(shutdown_timeout_seconds=0.02)
    entered, release = Event(), Event()
    released_leases = []
    monkeypatch.setattr(runtime, "_release_lease", lambda: released_leases.append(True))
    monkeypatch.setattr(runtime, "_stop_account_streams", lambda: None)

    def in_flight():
        entered.set()
        release.wait(timeout=2)

    async def exercise():
        runtime._runner_task = asyncio.create_task(asyncio.to_thread(in_flight))
        await asyncio.to_thread(entered.wait, 1)
        task = runtime._runner_task
        try:
            assert await runtime.stop() is False
            assert runtime._shutdown_requested.is_set()
            assert runtime._runner_task is task
            assert not task.cancelled()
            assert not task.done()
            assert released_leases == []
            assert runtime.snapshot().last_error_code == "worker_shutdown_incomplete"
            await runtime.start()
            assert runtime._runner_task is task
        finally:
            release.set()
        assert await runtime.stop() is True
        assert released_leases == [True]
        assert runtime._runner_task is None

    asyncio.run(exercise())


def test_unexpected_cycle_cancellation_revokes_routing_before_draining_thread(session_factory, monkeypatch):
    from threading import Event

    runtime = _runtime(session_factory)
    entered, release = Event(), Event()
    finished = []

    def cycle(_recovery):
        entered.set()
        release.wait(timeout=2)
        finished.append(True)
        return {}

    monkeypatch.setattr(runtime, "_run_cycle", cycle)
    runtime._replace_snapshot(owns_lease=True)

    async def exercise():
        task = asyncio.create_task(runtime._run_cycle_async(False))
        await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0.01)
        try:
            assert not task.done()
            assert runtime._shutdown_requested.is_set()
            assert runtime.snapshot().owns_lease is False
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished == [True]
        runtime._runner_finished(task)
        assert runtime.snapshot().last_error_code == "worker_task_cancelled"
        assert runtime.snapshot().state == "crashed"

    asyncio.run(exercise())


def test_outage_backoff_remains_bounded_after_days_of_failures():
    assert worker_module._retry_delay_seconds(
        ProjectXClientError("offline", status_code=503),
        failures=100000,
        key="outage",
        maximum=300,
    ) == 300


def test_cancelling_lease_renewal_waits_for_database_operation(session_factory, monkeypatch):
    from threading import Event

    runtime = _runtime(session_factory)
    entered, release = Event(), Event()
    finished = []

    def renewal():
        entered.set()
        release.wait(timeout=2)
        finished.append(True)
        return True

    monkeypatch.setattr(runtime, "_acquire_or_renew_lease", renewal)

    async def exercise():
        task = asyncio.create_task(runtime._renew_lease_async())
        await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0.01)
        try:
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished == [True]

    asyncio.run(exercise())


def test_worker_does_not_report_shutdown_complete_with_unreaped_stream(session_factory):
    runtime = _runtime(session_factory)
    runtime._account_streams[("fixture", 101)] = SimpleNamespace(stop=lambda: False)

    assert asyncio.run(runtime.stop()) is False
    assert runtime.snapshot().last_error_code == "streaming_shutdown_incomplete"
    assert runtime._account_streams


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_worker_rejects_nonfinite_environment_timing(monkeypatch, value):
    monkeypatch.setenv("TOPSIGNAL_BOT_WORKER_POLL_SECONDS", value)
    with pytest.raises(RuntimeError):
        BotWorkerSettings.from_env()
