# TopBot inactivity fixes — local implementation report

October 29, 2026. **Reviewable local patch; no deployment or activation.** No
production writes, service restarts, account changes, execution enablement, test
orders, model fitting, artifact replacement or promotion were performed.

## Implementation base and evidence

The base is repository HEAD
`6c8d77874377ca5fd24d1b0a28d470dade9bd38e` plus the workspace's pre-existing archive,
backup and UI work. A before-edit file manifest, source snapshot and original Git
diff were retained under
`backend/storage/databento/audits/implementation-20261029T172942Z/` (ignored local
evidence). The accompanying `topbot-inactivity-fixes.patch` is incremental against
that snapshot, **not a deployment patch against production or clean HEAD**.
The patch now includes the four subsequent P2 review fixes. The reviewed earlier
patch, receipt and report are preserved under
`backend/storage/databento/audits/p2-fixes-20261029/before-p2*`. The original review
and its failing probes remain unchanged under `audits/review-20261029/`.

The running-release evidence identifies `20261028-archive-15`. Eight relevant
baseline modules (strategy, session, observation loader, forward ledger/comparison
and quote/archive code) matched the retained deployed copy after newline
normalization. `bot_service.py` differs, as the audit reported. That deployed
release must be reconciled separately before any authorized rollout. The existing
audit report and retained production artifact/snapshot were not rewritten.

Before editing, all 66 retained deployed-code audit fixtures passed, reproducing
the HIDDEN-account block/disarm, ACTIVE-account durable paper admission and
15:30+1 microsecond rejection. An additional isolated reproduction confirmed that
a HOLD's empty risk codes admitted a directional baseline, and that a real SQLite
writer lock caused the old quote queue to discard its batch without persistence.
Historical logs do not identify the causes of all 131 OperationalErrors.

## Changes

| Issue | Implemented behavior |
| --- | --- |
| Paper versus broker eligibility | Explicit owned, authorized observation-only assessment; broker visibility/tradability no longer blocks paper. Separate shared, paper-account and broker readiness appears on HOLDs. Real routing retains its existing authorization, account, preflight and mutation gates. |
| Forecast/execution timing | Versioned v2 labels and payoff pricing use the first post-record open and original close+15m deadline. Normally two bars remain. Commit delays that change the entry or violate freshness block admission. Costs, stops, targets, size and risk limits stay fixed. |
| Inclusive 15:30 boundary | Strategy, risk adapter and coverage use completed-candle decision close for research-window eligibility, with independent wall-clock freshness and exchange/flat protections. |
| Comparison evidence | All accounts require explicit eligible evidence. Empty risk lists convey no eligibility; baselines no longer synthesize an approved execution status. Positions, cooldowns, counts and budgets remain independent. Directional support/edge differences are labeled. |
| Optional quote persistence | Separate bounded memory queue, background consumer and durable quote spool; classified bounded retries, stable receipt identities, restart deduplication, terminal-loss counters, physical/disk/shutdown limits and safe error diagnostics. Critical-event final-drain behavior is retained in its own worker. |

There are no application-database migrations or historical Supabase writers. New evidence and
spools remain under `TOPSIGNAL_DATABENTO_CACHE_DIR`. Online backups copy both
transport spools before the archive to preserve records during handoff.
The local paper SQLite ledger adds a recording-status index and a discontinuity
table; existing records and balances are retained.

## P2 review corrections

| Finding | Correction and regression evidence |
| --- | --- |
| Freshness checked before ledger waits | Runtime checks after acquiring the SQLite lock, after maturity work and after the initial durable commit. Conditional reservations cannot fill without a completion receipt. A real 2.2-second writer wait starting at age 119 seconds now blocks admission. Tests also cross the entry open/deadline, reject late commits without booking P&L, and preserve unknown exposure after a crash before confirmation. |
| Roll handler bypassed by ingestion's scope rejection | The service sends explicit roll evidence through `prepare_committed_record`. The ledger records the discontinuity and marks old pending forecasts/main/comparison exposure unknown without using replacement prices. End-to-end tests cover ordinary and trailing-partial feeds, repeated delivery, zero broker calls and rejection of foreign or inconsistent evidence. |
| Quote physical limit prevented cleanup | New growth and maintenance use separate thresholds with reserved headroom, smaller bounded batches and checkpoint recovery before refusing writes. Tests retain a reader connection after releasing its snapshot, recover a legacy full WAL without manual checkpointing, and verify cleanup can use reserved space while new growth is stopped. |
| Archived quotes falsely counted as lost | Terminal disposal first reconciles stable archive IDs. Tests recover both an expired acknowledgment and a crash after the last allowed successful attempt. A failed archive read records terminal uncertainty separately; the UI does not label it persistence or confirmed loss. |

These changes do not lower signal thresholds, uncertainty deductions or account
risk limits. They change the implementation binding; no prior artifact was
rehashed or relabeled to match it.

## Validation

- **534 backend tests passed** in the consolidated safety suite (27.41 seconds):
  the previous 514 plus 20 regression cases for the review findings,
  existing broker execution/risk tests, worker/hub isolation, mathematical strategy,
  forward APIs/coverage, archive durability and backup checks. All application
  databases and ledgers were isolated. External socket connections were denied;
  loopback remained available for Windows asyncio's internal socketpair.
- **385 bot frontend tests passed**; the frontend TypeScript/Vite production build
  and ESLint on changed UI files passed.
- Synthetic BUYs and HOLDs exercised HIDDEN, MISSING, LOCKED_OUT and ACTIVE
  accounts. Eligible BUYs produced one durable simulated trade under repeated
  recording/restart reads, while provider stubs recorded **zero place, cancel or
  close calls**. Broker visibility changes during fetch remained safe; ownership
  changes and ambiguous mode/authorization failed closed.
- Timing parity covered both directions, registered costs, opening gaps,
  ambiguous stop/target bars, two- and one-bar exposure, deadline exits, missing
  paths and delivery changes. Tests covered 15:30 exactly, +1 microsecond,
  +10/+119/+120 seconds, stale +121 seconds, 15:35, DST and early-close sessions.
- Quote tests covered real SQLite contention and read-only errors, unclassified
  failures, capacity, retry/age exhaustion, saturation, recovery, shutdown limits,
  critical-consumer isolation and process exit after archive commit before ack.
  A separate backup handoff test recovered pending quotes without duplication.
- All **54 original-input current-artifact forecasts** in the retained audit
  reproduced through the unchanged legacy payoff branch, with maximum numeric
  difference **2.94 × 10⁻¹⁴**. Source artifact and snapshot checksums were unchanged.
  This is historical reproducibility, not evidence of profitability.

The initial local evidence directory contains reproduction summaries, base/deployed
comparisons, JUnit results, exact backend invocation and patch checks. An initial
combined harness run blocked Windows asyncio's own loopback socketpair and
overrode a test's observation-log location; those harness errors were corrected
before the passing consolidated run. That initial output is retained separately.
The P2 evidence directory contains the new `run_validation.py`, exact invocations,
JUnit results, frontend logs and regenerated patch checks. Its focused run passed
128 tests before the additional trailing-partial case; the complete 534-test run
includes that case. Review findings are now regression tests in
`test_forward_recording.py` and `test_optional_quotes.py`.

## Compatibility, gaps and activation

The new identifiers are `post_record_open_fixed_deadline_v2`,
`simulation-eligibility-v2`, `forward-paper-v2` and `forward-comparison-v2`.
Old artifacts, forecast fields, trades, balances and cohort policies are preserved;
new results are not backfilled. Legacy pending forecasts retain three-bar pricing.
Reliability bins no longer pool different artifact/payoff definitions.

**No v2 observation artifact was fitted or installed.** Activation remains pending.
The loader uses the new `probabilistic-v3/unvalidated-paper-v2` namespace and
requires a matching artifact and implementation binding. Relabeling or rehashing
an old artifact is not a migration. The validated broker path explicitly rejects
paper v2; old validated artifacts also cannot bypass implementation-hash checks.

The exact separately authorized steps are in
[the paper-v2 contract](../../topbot-paper-v2.md#separate-authorization-before-activation):
review/reconcile the deployed base; authorize a new artifact fit and frozen
chronological validation; review and approve installing that observation artifact;
then separately approve deployment/restart after capacity checks and verified
off-device backups. Retain disabled routing gates and existing ledgers. Real
routing would require its own registered validation and explicit authorization.

The historical quote losses remain unrecoverable and their causes unknown.
Sampling, network gaps, memory-only crash windows, terminal drops and terminal
uncertainty remain. An unavailable archive at terminal reconciliation cannot be
called a confirmed loss. A paper commit without its completion receipt remains
unknown and blocks replay; it requires review instead of automatic backfilling.
As before,
durable counters cannot commit while their own storage is unavailable. No lossless
recording or production behavior is claimed. Signal thresholds and uncertainty
deductions were not lowered, and no profitability claim follows from these fixes.
