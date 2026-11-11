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

from .probabilistic_shadow import cache_root

FLOOR = 10_000_000_000
PREFIX = "trade_progress_"
SAMPLE_INTERVAL_SECONDS = 1


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
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=1)
    else:
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    try:
        if write:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA wal_autocheckpoint=1000")
            db.executescript(SCHEMA)
        yield db
        if write:
            db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


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


def enroll(owner, account, enabled):
    if not owner or account <= 0:
        raise ValueError("Explicit owner and positive account required")
    if enabled:
        verify_destination()
    with connect(write=True) as db:
        db.execute("INSERT INTO trade_progress_scopes(owner,account,enabled) VALUES(?,?,?) "
                   "ON CONFLICT(owner,account) DO UPDATE SET enabled=excluded.enabled",
                   (owner, account, int(enabled)))


def scopes():
    if not location().exists():
        return []
    with connect() as db:
        return [tuple(row) for row in db.execute("SELECT owner,account FROM trade_progress_scopes WHERE enabled=1")]


def status(owner, account):
    result = dict(enrolled=False, heartbeat=None, error=None, healthy=False,
                  sample_seconds=SAMPLE_INTERVAL_SECONDS, mark_basis="bid_ask_midpoint_else_last", storage="local_sqlite")
    if location().exists():
        with connect() as db:
            row = db.execute("SELECT * FROM trade_progress_scopes WHERE owner=? AND account=?", (owner, account)).fetchone()
        if row:
            result.update(enrolled=bool(row["enabled"]), heartbeat=row["heartbeat"], error=row["error"])
            result["healthy"] = bool(row["enabled"] and row["heartbeat"] and now()-row["heartbeat"] < 30 and not row["error"])
    if result["enrolled"]:
        try:
            report = verify_destination()
            result.update(free_bytes=report["free_bytes"], files=report["files"])
        except (ValueError, OSError, sqlite3.Error):
            result.update(healthy=False, error="local_storage_unavailable_or_unverified")
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
