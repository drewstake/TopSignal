# TopBot paper timing and eligibility v2

Local implementation, October 29, 2026. **Not deployed or activated.** This is a
new unvalidated observation contract. The registered v3 research protocol and
broker routing authorization are not expanded by this change.

## Timing and artifact compatibility

`post_record_open_fixed_deadline_v2` prices entry at the first five-minute open
strictly after recording, with the exit deadline fixed at decision close +15m.
A 10:00 close recorded at 10:00:20 therefore prices 10:05–10:15 exposure. A record
exactly at 10:05 would require 10:10–10:15 exposure; at 10:10 no exposure remains.
Neither delay extends the deadline. The independent 120-second freshness gate
normally excludes these later cases. A forecast is frozen at evaluation and
checked again after the application commit: if recording changes its entry
opportunity, admission is blocked instead of applying the old score to a new path.

The runtime also rechecks after the local SQLite write lock and pending-outcome
work. Its first transaction stores conditional reservations with a pending
recording status. After that commit returns, a completion receipt records the
observed commit-completion time and checks the frozen opportunity again. A late
commit cancels only those unfilled reservations and their reserved entry counts;
it does not book P&L or change the forecast. Main and comparison accounts follow
the same rule. The receipt may be persisted later, but cannot invent an earlier
completion time. A crash or write failure before that receipt leaves unknown
recording evidence: those reservations cannot replay into historical fills and
block further exposure pending review. The UI labels the timestamp provisional.
Explicit offline `ingest(now=...)` calls retain a fixed replay clock; the worker's
`record_committed` always supplies the wall clock and completion checks.

New samples store a three-bar scenario envelope ending at the original deadline,
normalized to the second bar's open. The first bar is context, never exposure.
Normal training labels, forecast pricing, offline probability scoring and
simulation price only the remaining two bars. The pure forecaster also supports
one remaining bar for explicit timing diagnostics; runtime freshness still applies.
The scale and stop sizing remain `sigma * sqrt(3)`, with the existing minimum,
rounding and configured capped/uncapped policy. Target, quantity, fees, adverse
costs, opening-gap ordering, stop-first ambiguous bars and daily risk limits are
unchanged. Missing prices or delivery/subscription changes produce unknown
exposure, never a zero return.

A delivery roll detected by the service carries scoped discontinuity metadata
through the committed-record adapter. The local ledger records that event and
marks old pending forecasts and affected main/comparison portfolios unknown even
when the configuration still names the old delivery and the run has stopped.
The new delivery's prices are not used to settle the old position. A reason string
alone does not authorize this path; owner, configured/observed contracts, observed
close and HOLD disposition must match. Prior balances and closed trades remain.

`delay_bars=1` in the original research stress shifts a full three-bar path and
its end; that is not this contract. The new sample builder rejects that combination.
New models and labels carry explicit payoff semantics. Legacy models default to
`decision_open_three_bars_v1`, retain their original numerical behavior, and cannot
be installed on the new observation route. Do not edit metadata or hashes to
declare an old artifact compatible.

The observation loader uses a new filesystem namespace:
`probabilistic-v3/unvalidated-v2.1/<owner-hash>/<stream-hash>.json` under
`TOPSIGNAL_DATABENTO_CACHE_DIR`. The fitter creates a new file exclusively and
builds new labels. The October 29 recording-path repair uses this separate
namespace to preserve the original `unvalidated-paper-v2` artifact. Its fresh
fit and validation retain the same frozen inputs, model payload, thresholds
and risk limits; the deployment report records their results. Old artifacts
remain under their existing paths. The implementation binding includes payoff,
eligibility, adapter, ledger and evaluation code. Existing validated artifacts
also fail a changed implementation hash; paper v2 is explicitly rejected by the
validated broker-routing loader regardless of claimed validation status.

Existing forecast records without v2 metadata continue to resolve on their
original three bars. New forecast outcomes resolve the frozen v2 entry/exposure.
The retained audit's 54 original-input forecasts were replayed through the legacy
pricing path after this change; maximum numeric difference was 2.94e-14. That is
reproducibility evidence, not validation of the new contract.

## Independent paper readiness

`simulation-eligibility-v2` is a read-only assessment computed for every selected
TopBot evaluation, including HOLD. It reports `eligible`, `blocked` or `unknown`.
It requires an owned ProjectX account, the configured explicit MNQ delivery and
five-minute data scope, an enabled dry-run configuration, an owned running
continuous dry-run with explicit false live-routing confirmation, observation
mode, and both live environment gates explicitly false. Ambiguous authorization
or missing evidence fails closed. Broker visibility and tradability are not paper
requirements; no account state is rewritten. Scope/ownership changes during a
fetch still abort evaluation; a broker visibility change alone does not.

The shared assessment checks closed, valid, contiguous candles, model integrity,
scope, payoff compatibility, the bracket/cost/quantity contract, freshness, and
session boundaries. Research-window eligibility uses the completed candle close,
including 15:30. Wall-clock freshness and exchange/flat deadlines are independent.
The same boundary helper governs strategy, execution-risk admission and coverage;
DST and early-close dates use the existing exchange calendar.

Paper account readiness additionally checks that account's own position, cooldown,
entry count and remaining dollar loss budget, including proposed stop risk and
costs. Configured limits can be stricter, never looser than one MNQ, 300 seconds,
30 daily entries and $250 daily loss admission. Pending orders reserve a position
and an entry. TopBot's edge and signal-support rules remain separate and unchanged.

Broker readiness lists known static blockers even during HOLDs. An absence of
static blockers is `unknown`, not routing clearance: the existing authorization,
environment gates, account classification, provider preflight, protection and
mutation fences still govern actual routing. No broker mutation is part of paper
eligibility or simulation.

## Comparisons and historical evidence

`forward-comparison-v2` requires the same explicit shared simulation assessment
for TopBot, flat, always-long, always-short and empirical-pool accounts. It never
infers approval from an empty risk-code list or fabricates an approved execution
disposition. Each account keeps independent exposure and daily state. Directional
baselines intentionally omit edge and sample-support gates; the historical pool
retains them on its own forecast. This distinction is visible in cohort metadata
and the UI. Unknown eligibility excludes a session from uncertainty estimates.

Payoff, comparison policy, artifact and configuration identities bind each new
cohort. Old cohorts/results are retained and labeled with their original policy;
old open positions still mature, and no older opportunities are backfilled.
Existing main-account balances, trades and limits continue without resetting.
Reliability bins select only the latest recorded artifact/payoff combination in
the requested window. They do not pool legacy and new payoff definitions.

All input histories, ledgers, quote spools and diagnostic datasets stay on the
filesystem. This patch adds no Supabase schema or historical-data writes.
The local SQLite ledger adds a recording-status index and a discontinuity table;
older records without recording metadata retain their historical behavior.

## Separate authorization before activation

1. Review this patch against HEAD `6c8d778` plus the preserved workspace archive
   additions. Reconcile the separate `20261028-archive-15` deployed release,
   especially its different `bot_service.py`, in a new isolated release tree.
2. Separately authorize fitting a new observation artifact for v2. Freeze the
   dataset provenance, training cutoff, label policy and chronological validation
   plan first. Fit to a **new** output file; do not modify, relabel or replace an
   existing artifact. Check label/path parity, calibration, dependence-aware
   uncertainty, fixed thresholds and risk limits on the newly specified exposure.
   The existing registered three-bar development report cannot validate v2.
3. Review those results and separately approve installing the new scoped artifact
   for unvalidated observation. Preserve the old artifact/code pair for replay.
   No existing model was retrained, replaced, promoted or modified here.
4. Separately approve deployment and service restart. Before either, inspect
   storage usage/capacity and take verified off-device backups of current ledgers,
   both event spools, archive, artifacts and source ZIPs. Retain current data for
   rollback. Use the existing single worker; retain disabled live-routing gates.
5. After an authorized rollout, verify HOLD readiness, fresh evidence collection,
   new comparison identity, original-deadline timing and zero broker mutations.
   Observe quote counters and SQLite error codes; do not assume lossless capture.

Real routing would require a separately registered and reviewed execution/payoff
protocol, validated artifact and explicit broker authorization. This patch does
not provide or request that activation.
