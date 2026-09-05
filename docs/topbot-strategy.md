# TopBot Adaptive v5

**Fee correction:** TopstepX MNQ defaults are now $0.61 per side ($1.22 round
trip). The corrected app replay returns -$5,357.50 net, with +$2,186.78 on longs
and -$7,544.28 on shorts. The comparisons below retain their original, overstated
$1.20-per-side costs. See [the corrected fee report](topbot-fee-correction.md)
for both application and observed-minute research results.

TopBot runs one MNQ trend-pullback setup with a long bias. There are no votes,
quality-score thresholds, benchmark instruments or operator tuning controls.
The code preset is in `backend/app/services/topbot_strategy.py`, revision
`mnq_ema_vwap_pullback_v5_bracket_exits`.

V5 preserves v4 entries and the fixed 50-point stop/target. It removes
opposite-signal exits: a new signal may enter from flat, but cannot flatten,
reverse or add to an existing position. A filtered short is now HOLD. Existing
positions keep their brackets. This improved historical performance but **still
has no demonstrated profitable edge after costs**.

## Rules

1. Use closed MNQ 5-minute candles and 200 bars of EMA warmup. Evaluate weekdays
   from 09:30 through 15:45 America/New_York at candle close.
2. Calculate EMA20 and its change over three bars. Calculate typical-price VWAP
   from all regular-session candles beginning at 09:30. A missing or duplicate
   session candle blocks entries. At least two session candles are required,
   making 09:40 the first possible entry decision.
3. A long needs a rising EMA20, a close above EMA20 and VWAP, a previous candle
   touching EMA20, and a confirmation close above the previous high and its own
   open. Shorts mirror these rules and also require EMA20 below a falling EMA50.
4. Send an absolute one-contract target (+1 for BUY, -1 for SELL), with a 50-point
   stop and 50-point target, each 200 MNQ ticks. The shared execution preflight
   uses the authoritative provider position. Existing equal, opposite or larger
   positions cannot be modified by these entry signals.
5. Keep the existing $250 daily-loss entry gate, maximum 30 entries/day, 300-second
   cooldown, one-contract size and one-position limit. Bracket distances are
   anchored to the actual entry fill. No trailing stop or time stop is added.

The entry cutoff does not flatten overnight positions. Live positions retain
broker brackets and manual/emergency controls; stopping automation does not
close them. Historical replay still closes at delivery rolls and, by default,
the end of the requested test. A gap can cause a stop loss larger than 50 points.

## Execution and verification

Live/dry-run evaluation and replay use the same strategy function. ProjectX
supplies run/chart candles; Databento OHLCV plus Definition data supplies replay.
Next-bar fills, configured slippage, commissions, conservative same-bar stop-first
handling, rollover warmup, risk controls and coverage diagnostics are unchanged.
Engine `5.0.0-topbot-bracket-exits` invalidates older cached results; saved earlier
reports remain historical.

Tests verify fixed 200-tick brackets, both signal directions, rejected shorts
holding existing positions, opposite signals not flattening replay positions,
and mocked provider routing from flat only (including oversized positions).
The production v5 full ledger exactly matches the previously defined offline
bracket-only experiment.

## V5 comparison

Full 2019–July 2026 replay, same 504,185 bars, costs, sizing and risk gates:

| Measurement | V4 | V5 |
| --- | ---: | ---: |
| Trades | 5,334 | 4,798 |
| Win rate | 45.39% | 50.35% |
| Gross P&L after modeled slippage | -$3,857.00 | $499.50 |
| Commission | $12,801.60 | $11,515.20 |
| Net P&L | -$16,658.60 | -$11,015.70 |
| Profit factor | 0.9330 | 0.9551 |
| Expectancy/trade | -$3.12 | -$2.30 |
| Maximum drawdown | $19,178.30 | $16,039.30 |
| Long net P&L | -$3,825.50 | -$1,383.90 |
| Short net P&L | -$12,833.10 | -$9,631.80 |
| Final-20% diagnostic net P&L | -$5,124.20 | -$4,279.20 |

The exit change improved both 2020–2023 and 2024–July 2026, including a later-period
comparison with two ticks of slippage. Two entry filters were also tested; neither
was adopted. See [the complete comparison and rejected hypotheses](topbot-improvement-comparison.md).
This history had already been examined; the comparisons are not independent
validation or evidence of future profitability. The remaining entry edge is
insufficient to cover costs, especially on shorts.

Current local exports: `backend/storage/databento/topbot-v5-replay-report.json`,
`topbot-v5-trades.json`, `topbot-v5-trades.csv`, and `topbot-v5-trade-analysis.json`.

## Historical v4 comparison: stricter shorts

Same 2019–2026 history, costs, sizing, entry session, stop/target, and account limits
as v3. No parameter sweep was performed. The 50 EMA rule was fixed before this replay.

| Measurement | V3 symmetric entries | V4 long bias |
| --- | ---: | ---: |
| Long trades | 3,197 | 3,350 |
| Short trades | 2,904 | 1,984 |
| Long share of trades | 52.40% | 62.80% |
| Long net P&L | -$4,251.80 | -$3,825.50 |
| Short net P&L | -$8,700.10 | -$12,833.10 |
| Total net P&L | -$12,951.90 | -$16,658.60 |
| Profit factor | 0.9539 | 0.9330 |
| Maximum drawdown | $17,335.50 | $19,178.30 |
| Final-20% diagnostic net P&L | -$4,137.90 | -$5,124.20 |

Shorts fell by 31.68%, but their win rate also fell from 44.83% to 43.60%. The
filter expresses the requested directional preference; it does not improve this
sample. Fewer shorts also change when capital is available for later long entries,
so long trade counts change even though the long signal rule is unchanged.

The full 504,185-bar replay plus diagnostic took 31.647 seconds. Local report:
`backend/storage/databento/topbot-v4-replay-report.json`; complete ledger:
`backend/storage/databento/topbot-v4-trades.csv`. Replay and diagnostic SHA-256:
`86e15ff73f3d459bfc4d674bb23660050c609d6e60b8ee75b5505fede46d5cbd`.
This reuses previously examined history; the final period is a comparison
diagnostic, not untouched out-of-sample validation.

## Historical v3 baseline: fixed 50-point stop and target

The full replay uses the same history, starting balance, costs, and session/account
limits listed below for v2. With the requested fixed 50/50-point bracket:

| Measurement | Full replay | Final 20% diagnostic |
| --- | ---: | ---: |
| Trades | 6,101 | 1,436 |
| Win rate | 45.78% | 48.33% |
| Net P&L after costs | -$12,951.90 | -$4,137.90 |
| Profit factor | 0.9539 | 0.9421 |

The full replay's maximum drawdown was $17,335.50 (32.45%). Initialization took
2.195 seconds; replay including the diagnostic took 29.410 seconds. The losses
are smaller than v2, but this is still a losing baseline after costs. Because the
same historical periods have already been examined, the final-period replay is a
comparison diagnostic, not an untouched validation set.

The local report is `backend/storage/databento/topbot-v3-replay-report.json`.
Serialized replay and diagnostic SHA-256:
`4ac4b10407c4a6704ad5d46f825c0e5a04d709f7dde8aacb14919f86e43f0027`.

See [the detailed trade analysis](topbot-backtest-analysis.md) for long/short counts,
winner/loser holding times, entry-time groups, exit reasons, and ledger exports.

## Historical v2 baseline: structural stop and 2R target

These results used the previous ATR-filtered structural stop and 2R target; they
do not describe the current fixed 50/50-point setup.

Measured on September 4, 2026 using the local imported cache. The replay spans
May 7, 2019 13:00 UTC through July 10, 2026 20:20 UTC, with 504,185 execution bars
after initial warmup. Assumptions: $50,000 starting balance, one MNQ contract,
$1.20 commission per contract per side, and one tick of slippage.

| Measurement | Full replay | Final 20% diagnostic |
| --- | ---: | ---: |
| Trades | 4,453 | 677 |
| Win rate | 31.04% | 29.99% |
| Net P&L after costs | -$20,539.20 | -$6,194.80 |
| Profit factor | 0.8736 | 0.8025 |

The full replay's maximum drawdown was $21,742.70 (42.90%). Its warnings recorded
1,570 source gaps and 29 delivery rolls. These data conditions and the execution
assumptions are part of the measurement, not evidence of future performance.

The chronological diagnostic starts February 6, 2025 08:20 UTC with fresh portfolio
state and unchanged rules. It is a diagnostic, not independent strategy validation
or a walk-forward optimization. Parameter changes informed by these results would
need a new evaluation plan; this history is now observed.

Engine initialization took 2.507 seconds. Replay including the diagnostic took
44.522 seconds. These timings exclude application persistence and result-cache hits.
The local JSON report is `backend/storage/databento/topbot-v2-replay-report.json`
(ignored generated storage). The benchmark now runs the current preset. To measure
v4 from the repository root and preserve the historical v2/v3 reports:

```powershell
backend\.venv\Scripts\python backend\tools\benchmark_topbot_replay.py --days 3000 --holdout --output backend/storage/databento/topbot-v4-replay-report.json --trades-output backend/storage/databento/topbot-v4-trades.json
```

Source fingerprint:
`cd56b8dbe08abc26b6bbbb9351e337984c603fe2562942ecb85ad0b9383a897d`.
Serialized replay and diagnostic SHA-256:
`afd1ad364af37a3ad674e82d40bf502adce88b6c015a2075ea195e29ed45b5d4`.

Tests cover long/short entries, no-vote dispatch, missing/duplicate session history,
wrong instruments/timeframes, fixed 200-tick long/short brackets, next-bar fills, single-stream
acquisition, rollover warmup, current-preset snapshots, and Databento storage parity.
