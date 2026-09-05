# A09 overnight results — September 4, 2026

The fixed overnight center **fails the original research screen and is not
promoted**. Its full-history one-tick net is $1,262.72 over 1,124 trades, with
$2,968.76 minute-close drawdown and $1.12 net expectancy per trade. Six numerical
gates fail; the wider neighbor also fails the required positive diagnostic net.
The tighter neighbor fails its own diagnostic profit-factor and uncertainty
gates and does not replace the center. All four declared variants are retained
below. Four-tick full-history net is negative for every variant.

All 36 registered cases completed: four variants, three fresh chronological
portfolios and three slippage levels. The independent source, accounting and
export audit found zero discrepancies. A separate raw-minute execution and
entry-omission audit also passed. No replay or parameter selection was performed
while auditing these results.

## Scope and fixed identity

The [overnight protocol](topbot-overnight-protocol-2026-09-04.md) was prepared
before A08. Its exact fixture already appears in the A07 source snapshot created
at 2026-09-05 01:13:30 UTC, before the A08 evaluation. A09 first began at 01:27:32
UTC. The fixture is unchanged from that prior snapshot; the engine, application
sources, runner, causal-roll helper, runtime versions and format-6 data identity
also match A07. Period boundaries match A06. The source snapshot and separate
candidate settings, rather than the production-normalized baseline description
in `config_snapshot`, identify the evaluated overnight rules.

All entries are one MNQ at exactly 16:00 Eastern, Monday–Thursday, after a closed
five-minute signal bar. An independent clock exits at 09:25 on the original
entry's next local date; resting brackets retain priority. The 50/100 and
100/200 neighbors change bracket distance while preserving the 2:1 target/stop
ratio. The short control changes direction at the center distances. The same
calendar exclusions, 200-bar warmup, $250 proposed-stop daily gate, one-entry
limit and $50,000 fresh cash apply throughout.

All cases explicitly use **$0.61 per contract per side**, independently checked
against $1.22 actual commission on every closed one-contract trade. Adverse
slippage is 1, 2 or 4 ticks on both entry and exit. Execution uses observed
one-minute bars, zero extra entry delay and ordinary target-touch fills. Costs
and brackets can change fills, exits and later daily-risk opportunities; these
are complete replay cases, not subtraction from a single trade ledger.

Requested full history is 2019-05-05 22:03 UTC through 2026-07-10 20:20 UTC;
development ends at 2024-01-01 00:00 UTC, and diagnostic starts there. Warmup
delays the first full/development evaluation to May 7, 2019. These are reused
historical samples. The newer ProjectX pool is now entirely exposed after A08;
it was not used in A09 and no currently independent pool remains.

| Identity | Value |
| --- | --- |
| Fixture revision | `mnq_overnight_drift_fixed_phase2_20260904_v1` |
| Fixture SHA-256 | `74159352b94c9ebb7a1ef87a59ea7ccf5a72c3a52c22c7481265db4feae18f59` |
| A09 code-bundle SHA-256 | `fb5faada235c1795083e3bb7f90c22e505145c242ed76f7be377f6684087b312` |
| A09 protocol SHA-256 | `ef24a2e0f8d1d6f1210fb403ab80f58c1306043748b3bdf6493c3819897a5f62` |
| Format-6 data fingerprint | `e900ae486308de577f0945e21cd54821ed2b206c027761d1973563a9085b4d6a` |
| Engine | `5.3.0-entry-latency-stress`; `observed_1m`; delay 0 |

## All 36 cases

Money columns are US dollars. DD is maximum drawdown measured at observed minute closes, not the intraminute extreme. Expectancy and profit factor use net trade P&L. Exposure is the share of observed execution bars with position exposure; it is not a wall-clock percentage. Full/development/diagnostic portfolios overlap in observations and must not be summed as independent evidence.

### Long 75/150 center

`overnight_long_75`

| Period | Ticks/side | Trades | Net | DD | Expectancy | PF | Exposure | Fees |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full | 1 | 1124 | 1,262.72 | 2,968.76 | 1.12 | 1.0171 | 31.32% | 1,371.28 |
| full | 2 | 1123 | 122.44 | 3,324.62 | 0.11 | 1.0016 | 31.28% | 1,370.06 |
| full | 4 | 1120 | -2,519.90 | 4,041.18 | -2.25 | 0.9666 | 31.08% | 1,366.40 |
| development | 1 | 740 | 1,000.70 | 2,913.84 | 1.35 | 1.0229 | 35.02% | 902.80 |
| development | 2 | 739 | 117.42 | 3,324.62 | 0.16 | 1.0027 | 34.95% | 901.58 |
| development | 4 | 737 | -1,601.64 | 3,866.86 | -2.17 | 0.9641 | 34.76% | 899.14 |
| diagnostic | 1 | 384 | 262.02 | 2,968.76 | 0.68 | 1.0086 | 24.56% | 468.48 |
| diagnostic | 2 | 384 | 5.02 | 3,003.26 | 0.01 | 1.0002 | 24.56% | 468.48 |
| diagnostic | 4 | 383 | -918.26 | 3,072.26 | -2.40 | 0.9703 | 24.33% | 467.26 |

### Long 50/100 neighbor

`overnight_long_50`

| Period | Ticks/side | Trades | Net | DD | Expectancy | PF | Exposure | Fees |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full | 1 | 1419 | 3,961.32 | 3,826.08 | 2.79 | 1.0509 | 29.17% | 1,731.18 |
| full | 2 | 1419 | 2,086.32 | 4,637.58 | 1.47 | 1.0266 | 29.13% | 1,731.18 |
| full | 4 | 1419 | -639.68 | 5,178.93 | -0.45 | 0.9920 | 29.03% | 1,731.18 |
| development | 1 | 923 | 2,890.94 | 3,826.08 | 3.13 | 1.0606 | 33.22% | 1,126.06 |
| development | 2 | 923 | 1,599.44 | 4,637.58 | 1.73 | 1.0332 | 33.18% | 1,126.06 |
| development | 4 | 923 | -174.56 | 5,178.93 | -0.19 | 0.9964 | 33.16% | 1,126.06 |
| diagnostic | 1 | 496 | 1,070.38 | 2,281.36 | 2.16 | 1.0355 | 21.75% | 605.12 |
| diagnostic | 2 | 496 | 486.88 | 2,392.84 | 0.98 | 1.0160 | 21.72% | 605.12 |
| diagnostic | 4 | 496 | -465.12 | 2,950.84 | -0.94 | 0.9850 | 21.46% | 605.12 |

### Long 100/200 neighbor

`overnight_long_100`

| Period | Ticks/side | Trades | Net | DD | Expectancy | PF | Exposure | Fees |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full | 1 | 1110 | 1,552.80 | 4,273.42 | 1.40 | 1.0195 | 34.69% | 1,354.20 |
| full | 2 | 1109 | 557.52 | 4,363.30 | 0.50 | 1.0070 | 34.65% | 1,352.98 |
| full | 4 | 1107 | -1,657.04 | 4,633.85 | -1.50 | 0.9796 | 34.50% | 1,350.54 |
| development | 1 | 730 | 2,050.40 | 4,273.42 | 2.81 | 1.0458 | 37.79% | 890.60 |
| development | 2 | 729 | 1,336.12 | 4,363.30 | 1.83 | 1.0297 | 37.73% | 889.38 |
| development | 4 | 727 | -67.94 | 4,373.80 | -0.09 | 0.9985 | 37.53% | 886.94 |
| diagnostic | 1 | 380 | -497.60 | 3,997.53 | -1.31 | 0.9858 | 29.02% | 463.60 |
| diagnostic | 2 | 380 | -778.60 | 4,071.03 | -2.05 | 0.9778 | 29.02% | 463.60 |
| diagnostic | 4 | 380 | -1,589.10 | 4,218.03 | -4.18 | 0.9553 | 28.95% | 463.60 |

### Short 75/150 control

`overnight_short_control_75`

| Period | Ticks/side | Trades | Net | DD | Expectancy | PF | Exposure | Fees |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full | 1 | 1099 | -11,893.28 | 14,189.70 | -10.82 | 0.8534 | 29.40% | 1,340.78 |
| full | 2 | 1099 | -12,980.78 | 15,207.70 | -11.81 | 0.8410 | 29.36% | 1,340.78 |
| full | 4 | 1096 | -15,858.62 | 17,649.76 | -14.47 | 0.8087 | 29.30% | 1,337.12 |
| development | 1 | 720 | -10,814.40 | 12,185.46 | -15.02 | 0.7851 | 32.73% | 878.40 |
| development | 2 | 720 | -11,652.90 | 12,942.96 | -16.18 | 0.7702 | 32.68% | 878.40 |
| development | 4 | 719 | -12,976.18 | 14,104.24 | -18.05 | 0.7481 | 32.56% | 877.18 |
| diagnostic | 1 | 379 | -1,078.88 | 4,227.14 | -2.85 | 0.9650 | 23.29% | 462.38 |
| diagnostic | 2 | 379 | -1,327.88 | 4,371.14 | -3.50 | 0.9571 | 23.28% | 462.38 |
| diagnostic | 4 | 377 | -2,882.44 | 5,418.92 | -7.65 | 0.9082 | 23.33% | 459.94 |

## Unchanged acceptance gates

The original 75/150 long center is evaluated without relaxing any threshold.

| Center requirement | Observed | Required | Result |
| --- | --- | --- | --- |
| Full one-tick trade count | 1124 | >= 500 | pass |
| Development net, one tick | 1,000.7000 | > 0 | pass |
| Development net, two ticks | 117.4200 | > 0 | pass |
| Diagnostic net, one tick | 262.0200 | > 0 | pass |
| Diagnostic net, two ticks | 5.0200 | > 0 | pass |
| Profitable complete years, 2020–2025 | 4 | >= 5 | fail |
| Diagnostic one-tick PF | 1.0086 | >= 1.10 | fail |
| 5-session bootstrap lower 95% mean | -4.5731 | > 0 | fail |
| 20-session bootstrap lower 95% mean | -4.7871 | > 0 | fail |
| Net excluding best five trades | -228.6800 | > 0 | fail |
| Net excluding best calendar year | -619.7800 | > 0 | fail |
| Both neighbors positive full and diagnostic at 1/2 ticks | Wider diagnostic -497.60 / -778.60 | > 0 in each case | fail |

The tighter neighbor's diagnostic PF is 1.0355 and both bootstrap lower endpoints are negative. The wider neighbor fails diagnostic profitability, PF, both uncertainty gates and both concentration exclusions. The short control fails every numerical gate except the 500-trade minimum. Thus none passes its numerical screen; the center additionally fails its registered neighbor requirement. The generic machine screen still lists neighboring variants as pending because it does not link separate candidates automatically; the table above resolves that requirement from the complete fixed neighbor runs.

### Full one-tick uncertainty and concentration

The fixed 2,000-repetition, 5/20-session circular moving-block bootstrap uses exact session-marked equity changes. Intervals below are for mean dollars per session. They describe this reused sample, do not adjust for repeated strategy selection, and are not probabilities of future profitability.

| Variant | 5-session 95% mean interval | 20-session 95% mean interval | Net without best 5 trades | Net without best year | Net without best 5 sessions |
| --- | --- | --- | --- | --- | --- |
| Long 75/150 center | [-4.5731, 6.2112] | [-4.7871, 5.9604] | -228.68 | -619.78 | -1,469.35 |
| Long 50/100 neighbor | [-3.0127, 7.4235] | [-2.5099, 6.8016] | 2,969.92 | 1,555.10 | 1,902.97 |
| Long 100/200 neighbor | [-5.2054, 7.1066] | [-5.4047, 6.9240] | -438.60 | -1,435.12 | -1,488.60 |
| Short 75/150 control | [-11.8188, -1.0475] | [-10.9709, -1.5669] | -13,384.68 | -12,572.90 | -14,321.46 |

### Full one-tick yearly net

Session-marked dollars allocate overnight unrealized P&L to the appropriate sessions. The 2019 and 2026 rows are partial years and do not count toward the five-profitable-years gate.

| Year | Long 75/150 center | Long 50/100 neighbor | Long 100/200 neighbor | Short 75/150 control |
| --- | --- | --- | --- | --- |
| 2019 (partial) | 633.54 | 874.62 | 596.80 | -1,484.80 |
| 2020 | 445.80 | 1,847.00 | 243.52 | -5,816.46 |
| 2021 | 1,100.86 | 2,406.22 | 2,987.92 | -2,190.72 |
| 2022 | -2,055.46 | -2,632.56 | -2,190.90 | 352.16 |
| 2023 | 875.96 | 395.66 | 413.06 | -1,674.58 |
| 2024 | -464.82 | 754.66 | 359.68 | 679.62 |
| 2025 | 1,882.50 | 46.60 | 1,967.22 | -2,257.06 |
| 2026 (partial) | -1,155.66 | 269.12 | -2,824.50 | 498.56 |

## Independent integrity and risk audit

The all-36 audit verified frozen manifest/source/protocol checksums, declared parameters and periods, actual fill fees and quantities, trade/session ledger hashes, P&L arithmetic, source identities, entry timestamps, fixed brackets, clock deadlines and entry-day proposed-stop reservations. It recomputed every numerical screen and matched the saved terminal results. All 555 files in the three A09 runs and the A07 reference run retained their hashes. The complete export contains 36 trade CSVs and 28,488 rows across overlapping period/cost cases; CSV counts, quantities and actual fees match immutable JSON.

The separate execution audit checked all 4,752 full-history one-tick trades across the four variants against 2,532,300 continuous minutes joined to raw outright OHLC and instrument identity. It rebuilt all 4,752 entry signal bars from exactly five then-known same-delivery minutes and verified all 77 carried-roll exits against the old delivery's observed minute. Its independent accounting and execution checks found zero discrepancies. No clock exit was late in any of the 36 ledgers. The raw-minute audit independently verified clock execution timing for the four full one-tick ledgers.

Every Monday–Thursday 16:00 full-period decision is explained. Each variant has the same 64 current-calendar exclusions, 13 next-date calendar exclusions and two insufficient same-delivery warmups. The remaining entry and risk-block counts are:

| Variant | Entries | Proposed-stop budget skips | Already-breached daily loss skips |
| --- | --- | --- | --- |
| Long 75/150 center | 1124 | 296 | 1 |
| Long 50/100 neighbor | 1419 | 1 | 1 |
| Long 100/200 neighbor | 1110 | 310 | 1 |
| Short 75/150 control | 1099 | 322 | 0 |

The daily gate books a trade's whole net result on its exit trading day. A prior overnight loss can therefore block that afternoon's proposed stop; the center's 296 such exclusions are part of the declared policy. The gate is an entry restriction, not a guaranteed loss cap. At one tick, the worst single long trade is -$301.72 for each of the three long brackets, exceeding their nominal stop distances and the $250 gate. Wider stops do not eliminate overnight gap risk. The short control's worst trade is -$174.22.

Source coverage remains a limitation. The full-range diagnostics record 3,041 calendar-open minute gaps totaling 6,162 missing minutes, plus four absent five-minute aggregates overlapping the configured entry window. The warning's zero missing one-minute bars within the entry window does not imply complete overnight holding coverage. Known exchange maintenance and holidays are handled separately; the calendar audit is bounded, not a certification of every historical exception. Observed OHLC cannot recover intraminute order paths, missing prices, live queueing, margin or transport behavior.

## Artifacts and decision

Complete [report](../backend/storage/research/reports/20260905T013704.193922Z-overnight-complete-1a59d901cd5d/report.md), [case metrics CSV](../backend/storage/research/reports/20260905T013704.193922Z-overnight-complete-1a59d901cd5d/case-metrics.csv), [trade CSV directory](../backend/storage/research/reports/20260905T013704.193922Z-overnight-complete-1a59d901cd5d/trades) and [export audit index](../backend/storage/research/reports/20260905T013704.193922Z-overnight-complete-1a59d901cd5d/export-index.json). The [all-36 audit](../backend/storage/research/overnight-audits/20260905T013217.563827Z-a09-final-audit-d4ce7c994d71/a09-audit.json) and [audit script](../backend/storage/research/overnight-audits/20260905T013217.563827Z-a09-final-audit-d4ce7c994d71/audit_a09.py) are stored separately from every original run.

[Independent raw execution proof](../backend/storage/research/overnight-audits/20260905-independent-full-one-tick/verification.json), SHA-256 `9189d24741729153170eb8795e2e56ef4af67768bbda6bdcb9327a9e2612e8e8`.

[Entry-date/risk reconciliation](../backend/storage/research/overnight-audits/20260905-independent-full-one-tick/entry-date-risk-reconciliation.json), SHA-256 `a7c0b07b8778ec50b62d45c6781326e82a79c97c2fb13162c515acfd3937c7bf`.

The all-36 audit SHA-256 is `f7cda17112d96d42281064af62b1fe2aa8da30978b8b3c85abf8664b566ee97a`.

Input runs (all immutable):

- `20260905T012732.368477Z-overnight-center-neighbor-f6b1977f9ae8`
- `20260905T012742.618736Z-overnight-wide-neighbor-f3f2be3017f6`
- `20260905T012753.026037Z-overnight-short-control-2883a70a5112`

The audit executed `backend/.venv/Scripts/python.exe backend/storage/research/overnight-audits/20260905T013217.563827Z-a09-final-audit-d4ce7c994d71/audit_a09.py`. Its only subprocess was `backend/tools/summarize_topbot_research.py --runs` with the three directories above and `--label overnight-complete`; this reads existing results and writes new exports, without launching a replay.

No overnight rule is promoted and no neighbor is selected. Delayed-entry stress, stricter target-fill stress and live runtime parity have not been demonstrated for this overnight fixture. They remain requirements for a future qualifying candidate, not a reason to advance this failed one. Independent confirmation would also require newly reserved observations meeting the existing threshold of at least 200 trades and six months. The original history and the entire A08 ProjectX pool are already exposed; neither can be relabeled untouched. No new hypothesis or replay is launched by this audit.
