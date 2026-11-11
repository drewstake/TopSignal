"""Derived, bounded replay checkpoints for registered exit-study captures.

Every exit-study replay previously re-read, re-verified and re-applied a worker
capture from its first event up to the entry. Late entries in a long capture
exceeded the child deadline, were killed, and retried from the beginning again
while the live recorder was writing to the same disk.

This module keeps the recorded model unchanged. It mirrors the hash-chain
verification of ``topbot_evidence.recorded_batches`` and the per-row logic of
``topbot_study_store.quotes`` (part of the experiment fingerprint) so that a
replay can resume the same reader state at a verified batch boundary instead of
event 1. The mirror is only used while the fingerprints of those two functions
match the versions it was written and tested against; any model change disables
it and replays fall back to the original full scan.

Checkpoints are derived local files under ``TOPSIGNAL_DATABENTO_CACHE_DIR``.
Each one is anchored to the capture's hash chain (batch id, batch hash, metadata
hash) and carries a SHA-256 of its own state. Raw captures are never modified.
Nothing here is written to the application or cloud database, and nothing is
deleted automatically: when the per-owner budget or the free-space floor is
reached, no new checkpoints are written and replays use the full scan.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import inspect
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
import zlib

from .topbot_depth import DepthState, utc
from .topbot_evidence import SCHEMA as EVIDENCE_SCHEMA, canonical

SCHEMA = "topbot-replay-checkpoints-v1"
INTERVAL_SECONDS = 300
MAX_OWNER_BYTES = 256 * 1024**2
FREE_FLOOR = 10 * 1024**3
MAX_STATE_BYTES = 4 * 1024**2
FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)


class CheckpointError(ValueError):
    """A derived checkpoint is unusable. The capture itself is not implicated."""

# Fingerprints of the functions this module mirrors. Recomputed at import; a
# mismatch means the recorded model changed and the mirror must not be used.
MIRRORED_SOURCES = {
    "topbot_study_store.quotes": "a12db3c86b3ca876d99b262eb047762dd2e413ca1b350d7025a99e72fd57a240",
    "topbot_evidence.recorded_batches": "0aaf9373dbaf115cbd69bf09e68a492dfcc4974b7c9786d2330b487bed33d5c9",
    "topbot_depth.DepthState": "66b5bb840e0a9acad308f1b0f7996302aaf12ff920c157d84035b3785e138dad",
}


def _source_digest(obj):
    return sha256(inspect.getsource(obj).encode()).hexdigest()


def current_sources():
    from . import topbot_study_store, topbot_evidence
    return {"topbot_study_store.quotes": _source_digest(topbot_study_store.quotes),
            "topbot_evidence.recorded_batches": _source_digest(topbot_evidence.recorded_batches),
            "topbot_depth.DepthState": _source_digest(DepthState)}


def mirror_status():
    """Whether the resumable reader matches the recorded reader it mirrors."""
    try:
        current = current_sources()
    except (OSError, TypeError) as exc:
        return {"available": False, "reason": "source_unavailable_" + type(exc).__name__}
    changed = sorted(k for k, v in MIRRORED_SOURCES.items() if current.get(k) != v)
    return {"available": not changed, "reason": "recorded_reader_changed" if changed else None,
            "changed": changed, "fingerprint": sha256(canonical(current)).hexdigest()}


# ---------------------------------------------------------------------------
# Reader state: exact serialization of the quotes() generator locals.
# ---------------------------------------------------------------------------

_TIMES = ("last_at", "episode_at", "qualified_episode")
_PLAIN = ("contract", "threshold", "sequence", "direction", "connected", "qualification_reset",
          "persistence_required", "transitions", "silence_reported")


def _state_attributes():
    return set(vars(DepthState("probe")))


def dump_state(state):
    if state.tracks or state.track_history or state.capture is not None or state.evidence is not None:
        raise ValueError("replay_state_not_isolated")
    if set(vars(state)) != _state_attributes():
        raise ValueError("replay_state_shape_changed")
    value = {"bids": [[p, q] for p, q in state.bids.items()],
             "asks": [[p, q] for p, q in state.asks.items()]}
    for name in _PLAIN:
        value[name] = getattr(state, name)
    for name in _TIMES:
        stamp = getattr(state, name)
        value[name] = stamp.isoformat() if stamp is not None else None
    return value


def load_state(value):
    state = DepthState(value["contract"], value["threshold"])
    state.bids = {float(p): float(q) for p, q in value["bids"]}
    state.asks = {float(p): float(q) for p, q in value["asks"]}
    for name in _PLAIN:
        setattr(state, name, value[name])
    for name in _TIMES:
        setattr(state, name, utc(value[name]) if value[name] is not None else None)
    if set(vars(state)) != _state_attributes():
        raise ValueError("replay_state_shape_changed")
    return state


class Mirror:
    """Row-by-row copy of topbot_study_store.quotes(); see the module docstring."""

    def __init__(self, contract, start, cutoff, *, state=None, last=0, damaged=False):
        self.state = state if state is not None else DepthState(contract)
        self.start, self.cutoff = start, cutoff
        self.last, self.damaged = last, damaged

    def step(self, row):
        """Return (stop, quote). ``stop`` mirrors the generator's break."""
        at = utc(row["received_at"])
        if at > self.cutoff:
            return True, None
        if row["event_index"] != self.last+1:
            self.damaged = True
        self.last = row["event_index"]
        if self.last % 4096 == 0:
            time.sleep(.001)  # Same CPU yield as the recorded reader.
        state = self.state
        prior_sequence = state.sequence
        state.apply(row["event"], at)
        sequence = (row["event"].get("data") or {}).get("sequence")
        if (at <= self.start and row["event"].get("event") == "snapshot"
                and sequence is not None and state.sequence == sequence
                and (prior_sequence is None or sequence > prior_sequence)
                and state.snapshot(at).get("ready")):
            self.damaged = False
        if at < self.start:
            return False, None
        snap = state.snapshot(at)
        if self.damaged or not snap.get("ready"):
            return False, {"at": at.isoformat(), "gap": "capture_index_gap" if self.damaged else "feed_unavailable"}
        return False, {"at": at.isoformat(), "capture_event_index": self.last, "bid": snap["bid"], "ask": snap["ask"],
                       "bid_size": state.bids[snap["bid"]], "ask_size": state.asks[snap["ask"]]}


def _ready(state, at):
    """Cheap equivalent of DepthState.snapshot(at)["ready"], for coverage statistics only."""
    if not state.connected or not state.last_at or not 0 <= (at-state.last_at).total_seconds() <= 2:
        return False
    return bool(state.bids and state.asks and max(state.bids) < min(state.asks))


# ---------------------------------------------------------------------------
# Verified batch stream starting at any anchored batch boundary.
# ---------------------------------------------------------------------------

class Capture:
    """Read-only, single-snapshot view of one worker capture."""

    def __init__(self, path):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path.resolve().as_uri()+"?mode=ro", uri=True, timeout=5)
        try:
            self.db.execute("PRAGMA query_only=ON")
            self.db.execute("BEGIN")
            self.meta = json.loads(self.db.execute("SELECT value FROM metadata").fetchone()[0])
            if self.meta.get("schema") != EVIDENCE_SCHEMA:
                raise ValueError("unsupported_evidence_schema")
            self.meta_hash = sha256(canonical(self.meta)).hexdigest()
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def check_scope(self, owner, contract):
        if self.meta["owner_hash"] != sha256(str(owner).encode()).hexdigest() or self.meta["contract_id"] != contract:
            raise ValueError("capture_scope_mismatch")

    def anchor_matches(self, batch_id, batch_hash):
        if batch_id == 0:
            return batch_hash == self.meta_hash
        row = self.db.execute("SELECT hash FROM batches WHERE id=?", (batch_id,)).fetchone()
        return row is not None and row[0] == batch_hash

    def batches(self, after_batch, previous, last_index):
        """Yield (batch_id, batch_hash, rows), verified exactly as recorded_batches does."""
        batch_id, last = after_batch, last_index
        cursor = self.db.execute("SELECT id,first_index,last_index,count,previous_hash,hash,payload FROM batches "
                                 "WHERE id>? ORDER BY id", (after_batch,))
        for row in cursor:
            decoder = zlib.decompressobj()
            raw = decoder.decompress(row[6], 6*1024**2)
            if not decoder.eof or decoder.unused_data:
                raise ValueError("oversize_or_invalid_evidence_batch")
            values = [json.loads(line) for line in raw.splitlines()]
            indexes = [v["event_index"] for v in values]
            if (row[0] != batch_id+1 or row[4] != previous or sha256(previous.encode()+raw).hexdigest() != row[5]
                    or len(values) != row[3] or indexes[0] != row[1] or indexes[-1] != row[2]
                    or any(b <= a for a, b in zip([last]+indexes, indexes))
                    or any(v["capture_id"] != self.meta["capture_id"] for v in values)):
                raise ValueError("invalid_evidence_batch")
            yield row[0], row[5], values
            previous, last, batch_id = row[5], indexes[-1], row[0]


# ---------------------------------------------------------------------------
# Checkpoint store (derived, owner-scoped, bounded).
# ---------------------------------------------------------------------------

@dataclass
class Checkpoint:
    batch_id: int
    batch_hash: str
    last_index: int
    received_at: str
    max_received_at: str
    damaged: bool
    state: dict


def root_dir():
    override = os.environ.get("TOPSIGNAL_REPLAY_CHECKPOINT_DIR")
    if override:
        return Path(override)
    from .databento_cache import default_cache_root
    return default_cache_root() / SCHEMA


class Store:
    def __init__(self, owner_hash, capture_id, *, root=None, budget=None, free_floor=None):
        if len(owner_hash) != 64 or any(c not in "0123456789abcdef" for c in owner_hash):
            raise ValueError("invalid_owner_scope")
        if len(capture_id) != 32 or any(c not in "0123456789abcdef" for c in capture_id):
            raise ValueError("invalid_capture_id")
        self.directory = Path(root or root_dir()) / owner_hash
        self.path = self.directory / (capture_id + ".sqlite3")
        self.capture_id = capture_id
        self.budget = MAX_OWNER_BYTES if budget is None else budget
        self.free_floor = FREE_FLOOR if free_floor is None else free_floor
        self.db = None
        self.blocked = None

    def owner_bytes(self):
        if not self.directory.exists():
            return 0
        return sum(p.stat().st_size for p in self.directory.iterdir() if p.is_file())

    def writable(self):
        """Never delete to make room: stop writing at the budget or the free-space floor."""
        self.directory.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.directory).free < self.free_floor:
            self.blocked = "disk_free_floor"
        elif self.owner_bytes() + 8*1024**2 > self.budget:
            self.blocked = "checkpoint_budget_reached"
        else:
            self.blocked = None
        return self.blocked is None

    def open(self, *, write):
        if self.db is not None:
            return self.db
        if not write and not self.path.exists():
            return None
        if write:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path, timeout=5)
            # Derived and rebuildable from the capture: NORMAL avoids an fsync per
            # checkpoint on the shared archive disk. A torn tail is detected by the
            # per-row hash and the store is rebuilt.
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript("""BEGIN;
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS checkpoints(batch_id INTEGER PRIMARY KEY, batch_hash TEXT NOT NULL,
                  last_index INTEGER NOT NULL, received_at TEXT NOT NULL, max_received_at TEXT NOT NULL,
                  damaged INTEGER NOT NULL, state BLOB NOT NULL, state_sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS segments(first_batch INTEGER PRIMARY KEY, last_batch INTEGER NOT NULL,
                  first_at TEXT, last_at TEXT, events INTEGER NOT NULL, index_holes INTEGER NOT NULL,
                  missing_events INTEGER NOT NULL, first_hole_at TEXT, last_hole_at TEXT,
                  max_gap_seconds REAL NOT NULL, max_gap_at TEXT, not_ready_events INTEGER NOT NULL,
                  first_not_ready_at TEXT, last_not_ready_at TEXT);
                COMMIT;""")
        else:
            self.db = sqlite3.connect(self.path.resolve().as_uri()+"?mode=ro", uri=True, timeout=5)
        return self.db

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None

    def bind(self, capture, fingerprint):
        """Checkpoints belong to exactly one capture metadata hash and reader fingerprint."""
        db = self.open(write=True)
        stored = dict(db.execute("SELECT key,value FROM meta").fetchall())
        wanted = {"schema": SCHEMA, "capture_id": capture.meta["capture_id"], "meta_sha256": capture.meta_hash,
                  "reader_fingerprint": fingerprint}
        if stored and stored != wanted:
            return False
        if not stored:
            with db:
                db.executemany("INSERT INTO meta VALUES(?,?)", sorted(wanted.items()))
        return True

    def matches(self, capture, fingerprint):
        db = self.open(write=False)
        if db is None:
            return False
        try:
            stored = dict(db.execute("SELECT key,value FROM meta").fetchall())
        except sqlite3.Error:
            return False
        return stored == {"schema": SCHEMA, "capture_id": capture.meta["capture_id"],
                          "meta_sha256": capture.meta_hash, "reader_fingerprint": fingerprint}

    def _row(self, row):
        raw = zlib.decompress(row[6])
        if sha256(raw).hexdigest() != row[7]:
            raise CheckpointError("checkpoint_hash_mismatch")
        return Checkpoint(row[0], row[1], row[2], row[3], row[4], bool(row[5]), json.loads(raw))

    def latest(self):
        db = self.open(write=False)
        if db is None:
            return None
        row = db.execute("SELECT * FROM checkpoints ORDER BY batch_id DESC LIMIT 1").fetchone()
        return self._row(row) if row else None

    def before(self, start):
        """Newest checkpoint whose every consumed row was received before ``start``."""
        db = self.open(write=False)
        if db is None:
            return None
        stamp = start.astimezone(timezone.utc)
        best = None
        for row in db.execute("SELECT batch_id,max_received_at FROM checkpoints ORDER BY batch_id DESC"):
            if utc(row[1]) < stamp:
                best = row[0]
                break
        if best is None:
            return None
        row = db.execute("SELECT * FROM checkpoints WHERE batch_id=?", (best,)).fetchone()
        return self._row(row)

    def write(self, checkpoint, segment):
        db = self.open(write=True)
        raw = canonical(checkpoint.state)
        if len(raw) > MAX_STATE_BYTES:
            raise CheckpointError("checkpoint_state_too_large")
        with db:
            db.execute("INSERT OR IGNORE INTO checkpoints VALUES(?,?,?,?,?,?,?,?)",
                       (checkpoint.batch_id, checkpoint.batch_hash, checkpoint.last_index, checkpoint.received_at,
                        checkpoint.max_received_at, int(checkpoint.damaged), zlib.compress(raw, 6),
                        sha256(raw).hexdigest()))
            if segment is not None and segment["events"]:
                db.execute("INSERT OR IGNORE INTO segments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           tuple(segment[k] for k in SEGMENT_FIELDS))

    def segments(self, start, end):
        db = self.open(write=False)
        if db is None:
            return []
        rows = db.execute("SELECT * FROM segments ORDER BY first_batch").fetchall()
        out = []
        for row in rows:
            value = dict(zip(SEGMENT_FIELDS, row))
            if value["last_at"] and value["first_at"] and utc(value["last_at"]) >= start and utc(value["first_at"]) <= end:
                out.append(value)
        return out


SEGMENT_FIELDS = ("first_batch", "last_batch", "first_at", "last_at", "events", "index_holes", "missing_events",
                  "first_hole_at", "last_hole_at", "max_gap_seconds", "max_gap_at", "not_ready_events",
                  "first_not_ready_at", "last_not_ready_at")


def _new_segment(first_batch):
    return {"first_batch": first_batch, "last_batch": first_batch, "first_at": None, "last_at": None, "events": 0,
            "index_holes": 0, "missing_events": 0, "first_hole_at": None, "last_hole_at": None,
            "max_gap_seconds": 0., "max_gap_at": None, "not_ready_events": 0,
            "first_not_ready_at": None, "last_not_ready_at": None}


# ---------------------------------------------------------------------------
# Builder and resumable reader.
# ---------------------------------------------------------------------------

def _owner_hash(path):
    return Path(path).parent.name


def advance(capture_path, owner, contract, *, until=None, budget_seconds=90., progress=None, root=None,
            interval=INTERVAL_SECONDS):
    """Extend a capture's checkpoints toward ``until`` within a time budget.

    Returns a summary. Work done before the budget is exhausted is saved at a
    batch boundary, so an interrupted builder never repeats it.
    """
    status = mirror_status()
    if not status["available"]:
        return {"state": "mirror_unavailable", "reason": status["reason"]}
    started = time.monotonic()
    capture = Capture(capture_path)
    store = Store(_owner_hash(capture_path), Path(capture_path).stem, root=root)
    try:
        capture.check_scope(owner, contract)
        if not store.writable():
            return {"state": "store_blocked", "reason": store.blocked}
        if not store.bind(capture, status["fingerprint"]):
            return {"state": "store_mismatch", "reason": "checkpoint_store_bound_to_other_capture_or_reader"}
        latest = store.latest()
        if latest is not None and not capture.anchor_matches(latest.batch_id, latest.batch_hash):
            raise CheckpointError("checkpoint_anchor_mismatch")
        if latest is None:
            mirror = Mirror(contract, FAR_FUTURE, FAR_FUTURE)
            batch_id, previous, max_at = 0, capture.meta_hash, None
            last_at = None
        else:
            mirror = Mirror(contract, FAR_FUTURE, FAR_FUTURE, state=load_state(latest.state),
                            last=latest.last_index, damaged=latest.damaged)
            batch_id, previous, max_at = latest.batch_id, latest.batch_hash, utc(latest.max_received_at)
            last_at = utc(latest.received_at)
        due = (last_at.timestamp() + interval) if last_at else None
        segment = _new_segment(batch_id+1)
        events = written = 0
        reached = False
        exhausted = False
        last_report = time.monotonic()
        pending_checkpoint = False
        for batch, digest, rows in capture.batches(batch_id, previous, mirror.last):
            for row in rows:
                at = utc(row["received_at"])
                expected = mirror.last+1
                if row["event_index"] != expected:
                    segment["index_holes"] += 1
                    segment["missing_events"] += row["event_index"]-expected
                    segment["first_hole_at"] = segment["first_hole_at"] or row["received_at"]
                    segment["last_hole_at"] = row["received_at"]
                if last_at is not None:
                    gap = (at-last_at).total_seconds()
                    if gap > segment["max_gap_seconds"]:
                        segment["max_gap_seconds"], segment["max_gap_at"] = gap, last_at.isoformat()
                mirror.step(row)
                if not _ready(mirror.state, at):
                    segment["not_ready_events"] += 1
                    segment["first_not_ready_at"] = segment["first_not_ready_at"] or row["received_at"]
                    segment["last_not_ready_at"] = row["received_at"]
                segment["first_at"] = segment["first_at"] or row["received_at"]
                segment["last_at"] = row["received_at"]
                segment["events"] += 1
                last_at = at
                max_at = at if max_at is None or at > max_at else max_at
                events += 1
            segment["last_batch"] = batch
            batch_id, previous = batch, digest
            due = due if due is not None else last_at.timestamp() + interval
            pending_checkpoint = True
            if until is not None and last_at >= until:
                reached = True
            if time.monotonic()-started >= budget_seconds:
                exhausted = True
            if last_at.timestamp() >= due or reached or exhausted:
                store.write(Checkpoint(batch_id, previous, mirror.last, last_at.isoformat(), max_at.isoformat(),
                                       mirror.damaged, dump_state(mirror.state)), segment)
                written += 1
                pending_checkpoint = False
                segment = _new_segment(batch_id+1)
                due = last_at.timestamp() + interval
            if progress is not None and time.monotonic()-last_report >= 1:
                last_report = time.monotonic()
                progress({"phase": "index", "events": events, "batch_id": batch_id,
                          "received_at": last_at.isoformat() if last_at else None})
            if reached or exhausted:
                break
        if pending_checkpoint and last_at is not None:
            # End of the committed capture: save the tail so the next run starts here.
            store.write(Checkpoint(batch_id, previous, mirror.last, last_at.isoformat(), max_at.isoformat(),
                                   mirror.damaged, dump_state(mirror.state)), segment)
            written += 1
        return {"state": "reached" if reached else "budget_exhausted" if exhausted else "end_of_capture",
                "events": events, "checkpoints_written": written, "batch_id": batch_id,
                "received_at": last_at.isoformat() if last_at else None,
                "seconds": round(time.monotonic()-started, 3)}
    finally:
        store.close()
        capture.close()


def locate(capture_path, owner, contract, start, *, root=None, require_indexed_through=False):
    """Cheap lookup of the checkpoint a replay would start from; LookupError if none."""
    status = mirror_status()
    if not status["available"]:
        raise LookupError(status["reason"])
    capture = Capture(capture_path)
    store = Store(_owner_hash(capture_path), Path(capture_path).stem, root=root)
    try:
        capture.check_scope(owner, contract)
        if not store.matches(capture, status["fingerprint"]):
            raise LookupError("no_matching_checkpoint_store")
        if require_indexed_through:
            latest = store.latest()
            if latest is None or utc(latest.max_received_at) < start:
                # An old checkpoint is valid but may precede a later trade by
                # hours. Extend the index before replaying that trade rather
                # than repeatedly reconstructing the growing unindexed tail.
                raise LookupError("checkpoint_index_behind_entry")
        checkpoint = store.before(start)
        if checkpoint is None:
            raise LookupError("no_checkpoint_before_entry")
        if not capture.anchor_matches(checkpoint.batch_id, checkpoint.batch_hash):
            raise CheckpointError("checkpoint_anchor_mismatch")
        return {"batch_id": checkpoint.batch_id, "received_at": checkpoint.received_at}
    finally:
        store.close()
        capture.close()


def resumed_quotes(capture_path, owner, contract, start, cutoff, *, root=None, report=None, progress=None):
    """Quotes identical to topbot_study_store.quotes(), starting at the best checkpoint.

    ``report`` (a dict) receives the anchor used. Raises LookupError when no
    usable checkpoint exists; callers then use the original full scan.
    """
    status = mirror_status()
    if not status["available"]:
        raise LookupError(status["reason"])
    capture = Capture(capture_path)
    store = Store(_owner_hash(capture_path), Path(capture_path).stem, root=root)
    try:
        capture.check_scope(owner, contract)
        if not store.matches(capture, status["fingerprint"]):
            raise LookupError("no_matching_checkpoint_store")
        checkpoint = store.before(start)
        if checkpoint is None:
            raise LookupError("no_checkpoint_before_entry")
        if not capture.anchor_matches(checkpoint.batch_id, checkpoint.batch_hash):
            raise CheckpointError("checkpoint_anchor_mismatch")
        mirror = Mirror(contract, start, cutoff, state=load_state(checkpoint.state),
                        last=checkpoint.last_index, damaged=checkpoint.damaged)
        store.close()
        if report is not None:
            report.update(reader="checkpoint-v1", checkpoint_batch=checkpoint.batch_id,
                          checkpoint_index=checkpoint.last_index, checkpoint_received_at=checkpoint.received_at)
        events, last_report = 0, time.monotonic()
        for _, _, rows in capture.batches(checkpoint.batch_id, checkpoint.batch_hash, checkpoint.last_index):
            for row in rows:
                stop, quote = mirror.step(row)
                if stop:
                    return
                events += 1
                if report is not None:
                    report["events_read"] = events
                if progress is not None and time.monotonic()-last_report >= 1:
                    last_report = time.monotonic()
                    progress({"phase": "replay", "events": events, "received_at": row["received_at"]})
                if quote is not None:
                    yield quote
    finally:
        store.close()
        capture.close()


def coverage(capture_path, start, end, *, root=None):
    """Recorder continuity over [start, end] from checkpoint segments (conservative at edges)."""
    store = Store(_owner_hash(capture_path), Path(capture_path).stem, root=root)
    try:
        rows = store.segments(start, end)
        latest = store.latest()
    finally:
        store.close()
    if not rows:
        return {"state": "not_indexed"}
    indexed_through = utc(latest.received_at) if latest else None
    holes = [r for r in rows if r["index_holes"] and r["last_hole_at"] and utc(r["last_hole_at"]) >= start
             and utc(r["first_hole_at"]) <= end]
    not_ready = [r for r in rows if r["not_ready_events"] and utc(r["last_not_ready_at"]) >= start
                 and utc(r["first_not_ready_at"]) <= end]
    return {"state": "complete" if indexed_through and indexed_through >= end else "partial",
            "indexed_through": indexed_through.isoformat() if indexed_through else None,
            "events": sum(r["events"] for r in rows),
            "index_holes": sum(r["index_holes"] for r in holes),
            "missing_events": sum(r["missing_events"] for r in holes),
            "max_receipt_gap_seconds": max(r["max_gap_seconds"] for r in rows),
            "not_ready_events": sum(r["not_ready_events"] for r in not_ready),
            "first_problem_at": min([r["first_hole_at"] for r in holes] + [r["first_not_ready_at"] for r in not_ready],
                                   default=None),
            "basis": "Recorder index continuity, receipt gaps and observed-book readiness in 5-minute segments; "
                     "segments overlapping the interval edges are included, so problems near an edge may lie just outside it"}
