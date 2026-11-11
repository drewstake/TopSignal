"""Durable, bounded replay-job state for the exit study (local SQLite only).

Results stay in the append-only study store. This file records why a replay is
still pending: how many attempts ran, how far each got, when it will retry and
a sanitized failure description. It survives restarts, so a backlog is never
silently reset to "pending" with its failure history discarded.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
import shutil
import sqlite3

MAX_FILE_BYTES = 16 * 1024**2
FREE_FLOOR = 10 * 1024**3
MAX_DETAIL_BYTES = 2048
ATTEMPTS_PER_ENTRY = 12
FAILED_AFTER = 6
SAFE_TEXT = re.compile(r"^[A-Za-z0-9_.:\- ]{1,96}$")


def location(owner):
    from .databento_cache import default_cache_root
    return default_cache_root()/"topbot-exit-study-v1"/sha256(str(owner).encode()).hexdigest()/"replay-jobs.sqlite3"


@contextmanager
def connection(owner, *, write=False):
    path = location(owner)
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        used = sum(p.stat().st_size for p in path.parent.glob("replay-jobs.sqlite3*") if p.is_file())
        if shutil.disk_usage(path.parent).free < FREE_FLOOR or used > MAX_FILE_BYTES:
            raise ValueError("replay_job_storage_review_required")
        db = sqlite3.connect(path, timeout=2)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.executescript("""CREATE TABLE IF NOT EXISTS jobs(account INTEGER NOT NULL, entry INTEGER NOT NULL,
            experiment TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
            consecutive_failures INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT, first_attempt_at TEXT,
            last_attempt_at TEXT, last_outcome TEXT, last_failure TEXT, first_failure TEXT, last_progress TEXT,
            PRIMARY KEY(account, entry, experiment));
          CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, account INTEGER NOT NULL, entry INTEGER NOT NULL,
            experiment TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT NOT NULL, outcome TEXT NOT NULL,
            detail TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS attempt_scope ON attempts(account, entry, experiment, id);""")
    else:
        if not path.exists():
            yield None
            return
        db = sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=2)
    try:
        yield db
        if write:
            db.commit()
    finally:
        db.close()


def sanitize(detail):
    """Keep only fixed keys, numbers and short identifier-like strings; never paths or messages."""
    def clean(value, depth=0):
        if isinstance(value, bool) or value is None or isinstance(value, int):
            return value
        if isinstance(value, float):
            return round(value, 3) if value == value and abs(value) != float("inf") else None
        if isinstance(value, str):
            return value if SAFE_TEXT.match(value) and "\\" not in value and "/" not in value else "redacted"
        if isinstance(value, (list, tuple)) and depth < 3:
            return [clean(v, depth+1) for v in list(value)[:8]]
        if isinstance(value, dict) and depth < 3:
            return {str(k)[:40]: clean(v, depth+1) for k, v in list(value.items())[:16] if SAFE_TEXT.match(str(k))}
        return "redacted"
    raw = json.dumps(clean(detail or {}), sort_keys=True, separators=(",", ":"))
    if len(raw) > MAX_DETAIL_BYTES:
        raw = json.dumps({"truncated": True, "category": (detail or {}).get("category", "unclassified")})
    return raw


def record(owner, account, entry, experiment, *, outcome, started_at, detail, next_attempt_at=None,
           failure=False, progress=None):
    """Append one attempt (bounded per entry) and update the entry's job summary."""
    now = datetime.now(timezone.utc).isoformat()
    text = sanitize(detail)
    with connection(owner, write=True) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT OR IGNORE INTO jobs(account,entry,experiment,first_attempt_at) VALUES(?,?,?,?)",
                   (account, entry, experiment, started_at))
        db.execute("""UPDATE jobs SET attempts=attempts+1, last_attempt_at=?, last_outcome=?, next_attempt_at=?,
                      failures=failures+?, consecutive_failures=CASE WHEN ? THEN consecutive_failures+1 ELSE 0 END,
                      last_failure=CASE WHEN ? THEN ? ELSE last_failure END,
                      first_failure=CASE WHEN ? AND first_failure IS NULL THEN ? ELSE first_failure END,
                      last_progress=coalesce(?, last_progress)
                      WHERE account=? AND entry=? AND experiment=?""",
                   (now, outcome, next_attempt_at, int(failure), failure, failure, text, failure, text,
                    sanitize(progress) if progress else None, account, entry, experiment))
        db.execute("INSERT INTO attempts(account,entry,experiment,started_at,ended_at,outcome,detail) VALUES(?,?,?,?,?,?,?)",
                   (account, entry, experiment, started_at, now, outcome, text))
        # Keep the first attempt and the most recent ones; the job row keeps the first failure.
        db.execute("""DELETE FROM attempts WHERE account=? AND entry=? AND experiment=? AND id NOT IN (
                        SELECT id FROM attempts WHERE account=? AND entry=? AND experiment=? ORDER BY id DESC LIMIT ?)
                      AND id != (SELECT min(id) FROM attempts WHERE account=? AND entry=? AND experiment=?)""",
                   (account, entry, experiment, account, entry, experiment, ATTEMPTS_PER_ENTRY-1,
                    account, entry, experiment))


def jobs(owner, account=None):
    with connection(owner) as db:
        if db is None:
            return {}
        query = "SELECT account,entry,experiment,attempts,failures,consecutive_failures,next_attempt_at,first_attempt_at," \
                "last_attempt_at,last_outcome,last_failure,first_failure,last_progress FROM jobs"
        rows = db.execute(query + (" WHERE account=?" if account is not None else ""),
                          (() if account is None else (account,))).fetchall()
    out = {}
    for row in rows:
        out[(row[0], row[1], row[2])] = {
            "attempts": row[3], "failures": row[4], "consecutive_failures": row[5], "next_attempt_at": row[6],
            "first_attempt_at": row[7], "last_attempt_at": row[8], "last_outcome": row[9],
            "last_failure": json.loads(row[10]) if row[10] else None,
            "first_failure": json.loads(row[11]) if row[11] else None,
            "last_progress": json.loads(row[12]) if row[12] else None}
    return out


def history(owner, account, entry, experiment, limit=ATTEMPTS_PER_ENTRY):
    with connection(owner) as db:
        if db is None:
            return []
        rows = db.execute("SELECT started_at,ended_at,outcome,detail FROM attempts WHERE account=? AND entry=? AND experiment=? "
                          "ORDER BY id DESC LIMIT ?", (account, entry, experiment, limit)).fetchall()
    return [{"started_at": r[0], "ended_at": r[1], "outcome": r[2], "detail": json.loads(r[3])} for r in rows]
