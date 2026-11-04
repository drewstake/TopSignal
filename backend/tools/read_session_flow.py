"""Bounded session summaries of committed ProjectX market trades.

Provider Buy/Sell labels are documented; aggressor semantics are NOT verified.
Never consumes DOM quantity, account fills, candles, or future observations.
Derived minute aggregates live only in bounded local SQLite, not cloud storage.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time

if __package__:
    from .read_depth_history import safe_path, timestamp, session_bounds, verified_rows
    from .read_depth_research import inventory
else:
    from read_depth_history import safe_path, timestamp, session_bounds, verified_rows
    from read_depth_research import inventory

VERSION = 'session-provider-flow-v1'
CACHE_VERSION = 'session-provider-flow-cache-v2'
MAX_READ_BYTES = 128 * 1024**2
MAX_CACHE_BYTES = 64 * 1024**2
FIELDS = ('buy', 'sell', 'unknown', 'trades', 'invalid', 'bad_time')


def valid_number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def empty_bucket():
    return dict(dict.fromkeys(FIELDS, 0), first_price=None, last_price=None, first_price_at=None, last_price_at=None)


def merge_bucket(target, source):
    for field in FIELDS:
        target[field] += source[field]
    if source['first_price_at'] and (not target['first_price_at'] or source['first_price_at'] < target['first_price_at']):
        target['first_price_at'], target['first_price'] = source['first_price_at'], source['first_price']
    if source['last_price_at'] and (not target['last_price_at'] or source['last_price_at'] >= target['last_price_at']):
        target['last_price_at'], target['last_price'] = source['last_price_at'], source['last_price']


def summarize(rows, contract, live):
    buckets, boundaries = {}, []
    first = last = None
    for event in rows:
        if (event['contract_id'] != contract or event['data_live'] is not live
                or event.get('source') != 'projectx'):
            continue
        received = timestamp(event['received_at'])
        first = received.isoformat() if first is None else first
        if last and (received-timestamp(last)).total_seconds() > 5:
            boundaries.append((received.isoformat(), 'receipt_silence'))
        last = received.isoformat()
        if event['kind'] in {'connect', 'disconnect', 'gap', 'roll', 'reset', 'end'}:
            boundaries.append((received.isoformat(), event['kind']))
        if event['kind'] != 'trade':
            continue
        payload = event['payload']
        try:
            at = timestamp(event['provider_at'])
            good_time = 0 <= (received-at).total_seconds() <= 2
        except (ValueError, TypeError, AttributeError):
            good_time = False
        # Unreliable provider times cannot place a trade in the true session.
        at = at if good_time else received
        key = at.replace(second=0, microsecond=0).isoformat()
        bucket = buckets.setdefault(key, empty_bucket())
        bucket['trades'] += 1
        volume, price = payload.get('volume'), payload.get('price')
        if not valid_number(volume) or not valid_number(price):
            bucket['invalid'] += 1
            continue
        typ = payload.get('type')
        side = ('buy' if typ == 0 else 'sell') if type(typ) is int and typ in (0, 1) and good_time else 'unknown'
        bucket[side] += volume
        bucket['bad_time'] += not good_time
        if good_time:
            at_text = at.isoformat()
            if not bucket['first_price_at'] or at_text < bucket['first_price_at']:
                bucket['first_price_at'], bucket['first_price'] = at_text, price
            if not bucket['last_price_at'] or at_text >= bucket['last_price_at']:
                bucket['last_price_at'], bucket['last_price'] = at_text, price
    return dict(buckets=buckets, boundaries=boundaries, first=first, last=last)


def flow_window(buckets, start, end):
    totals = empty_bucket()
    for at, values in buckets.items():
        if start <= timestamp(at) < end:
            merge_bucket(totals, values)
    classified = totals['buy'] + totals['sell']
    volume = classified + totals['unknown']
    change = totals['last_price']-totals['first_price'] if totals['first_price'] is not None and totals['first_price_at'] != totals['last_price_at'] else None
    return dict(totals, start=start.isoformat(), end=end.isoformat(), price_change=change,
                delta=totals['buy']-totals['sell'] if classified else None,
                buy_percent=100*totals['buy']/classified if classified else None,
                delta_percent=100*(totals['buy']-totals['sell'])/classified if classified else None,
                classified_percent=100*classified/volume if volume else None)


def read_session_flow(root, query, *, now=None, max_read_bytes=MAX_READ_BYTES):
    root = Path(root)
    owner, contract, live = query.get('owner_hash', ''), query.get('contract_id', ''), query.get('data_live', False)
    if not re.fullmatch('[a-f0-9]{64}', owner) or not re.fullmatch(r'CON\.F\.US\.MNQ\.[A-Z0-9]{1,12}', contract) or type(live) is not bool:
        raise ValueError('Invalid session scope')
    now = now or datetime.now(timezone.utc)
    start, end = session_bounds(now)
    end = end.replace(second=0, microsecond=0)
    manifests, errors = inventory(root, owner)
    selected = [entry for entry in manifests if entry[1] >= start and entry[0] < end+timedelta(seconds=2)
                and entry[3]['data_live'] is live]
    buckets, bounds, reasons, latest = {}, [], Counter(), None
    earliest = None
    processed = pending = read_bytes = 0
    started = time.monotonic()
    connection = None
    seen = set()
    previous = None
    try:
        if selected:
            cache_dir = safe_path(root.parent/'session-orderflow-v1'/owner, root.parent)
            cache_dir.mkdir(parents=True, exist_ok=True)
            path = safe_path(cache_dir/'segments.sqlite3', root.parent)
            if shutil.disk_usage(cache_dir).free < 1024**3:
                raise ValueError('Insufficient free space for bounded derived summaries')
            connection = sqlite3.connect(path, timeout=1)
            connection.execute('PRAGMA journal_mode=DELETE')
            size = connection.execute('PRAGMA page_size').fetchone()[0]
            connection.execute(f'PRAGMA max_page_count={MAX_CACHE_BYTES//size}')
            connection.execute('CREATE TABLE IF NOT EXISTS segments (key TEXT PRIMARY KEY, summary TEXT NOT NULL)')
        for first, last, marker, manifest in selected:
            identity = (manifest['capture_id'], manifest['first_index'], manifest['last_index'])
            if identity in seen:
                continue
            seen.add(identity)
            try:
                if not re.fullmatch(r'\d{8}\.ndjson\.gz', manifest['file']):
                    raise ValueError('Invalid filename')
                raw_path = safe_path(marker.parent/manifest['file'], root)
                stat = raw_path.stat()
                if stat.st_size != manifest['bytes']:
                    raise ValueError('Segment size changed')
                # Cache is tied to immutable source checksum, scope and file identity.
                material = [CACHE_VERSION, str(root.resolve()), contract, live, manifest,
                            stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino]
                key = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
                cached = connection.execute('SELECT summary FROM segments WHERE key=?', (key,)).fetchone()
                if cached:
                    summary = json.loads(cached[0])
                else:
                    if read_bytes+manifest['uncompressed_bytes'] > max_read_bytes or time.monotonic()-started > 12:
                        pending += 1
                        continue
                    read_bytes += manifest['uncompressed_bytes']
                    summary = summarize(verified_rows(marker, manifest, root, raw_events=True), contract, live)
                    encoded = json.dumps(summary, separators=(',', ':'), allow_nan=False)
                    if len(encoded) > 256*1024:
                        raise ValueError('Summary exceeds bound')
                    connection.execute('INSERT INTO segments VALUES (?,?)', (key, encoded))
                    connection.commit()
                processed += 1
                if not summary['first']:
                    continue  # Another delivery cannot suppress this delivery's observations.
                scoped_first, scoped_last = timestamp(summary['first']), timestamp(summary['last'])
                if previous:
                    if (identity[0] == previous[0] and identity[1] <= previous[2]) or scoped_first < previous[3]:
                        reasons['overlapping_recordings'] += 1
                        continue
                    if identity[0] != previous[0] or identity[1] != previous[2]+1:
                        reasons['recording_discontinuity'] += 1
                    if (scoped_first-previous[3]).total_seconds() > 5:
                        reasons['receipt_silence'] += 1
                previous = (*identity, scoped_last)
                earliest = min(earliest or summary['first'], summary['first'])
                latest = max(latest or summary['last'], summary['last'])
                bounds.extend(summary['boundaries'])
                for at, bucket in summary['buckets'].items():
                    combined = buckets.setdefault(at, empty_bucket())
                    merge_bucket(combined, bucket)
            except (ValueError, KeyError, OSError, TypeError, EOFError):
                reasons['unreadable_segment'] += 1
        if errors:
            reasons['invalid_manifest'] = errors
    finally:
        if connection:
            connection.close()
    # Only report complete minutes covered by sealed evidence, not the open tail.
    through = min(end, (timestamp(latest)-timedelta(seconds=2)).replace(second=0, microsecond=0)) if latest else start
    through = max(start, through)
    for at, kind in bounds:
        if start <= timestamp(at) < through:
            reasons[kind] += 1
    session = flow_window(buckets, start, through)
    windows = {str(n): flow_window(buckets, max(start, through-timedelta(minutes=n)), through) for n in (5, 15, 60)}
    if not earliest or timestamp(earliest) > start+timedelta(seconds=2):
        reasons['session_start_missing'] += 1
    stale = not latest or (end-timestamp(latest)).total_seconds() > 120
    if stale:
        reasons['stale_recording'] += 1
    if pending:
        reasons['history_building'] = pending
    if session['bad_time']:
        reasons['unreliable_trade_time'] = session['bad_time']
    if session['invalid']:
        reasons['invalid_trade'] = session['invalid']
    return dict(schema=VERSION, contract_id=contract, data_live=live,
                generated_at=now.isoformat(), session_start=start.isoformat(), through=through.isoformat(),
                session=session, windows=windows,
                classification=dict(method='projectx_reported_type', mapping={'0': 'Buy', '1': 'Sell'},
                                    aggressor_verified=False, source='https://gateway.docs.projectx.com/docs/realtime/'),
                coverage=dict(status='building' if pending else 'partial' if reasons else 'observed',
                              reasons=dict(reasons), first_received_at=earliest, last_received_at=latest,
                              stale=stale, processed_segments=processed, pending_segments=pending,
                              exchange_completeness_verified=False, sealed_only=True),
                bias='unavailable', absorption='unavailable',
                notes=['Provider trade labels are not verified aggressor sides. These are reported volumes, not a directional forecast.',
                       'Totals cover recorded deliveries only. No exchange trade ID is documented; provider redelivery cannot be ruled out.',
                       'Latest complete minutes from sealed recordings; unknown sides and unreliable timestamps are excluded from buy/sell percentages.'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    try:
        query = json.loads(sys.stdin.read(16385))
        print(json.dumps(read_session_flow(args.root, query), allow_nan=False))
    except Exception:
        print(json.dumps({'error': 'Session flow recordings unavailable'}))
        sys.exit(1)
