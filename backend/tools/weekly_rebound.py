"""Offline first-qualification recap from a frozen execution export and copied MNQ recordings."""
import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, timedelta
import gzip
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys

import trade_review
from depth_heatmap import BOUNDARY_KINDS, Inventory, session_key, session_start_ms, unpack
from read_depth_history import verified_rows

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.imbalance_study import EpisodeMatches, batch_before, broker_ids, saved_direction
from app.services.trading_day import trading_day_bounds_utc


def stamp(value):
    return trade_review.timestamp(value).timestamp()


def checked_attempts(data, owner, account, config):
    attempts, plans = [], {}
    for a in data['bot_order_attempts']:
        if (a['user_id'], a['account_id'], a['bot_config_id']) != (owner, account, config):
            continue
        request = a['raw_request'] or {}
        signal = (request.get('entryEvidence') or {}).get('signal') or {}
        snapshot = request.get('strategySnapshot') or {}
        rules = snapshot.get('rules') or {}
        version = snapshot.get('strategy_version_id')
        direction = saved_direction(rules.get('entry') or {})
        if version:
            plan = (direction, rules.get('exit') or {}, snapshot.get('quantity'))
            if version in plans and plans[version] != plan:
                raise ValueError('Inconsistent saved rules for version ' + version)
            plans[version] = plan
        attempts.append(dict(a, runner=(request.get('timeExit') or {}).get('runner') or {},
                             episode=signal.get('episode_at'), capture=(signal.get('evidence') or {}).get('capture_id'),
                             at=stamp(a['created_at']), version=version, direction=direction,
                             reason=a.get('rejection_reason')))
    return attempts, plans


def closed_outcome(match, position, runner, fills):
    result = dict(broker_net=None, broker_outcome=None, exit_kind=None, exit_order_ids=[], exit_fill_ids=[])
    if not match['entry_id'] or not position:
        return result
    entry_ids = set(match['entry_fill_ids'])
    exit_ids = set(position['fill_ids']) - entry_ids
    if (not entry_ids <= set(position['fill_ids']) or not exit_ids
            or exit_ids != set(broker_ids(runner, 'exit_fill_id'))):
        return result
    orders = {str(fills[i]['order_id']) for i in exit_ids}
    if orders != set(broker_ids(runner, 'exit_order_id')):
        return result
    net = position['net']
    result.update(broker_net=net, broker_outcome='unknown' if net is None else
                  'win' if net > 0 else 'loss' if net < 0 else 'flat',
                  exit_order_ids=sorted(orders), exit_fill_ids=sorted(exit_ids),
                  exit_kind='verified_target' if orders <= set(broker_ids(runner, 'target_order_id'))
                  else 'other_profitable_exit' if net is not None and net > 0 else 'other_exit')
    return result


def measure(at, quotes, gaps, prints, capture_ok=True):
    """quotes are receipt-time frames (seconds, bid, ask). Each half includes its prevailing bid."""
    times = sorted(quotes)
    start, mid, end = (bisect_right(times, t)-1 for t in (at-30, at-15, at))
    forward = bisect_right(times, at+60)-1
    result = dict(condition='unknown', reason=None, reported_buy=None, reported_sell=None,
                  first_low=None, final_low=None, midpoint_change_60s=None)
    if end >= 0 and at-times[end] <= 2 and forward >= 0 and 0 <= at+60-times[forward] <= 2:
        result['midpoint_change_60s'] = (sum(quotes[times[forward]]) - sum(quotes[times[end]]))/2
    if (start < 0 or at-30-times[start] > 2 or at-times[end] > 2
            or any(y-x > 2 for x, y in zip(times[start:end], times[start+1:end+1]))
            or any(lo <= at and hi > at-30 for lo, hi in gaps)):
        result['reason'] = 'missing or interrupted bid window'
        return result
    events = [e for e in prints if at-30 < e[0] <= at]
    if not capture_ok or not events or any(kind != 'trade' or type(side) is not int or side not in (0, 1)
            or type(volume) not in (int, float) or not math.isfinite(volume) or volume <= 0
            for _, kind, side, volume in events):
        result['reason'] = 'missing or interrupted print window'
        return result
    buy, sell = (sum(v for _, _, side, v in events if side == which) for which in (0, 1))
    first = min(quotes[t][0] for t in times[start:mid+1])
    last = min(quotes[t][0] for t in times[mid:end+1])
    result.update(condition='flagged' if sell > buy and last >= first else 'unflagged',
                  reported_buy=buy, reported_sell=sell, first_low=first, final_low=last)
    return result


def print_coverage(parts, at):
    return bool(parts and parts[0][0] <= at-30 and parts[-1][1] >= at and
                len({p[2] for p in parts}) == 1 and all(
                    x[4]+1 == y[3] and 0 <= y[0]-x[1] <= 2 for x, y in zip(parts, parts[1:])))


def observations(cache, owner, contract, start, end):
    from app.services.topbot_replay_index import Capture
    from track_imbalances import captures
    for path in captures(cache, owner, contract, start):
        with closing(Capture(path)) as cap:
            cap.check_scope(owner, contract)
            anchor = batch_before(cap, start)
            for _, _, rows in cap.batches(*anchor):
                for row in rows:
                    at = stamp(row['received_at'])
                    if at > end:
                        break
                    if at >= start:
                        for e in row['transitions']:
                            if e['kind'] == 'episode_qualified':
                                yield dict(capture=cap.meta['capture_id'], episode=e['episode_at'],
                                           contract=contract, at=at, heavy=e['direction'], source='Worker')
                if at > end:
                    break


class CopiedMarket:
    def __init__(self, cache, owner, start):
        self.root = cache / 'depth-recordings-v1'
        self.owner_hash = hashlib.sha256(owner.encode()).hexdigest()
        self.sessions = cache / 'depth-heatmap-v1' / self.owner_hash / 'sessions'
        inventory = Inventory(self.root)
        self.parts = [p for p in inventory.scan(since_ms=int((start-90)*1000)) if p['owner'] == self.owner_hash]
        if inventory.errors:
            raise ValueError('Invalid raw segment manifest in the copied inventory')

    def __call__(self, row):
        at, contract = row['at'], row['contract']
        a, b = int((at-33)*1000), int((at+60)*1000)
        quotes, gaps, captures = {}, [], set()
        for day in {session_key(session_start_ms(t)) for t in (a, b)}:
            path = self.sessions / (day+'.sqlite3')
            if not path.exists():
                continue
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
                for cap, first, last, gap, notes, blob in db.execute("""SELECT c.capture_id,c.start_ms,c.end_ms,c.gaps,c.notes,t.data
                    FROM chunks c JOIN tier_data t ON t.chunk_id=c.id AND t.kind='depth0'
                    WHERE c.contract_id=? AND c.data_live=0 AND c.start_ms<=? AND c.end_ms>=?""", (contract, b, a)):
                    if first <= at*1000 and last >= (at-30)*1000:
                        captures.add(cap)
                    gaps.extend((x/1000, y/1000) for x, y, _ in json.loads(gap))
                    gaps.extend((n[0]/1000, (n[0]+1)/1000) for n in json.loads(notes))
                    _, cols = unpack(blob)
                    offset = 0
                    for ts, nb, na in zip(cols['time'], cols['nb'], cols['na']):
                        if nb and na and a <= ts <= b:
                            quote = cols['tick'][offset]*.25, cols['tick'][offset+nb]*.25
                            if quotes.setdefault(ts/1000, quote) != quote:
                                raise ValueError('Conflicting BBO frames')
                        offset += nb+na
        parts, prints = [], []
        for p in self.parts:
            if p['data_live'] or p['capture'] not in captures or p['first_ms'] > at*1000+2000 or p['last_ms'] < (at-32)*1000:
                continue
            m = p['manifest']
            parts.append((stamp(m['first_received_at']), stamp(m['last_received_at']), p['capture'],
                          m['first_index'], m['last_index']))
            for e in verified_rows(p['marker'], m, self.root, raw_events=True):
                t = stamp(e['received_at'])
                if at-30 < t <= at:
                    if e['contract_id'] != contract or e['kind'] in BOUNDARY_KINDS:
                        prints.append((t, 'boundary', None, None))
                    elif e['kind'] == 'trade':
                        prints.append((t, 'trade', e['payload'].get('type'), e['payload'].get('volume')))
        return measure(at, quotes, gaps, prints, len(captures) == 1 and print_coverage(sorted(parts), at))


def recap(data, signals, market, owner, account, config, start, end):
    attempts, plans = checked_attempts(data, owner, account, config)
    positions, fills, _, diagnostics, accounts = trade_review.ledger(data, owner, trade_review.timestamp(end), [account])
    if accounts[account].get('provider_simulated') is not True:
        raise ValueError('Expected an owned practice account')
    for f in fills:
        f['voided'] = (f.get('raw_payload') or {}).get('voided')
    matches = EpisodeMatches(attempts, fills, owner, account, config)
    broker = {str(f['source_trade_id']): f for f in fills}
    runners = {a['id']: a['runner'] for a in attempts}
    closed = {p['bot_entry_ids'][0]: p for p in positions if p['attribution'] == 'bot' and len(p['bot_entry_ids']) == 1}
    rows, counts = [], defaultdict(Counter)
    unique = {}
    for s in signals:
        if s['source'] != 'Worker' or not start <= s['at'] <= end:
            continue
        key = s['contract'], s['capture'], s['episode']
        if key in unique and s != unique[key]:
            raise ValueError('Conflicting first qualifications')
        unique[key] = s
    for key, s in sorted(unique.items(), key=lambda item: item[1]['at']):
        m = matches.lookup(*key, s['at'], s['heavy'])
        row = dict(s, session=trade_review.session(s['at']), **m)
        row.update(closed_outcome(m, closed.get(m['entry_id']), runners.get(m['entry_id'], {}), broker))
        direction, plan, _ = plans.get(m['version'], (None, {}, None))
        if (s['heavy'] == 1 and direction == 'following' and plan.get('exit_mode') == 'fixed'
                and plan.get('initial_stop_points') == 25 and plan.get('take_profit_points') == 40):
            row.update(market(s))
            split = counts[row['session'], s['contract'], m['version'], row['condition']]
            split['qualifications'] += 1
            if not m['entry_id']:
                split['without_verified_fill'] += 1
            change = row['midpoint_change_60s']
            split['midpoint_unknown' if change is None else 'higher' if change > 0 else 'flat_or_lower'] += 1
            if m['entry_id']:
                split['filled'] += 1
                split[row['broker_outcome'] if row['broker_outcome'] in ('win','loss','flat') else 'broker_unknown'] += 1
                split[row['exit_kind'] or 'exit_unknown'] += 1
        rows.append(row)
    metrics = ('qualifications','filled','without_verified_fill','higher','flat_or_lower','midpoint_unknown',
               'win','loss','flat','broker_unknown','verified_target','other_profitable_exit','other_exit','exit_unknown')
    summary = [dict(session=d, contract=c, version=v, condition=flag, **{k: counts[d,c,v,flag][k] for k in metrics})
               for d,c,v in sorted({k[:3] for k in counts}) for flag in ('flagged','unflagged','unknown')]
    linked = {r['entry_id'] for r in rows if r['entry_id']}
    return dict(qualifications=rows, splits=summary, broker_diagnostics=diagnostics,
                unknown_setup_heavy_bids=sum(r['heavy']==1 and r['version'] is None for r in rows),
                unmatched_broker_entries=sorted(i for i,p in closed.items() if i in runners
                    and start <= stamp(p['open_utc']) <= end and i not in linked))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--week-ending', type=date.fromisoformat, required=True)
    parser.add_argument('--cache', type=Path, default=Path(__file__).resolve().parents[1]/'storage/databento')
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--account', type=int, default=29593203)
    parser.add_argument('--config', type=int, default=40)
    args = parser.parse_args()
    day = args.week_ending
    if day.weekday() != 4:
        parser.error('Week ending must be a Friday futures session date')
    folder = args.cache/'trade-review-v1'/f'week-{day:%Y%m%d}'
    bundle = args.bundle or folder/'evidence'
    manifest = json.loads((bundle/'manifest.json').read_bytes())
    path = bundle/'execution.json.gz'
    if trade_review.sha(path) != manifest['files'][path.name]['sha256']:
        raise ValueError('Execution export checksum mismatch')
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        data = json.load(f)
    owner = manifest['owner']
    if data['_owner'] != owner:
        raise ValueError('Execution export owner mismatch')
    start = trading_day_bounds_utc(day-timedelta(days=4))[0].timestamp()
    end = min(stamp(data['_snapshot_at']), trading_day_bounds_utc(day)[1].timestamp())
    if end < start:
        raise ValueError('Execution export predates review week')
    configs = [c for c in data['bot_configs'] if (c['id'],c['user_id'],c['account_id']) == (args.config,owner,args.account)]
    if len(configs) != 1:
        raise ValueError('Expected one owned study configuration')
    contracts = {configs[0]['contract_id']} | {a['contract_id'] for a in data['bot_order_attempts']
        if (a['user_id'],a['bot_config_id'],a['account_id']) == (owner,args.config,args.account) and start <= stamp(a['created_at']) <= end}
    if not (args.cache/'topbot-worker-evidence-v1'/hashlib.sha256(owner.encode()).hexdigest()).is_dir():
        raise ValueError('Copied owner-scoped worker evidence is unavailable')
    signals = (s for c in sorted(contracts) for s in observations(args.cache, owner, c, start, end))
    report = recap(data, signals, CopiedMarket(args.cache, owner, start), owner,args.account,args.config,start,end)
    report.update(owner=owner, account=args.account, config=args.config, week_ending=day.isoformat(),
                  start=start, through=end, execution_sha256=manifest['files'][path.name]['sha256'])
    output = args.output or folder/'rebound-recap.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as f:
        json.dump(report, f, indent=2, allow_nan=False)
    print(json.dumps(report['splits'], indent=2))
    print(f"{len(report['qualifications'])} worker qualifications. {output}")


if __name__ == '__main__':
    main()
