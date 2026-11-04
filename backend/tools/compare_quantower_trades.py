"""Offline Quantower/ProjectX consistency check; never supplies a trading signal.

CSV dates/time zone and contract must be supplied because Quantower's export
does not include them. Repeated identical prints are retained, not deduplicated.
"""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from .read_depth_history import verified_rows, read_manifest
except ImportError:
    from read_depth_history import verified_rows, read_manifest


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Comparison bounds must include a timezone.')
    return result.astimezone(timezone.utc)


def trade(at, price, size, side):
    price, size = Decimal(str(price)), Decimal(str(size))
    if not price.is_finite() or not size.is_finite() or price <= 0 or size <= 0:
        raise ValueError('Invalid trade price/size.')
    at = at.astimezone(timezone.utc)
    return (at.replace(microsecond=at.microsecond // 1000 * 1000), price, size, side)


def read_quantower(path, day, zone):
    if path.stat().st_size > 64 * 1024**2:
        raise ValueError('CSV exceeds comparison limit.')
    rows = []
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not {'Time', 'Last', 'Size', 'Aggressor'} <= set(reader.fieldnames or []):
            raise ValueError('Required trade columns missing.')
        for row in reader:
            local = datetime.strptime(day + ' ' + row['Time'].strip(), '%Y-%m-%d %I:%M:%S.%f %p')
            local = local.replace(tzinfo=ZoneInfo(zone))
            if local.utcoffset() != local.replace(fold=1).utcoffset():
                raise ValueError('Ambiguous DST timestamp: use an unambiguous export window.')
            side = row['Aggressor'].strip().lower()
            rows.append(trade(local, row['Last'], row['Size'], side if side in {'buy', 'sell'} else 'unknown'))
            if len(rows) > 200000:
                raise ValueError('Too many CSV trades.')
    return rows


def read_projectx(root, owner, contract, live, start, end):
    rows, sources, quality, seen = [], [], Counter(), set()
    total_bytes = 0
    for marker in sorted(root.rglob('*.segment.json')):
        manifest = read_manifest(marker, root)
        if manifest.get('owner_hash') != owner or manifest.get('data_live') is not live:
            continue
        if manifest.get('synthetic') is not False or manifest.get('source') != 'projectx':
            raise ValueError('Expected non-synthetic ProjectX segments.')
        total_bytes += manifest['uncompressed_bytes']
        if total_bytes > 256 * 1024**2:
            raise ValueError('Raw sample exceeds comparison budget.')
        sources.append({k: manifest[k] for k in ('capture_id', 'first_index', 'last_index', 'first_received_at', 'last_received_at', 'sha256')})
        for event in verified_rows(marker, manifest, root, raw_events=True):
            identity = (event['capture_id'], event['index'])
            if identity in seen:
                raise ValueError('Overlapping recording segments.')
            seen.add(identity)
            if event['contract_id'] != contract:
                continue
            received = stamp(event['received_at'])
            if start <= received < end and event['kind'] in {'gap', 'disconnect', 'connect', 'reset', 'roll', 'end'}:
                quality[event['kind']] += 1
            if event['kind'] != 'trade':
                continue
            if not event.get('provider_at'):
                if start <= received < end:
                    quality['missing_provider_timestamp'] += 1
                continue
            at = stamp(event['provider_at'])
            if not start <= at < end:
                continue
            if not 0 <= (received - at).total_seconds() <= 2:
                quality['provider_receipt_lag_outside_0_to_2s'] += 1
            payload = event['payload']
            typ = payload.get('type')
            side = {0: 'buy', 1: 'sell'}.get(typ, 'unknown') if type(typ) is int else 'unknown'
            rows.append(trade(at, payload['price'], payload['volume'], side))
    return rows, sources, dict(quality)


def totals(rows):
    counts, volumes = Counter(), Counter()
    for _, _, size, side in rows:
        counts[side] += 1
        volumes[side] += size
    return dict(trades=len(rows), trade_counts=dict(counts),
                contracts={s: str(volumes[s]) for s in ('buy', 'sell', 'unknown')},
                delta_contracts=str(volumes['buy'] - volumes['sell']))


def compare(qt, px, start, end):
    if end <= start:
        raise ValueError('Invalid comparison interval.')
    qt = [t for t in qt if start <= t[0] < end]
    px = [t for t in px if start <= t[0] < end]
    groups = []
    for stream in (qt, px):
        grouped = defaultdict(list)
        for at, price, size, side in stream:
            grouped[(at, price, size)].append(side)
        groups.append(grouped)
    matched = agreed = disagreed = unknown = ambiguous = side_matched = 0
    examples = []
    for key in groups[0].keys() & groups[1].keys():
        left, right = groups[0][key], groups[1][key]
        matched += min(len(left), len(right))
        side_matched += sum((Counter(left) & Counter(right)).values())
        if len(left) != 1 or len(right) != 1:
            ambiguous += 1
            continue
        if 'unknown' in (left[0], right[0]):
            unknown += 1
        elif left[0] == right[0]:
            agreed += 1
        else:
            disagreed += 1
            if len(examples) < 10:
                examples.append(dict(time=key[0].isoformat(), price=str(key[1]), size=str(key[2]), quantower=left[0], projectx=right[0]))
    minutes = sorted({t[0].replace(second=0, microsecond=0) for t in qt + px})
    return dict(start=start.isoformat(), end_exclusive=end.isoformat(),
                quantower=totals(qt), projectx=totals(px),
                exact_timestamp_price_size_matched_occurrences=matched,
                exact_timestamp_price_size_side_multiset_matches=side_matched,
                quantower_unmatched_occurrences=len(qt)-matched,
                projectx_unmatched_occurrences=len(px)-matched,
                ambiguous_matching_keys=ambiguous,
                unique_pair_label_agreements=agreed, unique_pair_label_disagreements=disagreed,
                unique_pair_unknown_labels=unknown, disagreement_examples=examples,
                minutes=[dict(minute=m.isoformat(),
                              quantower=totals([t for t in qt if t[0].replace(second=0, microsecond=0) == m]),
                              projectx=totals([t for t in px if t[0].replace(second=0, microsecond=0) == m])) for m in minutes])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('csv', 'date', 'timezone', 'root', 'owner', 'contract', 'start', 'end', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    start, end = stamp(args.start), stamp(args.end)
    csv_path = Path(args.csv)
    qt = read_quantower(csv_path, args.date, args.timezone)
    px, sources, quality = read_projectx(Path(args.root), args.owner, args.contract, args.live, start, end)
    result = compare(qt, px, start, end)
    if not result['quantower']['trades'] or not result['projectx']['trades']:
        raise ValueError('Both sources must contain trades in the comparison interval.')
    result.update(schema='quantower-projectx-comparison-v1', contract=args.contract,
                  csv_sha256=hashlib.sha256(csv_path.read_bytes()).hexdigest(),
                  csv_date=args.date, csv_timezone=args.timezone,
                  csv_first_trade=min(t[0] for t in qt).isoformat(),
                  csv_last_trade=max(t[0] for t in qt).isoformat(),
                  sources=sources, recording_quality_events=quality,
                  note='Cross-platform consistency only. Both platforms use ProjectX. Exact matches are candidate correspondences, not exchange trade-ID verification. Side multiset matches preserve duplicate counts but do not resolve individual identity. No session bias or strategy changes.')
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in {'sources', 'minutes', 'disagreement_examples'}}, indent=2))


if __name__ == '__main__':
    main()
