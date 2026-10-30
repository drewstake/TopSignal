"""Optional quote transport: bounded retries, durable loss counters, isolated from critical events."""
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import errno
import json
import shutil
import sqlite3
from time import time

from .market_archive import Archive, canonical, location, pack, stamp, unpack
from .market_archive_spool import event_identity

MAX_EVENT_BYTES = 4096
MAX_PENDING = 4096
MAX_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024 * 1024
MAX_ATTEMPTS = 5
MAX_AGE_SECONDS = 120
# Keep maintenance transactions small enough for the reserved WAL headroom.
MAX_BATCH = 16
WRITE_RESERVE_BYTES = 1024 * 1024
WAL_ALLOWANCE_BYTES = 2 * WRITE_RESERVE_BYTES


def diagnostic(exc):
    """Only numerical OS/SQLite identifiers and fixed categories; never exception text."""
    code = getattr(exc, 'sqlite_errorcode', None)
    code = code if type(code) is int else None
    os_code = getattr(exc, 'errno', None)
    os_code = os_code if type(os_code) is int else None
    base = code & 255 if code is not None else None
    recoverable = base in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or os_code in (errno.EAGAIN, errno.EBUSY, errno.EINTR, errno.ETIMEDOUT)
    category = ('contention' if base in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) else
                'capacity' if base == sqlite3.SQLITE_FULL or os_code == errno.ENOSPC else
                'read_only' if base == sqlite3.SQLITE_READONLY else
                'io' if base == sqlite3.SQLITE_IOERR else
                'transient_os' if recoverable else 'unclassified')
    return dict(type=type(exc).__name__[:64], sqlite_code=code, errno=os_code,
                category=category, recoverable=recoverable)


class QuoteSpool:
    def __init__(self, owner, root=None):
        self.owner, self.root = str(owner), root
        self.path = location(owner, root).with_name('optional-quotes.sqlite3')

    def physical_bytes(self):
        return sum(p.stat().st_size for p in self.path.parent.glob(self.path.name+'*') if p.is_file())

    @contextmanager
    def transaction(self, *, growth=False):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.path.parent).free < 512*1024*1024:
            raise OSError(errno.ENOSPC, 'quote_free_space_floor')
        db = sqlite3.connect(self.path, timeout=.05)
        try:
            # Opening SQLite before refusing growth lets a released reader's WAL
            # recover. TRUNCATE observes the same 50 ms busy timeout. No evidence
            # is deleted, and pinned readers still stop writes at a bounded size.
            limit = MAX_BYTES if growth else MAX_BYTES+WRITE_RESERVE_BYTES
            if self.physical_bytes() >= limit:
                db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            if self.physical_bytes() >= limit:
                raise OSError(errno.ENOSPC, 'quote_physical_capacity')
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA wal_autocheckpoint=128')
            db.execute('PRAGMA journal_size_limit=1048576')
            page_size = db.execute('PRAGMA page_size').fetchone()[0]
            db.execute(f'PRAGMA max_page_count={MAX_BYTES//page_size}')
            if db.execute('PRAGMA user_version').fetchone()[0] not in (0, 1):
                raise ValueError('Unsupported quote spool schema')
            db.executescript('''CREATE TABLE IF NOT EXISTS pending(
                id TEXT PRIMARY KEY, payload BLOB NOT NULL, created REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL);''')
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

    def enqueue(self, event, *, now=None):
        now = time() if now is None else now
        if len(canonical(event).encode()) > MAX_EVENT_BYTES:
            raise ValueError('quote_oversize')
        ident = event_identity(event)
        with self.transaction(growth=True) as db:
            if db.execute('SELECT 1 FROM pending WHERE id=?', (ident,)).fetchone():
                self.count(db, 'duplicate_pending')
                return
            if db.execute('SELECT count(*) FROM pending').fetchone()[0] >= MAX_PENDING:
                raise OSError(errno.ENOSPC, 'quote_spool_capacity')
            db.execute('INSERT INTO pending(id,payload,created) VALUES (?,?,?)', (ident, pack(event), now))
            self.count(db, 'durably_queued')

    def flush(self, *, now=None, archive=None):
        now = time() if now is None else now
        if not self.path.exists():
            return 0
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True, timeout=.05)) as db:
            if db.execute('PRAGMA user_version').fetchone()[0] == 0:
                return 0
            # Larger SQLite pages need a smaller maintenance batch to retain
            # the same reserved write budget on non-default SQLite builds.
            page_size = db.execute('PRAGMA page_size').fetchone()[0]
            batch = min(MAX_BATCH, max(1, 65536//page_size))
            rows = db.execute('SELECT id,payload,created,attempts FROM pending WHERE next_at<=? ORDER BY created,id LIMIT ?', (now, batch)).fetchall()
        if not rows:
            return 0
        expired = [r for r in rows if now-r[2] >= MAX_AGE_SECONDS or r[3] >= MAX_ATTEMPTS]
        active = [r for r in rows if r not in expired]
        recovered = self._resolve_terminal(expired, 'retry_exhausted') if expired else 0
        if not active:
            return recovered
        # Persist the attempt BEFORE the archive call, bounding repeated crashes too.
        with self.transaction() as db:
            db.executemany('UPDATE pending SET attempts=attempts+1,next_at=? WHERE id=?',
                           [(now+min(30, 2**(r[3]+1)), r[0]) for r in active])
            self.count(db, 'retried', sum(r[3] > 0 for r in active))
        try:
            path = location(self.owner, self.root)
            if sum(p.stat().st_size for p in path.parent.glob(path.name+'*') if p.is_file()) >= MAX_ARCHIVE_BYTES:
                raise OSError(errno.ENOSPC, 'optional_archive_capacity')
            inserted = (archive or Archive(self.owner, self.root)).events([unpack(r[1]) for r in active])
        except Exception as exc:
            info = diagnostic(exc)
            terminal_rows = [r for r in active if not info['recoverable'] or r[3]+1 >= MAX_ATTEMPTS]
            with self.transaction() as db:
                self.count(db, 'failed_batches')
                db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', ('last_error', canonical({**info, 'at': stamp(datetime.now(timezone.utc)), 'batch_size': len(active), 'terminal_count': len(terminal_rows)})))
            if terminal_rows:
                # A failing/ambiguous commit can already own these identities.
                attempted = [(r[0], r[1], r[2], r[3]+1) for r in terminal_rows]
                self._resolve_terminal(attempted, 'permanent_failure' if not info['recoverable'] else 'retry_exhausted')
            raise
        self._finish(active, inserted=inserted)
        return len(active)+recovered

    def _archive_presence(self, rows):
        target = Archive(self.owner, self.root)
        if not target.path.exists():
            return set()
        with target.reader() as db:
            return {r[0] for r in db.execute(
                'SELECT id FROM events WHERE id IN ('+','.join('?' for _ in rows)+')', [r[0] for r in rows])}

    def _resolve_terminal(self, rows, reason):
        """Bounded read reconciliation; unavailable evidence is not proof of loss."""
        try:
            present = self._archive_presence(rows)
        except Exception as exc:
            with self.transaction() as db:
                db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', ('last_error', canonical({
                    **diagnostic(exc), 'operation': 'archive_presence', 'at': stamp(datetime.now(timezone.utc)),
                    'batch_size': len(rows), 'uncertain_count': len(rows)})))
            # Terminal transport disposal stays bounded. Preserve uncertainty in
            # durable counters instead of falsely asserting persistence or loss.
            self._finish(rows, uncertain=reason)
            return 0
        found = [r for r in rows if r[0] in present]
        absent = [r for r in rows if r[0] not in present]
        if found:
            self._finish(found, inserted=0)
        if absent:
            self._finish(absent, loss=reason)
        return len(found)

    def _finish(self, rows, *, loss=None, inserted=None, uncertain=None):
        with self.transaction() as db:
            db.executemany('DELETE FROM pending WHERE id=?', [(r[0],) for r in rows])
            if uncertain:
                self.count(db, 'uncertain', len(rows))
                self.count(db, 'uncertain_'+uncertain, len(rows))
            elif loss:
                self.count(db, 'dropped', len(rows))
                self.count(db, 'dropped_'+loss, len(rows))
            else:
                # Includes deduplicated rows known to be in the immutable archive.
                self.count(db, 'persisted', len(rows))
                self.count(db, 'inserted', inserted if isinstance(inserted, int) else 0)
                self.count(db, 'recovered', sum(r[3] > 0 for r in rows))

    def status(self):
        result = dict(backlog=0, max_pending=MAX_PENDING, max_bytes=MAX_BYTES,
                      max_event_bytes=MAX_EVENT_BYTES, max_attempts=MAX_ATTEMPTS,
                      max_age_seconds=MAX_AGE_SECONDS, archive_budget_bytes=MAX_ARCHIVE_BYTES,
                      physical_limit_bytes=MAX_BYTES+WAL_ALLOWANCE_BYTES, maintenance_reserve_bytes=WRITE_RESERVE_BYTES,
                      max_batch=MAX_BATCH)
        if not self.path.exists(): return result
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True, timeout=.05)) as db:
            if db.execute('PRAGMA user_version').fetchone()[0] == 0:
                return result
            for key, value in db.execute('SELECT key,value FROM state'):
                result[key] = json.loads(value) if key == 'last_error' else int(value)
            result['backlog'] = db.execute('SELECT count(*) FROM pending').fetchone()[0]
        result['bytes'] = self.physical_bytes()
        return result
