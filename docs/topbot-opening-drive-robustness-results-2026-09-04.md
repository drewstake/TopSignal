# Opening-drive robustness results — September 4, 2026

All **46 A07 cases completed and passed the artifact/accounting audit**. The
four unchanged-center cases exactly reproduce A06. All six predeclared
one-parameter neighbors retain positive full and diagnostic net at one and two
ticks. Both the one-minute entry-delay and one-tick target-through scenarios
retain positive development and diagnostic net at one and two ticks. These are
passes of the registered retrospective robustness checks, not a claim of
untouched confirmation or live execution readiness.

The center remains displacement fraction **0.65**, stop fraction **0.50**, and
reward multiple **2.00**. This audit selected or changed no parameters. The
original A06 acceptance criteria and the separately registered A07 checks were
applied as written; no threshold was retuned after outcomes.

## Complete runs and independent verification

Every run uses one MNQ, $50,000 fresh starting cash, the same format-6 data,
$0.61 per contract per side ($1.22 per completed trade), and the same original
calendar and risk limits. Slippage is separate. Every trade quantity and fee,
actual replay assumption, ledger hash, session mark, metadata declaration and
source snapshot was checked. All terminal result and per-case start records
reconcile to their captured pre-test manifest fingerprint.

| Registered batch | Cases | Result |
| --- | ---: | --- |
| `20260905T011330.674136Z-drive-neighbors-displacement-54aa3a80dc03` | 12 | Complete, audit passed |
| `20260905T011340.970424Z-drive-neighbors-stop-f0cf9be62aa3` | 8 | Complete, audit passed |
| `20260905T011351.327135Z-drive-neighbors-reward-b6e8ca140898` | 8 | Complete, audit passed |
| `20260905T011407.431551Z-drive-delay-one-minute-75ad90735944` | 9 | Complete, audit passed |
| `20260905T011417.743363Z-drive-target-through-one-tick-07d9bf06d213` | 9 | Complete, audit passed |

All five runs record the same source bundle
`afe4c339238770bcb0e0fa623e4a7ff808d5b9f22185762b935c1794c4a76048`
and predeclared protocol fingerprint
`b16636f6329f8092bdf557900550c112bf6f21787f6e664040aa69678ab8b0cf`.
Their full data manifests match A06, including source fingerprint
`e900ae486308de577f0945e21cd54821ed2b206c027761d1973563a9085b4d6a`.
All 57 protected application/runner/original-fixture/roll-helper source files
match A06 byte for byte. Runtime versions, period bounds, bootstrap settings,
cost conventions and risk settings also match.

The neighbor fixture raw SHA-256 is
`8a9d611751f87a62bc9277970a7124b1a700cc04208fc344b9990da3def3e4d8`.
Each neighbor changes only its declared dimension; its center has no parameter
change. The seven private rule copies retain the original normalized source pin
`d0230d261f3e5f00f6f876756086b873987eb540ae2c6a6b1798ba2b376d80e6`.
Attribution revisions are captured separately from economic settings.

The target-fill wrapper raw SHA-256 is
`0912ccf134be3be4f220853d6681fb6e0361f8aa33976a4c013d57874f6309e7`.
Its engine/runner/fixture source pins were independently recomputed from the
immutable snapshots. It does not change the shared base engine: its isolated
wrapper adds the predeclared target-confirmation model.

Nine distinct parameter/execution compatibility groups are retained: seven
neighbor/control groups of four cases, one delay group of nine, and one
target-through group of nine. Their periods and cost scenarios are not
independent samples and are not pooled into another profitability estimate.

## Unchanged-center reproduction

Full and diagnostic runs at one and two ticks exactly match their corresponding
A06 trade ledgers, complete metrics, session-mark ledgers and equity curves.

| Period | Slippage ticks | Exact A06 trade-ledger SHA-256 |
| --- | ---: | --- |
| full | 1 | `d9ce0cd491cb9cdc536616223146fa92ed3ae94cafd8ce9008b02619196468fd` |
| full | 2 | `3db4b8a32343b137a6a8e1677249bf1e1adb69d732f6b3dc07a383291291651f` |
| diagnostic | 1 | `9138a7fdd5b9e0ec38512d70f279dad2546c3c8dbfadf1fd70fc6c28da5570f1` |
| diagnostic | 2 | `4422c1cb896e0e693c7037665df5c37b7e276c6e5157153e337e737d134bf6cc` |

## All predeclared parameter neighbors

All net values below include $0.61 per-side fees. The center row is a repeated
control. Full one-tick trade counts, drawdown and expectancy accompany all four
registered net comparisons; full precision and additional metrics are exported.

| Rule | Full 1 tick $ | Full 2 ticks $ | Diagnostic 1 tick $ | Diagnostic 2 ticks $ | Full trades | Full 1-tick DD $ | Full 1-tick expectancy $ | A07 sign gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Unchanged center | 11,413.04 | 11,104.04 | 1,999.74 | 1,878.24 | 543 | 2,692.84 | 21.02 | Exact control |
| Displacement 0.60 | 12,101.34 | 11,729.84 | 901.76 | 760.76 | 653 | 2,886.15 | 18.53 | Pass |
| Displacement 0.70 | 8,752.84 | 8,506.34 | 1,882.86 | 1,786.86 | 428 | 1,973.36 | 20.45 | Pass |
| Stop fraction 0.40 | 9,290.54 | 8,296.04 | 2,483.74 | 2,201.74 | 543 | 1,924.96 | 17.11 | Pass |
| Stop fraction 0.60 | 12,791.54 | 12,164.04 | 3,167.24 | 2,955.74 | 543 | 2,855.84 | 23.56 | Pass |
| Reward multiple 1.75 | 11,051.04 | 10,749.54 | 3,269.74 | 3,150.74 | 543 | 2,282.34 | 20.35 | Pass |
| Reward multiple 2.25 | 12,038.04 | 11,634.04 | 2,746.24 | 2,621.74 | 543 | 2,630.46 | 22.17 | Pass |

The registered nearby-parameter requirement is positive full and diagnostic
net at both costs; all six neighbors satisfy it. This does not certify each
neighbor as a new candidate under the original full selection screen. For
example, displacement 0.60 has only $760.76 later-period net at two ticks and
diagnostic one-tick profit factor 1.0435, while displacement 0.70 has 428 full
one-tick trades, below the original center-selection minimum of 500. Those
limitations remain visible; they are not reasons to alter either threshold or
pick a different center. Fresh development runs were not part of the 28-case
neighbor protocol. The general exporter may therefore label the broader
candidate screen incomplete or failed for a neighbor; that is distinct from
its explicitly predeclared A07 sign check.

## One-minute delayed entry

The original fixture and base engine `5.3.0-entry-latency-stress` are unchanged.
Manifest and actual assumptions record `entry_delay_minutes: 1`. Every entry
occurs exactly one minute after the original five-minute decision close: the
trade entry is six minutes after its recorded signal-bar start. No delayed
entry catches up on a later unregistered minute. Exits retain their original
clock/bracket policy. This is a coarse delay scenario, not measured latency.

| Period | Slippage ticks | Trades | Net $ | Change from A06 $ | Max DD $ | Expectancy $ | Exposure % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| full | 1 | 543 | 10,630.04 | -783.00 | 2,596.96 | 19.58 | 2.73 |
| full | 2 | 543 | 10,077.54 | -1,026.50 | 2,606.46 | 18.56 | 2.73 |
| full | 4 | 543 | 9,180.54 | -873.50 | 2,625.46 | 16.91 | 2.72 |
| development | 1 | 335 | 8,017.80 | -1,395.50 | 1,650.68 | 23.93 | 2.72 |
| development | 2 | 335 | 7,817.80 | -1,408.00 | 1,660.18 | 23.34 | 2.73 |
| development | 4 | 335 | 7,365.30 | -1,053.50 | 1,679.18 | 21.99 | 2.73 |
| diagnostic | 1 | 208 | 2,612.24 | 612.50 | 2,596.96 | 12.56 | 2.75 |
| diagnostic | 2 | 208 | 2,259.74 | 381.50 | 2,606.46 | 10.86 | 2.74 |
| diagnostic | 4 | 208 | 1,815.24 | 180.00 | 2,625.46 | 8.73 | 2.70 |

The registered development/diagnostic net checks at one and two ticks pass.
Four-tick results are disclosed as required; all three periods also remain
positive in that scenario. Each period is fresh and separately costed.

## One-tick target-through confirmation

The manifest, summary and raw replay all record engine
`5.3.0-entry-latency-stress+target-through-1tick-v1` and execution model
`observed_1m_target_through_one_tick_v1`, with one confirmation tick and zero
additional entry delay. Every entry retains the original decision-close time.
A mere target touch leaves the position and brackets open; qualification needs
an observed open or extreme one tick through the target. Qualified targets
still fill at their original price with adverse slippage and fees. Stops and
independent calendar exits retain the original order. This model is distinct
from both normal target-touch fills and the delayed-entry group.

| Period | Slippage ticks | Trades | Net $ | Change from A06 $ | Max DD $ | Expectancy $ | Exposure % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| full | 1 | 543 | 11,413.04 | 0.00 | 2,692.84 | 21.02 | 2.70 |
| full | 2 | 543 | 10,916.54 | -187.50 | 2,704.34 | 20.10 | 2.71 |
| full | 4 | 543 | 10,005.54 | -48.50 | 2,727.34 | 18.43 | 2.72 |
| development | 1 | 335 | 9,413.30 | 0.00 | 1,130.76 | 28.10 | 2.65 |
| development | 2 | 335 | 9,038.30 | -187.50 | 1,134.76 | 26.98 | 2.66 |
| development | 4 | 335 | 8,370.30 | -48.50 | 1,142.76 | 24.99 | 2.68 |
| diagnostic | 1 | 208 | 1,999.74 | 0.00 | 2,692.84 | 9.61 | 2.80 |
| diagnostic | 2 | 208 | 1,878.24 | 0.00 | 2,704.34 | 9.03 | 2.80 |
| diagnostic | 4 | 208 | 1,635.24 | 0.00 | 2,727.34 | 7.86 | 2.79 |

The registered development/diagnostic net checks at one and two ticks pass.
Four-tick results are disclosed as required; all three periods also remain
positive in that scenario. Each period is fresh and separately costed.

Target-through changes the economic trade path in all nine cases even when
net P&L stays equal: exit timing and exposure can change without changing a
qualified target price. At one tick, full net remains $11,413.04, while exposure
rises from 2.6989% to 2.7031%. Full net falls by $187.50 at two ticks and $48.50
at four ticks. The delayed-entry scenario also changes all nine paths; its
full one-tick net is $783.00 below A06, while its later diagnostic net improves.
Trade counts remain unchanged in both execution stresses. No favorable outcome
is used to change the original timing or fill assumption.

## Exports, retention and remaining limits

All 46 complete trade CSVs, a complete Markdown report and case metrics are at:
`backend/storage/research/reports/20260905T012024.452949Z-opening-drive-robustness-complete-200de24845e4`.
The 16,996 exported rows match their JSON source-ledger counts, quantities and
$1.22 round-trip fees. They include overlapping periods/costs and must not be
counted as 16,996 independent observations.

The independent checks and isolated read-only analysis source are at:
`backend/storage/research/robustness-audits/20260905T011708.987434Z-a07-final-audit-346333828b76`
(`a07-audit.json` and `audit_a07.py`). All 822 files across the five A07 runs
and the A06 opening reference run were SHA-256 identical before and after the
audit/export. The aggregate retention digest is
`c71baa34f9baba4242f11b408fad61420a88638ed53a05a84eb21f33b3b7d018`.

This evidence supports the registered robustness checks for the unchanged
center. It remains conditional on reused historical data, the audited calendar
approximation, bar-based fills and unadjusted selection uncertainty. One-tick
penetration does not establish queue priority or all live nonfill behavior, and
one added minute does not model every operational latency. The original
untouched-evaluation and runtime-policy-parity requirements remain separate.
This audit accessed no newer-quarantine prices, provider/order API, or live
execution path, and launched no strategy or additional experiment.
