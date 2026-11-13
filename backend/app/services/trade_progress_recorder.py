"""Independent, read-only subscriptions for locally enrolled accounts.

Socket callbacks only enqueue. HTTP reconciliation and SQLite work have their
own threads and bounded queues; no recording work runs on an execution path.
"""
import logging
from queue import Empty, Full, Queue
import threading
from time import monotonic

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
    def __init__(self, owner, account, points, client_factory, *, market=None):
        from .projectx_hubs import ProjectXHubRunner
        from .projectx_streaming_runtime import StreamingRuntime
        self.owner, self.account, self.points = owner, account, points
        self.client_factory = client_factory
        self.market = market
        self.queue = Queue(maxsize=20000)
        self.overflow = threading.Event()
        self.stop_event = threading.Event()
        self.refresh = threading.Event()
        self.metrics_lock = threading.Lock()
        self.metrics = dict(process_started_at=store.now(), queue_overflow_events=0,
            discarded_after_overflow=0, discarded_after_write_failure=0,
            snapshot_failures=0, snapshot_successes=0, reconnects=0,
            commit_failures=0, last_commit_seconds=None, max_commit_seconds=0,
            last_successful_write=None, last_snapshot_success=None)
        self.seen_connections = set()
        self.tracker = RecordingTracker(self)
        self.runtime = StreamingRuntime(tracker=self.tracker, runner=ProjectXHubRunner(
            tracker=self.tracker, client_factory=client_factory, user_id=owner, account_id=account,
            market_hub_url="" if market else "https://rtc.topstepx.com/hubs/market", track_positions=True,
            on_connection=self.connection,
            on_archive_event=lambda kind, payload: self.submit("fill", dict(payload)) if kind == "GatewayUserTrade" else None))
        self.thread = threading.Thread(target=self.run, name="trade-progress-writer", daemon=True)
        self.snapshot_thread = threading.Thread(target=self.reconcile, name="trade-progress-snapshot", daemon=True)

    def submit(self, kind, payload):
        try:
            self.queue.put_nowait((kind, payload, store.now()))
        except Full:
            self.overflow.set()
            with self.metrics_lock:
                self.metrics['queue_overflow_events'] += 1

    def connection(self, stream, reason):
        if reason.startswith('connected'):
            with self.metrics_lock:
                self.metrics['reconnects'] += stream in self.seen_connections
                self.seen_connections.add(stream)
        self.submit("connection", (stream, reason.startswith("connected")))
        self.refresh.set()

    def connection_states(self):
        states = dict(self.runtime.runner.stream_states)
        if self.market:
            states['market'] = self.market.runtime.runner.stream_states.get('market', 'disconnected')
        return states

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
                with self.metrics_lock:
                    self.metrics['snapshot_successes'] += 1
                    self.metrics['last_snapshot_success'] = store.now()
                self.submit("snapshot", ([row["raw_payload"] for row in rows], started))
            except Exception as exc:
                with self.metrics_lock:
                    self.metrics['snapshot_failures'] += 1
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
                                with self.metrics_lock:
                                    self.metrics['discarded_after_write_failure'] += 1
                            except Empty:
                                break
                        self.refresh.set()
                    engine.connected = {k: v == "connected" for k,v in self.connection_states().items()}
                    for key in ("market", "user"):
                        engine.connected.setdefault(key, False)
                if self.overflow.is_set():
                    self.overflow.clear()
                    engine.gap("queue_overflow", at)
                    while True:
                        try:
                            self.queue.get_nowait()
                            with self.metrics_lock:
                                self.metrics['discarded_after_overflow'] += 1
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
                    connected = self.connection_states().get(stream) == "connected"
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
                    with self.queue.mutex:
                        oldest = max(0, store.now()-self.queue.queue[0][2]) if self.queue.queue else 0
                        queued = len(self.queue.queue)
                    with self.metrics_lock:
                        diagnostics = dict(self.metrics, observed_at=store.now(), queued_events=queued,
                            oldest_queued_seconds=oldest, active_positions=len(engine.active),
                            positions_without_fresh_mark=sum(bool(engine.observation(s,store.now())['issue']) for s in engine.active.values()),
                            coverage_ready=healthy)
                    began = monotonic()
                    try:
                        engine.flush(error=None if healthy else "stream_or_position_coverage_unavailable", diagnostics=diagnostics)
                    except Exception:
                        with self.metrics_lock:
                            self.metrics['commit_failures'] += 1
                        raise
                    with self.metrics_lock:
                        elapsed = monotonic()-began
                        self.metrics.update(last_commit_seconds=elapsed,
                            max_commit_seconds=max(elapsed,self.metrics['max_commit_seconds']),
                            last_successful_write=store.now())
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


class SharedRecorderMarket:
    """One quote subscription per owner/contract, shared only across that owner.

    Positions and fills remain account-scoped. This does not substitute the
    valuation stream for the separate depth/exit-study experiment evidence.
    """
    def __init__(self, owner, client_factory):
        from .projectx_hubs import ProjectXHubRunner
        from .projectx_streaming_runtime import StreamingRuntime
        self.owner = owner
        self.workers = ()  # Copy-on-write tuple: callbacks never hold manager locks.
        self.runtime = StreamingRuntime(tracker=self, runner=ProjectXHubRunner(
            tracker=self,client_factory=client_factory,user_id=owner,user_hub_url="",
            market_hub_url="https://rtc.topstepx.com/hubs/market",track_positions=True,on_connection=self.connection))

    def add(self, worker):
        if worker.owner != self.owner:
            raise ValueError('Market sharing requires the same credential owner')
        self.workers = (*self.workers, worker)

    def remove(self, worker):
        self.workers = tuple(w for w in self.workers if w is not worker)

    def active_contracts(self):
        return set().union(*(w.tracker.contracts for w in self.workers))

    def reset_observations(self):
        pass  # Connection callback and live stream-state checks record gaps.

    def connection(self, stream, reason):
        for worker in self.workers:
            worker.connection(stream,reason)

    def ingest_market_event(self, payload):
        for worker in self.workers:
            if payload.get('contractId') in worker.tracker.contracts:
                worker.submit('quote',dict(payload))


class TradeProgressRecorder:
    def __init__(self, session_factory, client_factory):
        self.session_factory, self.client_factory = session_factory, client_factory
        self.stop_event = threading.Event()
        self.thread = None
        self.workers = {}
        self.markets = {}

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
                            if owner not in self.markets:
                                self.markets[owner] = SharedRecorderMarket(owner,lambda owner=owner:self.client(owner))
                            market = self.markets[owner]
                            worker = AccountRecorder(owner,account,points,lambda owner=owner: self.client(owner),market=market)
                            market.add(worker)
                            self.workers[owner,account] = worker
                            worker.start()
                        else:
                            self.workers[owner,account].runtime.start()
                for key in list(self.workers):
                    if key not in wanted and self.workers[key].stop():
                        self.markets[key[0]].remove(self.workers[key])
                        del self.workers[key]
                for owner in list(self.markets):
                    market = self.markets[owner]
                    if market.workers:
                        market.runtime.start()
                    elif market.runtime.stop():
                        del self.markets[owner]
                self.stop_event.wait(5)
        finally:
            # Retain the cross-process lock until all callbacks are reaped.
            for worker in self.workers.values():
                while not worker.stop():
                    self.stop_event.wait(1)
            self.workers.clear()
            for market in self.markets.values():
                while not market.runtime.stop():
                    self.stop_event.wait(1)
            self.markets.clear()
