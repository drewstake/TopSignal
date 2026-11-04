"""Consistent SQLite backups plus checksum-verified cache files; never deletes.

Copy the completed output off-device and run --verify there. A second directory
on the production drive is staging, not a backup against device failure.
"""
import argparse
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
import tempfile
import time
from datetime import datetime, timezone

_BACKGROUND_RESOURCES = False


def _set_windows_priority(value):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.SetPriorityClass.restype = wintypes.BOOL
    if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), value):
        raise RuntimeError('Unable to set backup scheduling priority')


@contextmanager
def _sqlite_priority():
    """Keep bulk work subordinate; avoid starving bounded SQLite operations."""
    global _BACKGROUND_RESOURCES
    if not _BACKGROUND_RESOURCES:
        yield
        return
    _set_windows_priority(0x00200000)  # End background resource mode.
    _BACKGROUND_RESOURCES = False
    try:
        _set_windows_priority(0x00004000)  # CPU stays below normal.
        yield
    finally:
        _set_windows_priority(0x00100000)
        _BACKGROUND_RESOURCES = True


def filesystem_path(path):
    """Use Windows extended paths without changing machine-wide registry policy."""
    path = Path(path).resolve()
    name = str(path)
    if os.name == 'nt' and not name.startswith('\\\\?\\'):
        return Path('\\\\?\\UNC\\'+name[2:] if name.startswith('\\\\') else '\\\\?\\'+name)
    return path


def checksum(path):
    with filesystem_path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def readonly_sqlite_uri(path):
    # SQLite URIs cannot use the Win32 extended-path prefix as an authority.
    name = str(path)
    if os.name == 'nt' and name.startswith('\\\\?\\UNC\\'):
        name = '\\\\'+name[8:]
    elif os.name == 'nt' and name.startswith('\\\\?\\'):
        name = name[4:]
    return Path(name).as_uri()+'?mode=ro'


def verify(root):
    root = filesystem_path(root)
    manifest = json.loads((root/'backup-manifest.json').read_text())
    for name, expected in manifest['files'].items():
        path = (root/name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError('Invalid backup member')
        if checksum(path) != expected['sha256'] or path.stat().st_size != expected['bytes']:
            raise ValueError('Backup checksum mismatch: '+name)
        if expected['sqlite']:
            verify_sqlite(path, expected['sha256'])
    return manifest


def verify_sqlite(path, expected_sha256, *, scratch=None):
    """Check identical snapshot bytes on SSD without random reads on live HDD."""
    path = filesystem_path(path)
    system_temp = filesystem_path(tempfile.gettempdir())
    if scratch is None and path.drive.casefold() != system_temp.drive.casefold():
        scratch = system_temp

    def integrity(candidate):
        with _sqlite_priority(), closing(sqlite3.connect(readonly_sqlite_uri(candidate), uri=True)) as db:
            db.execute('PRAGMA cache_size=-131072')
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('SQLite integrity failure: '+path.name)

    if scratch is None:
        integrity(path)
        return
    scratch = filesystem_path(scratch)
    if shutil.disk_usage(scratch).free < path.stat().st_size + 5_000_000_000:
        raise OSError('SQLite integrity scratch free-space floor')
    with tempfile.TemporaryDirectory(prefix='topsignal-integrity-', dir=scratch) as directory:
        owned = filesystem_path(directory)
        assert owned.is_relative_to(scratch) and owned != scratch
        candidate = owned/'snapshot.sqlite3'
        # This is a closed backup file, never a live database. Copy and hash
        # at background priority; only the SSD integrity scan raises I/O priority.
        shutil.copyfile(path, candidate)
        if checksum(candidate) != expected_sha256:
            raise ValueError('SQLite integrity scratch checksum mismatch')
        integrity(candidate)


def backup_sqlite(source, destination, *, progress=None, timeout_seconds=900):
    with _sqlite_priority():
        return _backup_sqlite(source, destination, progress=progress, timeout_seconds=timeout_seconds)


def _backup_sqlite(source, destination, *, progress=None, timeout_seconds=900):
    """Bound a WAL snapshot so continuous writers cannot restart every step.

    WAL readers leave writers free to commit. For rollback-journal databases,
    retain incremental read locks rather than blocking writers for the copy.
    The deadline bounds snapshot lifetime and resulting WAL retention.
    """
    if source.in_transaction:
        raise ValueError('Backup requires an idle source connection')
    pinned = source.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal'
    started = time.monotonic()
    def step(status, remaining, total):
        if time.monotonic() - started >= timeout_seconds:
            raise TimeoutError('SQLite snapshot backup deadline exceeded')
        if progress is not None:
            progress(status, remaining, total)
    try:
        if pinned:
            source.execute('BEGIN')
            source.execute('SELECT count(*) FROM sqlite_schema').fetchone()
        source.backup(destination, pages=256, sleep=.01, progress=step)
    finally:
        if pinned:
            source.rollback()


def snapshot_sqlite(path, target, *, scratch=None):
    """Use disposable scratch on another drive to avoid HDD read/write seeks.

    Only our newly created scratch directory is removed. Source databases and
    completed/failed backup directories remain untouched. A complete snapshot
    is copied sequentially and checked before scratch is released.
    """
    path, target = filesystem_path(path), filesystem_path(target)
    system_temp = filesystem_path(tempfile.gettempdir())
    if scratch is None and path.drive.casefold() != system_temp.drive.casefold():
        scratch = system_temp
    if scratch is None:
        with closing(sqlite3.connect(readonly_sqlite_uri(path), uri=True)) as src, closing(sqlite3.connect(target)) as dst:
            backup_sqlite(src, dst)
        return
    scratch = filesystem_path(scratch)
    wal = path.with_name(path.name+'-wal')
    estimate = path.stat().st_size + (wal.stat().st_size if wal.exists() else 0)
    if shutil.disk_usage(scratch).free < estimate + 5_000_000_000:
        raise OSError('SQLite snapshot scratch free-space floor')
    # SQLite requests individual pages. Background-priority 4 KiB reads on a
    # busy Windows HDD can starve even when the destination is an SSD. Warm
    # the bounded current file prefix using sequential reads first. These
    # bytes are never used as the backup: the online API supplies consistency.
    for member in (path, wal):
        try:
            remaining = member.stat().st_size
            with member.open('rb') as stream:
                while remaining:
                    block = stream.read(min(8 * 1024 * 1024, remaining))
                    if not block:
                        break
                    remaining -= len(block)
        except FileNotFoundError:
            if member == path:
                raise
    with tempfile.TemporaryDirectory(prefix='topsignal-sqlite-', dir=scratch) as directory:
        owned = filesystem_path(directory)
        assert owned.is_relative_to(scratch) and owned != scratch
        snapshot = owned/'snapshot.sqlite3'
        with closing(sqlite3.connect(readonly_sqlite_uri(path), uri=True)) as src, closing(sqlite3.connect(snapshot)) as dst:
            backup_sqlite(src, dst)
        shutil.copyfile(snapshot, target)
        if checksum(snapshot) != checksum(target):
            raise ValueError('SQLite snapshot transport checksum mismatch')


def backup(source, output):
    source, output = filesystem_path(source), filesystem_path(output)
    if not source.is_dir() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('Backup requires separate source and destination trees')
    output.mkdir(parents=True, exist_ok=False)
    files = {}
    # Immutable depth segment manifests commit their already-fsynced gzip file.
    # Snapshot this list once. Active partials and segments sealed later belong
    # to a subsequent backup; copying an open gzip file would create bad evidence.
    depth_root = source/'depth-recordings-v1'
    depth_files = set()
    for marker in depth_root.rglob('*.segment.json') if depth_root.exists() else []:
        value = json.loads(marker.read_text())
        name = value.get('file', '')
        member = (marker.parent/name).resolve()
        if (value.get('schema') != 'mnq-depth-segment-v1' or Path(name).name != name
                or not name.endswith('.ndjson.gz') or not member.is_relative_to(depth_root.resolve())
                or checksum(member) != value['sha256'] or member.stat().st_size != value['bytes']):
            raise ValueError('Invalid committed depth segment')
        depth_files.update((marker.resolve(), member))
    # A critical event moves spool -> archive -> transport acknowledgement.
    # Snapshot all spools first: archive-first can miss a pre-existing event if
    # its archive commit and spool acknowledgement happen between the copies.
    # Other files are individually consistent, not one global point in time.
    for path in sorted(source.rglob('*'), key=lambda p: (p.name not in {'critical-spool.sqlite3', 'optional-quotes.sqlite3'}, str(p))):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Reparse/symlink source requires explicit handling')
        if not path.is_file():
            continue
        if path.is_relative_to(depth_root) and path.resolve() not in depth_files:
            continue  # Preserve all originals on host; do not copy unfinished segments.
        if path.name.endswith(('-wal', '-shm')) and path.with_name(path.name.rsplit('-', 1)[0]).is_file():
            continue  # Consistent SQLite backup includes committed WAL pages.
        relative = path.relative_to(source)
        # A runtime restored from a verified backup may contain that backup's
        # manifest. Preserve it as evidence without colliding with this one.
        if relative.as_posix() == 'backup-manifest.json':
            relative = Path('source-manifests') / (checksum(path) + '.json')
        target = output/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        is_sqlite = path.suffix in {'.sqlite3', '.sqlite', '.db'}
        if is_sqlite:
            snapshot_sqlite(path, target)
        else:
            shutil.copyfile(path, target)
            if checksum(path) != checksum(target):
                raise ValueError('Source changed during backup; retry into a new directory: '+str(relative))
        files[relative.as_posix()] = dict(sha256=checksum(target), bytes=target.stat().st_size, sqlite=is_sqlite)
    (output/'backup-manifest.json').write_text(json.dumps(dict(created_at=datetime.now(timezone.utc).isoformat(), source=str(source), files=files,
        depth_snapshot=dict(sealed_segments=len(depth_files)//2,active_partials_excluded=True)), indent=2))
    return verify(output)


def pack(root, output):
    """Transport immutable snapshots as one file, avoiding Windows scp MAX_PATH."""
    root, output = filesystem_path(root), filesystem_path(output)
    if output.is_relative_to(root):
        raise ValueError('Transport archive must be outside the snapshot')
    manifest = verify(root)
    with tarfile.open(output, 'x') as archive:
        for name in sorted([*manifest['files'], 'backup-manifest.json']):
            archive.add(root/name, arcname=name, recursive=False)
    return dict(sha256=checksum(output), bytes=output.stat().st_size)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--verify', type=Path)
    parser.add_argument('--pack', type=Path, help='Pack an already verified snapshot into --output')
    args = parser.parse_args()
    if os.name == 'nt':
        # Bulk scans/copies yield I/O to live recording. Bounded SQLite steps
        # temporarily retain normal I/O/cache priority and below-normal CPU;
        # background resource mode starved their small page reads on the HDD.
        global _BACKGROUND_RESOURCES
        _set_windows_priority(0x00100000)
        _BACKGROUND_RESOURCES = True
    if args.pack and args.output:
        print(json.dumps(pack(args.pack,args.output)))
        return
    elif args.verify:
        result = verify(args.verify.resolve())
    elif args.source and args.output:
        result = backup(args.source, args.output)
    else:
        parser.error('Use --verify or both --source and --output')
    print(json.dumps({'verified_files': len(result['files']), 'bytes': sum(f['bytes'] for f in result['files'].values())}))


if __name__ == '__main__':
    main()
