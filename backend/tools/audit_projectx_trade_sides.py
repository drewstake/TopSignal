"""Read-only, bounded comparison of provider trade labels with preceding quotes.

Run against immutable raw recordings. Agreement is corroboration, not proof of
exchange aggressor semantics or completeness. No credentials or broker calls.
"""
import argparse
from collections import Counter
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def audit(root, day, owner):
    entries = []
    for marker in sorted((Path(root) / day).glob('*/*.segment.json')):
        m = json.loads(marker.read_text())
        if m.get('owner_hash') == owner and m.get('synthetic') is False and m.get('source') == 'projectx':
            entries.append((m['first_received_at'], marker, m))
    entries.sort()
    # Deterministic samples distributed across the recorded day, plus latest.
    selected = [entries[i] for i in sorted({int((len(entries)-1)*n/7) for n in range(8)})] if entries else []
    counts, matrix, volumes, reasons = Counter(), Counter(), Counter(), Counter()
    sources = []
    for _, marker, m in selected:
        if Path(m['file']).name != m['file'] or m['uncompressed_bytes'] > 17*1024**2:
            raise ValueError('Invalid segment bounds')
        data = (marker.parent / m['file']).read_bytes()
        if len(data) != m['bytes'] or hashlib.sha256(data).hexdigest() != m['sha256']:
            raise ValueError('Compressed checksum mismatch')
        raw = gzip.decompress(data)
        if len(raw) != m['uncompressed_bytes'] or hashlib.sha256(raw).hexdigest() != m['uncompressed_sha256']:
            raise ValueError('Raw checksum mismatch')
        quote = None
        total = 0
        for line in raw.splitlines():
            e = json.loads(line)
            if (e['owner_hash'] != owner or e['capture_id'] != m['capture_id']
                    or e['index'] != m['first_index']+total or e['synthetic'] is not False):
                raise ValueError('Envelope mismatch')
            total += 1
            p = e['payload']
            if e['kind'] in {'connect', 'disconnect', 'gap', 'reset', 'roll', 'end'}:
                quote = None
            if e['kind'] == 'quote':
                quote = e
            if e['kind'] != 'trade':
                continue
            counts[str(p.get('type'))] += 1
            if not quote or quote['contract_id'] != e['contract_id'] or not e.get('provider_at') or not quote.get('provider_at'):
                reasons['no_preceding_quote'] += 1
                continue
            q = quote['payload']
            bid, ask = q.get('bestBid'), q.get('bestAsk')
            receive_age = (stamp(e['received_at'])-stamp(quote['received_at'])).total_seconds()
            provider_age = (stamp(e['provider_at'])-stamp(quote['provider_at'])).total_seconds()
            lag = (stamp(e['received_at'])-stamp(e['provider_at'])).total_seconds()
            if not all(isinstance(x, (float, int)) for x in (bid, ask)) or not 0 < bid < ask:
                reasons['invalid_quote'] += 1
                continue
            if not (0 <= receive_age <= .5 and 0 <= provider_age <= .5 and 0 <= lag <= 2):
                reasons['stale_or_reversed_time'] += 1
                continue
            location = 'ask_or_above' if p['price'] >= ask else 'bid_or_below' if p['price'] <= bid else 'inside'
            key = str(p.get('type')) + ':' + location
            matrix[key] += 1
            volumes[key] += p['volume']
        if total != m['events']:
            raise ValueError('Event count mismatch')
        sources.append({k: m[k] for k in ('capture_id', 'first_index', 'last_index', 'first_received_at', 'last_received_at', 'sha256')})
    return dict(schema='projectx-trade-side-audit-v1', sources=sources, trade_types=dict(counts),
                preceding_quote_trade_counts=dict(matrix), preceding_quote_volume=dict(volumes), excluded=dict(reasons),
                method='Previously received same-contract uncrossed quote; receipt/provider age <=500ms; provider lag 0..2s. No future quotes.',
                conclusion='Observational corroboration only; not a provider guarantee of aggressor classification or exchange completeness.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--day', required=True)
    parser.add_argument('--owner', required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.root, args.day, args.owner), indent=2))
