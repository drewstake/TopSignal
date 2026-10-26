"""Independently audit JS fills/metrics and render a local backtest report."""
import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
cache = Path(os.environ.get('TOPSIGNAL_DATABENTO_CACHE_DIR', ROOT / 'backend/storage/databento'))
out = cache / 'exports/fxreplay/adaptive-2024-backtest'
bars = json.loads((out/'bars.json').read_text())
trades = json.loads((out/'trades.json').read_text())
summary = json.loads((out/'summary.json').read_text())
coverage = json.loads((out/'coverage.json').read_text())
by_time = {b['t']: i for i,b in enumerate(bars)}
tz = ZoneInfo('America/New_York')
dec = lambda n: Decimal(str(n))
monthly = defaultdict(list)
exit_counts = Counter()
ambiguous = 0
balance = peak = drawdown = Decimal(0)
profit = loss = Decimal(0)
prior_entry = 0
for tr in trades:
    assert tr['entryTime']-prior_entry >= 900
    prior_entry=tr['entryTime']
    idx=by_time[tr['entryTime']]
    side=tr['side']
    assert dec(tr['entry']) == dec(bars[idx]['o']) + side*Decimal('0.5')
    assert 4 <= tr['risk'] <= 25
    expected=None
    for age in range(3):
        b=bars[idx+age]
        assert b['t'] == tr['entryTime']+age*300
        assert b['instrument'] == tr['instrument']
        adverse=b['l'] if side==1 else b['h']
        favorable=b['h'] if side==1 else b['l']
        if side*(b['o']-tr['stop']) <= 0: price,reason=b['o'],'STOP GAP'
        elif side*(b['o']-tr['target']) >= 0: price,reason=tr['target'],'TARGET'
        elif side*(adverse-tr['stop']) <= 0:
            price,reason=tr['stop'],'STOP'
            if side*(favorable-tr['target']) >= 0: ambiguous+=1
        elif side*(favorable-tr['target']) >= 0: price,reason=tr['target'],'TARGET'
        elif age==2: price,reason=b['c'],'TIME'
        else: continue
        expected=dec(price)-side*Decimal('0.5')
        assert tr['exitTime']==b['t']+300
        assert tr['reason']==reason
        break
    assert expected == dec(tr['exit'])
    pnl=(expected-dec(tr['entry']))*side*2-Decimal('1.22')
    assert pnl==dec(tr['pnl'])
    balance+=pnl
    assert balance==dec(tr['cumulative'])
    peak=max(peak,balance)
    drawdown=max(drawdown,peak-balance)
    if pnl>0: profit+=pnl
    else: loss-=pnl
    monthly[datetime.fromtimestamp(tr['exitTime'],tz).strftime('%Y-%m')].append(tr)
    exit_counts[tr['reason']]+=1
assert abs(float(drawdown)-summary['maxClosedTradeDrawdown']['dollars']) < 1e-7
assert abs(float(profit/loss)-summary['profitFactor']) < 1e-10
assert abs(float(balance)-summary['netProfit']) < 1e-7
assert summary['unresolved']==0

monthly_rows=[]
for month, rows in sorted(monthly.items()):
    pos=sum(dec(t['pnl']) for t in rows if t['pnl']>0)
    neg=-sum(dec(t['pnl']) for t in rows if t['pnl']<0)
    monthly_rows.append(dict(month=month,trades=len(rows),win_rate=round(100*sum(t['pnl']>0 for t in rows)/len(rows),2),
        net_pnl=float(pos-neg),profit_factor=round(float(pos/neg),3) if neg else None))
with (out/'monthly.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=monthly_rows[0].keys());w.writeheader();w.writerows(monthly_rows)
audit=dict(fills_independently_verified=len(trades),unresolved=0,same_bar_stop_target_stop_first=ambiguous,
           exit_reasons=dict(exit_counts),net_profit=float(balance),profit_factor=float(profit/loss),
           max_closed_trade_drawdown=float(drawdown),monthly=monthly_rows)
(out/'audit.json').write_text(json.dumps(audit,indent=2))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

times=[datetime.fromtimestamp(bars[0]['t'],tz)]+[datetime.fromtimestamp(t['exitTime'],tz) for t in trades]
pnl=np.array([0]+[t['cumulative'] for t in trades])
dd=pnl-np.maximum.accumulate(pnl)
fig,(ax,dx)=plt.subplots(2,1,figsize=(12,7),sharex=True,gridspec_kw={'height_ratios':[2,1]},layout='constrained')
fig.suptitle('TopBot 2024 Adaptive | MNQ 5-minute | fixed 1 contract',fontsize=17,fontweight='bold')
ax.plot(times,pnl,color='#2166ac',linewidth=1.5)
ax.axhline(0,color='#777777',linewidth=.8)
ax.set_ylabel('Cumulative net P&L ($)')
ax.set_title(f"{summary['trades']:,} trades   |   Win rate {summary['winRate']:.2f}%   |   Profit factor {summary['profitFactor']:.3f}   |   Net ${float(balance):,.2f}",fontsize=11)
dx.fill_between(times,dd,0,color='#c44e52',alpha=.3)
dx.plot(times,dd,color='#c44e52',linewidth=1)
dx.set_ylabel('Closed-trade drawdown ($)')
dx.xaxis.set_major_locator(mdates.MonthLocator())
dx.xaxis.set_major_formatter(mdates.DateFormatter('%b'))
dx.set_xlim(datetime(2024,1,1,tzinfo=tz),datetime(2024,12,31,23,59,tzinfo=tz))
for axis in (ax,dx):
    axis.grid(alpha=.2)
    axis.spines[['top','right']].set_visible(False)
dx.set_xlabel('2024 | $0.61 commission + 0.5 point adverse slippage per side | Stop first if SL and TP touch in the same bar')
fig.savefig(out/'equity-and-drawdown.png',dpi=160)
plt.close(fig)

peak_date=datetime.fromtimestamp(summary['maxClosedTradeDrawdown']['peakTime'],tz).isoformat()
trough_date=datetime.fromtimestamp(summary['maxClosedTradeDrawdown']['troughTime'],tz).isoformat()
report=f'''TOPBOT 2024 ADAPTIVE — FULL-YEAR BACKTEST

Result: net loss of ${-float(balance):,.2f}, fixed one MNQ contract.
Win rate: {summary['winRate']:.2f}% ({summary['wins']} wins / {summary['trades']} trades)
Profit factor after costs: {summary['profitFactor']:.4f}
Maximum closed-trade drawdown: ${float(drawdown):,.2f}
Maximum 5-minute close liquidation-equity drawdown: ${summary['maxFiveMinuteLiquidationDrawdown']['dollars']:,.2f}
Drawdown peak: {peak_date}
Drawdown trough: {trough_date}
Average net trade: ${summary['averageTrade']:.2f}
Winning-trade profit after costs: ${float(profit):,.2f}
Losing-trade loss after costs: ${float(loss):,.2f}
Long entries: {summary['buys']}; short entries: {summary['sells']}

METHOD
Executed the actual JavaScript computational core from the FXReplay script.
This was a local Databento backtest, not an FXReplay platform-generated report.
No parameters were optimized or changed after observing results.
Default settings: 240 recent paths, 24 settled-path warmup, 0.05R direction
advantage, positive-payoff requirement OFF, regular-hours entries ON.
Entries: weekdays 09:35-15:30 New York, excluding the script's holiday list.
Holding period: first SL/TP or third candle close. All {ambiguous} same-bar
SL/TP ambiguities were scored stop first. Gap handling matches the script.
Fixed 1 MNQ, $2 per point, $0.61 commission and 0.5 point adverse slippage
per side. No compounding, account-level margin stops, or percentage-DD assumption.
Win rate uses strictly positive net closed trades / all closed trades.
Profit factor uses net winning dollars / absolute net losing dollars.
Drawdown is the largest peak-to-trough fall, with initial cumulative P&L zero.
5-minute marked drawdown includes estimated liquidation costs; neither measure
claims exact tick-by-tick intrabar equity drawdown.

DATA AND CAUSALITY
71,013 five-minute candles spanning January 1 evening through December 31, 2024.
All 12 months present. Zero missing scheduled buckets against the repository's
MNQ calendar and zero buckets with fewer than five observed one-minute records.
Rolling contract selected from the previous completed session's volume;
four contract changes occurred outside entry hours. No trade crossed a roll.
Initial learning state empty on the first available 2024 candle; training also
uses overnight bars. State persists across sessions throughout this run.
Only completed paths enter training; no 2026 frozen model or future candles.
FXReplay prices, rollover rules and history reloads may produce different results.

VALIDATION AND FILES
All {len(trades)} fills, fees, ledger balances and aggregate statistics independently
recalculated in Python; zero unresolved/open/gap-invalidated trades.
summary.json: metrics and exact settings/script hash.
coverage.json: dataset and series fingerprints, SHA-256 and coverage.
script-tested.js: exact source snapshot.
trades.csv: complete trade ledger; timestamps are UTC Unix seconds.
monthly.csv: monthly net results, win rates and profit factors.
equity-and-drawdown.png: closed-trade equity and drawdown chart.
'''
(out/'REPORT.txt').write_text(report,encoding='utf-8')
print(json.dumps(audit,indent=2))
print('REPORT',out/'REPORT.txt')
