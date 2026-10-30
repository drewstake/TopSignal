"""Consistent SQLite backups plus checksum-verified cache files; never deletes.

Copy the completed output off-device and run --verify there. A second directory
on the production drive is staging, not a backup against device failure.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
from datetime import datetime, timezone


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
            with closing(sqlite3.connect(readonly_sqlite_uri(path), uri=True)) as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('SQLite integrity failure: '+name)
    return manifest


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
            with closing(sqlite3.connect(readonly_sqlite_uri(path), uri=True)) as src, closing(sqlite3.connect(target)) as dst:
                src.backup(dst, pages=256, sleep=.01)
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
