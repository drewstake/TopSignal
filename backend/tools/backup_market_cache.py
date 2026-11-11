"""Consistent SQLite backups plus checksum-verified cache files; never deletes.

Copy the completed output off-device and run --verify there. A second directory
on the production drive is staging, not a backup against device failure.

With --previous, a file that is unchanged since that snapshot is listed in the
manifest with its recorded hash and marked "reused" instead of being read and
copied again. The off-device side restores it from its own verified copy and
checks every file against the manifest, so the result is still a complete,
fully verified backup. A reused file is NOT re-read on this host; this is a
backup, not a scrub of the source disk.
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
SQLITE_SUFFIXES = {'.sqlite3', '.sqlite', '.db'}


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


def verify(root, *, allow_reused=False, deep=True):
    """Check every member. Reused members may be absent only in host staging.

    deep=False checks presence and size only. It is for host staging whose
    members were hashed as they were written and will be fully verified by
    the receiver; it is never a substitute for the receiver's check.
    """
    root = filesystem_path(root)
    manifest = json.loads((root/'backup-manifest.json').read_text())
    for name, expected in manifest['files'].items():
        path = (root/name).resolve()
        if allow_reused and expected.get('reused') and not path.exists():
            continue
        if not deep:
            if not path.is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_size != expected['bytes']:
                raise ValueError('Invalid backup member: '+name)
            continue
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


def snapshot_deadline(size_bytes):
    """Fifteen minutes plus one second per MiB of database and WAL.

    A fixed limit failed every large capture on the recording disk. The bound
    still exists, so a snapshot cannot pin a WAL indefinitely.
    """
    return 900 + max(0, int(size_bytes)) // (1024 * 1024)


def source_state(path):
    """Cheap identity of a source file: sizes, modification times, SQLite headers.

    Capture databases commit with synchronous=FULL, which flushes file metadata,
    so an identical state on a later day means no commit happened in between.
    """
    path = filesystem_path(path)
    info = path.stat()
    state = [info.st_size, info.st_mtime_ns]
    wal = path.with_name(path.name+'-wal')
    if path.suffix in SQLITE_SUFFIXES:
        try:
            wal_info = wal.stat()
            with wal.open('rb') as stream:
                header = stream.read(32)
            state += [wal_info.st_size, wal_info.st_mtime_ns, hashlib.sha256(header).hexdigest()]
        except FileNotFoundError:
            state += [None, None, None]
    return state


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
            backup_sqlite(src, dst, timeout_seconds=snapshot_deadline(path.stat().st_size))
        return checksum(target)
    scratch = filesystem_path(scratch)
    wal = path.with_name(path.name+'-wal')
    estimate = path.stat().st_size + (wal.stat().st_size if wal.exists() else 0)
    if shutil.disk_usage(scratch).free < estimate + 5_000_000_000:
        raise OSError('SQLite snapshot scratch free-space floor')
    # SQLite requests individual pages. Background-priority 4 KiB reads on a
    # busy Windows HDD can starve even when the destination is an SSD. Warm
    # the bounded current file prefix using sequential reads first. These
    # bytes are never used as the backup: the online API supplies consistency.
    # The warm-up itself must not run in background resource mode: with live
    # recorders on the same disk a multi-gigabyte WAL then takes hours to read.
    with _sqlite_priority():
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
            backup_sqlite(src, dst, timeout_seconds=snapshot_deadline(estimate))
        shutil.copyfile(snapshot, target)
        digest = checksum(target)
        if checksum(snapshot) != digest:
            raise ValueError('SQLite snapshot transport checksum mismatch')
        return digest


def previous_files(previous):
    """Members of an earlier complete snapshot; nothing is reused without one."""
    if previous is None:
        return {}, None
    manifest_path = filesystem_path(previous)/'backup-manifest.json'
    if not manifest_path.is_file():
        return {}, None
    manifest = json.loads(manifest_path.read_text())
    return manifest['files'], Path(previous).name


def backup(source, output, *, previous=None, deep_verify=True):
    source, output = filesystem_path(source), filesystem_path(output)
    if not source.is_dir() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('Backup requires separate source and destination trees')
    earlier, earlier_name = previous_files(previous)
    output.mkdir(parents=True, exist_ok=False)
    files = {}
    reused = 0
    sealed = {}
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
                or member.stat().st_size != value['bytes']):
            raise ValueError('Invalid committed depth segment')
        # A sealed segment never changes. If the earlier snapshot holds the same
        # manifest and the same segment hash, both are already off-device.
        marker_name = marker.resolve().relative_to(source.resolve()).as_posix()
        member_name = member.relative_to(source.resolve()).as_posix()
        marker_bytes = marker.read_bytes()
        known_marker, known_member = earlier.get(marker_name), earlier.get(member_name)
        if (known_marker and known_member and known_member['sha256'] == value['sha256']
                and known_member['bytes'] == value['bytes'] and known_marker['bytes'] == len(marker_bytes)
                and known_marker['sha256'] == hashlib.sha256(marker_bytes).hexdigest()):
            for known_name, known in ((marker_name, known_marker), (member_name, known_member)):
                files[known_name] = dict(sha256=known['sha256'], bytes=known['bytes'], sqlite=False, reused=True)
            reused += 2
            depth_files.update((marker.resolve(), member))
            continue
        # A new segment is checked against its manifest hash as it is copied
        # below, reading it from the recording disk once instead of three times.
        sealed[member] = value['sha256']
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
        if path.name == 'progress.lock' and path.parent.name == 'trade-progress-v1':
            # Runtime coordination only, not recording data. Windows blocks
            # reads while the manager holds this lock; restore creates a new one.
            continue
        if path.is_relative_to(depth_root) and path.resolve() not in depth_files:
            continue  # Preserve all originals on host; do not copy unfinished segments.
        if path.is_relative_to(source/'depth-heatmap-v1'):
            # Derived display cache, rebuilt from the backed-up raw segments.
            # Its live SQLite files would otherwise be re-copied in every backup.
            continue
        if path.name.endswith(('-wal', '-shm')) and path.with_name(path.name.rsplit('-', 1)[0]).is_file():
            continue  # Consistent SQLite backup includes committed WAL pages.
        relative = path.relative_to(source)
        # A runtime restored from a verified backup may contain that backup's
        # manifest. Preserve it as evidence without colliding with this one.
        if relative.as_posix() == 'backup-manifest.json':
            relative = Path('source-manifests') / (checksum(path) + '.json')
        if relative.as_posix() in files:
            continue  # A sealed depth segment already recorded as reused above.
        is_sqlite = path.suffix in SQLITE_SUFFIXES
        state = source_state(path)
        known = earlier.get(relative.as_posix())
        # Unchanged since the earlier snapshot: ordinary files by size and
        # modification time, and closed worker captures by their full state.
        # Other SQLite files are live stores and are always snapshotted again.
        if (known and known.get('source_state') == state and bool(known.get('sqlite')) == is_sqlite
                and (not is_sqlite or relative.parts[0] == 'topbot-worker-evidence-v1')):
            files[relative.as_posix()] = dict(sha256=known['sha256'], bytes=known['bytes'], sqlite=is_sqlite,
                                              source_state=state, reused=True)
            reused += 1
            continue
        target = output/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if is_sqlite:
            digest = snapshot_sqlite(path, target)
        else:
            shutil.copyfile(path, target)
            digest = checksum(target)
            expected = sealed.get(path.resolve())
            if expected is not None:
                if digest != expected:
                    raise ValueError('Invalid committed depth segment')
            elif checksum(path) != digest:
                raise ValueError('Source changed during backup; retry into a new directory: '+str(relative))
        entry = dict(sha256=digest, bytes=target.stat().st_size, sqlite=is_sqlite)
        # Record the state only if the file did not change while it was copied.
        if source_state(path) == state:
            entry['source_state'] = state
        files[relative.as_posix()] = entry
    (output/'backup-manifest.json').write_text(json.dumps(dict(created_at=datetime.now(timezone.utc).isoformat(), source=str(source), files=files,
        depth_snapshot=dict(sealed_segments=len(depth_files)//2,active_partials_excluded=True),
        incremental=dict(previous=earlier_name, reused_files=reused, copied_files=len(files)-reused,
                         note='Reused members are restored and verified off-device from the previous backup.')), indent=2))
    return verify(output, allow_reused=True, deep=deep_verify)


def materialize(root, previous):
    """Complete a received snapshot from the previous verified backup.

    Each reused member is hard-linked (or copied) from that backup. Nothing is
    trusted here: verify() afterwards checks every file against the manifest.
    """
    root = filesystem_path(root)
    manifest = json.loads((root/'backup-manifest.json').read_text())
    missing = [name for name, expected in manifest['files'].items()
               if expected.get('reused') and not (root/name).exists()]
    if missing and previous is None:
        raise ValueError('Incremental snapshot needs the previous verified backup')
    previous = filesystem_path(previous) if previous is not None else None
    linked = copied = 0
    for name in missing:
        source = (previous/name).resolve()
        target = (root/name).resolve()
        if (not source.is_relative_to(previous.resolve()) or not target.is_relative_to(root.resolve())
                or not source.is_file()):
            raise ValueError('Previous backup lacks a reused member: '+name)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, target)
            linked += 1
        except OSError:
            shutil.copyfile(source, target)
            copied += 1
    return dict(reused=len(missing), linked=linked, copied=copied)


def pack(root, output):
    """Transport immutable snapshots as one file, avoiding Windows scp MAX_PATH."""
    root, output = filesystem_path(root), filesystem_path(output)
    if output.is_relative_to(root):
        raise ValueError('Transport archive must be outside the snapshot')
    # backup() verified this tree when it was written. Verifying again here read
    # every byte a second time on the recording disk; the receiver verifies all.
    manifest = json.loads((root/'backup-manifest.json').read_text())
    members = ['backup-manifest.json']
    for name, expected in sorted(manifest['files'].items()):
        path = root/name
        if expected.get('reused') and not path.exists():
            continue
        if not path.is_file() or path.stat().st_size != expected['bytes']:
            raise ValueError('Snapshot member missing or resized: '+name)
        members.append(name)
    with tarfile.open(output, 'x') as archive:
        for name in members:
            archive.add(root/name, arcname=name, recursive=False)
    return dict(sha256=checksum(output), bytes=output.stat().st_size, members=len(members)-1,
                reused=len(manifest['files'])-(len(members)-1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--verify', type=Path)
    parser.add_argument('--pack', type=Path, help='Pack an already verified snapshot into --output')
    parser.add_argument('--previous', type=Path, help='Earlier complete snapshot whose unchanged members are reused')
    parser.add_argument('--receiver-verifies', action='store_true',
                        help='Skip the second full read of staging; the receiver must verify every member')
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
        try:
            result = backup(args.source, args.output, previous=args.previous, deep_verify=not args.receiver_verifies)
        except Exception as exc:
            # The caller only sees an exit code. Leave the reason beside the
            # incomplete snapshot; our own messages name files, never secrets.
            try:
                receipt = filesystem_path(args.output).with_name(Path(args.output).name+'.failure.json')
                receipt.write_text(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(), type=type(exc).__name__,
                    message=str(exc)[:300] if isinstance(exc, (ValueError, TimeoutError)) else None)))
            except OSError:
                pass
            raise
    else:
        parser.error('Use --verify or both --source and --output')
    print(json.dumps({'verified_files': len(result['files']), 'bytes': sum(f['bytes'] for f in result['files'].values()),
                      'reused_files': sum(bool(f.get('reused')) for f in result['files'].values())}))


if __name__ == '__main__':
    main()
