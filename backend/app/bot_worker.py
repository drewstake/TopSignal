from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import socket
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import monotonic
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import case, func, or_
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .models import (
    Account,
    AccountEmergencyAction,
    BotConfig,
    BotOrderAttempt,
    BotRun,
    BotRuntimeLease,
)
from .services.bot_execution_safety import (
    live_execution_environment_enabled,
    transition_bot_run,
)
from .services.bot_service import (
    BotWorkerLeaseToken,
    evaluate_bot_config,
    reconcile_unresolved_order_attempts,
)
from .services.projectx_client import PROJECTX_ERROR_NETWORK, ProjectXClient
from .services.projectx_streaming_runtime import StreamingRuntime, create_streaming_runtime
from .services.trading_day import (
    futures_session_is_open,
    trading_day_bounds_utc,
    trading_day_date,
)


logger = logging.getLogger(__name__)

_LEASE_NAME = "recurring-bot-evaluator-v1"
_TRUE_VALUES = frozenset({"1", "true", "yes", "y", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "n", "off"})
_INTRADAY_TIMEFRAME_SECONDS = {
    "second": 1,
    "minute": 60,
    "hour": 60 * 60,
}


@dataclass(frozen=True)
class BotWorkerSettings:
    enabled: bool = False
    poll_seconds: float = 5.0
    candle_close_grace_seconds: float = 2.0
    lease_ttl_seconds: float = 45.0
    lease_heartbeat_seconds: float = 10.0
    provider_probe_seconds: float = 60.0
    max_schedule_jitter_seconds: float = 3.0
    max_retry_backoff_seconds: float = 300.0
    shutdown_timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "BotWorkerSettings":
        settings = cls(
            enabled=_bool_env("TOPSIGNAL_BOT_WORKER_ENABLED", False),
            poll_seconds=_float_env("TOPSIGNAL_BOT_WORKER_POLL_SECONDS", 5.0, minimum=1.0),
            candle_close_grace_seconds=_float_env(
                "TOPSIGNAL_BOT_CANDLE_CLOSE_GRACE_SECONDS", 2.0, minimum=0.0
            ),
            lease_ttl_seconds=_float_env(
                "TOPSIGNAL_BOT_WORKER_LEASE_TTL_SECONDS", 45.0, minimum=15.0
            ),
            lease_heartbeat_seconds=_float_env(
                "TOPSIGNAL_BOT_WORKER_HEARTBEAT_SECONDS", 10.0, minimum=2.0
            ),
            provider_probe_seconds=_float_env(
                "TOPSIGNAL_BOT_PROVIDER_PROBE_SECONDS", 60.0, minimum=15.0
            ),
            max_schedule_jitter_seconds=_float_env(
                "TOPSIGNAL_BOT_MAX_SCHEDULE_JITTER_SECONDS", 3.0, minimum=0.0
            ),
            max_retry_backoff_seconds=_float_env(
                "TOPSIGNAL_BOT_MAX_RETRY_BACKOFF_SECONDS", 300.0, minimum=30.0
            ),
            shutdown_timeout_seconds=_float_env(
                "TOPSIGNAL_BOT_WORKER_SHUTDOWN_TIMEOUT_SECONDS", 30.0, minimum=1.0
            ),
        )
        if settings.lease_ttl_seconds <= settings.lease_heartbeat_seconds * 2:
            raise RuntimeError(
                "TOPSIGNAL_BOT_WORKER_LEASE_TTL_SECONDS must be more than twice "
                "TOPSIGNAL_BOT_WORKER_HEARTBEAT_SECONDS"
            )
        return settings


@dataclass(frozen=True)
class BotWorkerSnapshot:
    enabled: bool
    state: str
    owns_lease: bool
    started_at: datetime | None
    last_heartbeat_at: datetime | None
    last_cycle_completed_at: datetime | None
    last_success_at: datetime | None
    last_provider_success_at: datetime | None
    last_provider_check_at: datetime | None
    last_error_code: str | None
    provider_status: str
    active_runs: int
    evaluated_runs: int
    retrying_runs: int
    unresolved_live_submissions: int

    def public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in (
            "started_at",
            "last_heartbeat_at",
            "last_cycle_completed_at",
            "last_success_at",
            "last_provider_success_at",
            "last_provider_check_at",
        ):
            value = payload[key]
            payload[key] = _as_utc(value).isoformat() if value is not None else None
        return payload


@dataclass(frozen=True)
class BotRuntimeReadiness:
    ready: bool
    checks: dict[str, bool]
    counts: dict[str, int]
    state: str
    provider_status: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "state": self.state,
            "provider_status": self.provider_status,
            "checks": dict(self.checks),
            "counts": dict(self.counts),
        }


ClientFactory = Callable[..., ProjectXClient]


class BotWorkerRuntime:
    """Single-owner, closed-candle evaluator coordinated through the database.

    A BotConfig being enabled is not authorization to create or resurrect a
    run.  Only the latest still-running BotRun created by the explicit start
    endpoint is eligible for adoption after restart.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        client_factory: ClientFactory,
        settings: BotWorkerSettings | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.client_factory = client_factory
        self.settings = settings or BotWorkerSettings.from_env()
        host = socket.gethostname()[:64] or "unknown-host"
        self.owner_id = f"{host}:{os.getpid()}:{uuid4()}"
        self._stop_event = asyncio.Event()
        self._runner_task: asyncio.Task[None] | None = None
        self._lease_task: asyncio.Task[None] | None = None
        self._state_lock = Lock()
        self._runner_heartbeat_at: datetime | None = None
        self._snapshot = BotWorkerSnapshot(
            enabled=self.settings.enabled,
            state="disabled" if not self.settings.enabled else "starting",
            owns_lease=False,
            started_at=None,
            last_heartbeat_at=None,
            last_cycle_completed_at=None,
            last_success_at=None,
            last_provider_success_at=None,
            last_provider_check_at=None,
            last_error_code=None,
            provider_status="idle",
            active_runs=0,
            evaluated_runs=0,
            retrying_runs=0,
            unresolved_live_submissions=0,
        )
        self._last_provider_probe = 0.0
        self._retry_not_before_by_run: dict[int, float] = {}
        self._retry_failures_by_run: dict[int, int] = {}
        self._provider_retry_not_before_by_user: dict[str, float] = {}
        self._provider_retry_failures_by_user: dict[str, int] = {}
        self._reconcile_retry_not_before = 0.0
        self._reconcile_retry_failures = 0
        self._account_streams: dict[tuple[str, int], StreamingRuntime] = {}

    async def start(self) -> None:
        if not self.settings.enabled or self._runner_task is not None:
            return
        self._stop_event.clear()
        self._replace_snapshot(started_at=datetime.now(timezone.utc), state="electing")
        self._runner_task = asyncio.create_task(
            self._run_forever(), name="topsignal-recurring-bot-worker"
        )
        self._runner_task.add_done_callback(self._runner_finished)

    async def stop(self) -> None:
        if not self.settings.enabled:
            self._replace_snapshot(state="disabled", owns_lease=False)
            return
        self._stop_event.set()
        task = self._runner_task
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=self.settings.shutdown_timeout_seconds)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._runner_task = None
        await asyncio.to_thread(self._stop_account_streams)
        await asyncio.to_thread(self._release_lease)
        self._replace_snapshot(state="stopped", owns_lease=False)

    def snapshot(self) -> BotWorkerSnapshot:
        with self._state_lock:
            return self._snapshot

    def task_healthy(self, *, now: datetime | None = None) -> bool:
        return _local_worker_task_is_healthy(
            self,
            snapshot=self.snapshot(),
            now=_as_utc(now or datetime.now(timezone.utc)),
        )

    def _runner_finished(self, task: asyncio.Task[None]) -> None:
        if task.cancelled() or self._stop_event.is_set():
            return
        try:
            failure = task.exception()
        except asyncio.CancelledError:
            return
        if failure is not None:
            if self._lease_task is not None:
                self._lease_task.cancel()
            logger.critical(
                "bot_worker_task_crashed",
                extra={"error_type": type(failure).__name__},
            )
            self._replace_snapshot(
                state="crashed",
                owns_lease=False,
                last_error_code="worker_task_crashed",
            )

    async def _run_forever(self) -> None:
        recovered_for_ownership = False
        while not self._stop_event.is_set():
            try:
                owns_lease = await asyncio.to_thread(self._acquire_or_renew_lease)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_failure("lease_database_error", exc)
                owns_lease = False
            # This runner-only heartbeat proves the evaluator loop is still
            # iterating even while the independent lease task stays healthy.
            self._touch_runner_heartbeat()

            if not owns_lease:
                recovered_for_ownership = False
                await self._stop_lease_heartbeat()
                await asyncio.to_thread(self._stop_account_streams)
                self._replace_snapshot(state="standby", owns_lease=False)
                await self._wait(self.settings.poll_seconds)
                continue

            if self._lease_task is None:
                self._lease_task = asyncio.create_task(
                    self._lease_heartbeat_loop(), name="topsignal-bot-lease-heartbeat"
                )
            self._replace_snapshot(state="recovering" if not recovered_for_ownership else "running", owns_lease=True)
            try:
                cycle = await asyncio.to_thread(self._run_cycle, not recovered_for_ownership)
                now = datetime.now(timezone.utc)
                recovered_for_ownership = self._replace_snapshot_if_lease_owned(
                    state="running",
                    owns_lease=True,
                    last_cycle_completed_at=now,
                    last_success_at=now if cycle["errors"] == 0 else self.snapshot().last_success_at,
                    last_error_code=None if cycle["errors"] == 0 else cycle["last_error_code"],
                    provider_status=cycle["provider_status"],
                    last_provider_success_at=cycle["last_provider_success_at"],
                    last_provider_check_at=cycle["last_provider_check_at"],
                    active_runs=cycle["active_runs"],
                    evaluated_runs=cycle["evaluated_runs"],
                    retrying_runs=cycle["retrying_runs"],
                    unresolved_live_submissions=cycle["unresolved"],
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_failure("worker_cycle_error", exc)
            await self._wait(self.settings.poll_seconds)

        await self._stop_lease_heartbeat()
        await asyncio.to_thread(self._stop_account_streams)

    async def _lease_heartbeat_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                await self._wait(self.settings.lease_heartbeat_seconds)
                if self._stop_event.is_set():
                    break
                try:
                    renewed = await asyncio.to_thread(self._acquire_or_renew_lease)
                except Exception as exc:
                    self._record_failure("lease_heartbeat_error", exc)
                    renewed = False
                if not renewed:
                    self._replace_snapshot(state="lease_lost", owns_lease=False)
                    return
        finally:
            self._lease_task = None

    async def _stop_lease_heartbeat(self) -> None:
        task = self._lease_task
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._lease_task = None

    async def _wait(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def _run_cycle(self, startup_recovery: bool) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        errors = 0
        last_error_code: str | None = None
        evaluated_runs = 0
        prior_snapshot = self.snapshot()
        provider_status = _fresh_provider_status(
            prior_snapshot,
            now=now,
            max_age_seconds=self.settings.provider_probe_seconds * 2,
        )
        provider_success_at = prior_snapshot.last_provider_success_at
        provider_check_at = prior_snapshot.last_provider_check_at

        with self.session_factory() as db:
            run_ids = _select_latest_running_run_ids(db, now=now)
            db.commit()
            unresolved = _unresolved_live_submission_count(db)
        active_run_ids = set(run_ids)
        self._retry_not_before_by_run = {
            run_id: retry_at
            for run_id, retry_at in self._retry_not_before_by_run.items()
            if run_id in active_run_ids
        }
        self._retry_failures_by_run = {
            run_id: failures
            for run_id, failures in self._retry_failures_by_run.items()
            if run_id in active_run_ids
        }
        self._sync_account_streams(run_ids)

        blocked_accounts: set[tuple[str, int]] = set()
        if unresolved and monotonic() >= self._reconcile_retry_not_before:
            outcomes = self._reconcile_unresolved_accounts(now=now)
            unresolved = outcomes["unresolved"]
            blocked_accounts = outcomes["blocked_accounts"]
            if outcomes["errors"]:
                errors += outcomes["errors"]
                last_error_code = outcomes["last_error_code"]
                provider_status = outcomes["provider_status"]
                provider_check_at = now
                self._reconcile_retry_failures += 1
                self._reconcile_retry_not_before = monotonic() + _retry_delay_seconds(
                    outcomes["last_exception"],
                    failures=self._reconcile_retry_failures,
                    key="reconciliation",
                    maximum=self.settings.max_retry_backoff_seconds,
                )
            else:
                provider_status = "ok"
                provider_success_at = now
                provider_check_at = now
                self._reconcile_retry_failures = 0
                self._reconcile_retry_not_before = 0.0
        elif unresolved:
            blocked_accounts = self._unresolved_account_keys()

        probe_due = monotonic() - self._last_provider_probe >= self.settings.provider_probe_seconds
        probed_users: set[str] = set()
        for run_id in run_ids:
            if monotonic() < self._retry_not_before_by_run.get(run_id, 0.0):
                continue
            try:
                result = self._process_run(
                    run_id=run_id,
                    now=now,
                    startup_recovery=startup_recovery,
                    blocked_accounts=blocked_accounts,
                    probe_provider=probe_due,
                    probed_users=probed_users,
                )
                if result["evaluated"]:
                    evaluated_runs += 1
                if result["provider_ok"]:
                    provider_status = "ok"
                    provider_success_at = now
                    provider_check_at = now
                self._retry_not_before_by_run.pop(run_id, None)
                self._retry_failures_by_run.pop(run_id, None)
                if result.get("lease_lost"):
                    last_error_code = "worker_lease_lost"
                    break
            except Exception as exc:
                errors += 1
                last_error_code = _safe_error_code(exc)
                provider_status = "throttled" if getattr(exc, "status_code", None) == 429 else "error"
                provider_check_at = now
                failures = self._retry_failures_by_run.get(run_id, 0) + 1
                self._retry_failures_by_run[run_id] = failures
                self._retry_not_before_by_run[run_id] = monotonic() + _retry_delay_seconds(
                    exc,
                    failures=failures,
                    key=str(run_id),
                    maximum=self.settings.max_retry_backoff_seconds,
                )
                logger.error(
                    "bot_worker_run_failed",
                    extra={"bot_run_id": run_id, "error_type": type(exc).__name__},
                )
        if probe_due:
            self._last_provider_probe = monotonic()

        with self.session_factory() as db:
            active_runs = _active_armed_run_count(db)
            unresolved = _unresolved_live_submission_count(db)

        if active_runs and provider_status == "idle":
            provider_status = "unknown"
        return {
            "errors": errors,
            "last_error_code": last_error_code,
            "provider_status": provider_status,
            "last_provider_success_at": provider_success_at,
            "last_provider_check_at": provider_check_at,
            "active_runs": active_runs,
            "evaluated_runs": evaluated_runs,
            "retrying_runs": sum(
                monotonic() < retry_at
                for retry_at in self._retry_not_before_by_run.values()
            ),
            "unresolved": unresolved,
        }

    def _process_run(
        self,
        *,
        run_id: int,
        now: datetime,
        startup_recovery: bool,
        blocked_accounts: set[tuple[str, int]],
        probe_provider: bool,
        probed_users: set[str],
    ) -> dict[str, bool]:
        with self.session_factory() as db:
            row = (
                db.query(BotRun, BotConfig)
                .join(BotConfig, BotConfig.id == BotRun.bot_config_id)
                .filter(BotRun.id == run_id)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                return {"evaluated": False, "provider_ok": False}
            run, config = row
            invalid_reason = _run_disarm_reason(run, config)
            if invalid_reason is not None:
                transition_bot_run(run, "blocked", reason=invalid_reason, now=now)
                db.commit()
                logger.error(
                    "bot_worker_disarmed_invalid_run",
                    extra={"bot_run_id": int(run.id), "reason_code": invalid_reason},
                )
                return {"evaluated": False, "provider_ok": False}

            state = dict(run.raw_state) if isinstance(run.raw_state, dict) else {}
            if startup_recovery:
                state["worker_recovered_at"] = now.isoformat()
                state["worker_recovered_stale_heartbeat"] = _heartbeat_is_stale(
                    run.last_heartbeat_at,
                    now=now,
                    stale_after_seconds=self.settings.lease_ttl_seconds,
                )
            state["phase"] = "idle"
            run.raw_state = state
            run.last_heartbeat_at = now
            user_id = str(run.user_id)
            account_id = int(run.account_id)
            config_id = int(config.id)
            scheduled_close = latest_closed_candle_boundary(
                unit=str(config.timeframe_unit),
                unit_number=int(config.timeframe_unit_number),
                symbol=str(config.symbol or config.contract_id or ""),
                now=now,
                grace_seconds=self.settings.candle_close_grace_seconds,
            )
            evaluation_due = _evaluation_is_due(
                run,
                scheduled_close=scheduled_close,
                grace_seconds=self.settings.candle_close_grace_seconds,
            )
            evaluation_due = evaluation_due and now >= scheduled_close + timedelta(
                seconds=self.settings.candle_close_grace_seconds
                + _deterministic_jitter_seconds(
                    f"{user_id}:{config_id}:{scheduled_close.isoformat()}",
                    self.settings.max_schedule_jitter_seconds,
                )
            )
            db.commit()
            if (user_id, account_id) in blocked_accounts:
                return {"evaluated": False, "provider_ok": False}

        if monotonic() < self._provider_retry_not_before_by_user.get(user_id, 0.0):
            return {"evaluated": False, "provider_ok": False}
        try:
            client = self._client_for_user(user_id)
        except Exception as exc:
            self._schedule_provider_retry(user_id, exc)
            raise
        provider_ok = False
        if probe_provider and user_id not in probed_users:
            probed_users.add(user_id)
            try:
                client.list_accounts(only_active_accounts=False)
            except Exception as exc:
                self._schedule_provider_retry(user_id, exc)
                raise
            provider_ok = True
            self._clear_provider_retry(user_id)

        if not evaluation_due:
            return {"evaluated": False, "provider_ok": provider_ok}

        with self.session_factory() as db:
            row = (
                db.query(BotRun, BotConfig)
                .join(BotConfig, BotConfig.id == BotRun.bot_config_id)
                .filter(BotRun.id == run_id)
                .one_or_none()
            )
            if row is None:
                return {"evaluated": False, "provider_ok": provider_ok}
            current_run, current_config = row
            invalid_reason = _run_disarm_reason(current_run, current_config)
            if invalid_reason is not None:
                return {"evaluated": False, "provider_ok": provider_ok}
            live_run = not bool(current_run.dry_run)
            if live_run and not _background_live_execution_enabled():
                transition_bot_run(
                    current_run,
                    "blocked",
                    reason="background_live_execution_not_enabled",
                    now=now,
                )
                db.commit()
                return {"evaluated": False, "provider_ok": provider_ok}

            try:
                result = evaluate_bot_config(
                    db,
                    user_id=str(current_run.user_id),
                    config=current_config,
                    account=None,
                    client=client,
                    run=current_run,
                    dry_run=bool(current_run.dry_run),
                    confirm_live_order_routing=live_run,
                    preserve_run_on_transient_pre_routing_error=True,
                    worker_lease_token=BotWorkerLeaseToken(
                        lease_name=_LEASE_NAME,
                        owner_id=self.owner_id,
                        lease_ttl_seconds=self.settings.lease_ttl_seconds,
                    ),
                )
                lease_lost = any(
                    str(event.code) == "worker_lease_lost"
                    for event in result.risk_events
                )
                if result.run is not None and str(result.run.status) == "running":
                    state = dict(result.run.raw_state) if isinstance(result.run.raw_state, dict) else {}
                    if lease_lost:
                        state["phase"] = "retry_wait"
                    else:
                        state["worker_last_scheduled_close_at"] = scheduled_close.isoformat()
                        state["phase"] = "idle"
                    result.run.raw_state = state
                db.commit()
            except Exception as exc:
                # The evaluator either records a terminal audit or, for an
                # explicitly classified pre-routing transient, leaves the run
                # armed in retry_wait. Preserve either durable result.
                try:
                    db.commit()
                except Exception:
                    db.rollback()
                self._schedule_provider_retry(user_id, exc)
                raise
        if lease_lost:
            self._replace_snapshot(
                state="lease_lost",
                owns_lease=False,
                last_error_code="worker_lease_lost",
            )
            return {"evaluated": False, "provider_ok": provider_ok, "lease_lost": True}
        self._clear_provider_retry(user_id)
        return {"evaluated": True, "provider_ok": True, "lease_lost": False}

    def _schedule_provider_retry(self, user_id: str, exc: BaseException) -> None:
        if not _is_retryable_provider_failure(exc):
            return
        failures = self._provider_retry_failures_by_user.get(user_id, 0) + 1
        self._provider_retry_failures_by_user[user_id] = failures
        self._provider_retry_not_before_by_user[user_id] = monotonic() + _retry_delay_seconds(
            exc,
            failures=failures,
            key=f"provider:{user_id}",
            maximum=self.settings.max_retry_backoff_seconds,
        )

    def _clear_provider_retry(self, user_id: str) -> None:
        self._provider_retry_failures_by_user.pop(user_id, None)
        self._provider_retry_not_before_by_user.pop(user_id, None)

    def _reconcile_unresolved_accounts(self, *, now: datetime) -> dict[str, Any]:
        with self.session_factory() as db:
            account_keys = [
                (str(user_id), int(account_id))
                for user_id, account_id in (
                    db.query(BotOrderAttempt.user_id, BotOrderAttempt.account_id)
                    .filter(BotOrderAttempt.execution_mode == "live")
                    .filter(BotOrderAttempt.status.in_(["pending", "submission_unknown"]))
                    .distinct()
                    .all()
                )
            ]

        errors = 0
        last_error_code: str | None = None
        last_exception: BaseException | None = None
        provider_status = "ok"
        blocked_accounts: set[tuple[str, int]] = set()
        for user_id, account_id in account_keys:
            blocked_accounts.add((user_id, account_id))
            try:
                client = self._client_for_user(user_id)
                with self.session_factory() as db:
                    outcome = reconcile_unresolved_order_attempts(
                        db,
                        user_id=user_id,
                        account_id=account_id,
                        client=client,
                        now=now,
                    )
                    db.commit()
                if outcome.error is not None:
                    raise outcome.error
                if outcome.unresolved_count == 0:
                    blocked_accounts.discard((user_id, account_id))
            except Exception as exc:
                errors += 1
                last_exception = exc
                last_error_code = _safe_error_code(exc)
                provider_status = "throttled" if getattr(exc, "status_code", None) == 429 else "error"
                logger.error(
                    "bot_worker_reconciliation_failed",
                    extra={
                        "account_id": account_id,
                        "error_type": type(exc).__name__,
                    },
                )

        with self.session_factory() as db:
            unresolved = _unresolved_live_submission_count(db)
        return {
            "errors": errors,
            "last_error_code": last_error_code,
            "last_exception": last_exception,
            "provider_status": provider_status,
            "blocked_accounts": blocked_accounts,
            "unresolved": unresolved,
        }

    def _unresolved_account_keys(self) -> set[tuple[str, int]]:
        with self.session_factory() as db:
            return {
                (str(user_id), int(account_id))
                for user_id, account_id in (
                    db.query(BotOrderAttempt.user_id, BotOrderAttempt.account_id)
                    .filter(BotOrderAttempt.execution_mode == "live")
                    .filter(BotOrderAttempt.status.in_(["pending", "submission_unknown"]))
                    .distinct()
                    .all()
                )
            }

    def _client_for_user(self, user_id: str) -> ProjectXClient:
        with self.session_factory() as db:
            return self.client_factory(db, user_id=user_id)

    def _sync_account_streams(self, run_ids: list[int]) -> None:
        with self.session_factory() as db:
            rows = (
                db.query(BotRun, BotConfig)
                .join(BotConfig, BotConfig.id == BotRun.bot_config_id)
                .filter(BotRun.id.in_(run_ids) if run_ids else False)
                .all()
            )
            desired = {
                (str(run.user_id), int(run.account_id))
                for run, config in rows
                if not bool(run.dry_run) and _run_disarm_reason(run, config) is None
            }

        for key in tuple(self._account_streams):
            if key in desired:
                continue
            runtime = self._account_streams[key]
            if runtime.stop():
                self._account_streams.pop(key, None)
        for user_id, account_id in desired:
            key = (user_id, account_id)
            runtime = self._account_streams.get(key)
            if runtime is None:
                runtime = create_streaming_runtime(
                    user_id=user_id,
                    account_id=account_id,
                    client_factory=lambda scoped_user_id=user_id: self._client_for_user(
                        scoped_user_id
                    ),
                    market_hub_url="",
                )
                self._account_streams[key] = runtime
            runtime.start()

    def _stop_account_streams(self) -> None:
        for key, runtime in tuple(self._account_streams.items()):
            if runtime.stop():
                self._account_streams.pop(key, None)

    def _acquire_or_renew_lease(self) -> bool:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=self.settings.lease_ttl_seconds)
        snapshot = self.snapshot()
        details = {
            "state": snapshot.state,
            "provider_status": snapshot.provider_status,
            "active_runs": snapshot.active_runs,
            "retrying_runs": snapshot.retrying_runs,
            "unresolved_live_submissions": snapshot.unresolved_live_submissions,
            "last_error_code": snapshot.last_error_code,
            "last_provider_success_at": (
                _as_utc(snapshot.last_provider_success_at).isoformat()
                if snapshot.last_provider_success_at is not None
                else None
            ),
            "last_provider_check_at": (
                _as_utc(snapshot.last_provider_check_at).isoformat()
                if snapshot.last_provider_check_at is not None
                else None
            ),
        }
        with self.session_factory() as db:
            dialect = db.get_bind().dialect.name
            if dialect == "postgresql":
                insert = postgresql_insert(BotRuntimeLease)
            elif dialect == "sqlite":
                insert = sqlite_insert(BotRuntimeLease)
            else:
                raise RuntimeError(f"Unsupported bot worker lease dialect: {dialect}")
            statement = insert.values(
                lease_name=_LEASE_NAME,
                owner_id=self.owner_id,
                acquired_at=now,
                heartbeat_at=now,
                expires_at=expires_at,
                details=details,
            ).on_conflict_do_update(
                index_elements=[BotRuntimeLease.lease_name],
                set_={
                    "owner_id": self.owner_id,
                    "acquired_at": case(
                        (BotRuntimeLease.owner_id == self.owner_id, BotRuntimeLease.acquired_at),
                        else_=now,
                    ),
                    "heartbeat_at": now,
                    "expires_at": expires_at,
                    "details": details,
                },
                where=or_(
                    BotRuntimeLease.owner_id == self.owner_id,
                    BotRuntimeLease.expires_at <= now,
                ),
            )
            db.execute(statement)
            db.commit()
            owner = (
                db.query(BotRuntimeLease.owner_id)
                .filter(BotRuntimeLease.lease_name == _LEASE_NAME)
                .scalar()
            )
        acquired = owner == self.owner_id
        if acquired:
            self._replace_snapshot(
                owns_lease=True,
                last_heartbeat_at=now,
            )
        return acquired

    def _release_lease(self) -> None:
        try:
            with self.session_factory() as db:
                db.query(BotRuntimeLease).filter(
                    BotRuntimeLease.lease_name == _LEASE_NAME,
                    BotRuntimeLease.owner_id == self.owner_id,
                ).delete(synchronize_session=False)
                db.commit()
        except Exception as exc:
            logger.warning(
                "bot_worker_lease_release_failed",
                extra={"error_type": type(exc).__name__},
            )

    def _record_failure(self, code: str, exc: BaseException) -> None:
        logger.error(code, extra={"error_type": type(exc).__name__})
        self._replace_snapshot(
            state="error",
            last_error_code=code,
            provider_status="error" if "lease" not in code else self.snapshot().provider_status,
        )

    def _replace_snapshot(self, **changes: Any) -> None:
        with self._state_lock:
            current = asdict(self._snapshot)
            current.update(changes)
            self._snapshot = BotWorkerSnapshot(**current)

    def _replace_snapshot_if_lease_owned(self, **changes: Any) -> bool:
        """Atomically avoid reviving a cycle after its heartbeat lost ownership."""

        with self._state_lock:
            if not self._snapshot.owns_lease or self._snapshot.state == "lease_lost":
                return False
            current = asdict(self._snapshot)
            current.update(changes)
            self._snapshot = BotWorkerSnapshot(**current)
            return True

    def _touch_runner_heartbeat(self, *, now: datetime | None = None) -> None:
        with self._state_lock:
            self._runner_heartbeat_at = _as_utc(now or datetime.now(timezone.utc))

    def _runner_heartbeat(self) -> datetime | None:
        with self._state_lock:
            return self._runner_heartbeat_at


def latest_closed_candle_boundary(
    *,
    unit: str,
    unit_number: int,
    symbol: str | None = None,
    now: datetime | None = None,
    grace_seconds: float = 0.0,
) -> datetime:
    observed_at = _as_utc(now or datetime.now(timezone.utc)) - timedelta(
        seconds=max(float(grace_seconds), 0.0)
    )
    normalized_unit = str(unit).strip().lower()
    size = max(int(unit_number), 1)
    if normalized_unit == "month":
        month_index = observed_at.year * 12 + (observed_at.month - 1)
        group_index = (month_index // size) * size
        year, month_zero = divmod(group_index, 12)
        return _first_open_session_boundary(
            datetime(year, month_zero + 1, 1, tzinfo=timezone.utc).date(),
            symbol=symbol,
        )
    if normalized_unit == "week":
        trading_date = trading_day_date(observed_at)
        monday = trading_date - timedelta(days=trading_date.weekday())
        anchor = datetime(1970, 1, 5, tzinfo=timezone.utc).date()
        week_number = (monday - anchor).days // 7
        group_monday = anchor + timedelta(weeks=(week_number // size) * size)
        return _first_open_session_boundary(group_monday, symbol=symbol)
    if normalized_unit == "day":
        if size != 1:
            raise ValueError("Multi-day bot worker scheduling is not supported safely.")
        trading_date = trading_day_date(observed_at)
        return _most_recent_open_session_boundary(trading_date, symbol=symbol)
    seconds = _INTRADAY_TIMEFRAME_SECONDS.get(normalized_unit)
    if seconds is None:
        raise ValueError(f"Unsupported bot timeframe unit: {unit}")
    interval = seconds * size
    epoch_seconds = int(observed_at.timestamp())
    return datetime.fromtimestamp((epoch_seconds // interval) * interval, tz=timezone.utc)


def _local_worker_task_is_healthy(
    runtime: BotWorkerRuntime,
    *,
    snapshot: BotWorkerSnapshot,
    now: datetime,
) -> bool:
    if not runtime.settings.enabled:
        return False
    task = runtime._runner_task
    if task is None or task.done():
        return False
    if snapshot.state in {"crashed", "error", "lease_lost", "stopped", "disabled"}:
        return False
    runner_heartbeat_at = runtime._runner_heartbeat()
    if runner_heartbeat_at is None:
        return False
    age = _as_utc(now) - _as_utc(runner_heartbeat_at)
    return timedelta(seconds=-30) <= age <= timedelta(
        seconds=runtime.settings.lease_ttl_seconds
    )


def inspect_bot_runtime(
    db: Session,
    *,
    runtime: BotWorkerRuntime,
    user_id: str | None = None,
    now: datetime | None = None,
) -> BotRuntimeReadiness:
    observed_at = _as_utc(now or datetime.now(timezone.utc))
    snapshot = runtime.snapshot()
    worker_task_healthy = _local_worker_task_is_healthy(
        runtime,
        snapshot=snapshot,
        now=observed_at,
    )
    config_query = db.query(BotConfig).filter(BotConfig.enabled.is_(True))
    run_query = db.query(BotRun, BotConfig).join(BotConfig, BotConfig.id == BotRun.bot_config_id).filter(
        BotRun.status == "running"
    )
    unresolved_query = db.query(func.count(BotOrderAttempt.id)).filter(
        BotOrderAttempt.execution_mode == "live",
        BotOrderAttempt.status.in_(["pending", "submission_unknown"]),
    )
    if user_id is not None:
        config_query = config_query.filter(BotConfig.user_id == user_id)
        run_query = run_query.filter(BotRun.user_id == user_id)
        unresolved_query = unresolved_query.filter(BotOrderAttempt.user_id == user_id)

    enabled_configs = config_query.all()
    running_rows = run_query.all()
    unresolved = int(unresolved_query.scalar() or 0)
    latest_emergency_ids = db.query(
        func.max(AccountEmergencyAction.id).label("action_id")
    )
    if user_id is not None:
        latest_emergency_ids = latest_emergency_ids.filter(
            AccountEmergencyAction.user_id == user_id
        )
    latest_emergency_ids = latest_emergency_ids.group_by(
        AccountEmergencyAction.user_id,
        AccountEmergencyAction.account_id,
    ).subquery()
    unresolved_emergency_latches = int(
        db.query(func.count(AccountEmergencyAction.id))
        .join(
            latest_emergency_ids,
            AccountEmergencyAction.id == latest_emergency_ids.c.action_id,
        )
        .filter(AccountEmergencyAction.status.in_(["pending", "unconfirmed"]))
        .scalar()
        or 0
    )
    armed_config_ids = {
        int(config.id)
        for run, config in running_rows
        if _run_disarm_reason(run, config) is None
    }
    unarmed_enabled = sum(int(config.id) not in armed_config_ids for config in enabled_configs)
    invalid_running = sum(_run_disarm_reason(run, config) is not None for run, config in running_rows)
    live_runs = sum(not bool(run.dry_run) for run, _config in running_rows)
    awaiting_classification = 0
    ineligible_live_accounts = 0
    for run, _config in running_rows:
        if bool(run.dry_run):
            continue
        account = (
            db.query(Account)
            .filter(
                Account.user_id == str(run.user_id),
                Account.provider == "projectx",
                Account.external_id == str(int(run.account_id)),
            )
            .one_or_none()
        )
        classification = _fresh_account_classification(account, now=observed_at)
        if classification is None:
            awaiting_classification += 1
        elif classification is False:
            ineligible_live_accounts += 1

    lease = db.query(BotRuntimeLease).filter(BotRuntimeLease.lease_name == _LEASE_NAME).one_or_none()
    lease_healthy = bool(
        lease is not None and _as_utc(lease.expires_at) > observed_at
    )
    lease_details = lease.details if lease is not None and isinstance(lease.details, dict) else {}
    local_owner = bool(lease is not None and str(lease.owner_id) == runtime.owner_id)
    if local_owner:
        provider_status = _fresh_provider_status(
            snapshot,
            now=observed_at,
            max_age_seconds=runtime.settings.provider_probe_seconds * 2,
        )
    else:
        provider_status = _fresh_lease_provider_status(
            lease_details,
            now=observed_at,
            max_age_seconds=runtime.settings.provider_probe_seconds * 2,
        )
    active_work = bool(running_rows or unresolved)
    # These public checks describe the actual runtime capability, even before a
    # run is armed.  Readiness below decides which checks are required for an
    # idle API-only deployment; callers must not mistake an implication such as
    # "no current work needs a lease" for proof that a lease is available.
    checks = {
        "worker_enabled": bool(runtime.settings.enabled),
        "worker_task_healthy": worker_task_healthy,
        "lease_healthy": bool(lease_healthy),
        "runs_armed": unarmed_enabled == 0 and invalid_running == 0,
        "live_gate": _background_live_execution_enabled(),
        "account_classification_fresh": awaiting_classification == 0,
        "accounts_simulated": ineligible_live_accounts == 0,
        "provider_healthy": provider_status not in {"error", "throttled"},
        "submissions_reconciled": unresolved == 0,
        # This is an account admission latch, not worker-process health.  The
        # UI must not arm while false, but /ready should remain healthy so an
        # operator can retry the emergency flatten that clears it.
        "account_emergency_clear": unresolved_emergency_latches == 0,
    }
    required_checks = {
        "runs_armed": checks["runs_armed"],
        "account_classification_fresh": checks["account_classification_fresh"],
        "accounts_simulated": checks["accounts_simulated"],
        "submissions_reconciled": checks["submissions_reconciled"],
    }
    if live_runs:
        required_checks["live_gate"] = checks["live_gate"]
    # Enabling the worker is an operational promise: it must own (or observe)
    # a healthy deployment lease even before the first continuous run is
    # armed.  A deliberately API-only deployment may remain ready while the
    # worker is disabled, provided it has no armed work to service.
    if runtime.settings.enabled or active_work:
        required_checks.update(
            {
                "worker_enabled": checks["worker_enabled"],
                "worker_task_healthy": checks["worker_task_healthy"],
                "lease_healthy": checks["lease_healthy"],
            }
        )
    if active_work:
        required_checks["provider_healthy"] = provider_status == "ok"
    return BotRuntimeReadiness(
        ready=all(required_checks.values()),
        checks=checks,
        counts={
            "enabled_configs": len(enabled_configs),
            "running_runs": len(running_rows),
            "unarmed_enabled_configs": unarmed_enabled,
            "invalid_running_runs": invalid_running,
            "live_runs_awaiting_account_classification": awaiting_classification,
            "ineligible_live_accounts": ineligible_live_accounts,
            "unresolved_live_submissions": unresolved,
            "unresolved_account_emergency_actions": unresolved_emergency_latches,
            "retrying_runs": (
                snapshot.retrying_runs
                if local_owner
                else int(lease_details.get("retrying_runs") or 0)
            ),
        },
        state=(
            snapshot.state
            if local_owner
            else str(lease_details.get("state") or ("standby" if lease_healthy else snapshot.state))
        ),
        provider_status=provider_status,
    )


def continuous_start_availability(
    db: Session,
    *,
    runtime: BotWorkerRuntime,
    requested_live: bool = False,
    now: datetime | None = None,
) -> tuple[bool, str | None]:
    """Fail-closed admission check performed before a continuous run is armed."""

    if not runtime.settings.enabled:
        return False, "bot_worker_disabled"
    if requested_live and not _background_live_execution_enabled():
        return False, "bot_worker_live_execution_disabled"
    observed_at = _as_utc(now or datetime.now(timezone.utc))
    lease = db.query(BotRuntimeLease).filter(BotRuntimeLease.lease_name == _LEASE_NAME).one_or_none()
    if lease is None or _as_utc(lease.expires_at) <= observed_at:
        return False, "bot_worker_lease_unavailable"
    snapshot = runtime.snapshot()
    if not _local_worker_task_is_healthy(
        runtime,
        snapshot=snapshot,
        now=observed_at,
    ):
        return False, "bot_worker_unhealthy"
    if _unresolved_live_submission_count(db):
        return False, "bot_submissions_unresolved"
    details = lease.details if isinstance(lease.details, dict) else {}
    provider_status = (
        _fresh_provider_status(
            snapshot,
            now=observed_at,
            max_age_seconds=runtime.settings.provider_probe_seconds * 2,
        )
        if str(lease.owner_id) == runtime.owner_id
        else _fresh_lease_provider_status(
            details,
            now=observed_at,
            max_age_seconds=runtime.settings.provider_probe_seconds * 2,
        )
    )
    if provider_status in {"error", "throttled"}:
        return False, "projectx_provider_unhealthy"
    return True, None


def _run_disarm_reason(run: BotRun, config: BotConfig) -> str | None:
    if str(run.status) != "running":
        return "run_not_running"
    if not bool(config.enabled):
        return "config_disabled"
    state = run.raw_state if isinstance(run.raw_state, dict) else {}
    if state.get("source") != "manual_start":
        return "run_not_created_by_explicit_start"
    if state.get("continuous") is not True:
        return "run_not_armed_for_continuous_execution"
    expected_mode = "dry_run" if bool(run.dry_run) else "live"
    if str(config.execution_mode) != expected_mode:
        return "run_execution_mode_changed"
    if str(state.get("execution_mode")) != expected_mode:
        return "run_authorization_mode_mismatch"
    if not bool(run.dry_run) and state.get("live_routing_confirmed") is not True:
        return "live_run_missing_durable_confirmation"
    if str(config.timeframe_unit) == "day" and int(config.timeframe_unit_number) != 1:
        return "unsupported_multi_day_worker_schedule"
    return None


def _evaluation_is_due(
    run: BotRun,
    *,
    scheduled_close: datetime,
    grace_seconds: float,
) -> bool:
    state = run.raw_state if isinstance(run.raw_state, dict) else {}
    raw_scheduled = state.get("worker_last_scheduled_close_at")
    if isinstance(raw_scheduled, str):
        try:
            if _as_utc(datetime.fromisoformat(raw_scheduled)) >= scheduled_close:
                return False
        except ValueError:
            pass
    if run.last_evaluated_at is not None:
        evaluated_at = _as_utc(run.last_evaluated_at) - timedelta(
            seconds=max(float(grace_seconds), 0.0)
        )
        if evaluated_at >= scheduled_close:
            return False
    return True


def _heartbeat_is_stale(
    value: datetime | None,
    *,
    now: datetime,
    stale_after_seconds: float,
) -> bool:
    if value is None:
        return True
    return _as_utc(value) < _as_utc(now) - timedelta(seconds=stale_after_seconds)


def _active_armed_run_count(db: Session) -> int:
    rows = (
        db.query(BotRun, BotConfig)
        .join(BotConfig, BotConfig.id == BotRun.bot_config_id)
        .filter(BotRun.status == "running", BotConfig.enabled.is_(True))
        .all()
    )
    return sum(_run_disarm_reason(run, config) is None for run, config in rows)


def _select_latest_running_run_ids(db: Session, *, now: datetime) -> list[int]:
    """Return one newest run per config and close corrupt legacy duplicates."""

    rows = (
        db.query(BotRun, BotConfig)
        .join(BotConfig, BotConfig.id == BotRun.bot_config_id)
        .filter(BotRun.status == "running")
        .order_by(
            BotRun.started_at.desc(),
            BotRun.id.desc(),
        )
        .with_for_update()
        .all()
    )
    seen: set[tuple[str, int]] = set()
    seen_live_accounts: set[tuple[str, int]] = set()
    selected: list[int] = []
    for run, config in rows:
        key = (str(run.user_id), int(run.bot_config_id))
        live_account_key = (str(run.user_id), int(run.account_id))
        duplicate_live_account = not bool(run.dry_run) and live_account_key in seen_live_accounts
        if key in seen or duplicate_live_account:
            transition_bot_run(
                run,
                "stopped",
                reason=(
                    "duplicate_live_account_run_recovered"
                    if duplicate_live_account
                    else "duplicate_running_run_recovered"
                ),
                now=now,
            )
            continue
        seen.add(key)
        if not bool(run.dry_run):
            seen_live_accounts.add(live_account_key)
        if not bool(config.enabled):
            # A config can be disabled by an edit or administrative action that
            # does not pass through the explicit Stop endpoint.  Close its
            # durable authorization here instead of leaving an unadopted
            # `running` row that keeps readiness degraded indefinitely.
            transition_bot_run(run, "stopped", reason="config_disabled", now=now)
            continue
        selected.append(int(run.id))
    return selected


def _unresolved_live_submission_count(db: Session) -> int:
    return int(
        db.query(func.count(BotOrderAttempt.id))
        .filter(
            BotOrderAttempt.execution_mode == "live",
            BotOrderAttempt.status.in_(["pending", "submission_unknown"]),
        )
        .scalar()
        or 0
    )


def _background_live_execution_enabled() -> bool:
    return live_execution_environment_enabled() and _bool_env(
        "TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION", False
    )


def _fresh_provider_status(
    snapshot: BotWorkerSnapshot,
    *,
    now: datetime,
    max_age_seconds: float,
) -> str:
    checked_at = snapshot.last_provider_check_at
    if checked_at is None:
        return snapshot.provider_status if snapshot.provider_status == "idle" else "unknown"
    if _as_utc(checked_at) < _as_utc(now) - timedelta(seconds=max_age_seconds):
        return "unknown"
    return snapshot.provider_status


def _fresh_account_classification(
    account: Account | None,
    *,
    now: datetime,
) -> bool | None:
    if account is None or not isinstance(account.provider_simulated, bool):
        return None
    observed_at = account.provider_classification_observed_at
    if observed_at is None:
        return None
    age = _as_utc(now) - _as_utc(observed_at)
    if age < timedelta(seconds=-30) or age > timedelta(minutes=5):
        return None
    return account.provider_simulated


def _fresh_lease_provider_status(
    details: dict[str, Any],
    *,
    now: datetime,
    max_age_seconds: float,
) -> str:
    raw_checked_at = details.get("last_provider_check_at")
    if not isinstance(raw_checked_at, str):
        return "unknown"
    try:
        checked_at = _as_utc(datetime.fromisoformat(raw_checked_at))
    except ValueError:
        return "unknown"
    if checked_at < _as_utc(now) - timedelta(seconds=max_age_seconds):
        return "unknown"
    return str(details.get("provider_status") or "unknown")


def _retry_delay_seconds(
    exc: BaseException | None,
    *,
    failures: int,
    key: str,
    maximum: float,
) -> float:
    status_code = getattr(exc, "status_code", None)
    base = 30.0 if status_code == 429 else 5.0
    exponential = min(base * (2 ** max(failures - 1, 0)), maximum)
    return min(exponential + _deterministic_jitter_seconds(key, min(base, 10.0)), maximum)


def _is_retryable_provider_failure(exc: BaseException) -> bool:
    status_code = getattr(exc, "status_code", None)
    try:
        normalized_status = int(status_code) if status_code is not None else None
    except (TypeError, ValueError, OverflowError):
        normalized_status = None
    return bool(
        getattr(exc, "reason_code", None) == PROJECTX_ERROR_NETWORK
        or normalized_status in {408, 429}
        or (normalized_status is not None and normalized_status >= 500)
    )


def _deterministic_jitter_seconds(key: str, maximum: float) -> float:
    if maximum <= 0:
        return 0.0
    digest = hashlib.sha256(str(key).encode("utf-8")).digest()
    fraction = int.from_bytes(digest[:8], "big") / float((1 << 64) - 1)
    return fraction * maximum


def _first_open_session_boundary(first_trading_date, *, symbol: str | None = None) -> datetime:
    candidate = first_trading_date
    for _ in range(10):
        boundary, _ = trading_day_bounds_utc(candidate)
        if futures_session_is_open(boundary + timedelta(minutes=1), symbol=symbol):
            return boundary
        candidate += timedelta(days=1)
    raise ValueError("No open futures session found for calendar boundary.")


def _most_recent_open_session_boundary(trading_date, *, symbol: str | None = None) -> datetime:
    candidate = trading_date
    for _ in range(10):
        boundary, _ = trading_day_bounds_utc(candidate)
        if futures_session_is_open(boundary + timedelta(minutes=1), symbol=symbol):
            return boundary
        candidate -= timedelta(days=1)
    raise ValueError("No recent open futures session found for trading day.")


def _safe_error_code(exc: BaseException) -> str:
    reason_code = getattr(exc, "reason_code", None)
    if isinstance(reason_code, str) and reason_code:
        return reason_code
    return f"{type(exc).__name__.lower()}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise RuntimeError(f"{name} must be a boolean value")


def _float_env(name: str, default: float, *, minimum: float) -> float:
    raw = os.getenv(name)
    try:
        value = default if raw is None else float(raw.strip())
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum:g}")
    return value


__all__ = [
    "BotRuntimeReadiness",
    "BotWorkerRuntime",
    "BotWorkerSettings",
    "BotWorkerSnapshot",
    "continuous_start_availability",
    "inspect_bot_runtime",
    "latest_closed_candle_boundary",
]
