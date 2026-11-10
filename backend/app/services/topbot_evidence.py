"""Bounded, asynchronous recording of the exact normalized worker input stream.

Only local SQLite files; never broker calls or application/cloud database writes.
Capture loss affects evidence quality, never trading rules or protective management.
"""
from datetime import datetime, timezone
import errno
from functools import lru_cache
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
from queue import Empty, Full, Queue
import shutil
import sqlite3
import stat as stat_module
from threading import Event, Lock, Thread
from time import monotonic, monotonic_ns
from uuid import uuid4
import zlib

logger = logging.getLogger(__name__)
SCHEMA = "topbot-worker-evidence-v1"
MAX_RECORD_BYTES = 1024 * 1024
DEFAULT_BUDGET = 20 * 1024**3
FREE_FLOOR = 10 * 1024**3
REVIEW_SECONDS = 30 * 86400
SCAN_SECONDS = 30
CHECKPOINT_BYTES = 64 * 1024**2
CHECKPOINT_SECONDS = 60
START_INDEX = "capture-starts.json"
_DISK_LOCK = Lock()
_ACTIVE_STARTS = {}
_INVENTORIES = {}
_INVENTORIES_LOCK = Lock()


def failure_category(exc):
    """Fixed, actionable categories from numeric codes only; never exception text."""
    if isinstance(exc, OverflowError):
        return "storage_review_required"
    code = getattr(exc, "sqlite_errorcode", None)
    base = code & 255 if type(code) is int else None
    number = getattr(exc, "errno", None)
    if base in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        return "contention"
    if base == sqlite3.SQLITE_FULL or number == errno.ENOSPC:
        return "disk_full"
    if (base in (sqlite3.SQLITE_READONLY, sqlite3.SQLITE_PERM, sqlite3.SQLITE_AUTH)
            or number in (errno.EACCES, errno.EPERM, errno.EROFS)):
        return "permission_or_read_only"
    if base in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
        return "corrupt_file"
    if base in (sqlite3.SQLITE_IOERR, sqlite3.SQLITE_CANTOPEN) or isinstance(exc, OSError):
        return "disk_io"
    if isinstance(exc, sqlite3.OperationalError):
        return "sqlite_operational"
    return "unclassified"


class Inventory:
    """Process-wide evidence usage for one directory; shared by every capture.

    A new capture previously reopened every older capture database to learn its
    start date. On the archive disk each open replays that file's WAL, which
    held recording in "starting" for minutes while the bounded queue overflowed,
    and one busy file ended the new capture. Usage is now a cached directory
    scan. Older files without a sidecar are read once, by a separate thread,
    and remembered in a small index file. Nothing here deletes or rewrites
    evidence, and the budget, free-space floor and review age are unchanged.
    """

    def __init__(self, root):
        self.root = Path(root)
        self.lock = Lock()
        self.scanned_at = None
        self.used = self.growth = 0
        self.oldest = None
        self.estimated = 0
        self.scan_seconds = None
        self.starts = {}
        self.pending = set()
        self.failed = {}
        self.resolver = None
        self.index_loaded = False

    def _load_index(self):
        self.index_loaded = True
        try:
            rows = json.loads((self.root / START_INDEX).read_text(encoding="utf-8"))["starts"]
            for name, value in rows.items():
                self.starts.setdefault(str(self.root / name), datetime.fromisoformat(value).timestamp())
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass  # Rebuildable convenience; the capture files remain authoritative.

    def _start(self, path, info):
        key = str(path)
        if key in self.starts:
            return self.starts[key], True
        value = _ACTIVE_STARTS.get(path)
        if value is None:
            try:
                value = json.loads(path.with_suffix(".metadata.json").read_text(encoding="utf-8"))["started_at"]
            except (OSError, ValueError, KeyError, TypeError):
                value = None
        if value is not None:
            try:
                self.starts[key] = datetime.fromisoformat(value).timestamp()
                return self.starts[key], True
            except (ValueError, TypeError):
                pass
        if key not in self.failed:
            self.pending.add(key)
        # Until its metadata is read, an older capture is dated by the file itself.
        return min(getattr(info, "st_birthtime", info.st_ctime), info.st_mtime), False

    def _scan(self):
        started = monotonic()
        if not self.index_loaded:
            self._load_index()
        used, oldest, estimated = 0, None, 0
        for path in self.root.rglob("*"):
            try:
                info = path.stat()
            except OSError:
                continue  # Another writer closed or checkpointed its WAL.
            if not stat_module.S_ISREG(info.st_mode):
                continue
            used += info.st_size
            if path.suffix != ".sqlite3":
                continue
            at, exact = self._start(path, info)
            estimated += not exact
            oldest = at if oldest is None else min(oldest, at)
        # A capture that has not created its file yet still counts toward review age.
        for path, value in list(_ACTIVE_STARTS.items()):
            if self.root in path.parents:
                at = datetime.fromisoformat(value).timestamp()
                oldest = at if oldest is None else min(oldest, at)
        self.used, self.oldest, self.estimated, self.growth = used, oldest, estimated, 0
        self.scanned_at = monotonic()
        self.scan_seconds = round(self.scanned_at-started, 3)

    def _resolve(self, names):
        for key in names:
            try:
                reader = sqlite3.connect(Path(key).resolve().as_uri()+"?mode=ro", uri=True, timeout=5)
                try:
                    value = json.loads(reader.execute("SELECT value FROM metadata").fetchone()[0])["started_at"]
                finally:
                    reader.close()
                at = datetime.fromisoformat(value).timestamp()
            except Exception as exc:
                with self.lock:
                    self.failed[key] = type(exc).__name__
                    self.pending.discard(key)
                continue
            with self.lock:
                self.starts[key] = at
                self.pending.discard(key)
                self.scanned_at = None  # The next check uses the exact date.
                known = {}
                for name, value in self.starts.items():
                    try:
                        relative = Path(name).relative_to(self.root).as_posix()
                    except ValueError:
                        continue
                    known[relative] = datetime.fromtimestamp(value, timezone.utc).isoformat()
            try:
                temporary = self.root / (START_INDEX + "." + uuid4().hex + ".tmp")
                temporary.write_bytes(canonical({"schema": "topbot-evidence-capture-starts-v1", "starts": known}))
                os.replace(temporary, self.root / START_INDEX)
            except OSError:
                pass

    def usage(self, *, wait=0.):
        with self.lock:
            if self.scanned_at is None or monotonic()-self.scanned_at >= SCAN_SECONDS:
                self._scan()
            resolver = self.resolver
            if self.pending and (resolver is None or not resolver.is_alive()):
                resolver = self.resolver = Thread(target=self._resolve, args=(sorted(self.pending),), daemon=True,
                                                  name="topbot-evidence-inventory")
                resolver.start()
        if wait and resolver is not None and resolver.is_alive():
            # Small local files finish here; a slow disk continues in the background.
            resolver.join(wait)
        with self.lock:
            if self.scanned_at is None:
                self._scan()
            return {"used": self.used+self.growth, "oldest": self.oldest, "estimated_starts": self.estimated,
                    "unreadable_starts": len(self.failed), "scan_seconds": self.scan_seconds}

    def grew(self, amount):
        with self.lock:
            self.growth += amount


def inventory(root):
    with _INVENTORIES_LOCK:
        if len(_INVENTORIES) > 16:
            _INVENTORIES.clear()
        return _INVENTORIES.setdefault(str(Path(root)), Inventory(root))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@lru_cache(maxsize=1)
def implementation():
    """Fingerprint code once per process; installed release trees are immutable."""
    base = Path(__file__).parent
    names = ("topbot_strategy.py", "topbot_depth.py", "topbot_evidence.py", "topbot_runner.py",
             "topbot_audit.py", "topbot_execution_trace.py", "topbot_time_exit.py", "topbot_session.py", "bot_service.py",
             "projectx_order_book.py", "projectx_client.py", "topstep_fees.py", "topbot_profit_guard.py", "topbot_entry_checks.py",
             "topbot_instrument_pause.py", "../bot_worker.py")
    names += ("topbot_study.py", "topbot_study_store.py", "topbot_study_worker.py")
    try:
        files = {name: sha256((base / name).read_text(encoding="utf-8").encode()).hexdigest()
                 for name in names if (base / name).exists()}
        return {"schema": SCHEMA, "code_sha256": sha256(canonical(files)).hexdigest(),
                "release": base.parents[2].name, "files": files}
    except (OSError, UnicodeError) as exc:
        return {"schema": SCHEMA, "code_sha256": None, "release": base.parents[2].name,
                "files": {}, "error_type": type(exc).__name__}


def binding():
    value = implementation()
    return {k: value[k] for k in ("schema", "code_sha256", "release")}


def normalized(event):
    """Allowlist exactly the input fields consumed by DepthState, without messages."""
    data = event.get("data") or {}
    result = {k: data[k] for k in ("contract_id", "timestamp", "sequence", "reset", "side", "price",
                                  "size", "state", "threshold", "persistence_seconds", "reason",
                                  "entry_id", "start") if k in data}
    for side in ("bids", "asks"):
        if side in data:
            if len(data[side]) > 2001:
                raise ValueError("oversize_evidence_book")
            result[side] = [{k: row[k] for k in ("price", "size") if k in row} for row in data[side]]
    return {"event": event.get("event"), "data": result}


class Capture:
    """One writer per feed. Producers only encode/enqueue; SQLite never holds the book lock."""
    def __init__(self, owner, contract, *, root=None, queue_size=4096, budget=DEFAULT_BUDGET,
                 free_floor=FREE_FLOOR):
        if root is None:
            from .databento_cache import default_cache_root
            root = Path(os.environ.get("TOPSIGNAL_TOPBOT_EVIDENCE_DIR") or default_cache_root() / "topbot-worker-evidence-v1")
        self.root = Path(root)
        self.capture_id = uuid4().hex
        self.owner_hash = sha256(str(owner).encode()).hexdigest()
        self.contract = contract
        self.path = self.root / self.owner_hash / (self.capture_id + ".sqlite3")
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.capture_dates = {}
        self.queue = Queue(maxsize=queue_size)
        self.queue_lock, self.queued_bytes = Lock(), 0
        self.stop = Event()
        self.index = self.durable_index = self.dropped = 0
        self.durable_events = self.committed_batches = 0
        self.last_durable_at = self.last_durable_received_at = None
        self.last_commit_completed_at = None
        self.last_drop = None
        self.phase, self.phase_started = "initializing", monotonic()
        self.phase_seconds = {}
        self.last_progress = monotonic()
        self.pending_events, self.pending_since = 0, monotonic()
        self.state, self.error_type = "starting", None
        self.error_category = self.last_error_at = self.blocked_reason = None
        self.failures = self.consecutive_failures = self.recoveries = 0
        self.last_commit_seconds = self.max_commit_seconds = None
        self.storage = {}
        self.checkpoint = {"runs": 0}
        self.budget, self.free_floor = budget, free_floor
        self.thread = Thread(target=self._run, daemon=True, name="topbot-evidence")
        self.thread.start()

    def append(self, event, at, transitions):
        self.index += 1
        reference = {"capture_id": self.capture_id, "event_index": self.index,
                     "received_at": at.isoformat()}
        try:
            row = {**reference, "monotonic_ns": monotonic_ns(), "event": normalized(event),
                   "transitions": transitions, "dropped_before": self.dropped}
            raw = canonical(row)
            if len(raw) > MAX_RECORD_BYTES:
                raise ValueError("oversize_evidence_event")
            if self.state in {"error", "storage_blocked", "closed"}:
                raise Full  # "retrying" keeps buffering up to the queue bound.
            with self.queue_lock:
                if self.queued_bytes + len(raw) > 8*1024**2:
                    raise Full
                self.queue.put_nowait((self.index, raw, monotonic()))
                self.queued_bytes += len(raw)
        except (Full, ValueError, TypeError, KeyError):
            self.dropped += 1
            self.last_drop = {"event_index": self.index, "received_at": at.isoformat()}
            if self.dropped & (self.dropped-1) == 0:
                from ..bot_diagnostics import event as diagnostic
                diagnostic(logger, "topbot_evidence_loss", level=logging.WARNING,
                           contract_id=self.contract, failure_count=self.dropped, state=self.state)
        return reference

    def status(self):
        # Called under the market-book lock: memory only, no file or disk calls.
        now = monotonic()
        with self.queue.mutex:
            head = self.queue.queue[0] if self.queue.queue else None
        oldest = round(now-head[2], 3) if head is not None else (
            round(now-self.pending_since, 3) if self.pending_events else None)
        stalled = (self.state in {"starting", "recording"} and (head is not None or self.pending_events)
                   and now-self.last_progress > 5)
        return {"state": "stalled" if stalled else self.state, "durable_index": self.durable_index,
                "dropped_events": self.dropped, "queued_events": self.queue.qsize(),
                "queued_bytes": self.queued_bytes, "queue_limit_events": self.queue.maxsize,
                "oldest_queued_seconds": oldest,
                "inflight_events": self.pending_events, "durable_events": self.durable_events,
                "committed_batches": self.committed_batches, "last_durable_at": self.last_durable_at,
                "last_durable_received_at": self.last_durable_received_at, "last_drop": self.last_drop,
                "last_commit_completed_at": self.last_commit_completed_at,
                "last_commit_seconds": self.last_commit_seconds, "max_commit_seconds": self.max_commit_seconds,
                "phase": self.phase, "phase_elapsed_seconds": round(now-self.phase_started, 3),
                "phase_seconds": dict(self.phase_seconds), "writer_alive": self.thread.is_alive(),
                "produced_index": self.index,
                "budget_bytes": self.budget, "free_floor_bytes": self.free_floor,
                "storage": dict(self.storage), "checkpoint": dict(self.checkpoint),
                "error_type": self.error_type, "error_category": self.error_category,
                "blocked_reason": self.blocked_reason, "last_error_at": self.last_error_at,
                "failures": self.failures, "consecutive_failures": self.consecutive_failures,
                "recoveries": self.recoveries,
                "durability": "Only committed batches are replayable"}

    def _phase(self, name):
        self.phase_seconds[self.phase] = round(monotonic()-self.phase_started, 6)
        self.phase, self.phase_started = name, monotonic()

    def _capacity(self, growth=0):
        usage = inventory(self.root).usage(wait=0. if growth else 1.)
        free = shutil.disk_usage(self.root).free
        reserve = 16 * 1024**2 + growth*4  # SQLite pages plus WAL, conservatively.
        oldest = usage["oldest"]
        stamp = lambda value: datetime.fromtimestamp(value, timezone.utc).isoformat()
        self.storage = {"evidence_bytes": usage["used"], "disk_free_bytes": free,
                        "oldest_capture_started_at": stamp(oldest) if oldest else None,
                        "review_due_at": stamp(oldest+REVIEW_SECONDS) if oldest else None,
                        "estimated_capture_starts": usage["estimated_starts"],
                        "unreadable_capture_starts": usage["unreadable_starts"],
                        "scan_seconds": usage["scan_seconds"]}
        reason = ("evidence_budget_reached" if usage["used"] + reserve > self.budget else
                  "disk_free_floor" if free < self.free_floor+reserve else
                  "retention_review_due" if oldest is not None
                  and datetime.now(timezone.utc).timestamp()-oldest > REVIEW_SECONDS else None)
        if reason:
            self.blocked_reason = reason
            raise OverflowError("evidence_storage_review_required")
        self.blocked_reason = None

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=1)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            # The default 1,000-page auto-checkpoint runs inside commit(). On
            # the archive HDD it can block this sole writer for a minute while
            # bulk readers compete for seeks. Keep FULL WAL durability; a
            # separate thread checkpoints so commits never wait for it. Both
            # database and WAL count toward the disk budget/free-space guard.
            db.execute("PRAGMA wal_autocheckpoint=0")
        except BaseException:
            db.close()
            raise
        return db

    def _open(self):
        self._phase("capacity_startup")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._capacity()
        self._phase("sqlite_open")
        db = self._connect()
        try:
            meta = {"schema": SCHEMA, "capture_id": self.capture_id, "owner_hash": self.owner_hash,
                    "contract_id": self.contract, "started_at": self.started_at,
                    "implementation": implementation(), "provider_snapshot_complete": False}
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='batches'").fetchone():
                # executescript otherwise autocommits each DDL statement. Under
                # disk contention that delayed first recording through seven FULL
                # sync transactions. Schema and metadata need one atomic commit.
                db.executescript("""BEGIN IMMEDIATE; CREATE TABLE metadata(value TEXT NOT NULL);
                    CREATE TABLE status(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL);
                    CREATE TABLE batches(id INTEGER PRIMARY KEY, first_index INTEGER, last_index INTEGER,
                      count INTEGER, previous_hash TEXT, hash TEXT, payload BLOB);
                    CREATE TRIGGER no_batch_update BEFORE UPDATE ON batches BEGIN SELECT RAISE(ABORT,'append_only'); END;
                    CREATE TRIGGER no_batch_delete BEFORE DELETE ON batches BEGIN SELECT RAISE(ABORT,'append_only'); END;""")
                db.execute("INSERT INTO metadata VALUES(?)", (canonical(meta).decode(),))
                self._phase("metadata_commit")
                db.commit()
            temporary_metadata = self.path.with_suffix(".metadata.pending")
            with temporary_metadata.open("wb") as sidecar:
                sidecar.write(canonical(meta))
                sidecar.flush()
                os.fsync(sidecar.fileno())
            os.replace(temporary_metadata, self.path.with_suffix(".metadata.json"))
            stored = db.execute("SELECT value FROM metadata").fetchone()[0]
        except BaseException:
            _close(db)
            raise
        return db, sha256(stored.encode()).hexdigest()

    def _failed(self, exc):
        category = failure_category(exc)
        self.error_type, self.error_category = type(exc).__name__, category
        self.last_error_at = datetime.now(timezone.utc).isoformat()
        self.failures += 1
        self.consecutive_failures += 1
        # Contention and I/O faults keep buffering in the bounded queue while the
        # writer retries. Storage limits and other faults refuse new evidence
        # immediately (counted as loss) and are retried at a slower cadence.
        self.state = ("storage_blocked" if category in {"storage_review_required", "disk_full"} else
                      "retrying" if category in {"contention", "disk_io"} and self.consecutive_failures <= 20
                      else "error")
        if self.consecutive_failures & (self.consecutive_failures-1) == 0:
            from ..bot_diagnostics import event
            event(logger, "topbot_evidence_unavailable", level=logging.ERROR, contract_id=self.contract,
                  state=self.state, error_type=self.error_type, error_category=category,
                  blocked_reason=self.blocked_reason, failure_count=self.consecutive_failures)
        return 30. if self.state == "storage_blocked" else min(30., .25 * 2**min(self.consecutive_failures, 7))

    def _recovered(self):
        if self.consecutive_failures:
            from ..bot_diagnostics import event
            event(logger, "topbot_evidence_recovered", level=logging.WARNING, contract_id=self.contract,
                  failure_count=self.consecutive_failures, dropped_events=self.dropped)
            self.recoveries += 1
        self.consecutive_failures = 0
        self.error_type = self.error_category = None
        self.state = "recording"

    def _lost(self, count, at):
        if count:
            self.dropped += count
            self.last_drop = {"event_index": self.index, "received_at": at,
                              "reason": "writer_stopped_before_commit", "events": count}

    def _checkpoints(self):
        """Bound WAL growth without ever making a commit wait for a checkpoint."""
        wal = Path(str(self.path)+"-wal")
        while not self.stop.wait(CHECKPOINT_SECONDS):
            try:
                if wal.stat().st_size < CHECKPOINT_BYTES:
                    continue
                started = monotonic()
                db = sqlite3.connect(self.path, timeout=1)
                try:
                    db.execute("PRAGMA wal_autocheckpoint=0")
                    busy, frames, moved = db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
                finally:
                    db.close()
                self.checkpoint = {"runs": self.checkpoint["runs"]+1, "at": datetime.now(timezone.utc).isoformat(),
                                   "seconds": round(monotonic()-started, 3), "busy": busy,
                                   "wal_frames": frames, "checkpointed_frames": moved}
            except (OSError, sqlite3.Error) as exc:
                self.checkpoint = {**self.checkpoint, "error_type": type(exc).__name__,
                                   "error_at": datetime.now(timezone.utc).isoformat()}

    def _run(self):
        db = None
        checkpointer = None
        try:
            _ACTIVE_STARTS[self.path] = self.started_at
            while True:
                try:
                    db, previous = self._open()
                    break
                except Exception as exc:
                    if self.stop.wait(self._failed(exc)):
                        return
            self._recovered()
            self.last_progress = monotonic()
            checkpointer = Thread(target=self._checkpoints, daemon=True, name="topbot-evidence-checkpoint")
            checkpointer.start()
            pending = None
            while True:
                if pending is None:
                    if self.stop.is_set() and self.queue.empty():
                        break
                    self._phase("collecting")
                    batch = []
                    try:
                        batch.append(self.queue.get(timeout=.25))
                    except Empty:
                        continue
                    until = monotonic()+.25
                    batch_bytes = len(batch[0][1])
                    # Amortize FULL-sync disk commits when the feed arrives in bursts.
                    # Keep the same byte and collection-time bounds; a small batch
                    # count otherwise caps throughput below the incoming event rate.
                    while len(batch) < 2048 and batch_bytes < 4*1024**2 and monotonic() < until:
                        try:
                            item = self.queue.get(timeout=max(.001, until-monotonic()))
                            batch.append(item)
                            batch_bytes += len(item[1])
                        except Empty: break
                    with self.queue_lock:
                        self.queued_bytes -= sum(len(item[1]) for item in batch)
                    self.pending_events, self.pending_since = len(batch), batch[0][2]
                    self._phase("compressing")
                    raw = b"\n".join(item[1] for item in batch)
                    pending = (batch, sha256(previous.encode()+raw).hexdigest(), zlib.compress(raw, 3),
                               json.loads(batch[-1][1])["received_at"])
                batch, digest, compressed, received_at = pending
                try:
                    with _DISK_LOCK:
                        self._phase("capacity_batch")
                        self._capacity(len(compressed))
                        self._phase("batch_commit")
                        began = monotonic()
                        if db is None:
                            db = self._connect()
                        # After an ambiguous failure the batch may already be durable.
                        present = self.consecutive_failures and db.execute(
                            "SELECT 1 FROM batches WHERE first_index=? AND hash=?", (batch[0][0], digest)).fetchone()
                        if not present:
                            committed_at = datetime.now(timezone.utc).isoformat()
                            durable_status = {**self.status(), "state": "recording", "durable_index": batch[-1][0],
                                "durable_events": self.durable_events+len(batch), "committed_batches": self.committed_batches+1,
                                "last_durable_at": received_at, "last_durable_received_at": received_at,
                                "commit_started_at": committed_at, "inflight_events": 0}
                            db.execute("INSERT INTO batches(first_index,last_index,count,previous_hash,hash,payload) VALUES(?,?,?,?,?,?)",
                                       (batch[0][0], batch[-1][0], len(batch), previous, digest, compressed))
                            db.execute("INSERT OR REPLACE INTO status VALUES(1,?)", (canonical(durable_status).decode(),))
                            db.commit()
                        seconds = round(monotonic()-began, 6)
                except Exception as exc:
                    delay = self._failed(exc)
                    # Never reuse a connection after a failed write; reopen for the retry.
                    _close(db)
                    db = None
                    if self.stop.wait(delay):
                        self._lost(len(batch)+self.queue.qsize(), received_at)
                        return
                    continue
                inventory(self.root).grew(len(compressed))
                pending = None
                previous, self.durable_index = digest, batch[-1][0]
                self.last_durable_at = self.last_durable_received_at = received_at
                self.last_commit_completed_at = datetime.now(timezone.utc).isoformat()
                self.last_commit_seconds = seconds
                self.max_commit_seconds = max(seconds, self.max_commit_seconds or 0)
                self.durable_events += len(batch)
                self.committed_batches += 1
                self.pending_events = 0
                self.last_progress = monotonic()
                self._recovered()
            self.state = "closed"
        except Exception as exc:
            # Unexpected fault in the recorder itself: visible, never raised into trading.
            self._failed(exc)
            self.state = "error"
        finally:
            self.stop.set()
            if checkpointer is not None:
                checkpointer.join(timeout=2)
            if db is not None:
                try:
                    if db.in_transaction:
                        db.rollback()
                    db.execute("INSERT OR REPLACE INTO status VALUES(1,?)", (canonical(self.status()).decode(),))
                    db.commit()
                except sqlite3.Error:
                    pass
                _close(db)
            _ACTIVE_STARTS.pop(self.path, None)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)


def _close(db):
    try:
        if db is not None:
            db.close()
    except sqlite3.Error:
        pass


def recorded_batches(path):
    """Verify and stream one consistent read-only snapshot; reject corrupt/missing batches."""
    db = sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        meta = json.loads(db.execute("SELECT value FROM metadata").fetchone()[0])
        if meta.get("schema") != SCHEMA:
            raise ValueError("unsupported_evidence_schema")
        status = db.execute("SELECT value FROM status WHERE id=1").fetchone()
        yield {"metadata": meta, "status": json.loads(status[0]) if status else {"state": "starting"}}
        previous, last, batch_id = sha256(canonical(meta)).hexdigest(), 0, 0
        for row in db.execute("SELECT id,first_index,last_index,count,previous_hash,hash,payload FROM batches ORDER BY id"):
            decoder = zlib.decompressobj()
            raw = decoder.decompress(row[6], 6*1024**2)
            if not decoder.eof or decoder.unused_data:
                raise ValueError("oversize_or_invalid_evidence_batch")
            values = [json.loads(line) for line in raw.splitlines()]
            indexes = [v["event_index"] for v in values]
            if (row[0] != batch_id+1 or row[4] != previous or sha256(previous.encode()+raw).hexdigest() != row[5]
                    or len(values) != row[3] or indexes[0] != row[1] or indexes[-1] != row[2]
                    or any(b <= a for a,b in zip([last]+indexes, indexes))
                    or any(v["capture_id"] != meta["capture_id"] for v in values)):
                raise ValueError("invalid_evidence_batch")
            for value in values: yield value
            previous, last, batch_id = row[5], indexes[-1], row[0]
    finally:
        db.close()
