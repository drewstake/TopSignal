"""Independent, read-only subscriptions for locally enrolled accounts.

Socket callbacks only enqueue. HTTP reconciliation and SQLite work have their
own threads and bounded queues; no recording work runs on an execution path.
"""
import logging
from queue import Empty, Full, Queue
import threading

from . import trade_progress_store as store
from .trade_progress import ProgressEngine

logger = logging.getLogger(__name__)


class RecordingTracker:
    """The hub's small tracker interface, with no execution lifecycle sink."""
    def __init__(self, worker):
        self.worker = worker
        self.contracts = frozenset()

    def active_contracts(self):
        return set(self.contracts)

    def reset_observations(self):
        # on_connection records the gap; retain subscriptions until reconciliation.
        pass

    def ingest_market_event(self, payload):
        self.worker.submit("quote", dict(payload))

    def ingest_position_event(self, payload, **_scope):
        self.worker.submit("position", dict(payload))


class AccountRecorder:
    def __init__(self, owner, account, points, client_factory):
        from .projectx_hubs import ProjectXHubRunner
        from .projectx_streaming_runtime import StreamingRuntime
        self.owner, self.account, self.points = owner, account, points
        self.client_factory = client_factory
        self.queue = Queue(maxsize=20000)
        self.overflow = threading.Event()
        self.stop_event = threading.Event()
        self.refresh = threading.Event()
        self.tracker = RecordingTracker(self)
        self.runtime = StreamingRuntime(tracker=self.tracker, runner=ProjectXHubRunner(
            tracker=self.tracker, client_factory=client_factory, user_id=owner, account_id=account,
            market_hub_url="https://rtc.topstepx.com/hubs/market", track_positions=True,
            on_connection=self.connection,
            on_archive_event=lambda kind, payload: self.submit("fill", dict(payload)) if kind == "GatewayUserTrade" else None))
        self.thread = threading.Thread(target=self.run, name="trade-progress-writer", daemon=True)
        self.snapshot_thread = threading.Thread(target=self.reconcile, name="trade-progress-snapshot", daemon=True)

    def submit(self, kind, payload):
        try:
            self.queue.put_nowait((kind, payload, store.now()))
        except Full:
            self.overflow.set()

    def connection(self, stream, reason):
        self.submit("connection", (stream, reason.startswith("connected")))
        self.refresh.set()

    def start(self):
        self.thread.start()
        self.runtime.start()
        self.snapshot_thread.start()

    def stop(self):
        self.stop_event.set()
        self.refresh.set()
        stopped = self.runtime.stop()
        self.snapshot_thread.join(timeout=20)
        self.thread.join(timeout=5)
        return stopped and not self.thread.is_alive() and not self.snapshot_thread.is_alive()

    def reconcile(self):
        while not self.stop_event.is_set():
            self.refresh.clear()
            started = store.now()
            try:
                rows = self.client_factory().search_open_positions(account_id=self.account)
                self.submit("snapshot", ([row["raw_payload"] for row in rows], started))
            except Exception as exc:
                self.submit("gap", "position_snapshot_failed")
                logger.warning("trade_progress_snapshot_failed error_type=%s", type(exc).__name__)
            self.refresh.wait(15)

    def run(self):
        engine = None
        last_flush = last_disk = 0
        storage_error = False
        while not self.stop_event.is_set():
            try:
                at = store.now()
                if engine is None or at-last_disk>=10:
                    store.verify_destination()
                    last_disk = at
                if engine is None:
                    engine = ProgressEngine(self.owner, self.account, self.points)
                    if storage_error:
                        engine.gap("storage_failure", at)
                        # Events received during the failure cannot establish a continuous path.
                        while True:
                            try:
                                self.queue.get_nowait()
                            except Empty:
                                break
                        self.refresh.set()
                    engine.connected = {k: v == "connected" for k,v in self.runtime.runner.stream_states.items()}
                    for key in ("market", "user"):
                        engine.connected.setdefault(key, False)
                if self.overflow.is_set():
                    self.overflow.clear()
                    engine.gap("queue_overflow", at)
                    while True:
                        try:
                            self.queue.get_nowait()
                        except Empty:
                            break
                    self.refresh.set()
                # Bound each drain so prices never starve periodic sampling/commits.
                for _ in range(2000):
                    try:
                        kind, payload, received = self.queue.get_nowait()
                    except Empty:
                        break
                    if kind == "connection":
                        engine.connection(*payload, received)
                    elif kind == "snapshot":
                        engine.snapshot(*payload, received)
                    elif kind == "gap":
                        engine.gap(payload, received)
                    else:
                        getattr(engine, kind)(payload, received)
                # Queue loss must not leave a stale "connected" value behind.
                # The runner's current state is authoritative for sampling now.
                for stream in ("market", "user"):
                    connected = self.runtime.runner.stream_states.get(stream) == "connected"
                    if connected != engine.connected.get(stream):
                        engine.connection(stream, connected, store.now())
                if not self.queue.empty():
                    # Don't label an old queued price as current, or stamp
                    # future periodic samples ahead of queued lifecycle events.
                    engine.gap("recorder_backlog", at)
                    self.refresh.set()
                else:
                    engine.tick(store.now())
                self.tracker.contracts = frozenset(engine.active)
                if at-last_flush>=1:
                    healthy = all(engine.connected.values()) and engine.reconciled and not self.overflow.is_set()
                    if healthy and any(engine.observation(s, store.now())["issue"] for s in engine.active.values()):
                        healthy = False
                    engine.flush(error=None if healthy else "stream_or_position_coverage_unavailable")
                    last_flush = at
                self.stop_event.wait(.05)
            except Exception as exc:
                # Reload only committed state. A failed transaction never advances
                # durable sampling cursors; the restart gap includes its lost tail.
                logger.error("trade_progress_recording_failed error_type=%s", type(exc).__name__)
                storage_error = True
                engine = None
                self.stop_event.wait(2)
        if engine is not None:
            try:
                store.verify_destination()
                engine.gap("recorder_stopped", store.now())
                engine.flush(error="recorder_stopped")
            except Exception as exc:
                logger.error("trade_progress_final_flush_failed error_type=%s", type(exc).__name__)


class TradeProgressRecorder:
    def __init__(self, session_factory, client_factory):
        self.session_factory, self.client_factory = session_factory, client_factory
        self.stop_event = threading.Event()
        self.thread = None
        self.workers = {}

    def start(self):
        self.thread = threading.Thread(target=self.run, name="trade-progress-manager", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=35)
        return self.thread is None or not self.thread.is_alive()

    def client(self, owner):
        with self.session_factory() as db:
            client = self.client_factory(db, user_id=owner)
            client.timeout_seconds = min(client.timeout_seconds, 15)
            return client

    def run(self):
        from .historical_trade_excursions import recorder_lock
        while not self.stop_event.is_set():
            try:
                if store.scopes():
                    store.verify_destination()
                    with recorder_lock(store.location().with_suffix(".lock")) as acquired:
                        if acquired:
                            self.run_owned()
            except Exception as exc:
                logger.error("trade_progress_manager_failed error_type=%s", type(exc).__name__)
            self.stop_event.wait(5)

    def run_owned(self):
        from ..models import Account
        from .instruments import build_point_value_lookup, load_instrument_specs
        try:
            while not self.stop_event.is_set():
                wanted = set()
                for owner, account in store.scopes():
                    with self.session_factory() as db:
                        valid = db.query(Account.id).filter(Account.user_id==owner, Account.external_id==str(account),
                            Account.provider=="projectx", Account.trade_data_source=="projectx").first()
                        points = build_point_value_lookup(load_instrument_specs(db)) if valid else {}
                    if valid:
                        wanted.add((owner,account))
                        if (owner,account) not in self.workers:
                            worker = AccountRecorder(owner,account,points,lambda owner=owner: self.client(owner))
                            self.workers[owner,account] = worker
                            worker.start()
                        else:
                            self.workers[owner,account].runtime.start()
                for key in list(self.workers):
                    if key not in wanted and self.workers[key].stop():
                        del self.workers[key]
                self.stop_event.wait(5)
        finally:
            # Retain the cross-process lock until all callbacks are reaped.
            for worker in self.workers.values():
                while not worker.stop():
                    self.stop_event.wait(1)
            self.workers.clear()
