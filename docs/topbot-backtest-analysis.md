# TopBot replay: trade analysis

The Backtest result now includes direction comparisons, winner/loser holding-time
statistics, entry-hour/weekday/year groups, exit reasons, duration groups, and a
filterable, sortable trade ledger. Select all trades, longs, or shorts for the
detailed views. CSV exports include every trade in that direction, regardless of
ledger pagination; JSON exports include the selected summary, run fingerprint,
configuration, execution assumptions, and metric definitions.

The analysis is computed from the complete returned trade ledger. Existing saved
replays can use the new views without changing their strategy or fills. Missing
timing is excluded from duration statistics only. Empty groups and ratios without
the required denominator display a dash rather than a fabricated zero.

## Current v4: long bias with stricter shorts

The current strategy additionally requires a short's 20 EMA to be below a falling
50 EMA. This reduced shorts from 2,904 to 1,984 and increased longs from 3,197 to
3,350. The existing opposite-signal exit remains available even when the short
entry filter fails. Net P&L worsened from -$12,951.90 to -$16,658.60; profit factor
fell from 0.9539 to 0.9330. See [the complete comparison and rules](topbot-strategy.md).

V4's winners averaged 206.1 minutes held (median 30); losers averaged 144.2 minutes
(median 25). Local exports use the `topbot-v4-` prefix, including `trades.csv`,
`trades.json`, `trade-analysis.json`, and `replay-report.json` in
`backend/storage/databento/`. The more detailed v3 tables below remain historical.

## Historical v3 50/50-point results

Source: `mnq_ema_vwap_pullback_v3_fixed_50pt`, May 7, 2019 through July 10, 2026.
One MNQ contract; $1.20 commission per contract per side; one tick of modeled
slippage; $50,000 initial balance. Full replay: 504,185 bars and 6,101 closed trades.
The new analysis reconciles with the original engine's overall and direction
metrics. Trading results are unchanged; replay SHA-256 remains
`4ac4b10407c4a6704ad5d46f825c0e5a04d709f7dde8aacb14919f86e43f0027`.

| Direction | Trades | Wins | Losses | Win rate | Net P&L | Profit factor |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Long | 3,197 | 1,491 | 1,706 | 46.64% | -$4,251.80 | 0.9711 |
| Short | 2,904 | 1,302 | 1,602 | 44.83% | -$8,700.10 | 0.9350 |
| All | 6,101 | 2,793 | 3,308 | 45.78% | -$12,951.90 | 0.9539 |

There were no net-breakeven trades. Gross P&L was +$1,690.50 after modeled slippage
but before commissions. Commissions totaled $14,642.40. Eight gross winners became
net losers after fees. Slippage is already reflected in entry/exit prices; it must
not be subtracted a second time.

| Net outcome | Trades | Average held | Median held | 90th percentile | Average bars | Average MAE | Average MFE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Winner | 2,793 | 182.4 min | 25 min | 255 min | 20.31 | $36.39 | $99.68 |
| Loser | 3,308 | 132.9 min | 25 min | 150 min | 16.90 | $85.78 | $33.61 |

Holding time uses elapsed recorded entry-to-exit time, including overnight and
market-closure gaps. It is approximate at candle resolution: a same-candle exit
can have zero elapsed minutes. A small number of long holds pulls the means well
above the medians. Bars held and elapsed minutes answer different questions.

MAE is the worst adverse move and MFE the best favorable move observed while held,
expressed in gross dollars for the whole position. OHLC bars do not reveal the
exact intrabar path or when the excursion occurred. These values alone cannot
prove that a different stop, target, or time exit would improve results.

## Entry hour in New York time

| Entry hour ET | Trades | Net P&L | Profit factor |
| --- | ---: | ---: | ---: |
| 09:00–09:59 | 1,128 | -$6,460.70 | 0.8811 |
| 10:00–10:59 | 1,386 | +$556.10 | 1.0090 |
| 11:00–11:59 | 980 | -$4,558.50 | 0.8980 |
| 12:00–12:59 | 796 | +$1,363.60 | 1.0402 |
| 13:00–13:59 | 669 | +$3,156.40 | 1.1105 |
| 14:00–14:59 | 634 | -$2,494.60 | 0.9187 |
| 15:00–15:59 | 508 | -$4,514.20 | 0.8307 |

Hour labels are grouping buckets, not enabled trading windows; the entry-session
rules still apply. Grouping uses the actual entry timestamp and handles daylight
saving time. Weekday and year tabs use the same entry-time basis, whereas existing
daily/monthly accounting tables use exit trading days.

## Exit reasons

| Exit reason | Trades | Net P&L |
| --- | ---: | ---: |
| Take profit | 2,754 | +$267,413.40 |
| Stop loss | 2,124 | -$218,559.60 |
| Opposite signal flatten | 1,186 | -$58,682.40 |
| Stop first when both levels touched | 21 | -$2,160.90 |
| Contract roll | 10 | -$235.00 |
| Stop loss at a gap | 6 | -$727.40 |

Opposite-signal exits and gap losses explain why individual outcomes are not
always exactly the planned 50 points. The largest net loss was $150.40; a fixed
stop distance is not a guarantee of the realized loss through a gap.

The direction imbalance, entry-time differences, and opposite-signal exits provide
specific places to inspect the ledger. They are descriptions of this observed
history. Removing a group of past trades does not simulate removing its entry rule:
positions, cooldowns, and later signals can all change, so candidate changes need
a separate replay and subsequent evaluation on new data.

## Local exports and reproduction

- Full ledger: `backend/storage/databento/topbot-v3-trades.json`
- Spreadsheet-compatible ledger: `backend/storage/databento/topbot-v3-trades.csv`
- Derived groups: `backend/storage/databento/topbot-v3-trade-analysis.json`
- Replay report: `backend/storage/databento/topbot-v3-detailed-report.json`

These generated files remain in ignored local storage. The UI export buttons
produce the same ledger fields and derived statistics for any result it displays.
To capture the current v4 ledger from the repository root without overwriting v3:

```powershell
backend\.venv\Scripts\python backend\tools\benchmark_topbot_replay.py --days 3000 --holdout --output backend/storage/databento/topbot-v4-replay-report.json --trades-output backend/storage/databento/topbot-v4-trades.json
```

Implementation: `frontend/src/pages/bot/backtestAnalytics.ts` and
`frontend/src/pages/bot/BacktestTradeAnalysis.tsx`. Tests cover direction/outcome
reconciliation, costs, percentiles, empty samples, DST, overnight gaps, invalid
timestamps, duration boundaries, filters, sorting, pagination, and complete exports.
