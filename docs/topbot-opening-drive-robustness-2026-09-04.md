# Conditional opening-drive robustness checks — September 4, 2026

This plan is recorded while A06's corrected-fee matrix is still running and
before any of the parameter-neighbor or delayed-entry replays below. The
unchanged opening-drive candidate passed all measured A04 gates at the old
$1.20 fee except later-period profit factor (1.099296 versus the predeclared
1.10 minimum). That makes it worth reconsidering at the verified fee; it does
not establish that the corrected candidate passes. Finish and audit A06 first.

If corrected opening drive passes the measured original gates, retain its
original parameters as the center and test these six one-at-a-time neighbors:

| Dimension | Lower neighbor | Original center | Upper neighbor |
| --- | ---: | ---: | ---: |
| Absolute opening displacement / opening range | 0.60 | 0.65 | 0.70 |
| Stop distance / opening range | 0.40 | 0.50 | 0.60 |
| Target / stop distance | 1.75 | 2.00 | 2.25 |

Every other rule stays fixed: the first six completed regular-session
five-minute candles, one decision at 10:00 Eastern, direction of displacement,
one MNQ, 10–100 point clipped stop, existing daily budget and holiday-aware
15:55 clock exit. These are six registered alternatives, not a grid search or
permission to optimize another combination after seeing results.

Stops use the original rule: clip range times stop fraction to 10–100 points,
then round up to a quarter point. Targets similarly round risk times reward
multiple up to a quarter point; the realized planned ratio can therefore be
slightly above its requested value. The original settings remain 09:30–16:00,
three permitted daily entries, 300-second cooldown and $250 daily budget; the
exact 10:00 decision allows only one natural setup per day. Known early closes
move the clock deadline earlier. No parameter-neighbor changes these details.

The separate neighbor fixture is
`backend/tools/fixtures/topbot_research_opening_drive_neighbors.py`, SHA-256
`8a9d611751f87a62bc9277970a7124b1a700cc04208fc344b9990da3def3e4d8`.
It uses seven private copies of the pinned original fixture, with no shared
module mutation. Thirty-five synthetic tests passed, including identical
center/original trade ledger, equity curve and metrics at $0.61 per side.
These checks establish implementation equivalence, not historical profitability.

Replay each neighbor and the unchanged center on full and later diagnostic
periods at one and two ticks of slippage, with explicit
`--commission-per-side 0.61`: 28 cases. The center's replay must reproduce A06's
economic ledger; differing fixture attribution may be disclosed separately.
The original nearby-parameter gate requires positive full and diagnostic net
results. Report every neighbor, including failures, and interpret thin margins,
drawdown, counts, uncertainty and concentration alongside signs. A failure
cannot be repaired by quietly selecting the best neighbor as a new center.

Separately replay the original A06 fixture and unchanged opening-drive rules
with `--entry-delay-minutes 1 --commission-per-side 0.61` on full, development
and diagnostic periods at 1/2/4 ticks: nine cases. The engine retains the
original information at the decision time and anchors its bracket at the
delayed actual open. Missing exact execution minutes discard entries. This is
a coarse latency stress, not measured live latency. Positive development and
diagnostic net at one and two ticks are necessary; report the four-tick case.

Target-touch fills also need a separate stricter trade-through/nonfill stress
before adoption. The fixed additional scenario requires the observed open or
intrabar extreme to pass the original target by at least **one tick**, in the
profitable direction, before assuming its fill. A mere touch leaves the position
and brackets open. Qualified targets still fill at their original price less
configured adverse slippage; stops, gap-stop prices, stop-first ambiguity and
calendar-clock ordering retain the original behavior. This approximates a
possible nonfill; trading through one tick does not prove queue priority.

Replay only unchanged `opening_drive` with no additional entry delay at
explicit $0.61 per side, full/development/diagnostic periods and 1/2/4 ticks
slippage: nine cases. Positive development and diagnostic net at one and two
ticks are necessary; disclose all cases and changes in exposure, drawdown and
trade count. Do not interpret this as nine independent samples or optimize the
confirmation distance after seeing results.

The isolated wrapper is `backend/tools/research_topbot_target_fill_stress.py`,
SHA-256 `0912ccf134be3be4f220853d6681fb6e0361f8aa33976a4c013d57874f6309e7`.
It records engine `5.3.0-entry-latency-stress+target-through-1tick-v1` and model
`observed_1m_target_through_one_tick_v1` before replay. Forty-eight synthetic
stress tests and ten existing runner tests passed for both directions, retained
positions, original target/fee economics, stops, independent clocks and manifest
hashes. No original engine, runner or A06 fixture file changed. No historical
target-fill stress had run when this plan was recorded. No candidate
is promoted based on these reused-history results alone. Runtime policy parity,
unseen evaluation and all other requirements in the original protocol remain.
The reserved newer pool stays untouched during this stage. The prepared
overnight hypotheses remain conditional and untested while a promising
corrected candidate receives these checks.

## Runtime work remains conditional

Read-only integration review found that the evaluator alone cannot establish
live/replay agreement. `BotWorkerRuntime._process_run` needs an independent
clock check before its no-new-candle return; it must not create synthetic
candles or advance candle bookkeeping. A separate service action should reuse
the mutation fence and `_execute_verified_reduce_only_flatten`, preserving
fresh account, position, order, tenant, lease, stop and emergency checks.
`LiveExecutionPreflight` currently discards the provider position identity and
creation timestamp; a validated exact-contract snapshot must retain them and
recheck identity after bracket cancellation. Already-flat must be an audited
no-op. Clock metadata can use the existing `lifecycle` decision type without
pretending the database constraint already accepts a new type.

The research daily reset policy needs a deliberate TopBot-specific entry lock
that expires at the futures trading-date boundary after fresh account checks.
It must never revive a stopped/blocked run, restore confirmation, bypass startup
rearm or clear an emergency latch. App backtests still need observed-minute
execution, and historical versus provider-active contract selection still needs
a documented common policy. Shared-rule parity, worker clock/race tests and
mocked verified reductions are required before promotion. No production or
provider action was taken during this read-only design review.
