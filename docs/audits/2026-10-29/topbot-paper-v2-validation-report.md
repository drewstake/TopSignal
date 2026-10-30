# TopBot paper v2 — local fit and validation

October 29, 2026. **Fit completed; deployment remains on hold.** A new immutable
observation candidate was fitted locally. The existing artifacts, production
system, thresholds and risk limits were preserved. No model was installed or
promoted, no broker call was made, and no deployment or service restart occurred.

The normal two-bar payoff checks passed. Validation found one numerical mismatch
in the supported one-bar diagnostic path. The model also produced no qualifying
signals in either chronological evaluation or the small post-training provider
sample. These results do not establish profitability or calibrated probabilities.

## Candidate and frozen inputs

The candidate is [candidate-v2.json](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/paper-v2-validation-20261029/candidate-v2.json).
All datasets and detailed results remain on the local filesystem under
`backend/storage/databento/audits/paper-v2-validation-20261029/`.

| Field | Result |
| --- | --- |
| Model | `bayesian_cells_v1`, fixed mixture `0.0`; no parameter search |
| Payoff | `post_record_open_fixed_deadline_v2` |
| Stop policy | Existing `observation_volatility_uncapped_v1` |
| Declared fitting window | 240 complete sessions, November 6, 2025–October 23, 2026 |
| Actual retained training paths | Latest 4,096 stride-three paths, representing 171 dates |
| First retained decision | February 20, 2026, 11:45 Eastern |
| Last training label | October 23, 2026, 15:45 Eastern |
| Candidate size | 2,057,544 bytes; below the loader's 4,000,000-byte limit |
| Runtime status | `unvalidated`, `routing_allowed=false` |
| Age at local loader check | 5.98 days; not stale at that check |

The [plan](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/paper-v2-validation-20261029/plan.json)
was frozen at 19:07:58 UTC before fitting or evaluating outcomes. Its source
archives and replay arrays were hash-verified. The retained raw source ends
October 23. The current filesystem reader found 1,696 complete sessions, and
85,344 aggregated candles covering the required recent windows were frozen for
the validation harness. No history was imported or restored, and no Supabase
data was written.

One pre-fit correction records the exact existing stop-policy enum spelling in
`plan-policy-correction.json`. It changes no model parameter, stop population,
threshold or risk limit. The original plan and its receipt remain unchanged.

| Binding | SHA-256 |
| --- | --- |
| Candidate canonical identity | `84211f02b2c94ebcee7311276a453d5dd84e1610714eab512ca62dd54d8eddfe` |
| Candidate file bytes | `771c43207b2e0ed8c46f92ce0537fb09fd121c9e66ab5748ace80a72ece56949` |
| Model payload | `554a3df6d3afd3842abfd3716fac0fac81a3c284e0af8a2bade92e3230521a4d` |
| Implementation | `1564298dc68b7be36fdd467c88d6714942ee0e9f50a03adc20a96c48a29e9449` |
| Aggregated source | `97039298a7e9ac9abf85dbf90a7d275b116ab5943f522016888d7962b2c310c4` |

## Chronological results

The frozen validation window contains **60 complete sessions, July 31–October
23, 2026**, grouped into three consecutive 20-session reporting blocks. Each
session uses a new fixed-specification fit on the preceding 240 complete
sessions, retaining the latest 4,096 eligible paths. Every training label ends
at least 15 minutes before its evaluated forecast. No validation outcome is used
to select parameters, thresholds or a mixture.

These are out-of-sample predictions of the fitting procedure. The final candidate
uses the full recent training window, which includes those historical validation
dates. Its own post-training evaluation is reported separately below. The data
was previously inspected; neither panel is certified as an untouched holdout.

| Metric | Bayesian candidate | Same-path empirical pool |
| --- | ---: | ---: |
| Five-minute forecasts | 4,320 | 4,320 |
| Qualifying candidate signals / simulated trades | 0 / 0 | 0 trades |
| Net simulated P&L | $0.00 | $0.00 |
| Nonoverlapping BUY probability scores | 1,440 | 1,440 |
| Brier score, lower is better | 0.249328 | 0.248413 |
| CRPS in USD, lower is better | 30.689418 | 30.712892 |
| Log loss, lower is better | 0.691811 | 0.689966 |

The best candidate entry score was **−$4.807**, below the unchanged **strictly
greater than $1** requirement. Effective training-day support ranged from
144.63 to 159.09, above the unchanged minimum of 20. The absence of entries came
from the edge rule, not insufficient sample support. Zero trades leave expectancy,
trade-tail loss and concentration checks undefined; $0 P&L is not evidence of a
profitable strategy.

Probability scoring uses only BUY outcomes on the predeclared 15-minute lattice.
Uncertainty uses 20,000 non-circular moving day-block draws with lengths 5 and 10,
seed 20260921, and the existing conservative alpha of `0.05 / 6`:

| Candidate minus pool | Mean | Five-day block interval | Ten-day block interval |
| --- | ---: | --- | --- |
| Brier | +0.000915 | [+0.000195, +0.001547] | [+0.000063, +0.001603] |
| CRPS USD | −0.023474 | [−0.223867, +0.148688] | [−0.268469, +0.159260] |

Brier is worse than the pool in this sample under both block calculations. CRPS
has a small favorable point estimate, but neither interval establishes an
improvement. These bounds do not certify an exhaustive historical research
multiplicity count or an untouched experiment.

Only **one** probability bin has at least 30 observations; the required
three-bin calibration check is therefore **undefined and does not pass**. Of
1,440 candidate predictions, 1,434 are in the 0.4–0.5 bin: mean forecast 45.76%,
observed positive outcome rate 46.51%. Six predictions occupy the adjacent bin.
A small pooled calibration error does not demonstrate calibration across a
useful probability range.

The fixed directional comparison accounts produced 918 long trades for
−$5,787.96 and 946 short trades for −$5,138.12 under the same position, cooldown,
entry-count and dollar-loss rules. Flat and empirical-pool accounts made no
trades. Beating losing directional baselines by abstaining does not establish
positive advantage over doing nothing; both paired daily P&L lower bounds are
$0. Spread/slippage stresses and the target trade-through stress also produced
zero candidate trades, so positive-profit stress requirements do not pass.

Realized outcomes were independently priced from absolute observed OHLC and
checked against the runtime paper path engine. Forecast distributions remained
unchanged. The one-bar diagnostic has its own scores in `results.json`; it is
not counted as eligible trading because its necessary five-minute recording
delay violates the existing 120-second freshness gate.

## Exact candidate after its training cutoff

The retained ProjectX archive provides **230 valid later forecasts**, October
26 at 09:35 through October 29 at 10:40 Eastern, using complete same-delivery
five-minute buckets and completed future paths. All **230 HOLD**; there are zero
simulated trades. The best entry score is **−$6.051**.

There are 76 nonoverlapping BUY scores: Brier **0.254898**, CRPS **$37.555189**.
Only three complete sessions plus part of a fourth are represented. Day-block
confidence intervals and broad calibration are unsupported. This is a
retrospective check of the exact frozen candidate on post-training inputs, not
new prospective observation or an untouched holdout.

## Engineering validation and remaining defect

- **534 existing backend safety tests passed** in 20.95 seconds with isolated
  SQLite databases and external network connections denied. No application or
  frontend code changed during this fit/validation task.
- Every fitted path reconstructed exactly from the frozen source into the same
  model payload hash. Scope, integrity, payoff, size and observation loader checks
  passed. An isolated actual-candidate HIDDEN-account readiness check was eligible;
  wrong-owner input and the broker-routing loader were rejected.
- Independent scalar day-cluster/HAC/Student-t calculations matched five
  deterministically selected post-training queries for both directions, with a
  maximum numeric difference of **2.274 × 10⁻¹³**.
- Both directions and offsets 1/2 were checked against raw OHLC and the runtime
  paper engine for 4,320 validation paths, 4,096 retained training paths, and
  230 provider paths. These panels overlap. All normal two-bar comparisons
  passed, and all 920 provider side/offset comparisons passed.

**Open P2: an exact stop touch can be missed by the one-bar normalized pricer.**
One distinct historical case appears in both the training and validation panels:
MNQU6, August 18, 2026, 09:45 Eastern decision, BUY, 33-point stop, entry offset 2.
At the 09:55 bar, adversely filled entry is 29,638 and the stop is 29,605, exactly
equal to the observed low. The paper engine stops for **−$68.22**. Normalization
and rescaling produce a relative low of `-52.24999999999999` versus stop `-52.25`;
the strict comparison misses the stop and returns a time exit for **+$16.78**.
The difference is **$85** from a rounding error of **7.105 × 10⁻¹⁵ points**.

The retained [regression](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/paper-v2-validation-20261029/test_exact_touch.py)
reports **one pass and one failure**: two-bar entry passes, one-bar entry fails.
The existing safety suite therefore must not be described as complete evidence
of payoff parity. The independent absolute-price calculation agrees with the
runtime paper engine. No attempt was made to hide the failure by changing the
candidate, precision policy, thresholds or test expectations.

Current freshness rules prevent that one-bar path from entering runtime paper
trading. No normal two-bar discrepancy was found in the checked data. The
supported diagnostic still violates the stated payoff contract and should be
fixed and regression-tested before this release is approved.

Initial harness attempts are retained separately. They corrected canonical JSON
`0` versus `0.0` representation, scalar multiplication ordering and Windows path
length in an isolated fixture. The completed run records the remaining real
pricer defect as a failed check rather than aborting or changing the model.

## Preservation and next decision

Before/after checks matched all **68 protected files** and **16 source/frozen-data
files**, including existing local artifacts, original audit records, snapshot
databases and the reviewed patch files. No existing artifact was modified or
rehash-labeled. Candidate loading used a short isolated fixture directory; the
active artifact namespace was not populated. The candidate remains unvalidated.

The unchanged contract includes one MNQ, a $250 daily loss-admission budget,
30 daily entries, 300-second cooldown, 120-second market freshness, minimum
300 training paths and 20 effective days, strict $1 net-edge threshold, $1 cost
uncertainty buffer, the original forecast multiplicity deduction, 1.5× target,
the existing uncapped observation stop population and unchanged costs. No
threshold or risk limit was relaxed to obtain a trade.

Before deployment, resolve the numerical exact-touch defect and rerun its
regression plus payoff/compatibility checks. Any source correction that changes
the implementation binding requires a **new separately named fit and validation
receipt**, preserving this candidate; changing its hash in place is not valid.
Reconcile and test the exact deployment package, review observation installation,
and take fresh verified off-device backups before an authorized rollout. Keep
live-routing gates disabled.

This run does not satisfy statistical qualification: there are no candidate
trades, calibration breadth is insufficient, pool-relative scoring does not pass,
and untouched holdout, prospective observation, measured fills and full research
multiplicity remain unverified. Those limitations are separate from technical
readiness for an explicitly unvalidated observation mode. No deployment approval
or trading permission is implied.

Detailed evidence: [results.json](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/paper-v2-validation-20261029/results.json),
[payoff parity](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/paper-v2-validation-20261029/payoff-parity.json),
[preservation receipt](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/paper-v2-validation-20261029/preservation-after.json),
[safety output](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/paper-v2-validation-20261029/safety.log),
and [completed validation output](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/paper-v2-validation-20261029/validation-final.log).
