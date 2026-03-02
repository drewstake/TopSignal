from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass

from ..db import SessionLocal
from .instruments import build_point_value_lookup, load_instrument_specs
from .projectx_hubs import ProjectXHubRunner
from .streaming_pnl_tracker import (
    ClosedPositionLifecycle,
    StreamingPnlTracker,
    save_position_lifecycle_mae_mfe,
)

logger = logging.getLogger(__name__)


@dataclass
class StreamingRuntime:
    tracker: StreamingPnlTracker
    runner: ProjectXHubRunner
    thread: threading.Thread | None = None
    stop_event: threading.Event | None = None
    loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return

        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run_thread, name="projectx-streaming", daemon=True)
        self.thread.start()

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        if self.thread is None:
            return

        if self.stop_event is not None:
            self.stop_event.set()
        if self.loop is not None:
            self.loop.call_soon_threadsafe(lambda: None)
        self.thread.join(timeout=max(0.5, timeout_seconds))
        self.thread = None
        self.loop = None
        self.stop_event = None

    def _run_thread(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._run_until_stopped())
        except Exception:
            logger.exception("[streaming] runtime crashed")
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


def create_streaming_runtime() -> StreamingRuntime:
    with SessionLocal() as db:
        specs = load_instrument_specs(db)
    point_value_lookup = build_point_value_lookup(specs)

    tracker = StreamingPnlTracker(
        point_value_by_symbol=point_value_lookup,
        on_lifecycle_closed=_persist_closed_lifecycle,
    )
    runner = ProjectXHubRunner(tracker=tracker)
    return StreamingRuntime(tracker=tracker, runner=runner)


def _persist_closed_lifecycle(lifecycle: ClosedPositionLifecycle) -> None:
    db = SessionLocal()
    try:
        save_position_lifecycle_mae_mfe(
            db,
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
    except Exception:
        db.rollback()
        logger.exception(
            "[streaming] failed to persist lifecycle account_id=%s contract_id=%s",
            lifecycle.account_id,
            lifecycle.contract_id,
        )
    finally:
        db.close()
