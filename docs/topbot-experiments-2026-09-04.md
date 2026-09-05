# TopBot research experiment record — September 4, 2026

Research branch: `feature/mnq-credible-research`, original commit `592791b`.
Protocol: [predeclared hypotheses and acceptance gates](topbot-research-protocol-2026-09-04.md).
All 2019–July 2026 data is reused historical evidence. No candidate has yet met
the independent-evidence requirement. No real orders or live runs were started.

## A00 — reproduce the original baseline

Hypothesis: the v5 evaluator and original replay should exactly reproduce the
saved checkpoint before changing execution assumptions or strategy rules.

Command from repository root:

```powershell
& backend/.venv/Scripts/python.exe backend/tools/benchmark_topbot_replay.py --days 3000 --holdout --output backend/storage/research/audit-baseline-20260904.json --trades-output backend/storage/research/audit-baseline-20260904-trades.json
```

Outcome: **reproduced**. All metrics and the complete 4,798-trade ledger exactly
match `checkpoint-baseline.json`. Ledger SHA-256:
`6c76cb6d116e58bb4ea01f8b9e698bcd163c1fd3a31dd3d3edc3761bac56f39a`.
Source fingerprint:
`cd56b8dbe08abc26b6bbbb9351e337984c603fe2562942ecb85ad0b9383a897d`.
This is the original engine's reproducible result, **not a corrected fill-model
claim**; the audit below identifies material limitations.

| Measure | Original v5 |
| --- | ---: |
| Net P&L | -$11,015.70 |
| Gross P&L after modeled slippage | $499.50 |
| Commissions | $11,515.20 |
| Trades | 4,798 |
| Expectancy per trade | -$2.2959 |
| Profit factor | 0.9551 |
| Maximum drawdown, observed bar-close marks | $16,039.30 |
| Exposure, fraction of execution bars | 23.07% |
| Long trades / net | 3,026 / -$1,383.90 |
| Short trades / net | 1,772 / -$9,631.80 |

The main baseline problem is insufficient gross edge to pay fees, especially
on shorts. Trades held less than one hour contributed -$19,001.20 across 3,298
trades; 1–6 hour holds contributed $3,290.80 and longer holds $4,694.70. These are
post-outcome descriptive groups, not usable entry filters or causal estimates.
Seven stop gaps lost $1,515.30 total. Seven roll exits contributed -$91.30 under
the original noncausal previous-close assumption.

| Entry year | Trades | Net P&L |
| --- | ---: | ---: |
| 2019, partial and sparse | 191 | $643.10 |
| 2020 | 626 | $1,849.10 |
| 2021 | 583 | -$4,409.70 |
| 2022 | 814 | $2,770.40 |
| 2023 | 639 | -$2,953.10 |
| 2024 | 657 | -$3,718.80 |
| 2025 | 793 | -$3,080.20 |
| 2026, partial | 495 | -$2,116.50 |

## A01 — audit corrections before candidate selection

The original engine executes resting brackets only on complete five-minute
signal bars. Real observed minutes inside an excluded aggregate can therefore
be skipped. The new optional execution stream processes every observed one-minute
bar while generating decisions only at an actual newly closed five-minute bar.
Missing next-minute executions discard the entry instead of delaying it across a
gap. Intrabar exits retain conservative stop-first ordering and use the bar close
as the upper bound of the unknown execution time for cooldown accounting.

The original contract-roll exit uses the previous delivery's final close after
the new session's roll is detected. Corrected research requires an actual
old-delivery open at the known roll time or fails explicitly. It cannot substitute
the new contract price or look backward to a favorable previous close.

The calendar incorrectly retained the equity-index 16:15–16:30 Eastern halt
after CME removed it on June 28, 2021. The corrected calendar preserves the halt
for earlier dates and requires a new cache version. A separate rebuilt cache
keeps the original baseline artifacts intact. See the data audit for provenance,
coverage and remaining historical-calendar limitations.

## A02 — minute execution, before daily-risk parity correction

Run `20260905T002140.238988Z-corrected-baseline-6cdcad805f0b` replayed the full
baseline in 72 seconds against corrected data with one-minute execution. It
produced 4,696 trades, -$9,514.40 net, $1,756 gross, $11,270.40 commissions,
0.9603 profit factor, -$2.0261 expectancy and $14,741.20 minute-close drawdown.
Complete ledger SHA-256:
`bfd742b4ae24602ef1c633699b57a53a6be9e728226f9adcb8026ed86724c392`.
Ledger, marked session returns and cash accounting reconcile. This remains an
intermediate diagnostic: the subsequent live audit found a risk-gate mismatch.

Three predeclared batches launched together with identical frozen code and
protocol, planning 72 cases across eight variants, three periods and three
slippage settings. They were deliberately stopped after the risk defect was
identified. Their `interruption.json` records explain the stop; all completed
results and incomplete `.started.json` cases remain preserved:

- `20260905T002426.194903Z-hypotheses-controls-5777400469ac`: one completed,
  one interrupted case.
- `20260905T002426.361369Z-hypotheses-opening-50e1a7cb8eb7`: one completed,
  one interrupted case.
- `20260905T002426.589342Z-hypotheses-alternatives-d9fc6f6e2f88`: one interrupted
  case, no completed cases.

These results are preliminary and are not eligible for selecting a strategy.
The baseline repeat is a reproducibility check, not another strategy hypothesis.
There has been no outcome-based change to the seven registered candidate rules.

## A03 — match the live daily-risk gate

Hypothesis: a chronological replay must reject any new position whose proposed
provider-held stop would consume the remaining daily-loss budget, because the
live risk gate already does so. The previous replay checked realized losses
alone and could accept trades the live bot rejects. Live also attributes full
round-trip trade net to the exit day when computing this gate; cash bookkeeping
can still charge each commission when incurred. The corrected mode must keep
these distinct accounting purposes explicit. A nonpositive account balance
must not finance further entries. No arbitrary margin number is invented.

The same candidate rules and test periods will be replayed after this parity
correction. Other identified integration work remains: the clock-based flatten
policy must act independently of signal-bar completeness in production, and
authoritative live positions must reach its strategy evaluator. No research
candidate is integrated or armed merely because its retrospective result is
positive.

Verification so far: the first broad backend run found three tests expecting
the obsolete 2026 halt; they were corrected to preserve historical coverage,
and a modern open-market regression was added. The complete rerun passed
1,510 tests with eight disposable-PostgreSQL skips and zero external network
attempts. Further tests cover the new risk correction before the next batch.

## A04 — fixed matrix with audited execution and calendar

Frozen code commit: `63b061146cd84ea4ef278315c5a54b51e8a30e43`.
Engine: `5.3.0-entry-latency-stress`. Corrected cache format: 6, source
`e900ae486308de577f0945e21cd54821ed2b206c027761d1973563a9085b4d6a`.
The three run manifests share source-bundle SHA-256
`eb4daae627b3a5fc078400fac5450dcfd4112da1384f4d9ac2d246763b6a4e1c`.
Each includes installed dependency versions and exact sources.

The final pre-batch backend suite passed **1,546 tests, eight PostgreSQL skips**,
with zero external connections. Log:
`backend/storage/research/audit-backend-tests-20260904-final-harness.log`.
The [calendar audit](topbot-calendar-audit-2026-09-04.md) records date-specific
closures and source-confidence limitations. All underlying 1m/5m/15m arrays
are byte-identical between cache versions 5 and 6.

The seven hypotheses and original control each receive full, development and
later diagnostic fresh portfolios at 1/2/4 tick adverse slippage: **72 cases,
eight distinct variants**. Base entry delay is zero; promising candidates also
require the one-minute delayed-entry stress. This is not a claim that the live
worker already implements these research policies; see the
[lifecycle comparison](topbot-live-replay-parity-2026-09-04.md).

Run directories under `backend/storage/research/experiments`:

- `20260905T003843.383966Z-parity-controls-b0cf5c51afd3`: baseline v5 and
  long-only baseline, 18 cases.
- `20260905T003843.544041Z-parity-opening-b098560f5cb6`: both-direction and
  long-only opening-range breakouts plus opening drive, 27 cases.
- `20260905T003843.714717Z-parity-alternatives-21c91ed79578`: long ATR pullback,
  VWAP reversion and afternoon momentum, 27 cases.

Each immutable manifest records the exact command, configuration and periods.
Summaries and ledgers appear after each case; `.started.json` alone does not
prove a process remains alive. `results.json` records batch completion. Never
restart because observation timed out; inspect the original process handle.

The 55,240-bar newer ProjectX pool through September 4 remains separately
reserved without any strategy-return inspection. A04 uses only already-exposed
2019–July 2026 Databento history. No candidate is selected or confirmed at launch.


## A05 — correct externally verified transaction fees

The user identified the TopstepX MNQ round-trip rate as $1.22. Verified against
Topstep's July 28 fee schedule: $0.61 per side, including exchange, NFA and
commission. The former $1.20-per-side default overstated round-trip costs by
$1.18. UI, API and all TopBot replay/research tools now default to $0.61.

The fee-only protocol, exact commands and full results are recorded in
[topbot-fee-correction.md](topbot-fee-correction.md), with machine-readable values
in `topbot-fee-corrected-baseline.json`. The app replay improved from -$11,015.70
to -$5,357.50; its 4,800 trades include two additional shorts allowed through the
risk gate. The observed-minute research baseline improved from -$8,433.60 to
-$3,449.28 on the same 4,224 trades. Engine, strategy, calendar, cache and all
non-fee trade fields match the saved A04 control. The combined baseline still
loses after costs. No candidate rules or untouched newer-data tests were run.

Corrected research run: `20260905T004802.745634Z-fee-corrected-baseline-1c25d5260c60`.
Existing A04 processes/manifests retain their original $1.20 fee; this correction
does not restart the 72-case batch. Preserve those results as higher-cost stress
cases and rerun the fixed comparisons at explicit `--commission-per-side 0.61`
before selecting a candidate. No live run was started.

## A06 — fixed candidate matrix at corrected base fees

Predeclared continuation after the fee correction: rerun all eight A04 variants,
each on full, development and diagnostic periods at 1/2/4 ticks of slippage,
with explicit `--commission-per-side 0.61` (72 fresh engine cases). Keep the
same engine, candidate fixture, v6 cache, initial balance, sizing, risk policy,
entry delay and acceptance criteria. The amended protocol changes the externally
verified base fee only. A repeated baseline is a reproducibility check, not a
new hypothesis. No strategy decisions use the incomplete corrected matrix.

Retain the completed and still-running A04 cases as **higher-cost stress tests
at $1.20 per side**. Their immutable manifests and ledgers remain unchanged.
Earlier A02 runs also have a risk-model defect and remain ineligible; reducing
their fee label does not repair that defect. Corrected cases use separate unique
`fee061-controls`, `fee061-opening` and `fee061-alternatives` run directories.
Every new manifest and completed ledger must verify the intended fee. Compare
matched cases within the observed-minute execution model, and report changes
in trading and risk gates instead of applying a refund to old P&L. The separate
newer-data pool stays reserved during these reused-history comparisons.

A04 completed all 72 cases with no replay or artifact/accounting failures.
The complete higher-cost report and CSV ledgers are in
`backend/storage/research/reports/20260905T005609.050325Z-fee120-stress-complete-4657ecf445a8`.
A separate retention audit verified all old source snapshots and ledger hashes.

A06 immutable run directories under `backend/storage/research/experiments`:

- `20260905T005437.879081Z-fee061-controls-049bbb66d292`: 18 cases.
- `20260905T005448.370797Z-fee061-opening-8482f9451110`: 27 cases.
- `20260905T005537.092354Z-fee061-alternatives-7788ffa0563a`: 27 cases.

All three manifests explicitly record 0.61, observed-minute execution and zero
additional entry delay. The repeated full baseline reproduces -$3,449.28 net
on 4,224 trades. The paired audit tool is documented in
[the fee-comparison audit](topbot-fee-comparison-audit-2026-09-04.md); a complete
report requires every matched case, terminal run indexes, preserved source
hashes and reconciled actual ledger fees. Partial snapshots remain labeled
incomplete. The comparison also includes the old v4 filters through a separate
legacy execution study; those outputs cannot be pooled with observed-minute
results.

Follow-up hypotheses prepared without running them are recorded in the
[conditional opening-drive checks](topbot-opening-drive-robustness-2026-09-04.md)
and [conditional overnight protocol](topbot-overnight-protocol-2026-09-04.md).
Their preparation does not select a strategy or relax the original criteria.

The combined backend suite after fee correction and preparation of these
isolated research tools passed **1,672 tests, eight PostgreSQL skips**, in
34.11 seconds. The offline guard recorded zero external connection attempts.
Log: `backend/storage/research/fee-correction-research-tests-20260905.log`.
The fee UI's previously reported 22 focused frontend tests, lint and production
build remain applicable; this continuation made no further frontend changes.

### A06 completion and decision

All **72 corrected cases matched** their original higher-cost controls, with
zero execution, compatibility or accounting failures. The export preserved all
1,146 files across the six runs byte-for-byte (570 old and 576 corrected).
Every corrected replay records 0.61 per side; each one-contract trade records
1.22 round trip. Seventeen paired cases changed their fill paths (eight ATR
pullback and nine VWAP reversion cases); 55 retained identical non-fee ledgers.
The repeated corrected baseline exactly reproduces the earlier corrected run.

Complete paired audit:
`backend/storage/research/fee-comparisons/20260905T011243.016116Z-matched-fee-complete-1aa93aa653e1`.
Complete corrected report and CSV ledgers:
`backend/storage/research/reports/20260905T011243.976136Z-fee061-complete-6e23a5c58210`.

Only unchanged opening drive passes all measured registered historical gates:
543 trades, $11,413.04 full net, $2,692.84 drawdown and $21.02 expectancy at one
tick. Development net is $9,413.30 / $9,225.80 at one/two ticks; diagnostic net
is $1,999.74 / $1,878.24, with one-tick profit factor 1.1141. Full four-tick net
is $10,054.04. This is a retrospective shortlist result, not confirmed future
profitability. The other seven variants fail at least one unchanged gate.

The separate legacy study completed all 48 cases with zero audit errors. Its
24 fee pairs keep all non-fee controls fixed, and all 11 saved historical
comparisons reproduce their metrics and trade hashes. The old entry-distance
filter improves but remains negative in the later period; the extra trend
filter also remains negative. See the complete
[legacy fee reconsideration](topbot-legacy-fee-audit-2026-09-04.md).

## A07 — predeclared robustness of unchanged opening drive

The A06 result triggers the previously recorded
[opening-drive robustness protocol](topbot-opening-drive-robustness-2026-09-04.md).
Run 28 full/diagnostic one/two-tick cases covering the center and six fixed
one-at-a-time neighbors, nine delayed-entry cases, and nine target-trade-through
cases. All 46 use explicit 0.61 fees and the same reused v6 data. No parameter
is chosen from interim results, and no candidate is promoted to production.
The overnight hypotheses remain prepared but deferred while this candidate
receives the specified robustness checks. The newer reserved pool is untouched.

### A07 completion and frozen-parameter decision

All 46 cases completed and passed independent source, cost, ledger and timing
checks. All six neighbors retain positive full and diagnostic net at one and
two ticks. The registered delayed-entry and target-trade-through checks also
retain positive development and diagnostic net at one and two ticks. Four
unchanged-center cases exactly reproduce A06's trades, metrics, session ledgers
and equity curves. Every trade pays $1.22 round trip. Nine distinct parameter
and execution groups remain separate; all 822 checked A07/reference input files
are unchanged.

Full report and all 46 CSV ledgers:
`backend/storage/research/reports/20260905T012024.452949Z-opening-drive-robustness-complete-200de24845e4`.
Independent audit:
`backend/storage/research/robustness-audits/20260905T011708.987434Z-a07-final-audit-346333828b76/a07-audit.json`.

Retain the **original opening-drive center**; do not choose the best-performing
neighbor. These remain reused-history results. Prepare a frozen preliminary
evaluation on the reserved newer ProjectX pool only after its isolated adapter
and protocol pass review. No newer prices or strategy outcomes have been opened
at this decision. Forty sessions cannot meet the original six-month/200-trade
confirmation target, regardless of their eventual P&L. Production integration
remains conditional on the evidence and parity requirements; no live run was
started.

## A08 — frozen original candidate on reserved newer observations

Before viewing any new outcome, retain the unchanged opening-drive center and
the [predeclared newer-data protocol](topbot-unseen-opening-drive-protocol-2026-09-04.md).
Metadata-only preparation is
`backend/storage/research/unseen-preparations/20260905T012339.618436Z-projectx-unseen-prepared-c8255427c5af`.
Its immutable manifest SHA-256 is
`fe880f413c5c2cd3db1addc0507cafc2ec5cf8da09cdac0c430d8cef67d3af6f`.
All 56 A06 application/runner/fixture lineage files match. Thirty synthetic
adapter tests passed, the independent code review found no blocker, and the
metadata-open guard recorded zero raw-price reads or attempts during preparation.

The parent now authorizes the three predeclared offline evaluation cases under
the user's existing research authority. An internal decision receipt binds the
frozen manifest and passed A07 audit before price access. Use all 40 complete
ProjectX MNQU6 sessions, same-pool warmup, fresh $50,000 portfolios, one contract,
0.61 per-side fees and 1/2/4 ticks of slippage. No strategy, date range or
acceptance threshold changes. This is preliminary evidence: no result can meet
the original six-month/200-trade independent confirmation target. Once evaluated,
the whole pool is exposed and cannot be reused as untouched data. Preserve all
outputs and report failure as well as success. No live action is authorized.

### A08 observed result

All three frozen cases completed. Twelve trades produced net -$272.64 / -$279.14
/ -$892.14 at one/two/four ticks, with $14.64 total fees per case. The unchanged
candidate fails the predeclared positive-net preliminary screen. The one-tick
expectancy is -$22.72, profit factor 0.8182 and minute-close drawdown $856.49.
Forty sessions and twelve trades remain too small for confirmation or a
statistical claim that the underlying edge is absent. The original center is
not promoted; no neighbor or date subset replaces it. Independent output audit
and CSV export follow without rerunning or retuning.

The entire newer ProjectX pool is now **exposed**, first evaluated under the
frozen original candidate. It must never be labeled untouched in subsequent
research. Its original collection manifest and raw files remain unchanged.

### A08 audit and export completion

The [completed independent audit](topbot-unseen-opening-drive-results-2026-09-04.md)
found zero errors. It checked 77 frozen source files, 56 A06 lineage matches,
all 55,240 raw minutes, all 40 sessions, all 36 cost-case trade records, recorded
equity and drawdown, fees, concentration and uncertainty. All 108 input files
retained their hashes. The complete trade/session CSV exports match their JSON
records. No strategy was rerun or retuned during the audit.

Audit and complete exports:
`backend/storage/research/unseen-audits/20260905T013111.271967Z-a08-audit-1359ab18f0cc`.
The audit SHA-256 is
`7bb3766b6d1d768aef30b393e7322f83e90b93f4fa86e469da5c49208b5f8b36`.
The candidate remains unpromoted with terminal status
`fails_predeclared_preliminary_screen`.

## A09 — previously declared overnight hypotheses

After A08 fails its advancement screen, continue the bounded overnight set
prepared before A08 outcomes. The [overnight protocol](topbot-overnight-protocol-2026-09-04.md)
and original fixture hash remain unchanged: one fixed 75/150-point long center,
50/100 and 100/200 neighbors, and a 75/150 short control. Monday–Thursday exact
16:00 entry, next-local-date 09:25 independent clock, same one-contract sizing,
calendar exclusions and daily risk controls. These rules are not derived from
the newly exposed twelve-trade result.

Replay only the original reused v6 Databento history, all three registered
periods at 1/2/4 ticks, explicit 0.61 fees: 36 cases. Apply the unchanged original
historical gates and disclose every failure. Do not include or reuse the newer
pool in this stage. A later independent evaluation would require new reserved
observations; no current result can be promoted merely by relabeling old data.

### A09 completion and fixed-center decision

All 36 cases completed with explicit $0.61 per-side fees. The [complete A09
results and audit](topbot-overnight-results-2026-09-04.md) retain every variant,
fresh period and 1/2/4-tick scenario. All four fail the unchanged numerical
screen; the original long 75/150 center is not promoted and no neighbor replaces it.

| Fixed variant | Full one-tick trades | Full one-tick net | Full one-tick DD | Full four-tick net | Numerical screen |
| --- | ---: | ---: | ---: | ---: | --- |
| Long 75/150 center | 1,124 | $1,262.72 | $2,968.76 | -$2,519.90 | Fail |
| Long 50/100 neighbor | 1,419 | $3,961.32 | $3,826.08 | -$639.68 | Fail |
| Long 100/200 neighbor | 1,110 | $1,552.80 | $4,273.42 | -$1,657.04 | Fail |
| Short 75/150 control | 1,099 | -$11,893.28 | $14,189.70 | -$15,858.62 | Fail |

The center has only four profitable complete years, diagnostic PF 1.0086,
negative 5/20-session bootstrap lower bounds, net -$228.68 after removing its
best five trades and -$619.78 after removing its best year. Its development and
diagnostic two-tick nets are just $117.42 and $5.02. The wider neighbor loses
$497.60/$778.60 in the diagnostic period at one/two ticks, failing the required
neighbor robustness. The tighter neighbor fails diagnostic PF and both
uncertainty gates. No thresholds or dates were changed after these outcomes.

The independent all-36 source/fee/ledger audit found zero discrepancies and
retained all 555 A09/A07 reference file hashes. It exported 36 trade CSVs with
28,488 overlapping period/cost records. A separate full one-tick audit verified
all 4,752 trades and 77 carried-roll exits against raw outright minutes and
explained every entry omission, including the center's 296 proposed-stop budget
blocks. No late clock exits were found. These checks establish consistency with
the specified model, while overnight gaps and live-runtime limitations remain.

Complete exports:
`backend/storage/research/reports/20260905T013704.193922Z-overnight-complete-1a59d901cd5d`.
All-36 audit:
`backend/storage/research/overnight-audits/20260905T013217.563827Z-a09-final-audit-d4ce7c994d71/a09-audit.json`,
SHA-256 `f7cda17112d96d42281064af62b1fe2aa8da30978b8b3c85abf8664b566ee97a`.
The results document links the independently verified raw-execution and
entry-date/risk reconciliation proofs. No replay was launched by the audit.

This bounded round ends without a promoted strategy. The overnight fixture has
not passed delayed-entry, stricter target-fill or live-runtime parity checks;
its failed necessary gates do not justify advancing it to those stages here.
The original history and entire newer A08 ProjectX pool are now exposed. No
independent observations remain available, and no further hypothesis or
retuned candidate is selected in this round.
