"""Local, deterministic reproduction of topbot-adaptive.pine, SL/TP-only revision.

Reads the existing filesystem cache. No cloud writes and no parameter search.
This is not TradingView's Pine runtime; the exact Pine source is snapshotted.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import sys
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.services.trading_day import futures_session_is_open

TZ = ZoneInfo('America/New_York')


@dataclass
class Leg:
    side: int
    entry: float
    stop: float
    target: float
    risk: float
    done: bool = False
    net_r: float = 0
    exit: float = 0
    reason: str = ''


def new_leg(side, raw, risk, reward):
    fill = raw + side * .5
    return Leg(side, fill, fill - side * risk, fill + side * reward, risk)


def settle(leg, op, hi, lo):
    if leg.done:
        return
    s = leg.side
    adverse, favorable = (lo, hi) if s == 1 else (hi, lo)
    if s * (op - leg.stop) <= 0:
        fill, reason = op, 'STOP GAP'
    elif s * (op - leg.target) >= 0:
        fill, reason = leg.target, 'TARGET'
    elif s * (adverse - leg.stop) <= 0:
        fill, reason = leg.stop, 'STOP'
    elif s * (favorable - leg.target) >= 0:
        fill, reason = leg.target, 'TARGET'
    else:
        return
    leg.done = True
    leg.exit = fill - s * .5
    leg.reason = reason
    leg.net_r = (s * (leg.exit - leg.entry) * 2 - 1.22) / (leg.risk * 2)


def checks():
    for side in (1, -1):
        leg = new_leg(side, 100, 10, 15)
        for _ in range(100):
            settle(leg, 100, 101, 99)
        assert not leg.done, 'A quiet trade must survive more than three candles'
        settle(leg, 100, 120, 80)
        assert leg.done and leg.reason == 'STOP'
        leg = new_leg(side, 100, 10, 15)
        settle(leg, 100, 120 if side == 1 else 101, 99 if side == 1 else 80)
        assert leg.reason == 'TARGET'
        leg = new_leg(side, 100, 10, 15)
        gap = 80 if side == 1 else 120
        settle(leg, gap, gap + 1, gap - 1)
        assert leg.reason == 'STOP GAP' and leg.exit == gap - side * .5


def drawdown(values, times):
    peak = worst = 0.0
    peak_t = start = end = int(times[0])
    for v, t in zip(values, times):
        if v > peak:
            peak, peak_t = float(v), int(t)
        if peak - v > worst:
            worst, start, end = peak - float(v), peak_t, int(t)
    return dict(dollars=round(worst, 2), peak_time=start, trough_time=end)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--minutes', type=int, choices=[1, 5], default=1)
    args = parser.parse_args()
    checks()
    minutes = args.minutes
    cache = Path(os.environ.get('TOPSIGNAL_DATABENTO_CACHE_DIR', ROOT / 'backend/storage/databento'))
    manifest = json.loads((cache / 'current.json').read_text())
    meta = manifest['series'][f'MNQ:{minutes}m']
    folder = cache / manifest['version_dir'] / meta['path']
    names = ['timestamp_ns', 'open_nano', 'high_nano', 'low_nano', 'close_nano', 'volume', 'instrument_id', 'raw_symbol_code']
    arrays = {k: np.load(folder / (k + '.npy'), mmap_mode='r') for k in names}
    start, end = datetime(2024, 1, 1, tzinfo=TZ), datetime(2025, 1, 1, tzinfo=TZ)
    left, right = np.searchsorted(arrays['timestamp_ns'], [int(start.timestamp()*1e9), int(end.timestamp()*1e9)])
    data = {k: np.array(v[left:right]) for k, v in arrays.items()}
    ts = data['timestamp_ns'] // 1_000_000_000
    op, hi, lo, cl = [data[k + '_nano'] / 1e9 for k in ('open', 'high', 'low', 'close')]
    volume = data['volume']
    n = len(ts)
    assert np.all(np.diff(ts) > 0)
    valid = (volume >= 0) & (np.minimum.reduce([op, hi, lo, cl]) > 0) & (lo <= np.minimum(op, cl)) & (hi >= np.maximum(op, cl))
    assert bool(np.all(valid)), 'Invalid OHLCV requires explicit data-gap review'
    dates = [datetime.fromtimestamp(int(t), TZ) for t in ts]
    holiday = {101,115,219,329,527,619,703,704,902,1128,1129,1224,1225}
    def eligible(d):
        return d.weekday() < 5 and d.month*100+d.day not in holiday and 575 <= d.hour*60+d.minute <= 930
    slots = [eligible(d) for d in dates]
    actual = set(map(int, ts))
    missing, missing_entry = [], []
    scheduled = 0
    d = start
    while d < end:
        if futures_session_is_open(d, symbol='MNQ'):
            scheduled += 1
            if int(d.timestamp()) not in actual:
                missing.append(d.isoformat())
        if eligible(d) and int(d.timestamp()) not in actual:
            missing_entry.append(d.isoformat())
        d += timedelta(minutes=minutes)
    out = cache / f'exports/tradingview/stop-target-2024-{minutes}m'
    out.mkdir(parents=True, exist_ok=True)
    source = (ROOT / 'scripts/tradingview/topbot-adaptive.pine').read_bytes()
    assert b'reason := "TIME"' not in source and b'if p.buy.done and p.sell.done' in source
    (out / 'script-tested.pine').write_bytes(source)
    digest = hashlib.sha256()
    for k in names:
        digest.update(k.encode()); digest.update(data[k].tobytes())
    coverage = dict(bars=n, first=dates[0].isoformat(), last=dates[-1].isoformat(),
        months=dict(sorted(Counter(d.strftime('%Y-%m') for d in dates).items())),
        expected_buckets=scheduled, missing_scheduled=len(missing), missing_entry=len(missing_entry),
        missing_scheduled_times=missing, missing_entry_times=missing_entry,
        series_fingerprint=meta['series_fingerprint'], source_fingerprint=manifest['source_fingerprint'],
        roll_policy=manifest['roll_policy_version'], input_sha256=digest.hexdigest(),
        roll_count=int(np.count_nonzero(np.diff(data['raw_symbol_code']))))
    (out/'coverage.json').write_text(json.dumps(coverage, indent=2))
    print('Loaded', n, 'bars; missing scheduled:', len(missing), flush=True)
    # Vectorize only the fixed 21-prior-candle features. Add in Pine's order.
    squared = np.zeros(n); recent = np.zeros(n); momentum = np.zeros(n)
    for offset in range(1, 21):
        change = cl[21-offset:n-offset] - cl[20-offset:n-offset-1]
        squared[21:] += change*change
        if offset <= 5: recent[21:] += change*change
        if offset <= 3: momentum[21:] += change
    sigma = np.sqrt(squared / 20)
    min_stop = max(.25, math.ceil(4*math.sqrt(minutes/5)*4)/4)
    max_stop = max(min_stop, math.ceil(25*math.sqrt(minutes/5)*4)/4)
    stops = np.maximum(min_stop, np.ceil(sigma*math.sqrt(3)*4)/4)
    targets = np.ceil(stops*1.5*4)/4
    cells = (momentum >= 0).astype(int)*2 + (recent/5 >= squared/20).astype(int)
    samples = deque(maxlen=240)
    pending = []
    active = opened = None
    next_entry = 0
    trades = []
    cumulative = 0
    closed = np.zeros(n); marked = np.zeros(n)
    skipped = peak_pending = 0
    # Final iteration confirms the last 2024 candle without placing a 2025 entry.
    for i in range(21, n + 1):
        j = i - 1
        if active is not None:
            settle(active, op[j], hi[j], lo[j])
            if active.done:
                pnl = round(active.side*(active.exit-active.entry)*2-1.22, 2)
                cumulative = round(cumulative+pnl, 2)
                trades.append(dict(**opened, exit_index=j, exit_time=int(ts[j])+minutes*60,
                    exit=active.exit, reason=active.reason, pnl=pnl, cumulative=cumulative,
                    held_bars=j-opened['entry_index']+1))
                active = opened = None
        closed[j] = cumulative
        marked[j] = cumulative if active is None else cumulative + active.side*(cl[j]-active.entry)*2-2*.5-1.22
        if i == n: break
        remaining = []
        for cell, buy, sell in pending:
            settle(buy, op[j], hi[j], lo[j]); settle(sell, op[j], hi[j], lo[j])
            if buy.done and sell.done:
                samples.append((cell, buy.net_r, sell.net_r))
            else:
                remaining.append((cell, buy, sell))
        pending = remaining
        if sigma[i] <= 0 or stops[i] > max_stop: continue
        cell, risk, reward = int(cells[i]), float(stops[i]), float(targets[i])
        if len(pending) < 500:
            pending.append((cell, new_leg(1, op[i], risk, reward), new_leg(-1, op[i], risk, reward)))
        else: skipped += 1
        peak_pending = max(peak_pending, len(pending))
        if len(samples) < 24: continue
        all_buy = all_sell = cell_buy = cell_sell = 0.0
        count = 0
        for sc, b, s in samples:
            all_buy += b; all_sell += s
            if sc == cell:
                cell_buy += b; cell_sell += s; count += 1
        bs = (cell_buy+20*all_buy/len(samples))/(count+20)
        ss = (cell_sell+20*all_sell/len(samples))/(count+20)
        diff = bs-ss
        if not slots[i] or active is not None or i < next_entry or abs(diff) < .05 or diff == 0:
            continue
        assert opened is None
        side = 1 if diff > 0 else -1
        active = new_leg(side, op[i], risk, reward)
        next_entry = i + 3
        opened = dict(entry_index=i, entry_time=int(ts[i]), side=side, entry=active.entry,
            stop=active.stop, target=active.target, risk=risk, buy_score=bs, sell_score=ss,
            instrument=int(data['instrument_id'][i]), symbol_code=int(data['raw_symbol_code'][i]))
    # Independent first-touch audit, using explicit long/short comparisons.
    prior_exit = prior_entry = -100
    ambiguous = cross_roll = 0
    for tr in trades:
        a, z, side = tr['entry_index'], tr['exit_index'], tr['side']
        assert a > prior_exit and a-prior_entry >= 3
        assert slots[a] and tr['entry'] == op[a]+side*.5
        prior_entry, prior_exit = a, z
        cross_roll += int(np.any(data['instrument_id'][a:z+1] != tr['instrument']))
        for k in range(a, z+1):
            if side == 1:
                gap_stop, gap_target = op[k] <= tr['stop'], op[k] >= tr['target']
                hit_stop, hit_target = lo[k] <= tr['stop'], hi[k] >= tr['target']
            else:
                gap_stop, gap_target = op[k] >= tr['stop'], op[k] <= tr['target']
                hit_stop, hit_target = hi[k] >= tr['stop'], lo[k] <= tr['target']
            if gap_stop: fill, reason = op[k], 'STOP GAP'
            elif gap_target: fill, reason = tr['target'], 'TARGET'
            elif hit_stop:
                fill, reason = tr['stop'], 'STOP'
                ambiguous += int(hit_target)
            elif hit_target: fill, reason = tr['target'], 'TARGET'
            else: continue
            assert k == z and reason == tr['reason']
            assert fill-side*.5 == tr['exit']
            assert round(side*(tr['exit']-tr['entry'])*200)-122 == round(tr['pnl']*100)
            break
        else: raise AssertionError('Trade closed without first SL/TP hit')
    profit_cents = sum(round(t['pnl']*100) for t in trades if t['pnl'] > 0)
    loss_cents = -sum(round(t['pnl']*100) for t in trades if t['pnl'] < 0)
    balance = peak = dd = 0
    for tr in trades:
        balance += round(tr['pnl']*100)
        peak = max(peak, balance); dd = max(dd, peak-balance)
        assert balance == round(tr['cumulative']*100)
    assert balance == round(cumulative*100)
    closed_dd = drawdown(closed, ts + minutes*60)
    assert dd == round(closed_dd['dollars']*100)
    summary = dict(strategy='TopBot Adaptive SL/TP only', year=2024, timeframe=f'{minutes}m',
        implementation='Local Python reproduction of the snapshotted Pine source; not TradingView Strategy Tester',
        pine_sha256=hashlib.sha256(source).hexdigest(),
        contracts=1, point_value=2, commission_per_side=.61, slippage_points_per_side=.5,
        defaults=dict(memory=240, minimum=24, advantage=.05, positive_only=False, rth_only=True,
            pending_cap=500, minimum_entry_spacing=3, minimum_stop=min_stop, maximum_stop=max_stop),
        trades=len(trades), wins=sum(t['pnl'] > 0 for t in trades), losses=sum(t['pnl'] < 0 for t in trades),
        win_rate=100*sum(t['pnl'] > 0 for t in trades)/len(trades),
        profit_factor=profit_cents/loss_cents if loss_cents else None,
        gross_profit=profit_cents/100, gross_loss=loss_cents/100, net_profit=balance/100,
        max_closed_drawdown=closed_dd, max_bar_close_liquidation_drawdown=drawdown(marked, ts+minutes*60),
        long_entries=sum(t['side'] == 1 for t in trades)+(int(opened['side']==1) if opened else 0),
        short_entries=sum(t['side'] == -1 for t in trades)+(int(opened['side']==-1) if opened else 0),
        exits=dict(Counter(t['reason'] for t in trades)), held_over_three_bars=sum(t['held_bars'] > 3 for t in trades),
        max_held_bars=max(t['held_bars'] for t in trades), open_at_end=opened,
        end_marked_pnl=round(float(marked[-1]), 2), peak_pending=peak_pending, training_skipped_at_capacity=skipped,
        audited_fills=len(trades), ambiguous_stop_first=ambiguous, trades_crossing_contract_roll=cross_roll,
        data_missing_scheduled=len(missing), data_missing_entry=len(missing_entry))
    summary = json.loads(json.dumps(summary, default=lambda value: value.item()))
    (out/'summary.json').write_text(json.dumps(summary, indent=2))
    for filename, rows in [('trades.csv', trades)]:
        with (out/filename).open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    np.savez_compressed(out/'equity.npz', timestamp=ts+minutes*60, closed=closed, marked=marked)
    monthly = []
    for month in range(1, 13):
        rows = [t for t in trades if datetime.fromtimestamp(t['exit_time']-1,TZ).month == month]
        p = sum(t['pnl'] for t in rows if t['pnl'] > 0); loss = -sum(t['pnl'] for t in rows if t['pnl'] < 0)
        monthly.append(dict(month=f'2024-{month:02}', trades=len(rows), net=round(p-loss,2),
            win_rate=100*sum(t['pnl'] > 0 for t in rows)/len(rows) if rows else None, profit_factor=p/loss if loss else None))
    with (out/'monthly.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=monthly[0].keys());w.writeheader();w.writerows(monthly)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, layout='constrained')
    chart_times = [start]+[datetime.fromtimestamp(t['exit_time'],TZ) for t in trades]
    values = np.array([0]+[t['cumulative'] for t in trades])
    axes[0].plot(chart_times, values, color='#2866b0'); axes[0].axhline(0,color='gray',lw=.7)
    axes[1].fill_between(chart_times, values-np.maximum.accumulate(values), 0, color='#c74747',alpha=.35)
    axes[0].set_ylabel('Net closed P&L ($)'); axes[1].set_ylabel('Closed-trade drawdown ($)')
    axes[0].set_title(f"Win rate {summary['win_rate']:.2f}% | Profit factor {summary['profit_factor']:.3f} | Net ${summary['net_profit']:,.2f}")
    axes[1].xaxis.set_major_locator(mdates.MonthLocator()); axes[1].xaxis.set_major_formatter(mdates.DateFormatter('%b'))
    axes[1].set_xlim(start,end)
    for ax in axes: ax.grid(alpha=.2)
    fig.suptitle(f'TopBot SL/TP only | MNQ {minutes}-minute | Full 2024 | Fixed 1 contract',fontsize=15)
    fig.savefig(out/'equity-and-drawdown.png',dpi=150);plt.close(fig)
    report = f'''TOPBOT ADAPTIVE: SL/TP-ONLY BACKTEST — FULL 2024
MNQ {minutes}-minute, fixed one contract, no compounding.
Win rate: {summary['win_rate']:.2f}% ({summary['wins']:,}/{len(trades):,})
Profit factor after costs: {summary['profit_factor']:.4f}
Net closed profit/loss: ${summary['net_profit']:,.2f}
Max closed-trade drawdown: ${closed_dd['dollars']:,.2f}
Max {minutes}-minute-close liquidation-equity drawdown: ${summary['max_bar_close_liquidation_drawdown']['dollars']:,.2f}
Open at year end: {int(opened is not None)}

METHOD AND LIMITATIONS
Local Python reproduction of the exact snapshotted Pine script, not a TradingView
platform report. Existing Databento continuous futures cache; TradingView's feed,
contract-roll policy and loaded history can differ. Started with empty learning
state on the first available 2024 candle. No parameters optimized after results.
Only prior closed candles enter features and learning. Training settles both
hypothetical legs at SL/TP. No time exit. One active simulated trade at a time.
Three-candle minimum entry spacing remains. Fixed entry-time stop and 1.5R target.
Entries weekdays 09:35–15:30 New York with the script's 2024 holiday exclusions.
Positions and training may continue overnight. No forced end-of-year liquidation.
Final 2024 candle evaluated at completion, without entering on 2025 data.
$2/point, $0.61 fee per side, 0.5 point adverse slippage per side. SL wins if
both thresholds touch in the same candle. Win rate and PF use net CLOSED trades.
Drawdown dollars use initial cumulative P&L zero; no assumed account balance.
Bar-close marked equity includes liquidation costs but is not tick-level maximum
drawdown. Percentage drawdown cannot be stated without an initial balance.
{n:,} candles across all 12 months; {len(missing)} missing scheduled buckets and
{len(missing_entry)} missing entry slots against the repository calendar.
{cross_roll} closed trades crossed a contract rollover.

VERIFICATION
{len(trades):,} fills independently rescanned for first SL/TP touch. Integer-cent
fees, P&L, ledger totals and closed drawdown verified. {ambiguous} ambiguous bars
scored stop first. {summary['held_over_three_bars']} trades lasted >3 candles;
longest {summary['max_held_bars']} candles. Synthetic checks confirm survival for
100 quiet candles and long/short target, stop, ambiguous and gap behavior.
Training-cap skips: {skipped}. No forced time exits or overlapping trades.
See summary.json, coverage.json, script-tested.pine, trades.csv and monthly.csv.
'''
    (out/'REPORT.txt').write_text(report,encoding='utf8')
    print(json.dumps(summary,indent=2));print('OUTPUT',out,flush=True)


if __name__ == '__main__':
    main()
