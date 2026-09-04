"""Bounded offline scheduling soak. SOAK_DURATION_SECONDS=60 extends the run.

Every provider read and evaluation is a fake; mutations fail the test. This
checks worker lifecycle/concurrency, not broker execution or strategy behavior.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
import threading
import time
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.bot_worker as worker_module
from app.bot_worker import BotWorkerRuntime, BotWorkerSettings
from app.db import Base
from app.models import Account, BotConfig, BotRun, BotRuntimeLease
from app.services.projectx_client import ProjectXClientError


def test_offline_worker_soak_recovers_failures_and_transfers_ownership(tmp_path, monkeypatch):
    duration = min(120.0, max(3.0, float(os.getenv("SOAK_DURATION_SECONDS", "3"))))
    baseline_threads = threading.active_count()
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'worker-soak.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 2},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    database_down, provider_down = threading.Event(), threading.Event()
    counts = {"evaluations": 0, "active": 0, "max_active": 0, "reads": 0, "mutations": 0, "db_failures": 0, "provider_failures": 0}
    mutex = threading.Lock()
    owners: set[str] = set()

    def session_factory():
        if database_down.is_set():
            with mutex:
                counts["db_failures"] += 1
            raise RuntimeError("fixture database unavailable")
        return factory()

    class FakeProvider:
        def list_accounts(self, **_kwargs):
            with mutex:
                counts["reads"] += 1
            if provider_down.is_set():
                with mutex:
                    counts["provider_failures"] += 1
                raise ProjectXClientError("fixture provider unavailable", status_code=503)
            return [{"id": 101, "simulated": True, "can_trade": True}]

        def place_order(self, **_kwargs):
            counts["mutations"] += 1
            raise AssertionError("offline soak must never route an order")

        cancel_order = close_position = place_order

    def evaluate(_db, **kwargs):
        assert kwargs["dry_run"] is True
        assert kwargs["confirm_live_order_routing"] is False
        token = kwargs["worker_lease_token"]
        assert token.mutation_allowed() is True
        with mutex:
            counts["active"] += 1
            counts["max_active"] = max(counts["max_active"], counts["active"])
            counts["evaluations"] += 1
            owners.add(token.owner_id)
        try:
            time.sleep(0.005)
            return SimpleNamespace(run=kwargs["run"], risk_events=[])
        finally:
            with mutex:
                counts["active"] -= 1

    monkeypatch.setattr(worker_module, "evaluate_bot_config", evaluate)
    with factory() as db:
        db.add(Account(user_id="fixture", provider="projectx", external_id="101", name="Practice", trade_data_source="projectx"))
        config = BotConfig(
            id=1, user_id="fixture", account_id=101, name="Offline soak", enabled=True,
            execution_mode="dry_run", strategy_type="sma_cross", contract_id="CON.F.US.MNQ.H26",
            timeframe_unit="second", timeframe_unit_number=1, lookback_bars=25, fast_period=2,
            slow_period=3, order_size=1, max_contracts=1, max_daily_loss=100,
            max_trades_per_day=1, max_open_position=1,
        )
        db.add(config)
        db.add(BotRun(
            id=1, user_id="fixture", account_id=101, bot_config_id=1, status="running", dry_run=True,
            started_at=datetime.now(timezone.utc), last_heartbeat_at=datetime.now(timezone.utc),
            raw_state={"source": "manual_start", "continuous": True, "execution_mode": "dry_run"},
        ))
        db.commit()

    settings = BotWorkerSettings(
        enabled=True, poll_seconds=0.03, lease_ttl_seconds=0.5,
        lease_heartbeat_seconds=0.1, provider_probe_seconds=0.06,
        max_retry_backoff_seconds=0.1, max_schedule_jitter_seconds=0,
        candle_close_grace_seconds=0, shutdown_timeout_seconds=3,
    )
    first = BotWorkerRuntime(session_factory=session_factory, client_factory=lambda *_args, **_kwargs: FakeProvider(), settings=settings)
    second = BotWorkerRuntime(session_factory=session_factory, client_factory=lambda *_args, **_kwargs: FakeProvider(), settings=settings)

    async def wait_until(predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate():
            assert time.monotonic() < deadline, "soak condition timed out"
            await asyncio.sleep(0.01)

    async def run():
        await first.start()
        await wait_until(lambda: counts["evaluations"] >= 1)
        await second.start()
        try:
            await asyncio.sleep(0.1)
            assert first.snapshot().owns_lease is True
            assert second.snapshot().owns_lease is False

            provider_down.set()
            await wait_until(lambda: counts["provider_failures"] > 0)
            await wait_until(lambda: first.snapshot().provider_status == "error")
            provider_down.clear()
            await wait_until(lambda: first.snapshot().provider_status == "ok")

            database_down.set()
            await wait_until(lambda: counts["db_failures"] >= 2)
            await wait_until(lambda: not first.snapshot().owns_lease and not second.snapshot().owns_lease)
            database_down.clear()
            await wait_until(lambda: first.snapshot().owns_lease or second.snapshot().owns_lease)

            assert await first.stop() is True
            await wait_until(lambda: second.snapshot().owns_lease)
            # Remove the prior candle watermark to verify restart adoption now.
            with factory() as db:
                run_row = db.get(BotRun, 1)
                state = dict(run_row.raw_state)
                state.pop("worker_last_scheduled_close_at", None)
                run_row.raw_state = state
                db.commit()
            await wait_until(lambda: second.owner_id in owners)

            # Reintroduce the former owner and hold both real runtimes active.
            await first.start()
            finish_at = time.monotonic() + duration
            while time.monotonic() < finish_at:
                with factory() as db:
                    assert db.query(BotRuntimeLease).count() == 1
                assert counts["max_active"] == 1
                assert counts["mutations"] == 0
                await asyncio.sleep(min(0.1, max(0, finish_at - time.monotonic())))
        finally:
            database_down.clear()
            provider_down.clear()
            results = await asyncio.gather(first.stop(), second.stop())
            assert results == [True, True]
        assert asyncio.all_tasks() == {asyncio.current_task()}
        with factory() as db:
            assert db.query(BotRuntimeLease).count() == 0
            assert db.get(BotRun, 1).status == "running"

    try:
        asyncio.run(run())
        assert counts["evaluations"] >= 2
        assert counts["max_active"] == 1
        assert counts["mutations"] == 0
        assert len(owners) == 2
        assert engine.pool.checkedout() == 0
        assert threading.active_count() <= baseline_threads
        print("OFFLINE_SOAK " + json.dumps({**counts, "duration_seconds": duration, "owners": len(owners), "checked_out_connections": engine.pool.checkedout(), "thread_delta": threading.active_count() - baseline_threads}, sort_keys=True))
    finally:
        engine.dispose()
