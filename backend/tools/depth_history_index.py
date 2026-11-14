"""Disposable, bounded local indexes for verified liquidity history.

Only derived data is evicted. Source recordings and commit receipts are never
changed. The reader revalidates selected receipts and file identities before
using parsed points; misses still verify both source checksums.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time
import zlib

MAX_BYTES = 64 * 1024**2
MAX_PAYLOAD = 4 * 1024**2
MAX_MARKERS = 50000


def signature(path):
    info = path.stat()
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


class HistoryIndex:
    def __init__(self, path, root):
        self.path, self.root = Path(path), Path(root)
        self.db = None

    def __enter__(self):
        for part in (self.path, *self.path.parents):
            if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
                raise OSError('History cache reparse point')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.path.parent).free < 10 * 1024**3:
            raise OSError('History cache free-space reserve')
        self.db = sqlite3.connect(self.path, timeout=.1)
        page_size = self.db.execute('PRAGMA page_size').fetchone()[0]
        self.db.execute(f'PRAGMA max_page_count={MAX_BYTES // page_size}')
        self.db.execute('PRAGMA cache_size=-2048')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS runs(root TEXT,run TEXT,signature TEXT,
            PRIMARY KEY(root,run));
          CREATE TABLE IF NOT EXISTS markers(root TEXT,path TEXT,run TEXT,owner TEXT,
            first TEXT,last TEXT,manifest TEXT,PRIMARY KEY(root,path));
          CREATE INDEX IF NOT EXISTS marker_range ON markers(root,owner,last,first);
          CREATE TABLE IF NOT EXISTS points(root TEXT,path TEXT,scope TEXT,identity TEXT,
            payload BLOB,digest TEXT,used REAL,PRIMARY KEY(root,path,scope));
        ''')
        return self

    def __exit__(self, *args):
        if self.db:
            self.db.close()

    def run_current(self, run, current_signature=None):
        row = self.db.execute('SELECT signature FROM runs WHERE root=? AND run=?',
                              (str(self.root), str(run))).fetchone()
        return row is not None and row[0] == json.dumps(current_signature or signature(run))

    def known(self, run):
        return {p: json.loads(m) for p, m in self.db.execute(
            'SELECT path,manifest FROM markers WHERE root=? AND run=?', (str(self.root), str(run)))}

    def save_partial(self, run, manifests):
        with self.db:
            self.db.executemany('''INSERT INTO markers VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(root,path) DO UPDATE SET owner=excluded.owner,first=excluded.first,
                    last=excluded.last,manifest=excluded.manifest WHERE manifest!=excluded.manifest''',
                [(str(self.root), str(p), str(run), m.get('owner_hash'), m.get('first_received_at'),
                  m.get('last_received_at'), json.dumps(m)) for p, m in manifests])
            if self.db.execute('SELECT count(*) FROM markers').fetchone()[0] > MAX_MARKERS:
                raise OverflowError('History index capacity reached')

    def invalidate_run(self, run):
        with self.db:
            self.db.execute('DELETE FROM runs WHERE root=? AND run=?', (str(self.root), str(run)))
            self.db.execute('DELETE FROM markers WHERE root=? AND run=?', (str(self.root), str(run)))

    def save_run(self, run, before, manifests):
        # Directory changes during enumeration leave the run uncached for retry.
        self.save_partial(run, manifests)
        after = signature(run)
        if before != after:
            return
        present = {str(p) for p, _ in manifests}
        with self.db:
            for path in self.known(run):
                if path not in present:
                    self.db.execute('DELETE FROM markers WHERE root=? AND path=?', (str(self.root), path))
            self.db.execute('INSERT OR REPLACE INTO runs VALUES(?,?,?)',
                (str(self.root), str(run), json.dumps(after)))

    def manifests(self, owner):
        return [(Path(p), json.loads(m)) for p, m in self.db.execute(
            'SELECT path,manifest FROM markers WHERE root=? AND owner=? ORDER BY first,path',
            (str(self.root), owner))]

    def remove_missing_runs(self, present):
        rows = self.db.execute('SELECT run FROM runs WHERE root=?', (str(self.root),)).fetchall()
        with self.db:
            for (run,) in rows:
                if run in present:
                    continue
                self.db.execute('DELETE FROM points WHERE root=? AND path IN '
                    '(SELECT path FROM markers WHERE root=? AND run=?)', (str(self.root), str(self.root), run))
                self.db.execute('DELETE FROM markers WHERE root=? AND run=?', (str(self.root), run))
                self.db.execute('DELETE FROM runs WHERE root=? AND run=?', (str(self.root), run))

    def load_points(self, marker, scope, identity):
        row = self.db.execute('SELECT identity,payload,digest FROM points WHERE root=? AND path=? AND scope=?',
                              (str(self.root), str(marker), scope)).fetchone()
        if row is None or row[0] != identity:
            return None
        try:
            raw = zlib.decompressobj().decompress(row[1], MAX_PAYLOAD+1)
            if len(raw) > MAX_PAYLOAD or hashlib.sha256(raw).hexdigest() != row[2]:
                return None
            value = json.loads(raw)
            if not isinstance(value, list):
                return None
            return value
        except (zlib.error, ValueError):
            return None

    def save_points(self, marker, scope, identity, points):
        raw = json.dumps(points, separators=(',', ':'), allow_nan=False).encode()
        if len(raw) > MAX_PAYLOAD:
            return
        payload = zlib.compress(raw, 1)
        # Leave ample room for indexes, manifests and SQLite transaction pages.
        with self.db:
            size = self.db.execute('SELECT coalesce(sum(length(payload)),0) FROM points').fetchone()[0]
            while size + len(payload) > MAX_BYTES // 2:
                oldest = self.db.execute('SELECT rowid,length(payload) FROM points ORDER BY used LIMIT 1').fetchone()
                if not oldest:
                    break
                self.db.execute('DELETE FROM points WHERE rowid=?', (oldest[0],))
                size -= oldest[1]
            self.db.execute('INSERT OR REPLACE INTO points VALUES(?,?,?,?,?,?,?)',
                (str(self.root), str(marker), scope, identity, payload, hashlib.sha256(raw).hexdigest(), time.time()))


@contextmanager
def optional_index(path, root):
    if path is None:
        yield None
        return
    index = HistoryIndex(path, root)
    try:
        index.__enter__()
    except (OSError, sqlite3.Error):
        index.__exit__()
        yield None
        return
    try:
        yield index
    finally:
        index.__exit__()
