# TopSignal implementation audit

Date: 2026-10-26. Repository: `C:\Users\drews\Development\TopSignal`. Base commit: `257e1b6c06c3dc4b4bdbab9731450b8724f3594f`, plus the existing uncommitted working tree. Investigation only; no application changes were made.

The most valuable fixes are to bind expense reconciliation to the identity that started it, make timed exits handle an identified but still-working broker order, and protect anonymous local API mutations from foreign origins. Each has a focused reproduction against current implementation. Two smaller confirmed issues affect Windows test execution and the retained journal API's fee calculation.

Starting sources included AGENTS.md, the Supabase storage policy, README, the complete CI configuration, the September 22 audit/report/coverage/remediation records, the September 4 unattended-readiness report, the ProjectX integration audit and the trading-bot remediation report. Older findings were treated as leads and checked against current routing, services and tests.

Passing tests are substantial evidence of the behaviors they exercise, but do not establish unattended trading readiness. PostgreSQL locking and migrations, actual broker responses, independent alert delivery, and off-device recovery remain acceptance gaps.

## Recommended first five fixes

| Order | Finding | Severity | Confidence | Estimated effort |
|---|---|---|---|---|
| 1 | A1: Expense reconciliation continues under a replacement identity | P1 high | High: real frontend functions with delayed reads and fake transport | 1–2 engineering days |
| 2 | A2: Identified working orders bypass their timed-exit deadline | P1 high for execution reliability | High: real reconciler and timed-exit processor with a fake broker | 2–3 days |
| 3 | A3: Anonymous local API accepts foreign-origin simple POST mutations | P2 medium, security | High for server behavior; browser delivery not reproduced | 1–2 days |
| 4 | A4: Default Windows test paths break 14 observation tests | P2 medium, development reliability | High: fails normally, passes with a shorter disposable directory | 0.5–1 day for runner/fixture portability |
| 5 | A5: Journal snapshots undercount per-side fees | P2 medium, financial correctness; lower product reach | High: identical event produces different net P&L | 0.5–1 day |

P1 means address before depending on the affected workflow. P2 means schedule promptly within the affected deployment or feature. Effort includes focused regression tests, but excludes broker acceptance, a deployment rollout, and historical-data remediation. A1 should establish a reusable identity guard before auditing other multi-request mutations. A2 must preserve the existing duplicate-order latch and fencing. The remaining fixes can proceed independently. PostgreSQL and operational acceptance should run alongside this work, rather than wait for the lower-reach journal correction.

## Confirmed findings

### A1 — Expense reconciliation can cross an authentication boundary

**Trigger:** User A starts Reconcile Combine Expenses. An account/expense read is still pending when the Supabase session changes to B, including through another tab. The outstanding JavaScript operation resumes after the UI remounts.

**Implementation:** [ExpensesPage.tsx:513](C:/Users/drews/Development/TopSignal/frontend/src/pages/expenses/ExpensesPage.tsx:513) supplies live API and storage functions to [expenseReconciliation.ts:363](C:/Users/drews/Development/TopSignal/frontend/src/pages/expenses/expenseReconciliation.ts:363). After awaiting the reads, the reconciler updates browser storage and begins delete/create loops without checking the initiating identity. [combineTracker.ts:647](C:/Users/drews/Development/TopSignal/frontend/src/lib/combineTracker.ts:647) and [combineTracker.ts:664](C:/Users/drews/Development/TopSignal/frontend/src/lib/combineTracker.ts:664) resolve the current storage namespace on every call. [api.ts:517](C:/Users/drews/Development/TopSignal/frontend/src/lib/api.ts:517) obtains the current access token for each new request. The keyed subtree at [AccountAccess.tsx:120](C:/Users/drews/Development/TopSignal/frontend/src/app/AccountAccess.tsx:120) resets React state but does not cancel the outstanding operation.

**Evidence:** `audit-probes.test.ts` invokes the actual reconciliation, tracker and API functions. It holds A's expense response, changes the mocked session to B, then releases the response. A's account 91001 appears in B's scoped tracker, and the subsequent expense POST carries B's bearer with A's account ID. Both assertions pass. Fetch and token access are fakes; no real authentication service or expense database is contacted.

**Impact and limits:** B can inherit A's purchase metadata in browser storage. The client also sends a mutation with the wrong initiating identity. Existing backend ownership checks normally reject an account belonging exclusively to A; this is not a demonstrated backend authorization bypass. If both users have an owned record for the same external broker account ID, those checks cannot identify the stale intent. That accepted-write scenario was not exercised against the backend. Sign-out-only and demo-transition variants need additional regression coverage; the confirmed case is A-to-B identity replacement.

**Proposed fix:** Capture an authentication generation and storage scope at operation start. Abort/invalidate the whole operation on identity or mode changes, check the generation after every await and immediately before each storage/API mutation, and bind API dispatch to the expected current identity. Merely ignoring late React state updates is insufficient. Avoid continuing an old operation by retaining A's bearer after sign-out.

**Validation:** Delayed account reads, paginated expense reads, and delayed creates/deletes; A-to-B, A-to-signed-out, cross-tab session changes, and demo on/off. Assert no writes to the replacement scope, no follow-up requests after invalidation, and an actionable cancellation state. Retain backend two-owner ownership tests, including shared external account IDs.

### A2 — Reconciled working orders never enter timed-exit processing

**Trigger:** A live submission becomes `submission_unknown` or remains `pending`. Reconciliation subsequently finds exactly one matching broker order with the correct tag, account and contract, but its provider status is Open (1) or Pending (6). The timed-exit deadline passes while that status persists.

**Implementation:** [bot_service.py:11859](C:/Users/drews/Development/TopSignal/backend/app/services/bot_service.py:11859) records the recovered provider order ID. [bot_service.py:11865](C:/Users/drews/Development/TopSignal/backend/app/services/bot_service.py:11865) deliberately leaves the attempt unresolved to block further exposure. However, [topbot_time_exit.py:63](C:/Users/drews/Development/TopSignal/backend/app/services/topbot_time_exit.py:63) returns early for every status except submitted/cancelled, before reading the deadline or checking/cancelling working orders. [bot_worker.py:843](C:/Users/drews/Development/TopSignal/backend/app/bot_worker.py:843) commits reconciliation before calling the exit processor at [bot_worker.py:503](C:/Users/drews/Development/TopSignal/backend/app/bot_worker.py:503), so this is the actual persisted state transition.

**Evidence:** The backend probe creates a fake-provider entry using the existing execution-safety fixtures, marks the acknowledgement unknown, then returns a uniquely matching Open order. At deadline plus ten minutes, four reconcile/commit/exit cycles recover the provider ID but produce zero cancel calls, zero close calls, and an exit still marked pending. The test uses real database/service logic with an in-memory SQLite database and no real broker transport.

**Impact and limits:** A known working entry can remain available to fill beyond its intended entry/exit window. New entries remain blocked and diagnostics escalate, so this is not a duplicate-submission finding. The current TopBot route restricts live routing to verified simulated Practice accounts; this reproduction does not demonstrate cash-account exposure. Partial-fill and bracket interactions need additional fixtures and eventual broker acceptance.

**Proposed fix:** Separate verified order identity and working/fill state from the latch that prohibits another entry. Once identity is established, allow the timed-exit path to cancel a working entry after its deadline, verify cancellation and actual position state, and close only appropriately attributed exposure under the existing lease and eligibility checks. Preserve uncertainty until broker readback supports the next action.

**Validation:** Open and Pending orders, zero/partial/full fills, cancellation racing a fill, lost cancel acknowledgement, duplicate tags, wrong account/contract, restart between reconciliation and exit, expired lease, and stopped automation. Assert no resubmission, no unrelated position close, deadline cancellation when justified, and persistent reconciliation after uncertain outcomes. Run the locking variants on disposable PostgreSQL. Real Practice/OCO acceptance remains a separate authorized exercise.

### A3 — Anonymous local mode lacks a server-side cross-origin mutation boundary

**Trigger:** The local API runs with `AUTH_REQUIRED=false`, as configured by [offline-env.cjs:25](C:/Users/drews/Development/TopSignal/scripts/offline-env.cjs:25), including the connected local profile. A request from an untrusted origin reaches a bodyless POST route with a valid account or bot ID.

**Implementation:** [main.py:617](C:/Users/drews/Development/TopSignal/backend/app/main.py:617) selects the default local user when no bearer is required. The origin helper at [main.py:525](C:/Users/drews/Development/TopSignal/backend/app/main.py:525) controls response headers, not mutation authorization. [main.py:2150](C:/Users/drews/Development/TopSignal/backend/app/main.py:2150) accepts a bodyless main-account POST. [main.py:3455](C:/Users/drews/Development/TopSignal/backend/app/main.py:3455) exposes the same request shape for Stop Automation; that control was inspected only.

**Evidence:** The isolated ASGI probe posts to `/api/accounts/91001/main` with an untrusted Origin, `Sec-Fetch-Site: cross-site`, form content type and no bearer. It receives 200, and an independent database read verifies the disposable account became main. The test exercises middleware and the real route. No stop/emergency endpoint was called.

**Impact and limits:** Where a browser permits delivery to the local server, a foreign page can cause a simple POST side effect despite being unable to read the response. Valid IDs must be known or guessed. Modern browser local-network restrictions can affect delivery; an end-to-end hostile-page demonstration was not performed. Authentication-required deployments do not acquire anonymous access through this finding. This is a confirmed missing server check, not a claim that every browser/deployment is exploitable.

**Proposed fix:** Reject unsafe requests with untrusted origins in anonymous mode, add an explicit trusted-host policy, and consider a local session/CSRF token. Define no-Origin behavior for CLI and supervisor callers rather than accidentally breaking them. Retain loopback binding and existing bearer authorization.

**Validation:** Disallowed simple form POSTs and no-cors requests must fail before writes; allowed local UI requests must work. Cover absent/null origins, Host variations, local connected/offline profiles, and auth-required mode. Add a disposable two-origin browser test to establish actual browser behavior.

### A4 — Normal Windows test execution exceeds the observation artifact path limit

**Trigger:** Run the documented offline backend command in this Windows checkout with pytest's default temporary root. Long test directory names combine with two full SHA-256 path components.

**Implementation:** [probabilistic_shadow.py:32](C:/Users/drews/Development/TopSignal/backend/app/services/probabilistic_shadow.py:32) builds the hashed model path; [probabilistic_observation.py:24](C:/Users/drews/Development/TopSignal/backend/app/services/probabilistic_observation.py:24) expands it into the `unvalidated-models` layout. The fixture writes it at [test_unvalidated_observation.py:35](C:/Users/drews/Development/TopSignal/backend/tests/test_unvalidated_observation.py:35). [run_offline_tests.py:57](C:/Users/drews/Development/TopSignal/backend/tools/run_offline_tests.py:57) passes the ordinary pytest defaults through.

**Evidence:** The required baseline command completed with 2,317 passes, nine skips, and 14 setup errors, all failing to create these long JSON paths. The affected module then passed all 14 tests with a short new `--basetemp`. A full rerun using a short new root passed 2,331 tests with the same nine skips. No semantic change was needed.

**Impact and limits:** A supported development workflow produces a red suite and does not exercise the affected guard assertions. Deep configured model roots may have the same path problem, but failure of the normal production cache root was not reproduced. The passing short-root run prevents misclassifying these as 14 logic defects.

**Proposed fix:** Give Windows tests a documented, uniquely allocated short disposable root or shorten fixture layout while retaining a separate path-portability test. If application paths are shortened, keep full hashes in manifests, verify identity on load, and design compatibility without deleting existing artifacts. Do not reuse arbitrary directories as pytest basetemp, because pytest can clear them.

**Validation:** Standard documented test invocation on Windows, a normal checkout, deliberately deep cache roots, and artifact ownership/integrity checks. The test-root correction is small; an application artifact-layout migration would require additional design and effort.

### A5 — Retained journal snapshots disagree with dashboard fee normalization

**Trigger:** Refresh a journal trade-stat snapshot containing a closed event whose `fee_scope` is `per_side`.

**Implementation:** [journal.py:815](C:/Users/drews/Development/TopSignal/backend/app/services/journal.py:815) loads raw event fees for the snapshot, without fee scope in its selected columns. [journal.py:1367](C:/Users/drews/Development/TopSignal/backend/app/services/journal.py:1367) always treats the raw fee as round-turn. The snapshot uses that helper at [journal.py:1386](C:/Users/drews/Development/TopSignal/backend/app/services/journal.py:1386). In contrast, [projectx_trades.py:1733](C:/Users/drews/Development/TopSignal/backend/app/services/projectx_trades.py:1733) uses [projectx_trades.py:1782](C:/Users/drews/Development/TopSignal/backend/app/services/projectx_trades.py:1782) to normalize per-side fees before dashboard metrics.

**Evidence:** One synthetic MNQ close with gross P&L $100, per-side fees $0.36, and commissions $0.50 yields journal net $99.14 and dashboard net $98.78 through the actual calculation functions. The $0.36 difference is the missing second fee side.

**Impact and limits:** Retained journal snapshots and consumers of those statistics can overstate net P&L. Round-turn events do not trigger this example. Journal is intentionally redirected out of current navigation; this finding is about the still-registered backend, not a reason to restore its old frontend. No historical records were inspected or recalculated.

**Proposed fix:** Share fee-scope normalization with the event metrics path and include scope in the snapshot query. Preserve existing stored snapshots until a separately reviewed recomputation is authorized.

**Validation:** Cross-consumer equality for per-side and round-turn rows, separate commissions, fee overrides, breakeven/losing trades, split/partial closes and imported lifecycles. Verify existing snapshot/version-conflict behavior. A shared financial fixture should compare dashboard, trade display and journal calculations.

## Current implementation map

| Boundary | Current implementation and behavior |
|---|---|
| Routed frontend | [routes.tsx:37](C:/Users/drews/Development/TopSignal/frontend/src/app/routes.tsx:37) protects the standalone bot-results page. The main shell routes Dashboard, Accounts, Trades, Expenses, Bot, browser-local Simulation, newly added Backtesting, and Appearance. Journal and Data redirect to Dashboard while preserving query strings; Themes redirects to Appearance. Unused former pages alone do not establish a broken feature. |
| Authentication and browser state | AccountAccess bootstraps/subscribes to Supabase; account identity keys the main subtree. API helpers attach bearer tokens and enforce demo read-only transport. Browser financial state and account caches are scoped. Those mechanisms protect individual operations but do not invalidate A1's multi-step operation. |
| Backend API | `backend/app/main.py` holds account, expense, payout, credential, import, bot and retained journal routes. Market-data, market-event, market-observation and risk-forecast routers add their respective APIs. Authentication middleware covers `/api/`, `/metrics/`, `/projectx/` and `/trades`; endpoint/service ownership checks remain essential. |
| Relational data | SQLAlchemy sessions use PostgreSQL for deployed relational records; offline development and this audit's fixtures use SQLite. Accounts, credentials, journals, trade events, bot state and saved result metadata belong here. Schema/adoption/privilege checks exist in CI but were not executed locally against PostgreSQL. |
| Execution | TopBot start/preparation and bot_service validate ownership, account eligibility, mode, risk and worker availability. The recurring worker holds a durable lease/fence, records deterministic order attempts before broker I/O, and reconciles ambiguous acknowledgements by identity. Timed exits run independently of new-entry evaluation. A2 lies between reconciliation and that exit path. |
| Emergency controls | Stop Automation disarms new entries while preserving pending exits. Emergency flatten is a distinct account-scoped operation with confirmation, durable state and broker reconciliation. Controls and tests were inspected; no actual emergency operation was invoked. |
| Historical data | Databento archives, normalized candles and replay arrays use the configured filesystem cache. The relational importer retains its SQLite-only guard. The current historical UI reads that cache. TopBot backtest jobs are owner-scoped temporary memory jobs with one running job, up to eight retained entries, a one-hour expiry and a 366-day request bound. |
| Local Simulation | A dedicated browser Worker and local exchange runtime handle synthetic prices, orders and account state. The order path has no real broker API dependency; the browser initialized it with an empty account database and no credentials. This is distinct from Bot Dry Run, which follows external market data when configured. |

## Coverage and previous findings

**Security:** Inspected JWT verification/admission, owner checks, credential encryption/fail-closed local-key behavior, scoped storage, account remounts, demo request cancellation, import validation, and retained image upload/read paths. Upload handlers impose bounded reads, and service code checks entry ownership and permitted image types/signatures. No real secrets were decrypted or printed. A1 and A3 are the actionable findings. Real Supabase sign-in, token expiry, multi-tab browser auth, deployed proxy headers and direct database-role privileges were not exercised.

**Execution:** Inspected dry/live gates, Practice classification freshness, deterministic attempts, durable uncertainty, lease/fencing checks, risk gates, stale-data handling, reconnect/restart paths, timed exits and emergency separation. Existing safety tests passed under the offline runner. A2 supplements the existing unknown-submission escalation test: an identified working order still cannot reach deadline cleanup. SQLite and fake clients do not verify PostgreSQL concurrency, actual OCO/bracket semantics or broker fill/cancel races.

**Financial correctness:** Followed event fee scope, gross/net calculations, lifecycle reconstruction, CSV preview/confirmation and duplicate handling, expense/payout summaries, balances, drawdown and timezone/date-bound helpers. The September regression fixes for no-loss profit factor, import refresh invalidation, non-USD totals, mixed timezone bounds, date/ID bounds, expense categories and Stop wording remain represented in code and passing suites. Their earlier interactive fixture sessions were not all repeated. A5 is a distinct retained-consumer mismatch. The historical account-balance chart's estimate caveat and the backtest's exit-candle excursion caveat are explicit product limitations, not newly proven defects.

**Frontend:** Desktop smoke checks covered all current routes using a sanitized empty SQLite workspace on API 8016/UI 5186. Accounts and Trades showed empty states; Expenses completed loading with zero totals; Bot displayed disabled execution controls and an explicit missing-credentials market-data error; Backtesting explained that local history was not installed; Appearance exposed labeled controls; Simulation reached its initialized local exchange state. Dashboard and Simulation had no document-level horizontal overflow at a 390×844 viewport. Labels, landmarks, focusable controls and existing accessibility/race tests were inspected. This was not a complete screen-reader, keyboard, theme/zoom, populated mobile table, or provider-outage browser matrix. No bot execution or emergency control was clicked.

**Backend/performance:** Inspected bounded request bodies, auth-verifier admission, API pagination/aggregate paths, risk-forecast trade/work limits, backtest result caching, and worker/stream/writer shutdown behavior. Risk forecasting caps source trades and scenarios; historical jobs serialize expensive work. No production query trace or representative PostgreSQL `EXPLAIN ANALYZE` was available. This audit does not recommend speculative indexes or repeat old SQLite timing measurements as current production latency. Transport deadlines, saturation, large-account memory use, and shutdown during provider failure still need controlled load/fault tests.

**Storage/deployment:** Read the complete storage policy and checked the current cache/import/persistence paths. Local configuration does not override the default cache root. A read-only inventory found approximately 754.4 MiB in 832 files under `backend/storage/databento`, including source files, versioned data, model artifacts and exports. No archive was opened/restored, no cache was rebuilt, and no SQL historical import ran. The SQLite-only guard remains in [databento_ingestion.py:45](C:/Users/drews/Development/TopSignal/backend/app/services/databento_ingestion.py:45). Persistent-cache and separate backup requirements remain documented. Startup/schema checks, readiness versus liveness, the production supervisor/STOP latch, graceful shutdown code and Windows script tests were reviewed. Nothing here verifies the deployed machine's disk, service identity, TLS, scheduler, shutdown under load, or backup recoverability.

## Unverified concerns and optional improvements

These are not additional confirmed defects.

| Concern | Evidence and next validation | Priority and effort |
|---|---|---|
| PostgreSQL acceptance | The CI file provisions PostgreSQL 16 and tests schema, upgrades/adoption, privileges, concurrency and worker safety. Docker's daemon was unavailable locally and PostgreSQL binaries were absent. Reproduce these jobs in a newly provisioned disposable database; never point them at trading data. | High deployment gate; 1–2 days once a disposable server is available. |
| Off-device history backup and restore | The storage policy says the ignored source archives need an off-device backup destination. The local cache exists, but an external backup and successful restore were not verified. Confirm checksum coverage for sources/manifests/models, schedule backups, then restore to a separate location. Database/Git backups do not supply this. | High operational priority; 0.5–2 days plus transfer time. |
| Saved legacy backtest payload budget | [bot_backtesting.py:993](C:/Users/drews/Development/TopSignal/backend/app/services/bot_backtesting.py:993) includes sampled curves plus trades; [bot_backtesting.py:3347](C:/Users/drews/Development/TopSignal/backend/app/services/bot_backtesting.py:3347) persists the result. Input candles are not embedded, and execution/work/memory limits exist. An explicit serialized-byte ceiling for each persisted result was not found. Measure the largest supported saved result and projected cloud growth before adding a byte cap or summary/detail split. This is not evidence of current Supabase bloat. | Medium hardening; 0.5–1 day measurement, then size-dependent work. |
| Independent alerts and total request deadlines | Local readiness, heartbeat and diagnostic logs exist. They cannot alone prove notification when the host is off, or bound DNS/slow-drip responses. Recheck the earlier readiness report's acceptance items through an isolated fault server and externally observed host-loss drill. | High before unattended use; 1–3 days plus operational setup. |
| Shared financial contract fixtures | A5 shows how separate consumers drift despite passing their own tests. Add representative event/lifecycle fixtures compared across API consumers and frontend formatting. Include DST transitions and fee-scope transitions. | Medium; 1–2 days, after the fee semantics are agreed. |

## Validation record

Commands and their launchers were inspected before execution. Backend tests ran through the offline wrapper, which strips application credentials, disables dotenv and blocks non-loopback connections. Its connection counter was zero in every completed run. Fakes temporarily enable execution gates only inside isolated safety tests; no real broker was authenticated or contacted.

| Check | Result |
|---|---|
| `npm --prefix frontend test` | Passed: 148 files, 1,271 tests. |
| `npm --prefix frontend run lint -- --max-warnings=0` | Passed; repeated after concurrent panel edits, also passed. |
| `npm --prefix frontend run build` | Passed TypeScript and Vite; repeated after concurrent panel edits, also passed. |
| `npm run test:dev-scripts` | Passed: 40 tests, no skips, including Windows-specific checks. |
| `backend/.venv/Scripts/python.exe backend/tools/run_offline_tests.py` | 2,317 passed, nine skipped, 14 setup errors, eight subtests passed. All setup errors traced to A4. |
| Affected backend module with new short basetemp | Passed: 14 tests. |
| Full backend suite with new short basetemp | Passed: 2,331 tests, nine skips, eight subtests. |
| Investigation backend probes | Passed: three defect-characterization tests for A2, A3 and A5. |
| Investigation frontend probe | Passed: one defect-characterization test for A1. |
| Current risk-forecast panel tests after concurrent edit | Passed: seven tests. |
| `git diff --check` | Passed; Git emitted non-blocking LF/CRLF normalization warnings for existing changes. |
| Isolated UI | All current routes visited; empty/error states and two narrow layouts checked. Temporary browser and both servers closed; no listeners remained on 8016/5186. |
| PostgreSQL-specific checks | Not run: Docker daemon unavailable; no disposable PostgreSQL or local server binaries. Eight skipped test cases require PostgreSQL. |
| Optional market-data fixture | One skipped test requires an uninstalled workspace capture. |

The complete hosted CI workflow and a fresh dependency-advisory scan were not run. CI configuration was inspected, rather than treated as evidence that the current working tree has passed hosted jobs.

The full backend rerun used `tests -q -ra --basetemp C:/Users/drews/AppData/Local/Temp/tsall1026`; that directory was verified absent before use. Do not copy this path into a future run without choosing another new disposable location.

The reproduction files intentionally assert the defective current behavior. They are investigation evidence outside the normal application test suites, not acceptance tests to preserve after a fix:

- [Frontend probe](C:/Users/drews/Development/TopSignal/docs/audits/2026-10-26/audit-probes.test.ts), with [isolated Vitest configuration](C:/Users/drews/Development/TopSignal/docs/audits/2026-10-26/vitest.config.mjs). Run from the repository root: `node --no-experimental-webstorage frontend/node_modules/vitest/vitest.mjs run --config docs/audits/2026-10-26/vitest.config.mjs`.
- [Backend probes](C:/Users/drews/Development/TopSignal/docs/audits/2026-10-26/test_audit_probes.py). Run: `backend/.venv/Scripts/python.exe backend/tools/run_offline_tests.py ../docs/audits/2026-10-26/test_audit_probes.py -q -s`.

Raw validation logs, sanitized UI harness, local inventory, starting Git status and 871 baseline file hashes are retained in `C:\Users\drews\AppData\Local\Temp\topsignal-audit-20261026-codex`. Initial probe-harness issues were corrected before the results above: the timed-exit probe needed the worker's reconciliation commit, and the standalone Vitest configuration needed a repository-relative include glob. Those were audit harness corrections, not application fixes.

During the audit, concurrent activity changed `frontend/src/pages/dashboard/components/AccountRiskForecastPanel.tsx` and removed two pre-existing preview files, `frontend/.risk-forecast-mobile-preview.html` and `frontend/.risk-forecast-preview.html`. This audit did not make or revert those changes. The current panel was reread and its tests, frontend lint and build repeated. Other baseline files matched their initial hashes at the final comparison. Only this report and the three reproduction/configuration files were added by the audit. Existing staged/unstaged work was not reset, committed, stashed or rewritten. Builds and tests produced their normal ignored outputs.

No production data mutation, migration application, source-archive restoration, retention cleanup, billing change, real order, authenticated broker request or emergency-control action was performed.
