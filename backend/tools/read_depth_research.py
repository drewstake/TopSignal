"""Read-only bounded raw reconstruction helper, including the private SSH entrypoint."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import gzip
import io
import json
from pathlib import Path
import re
import sys
import time

if __package__:
    from .read_depth_history import safe_path, timestamp, verified_rows, MAX_FILES
    from .depth_totals import BookTotals
    from .depth_features import FEATURE_VERSION
else:
    from read_depth_history import safe_path, timestamp, verified_rows, MAX_FILES
    from depth_totals import BookTotals
    from depth_features import FEATURE_VERSION

MAX_BYTES = 256 * 1024**2
MAX_EVENTS = 600000
MAX_SECONDS = 600
MAX_POINTS = 3000
MAX_FRAMES = 1200


def measurement_code_hash():
    here = Path(__file__).parent
    names = ('depth_book.py', 'depth_totals.py', 'depth_features.py', 'read_depth_research.py', 'read_depth_history.py')
    return hashlib.sha256(b''.join((here/name).read_bytes() for name in names)).hexdigest()


def inventory(root, owner):
    root = Path(root)
    if not re.fullmatch('[a-f0-9]{64}', owner):
        raise ValueError('Invalid recording owner')
    manifests, errors, examined = [], 0, 0
    if not root.exists():
        return manifests, errors
    safe_path(root, root)
    for day in sorted(root.iterdir()):
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
                    raise ValueError('Inventory exceeds 50,000 manifests; storage review required')
                try:
                    safe_path(marker, root)
                    if marker.stat().st_size > 16384:
                        raise ValueError('Oversized manifest')
                    m = json.loads(marker.read_text())
                    if m.get('owner_hash') != owner:
                        continue
                    if m['schema'] != 'mnq-depth-segment-v1' or m['synthetic'] is not False or m['source'] != 'projectx':
                        raise ValueError('Invalid segment scope')
                    if not re.fullmatch('[a-f0-9]{32}', m['capture_id']) or m['capture_id'] != run.name:
                        raise ValueError('Invalid capture identity')
                    first, last = timestamp(m['first_received_at']), timestamp(m['last_received_at'])
                    if first > last or not 0 < m['uncompressed_bytes'] <= 16*1024**2+70000:
                        raise ValueError('Invalid segment bounds')
                    manifests.append((first, last, marker, m))
                except (ValueError, KeyError, OSError, TypeError):
                    errors += 1
    manifests.sort(key=lambda v: (v[0], v[3]['capture_id'], v[3]['first_index']))
    return manifests, errors


def coverage(manifests, errors):
    return dict(earliest=min((v[0].isoformat() for v in manifests), default=None),
                latest=max((v[1].isoformat() for v in manifests), default=None),
                available_days=sorted({v[0].date().isoformat() for v in manifests}),
                segments=len(manifests), compressed_bytes=sum(v[3]['bytes'] for v in manifests),
                skipped_segments=errors, sealed_only=True, feature_version=FEATURE_VERSION,
                snapshot_completion_verified=False, provider_sequence_guarantee=False,
                directional_flow_validated=False, max_seconds=MAX_SECONDS,
                max_read_bytes=MAX_BYTES, max_points=MAX_POINTS,
                generated_at=datetime.now(timezone.utc).isoformat())


def cached_features(raw_root, query, start, end, scope, ticks):
    if query.get('replay') or query.get('all_scopes'):
        return None
    cache = raw_root.parent/'microstructure-derived-v2'/query['owner_hash']
    if not cache.exists():
        return None
    safe_path(cache, raw_root.parent)
    for i, marker in enumerate(cache.glob('*.manifest.json')):
        if i >= 5000:
            raise ValueError('Derived inventory requires review')
        safe_path(marker, raw_root.parent)
        if marker.stat().st_size > 65536:
            continue
        try:
            m = json.loads(marker.read_text())
            if (timestamp(m['start']) != start or timestamp(m['end']) != end or m['owner_hash'] != query['owner_hash']
                    or m['measurement_code_hash'] != measurement_code_hash()):
                continue
            if not re.fullmatch('([a-f0-9]{32}|[a-f0-9]{64})\\.json\\.gz',m['file']):
                continue
            path = safe_path(marker.parent/m['file'], raw_root.parent)
            if path.stat().st_size != m['bytes'] or m['bytes'] > 24*1024**2:
                continue
            compressed=path.read_bytes()
            if hashlib.sha256(compressed).hexdigest() != m['sha256']:
                continue
            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
                raw=stream.read(24*1024**2+1)
            if len(raw)>24*1024**2:
                continue
            data=json.loads(raw)
            if len(data['points'])>MAX_POINTS or any(p.get('tick_distances')!=ticks or scope not in p.get('scopes',{}) for p in data['points']):
                continue
            for p in data['points']:
                scopes=p.pop('scopes')
                p.update(scopes[scope])
            data['scope']=scope
            data['derived_cache_id']=data['data_id']
            data['data_id']=hashlib.sha256((data['data_id']+'|'+scope).encode()).hexdigest()
            return data
        except (ValueError,KeyError,OSError,TypeError,EOFError):
            continue  # Fall back to immutable raw segments; never use corrupt cache.
    return None


def read_research(root, query, *, cancelled=lambda: False, progress=lambda value: None):
    root = Path(root)
    manifests, errors = inventory(root, query.get('owner_hash', ''))
    result = coverage(manifests, errors)
    if query.get('operation') == 'inventory':
        return result
    start, end = timestamp(query['start']), timestamp(query['end'])
    if not timedelta(0) < end-start <= timedelta(seconds=MAX_SECONDS):
        raise ValueError('Choose at most 10 minutes per raw research window')
    scope = str(query.get('scope', '10'))
    ticks = query.get('tick_distances', [1, 5, 10, 20])
    allowed_scopes = {'all', '1', '5', '10', '20', '50', '100'} | {f'ticks:{n}' for n in ticks}
    if scope not in allowed_scopes:
        raise ValueError('Invalid measurement scope')
    cached = cached_features(root, query, start, end, scope, ticks)
    if cached is not None:
        return dict(cached, **{k:v for k,v in result.items() if k not in cached})
    pre_start = start - timedelta(seconds=60)
    selected = [v for v in manifests if v[1] >= pre_start and v[0] < end]
    if sum(v[3]['uncompressed_bytes'] for v in selected) > MAX_BYTES:
        raise ValueError('Raw read exceeds 256 MiB; shorten the window')
    engine = BookTotals(tick_distances=tuple(ticks))
    points, frames, sources, excluded = [], [], [], []
    previous, last_frame = None, None
    counts, types, seq = Counter(), Counter(), Counter()
    events = 0
    started = time.monotonic()
    capture = None
    last_seq, last_epoch = None, None
    reconstruction = 0
    for ordinal, (_, _, marker, manifest) in enumerate(selected):
        if cancelled():
            raise InterruptedError('Research cancelled')
        if time.monotonic()-started > 110:
            raise ValueError('Reconstruction time budget reached; shorten the window')
        try:
            rows = verified_rows(marker, manifest, root, raw_events=True)
        except (ValueError, KeyError, OSError, TypeError, EOFError):
            errors += 1
            engine = BookTotals(tick_distances=tuple(ticks))
            reconstruction += 1
            previous = None
            excluded.append(dict(start=manifest['first_received_at'], end=manifest['last_received_at'], reason='corrupt_segment'))
            continue
        sources.append({k: manifest[k] for k in ('capture_id', 'first_index', 'last_index', 'sha256', 'uncompressed_sha256')})
        for event in rows:
            events += 1
            if events > MAX_EVENTS:
                raise ValueError('Raw read exceeds 600,000 events; shorten the window')
            if events % 2048 == 0 and cancelled():
                raise InterruptedError('Research cancelled')
            at = timestamp(event['received_at'])
            if at >= end:
                continue
            if (not re.fullmatch(r'CON\.F\.US\.MNQ\.[A-Z0-9]{1,12}', event['contract_id'])
                    or event['kind'] not in {'depth', 'trade', 'quote', 'connect', 'disconnect', 'gap', 'roll', 'end'}
                    or type(event['monotonic_ns']) is not int):
                raise ValueError('Invalid raw event')
            if capture != event['capture_id'] or (previous and event['index'] != previous['index']+1):
                engine = BookTotals(tick_distances=tuple(ticks))
                reconstruction += 1
                last_seq, last_epoch = None, None
            capture = event['capture_id']
            previous = event
            p = event['payload']
            counts[event['kind']] += 1
            if event['kind'] == 'trade':
                types[str(p.get('type'))] += 1
            if event['kind'] == 'depth' and p.get('seq') is not None:
                current = p['seq']
                if isinstance(current, (int, float)) and isinstance(last_seq, (int, float)) and p.get('epoch') == last_epoch:
                    seq['repeat' if current == last_seq else 'reversal' if current < last_seq else 'increase'] += 1
                    if current > last_seq+1:
                        seq['jumps'] += 1
                last_seq, last_epoch = current, p.get('epoch')
            row = engine.apply(event)
            if row is None or at < start:
                continue
            f = row['features']
            point = dict(timestamp=at.isoformat(), time=at.timestamp(), contract_id=event['contract_id'],
                capture_id=capture, index=event['index'], generation=row['generation'], reconstruction_id=reconstruction,
                provider_at=event['provider_at'], receipt_monotonic_ns=event['monotonic_ns'],
                source='projectx', data_live=event['data_live'], schema=row['schema'],
                **{k: row[k] for k in ('best_bid', 'best_ask', 'spread', 'mid_price', 'last_trade_price', 'trade_age_ms', 'side_age_ms', 'provider_age_ms', 'quality_flags', 'execution_interval')},
                **f['scopes'][scope], **{k: v for k, v in f.items() if k != 'scopes'})
            points.append(point)
            if query.get('all_scopes'):
                point['scopes'] = f['scopes']
            if len(points) > MAX_POINTS:
                raise ValueError('Too many samples; shorten window')
            if query.get('replay', False) and (last_frame is None or point['time']-last_frame >= max(.5, (end-start).total_seconds()/MAX_FRAMES)):
                snapshot = engine.book.snapshot()
                frames.append(dict(time=point['time'], timestamp=point['timestamp'], contract_id=point['contract_id'],
                    capture_id=capture, index=event['index'], generation=point['generation'], reconstruction_id=reconstruction,
                    quality_flags=point['quality_flags'], mid_price=row['mid_price'],
                    last_trade_price=row['last_trade_price'] if row['trade_age_ms'] is not None and row['trade_age_ms'] <= 500 else None,
                    bids=snapshot['bids'][:50], asks=snapshot['asks'][:50]))
                last_frame = point['time']
        progress((ordinal+1)/max(1, len(selected)))
    identity = dict(sources=sources, start=start.isoformat(), end=end.isoformat(), scope=scope,
                    feature_version=FEATURE_VERSION, tick_distances=ticks, pre_roll_seconds=60,
                    measurement_code_hash=measurement_code_hash())
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return dict(result, points=points, frames=frames, sources=sources, data_id=digest,
        start=start.isoformat(), end=end.isoformat(), scope=scope, pre_roll_seconds=60,
        contracts=sorted({p['contract_id'] for p in points}), skipped_segments=errors,
        excluded_intervals=excluded, read_bytes=sum(v[3]['uncompressed_bytes'] for v in selected),
        raw_events=events, event_counts=dict(counts), provider_trade_types=dict(types), measurement_code_hash=identity['measurement_code_hash'],
        observed_sequence_diagnostics=dict(seq), reconstruction='Observed reconstruction; no verified initial full snapshot. Starts at the first selected segment, with at least 60 seconds of pre-roll where available.',
        interval_ms=250, replay_interval_ms=max(500, (end-start).total_seconds()*1000/MAX_FRAMES))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    try:
        query = json.loads(sys.stdin.read(8193))
        print(json.dumps(read_research(args.root, query), separators=(',', ':'), allow_nan=False))
    except (ValueError, KeyError, TypeError, OverflowError, OSError) as exc:
        print(json.dumps({'error': str(exc) if isinstance(exc, ValueError) else 'Recording integrity or access failure'}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
