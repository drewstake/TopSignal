# TopBot diagnostics, refresh tracking, and controlled model comparison

Completed October 30, 2026. Reporting release `20261030-dashboard-18` is running
on the host PC, `DESKTOP-6EUPDU1`. TopBot configuration 32 and run 9 remain in
paper mode. The installed model, trading implementation, entry threshold, and
risk settings were preserved. No model was promoted or replaced.

## Dashboard changes

The existing Bot forward-results panel now includes current-model diagnostics:

- Installed artifact identity and integrity, training cutoff, training age,
  seven-day refresh deadline, and fresh/due-soon/overdue status.
- Current-model forecast and signal counts, requiring an exact match of
  artifact, payoff semantics, and data stream. Historical model totals and
  records without model identity remain separate.
- Permitted entry hours, the next eligible candle close, and an explanation
  that outside-hours evaluations normally have no numerical forecast.
- Expected net profit, uncertainty deduction, final entry score, and required
  threshold for the latest current-model forecast, once one exists.
- Separate paper eligibility and broker readiness, plus training-cache coverage.

Diagnostics use the existing owned, authenticated forward-results endpoint with
`Cache-Control: no-store`. Ledger access is read-only and capped at 9,000 records
over at most 30 days; partial coverage is labeled. Reading the dashboard never
fits, installs, or routes a model and never creates a missing replay cache.

Refresh tracking is observational. The installed model was trained through
October 23 at 3:45 p.m. Eastern; its seven-day boundary is October 30 at 3:45 p.m.
Eastern. Observation continues with a stale-training warning after that point,
consistent with the existing observation policy. Refitting and installation are
not automatic. The host currently has no verifiable Databento replay metadata at
the configured cache root, so the dashboard reports training-cache coverage as
unavailable. Provider observations are not treated as verified training data.

At the premarket deployment check, the installed model had no attributed
numerical forecasts yet. The 137 historical numerical evaluations belonged to
older model bindings. Outside-hours evaluations without model identity are not
retroactively attributed to the current model.

## Frozen comparison

The comparison plan and input hash were frozen at 11:17:29 UTC before the run.
It covered the same 60 sessions, July 31 through October 23, using previously
inspected development data. These results are not an untouched holdout.

All three existing model families used the same daily preceding-240-session
training policy, last 4,096 stride-three paths, 15-minute label embargo, fixed
mixture of zero, paper-v2 payoff timing, cost assumptions, and simulation rules.
Round-trip costs remained $3.22 per contract. Entry required expected net profit
minus uncertainty to strictly exceed $1. External networking and production
writes were disabled in the comparison process.

| Model | Decisions | Qualifying signals | Simulated trades | Best final entry score |
| --- | ---: | ---: | ---: | ---: |
| Bayesian cells | 4,320 | 0 | 0 | -$4.807 |
| Kernel paths | 4,320 | 0 | 0 | -$3.967 |
| Empirical pool | 4,320 | 0 | 0 | -$4.698 |

All three produced $0 simulated P&L through abstention. This provides no evidence
of positive trading expectancy. The Bayesian control exactly reproduced all
4,320 forecasts from the prior fixed-control validation, with maximum numeric
difference zero.

BUY and SELL forecasts were scored separately on the 15-minute lattice, with
1,440 predictions per side per model. Lower scores are better:

| Model | BUY Brier | SELL Brier | BUY CRPS ($) | SELL CRPS ($) |
| --- | ---: | ---: | ---: | ---: |
| Bayesian cells | 0.249328 | 0.248831 | 30.6894 | 31.1794 |
| Kernel paths | 0.248950 | 0.248201 | 30.4072 | 30.9286 |
| Empirical pool | 0.248413 | 0.248171 | 30.7129 | 31.3039 |

Kernel improved dollar-distribution accuracy against the empirical pool: paired
CRPS differences were -$0.3057 for BUY and -$0.3753 for SELL, with both five-day
and ten-day block intervals below zero. Its profit-probability Brier scores did
not improve consistently against the pool. These development-data intervals
use the frozen procedure; they do not establish confirmatory significance across
all historical research or demonstrate a tradable edge.

A descriptive decomposition of the saved forecasts found 189 Bayesian and 312
kernel decisions where at least one side's expected net profit exceeded $1
before uncertainty. None exceeded $1 afterward. The pool had zero such decisions
even before uncertainty. This identifies the uncertainty deduction as binding
for some conditional forecasts; it does not show that removing it would produce
profitable trades. No deduction, cost, threshold, or risk rule was relaxed, and
no threshold sweep or additional strategy run was performed.

The first invocation stopped on a plan-parser error before any forecasts were
generated. The parser was corrected to accept the frozen single-date fold format;
the unchanged plan and input were rerun into a new directory. The failed output
and logs were retained.

## Deployment and verification

Release 18 was assembled from verified production release 17, with only this
reporting feature and comparison tooling overlaid. Unrelated existing workspace
changes were excluded. The scheduled task still runs as the limited
`TopSignalSvc` account. A filesystem backup of 33 files / 54,363,193 bytes and an
independent restore check completed before activation. Release 17 and a rollback
script remain available; rollback was not needed.

- 114 backend tests passed, including diagnostics, comparison isolation,
  forward-results ownership, session rules, and paper observation behavior.
- 21 frontend tests passed across diagnostics and related results panels.
- The staged production frontend built successfully. Its served index matched
  the staged build exactly; the scoped route returned verified diagnostics.
- Model/configuration hashes were unchanged; existing ledger counts were
  preserved. Both live-routing flags remain false and the active run is dry-run.
- Read-only broker checks found zero orders, open orders, positions, or fills
  from the bot observation start through 7:35 a.m. Eastern. There were zero
  broker mutation calls in verification. The paper trading ledger had zero
  trades; its 76 separate comparison trades are not TopBot paper executions.
- At 7:36 a.m. Eastern, the worker and readiness endpoints were healthy, the
  provider status was `ok`, the run had no error, and its 7:35 evaluation had
  committed normally with `outside_research_session`.

A brief provider error at 7:34 recovered before the final health check. Logs also
contained retryable SQLite contention in the existing market archive collector,
including optional quote writes. This report does not certify that separate
archive contention issue as fixed. No historical database import, Supabase
storage change, billing change, or data cleanup was performed.

## Reproduction and evidence

The runner is `backend/tools/compare_paper_models.py`. Inputs, frozen plan,
receipt, results, forecast rows, scoring details, and deployment evidence stay
under the configured local filesystem cache, not in cloud result JSON:

`backend/storage/databento/audits/dashboard-comparison-20261030/`

Important files are `comparison-plan.json`, `comparison-plan-receipt.json`,
`comparison-results-r2/results.json`, `comparison-results-r2/forecasts.jsonl.gz`,
`comparison-control-parity.json`, `entry-score-decomposition.json`,
`staged-backend.xml`, `deployment-verification.json`, and `worker-followup.json`.
These cache artifacts are ignored by Git; this report is the repository summary.

| Binding | SHA-256 |
| --- | --- |
| Frozen plan | `467705d3d5695debab5b7dfeb80d6b0075d5eae9914be94866430f041b2496b6` |
| Frozen input | `58cddbda224de4ad1094405ae18eda82ca6a09281f63818f4fbe4f012b507d8e` |
| Trading implementation, unchanged | `856cee44fec03ded157e8d64423a19573027417f13a4dcb07d541f2b091a6da5` |
| Installed artifact, unchanged | `7e4e044e74c255603b19f31fb966f336d786bdfccfb97231e2ceb60d9eafbf94` |
| Release ZIP | `62939dad87e70e6773db61385a54e7d78f7a07c668d3e5a76b49ec9e31aa4f82` |
