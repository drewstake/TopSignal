"""Bounded local WAL/FULL transport spool for critical broker observations.

An observation is durable only after enqueue commits. Archive commit precedes
acknowledgement; a crash between them replays the same identity safely. Consumed
transport rows may be removed only after the immutable archive owns the evidence.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import shutil
import sqlite3
from time import time

from .market_archive import Archive, canonical, digest, location, pack, stamp, unpack

MAX_BYTES = 256 * 1024 * 1024
MAX_PENDING = 100_000
MAX_EVENT_BYTES = 64 * 1024


def event_identity(event):
    return digest({k: v for k, v in event.items() if k not in {'received_at', 'event_id', 'capture'}})


class Spool:
    def __init__(self, owner, root=None):
        self.owner, self.root = str(owner), root
        self.path = location(owner, root).with_name('critical-spool.sqlite3')

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.path.parent).free < 512 * 1024 * 1024:
            raise OSError('spool_free_space_floor')
        db = sqlite3.connect(self.path, timeout=.05)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            if db.execute('PRAGMA user_version').fetchone()[0] not in (0, 1):
                raise ValueError('Unsupported critical spool schema')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS pending(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,payload BLOB NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_at REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            ''')
            db.execute('BEGIN IMMEDIATE')
            db.execute('PRAGMA user_version=1')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def count(db, key, amount=1):
        db.execute('INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+?', (key, str(amount), amount))

    def enqueue(self, event):
        if len(canonical(event).encode()) > MAX_EVENT_BYTES:
            raise ValueError('critical_event_oversize')
        identity = event_identity(event)
        with self.transaction() as db:
            if db.execute('SELECT 1 FROM pending WHERE id=?', (identity,)).fetchone():
                self.count(db, 'duplicate_pending')
                return identity
            size = (db.execute('PRAGMA page_count').fetchone()[0] - db.execute('PRAGMA freelist_count').fetchone()[0]) * db.execute('PRAGMA page_size').fetchone()[0]
            if size >= MAX_BYTES or db.execute('SELECT count(*) FROM pending').fetchone()[0] >= MAX_PENDING:
                raise OSError('critical_spool_capacity')
            event = {**event, 'event_id': identity, 'capture': {**event.get('capture', {}),
                     'durable_at': stamp(datetime.now(timezone.utc)), 'schema': 1}}
            cursor = db.execute('INSERT INTO pending(id,payload) VALUES (?,?)', (identity, pack(event)))
            event['capture']['spool_sequence'] = cursor.lastrowid
            db.execute('UPDATE pending SET payload=? WHERE seq=?', (pack(event), cursor.lastrowid))
            self.count(db, 'durably_queued')
        return identity

    def flush(self, *, now=None, archive=None):
        now = time() if now is None else now
        # Preserve receipt order: a failed head blocks later critical observations.
        if not self.path.exists():
            return 0
        db = sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True, timeout=.05)
        try:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version == 0:
                return 0  # First enqueue has not committed initialization yet.
            if version != 1:
                raise ValueError('Unsupported critical spool schema')
            rows = db.execute('SELECT seq,payload,attempts,next_at FROM pending ORDER BY seq LIMIT 128').fetchall()
        finally:
            db.close()
        if not rows or rows[0][3] > now:
            return 0
        events = [unpack(r[1]) for r in rows]
        try:
            (archive or Archive(self.owner, self.root)).events(events)
        except Exception as exc:
            with self.transaction() as db:
                attempt = rows[0][2]+1
                db.executemany('UPDATE pending SET attempts=attempts+1,next_at=? WHERE seq=?',
                               [(now+min(300, 2**min(attempt, 9)), r[0]) for r in rows])
                self.count(db, 'archive_retry_batches')
                db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', ('last_error', canonical(dict(at=stamp(datetime.now(timezone.utc)), type=type(exc).__name__))))
            raise
        with self.transaction() as db:
            db.executemany('DELETE FROM pending WHERE seq=?', [(r[0],) for r in rows])
            self.count(db, 'archived', len(rows))
            self.count(db, 'recovered_after_retry', sum(r[2] > 0 for r in rows))
            db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', ('last_archive_success', stamp(datetime.now(timezone.utc))))
        return len(rows)

    def status(self):
        result = dict(backlog=0, bytes=0, max_bytes=MAX_BYTES, max_pending=MAX_PENDING,
                      durability='FULL spool commit; memory fallback can be lost on crash')
        if not self.path.exists():
            return result
        db = sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True, timeout=.05)
        try:
            result.update(dict(db.execute('SELECT key,value FROM state')))
            result.update(backlog=db.execute('SELECT count(*) FROM pending').fetchone()[0],
                          retry_records=db.execute('SELECT count(*) FROM pending WHERE attempts>0').fetchone()[0])
        finally:
            db.close()
        result['bytes'] = sum(p.stat().st_size for p in self.path.parent.glob(self.path.name+'*') if p.is_file())
        return result
