# Independent audit of the matched-fee research matrix

The original 72 observed-minute cases are preserved at $1.20 per contract per
side. The new matrix explicitly passes $0.61 per side for all eight original
hypotheses, each on fresh full/development/diagnostic periods and one/two/four
ticks of slippage. The original matrix is higher-fee stress evidence; the new
matrix supplies the corrected base-fee cases. No cases are repriced in place.

The fee correction was independently checked against
[Topstep's July 28, 2026 publication](https://help.topstep.com/en/articles/8284213-topstepx-commissions-and-fees).
It explicitly lists MNQ at $1.22 round trip and says each side incurs half the
round-trip amount. This is $0.61 per contract per side, including the specified
exchange, NFA and commission charges. Slippage remains separate. Applying this
current published fee throughout the replay models current costs; it does not
reconstruct historical fee schedules.

## Frozen comparison inputs

| Group | Original $1.20 run | Corrected $0.61 run |
| --- | --- | --- |
| Controls | `20260905T003843.383966Z-parity-controls-b0cf5c51afd3` | `20260905T005437.879081Z-fee061-controls-049bbb66d292` |
| Opening | `20260905T003843.544041Z-parity-opening-b098560f5cb6` | `20260905T005448.370797Z-fee061-opening-8482f9451110` |
| Alternatives | `20260905T003843.714717Z-parity-alternatives-21c91ed79578` | `20260905T005537.092354Z-fee061-alternatives-7788ffa0563a` |

Directories are under `backend/storage/research/experiments`. The earlier
interrupted risk-defect batches remain ineligible and are not matched to these
corrected cases. The separate one-case `fee-corrected-baseline` reproduction is
also excluded from this matrix to avoid duplicate baseline cases.

All three run pairs passed the immutable source audit. Each pair has 54 protected
source files with exact recorded and independently recomputed SHA-256 agreement.
These include the replay engine, original and research strategy, indicators,
risk implementation, calendar, data loader, model/config dependencies, and raw
old-contract roll resolver. The engine remains `5.3.0-entry-latency-stress`;
all cases use observed one-minute execution and zero additional entry delay.
The complete data manifests match, including format 6, source archives, series
identities and this source fingerprint:

`e900ae486308de577f0945e21cd54821ed2b206c027761d1973563a9085b4d6a`.

Complete source bundles deliberately differ. The changes are the API fee
default, three CLI fee defaults, the new fee constant, and an unused additional
fixture captured by the broad source snapshot. The comparison validates the
selected original fixture path; the extra fixture is not executed by these
72 cases. An AST comparison permits only the specifically reviewed runner/API
fee-default import, value and help-text edits. All other application source is
required to match byte for byte. The new fee module must contain only its
documentation and the declared `MNQ_FEES_PER_CONTRACT_PER_SIDE = 0.61` constant.
Unused tool changes remain listed in the generated source audit.

Candidate definitions and settings, cache manifests, chronological bounds,
starting cash, sizing, risk limits, slippage grid, tick economics, roll behavior,
calendar risk hook, runtime versions and bootstrap repetitions match exactly.
The original acceptance section matches after a narrowly disclosed punctuation
repair: some earlier captured protocol dashes were U+FFFD replacement
characters. Protocol comparison otherwise allows only the fee-line correction
and the exact published fee amendment. Threshold changes or additional
unreviewed amendment text fail the audit.

## Read-only comparison tool

`backend/tools/compare_topbot_fee_runs.py` matches cases by hypothesis, period
and slippage. It refuses duplicate cases and reports missing or incomplete
evidence. It independently validates every captured source against its manifest,
checks actual non-fee replay assumptions and evaluated ranges, and reconciles
each complete trade's fee, quantity, net/gross P&L, ledger hash and total fees.
Summary candidate metadata must agree with the pre-test manifest. Interrupted
runs or incompatible controls cannot become matched evidence.

The tool writes a new unique directory containing `report.md`,
`matched-fees.csv` and `comparison.json`. Inputs are read only; output inside an
input run is rejected. CSV/JSON preserve full old/new metrics, differences,
ledger hashes, source evidence, actual trade-count changes, and failed checks.
All trade fields except commission and net P&L are compared to detect changed
execution paths. The labeled old-ledger fee-refund counterfactual is reported
separately and never replaces a fresh replay. Periods overlap, so P&L is not
summed across the matrix.

Run after the matrix completes, or use a new label for an explicitly incomplete
snapshot. Exit status 1 means incomplete or incompatible evidence; only a
complete matching 72/72 comparison returns 0.

```powershell
$oldFeeRuns = @(
  'backend/storage/research/experiments/20260905T003843.383966Z-parity-controls-b0cf5c51afd3'
  'backend/storage/research/experiments/20260905T003843.544041Z-parity-opening-b098560f5cb6'
  'backend/storage/research/experiments/20260905T003843.714717Z-parity-alternatives-21c91ed79578'
)
$correctedFeeRuns = @(
  'backend/storage/research/experiments/20260905T005437.879081Z-fee061-controls-049bbb66d292'
  'backend/storage/research/experiments/20260905T005448.370797Z-fee061-opening-8482f9451110'
  'backend/storage/research/experiments/20260905T005537.092354Z-fee061-alternatives-7788ffa0563a'
)
& backend/.venv/Scripts/python.exe backend/tools/compare_topbot_fee_runs.py --old-runs $oldFeeRuns --new-runs $correctedFeeRuns --label complete-matched-fees
```

## Bounded audit snapshot and verification

The snapshot at September 5, 2026 00:59:07 UTC has **15 matched complete cases
and 57 incomplete cases**, with no incompatible controls. It is retained at:

`backend/storage/research/fee-comparisons/20260905T005907.008866Z-matched-fee-audit-f73f61250fd2`.

The baseline full one-tick case has unchanged 4,224 trades and fills; net P&L
changes from -$8,433.60 to -$3,449.28. Fee-sensitive behavior is already visible
in other original rules: full `v5_long_atr` changes from 2,307 to 2,310 trades
at one-tick slippage, and from 2,307 to 2,311 at two ticks. Thus a constant refund
on every old ledger would not satisfy the requested comparison. These examples
are audit observations, not strategy selection or a completed matrix result.

Six bounded integrity checks passed: reject non-fee runner edits; reject changed
acceptance thresholds/unreviewed amendments; detect corrupted source snapshots;
reject incorrect actual fees even with a newly supplied ledger hash; reject
duplicate matches while exposing missing cases; and reject a different selected
fixture. End-to-end verification additionally SHA-256 compared all **570 files**
in the three original completed runs before and after export and found no change.
The output-within-input protection also passed.

This work reads only explicitly named existing experiment artifacts. It does
not rerun strategies, inspect the newer-data quarantine, use provider/order
APIs, alter acceptance criteria, or certify future profitability. A completed
matched-fee report remains reused historical evidence; the registered neighbor,
untouched-data and live parity requirements remain separate.

## Completed 72-case matched-fee audit

The final audit completed September 5, 2026 at approximately 01:13 UTC. All
**72/72 old/new pairs matched**, with zero incompatible controls, execution
failures or accounting errors. All three corrected batches have terminal
`results.json` records. This completed result supersedes the incomplete status
of the earlier snapshots; those snapshots and every original run are retained.

Final comparison:
`backend/storage/research/fee-comparisons/20260905T011243.016116Z-matched-fee-complete-1aa93aa653e1`.

Complete corrected-fee report and all 72 CSV trade ledgers:
`backend/storage/research/reports/20260905T011243.976136Z-fee061-complete-6e23a5c58210`.

Every corrected case explicitly declares $0.61 per contract per side, every
closed one-contract trade charges $1.22, and total fees reconcile to the actual
ledger. Every source/settings/data/period/assumption check passed. A before/after
SHA-256 comparison preserved all **1,146 input files**: 570 original higher-fee
files and 576 corrected-fee files. Their aggregate file-identity digest is
`a5912966ea3a822d30d20f631562d7f6672f959e920a68037267e6e569e1ef3a`.

CSV row counts match all 72 source ledgers: 100,820 exported rows across the
overlapping periods and cost cases. This row total is an export completeness
check, not a count of independent trades or observations. All exported quantities
and commissions were also checked. The baseline full one-tick trade file and
metrics exactly reproduce the prior standalone corrected-fee baseline, including
ledger hash `9774d5bd9a3735188f3184ef7b07dc30d426c820af2359dfb95a57d40fe7d9ee`.

### All eight original rules

The first table uses full history and one tick of slippage. Old net uses $1.20
per side; corrected net and every other metric use $0.61. Drawdown is the
execution-bar-close equity measure defined in the replay assumptions. Dollar
values are rounded here; complete precision is preserved in the JSON/CSV.

| Variant | Old full net $ | Corrected full net $ | Corrected trades | Corrected max DD $ | Corrected expectancy $/trade | Numerical screen |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| afternoon_momentum | 3,428.60 | 4,627.48 | 1016 | 3,143.98 | 4.55 | Fail |
| baseline_v5 | -8,433.60 | -3,449.28 | 4224 | 11,263.96 | -0.82 | Fail |
| opening_drive | 10,772.30 | 11,413.04 | 543 | 2,692.84 | 21.02 | Pass measured gates only |
| orb30_both | -1,804.60 | 158.92 | 1664 | 3,737.11 | 0.10 | Fail |
| orb30_long | 1,786.20 | 2,838.76 | 892 | 2,856.19 | 3.18 | Fail |
| v5_long | -1,804.80 | 1,920.46 | 3157 | 9,134.52 | 0.61 | Fail |
| v5_long_atr | -6,273.30 | -3,301.70 | 2310 | 7,770.48 | -1.43 | Fail |
| vwap_reversion | -20,436.90 | -16,446.16 | 3003 | 17,791.61 | -5.48 | Fail |

All values below use corrected fees. Each chronological period starts fresh.
Development and diagnostic results can differ from the sum implied by a single
full-history run because positions, risk state and pending signals restart at
the split and period-end positions are closed. Do not add overlapping cases.

| Variant | Development 1 tick $ | Development 2 ticks $ | Diagnostic 1 tick $ | Diagnostic 2 ticks $ | Full 4 ticks $ | Fee-sensitive fill paths / 9 cases |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| afternoon_momentum | 6,866.42 | 5,831.42 | -2,238.94 | -2,490.44 | 1,167.98 | 0 |
| baseline_v5 | 2,696.58 | 326.80 | -6,107.36 | -7,711.92 | -16,461.82 | 0 |
| opening_drive | 9,413.30 | 9,225.80 | 1,999.74 | 1,878.24 | 10,054.04 | 0 |
| orb30_both | 2,251.60 | 1,625.60 | -2,092.68 | -2,558.68 | -3,876.58 | 0 |
| orb30_long | 2,431.26 | 2,105.76 | 407.50 | 81.50 | 889.26 | 0 |
| v5_long | 7,846.60 | 5,988.44 | -5,887.64 | -6,878.70 | -6,676.94 | 0 |
| v5_long_atr | 717.80 | -1,124.92 | -4,019.50 | -4,586.00 | -9,099.54 | 8 |
| vwap_reversion | -14,326.76 | -16,622.70 | -2,119.40 | -3,394.40 | -25,728.42 | 9 |

Opening drive is the only original hypothesis that passes every measured
registered gate. It has six positive complete marked calendar years among
2020–2025; diagnostic one-tick profit factor 1.1141186555; lower 95% mean-session
bootstrap endpoints of $1.9749 and $1.6754 for 5- and 20-session blocks; and
full net of $9,421.64 after removing the best five trades or $6,881.18 after
removing the best marked calendar year. Its exact status remains
`passes_measured_gates_only`, with `confirmed_profitability: false`. The separate
predeclared parameter-neighbor, execution-stress, runtime-parity and untouched
evaluation requirements are still outstanding. This audit launches or selects
no follow-up strategy.

Other rules retain every failure: the original v5, long-only v5, ATR rule,
both-direction opening breakout and afternoon momentum lose money in the later
diagnostic period; long-only opening breakout remains below the profit-factor
and bootstrap gates; VWAP reversion fails the net, year, profit-factor,
bootstrap and concentration gates. The complete
report preserves each failed check and all three slippage costs.

### Fees changed opportunities, not just accounting

Across the matrix, **17 cases have different fill paths** and 55 have identical
fill paths. The differences occur in eight ATR-rule cases and all nine VWAP
reversion cases. Three cases change paths despite unchanged trade counts:
`v5_long_atr__development__slip-4`, `v5_long_atr__full__slip-4`, and
`vwap_reversion__development__slip-4`. Lower fees affect daily risk gating and
later opportunities; subtracting a fee refund from the old P&L cannot establish
what those fresh replays would do. The original opening-drive fill paths are
identical at both fee rates, so its measured difference happens to equal the
fee saving on its unchanged trades.

The final machine-readable retention and matrix check is
`final-matrix-audit.json` inside the matched-fee output directory. The completed
comparison remains reused historical evidence and does not establish future
profitability. No newer-quarantine prices, provider/order APIs, live execution,
engine code, candidate rules or acceptance criteria were changed by this audit.
