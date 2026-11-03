"""Bounded, asynchronous recording of the exact normalized worker input stream.

Only local SQLite files; never broker calls or application/cloud database writes.
Capture loss affects evidence quality, never trading rules or protective management.
"""
from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
from queue import Empty, Full, Queue
import shutil
import sqlite3
from threading import Event, Lock, Thread
from time import monotonic, monotonic_ns
from uuid import uuid4
import zlib

logger = logging.getLogger(__name__)
SCHEMA = "topbot-worker-evidence-v1"
MAX_RECORD_BYTES = 1024 * 1024
DEFAULT_BUDGET = 20 * 1024**3
FREE_FLOOR = 10 * 1024**3
_DISK_LOCK = Lock()
_ACTIVE_STARTS = {}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@lru_cache(maxsize=1)
def implementation():
    """Fingerprint code once per process; installed release trees are immutable."""
    base = Path(__file__).parent
    names = ("topbot_strategy.py", "topbot_depth.py", "topbot_evidence.py", "topbot_runner.py",
             "topbot_audit.py", "topbot_execution_trace.py", "topbot_time_exit.py", "topbot_session.py", "bot_service.py",
             "projectx_order_book.py", "topstep_fees.py", "topbot_profit_guard.py", "topbot_entry_checks.py", "../bot_worker.py")
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
        self.state, self.error_type = "starting", None
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
                raise Full
            with self.queue_lock:
                if self.queued_bytes + len(raw) > 8*1024**2:
                    raise Full
                self.queue.put_nowait((self.index, raw))
                self.queued_bytes += len(raw)
        except (Full, ValueError, TypeError, KeyError):
            self.dropped += 1
            if self.dropped & (self.dropped-1) == 0:
                from ..bot_diagnostics import event as diagnostic
                diagnostic(logger, "topbot_evidence_loss", level=logging.WARNING,
                           contract_id=self.contract, failure_count=self.dropped, state=self.state)
        return reference

    def status(self):
        return {"state": self.state, "durable_index": self.durable_index,
                "dropped_events": self.dropped, "queued_events": self.queue.qsize(),
                "produced_index": self.index,
                "budget_bytes": self.budget, "free_floor_bytes": self.free_floor,
                "error_type": self.error_type, "durability": "Only committed batches are replayable"}

    def _capacity(self, growth=0):
        paths = [p for p in self.root.rglob("*") if p.is_file()]
        used, starts = 0, []
        for path in paths:
            try: used += path.stat().st_size
            except FileNotFoundError: continue  # Another writer closed/checkpointed its WAL.
            if path.suffix != ".sqlite3": continue
            if path not in self.capture_dates:
                stamp = _ACTIVE_STARTS.get(path)
                if stamp is None:
                    reader = sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=1)
                    try: stamp = json.loads(reader.execute("SELECT value FROM metadata").fetchone()[0])["started_at"]
                    finally: reader.close()
                self.capture_dates[path] = datetime.fromisoformat(stamp).timestamp()
            starts.append(self.capture_dates[path])
        oldest = min(starts, default=None)
        reserve = 16 * 1024**2 + growth*4  # SQLite pages plus WAL, conservatively.
        if (used + reserve > self.budget or shutil.disk_usage(self.root).free < self.free_floor+reserve
                or (oldest is not None and datetime.now(timezone.utc).timestamp()-oldest > 30*86400)):
            raise OverflowError("evidence_storage_review_required")

    def _run(self):
        db = None
        try:
            _ACTIVE_STARTS[self.path] = self.started_at
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._capacity()
            db = sqlite3.connect(self.path, timeout=1)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.executescript("""CREATE TABLE metadata(value TEXT NOT NULL);
                CREATE TABLE status(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL);
                CREATE TABLE batches(id INTEGER PRIMARY KEY, first_index INTEGER, last_index INTEGER,
                  count INTEGER, previous_hash TEXT, hash TEXT, payload BLOB);
                CREATE TRIGGER no_batch_update BEFORE UPDATE ON batches BEGIN SELECT RAISE(ABORT,'append_only'); END;
                CREATE TRIGGER no_batch_delete BEFORE DELETE ON batches BEGIN SELECT RAISE(ABORT,'append_only'); END;""")
            meta = {"schema": SCHEMA, "capture_id": self.capture_id, "owner_hash": self.owner_hash,
                    "contract_id": self.contract, "started_at": self.started_at,
                    "implementation": implementation(), "provider_snapshot_complete": False}
            db.execute("INSERT INTO metadata VALUES(?)", (canonical(meta).decode(),))
            db.commit()
            previous = sha256(canonical(meta)).hexdigest()
            self.state = "recording"
            while not self.stop.is_set() or not self.queue.empty():
                batch = []
                try:
                    batch.append(self.queue.get(timeout=.25))
                except Empty:
                    continue
                until = monotonic()+.25
                batch_bytes = len(batch[0][1])
                while len(batch) < 128 and batch_bytes < 4*1024**2 and monotonic() < until:
                    try:
                        item = self.queue.get(timeout=max(.001, until-monotonic()))
                        batch.append(item)
                        batch_bytes += len(item[1])
                    except Empty: break
                with self.queue_lock:
                    self.queued_bytes -= sum(len(item[1]) for item in batch)
                raw = b"\n".join(item[1] for item in batch)
                digest = sha256(previous.encode()+raw).hexdigest()
                compressed = zlib.compress(raw, 3)
                with _DISK_LOCK:
                    self._capacity(len(compressed))
                    db.execute("INSERT INTO batches(first_index,last_index,count,previous_hash,hash,payload) VALUES(?,?,?,?,?,?)",
                               (batch[0][0], batch[-1][0], len(batch), previous, digest, compressed))
                    db.execute("INSERT OR REPLACE INTO status VALUES(1,?)", (canonical({**self.status(), "durable_index": batch[-1][0]}).decode(),))
                    db.commit()
                previous, self.durable_index = digest, batch[-1][0]
            self.state = "closed"
        except Exception as exc:
            self.state = "storage_blocked" if isinstance(exc, OverflowError) else "error"
            self.error_type = type(exc).__name__
            from ..bot_diagnostics import event
            event(logger, "topbot_evidence_unavailable", level=logging.ERROR, contract_id=self.contract,
                  state=self.state, error_type=self.error_type)
        finally:
            if db is not None:
                try:
                    db.rollback()
                    db.execute("INSERT OR REPLACE INTO status VALUES(1,?)", (canonical(self.status()).decode(),))
                    db.commit()
                except sqlite3.Error:
                    pass
                db.close()
            _ACTIVE_STARTS.pop(self.path, None)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)


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
