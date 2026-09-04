from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from ..db import SessionLocal
from .instruments import build_point_value_lookup, load_instrument_specs
from .projectx_hubs import ProjectXHubRunner
from .projectx_client import ProjectXClient
from .projectx_accounts import (
    TRADE_DATA_SOURCE_PROJECTX,
    get_projectx_account_row,
    invalidate_projectx_account_classification,
    persist_projectx_account_classification,
)
from .streaming_pnl_tracker import (
    ClosedPositionLifecycle,
    StreamingPnlTracker,
    save_position_lifecycle_mae_mfe,
)

logger = logging.getLogger(__name__)


class ProjectXAccountClassificationProbeTimeout(RuntimeError):
    pass


class ProjectXAccountClassificationProbeUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectXAccountClassificationObservation:
    account_id: int
    provider_simulated: bool
    provider_classification_observed_at: datetime
    source: str = "projectx_user_hub"


@dataclass
class StreamingRuntime:
    tracker: StreamingPnlTracker
    runner: ProjectXHubRunner
    thread: threading.Thread | None = None
    stop_event: threading.Event | None = None
    loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        if self.thread is not None:
            if self.thread.is_alive():
                return
            # Reap a previously timed-out stop before constructing a replacement.
            self.thread = None
            self.loop = None
            self.stop_event = None

        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run_thread, name="projectx-streaming", daemon=True)
        self.thread.start()

    def stop(self, *, timeout_seconds: float = 5.0) -> bool:
        if self.thread is None:
            return True

        thread = self.thread
        if self.stop_event is not None:
            self.stop_event.set()
        if self.loop is not None:
            self.loop.call_soon_threadsafe(lambda: None)
        thread.join(timeout=max(0.5, timeout_seconds))
        if thread.is_alive():
            # Keep every handle so callers can retry/reap this exact runtime.
            # Dropping them here would let a later arm create a duplicate user
            # hub while the old daemon still owns callbacks and credentials.
            logger.error(
                "projectx_streaming_runtime_stop_timed_out",
                extra={"reason_code": "projectx_streaming_runtime_stop_timeout"},
            )
            return False
        self.thread = None
        self.loop = None
        self.stop_event = None
        return True

    def _run_thread(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._run_until_stopped())
        except Exception as exc:
            logger.error(
                "projectx_streaming_runtime_crashed",
                extra={
                    "reason_code": "projectx_streaming_runtime_error",
                    "error_type": type(exc).__name__,
                },
            )
        finally:
            self.loop.close()

    async def _run_until_stopped(self) -> None:
        stop_event = self.stop_event
        if stop_event is None:
            return

        runner_task = asyncio.create_task(self.runner.run_forever())
        try:
            while not stop_event.is_set():
                await asyncio.sleep(0.25)
        finally:
            runner_task.cancel()
            await asyncio.gather(runner_task, return_exceptions=True)


def create_streaming_runtime(
    *,
    user_id: str,
    account_id: int,
    client_factory: Callable[[], ProjectXClient],
    market_hub_url: str | None = None,
    user_hub_url: str | None = None,
) -> StreamingRuntime:
    normalized_user_id = user_id.strip()
    if not normalized_user_id:
        raise ValueError("streaming runtime requires an explicit user_id")
    if account_id <= 0:
        raise ValueError("streaming runtime requires a positive account_id")

    with SessionLocal() as db:
        specs = load_instrument_specs(db)
    point_value_lookup = build_point_value_lookup(specs)

    tracker = StreamingPnlTracker(
        owner_user_id=normalized_user_id,
        owner_account_id=account_id,
        point_value_by_symbol=point_value_lookup,
        on_lifecycle_closed=_persist_closed_lifecycle,
    )
    runner = ProjectXHubRunner(
        tracker=tracker,
        client_factory=client_factory,
        user_id=normalized_user_id,
        account_id=account_id,
        market_hub_url=market_hub_url,
        user_hub_url=user_hub_url,
        on_user_account=lambda payload: _persist_account_classification(
            user_id=normalized_user_id,
            account_id=account_id,
            payload=payload,
        ),
        on_user_disconnect=lambda: _invalidate_account_classification(
            user_id=normalized_user_id,
            account_id=account_id,
        ),
    )
    return StreamingRuntime(tracker=tracker, runner=runner)


def refresh_projectx_account_classification_once(
    *,
    user_id: str,
    account_id: int,
    client_factory: Callable[[], ProjectXClient],
    timeout_seconds: float = 10.0,
) -> ProjectXAccountClassificationObservation:
    """Fail-closed, bounded one-shot refresh from GatewayUserAccount.

    Any cached timestamp is invalidated before opening the user hub.  Success
    requires this exact probe to persist a boolean classification observed
    after it began; an unrelated stale database value can never satisfy it.
    """

    normalized_user_id = user_id.strip()
    if not normalized_user_id:
        raise ValueError("classification refresh requires an explicit user_id")
    if int(account_id) <= 0:
        raise ValueError("classification refresh requires a positive account_id")
    timeout = min(15.0, max(0.5, float(timeout_seconds)))
    probe_started_at = datetime.now(timezone.utc)

    with SessionLocal() as db:
        row = get_projectx_account_row(
            db,
            int(account_id),
            user_id=normalized_user_id,
            lock_for_update=True,
        )
        if row is None:
            raise LookupError("account_not_found")
        if row.trade_data_source != TRADE_DATA_SOURCE_PROJECTX:
            raise ValueError("csv_import_accounts_cannot_refresh_automation_classification")
        invalidate_projectx_account_classification(
            db,
            user_id=normalized_user_id,
            account_id=int(account_id),
        )
        db.commit()

    runner = ProjectXHubRunner(
        tracker=StreamingPnlTracker(
            owner_user_id=normalized_user_id,
            owner_account_id=int(account_id),
        ),
        client_factory=client_factory,
        user_id=normalized_user_id,
        account_id=int(account_id),
        market_hub_url="",
        on_user_account=lambda payload: _persist_account_classification(
            user_id=normalized_user_id,
            account_id=int(account_id),
            payload=payload,
        ),
        # Intentional one-shot cleanup is not a loss of authoritative state.
        # Real long-lived hub disconnects continue to invalidate via
        # create_streaming_runtime's callback.
        on_user_disconnect=None,
    )
    try:
        payload = asyncio.run(
            runner.probe_user_account_once(timeout_seconds=timeout)
        )
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise ProjectXAccountClassificationProbeTimeout(
            "projectx_account_classification_timeout"
        ) from exc
    except Exception as exc:
        raise ProjectXAccountClassificationProbeUnavailable(
            "projectx_account_classification_unavailable"
        ) from exc

    simulated = payload.get("simulated") if hasattr(payload, "get") else None
    if not isinstance(simulated, bool):
        raise ProjectXAccountClassificationProbeUnavailable(
            "projectx_account_classification_unavailable"
        )

    with SessionLocal() as db:
        row = get_projectx_account_row(
            db,
            int(account_id),
            user_id=normalized_user_id,
        )
        if row is None:
            raise ProjectXAccountClassificationProbeUnavailable(
                "projectx_account_classification_unavailable"
            )
        observed_at = row.provider_classification_observed_at
        persisted_simulated = row.provider_simulated
        if observed_at is None:
            raise ProjectXAccountClassificationProbeUnavailable(
                "projectx_account_classification_unavailable"
            )
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        else:
            observed_at = observed_at.astimezone(timezone.utc)
        if observed_at < probe_started_at or persisted_simulated is not simulated:
            raise ProjectXAccountClassificationProbeUnavailable(
                "projectx_account_classification_unavailable"
            )

    return ProjectXAccountClassificationObservation(
        account_id=int(account_id),
        provider_simulated=simulated,
        provider_classification_observed_at=observed_at,
    )


def _persist_account_classification(
    *,
    user_id: str,
    account_id: int,
    payload,
) -> None:
    simulated = payload.get("simulated") if hasattr(payload, "get") else None
    if not isinstance(simulated, bool):
        raise ValueError("GatewayUserAccount omitted simulated classification")
    with SessionLocal() as db:
        try:
            persist_projectx_account_classification(
                db,
                user_id=user_id,
                account_id=account_id,
                simulated=simulated,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise


def _invalidate_account_classification(*, user_id: str, account_id: int) -> None:
    with SessionLocal() as db:
        try:
            invalidate_projectx_account_classification(
                db,
                user_id=user_id,
                account_id=account_id,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise


def _persist_closed_lifecycle(lifecycle: ClosedPositionLifecycle) -> None:
    db = SessionLocal()
    try:
        save_position_lifecycle_mae_mfe(
            db,
            user_id=lifecycle.user_id,
            account_id=lifecycle.account_id,
            contract_id=lifecycle.contract_id,
            symbol=lifecycle.symbol,
            opened_at=lifecycle.opened_at,
            closed_at=lifecycle.closed_at,
            mae_usd=lifecycle.mae_usd,
            mfe_usd=lifecycle.mfe_usd,
            realized_pnl_usd=lifecycle.realized_pnl_usd,
            side=lifecycle.side,
            max_qty=lifecycle.max_qty,
            avg_entry_at_open=lifecycle.avg_entry_at_open,
            mae_points=lifecycle.mae_points,
            mfe_points=lifecycle.mfe_points,
            mae_timestamp=lifecycle.mae_timestamp,
            mfe_timestamp=lifecycle.mfe_timestamp,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "projectx_streaming_lifecycle_persist_failed",
            extra={
                "reason_code": "projectx_streaming_persist_error",
                "error_type": type(exc).__name__,
            },
        )
    finally:
        db.close()
