"""Bounded, owner-scoped reader for committed order-book summary segments.

Standalone stdlib entry point also used over the private host SSH connection.
No database, credentials, recording changes, or trading process dependency.
"""
import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sys

MAX_POINTS = 20000
MAX_RAW_BYTES = 64 * 1024**2
MAX_FILES = 50000
SCOPES = {'all', '10', '20', '50', '100'}


def timestamp(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Times must include a timezone.')
    return result.astimezone(timezone.utc)


def safe_path(path, root):
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Recording path escapes storage root.')
    for part in (path, *path.parents):
        if part == root.parent:
            break
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('Recording reparse points are unsupported.')
    return path


def verified_rows(marker, manifest, root):
    name = manifest['file']
    if not re.fullmatch(r'\d{8}\.ndjson\.gz', name):
        raise ValueError('Invalid segment filename.')
    path = safe_path(marker.parent / name, root)
    if not 0 < manifest['bytes'] <= 17 * 1024**2 or path.stat().st_size != manifest['bytes']:
        raise ValueError('Invalid segment size.')
    compressed = path.read_bytes()
    if hashlib.sha256(compressed).hexdigest() != manifest['sha256']:
        raise ValueError('Segment checksum mismatch.')
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        raw = stream.read(16 * 1024**2 + 70001)
    if len(raw) != manifest['uncompressed_bytes'] or len(raw) > 16 * 1024**2 + 70000:
        raise ValueError('Invalid uncompressed size.')
    if hashlib.sha256(raw).hexdigest() != manifest['uncompressed_sha256']:
        raise ValueError('Content checksum mismatch.')
    rows = []
    for index, line in enumerate(raw.splitlines()):
        if len(line) > 70000:
            raise ValueError('Oversized record.')
        row = json.loads(line)
        if (row['schema'] != 'mnq-depth-events-v1' or row['kind'] != 'book_totals'
                or row['owner_hash'] != manifest['owner_hash'] or row['capture_id'] != manifest['capture_id']
                or row['index'] != manifest['first_index'] + index or row['synthetic'] is not False
                or row['data_live'] is not manifest['data_live']
                or row['payload']['schema'] != 'mnq-book-totals-v1'):
            raise ValueError('Summary scope or order mismatch.')
        rows.append(row)
    if len(rows) != manifest['events'] or manifest['last_index'] != manifest['first_index'] + len(rows) - 1:
        raise ValueError('Segment receipt count mismatch.')
    return rows


def number(value, *, nonnegative=False):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Invalid numeric measurement.')
    if nonnegative and value < 0:
        raise ValueError('Negative quantity.')
    return value


def read_history(root, query):
    root = Path(root)
    owner = query.get('owner_hash', '')
    scope = str(query.get('scope', 'all'))
    if not re.fullmatch('[a-f0-9]{64}', owner) or scope not in SCOPES:
        raise ValueError('Invalid recording scope.')
    start, end = query.get('start'), query.get('end')
    if bool(start) != bool(end):
        raise ValueError('Provide both start and end.')
    if start:
        start, end = timestamp(start), timestamp(end)
        if not timedelta(0) < end - start <= timedelta(hours=1):
            raise ValueError('Choose a time range of one hour or less.')
    manifests, errors, examined = [], 0, 0
    if root.exists():
        safe_path(root, root)
        for day in sorted(root.iterdir(), reverse=True):
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day.name) or not day.is_dir():
                continue
            safe_path(day, root)
            for run in sorted(day.iterdir()):
                if not re.fullmatch('[a-f0-9]{32}', run.name) or not run.is_dir():
                    continue
                safe_path(run, root)
                for marker in sorted(run.glob('*.segment.json')):
                    examined += 1
                    if examined > MAX_FILES:
                        raise ValueError('Recording inventory is too large; archive review is required.')
                    try:
                        safe_path(marker, root)
                        if marker.stat().st_size > 16384:
                            raise ValueError('Oversized manifest.')
                        m = json.loads(marker.read_text())
                        if m.get('owner_hash') != owner:
                            continue
                        if m['schema'] != 'mnq-depth-segment-v1' or m['synthetic'] is not False:
                            raise ValueError('Unsupported segment.')
                        first, last = timestamp(m['first_received_at']), timestamp(m['last_received_at'])
                        if first > last:
                            raise ValueError('Invalid segment range.')
                        manifests.append((first, last, marker, m))
                    except (ValueError, KeyError, OSError, TypeError):
                        errors += 1
    manifests.sort(key=lambda entry: (entry[0], str(entry[2])))
    earliest = min((v[0] for v in manifests), default=None)
    latest = max((v[1] for v in manifests), default=None)
    if start is None:
        end = (latest + timedelta(milliseconds=1)) if latest else datetime.now(timezone.utc)
        start = end - timedelta(minutes=15)
    days = sorted({m[0].date().isoformat() for m in manifests})
    points, raw_bytes, seen = [], 0, set()
    for first, last, marker, m in manifests:
        if last < start or first >= end:
            continue
        try:
            raw_bytes += m['uncompressed_bytes']
            if raw_bytes > MAX_RAW_BYTES:
                raise OverflowError('Choose a shorter time range to read this recording.')
            rows = verified_rows(marker, m, root)
            segment_points = []
            for row in rows:
                at = timestamp(row['received_at'])
                if not start <= at < end:
                    continue
                p = row['payload']
                totals = p if scope == 'all' else p['nearest'][scope]
                flags = p['quality_flags']
                if not isinstance(flags, list) or any(not isinstance(f, str) or not re.fullmatch('[a-z_]{1,64}', f) for f in flags):
                    raise ValueError('Invalid quality flags.')
                contract = row['contract_id']
                if not re.fullmatch(r'CON\.F\.US\.MNQ\.[A-Z0-9]{1,12}', contract):
                    raise ValueError('Invalid contract.')
                segment_points.append(dict(
                    timestamp=at.isoformat(), time=at.timestamp(), contract_id=contract,
                    capture_id=row['capture_id'], generation=p['generation'], index=row['index'],
                    bid_contracts=number(totals['bid_contracts'], nonnegative=True),
                    ask_contracts=number(totals['ask_contracts'], nonnegative=True),
                    bid_levels=number(totals['bid_levels'], nonnegative=True),
                    ask_levels=number(totals['ask_levels'], nonnegative=True),
                    imbalance=number(totals['imbalance']), mid_price=number(p['mid_price']),
                    last_trade_price=number(p['last_trade_price']), spread=number(p['spread']),
                    quality_flags=flags,
                ))
            for point in segment_points:
                key = (point['capture_id'], point['index'])
                if key not in seen:
                    seen.add(key)
                    points.append(point)
        except (ValueError, KeyError, OSError, TypeError, EOFError):
            errors += 1
        if len(points) > MAX_POINTS:
            raise ValueError('Too many measurements; choose a shorter time range.')
    points.sort(key=lambda p: (p['time'], p['capture_id'], p['index']))
    return dict(points=points, scope=scope, start=start.isoformat(), end=end.isoformat(),
                earliest=earliest.isoformat() if earliest else None,
                latest=latest.isoformat() if latest else None, available_days=days,
                contracts=sorted({p['contract_id'] for p in points}),
                skipped_segments=errors, sealed_only=True, interval_ms=250,
                snapshot_completion_verified=False, provider_sequence_guarantee=False,
                generated_at=datetime.now(timezone.utc).isoformat())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    try:
        query = json.loads(sys.stdin.read(4097))
        print(json.dumps(read_history(args.root, query), separators=(',', ':'), allow_nan=False))
    except (ValueError, OSError, KeyError, TypeError, OverflowError):
        print(json.dumps({'error': 'History could not be read. Choose a shorter range or check recording integrity.'}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
