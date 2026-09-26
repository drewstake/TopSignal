# MNQ probabilistic research — protocol v3

Registered September 26, 2026, before a v3 market-data development run. The
[specification](topbot-probabilistic-protocol-v3.json) is SHA-pinned and read by
research and runtime. Changes require a new registration and implementation hash.
The v1/v2 result files are historical diagnostics, not current acceptance evidence.

## Data and population

Use the existing local `DatabentoReplayStore` one-minute causal volume-roll
stream. Aggregate observed minutes by delivery into five-minute OHLCV, with
missing-minute quality counts. Do not interpolate quiet minutes. Drop mixed
contract buckets and known truncated boundaries. Contiguity and delivery identity
prevent features or labels from crossing rolls. Start research in 2020.

The label population is the regular-session rule in the
[current strategy contract](topbot-mathematical-strategy.md), conditional on
sigma*sqrt(3) <= 25 points (sigma approximately <= 14.434 points per five minutes).
Reports tabulate accepted windows and cap/other rejections by session and fold.

ProjectX is used only for a read-only overlap parity report. Compare identical
UTC bar opens and delivery years; report overlap count, OHLCV discrepancies and
exact match rate. No overlap never counts as passed parity. Per-candle first
receipt time and revision hashes identify newly observed/revised inputs; legacy
first receipt times remain unknown.

All archives, replay arrays, ledgers and models remain under
`TOPSIGNAL_DATABENTO_CACHE_DIR`. Keep source archives and an off-device backup.
The [storage policy](supabase-storage-policy.md), SQLite-only relational import
guard and local replay boundary remain in force. Retired history is not restored
automatically, and the tool never downloads or buys history.

## Registered methods

- Training paths do not overlap (stride three); features use only closed bars.
  Paths normalize by unrounded sigma*sqrt(3). Forecast scale is independent of
  the rounded bracket, including the 0.75/1.25 stop stresses. Report net dollars
  and net R.
- Select the mixture using calibration-period net P&L of the complete rule.
  Simulate every five-minute decision with the live cooldown, one-position rule,
  30-entry limit and stop-risk budget. Compare flat, always-long and always-short
  ledgers with identical brackets and costs.
- Probability calibration scores only BUY on the non-overlapping fifteen-minute
  lattice. Complementary sides are not counted as independent observations.
- Forecast uncertainty uses the larger day-cluster/HAC variance, Kish effective
  days and a small-cluster correction. The one-sided Student t critical value
  uses alpha=.01/8 and effective-days minus one degrees of freedom. Require 300
  training paths, 20 effective days and lower utility above $1 after a $1 buffer.
- Pool disjoint out-of-sample daily ledgers across at least three folds. No fold
  may be negative. Use 20,000 non-circular moving-block replicates at block
  lengths five and ten, paired daily Brier/CRPS bounds, and seed sensitivity
  for seeds 1–20. Drawdown from trade closes does not establish intratrade risk.
- Test spread, slippage, entry delay, independent bracket width, missing data,
  unknown volume and a one-tick target trade-through requirement.
- The experiment ledger counts overlapping configurations, including reviewed
  pre-v3 history. Family bounds use a conservative Bonferroni correction by that
  count. An unknown historical count remains a live-promotion blocker. A later
  verified larger count requires recomputation; it cannot bless an older bound.
- Depth remains research-only. Its v2 model estimates conditional net payoff
  directly with day-cluster covariance and effective-day support.

## Commands and immutable artifacts

From the repository root, with the bundled backend environment:

```powershell
backend/.venv/Scripts/python.exe backend/tools/research_probabilistic_v3.py --mode audit
backend/.venv/Scripts/python.exe backend/tools/research_probabilistic_v3.py --mode development --summary docs/local-development-result.json
```

`--cache-root PATH` selects an existing active cache. The tool disables provider
credentials and external network connections before importing the application.
A registration is written exclusively before fitting or scoring. Repeating the
same provenance reads its existing report. Ledgers are hashed local companion
files so reports/model metadata do not embed unbounded replay arrays.

For optional ProjectX parity, add `--sqlite PATH --contract CON.F.US.MNQ.Z26`
and `--owner-hash SHA256` when needed to select exactly one owner. Never use
ProjectX rows as the training source. For historical multiplicity, add
`--historical-ledger PATH`: a reviewed JSON object with `reviewed_by`,
`historical_count_verified` and `configurations`, a list of unique
`id` objects for pre-v3 configurations. Its digest is part of the experiment ID.
An incomplete inventory must explicitly set `historical_count_verified: false`;
its known runs still increase the correction, while completeness remains a
promotion blocker. Reruns may be counted conservatively instead of deduplicated.
Do not count synthetic fixtures as market-data evidence.

The initial September 26 attempt reported insufficient data. Following explicit
authorization, the original source archives were restored and the local cache
rebuilt. The 74-fold run on 1,622 complete sessions finished `offline_failed`:
neither candidate produced an admissible trade. Per-trade expectancy and bounds
are undefined. The [September 26 result](topbot-probabilistic-v3-result-2026-09-26.json)
records the failed gates and baseline outcomes. The final run uses the corrected
baseline-ledger writer and produces a native report within the loader's size
limit. Its registration, current implementation hash and all 3,111 ledger
references verified; the loader rejects the failed candidates at the acceptance
gate. Earlier reports remain preserved, and registered parameters were unchanged.
The correction counts 292 known/registered configurations, including conservative
rerun counts. The local SQLite
has only short U26/Z26 fragments after Databento coverage ends. A read-only July
ProjectX request returned no bars, so parity remains unavailable. No model was installed.
The [zero-drift diagnostic](topbot-zero-drift-diagnostic-2026-09-26.json) observed
zero proposals across 100 synthetic refits and eight tests per refit. That small,
simplified simulation is a numerical check, not evidence of market calibration.

## Reviewed refit and promotion

After offline acceptance, supply an explicit review JSON containing
`model_version`, `reviewed_by` and `validation_status: "offline_passed"`:

```powershell
backend/.venv/Scripts/python.exe backend/tools/research_probabilistic_v3.py --refit-experiment EXPERIMENT_SHA --review REVIEW_JSON --owner USER_ID
```

This writes the scoped artifact atomically after all binding/freshness checks.
It refits the frozen mixture on the latest 60 complete sessions, with a
15-minute label embargo. Offline status authorizes forward Dry Run only.

Full status `passed` needs five evidence records: `untouched_holdout`,
`forward_dry_run`, `fill_exit_parity`, `intratrade_drawdown`, `experiment_ledger`.
Each record contains `kind`, `experiment_id`, `model_version`, `protocol_sha256`,
`reviewed_by`, `observed_through`, explicit named boolean `checks`, and the actual
measurements/source provenance that the reviewer used. Archive an observed record
without changing routing with:

```powershell
backend/.venv/Scripts/python.exe backend/tools/research_probabilistic_v3.py --record-evidence EVIDENCE_JSON
```

The returned SHA identifies `probabilistic-v3/evidence/SHA.json`. The review's
`evidence` maps each kind to `{ "passed": true, "sha256": "..." }`. The loader
resolves the files and checks their hashes, experiment/model/specification,
observation cutoff and every check. It does not trust summary facts in the review.

Holdout records need at least 60 sessions, 200 trades, `first_session` strictly
after 2026-09-26, and one predeclared evaluation of unseen outcomes. Forward
records need at least 20 sessions and comparison with the research confidence
band. Parity review must cover broker fills, slippage, fee accounting, target
non-fills, bracket mode and exit drift. Intratrade evidence must measure the
registered $1,000 drawdown limit. Ledger records need `configuration_count` and
`historical_count_verified: true`; the report must use at least that count.
Source measurements must be retained locally so review is reproducible.

A review is an operator attestation backed by these files, not a cryptographic
third-party audit. Editing local measurements is not validation. No tool
self-certifies unavailable evidence or automatically promotes a model. The
implementation hash also binds the execution/risk code; changing it requires
re-evaluation rather than silently loading a stale model.

Required named review checks (all must pass; names are not interchangeable):

| Evidence kind | Check keys |
| --- | --- |
| untouched_holdout | positive_expectancy_bounds, positive_paired_advantage, calibration, risk_limits, cost_stresses, unseen_provenance |
| forward_dry_run | observed_sessions, inside_research_confidence_band, session_population, calibration, routing_disposition |
| fill_exit_parity | bar_alignment, fees_reconciled, bracket_mode, slippage, target_nonfills, exit_drift, halt_behavior, retrieve_bars_limit |
| intratrade_drawdown | complete_marks, within_registered_limit |
| experiment_ledger | complete_research_history, multiplicity_recomputed |

These reviews require actual measurements. In particular, broker parity can be
established from separately authorized manual tests and broker records; an
`offline_passed` strategy cannot route Practice orders to waive its own gate.
