"""Idempotent bounded feature backfill, or latest sealed five-minute collection.

Original recordings are read-only. Derived artifacts live beside the raw tree,
inside TOPSIGNAL_DATABENTO_CACHE_DIR, and use the existing verified cache backup.
"""
import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from uuid import uuid4

if __package__:
    from .depth_recording import atomic_json, canonical, inventory as storage_inventory
    from .read_depth_research import read_research, inventory
    from .read_depth_history import safe_path, timestamp
else:
    from depth_recording import atomic_json, canonical, inventory as storage_inventory
    from read_depth_research import read_research, inventory
    from read_depth_history import safe_path, timestamp

DERIVED_BUDGET = 512 * 1024**2
RECORDING_BUDGET = 20 * 1024**3
RESERVE = 10 * 1024**3


def backfill(cache, owner, start, end, *, cancelled=lambda: False):
    cache = Path(cache).resolve()
    raw = cache/'depth-recordings-v1'
    target = cache/'microstructure-derived-v2'/owner
    if not re.fullmatch('[a-f0-9]{64}', owner):
        raise ValueError('Invalid owner scope')
    safe_path(target, cache)
    limits = storage_inventory(raw, max_bytes=RECORDING_BUDGET, reserve_bytes=RESERVE)
    derived_root = cache/'microstructure-derived-v2'
    derived_bytes = 0
    for path in derived_root.rglob('*') if derived_root.exists() else []:
        safe_path(path, cache)
        if path.is_file():
            derived_bytes += path.stat().st_size
    if (limits['blocked_reason'] or derived_bytes+24*1024**2 > DERIVED_BUDGET
            or limits['bytes']+derived_bytes+32*1024**2 > RECORDING_BUDGET):
        raise ValueError('Existing recording/derived storage limit or review boundary reached')
    result = read_research(raw, dict(owner_hash=owner, start=start, end=end, scope='all', replay=False, all_scopes=True), cancelled=cancelled)
    # Keep every compact scope without duplicating raw events. Each requested
    # backfill is bounded; the streaming companion uses the live v2 recorder
    # representation through the reader's all-scope payload.
    identity = result['data_id']
    # Short filenames keep owner-scoped Windows paths below MAX_PATH; the full
    # 256-bit digest remains checked inside the immutable manifest.
    marker = safe_path(target/(identity[:32]+'.manifest.json'), cache)
    data = safe_path(target/(identity[:32]+'.json.gz'), cache)
    if marker.exists():
        previous = json.loads(marker.read_text())
        if previous['data_id'] != identity or data.stat().st_size != previous['bytes'] or hashlib.sha256(data.read_bytes()).hexdigest() != previous['sha256']:
            raise ValueError('Existing backfill checksum mismatch; originals preserved')
        return dict(previous, created=False, incremental_bytes=0)
    encoded = canonical(result)
    if len(encoded) > 24*1024**2:
        raise ValueError('Derived result exceeds 24 MiB; shorten range')
    payload = gzip.compress(encoded, compresslevel=3, mtime=0)
    if shutil.disk_usage(cache).free < RESERVE+len(payload)*2:
        raise ValueError('Free-space reserve reached')
    target.mkdir(parents=True, exist_ok=True)
    temporary = target/(uuid4().hex+'.partial')
    with temporary.open('xb') as stream:
        stream.write(payload);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,data)
    manifest = dict(schema='microstructure-derived-manifest-v2', file=data.name, bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(), data_id=identity, feature_version=result['feature_version'],
        measurement_code_hash=result['measurement_code_hash'], owner_hash=owner, start=start, end=end,
        observations=len(result['points']), raw_events=result['raw_events'], sources=result['sources'],
        skipped_segments=result['skipped_segments'], created_at=datetime.now(timezone.utc).isoformat(),
        snapshot_completion_verified=False, source_archives_preserved=True)
    atomic_json(marker,manifest)
    return dict(manifest, created=True, incremental_bytes=len(payload)+marker.stat().st_size)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-root',type=Path,required=True)
    parser.add_argument('--owner-hash',required=True)
    parser.add_argument('--start')
    parser.add_argument('--end')
    parser.add_argument('--latest',action='store_true',help='Derive the latest fully sealed UTC five-minute interval')
    args=parser.parse_args()
    cache=args.cache_root.resolve()
    status_path=cache/'microstructure-derived-v2'/'collector-status.json'
    try:
        start,end=args.start,args.end
        if args.latest:
            manifests,_=inventory(cache/'depth-recordings-v1',args.owner_hash)
            if not manifests:raise ValueError('No committed recordings')
            latest=max(v[1] for v in manifests)
            end=datetime.fromtimestamp(int(latest.timestamp())//300*300,timezone.utc)
            start=(end-timedelta(minutes=5)).isoformat();end=end.isoformat()
        if not start or not end:raise ValueError('Provide --latest or explicit --start and --end')
        if timestamp(end)-timestamp(start)>timedelta(minutes=10):raise ValueError('At most ten minutes per invocation')
        result=backfill(cache,args.owner_hash,start,end)
        status=dict(state='complete',at=datetime.now(timezone.utc).isoformat(),**result)
        atomic_json(status_path,status)
        print(json.dumps({k:v for k,v in status.items() if k not in {'sources','owner_hash'}}))
    except (ValueError,OSError,KeyError,TypeError) as exc:
        status=dict(state='failed',at=datetime.now(timezone.utc).isoformat(),reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
        atomic_json(status_path,status)
        print(json.dumps(status));raise SystemExit(1)


if __name__=='__main__':main()
