"""Offline regression against the frozen November 20 worker and broker evidence."""
import argparse
import base64
from bisect import bisect_left, bisect_right
from collections import Counter
import csv
import gzip
import hashlib
import json
from pathlib import Path
import struct
import zlib

from weekly_rebound import measure, print_coverage, recap, stamp
from trade_review import sha


def replay(folder):
    manifest = json.loads((folder/'manifest.json').read_bytes())
    owner, account, contract = manifest['owner'], manifest['account'], manifest['contract']

    def load(name):
        path = folder/(name+'.json.gz')
        if sha(path) != manifest['inputs'][path.name]['sha256']:
            raise ValueError('Input checksum mismatch: '+name)
        return json.loads(gzip.decompress(path.read_bytes()))

    def csv_rows(name):
        with (folder/name).open(encoding='utf-8', newline='') as f:
            return list(csv.DictReader(f))

    executions, market = load('execution'), load('market')
    capture_ids = {c[0][:8]: c[0] for c in market['workers']}
    anchors = {(s['capture'], s['episode']): s for s in load('records')['anchors'] if s['source']=='Worker' and s['side']}
    signals = []
    for name in sorted(manifest['inputs']):
        if not name.startswith(('transitions-', 'post-')):
            continue
        cap = capture_ids[name.split('-')[1]]
        for _, _, _, at, e in load(name.removesuffix('.json.gz'))['signals']:
            if e['kind'] == 'episode_qualified':
                signals.append(dict(source='Worker', capture=cap, contract=contract,
                                    episode=e['episode_at'], at=stamp(at), heavy=e['direction']))
    quotes = {}
    for blob in load('bbo').values():
        for t, b, a, _, _ in struct.iter_unpack('<qiiHH',zlib.decompress(base64.b64decode(blob))):
            quotes[t/1000] = b/4, a/4
    times = sorted(quotes)
    gaps = []
    for day, chunks in load('indices').items():
        if day[:4] == '2026':
            for chunk in chunks:
                if chunk['contract_id'] == contract:
                    gaps.extend((a/1000,b/1000) for a,b,_ in json.loads(chunk['gaps']))
                    gaps.extend((n[0]/1000,(n[0]+1)/1000) for n in json.loads(chunk['notes']))
    print_path = folder/'rebound-review/raw-print-windows.json.gz'
    if sha(print_path) != 'c3d3b4d8fcc7d6a97e516da9e089aff108b6b14111a7f1678a809a6eeaec8a3f':
        raise ValueError('November 20 print projection checksum mismatch')
    raw = json.loads(gzip.decompress(print_path.read_bytes()))
    projected = []
    keys = {s['segment_key'] for s in market['segments']}
    for key, s in raw.items():
        m = s['manifest']
        assert key in keys and key == f"{m['capture_id']}:{m['first_index']}:{m['sha256']}"
        assert m['owner_hash'] == hashlib.sha256(owner.encode()).hexdigest() and m['synthetic'] is False
        projected.append((stamp(m['first_received_at']),stamp(m['last_received_at']),m['capture_id'],
                          m['first_index'],m['last_index'],s['rows']))

    def window(row):
        at = row['at']
        parts = sorted(p for p in projected if p[0] <= at+2 and p[1] >= at-32)
        prints = [(stamp(e[1]),e[3],e[4],e[5]) for p in parts for e in p[5]]
        q = {t:quotes[t] for t in times[bisect_left(times,at-33):bisect_right(times,at+60)]}
        return measure(at, q, gaps, prints, print_coverage(parts,at))

    report = recap(executions, signals, window, owner, account, 40, stamp(manifest['start']),stamp(manifest['cutoff']))
    rows = report['qualifications']
    matched = [r for r in rows if r['entry_id']]
    reference = csv_rows('trades.csv')
    assert len(matched) == len({r['entry_id'] for r in matched}) == len(reference) == 175
    actual = {r['entry_id']:r for r in matched}
    provider = {str(f['source_trade_id']):str(f['order_id']) for f in executions['projectx_trade_events']
                if f['user_id']==owner and f['account_id']==account}
    seen = set()
    for t in reference:
        r = actual[int(t['entry'])]
        ids = t['fill_ids'].split('|')
        assert set(r['entry_fill_ids']) | set(r['exit_fill_ids']) == set(ids)
        assert all(provider[i] == r['entry_order_id'] for i in r['entry_fill_ids'])
        assert set(r['exit_order_ids']) == {provider[i] for i in r['exit_fill_ids']}
        assert r['version'] == t['version'] and r['session'] == t['session'] and r['contract'] == t['contract']
        assert not seen.intersection(ids)
        seen.update(ids)
    assert not report['unmatched_broker_entries']
    friday = [r for r in matched if r['session']=='2026-11-20']
    assert Counter((r['heavy'],r['order_side']) for r in friday) == {(1,1):32,(-1,-1):5}
    assert sum(r['heavy']==-r['order_side'] for r in matched) == 138
    scored = {str(anchors[r['capture'],r['episode']]['id']):r for r in rows if 'condition' in r}
    original = csv_rows('rebound-review/heavy-bid-setups.csv')
    assert len(scored)==19 and set(scored)=={r['anchor'] for r in original}
    for old in original:
        new = scored[old['anchor']]
        assert new['condition'] == ('flagged' if old['held_under_selling']=='True' else 'unflagged')
        assert new['midpoint_change_60s'] == float(old['forward_60'])
        assert [new[k] for k in ('reported_buy','reported_sell','first_low','final_low')] == [
            float(old[k]) for k in ('reported_buy_30','reported_sell_30','bid_low_first_15','bid_low_final_15')]
        assert new['entry_id'] == (int(old['entry']) if old['entry'] else None)
    flagged = [r for r in scored.values() if r['entry_id'] and r['condition']=='flagged']
    assert sum(bool(r['entry_id']) for r in scored.values())==12 and len(flagged)==4
    assert all(r['midpoint_change_60s']>0 for r in flagged) and sum(r['broker_outcome']=='win' for r in flagged)==3
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = replay(args.folder)
    if args.output:
        with args.output.open('x', encoding='utf-8') as f:
            json.dump(report, f, indent=2, allow_nan=False)
    print(json.dumps(report['splits'], indent=2))
