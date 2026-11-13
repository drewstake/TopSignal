"""Bounded, owner-scoped reader for committed order-book summary segments.

Standalone stdlib entry point also used over the private host SSH connection.
No database, credentials, recording changes, or trading process dependency.
"""
import argparse
from collections import OrderedDict, Counter
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import gzip
import hashlib
import io
import json
import math
import os
import stat
from pathlib import Path
import re
import sys
from threading import Lock
from time import monotonic
import sqlite3

try:
    from .depth_history_index import optional_index, signature
    from .recording_inventory import directories
except ImportError:
    from depth_history_index import optional_index, signature
    from recording_inventory import directories

MAX_POINTS = 100000
MAX_RAW_BYTES = 64 * 1024**2
MAX_SESSION_RAW_BYTES = 2 * 1024**3
MAX_FILES = 50000
SCOPES = {'all', '1', '5', '10', '20', '50', '100'}

# Sealed manifests are immutable commit receipts. Keep only their small parsed
# metadata, never raw recordings or replay arrays. Recheck paths and file
# identity on every request so replacement, new segments and tampering remain
# visible. The long-running API reuses this cache across history refreshes.
_MANIFEST_CACHE_ENTRIES = 4096
_MANIFEST_CACHE_BYTES = 8 * 1024**2
_manifest_cache = OrderedDict()
_manifest_cache_bytes = 0
_manifest_cache_lock = Lock()


def read_manifest(marker, root, *, validated_parent=None, file_info=None):
    global _manifest_cache_bytes
    if validated_parent is None:
        safe_path(marker, root)
    elif marker.parent != validated_parent or not re.fullmatch(r'\d{8}\.segment\.json', marker.name):
        raise ValueError('Invalid manifest under verified run directory.')
    # Inventory validates the run's ancestors once, then supplies fresh Windows
    # enumeration metadata for each immediate child. Selected source receipts
    # and data still pass the complete path check before returning observations.
    info = file_info or marker.stat(follow_symlinks=False)
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError('Recording reparse points are unsupported.')
    if info.st_size > 16384:
        raise ValueError('Oversized manifest.')
    key = str(marker.absolute())
    signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    with _manifest_cache_lock:
        cached = _manifest_cache.get(key)
        # Windows directory enumeration omits device/inode. Compare its fresh
        # metadata, then retain the complete identity when the selected-receipt
        # path check supplies it. Never discard a known complete identity.
        same_identity = (cached and (not all(signature[:2]) or not all(cached[0][:2])
                                    or cached[0][:2] == signature[:2]))
        if cached and same_identity and cached[0][2:] == signature[2:]:
            if all(signature[:2]):
                _manifest_cache[key] = (signature,cached[1],cached[2])
            _manifest_cache.move_to_end(key)
            return cached[1]
    # Use a bounded read even if a file changes between stat and open.
    with marker.open('rb') as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError('Oversized manifest.')
    manifest = json.loads(raw)
    if not isinstance(manifest, dict):
        raise ValueError('Invalid manifest.')
    with _manifest_cache_lock:
        old = _manifest_cache.pop(key, None)
        if old:
            _manifest_cache_bytes -= old[2]
        _manifest_cache[key] = (signature, manifest, len(raw))
        _manifest_cache_bytes += len(raw)
        while (len(_manifest_cache) > _MANIFEST_CACHE_ENTRIES
               or _manifest_cache_bytes > _MANIFEST_CACHE_BYTES):
            _, evicted = _manifest_cache.popitem(last=False)
            _manifest_cache_bytes -= evicted[2]
    return manifest


def session_bounds(now):
    local = now.astimezone(ZoneInfo('America/New_York'))
    day = local.date() - timedelta(days=local.hour < 18)
    while day.weekday() in (4, 5):
        day -= timedelta(days=1)
    start = datetime.combine(day, time(18), local.tzinfo).astimezone(timezone.utc)
    close = datetime.combine(day + timedelta(days=1), time(17), local.tzinfo).astimezone(timezone.utc)
    return start, min(now, close)


FIELDS = ('mid_price', 'bid_contracts', 'ask_contracts', 'imbalance')
BOUNDARIES = {'connect', 'disconnect', 'gap', 'roll', 'end', 'reset', 'epoch_changed',
              'contract_changed', 'receipt_gap', 'receipt_time_reversed'}
ADVISORY = {'bid_provider_time_unreliable', 'ask_provider_time_unreliable'}
KNOWN_FLAGS = BOUNDARIES | ADVISORY | {'missing_side', 'bid_stale', 'ask_stale', 'locked_or_crossed'}


def metric_reasons(point, field):
    flags = set(point['quality_flags'])
    reasons = flags & BOUNDARIES | flags - KNOWN_FLAGS
    sides = ('bid',) if field == 'bid_contracts' else ('ask',) if field == 'ask_contracts' else ('bid', 'ask')
    for side in sides:
        if f'{side}_stale' in flags:
            reasons.add(f'{side}_stale')
        if not point.get(f'{side}_levels'):
            reasons.add(f'{side}_missing')
    if field == 'mid_price':
        reasons.update(flags & (ADVISORY | {'locked_or_crossed'}))
    if point.get(field) is None:
        reasons.add(f'{field}_unavailable')
    return sorted(reasons)


def point_quality(point):
    if not point['quality_flags'] and point.get('bid_levels') and point.get('ask_levels'):
        return {field: [] if point.get(field) is not None else [field+'_unavailable'] for field in FIELDS}
    return {field: metric_reasons(point,field) for field in FIELDS}


def annotate_continuity(previous, point, quality=None):
    """Receipt-based continuity, before reduction. Flags remain on their own observation."""
    stream = (point['contract_id'], point['capture_id'], point['generation'])
    old, old_quality = previous.get(stream, (None, {}))
    quality = quality if quality is not None else point_quality(point)
    elapsed = point['time'] - old['time'] if old else 0
    point['continuity'] = {}
    point['gap_before'] = {}
    for field in FIELDS:
        invalid = quality[field]
        prior_invalid = old_quality.get(field, [])
        reasons = invalid or prior_invalid
        if old and (elapsed > 2 or elapsed <= 0):
            reasons = sorted(set(reasons + ['missing_observations' if elapsed > 2 else 'receipt_time_reversed']))
        changed = bool(reasons)
        point['continuity'][field] = point['index'] if changed else old['continuity'][field] if old else 0
        point['gap_before'][field] = (dict(reasons=reasons, seconds=max(0, elapsed), start=old['time'] if old else point['time'], end=point['time'])
                                      if changed else old['gap_before'].get(field) if old else None)
    previous[stream] = (point, quality)
    return point


def add_overview_point(buckets, previous, point, quality=None):
    """Retain actual first/last and metric extrema per 10s. Never union flags onto a sample."""
    quality = quality if quality is not None else point_quality(point)
    point = annotate_continuity(previous, point, quality)
    point['sample_interval_ms'] = 10000
    key = (point['contract_id'], point['capture_id'], point['generation'], int(point['time'] // 10))
    bucket = buckets.setdefault(key, {'first': point})
    bucket['last'] = point
    for field in FIELDS:
        if quality[field]:
            continue
        for suffix, choose in (('min', min), ('max', max)):
            name = field + suffix
            old = bucket.get(name)
            if old is None or choose(point[field], old[field]) == point[field]:
                bucket[name] = point


def overview_points(buckets):
    return list({(p['capture_id'], p['index']): p for bucket in buckets.values() for p in bucket.values()}.values())


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


def verified_rows(marker, manifest, root, *, raw_events=False):
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
        if (row['schema'] != 'mnq-depth-events-v1' or (not raw_events and row['kind'] != 'book_totals')
                or row['owner_hash'] != manifest['owner_hash'] or row['capture_id'] != manifest['capture_id']
                or row['index'] != manifest['first_index'] + index or row['synthetic'] is not False
                or row['data_live'] is not manifest['data_live']
                or (not raw_events and row['payload']['schema'] not in {'mnq-book-totals-v1', 'mnq-book-totals-v2'})):
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


def recording_health(root, owner):
    path = root.parent / 'recording-health.json'
    try:
        safe_path(path, root.parent)
        with path.open('rb') as stream:
            value = json.loads(stream.read(16385))
        if value.pop('owner_hash', None) != owner:
            return None
        value['stale'] = (datetime.now(timezone.utc)-timestamp(value['at'])).total_seconds() > 20
        return value
    except (OSError, ValueError, TypeError, KeyError):
        return None


class HistoryReadTimeout(RuntimeError):
    pass


def check_deadline(deadline):
    if deadline is not None and monotonic() >= deadline:
        raise HistoryReadTimeout('History is still preparing. Retry shortly or choose a shorter range.')


def read_history(root, query, *, deadline=None, cache_path=None):
    try:
        with optional_index(cache_path, root) as index:
            return _read_history(root, query, deadline=deadline, index=index)
    except sqlite3.Error as exc:
        raise RuntimeError('History cache is busy or unavailable. Retry shortly.') from exc


def _read_history(root, query, *, deadline=None, index=None):
    root = Path(root)
    owner = query.get('owner_hash', '')
    scope = str(query.get('scope', 'all'))
    if not re.fullmatch('[a-f0-9]{64}', owner) or scope not in SCOPES:
        raise ValueError('Invalid recording scope.')
    start, end = query.get('start'), query.get('end')
    window = query.get('window', 'latest')
    if window not in {'latest', 'session'} or (window == 'session' and (start or end)):
        raise ValueError('Invalid history window.')
    if bool(start) != bool(end):
        raise ValueError('Provide both start and end.')
    if start:
        start, end = timestamp(start), timestamp(end)
        if not timedelta(0) < end - start <= timedelta(hours=24):
            raise ValueError('Choose a time range of one session (up to 24 hours) or less.')
    elif window == 'session':
        start, end = session_bounds(datetime.now(timezone.utc))
    manifests, errors, examined = [], 0, 0
    present_runs = set()
    if root.exists():
        safe_path(root, root)
        for day, _ in directories(root, r'\d{4}-\d{2}-\d{2}', reverse=True):
            check_deadline(deadline)
            for run, _ in directories(day, '[a-f0-9]{32}'):
                check_deadline(deadline)
                present_runs.add(str(run))
                if index is not None and index.run_current(run):
                    continue
                before = signature(run)
                safe_path(run, root)
                found = []
                known = index.known(run) if index is not None else {}
                with os.scandir(run) as entries:
                    markers = sorted((Path(e.path), e.stat(follow_symlinks=False)) for e in entries if e.name.endswith('.segment.json'))
                for marker, info in markers:
                    if deadline is not None and monotonic() >= deadline:
                        if index is not None:
                            index.save_partial(run, found)
                        check_deadline(deadline)
                    examined += 1
                    if examined > MAX_FILES:
                        raise ValueError('Recording inventory is too large; archive review is required.')
                    try:
                        m = known.get(str(marker)) or read_manifest(marker, root, validated_parent=run, file_info=info)
                        found.append((marker, m))
                        if m.get('owner_hash') != owner:
                            continue
                        if m['schema'] != 'mnq-depth-segment-v1' or m['synthetic'] is not False:
                            raise ValueError('Unsupported segment.')
                        first, last = timestamp(m['first_received_at']), timestamp(m['last_received_at'])
                        if first > last:
                            raise ValueError('Invalid segment range.')
                        if index is None:
                            manifests.append((first, last, marker, m))
                    except (ValueError, KeyError, OSError, TypeError):
                        errors += 1
                if index is not None:
                    index.save_run(run, before, found)
        if index is not None:
            index.remove_missing_runs(present_runs)
            for marker, m in index.manifests(owner):
                check_deadline(deadline)
                try:
                    first, last = timestamp(m['first_received_at']), timestamp(m['last_received_at'])
                    if m['schema'] != 'mnq-depth-segment-v1' or m['synthetic'] is not False or first > last:
                        raise ValueError('Invalid indexed segment')
                    manifests.append((first, last, marker, m))
                except (ValueError, KeyError, TypeError):
                    errors += 1
    manifests.sort(key=lambda entry: (entry[0], str(entry[2])))
    earliest = min((v[0] for v in manifests), default=None)
    latest = max((v[1] for v in manifests), default=None)
    if start is None:
        end = (latest + timedelta(milliseconds=1)) if latest else datetime.now(timezone.utc)
        start = end - timedelta(minutes=15)
    days = sorted({m[0].date().isoformat() for m in manifests})
    overview = end - start > timedelta(hours=1)
    points, raw_bytes, seen = [], 0, set()
    buckets, previous = {}, {}
    quality_counts, excluded_counts = Counter(), Counter()
    raw_count = flagged_count = 0
    for first, last, marker, m in manifests:
        check_deadline(deadline)
        if last < start or first >= end:
            continue
        try:
            # An index is a locator, never authority for ownership or integrity.
            current = read_manifest(marker, root)
            if current != m:
                if index is not None:
                    index.invalidate_run(marker.parent)
                raise ValueError('Indexed receipt changed; rediscovery required')
            if current.get('owner_hash') != owner:
                raise ValueError('Recording ownership changed')
            raw_bytes += m['uncompressed_bytes']
            if raw_bytes > (MAX_SESSION_RAW_BYTES if overview else MAX_RAW_BYTES):
                raise OverflowError('Choose a shorter time range to read this recording.')
            segment_points = segment_measurements(marker, m, root, scope, index=index, deadline=deadline)
            for point in segment_points:
                if not start.timestamp() <= point['time'] < end.timestamp():
                    continue
                key = (point['capture_id'], point['index'])
                if key in seen:
                    continue
                seen.add(key)
                raw_count += 1
                flagged_count += bool(point['quality_flags'])
                quality_counts.update(point['quality_flags'])
                quality = point_quality(point)
                excluded_counts.update(field for field in FIELDS if quality[field])
                if overview:
                    add_overview_point(buckets, previous, point, quality)
                    continue
                points.append(annotate_continuity(previous, point, quality))
        except (ValueError, KeyError, OSError, TypeError, EOFError):
            errors += 1
        if max(len(points), len(buckets) * 10) > MAX_POINTS or len(seen) > 400000:
            raise ValueError('Too many measurements; choose a shorter time range.')
    if overview:
        points = overview_points(buckets)
    points.sort(key=lambda p: (p['time'], p['capture_id'], p['index']))
    return dict(points=points, scope=scope, start=start.isoformat(), end=end.isoformat(),
                earliest=earliest.isoformat() if earliest else None,
                latest=latest.isoformat() if latest else None, available_days=days,
                contracts=sorted({p['contract_id'] for p in points}),
                skipped_segments=errors, sealed_only=True, interval_ms=10000 if overview else 250,
                window=window, recording_health=recording_health(root,owner), raw_measurements=raw_count, flagged_measurements=flagged_count,
                quality_counts=dict(quality_counts), excluded_by_metric=dict(excluded_counts),
                downsampling='first_last_metric_extrema' if overview else 'none',
                snapshot_completion_verified=False, provider_sequence_guarantee=False,
                generated_at=datetime.now(timezone.utc).isoformat())


def segment_measurements(marker, manifest, root, scope, *, index=None, deadline=None):
    name = manifest['file']
    if not re.fullmatch(r'\d{8}\.ndjson\.gz', name):
        raise ValueError('Invalid segment filename.')
    path = safe_path(marker.parent / name, root)
    before = signature(path)
    identity = json.dumps([before, manifest], sort_keys=True)
    if index is not None:
        cached = index.load_points(marker, scope, identity)
        if cached is not None:
            return cached
    points = []
    for i, row in enumerate(verified_rows(marker, manifest, root)):
        if i % 64 == 0:
            check_deadline(deadline)
        at = timestamp(row['received_at'])
        p = row['payload']
        totals = p if scope == 'all' else p['nearest'][scope]
        flags = p['quality_flags']
        if not isinstance(flags, list) or any(not isinstance(f, str) or not re.fullmatch('[a-z_]{1,64}', f) for f in flags):
            raise ValueError('Invalid quality flags.')
        contract = row['contract_id']
        if not re.fullmatch(r'CON\.F\.US\.MNQ\.[A-Z0-9]{1,12}', contract):
            raise ValueError('Invalid contract.')
        points.append(dict(timestamp=at.isoformat(), time=at.timestamp(), contract_id=contract,
            capture_id=row['capture_id'], generation=p['generation'], index=row['index'],
            bid_contracts=number(totals['bid_contracts'], nonnegative=True),
            ask_contracts=number(totals['ask_contracts'], nonnegative=True),
            bid_levels=number(totals['bid_levels'], nonnegative=True),
            ask_levels=number(totals['ask_levels'], nonnegative=True),
            imbalance=number(totals['imbalance']), mid_price=number(p['mid_price']),
            last_trade_price=number(p['last_trade_price']), spread=number(p['spread']),
            quality_flags=flags, side_age_ms=p.get('side_age_ms'), provider_age_ms=p.get('provider_age_ms')))
    if signature(path) != before:
        raise ValueError('Recording changed while reading')
    if index is not None:
        try:
            index.save_points(marker, scope, identity, points)
        except sqlite3.Error:
            pass  # Optional cache failure never creates or discards observations.
    return points


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    try:
        query = json.loads(sys.stdin.read(4097))
        cache = args.root.parent.parent / 'depth-history-cache-v1' / 'history.sqlite3'
        result = read_history(args.root, query, deadline=monotonic() + 20, cache_path=cache)
        print(json.dumps(result, separators=(',', ':'), allow_nan=False))
    except (ValueError, OSError, KeyError, TypeError, OverflowError, RuntimeError):
        print(json.dumps({'error': 'History could not be read. Choose a shorter range or check recording integrity.'}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
