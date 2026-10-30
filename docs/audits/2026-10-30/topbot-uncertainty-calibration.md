# TopBot uncertainty penalty and mean-calibration experiment

Completed October 30, 2026. Reducing the uncertainty penalty did not reveal a
reliable trading edge in this experiment. No replacement passed the predeclared
research gates. The host remains in paper mode with its existing model and
trading settings unchanged.

## What was tested

The existing deduction is a day-cluster/HAC standard error multiplied by a
Student-t critical value, plus a $1 cost-uncertainty buffer. Its registered
confidence setting is 1% divided across eight tests, giving roughly a 3x
multiplier. Expected net profit already includes the modeled $3.22 round-trip
trading costs. Entry requires the remaining score to be strictly greater than
$1. A more permissive multiplier is a different confidence policy, not an
automatic statistical correction.

Eight fixed policies were compared for Bayesian cells, kernel paths, and the
empirical pool, giving 24 configurations:

| Policy | Deduction or decision rule |
| --- | --- |
| Registered control | Existing critical value times SE, plus $1 |
| Two-action t95 | t(0.975) times SE, plus $1 |
| One-SE diagnostic | One standard error plus $1 |
| Cost-buffer-only diagnostic | Retain $1, remove the statistical deduction |
| Zero-penalty diagnostic | Remove both extra deductions; modeled trading costs remain |
| Block joint95 | Resample trading days; estimate the joint BUY/SELL sampling error at each query |
| Block daily95 | Same resampling, with a fixed nominal .05/72 error budget per query |
| Rolling mean calibration95 | Correct expected returns using earlier out-of-sample forecasts, then deduct calibration and model uncertainty |

All variants retain the minimum training/effective-day requirements, delayed
paper-v2 entry, original deadline, one-contract sizing, position exclusion,
cooldown, entry cap, and daily loss rule. The host implementation was not edited.

The common evaluation period comprises 40 sessions, August 28 through October
23, 2026: 2,880 decisions per model/policy. The preceding 20 sessions, July 31
through August 27, initialize calibration. Raw model fits use the same prior
240-session policy, last 4,096 training paths, and label embargo as the previous
comparison. Calibration expands using only completed earlier out-of-sample
days. Scoring uses 960 nonoverlapping forecasts per side in the primary period.

This history was already inspected in earlier research. Chronological ordering
prevents within-run future-label leakage; it does not turn reused history into
an untouched holdout.

## Trading results

Values below are executed simulated trades and net P&L after modeled costs,
with the existing risk and position rules applied. Signals and executions are
stored separately in the detailed results.

| Policy | Bayesian trades / P&L | Kernel trades / P&L | Pool trades / P&L |
| --- | ---: | ---: | ---: |
| Registered control | 0 / $0.00 | 0 / $0.00 | 0 / $0.00 |
| Two-action t95 | 0 / $0.00 | 0 / $0.00 | 0 / $0.00 |
| One-SE diagnostic | 0 / $0.00 | 5 / +$46.40 | 0 / $0.00 |
| Cost buffer only | 5 / -$400.10 | 50 / -$316.50 | 0 / $0.00 |
| Zero extra penalty | 19 / -$579.18 | 79 / -$985.38 | 0 / $0.00 |
| Block joint95 | 0 / $0.00 | 0 / $0.00 | 0 / $0.00 |
| Block daily95 | 0 / $0.00 | 0 / $0.00 | 0 / $0.00 |
| Rolling mean calibration95 | 0 / $0.00 | 0 / $0.00 | 0 / $0.00 |

The Bayesian median deduction fell from $6.56 to $4.64 with joint day-block
resampling, yet its best entry score remained -$3.86. Kernel's median fell from
$5.54 to $4.01, with a best score of -$1.63. Thus more permissive, empirically
estimated pointwise deductions still did not clear the $1 entry requirement.
The daily-budget version was more conservative: median deductions were $7.28
and $6.18 respectively. These confidence scopes are different; they are not
interchangeable guarantees.

The five kernel one-SE trades are insufficient evidence of a replacement. Its
corrected mean-daily-P&L intervals were [-$15.54, $18.11] using five-day blocks
and [-$10.52, $9.36] using ten-day blocks. It had no trades in the first
20-session half, a $115.22 worst-tail loss estimate, and negative P&L after
removing the five best days. It failed the minimum sample and positive lower
bound requirements even though its aggregate P&L stayed positive under the
execution stresses.

Removing all extra deductions from kernel also produced a $1,398.44 closed-equity
drawdown. Its first half made $332.46, followed by a $1,317.84 loss in the second
half. This is a warning against selecting a permissive setting from a short
profitable stretch.

Five execution stresses were run for every candidate: two- and four-tick
spreads, two- and four-tick slippage, and one-tick target trade-through. They
preserved the base decision policy while changing fills, costs, exits, and
resulting risk availability. All four negative-P&L ablations remained negative
under every stress. No candidate passed all frozen research gates.

## What the calibration experiment found

The implemented calibration method fits a separate BUY/SELL affine relation
between predicted and realized net return, normalized by stop risk. Days receive
equal weight; the slope is constrained to [0,1]. Five- and ten-day block
resamples estimate coefficient uncertainty. Only past out-of-sample outcomes
are available to a fit. The decision bound reserves uncertainty for both the
calibration fit and the underlying model mean, plus the unchanged $1 buffer.

This improved Bayesian's average bias on the evaluation period:

| Bayesian metric | Original | Calibrated |
| --- | ---: | ---: |
| BUY mean forecast minus realized payoff | -$0.387 | -$0.022 |
| SELL mean forecast minus realized payoff | +$1.107 | +$0.075 |
| BUY RMSE | $56.051 | $55.967 |
| SELL RMSE | $57.973 | $57.916 |

The reductions in squared error were small and statistically inconclusive:
both block lengths' paired, corrected intervals included zero. Kernel and pool
did not show consistent error improvements. Bayesian's fitted slope was zero
on 35/40 BUY evaluation days and all 40 SELL days. In this constrained fit, the
data often favored discarding the model's variation between predictions rather
than trusting its larger positive estimates. This is evidence to investigate
conditional-mean quality, not proof that the model can never identify an edge.

The rolling calibrator is a concrete research implementation for further paper
evaluation. It is not established as a superior trading policy and was not
installed. The result supports retaining current settings while obtaining
prospective evidence, instead of reducing uncertainty until trades appear.

## Statistical scope and causal correction

Mean-uncertainty penalties concern an unknown expected payoff. Comparing each
bound with one realized trade outcome does not measure coverage of that mean;
we did not use such a hit rate to claim calibration. Day-block bootstrap
estimates also rely on weak dependence and sufficiently representative history.
Neither the bootstrap nor the constrained regression supplies a distribution-free
conditional-coverage guarantee under changing market conditions.

The primary analysis uses 20,000 five- and ten-day block replicates and a .05/24
comparison correction for the configurations in this experiment. This does not
erase historical model searches. Forty sessions and sparse trades limit tail
precision. The criteria included 200 trades, positive corrected daily-P&L lower
bounds, both halves profitable, robustness to removing the best five days/trades,
positive stress P&L, drawdown at most $1,000, and tail loss at most $100. Existing
full promotion requirements and prospective paper evidence would still apply.

Code review found that the first experimental daily maximum used later intraday
query features. Its artifacts were retained and superseded. A second frozen
plan replaced it with a fixed budget of .05/72 per query, and increased training
bootstrap replication to 20,000 for the extreme quantile. This removes dependence
on later query columns. A regression test verifies that appending future queries
does not change earlier penalties. This was a causal correction, not a
performance-driven threshold search. All final trading totals happened to be
unchanged; only the corrected `causal-r2` results support this report.

The methodology follows the temporal separation described in
[Forecasting: Principles and Practice](https://otexts.com/fpp3/toolbox.html).
The need to account for searching multiple alternatives is discussed in
[Hansen's Test for Superior Predictive Ability](https://www.tandfonline.com/doi/abs/10.1198/073500105000000063).
This experiment uses the declared block intervals and comparison correction,
not an implementation of Hansen's SPA test.

## Verification and reproducibility

`backend/tools/research_uncertainty_penalty.py` is an offline research tool.
It checks frozen input/plan hashes, disables external networking and live flags,
isolates the database environment, refuses an existing output directory, and
writes only under the local cache's `audits` directory. No production model,
configuration, database, or broker mutation was performed. No historical cloud
import or storage migration was needed.

Fourteen targeted tests passed, including future-label exclusion, future-query
invariance, minimum evidence gates, strict entry thresholds, day resampling,
calibration constraints, and offline isolation. All recomputed raw forecast
means matched the prior frozen forecasts exactly. The final host check at
7:57 a.m. Eastern confirmed configuration 32 and run 9 in paper mode, both live
flags false, zero bot order attempts, and no run error. Observation model file
hashes and the trading implementation were unchanged.

Final local evidence is in:

`backend/storage/databento/audits/uncertainty-calibration-20261030/causal-r2/`

Key files: `plan.json`, `plan-receipt.json`, `results/results.json`,
`results/decisions.jsonl.gz`, `results/ledgers.json`, `results/stress-ledgers.json`,
`results/mean-calibration-rows.json`, `calibration-diagnostics.json`,
`parity-and-causality.json`, and `tests.xml`. Host snapshots and preservation
checks are in the parent directory. These files are ignored by Git; this report
and the runner/tests are the repository record.

Reproduce from the repository root using a new output directory:

```powershell
backend/.venv/Scripts/python.exe -B backend/tools/research_uncertainty_penalty.py --plan backend/storage/databento/audits/uncertainty-calibration-20261030/causal-r2/plan.json --cache-root backend/storage/databento --output backend/storage/databento/audits/uncertainty-calibration-20261030/causal-r2/reproduction
```

Corrected plan SHA-256:
`03d4595ff3a4d7acf8d1d2dea7a9b785357eacbada57e11628039638047c4680`.
Unchanged trading implementation SHA-256:
`856cee44fec03ded157e8d64423a19573027417f13a4dcb07d541f2b091a6da5`.
