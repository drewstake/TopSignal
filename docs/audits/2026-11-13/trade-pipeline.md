# Trade pipeline audit — November 13, 2026

Implementation is prepared locally. Production remains on
`20261112-auto-signin-87`; no application restart, migration, policy enrollment,
large transfer, candidate registration or trading change was performed. The owner
explicitly answered **“Keep restriction; prepare everything”** to the maintenance
question. This audit preserves that restriction even though free disk capacity is
now ample; latency and concurrent archive load are separate constraints.

## Issue checklist

| Issue | Verified evidence / cause or uncertainty | Prepared fix and validation | Deployment / historical limits |
|---|---|---|---|
| Browser-dependent history | Existing account/day UI reads trigger refresh; no durable account-wide scheduler. Database had 4,498 scoped legs, stale discovery and only six legs on the current practice account. | Independent persisted owner policy, bounded day windows, rate spacing/backoff, recent reconciliation, durable coverage, correction/void revisions and prior heads. Tests cover idempotency, scope, dense splitting, backoff and policy opt-outs/cap. | Not activated. Provider recovery is read-only into a separate bundle; production heads unchanged. Empty old responses cannot prove no trading or expiry. |
| Distinct fill legs collapse | ORM fallback and deployed PostgreSQL unique index both use owner/account/order/time even when two source leg IDs differ. | Partial fallback index only for null source IDs; exact leg-ID merge first. Regression and PostgreSQL CI migration/concurrency tests. | Migration pending. Lost historical legs require provider recovery; no synthetic repair. |
| Legacy-owner merge | Old script deduplicates account/leg across owners, collapsing 159 rows and retaining some legacy-only data without an ownership migration receipt. | Explicit owner and account scope; no cross-owner or cross-account merge. | Original evidence preserved. Differences from legacy scope are exclusions, not deleted trades. |
| Active account coverage | Only practice 29593203 is enrolled. Fresh provider discovery has 826 accounts but only five visible/tradable supported accounts. | Persisted active-account policy with cap eight, explicit opt-outs, destination guard and newly discovered enrollment. Shared market quotes per owner/contract; position/fill streams stay scoped. | Policy/enrollment deferred. Historical unenrolled paths cannot be created retrospectively. |
| Empty progress fill table | Snapshot: 83 positions, 33,822 samples, zero fills/revisions. Exact-case archive dispatch differs from case-insensitive SignalR dispatch; future provider fill timestamps were discarded. Actual historical payload casing/clock offset is not retained, so causal attribution remains incomplete. | Canonical case-insensitive dispatch; preserve signed clock issue instead of dropping fill evidence. Tests cover casing and future receipt skew. | Not deployed. REST recovery restores legs, not missed live observations. |
| Delayed marks/split paths | Every captured position has unavailable update/exit time flags; most have incomplete identity, stale/missing marks, sampling or exit-coverage gaps. | Receipt-only identities can be reconciled to later provider identity without invented matching; uncertainty still splits. Broker times bound report samples; post-exit raw samples retained. Tests exclude nearest/post-exit joins and verify owner sharing. | Old gaps stay visible. New recording is not proof of retrospective coverage. |
| Legacy excursion zeros | 50/50 current `position_lifecycles` rows have zero MAE/MFE. No `trade-excursions/v1.sqlite3` exists. Tracker initialized extrema at zero even with no market marks. | No-mark lifecycle exports now use null extrema/timestamps; regression confirms. Legacy rows are not rewritten or treated as actual zero movement. | Not deployed. Historical initialized rows cannot establish excursions. |
| Inactive archive account | Scope `mnq-primary`, account 26507139, exact MNQ Z26. Recent candle cursor and successful 20-bar retrieval advanced on November 13 despite inactive trading status. | No speculative account replacement. Distinguish credential market entitlement/instrument collection from position enrollment. | Existing archive active; historical empty user-trade scope does not cover other accounts. |
| Selective exit-study coverage | Original fixed sample 31/41 losers versus 8/36 winners paired-valid. Current copied stores: 98 exploratory results (54 valid/44 excluded), 97 replay jobs (91 completed/six no-progress), 104 enrollments and zero selections. | Silent retention refusal now reports failed unless a real stream holds the saved cutoff. Existing restart/client-factory fix was already present. Report groups inclusion by actual outcome/session/version; offline copied replay enforces exact model and exploratory enrollment. | No model/protocol/paired-rule changes. Large source copies and historical recomputation deferred. Completed jobs with missing feed/tail are not backlog. |
| Worker-input event loss | November 13 live capture status shows 303,771 drops; queue currently near empty. Code rejects snapshots over 2,001 levels; observed raw book has 2,722 bid/2,930 ask levels. Model itself rejects >2,000 levels. Historical status lacks per-cause counts, so not every drop is attributed to this cause. | Preserve rejected large inputs within unchanged 1 MiB record/8 MiB queue/20 GiB storage limits; report fixed loss-reason counters. Test records 3,000 levels while unchanged model still rejects them. | Not deployed. Missing inputs/tails remain unrecoverable from these captures. Model limit deliberately unchanged. |
| Backup date ambiguity | Full cache last verified November 4. November 11 progress snapshot is 69,632 bytes/zero samples. These protect different scopes. Local scheduled off-device task is disabled. November 10 failure was estimate timeout; installed helper hash matches repository's subsequent 900-second timeout/overlap fix. | No invented new backup bug or schedule enablement. New bounded off-device online snapshots and restore check protect actual progress/results/jobs and execution export; all hashes and SQLite integrity pass. | Full-cache catch-up remains blocked by load restriction. Large worker/depth/archive/raw sources after November 4 are not covered by the new focused receipt. |
| Analysis methodology | Old ledger replaces fill-derived bot positions with 164 bot-journal trades, calls timing groups decisions, infers environments from names and fills some missing P&L/costs. | Frozen broker-leg ledger, exact bot order attribution, fee completeness, environment separation, no manual/copy inference, cash flows separate, honest path/study exclusions. Maintained workflow and seven audited artifacts. | Report explicitly materially incomplete where gaps remain; no causal or optimized-stop claims. |

## Current recorder and backup evidence

At 07:09 UTC November 13, independent depth recorder PID 10452 was recording
MNQ Z26 with accepted subscriptions, zero reported connection errors, recent
frames, durable index 2,728,073 and advancing totals (48,706 samples). Its
inventory was about 8.89 GB against the unchanged 20 GiB budget. Sequence and
full-snapshot completeness remain unverified. Its existing inventory scan is
expensive (about 354 seconds); no extra full scan was launched.

The newest referenced worker capture was 157 MB, recording with committed
input progress and 303,771 drops. The preceding 311 MB capture had 11,624 drops.
A stored `writer_alive` value on a historical capture is not proof its process
still exists. Worker inventory was about 7.65 GB/20 GiB, with the existing review
boundary December 3. Depth's 30-day review boundary remains in late November.
No budget, retention age or delete policy was changed.

D: initially had 1,330,723,454,976 free bytes; C: 80,326,541,312. The execution
DB was about 58.5 MB. The new focused snapshot totaled about 23.3 MB. Conservative
new five-account progress growth is about 18 MB/open-position-hour, or 36 GB/year
at eight hours/day and 252 days, before backup copies and other recorders.

The focused restore receipt is at
`backend/storage/databento/trade-review-v1/restore-check-20261113-01/restore-receipt.json`.
It verifies 83 positions/33,822 samples, 98 study results, 104 enrollments,
97 replay jobs/238 attempts, and a scoped execution export. It is **not** a full
PostgreSQL disaster-recovery backup or full cache backup. The November 4 full
receipt protects 20,455 files/5,561,615,793 bytes and has an actual replay restore
check. November 11 protects readiness only. The full-cache schedule remains
intentionally disabled under the current restriction.

Private evidence paths under `backend/storage/databento/trade-review-v1/`:
`evidence-20261113-initial-02`, `evidence-20261113-initial-03`,
`recorder-health-20261113.json`, `provider-probe-20261113`,
`provider-history-20261113`, and the derived `evidence-20261113-recovered`.
Sources are individually timed, not cross-store atomic. Original November 12
report/scripts/evidence remain under `tmp/trade-review-20261112` unchanged.

## Prepared deployment sequence — do not execute under the restriction

1. Finish normal clean-commit release validation/package, review the PR and
   require the six exact-commit CI jobs, including PostgreSQL migration tests.
   Keep source/assets immutable and preserve previous releases.
2. In an owner-permitted window, obtain and verify a current execution database
   backup through the existing system. Verify account exposure/orders and all
   release gates. Use the existing graceful maintenance procedure; no force kill.
3. Apply the one pending schema migration with the existing migration runner,
   check readiness, then stage/activate the reviewed bundle via `npm run release`.
   The routine activation path correctly refuses pending migrations.
4. Verify API/worker/independent recorder health and unchanged trading settings.
   Configure the documented persisted owner policy, after destination/capacity
   verification. Current eligible IDs are 29593203, 29671908, 29671939, 29671977,
   29672024. Preserve explicit opt-outs; do not enroll 826 historical accounts.
5. Reconcile selected historical windows through the migrated store. Verify
   distinct same-order legs, corrected/voided heads, coverage and browser-closed,
   bot-disarmed manual position recording. No orders are needed for passive
   readiness checks; actual fill coverage must await real authorized activity.
6. In the permitted low-load transfer window, run the existing full-cache backup
   with restore check, preserving budgets. Copy only required verified worker
   captures and replay explicit exploratory entries with matching saved model
   code. Append new results/provenance; never consume holdouts or create tails.

## Validation and report receipts

The first full release validation passed 3,461 backend tests (21 skipped),
1,364 frontend tests, lint, deployment regressions, both dependency audits and
production build. Its receipt was correctly invalidated because source changed
while validation ran. Final committed-source validation/package and CI receipts
are required; do not reuse that invalid receipt. Regression additions also cover
active-account opt-outs/cap, scoped assembly, cash-flow periods and real offline
replay with holdout refusal. HTML was rendered and visually checked headlessly.

The finalized recovered report and its reconciliation summary are linked from
[the workflow](../../trade-review-workflow.md). Artifact manifests carry final
source hashes and exact report parameters; current operational restrictions and
unrecoverable gaps remain part of the deliverable.

Provider docs checked November 13:
[trade legs](https://gateway.docs.projectx.com/docs/api-reference/trade/trade-search/),
[realtime](https://gateway.docs.projectx.com/docs/realtime/),
[rate limits](https://gateway.docs.projectx.com/docs/getting-started/rate-limits/).
No undocumented pagination, date limit or retention guarantee is asserted.

## Reconciliation through November 12, 17:00 ET

The collector completed all 826 owned provider requests for January 1, 2025
through November 13, 00:00 UTC. One temporary failure succeeded on the explicit
bounded retry. Ten accounts returned nonempty responses, 816 returned empty.
The earlier failed receipt and original collection remain preserved locally.
Collection recovered **295 additional legs**, including November 11–12 fills;
none changed an existing payload and none were merged into production. One
zero-quantity provider row is retained as source evidence and excluded as a
trade. Nothing before the requested start or beyond provider retention is proven.

The refreshed scoped ledger has **4,792 valid legs and 1,433 closed positions**,
versus the original report's 4,522 cross-owner-deduplicated legs and 1,446 final
positions (including journal substitutions). The unrecovered pre-collection
scoped ledger had 1,302 positions; recovery added 131. All **164 bot positions**
now have broker fill support. Their actual gross is -$774.00, known fees plus
commissions $200.08 and net **-$974.08**, versus -$956.08 in the old journal-based
report. The $18 difference is cost reconciliation, not changed strategy results.
`comparison-20261113.json` binds the original ledger and new position hashes and
lists each changed bot result.

There are **630 positions with incomplete costs**; gross/net headline totals from
the old report cannot be carried forward. A new source-overlap candidate also
requires review: CSV account 8141 and provider account 25869065 share the name
TOPX8141 and four apparent corresponding lifecycles, with different order IDs,
subsecond timestamps and fees. Both scopes remain intact. Combined profit is
withheld; the displayed known-net row sum is explicitly conditional and may
count overlapping sources. Provider-live, broker-simulated and unknown labels
remain separate. No name-based environment inference or silent account merge
was used.

Four unresolved inventory groups and two orphan closing legs remain. Of 1,433
positions, 1,350 are not enrolled in the captured progress store, six lack a
matching recording, 74 have partial paths, and three have ambiguous/split paths.
These are historical coverage states, not claims of current open positions.
The fixed-bracket inclusion disparity remains **31/41 losers (75.6%) versus
8/36 winners (22.2%)**. Trailing inclusion is separately 9/11 losers and 3/7
winners. No missing tails were fabricated and no historical result was replayed
without its required copied inputs. The report is **materially incomplete**;
the requested session is closed by cutoff, not an unfinished-session total.
