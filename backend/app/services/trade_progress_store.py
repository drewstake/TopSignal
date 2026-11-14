"""Trade progress has one storage destination: local SQLite beneath the cache.

No ORM models, application DB sessions, outbox writes or cloud fallbacks here.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import threading

from .probabilistic_shadow import cache_root

FLOOR = 10_000_000_000
PREFIX = "trade_progress_"
SAMPLE_INTERVAL_SECONDS = 1
# SQLite has one writer. Coordinate account threads before they open a write
# connection, instead of making every account compete in SQLite's busy handler.
_writer_lock = threading.Lock()


def is_busy(exc):
    return (isinstance(exc, sqlite3.OperationalError)
            and getattr(exc, 'sqlite_errorcode', -1) & 0xff
            in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED))


def now():
    return datetime.now(timezone.utc).timestamp()


def encode(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


def location():
    return cache_root() / "trade-progress-v1" / "progress.sqlite3"


SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_progress_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trade_progress_scopes(
 owner TEXT NOT NULL, account INTEGER NOT NULL, enabled INTEGER NOT NULL,
 heartbeat REAL, error TEXT, PRIMARY KEY(owner,account));
CREATE TABLE IF NOT EXISTS trade_progress_enrollment_preferences(
 owner TEXT NOT NULL, account INTEGER NOT NULL, origin TEXT NOT NULL, changed_at REAL NOT NULL,
 PRIMARY KEY(owner,account));
CREATE TABLE IF NOT EXISTS trade_progress_enrollment_changes(
 id INTEGER PRIMARY KEY, owner TEXT NOT NULL, account INTEGER NOT NULL,
 enabled INTEGER NOT NULL, origin TEXT NOT NULL, changed_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS trade_progress_positions(
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, account INTEGER NOT NULL,
 contract TEXT NOT NULL, opened REAL NOT NULL, ended REAL, state TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS trade_progress_scope ON trade_progress_positions(owner,account,contract,opened);
CREATE TABLE IF NOT EXISTS trade_progress_samples(
 id INTEGER PRIMARY KEY, position TEXT NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS trade_progress_timeline ON trade_progress_samples(position,id);
CREATE TABLE IF NOT EXISTS trade_progress_cursors(
 owner TEXT NOT NULL, account INTEGER NOT NULL, contract TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(owner,account,contract));
CREATE TABLE IF NOT EXISTS trade_progress_fills(
 owner TEXT NOT NULL, account INTEGER NOT NULL, trade TEXT NOT NULL,
 contract TEXT NOT NULL, ts REAL NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(owner,account,trade));
CREATE INDEX IF NOT EXISTS trade_progress_fill_time ON trade_progress_fills(owner,account,contract,ts);
CREATE TABLE IF NOT EXISTS trade_progress_fill_revisions(
 owner TEXT NOT NULL, account INTEGER NOT NULL, trade TEXT NOT NULL, digest TEXT NOT NULL,
 received REAL NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(owner,account,trade,digest));
"""


@contextmanager
def connect(*, write=False):
    path = location()
    acquired = False
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        acquired = _writer_lock.acquire(timeout=1)
        if not acquired:
            exc = sqlite3.OperationalError('Trade progress writer is busy')
            exc.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise exc
    db = None
    try:
        if write:
            db = sqlite3.connect(path, timeout=1)
        else:
            db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
        db.row_factory = sqlite3.Row
        if write:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA wal_autocheckpoint=1000")
            db.executescript(SCHEMA)
        yield db
        if write:
            db.commit()
    except BaseException:
        if db is not None:
            db.rollback()
        raise
    finally:
        if db is not None:
            db.close()
        if acquired:
            _writer_lock.release()


def disk_report():
    root = cache_root()
    existing = root
    while not existing.exists():
        existing = existing.parent
    usage = shutil.disk_usage(existing)
    path = location()
    sizes = {suffix or "database": Path(str(path)+suffix).stat().st_size
             if Path(str(path)+suffix).exists() else 0 for suffix in ("", "-wal", "-shm")}
    return dict(root=str(root), host=platform.node().lower(), drive=root.anchor,
                device=existing.stat().st_dev, total_bytes=usage.total, free_bytes=usage.free,
                minimum_free_bytes=FLOOR, files=sizes)


def verify_destination():
    report = disk_report()
    if not location().resolve().is_relative_to(cache_root()):
        raise ValueError("Recording database escaped the verified cache")
    if not os.getenv("TOPSIGNAL_DATABENTO_CACHE_DIR", "").strip():
        raise ValueError("Explicit TOPSIGNAL_DATABENTO_CACHE_DIR required")
    if not location().exists():
        raise ValueError("Run the local storage verification command before enrollment")
    with connect() as db:
        row = db.execute("SELECT value FROM trade_progress_meta WHERE key='destination'").fetchone()
    verified = json.loads(row[0]) if row else {}
    for key in ("root", "host", "drive", "device", "total_bytes"):
        if verified.get(key) != report[key]:
            raise ValueError("Recording destination differs from the verified drive")
    if report["free_bytes"] < FLOOR:
        raise OSError("Trade progress recording paused: free disk space below 10 GB")
    return report


def verify_and_register(*, expected_host, expected_root, expected_drive):
    """Explicit operator action after inspecting the actual disk inventory."""
    report = disk_report()
    if (not os.getenv("TOPSIGNAL_DATABENTO_CACHE_DIR", "").strip()
            or report["host"] != expected_host.lower()
            or Path(expected_root).resolve() != cache_root()
            or Path(expected_drive).resolve().anchor.casefold() != cache_root().anchor.casefold()
            or report["free_bytes"] < FLOOR):
        raise ValueError("Destination/host/free-space verification failed")
    # Resolve the final store as well, refusing a junction/symlink escape.
    if not location().resolve().is_relative_to(cache_root()):
        raise ValueError("Recording database must stay within the verified cache")
    with connect(write=True) as db:
        db.execute("INSERT OR REPLACE INTO trade_progress_meta VALUES('destination',?)",
                   (encode(report | {"verified_at": now()}),))
    return report


def enroll(owner, account, enabled, *, origin='manual'):
    if not owner or account <= 0 or origin not in {'manual', 'policy'}:
        raise ValueError("Explicit owner and positive account required")
    if enabled:
        verify_destination()
    with connect(write=True) as db:
        db.execute('BEGIN IMMEDIATE')
        _set_enrollment(db, owner, account, enabled, origin)


def _set_enrollment(db, owner, account, enabled, origin):
    previous = db.execute('SELECT s.enabled,p.origin FROM trade_progress_scopes s LEFT JOIN '
        'trade_progress_enrollment_preferences p USING(owner,account) WHERE s.owner=? AND s.account=?',
        (owner,account)).fetchone()
    db.execute("INSERT INTO trade_progress_scopes(owner,account,enabled) VALUES(?,?,?) "
               "ON CONFLICT(owner,account) DO UPDATE SET enabled=excluded.enabled",
               (owner, account, int(enabled)))
    if previous is None or previous['enabled'] != int(enabled) or previous['origin'] != origin:
        at = now()
        db.execute('INSERT OR REPLACE INTO trade_progress_enrollment_preferences VALUES(?,?,?,?)',
                   (owner,account,origin,at))
        db.execute('INSERT INTO trade_progress_enrollment_changes(owner,account,enabled,origin,changed_at) VALUES(?,?,?,?,?)',
                   (owner,account,int(enabled),origin,at))


def reconcile_policy(owner, eligible, capacity):
    """Fresh discovery can manage only its own enrollments; manual opt-outs win."""
    verify_destination()
    with connect(write=True) as db:
        db.execute('BEGIN IMMEDIATE')
        rows = [dict(r) for r in db.execute('SELECT s.account,s.enabled,p.origin FROM trade_progress_scopes s '
            'LEFT JOIN trade_progress_enrollment_preferences p USING(owner,account) WHERE s.owner=?', (owner,))]
        existing = {r['account']: r for r in rows}
        opted_out = {r['account'] for r in rows if not r['enabled'] and r['origin'] != 'policy'}
        manual_enabled = {r['account'] for r in rows if r['enabled'] and r['origin'] != 'policy'}
        desired = set(eligible) - opted_out
        if len(set(eligible)) > capacity or len(manual_enabled | desired) > capacity:
            return False
        for r in rows:
            if r['origin'] == 'policy' and r['enabled'] and r['account'] not in desired:
                _set_enrollment(db, owner, r['account'], False, 'policy')
        for account in sorted(desired):
            if account not in existing or not existing[account]['enabled']:
                _set_enrollment(db, owner, account, True, 'policy')
    return True


def scopes():
    if not location().exists():
        return []
    with connect() as db:
        return [tuple(row) for row in db.execute("SELECT owner,account FROM trade_progress_scopes WHERE enabled=1")]


def diagnostics_key(owner, account):
    return 'diagnostics:' + encode([owner,account])


def status(owner, account):
    result = dict(enrolled=False, heartbeat=None, error=None, healthy=False,
                  open_positions=0, heartbeat_age_seconds=None,
                  sample_seconds=SAMPLE_INTERVAL_SECONDS, mark_basis="bid_ask_midpoint_else_last", storage="local_sqlite")
    if location().exists():
        with connect() as db:
            row = db.execute("SELECT * FROM trade_progress_scopes WHERE owner=? AND account=?", (owner, account)).fetchone()
            result['open_positions'] = db.execute(
                "SELECT count(*) FROM trade_progress_positions WHERE owner=? AND account=? AND ended IS NULL",
                (owner, account)).fetchone()[0]
            diagnostics = db.execute('SELECT value FROM trade_progress_meta WHERE key=?',
                                     (diagnostics_key(owner,account),)).fetchone()
            result['diagnostics'] = json.loads(diagnostics[0]) if diagnostics else None
        if row:
            result.update(enrolled=bool(row["enabled"]), heartbeat=row["heartbeat"], error=row["error"])
            age = now()-row['heartbeat'] if row['heartbeat'] is not None else None
            result['heartbeat_age_seconds'] = age
            result["healthy"] = bool(row["enabled"] and age is not None and 0 <= age < 30 and not row["error"])
    if result["enrolled"]:
        try:
            report = verify_destination()
            result.update(free_bytes=report["free_bytes"], files=report["files"])
        except (ValueError, OSError, sqlite3.Error):
            result.update(healthy=False, error="local_storage_unavailable_or_unverified")
    result['recording_state'] = ('not_enrolled' if not result['enrolled'] else
        'heartbeat_stale' if result['heartbeat_age_seconds'] is not None and
            not 0 <= result['heartbeat_age_seconds'] < 30 else
        'enrolled_unhealthy' if not result['healthy'] else
        'recording_flat' if result['open_positions'] == 0 else 'recording')
    return result


def candidates(owner, account, contract, start, end):
    if not location().exists():
        return []
    with connect() as db:
        rows = db.execute("SELECT state FROM trade_progress_positions WHERE owner=? AND account=? AND contract=? "
                          "AND opened<=? AND (ended IS NULL OR ended>=?) ORDER BY opened LIMIT 101",
                          (owner, account, contract, end+1, start-1)).fetchall()
    return [json.loads(row[0]) for row in rows]


def timeline(owner, account, position, *, after=0, limit=5000):
    with connect() as db:
        row = db.execute("SELECT state FROM trade_progress_positions WHERE id=? AND owner=? AND account=?",
                         (position, owner, account)).fetchone()
        if row is None:
            return None
        state = json.loads(row[0])
        samples = [dict(id=r["id"], **json.loads(r["payload"])) for r in db.execute(
            "SELECT id,payload FROM trade_progress_samples WHERE position=? AND id>? ORDER BY id LIMIT ?",
            (position, after, limit+1))]
        fills = [json.loads(r[0]) for r in db.execute(
            "SELECT payload FROM trade_progress_fills WHERE owner=? AND account=? AND contract=? AND ts>=? AND ts<=? ORDER BY ts LIMIT 1001",
            (owner, account, state["contract"], state["opened"], state["ended"] or now()))]
    return dict(position=state, samples=samples[:limit], next_cursor=samples[limit-1]["id"] if len(samples)>limit else None,
                fills=fills[:1000], fills_truncated=len(fills)>1000,
                source="live_observation", coverage="partial" if state["reasons"] else "observed_interval")
