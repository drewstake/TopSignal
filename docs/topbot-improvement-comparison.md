# TopBot improvement comparison — September 4, 2026

**Fee correction:** These experiments used the original $1.20 per side. The
current TopstepX MNQ base fee is $0.61 per side ($1.22 round trip); see
[the corrected baseline report](topbot-fee-correction.md). The tool now defaults
to $0.61; pass `--commission-per-side 1.20` to reproduce these historical costs.

The v4 baseline lost $16,658.60 across the stored 2019–2026 history. Shorts lost
$12,833.10. Commissions totaled $12,801.60; gross P&L was already negative at
-$3,857.00. Opposite-signal exits accounted for 1,014 trades and -$48,796.10 net,
but that grouping alone cannot show what would have happened without the exits.

Three fixed hypotheses were compared on 2020–2023, excluding the sparse 2019
launch history from selection. Every comparison retains one MNQ, the 50-point
stop and target, the same session, $1.20/side commission, one tick slippage,
daily-loss gate, next-bar fills and rollover handling. No parameter sweep.

| Fixed hypothesis | Net P&L | Profit factor | Max drawdown | Trades |
| --- | ---: | ---: | ---: | ---: |
| Baseline v4 | -$5,352.80 | 0.9607 | $7,932.40 | 2,967 |
| Hold for bracket; ignore opposite signals | -$2,704.80 | 0.9799 | $6,503.40 | 2,662 |
| Also require rising EMA50 alignment for longs | -$5,465.90 | 0.9530 | $7,497.50 | 2,511 |
| Enter only within 25 points of EMA20 | $810.40 | 1.0107 | $4,385.50 | 1,809 |

**Selected before running later-period comparisons:** limit new entries to a
close at most 25 points from EMA20 (half the fixed planned risk). Preserve all
existing opposite-signal exits, including when an otherwise valid entry is too
extended. This isolates entry quality instead of also changing exit behavior.
It keeps the requested long bias and adds no indicator or UI configuration.

The candidate improved each selection year's net result relative to baseline,
but still lost money in 2021 and 2023. Its selection-period expectancy was only
$0.45/trade. Next checks: unchanged candidate on 2024–July 2026 with fresh account
state, then the same comparison with two ticks of slippage. Later comparisons
will be added below without retuning the 25-point rule.

All these dates were already examined in earlier work. They are retrospective
comparisons, not untouched validation. Trying alternatives on reused history can
produce misleading apparent improvements; see
[Bailey et al., The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).

The reproducible offline tool is `backend/tools/compare_topbot_variants.py`.
Each JSON report embeds the exact baseline source, its SHA-256, fixed hypotheses,
data fingerprint, execution costs, metrics, yearly breakdown and ledger hashes.
The frozen v4 evaluator is checked in at `backend/tools/fixtures/topbot_v4.py` and
is the comparison tool's default. Generated reports remain in ignored
`backend/storage/databento`. Use `--cache-dir` to read the original cache from a
separate worktree. The tool uses the current replay engine, shared indicators and
code-owned account limits; record the Git commit alongside every report. New
reports include configuration, assumptions and coverage diagnostics to expose
changes in those dependencies.

## Later-period checks and final decision

The unchanged 25-point entry filter failed the later-period quality check. It
reduced losses by trading much less, but worsened profit factor from 0.8936 to
0.8672 and expectancy from -$5.34 to -$6.33/trade. It is **not adopted**.

The originally defined bracket-only variant was then checked on the same later
period, with no change to its rule. This is an additional comparison on already
observed history, not a new validation set. It improved net results, profit
factor, expectancy and drawdown, also with higher slippage. It is adopted as v5.

| 2024–July 2026, fresh account state | Baseline v4 | V5 bracket exits |
| --- | ---: | ---: |
| Net P&L, 1 tick slippage | -$10,864.90 | -$8,915.50 |
| Profit factor | 0.8936 | 0.9125 |
| Expectancy/trade | -$5.34 | -$4.58 |
| Maximum drawdown | $12,780.80 | $11,645.70 |
| Trades | 2,036 | 1,945 |
| Net P&L, 2 ticks slippage | -$12,470.10 | -$10,661.30 |
| Profit factor, 2 ticks | 0.8788 | 0.8963 |
| Maximum drawdown, 2 ticks | $14,177.70 | $13,199.60 |

The entry rule still has no demonstrated positive expectancy. Bracket exits
improve this comparison but do not make TopBot profitable. The entry-distance
filter and extra long trend filter remain offline experiments, not production
knobs. Three rule alternatives were tried; the exit variant was inspected after
the initially selected entry filter failed its later-period quality check.

V5 emits signed absolute one-contract entry targets instead of market-order
deltas. The authoritative provider target checks block an order when the position
is already at that target, opposite, or oversized; entry signals therefore cannot
flatten, reverse or add to an existing position. A short failing the long-bias
filter emits HOLD. Existing broker brackets and manual emergency controls remain
responsible for exits; replay retains its rollover and end-of-test closures.

## Full replay and checks

The production v5 ledger contains 4,798 trades and exactly matches the frozen
bracket-only experiment's ledger SHA-256. Full-replay net P&L improved from
-$16,658.60 to -$11,015.70, profit factor from 0.9330 to 0.9551, and maximum
drawdown from $19,178.30 to $16,039.30. The final-20% diagnostic improved from
-$5,124.20 to -$4,279.20. These full-history results also include sparse launch
data and the remaining coverage limitations described in the replay report.

Gross P&L after modeled slippage was only $499.50, while commissions were
$11,515.20. Short net P&L was -$9,631.80 and long net P&L was -$1,383.90. Removing
early exits helps this sample, but the weak entry edge and short-side losses
remain unresolved. Stops retain their full planned risk and can realize more
through a gap; this change is not a reduction of the per-trade stop distance.

Verification: backend suite 1,474 passed / 8 skipped; 25 focused frontend tests
passed; frontend production build passed. Replay tests cover both opposite-entry
directions, and mocked provider tests cover flat, existing long/short, and
oversized positions with 200-tick attached brackets. No live run was started.
