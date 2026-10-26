# TopSignal audit fixes — October 26, 2026

Implements A1–A5 from [the investigation report](C:/Users/drews/Development/TopSignal/docs/audits/2026-10-26/report.md) in its recommended order. The initial report and its defect-characterization probes remain historical evidence; the regression tests below assert the corrected behavior.

## Implemented behavior

| Priority | Change | Regression evidence |
| --- | --- | --- |
| A1 — account isolation | Combine expense reconciliation captures the authenticated identity and demo mode, permanently cancels on identity/mode changes or unmount, checks after asynchronous reads and before each mutation/storage update, and verifies the dispatch token after token acquisition. Same-user token refresh remains valid. | Delayed reads; sign-out; A→B→A; demo round trips and cross-tab events; late create/delete completion; pagination; token acquisition; unmount; same-user refresh. |
| A2 — recovered working-order exits | A due timed exit can recover exactly one tagged order with matching account, contract and recorded provider ID. It cancels the working entry behind the existing Practice/live/lease fences, requires terminal order readback, verifies fill attribution, then uses the existing verified reduce-only close. Unknown outcomes retain the pending obligation and protective stops. Provider read outages and uncertain cancellation back off. | Open/Pending orders with zero, partial and full fills; stopped-run recovery; fill/cancel races; lost acknowledgements; durable retry/completion; identity mismatches; lost lease; funded accounts; disabled live gate; unverified exposure; no early cancellation; provider-read outage. No entry resubmission. |
| A3 — anonymous local API boundary | Anonymous protected routes require a loopback peer and one valid loopback Host. Foreign/null origins and cross-site requests without Origin fail before route handlers, including provider-refresh GETs. Configured local origins and loopback no-Origin CLI calls work. Bearer authentication remains required in authenticated mode. | Real middleware and account route with disposable SQLite rows: rejected simple POST leaves account unchanged; allowed UI/CLI succeeds; malformed/duplicate Host and remote peer rejected; refresh GET blocked; authenticated mode unchanged. The development-script ASGI fixture now supplies a realistic loopback Host. |
| A4 — Windows test portability | The offline runner allocates a unique short pytest root on Windows, closes diagnostic logs, and cleans only its own fresh fixture directory. Explicit basetemp and non-Windows arguments retain their existing behavior. Application/model/cache paths are unchanged. | Standard Windows runner command, long observation-artifact fixtures, unique roots, caller-owned sentinel preservation, explicit basetemp in both forms, non-Windows arguments, and the existing subprocess network guard. |
| A5 — fee consistency | Journal snapshots load fee scope and share event-fee normalization with dashboard metrics. Closed per-side fees are doubled; round-turn fees and commissions are not doubled. Existing stored snapshots remain unchanged until explicitly refreshed. | Cross-consumer net/fee equality for both scopes, separate/absent/zero commissions, wins/breakeven/losses, split closes plus entry events, commission effective-date and DST instants, eagerly loaded scope, and explicit snapshot refresh. The audit example now produces net $98.78 in both consumers. |

### Main implementation references

- A1: [accountOperation.ts:19](C:/Users/drews/Development/TopSignal/frontend/src/lib/accountOperation.ts:19), [authIdentity.ts](C:/Users/drews/Development/TopSignal/frontend/src/lib/authIdentity.ts), [expenseReconciliation.ts:362](C:/Users/drews/Development/TopSignal/frontend/src/pages/expenses/expenseReconciliation.ts:362), [api.ts:505](C:/Users/drews/Development/TopSignal/frontend/src/lib/api.ts:505).
- A2: [topbot_time_exit.py:31](C:/Users/drews/Development/TopSignal/backend/app/services/topbot_time_exit.py:31), [bot_service.py:11784](C:/Users/drews/Development/TopSignal/backend/app/services/bot_service.py:11784). Nested exit-plan JSON is copied on save so retry/completion updates remain durable after the pre-cancel audit flush.
- A3: [local_request_security.py:13](C:/Users/drews/Development/TopSignal/backend/app/local_request_security.py:13), [main.py:592](C:/Users/drews/Development/TopSignal/backend/app/main.py:592).
- A4: [run_offline_tests.py:21](C:/Users/drews/Development/TopSignal/backend/tools/run_offline_tests.py:21).
- A5: [topstep_fees.py:30](C:/Users/drews/Development/TopSignal/backend/app/services/topstep_fees.py:30), [journal.py:826](C:/Users/drews/Development/TopSignal/backend/app/services/journal.py:826), [journal.py:1355](C:/Users/drews/Development/TopSignal/backend/app/services/journal.py:1355).

### Regression suites

- [expenseReconciliation.identity.test.ts](C:/Users/drews/Development/TopSignal/frontend/src/pages/expenses/expenseReconciliation.identity.test.ts), plus the mounted-page tests in [ExpensesPage.test.tsx](C:/Users/drews/Development/TopSignal/frontend/src/pages/expenses/ExpensesPage.test.tsx).
- [test_topbot_time_exit.py](C:/Users/drews/Development/TopSignal/backend/tests/test_topbot_time_exit.py) and the existing execution-safety suite.
- [test_local_request_security.py](C:/Users/drews/Development/TopSignal/backend/tests/test_local_request_security.py).
- [test_offline_runner.py](C:/Users/drews/Development/TopSignal/backend/tests/test_offline_runner.py) and the existing observation-artifact suite.
- [test_journal_fee_consistency.py](C:/Users/drews/Development/TopSignal/backend/tests/test_journal_fee_consistency.py), plus corrected fee-scope expectations in [test_journal_service.py](C:/Users/drews/Development/TopSignal/backend/tests/test_journal_service.py).

## Validation

Commands were inspected before execution. Backend execution used `backend/tools/run_offline_tests.py`, which disables dotenv loading, clears operator credentials, disables live gates and blocks non-loopback socket connections. Broker behavior in regression tests is simulated; persistence uses isolated fixtures.

| Check | Result |
| --- | --- |
| `npm.cmd test -- --maxWorkers=4` in frontend | 149 files, 1,284 tests passed. |
| `npm.cmd run lint -- --max-warnings=0` in frontend | Passed. |
| `npm.cmd run build` in frontend | TypeScript and Vite build passed. |
| `npm.cmd run test:dev-scripts` at repository root | 40 tests passed. |
| `backend/.venv/Scripts/python.exe backend/tools/run_offline_tests.py` | 2,398 passed, 9 skipped, 8 subtests passed; zero attempted external connections. Standard invocation completed successfully on Windows without a manual basetemp. |
| Focused timed-exit and execution-safety suites after outage/backoff coverage | 195 tests passed; zero attempted external connections. |
| Follow-up: the eight PostgreSQL checks on disposable local PostgreSQL 16.15 | 8 passed, 0 skipped, 0 failures; zero attempted external connections. See the PostgreSQL acceptance record below. |
| `git diff --check` | Passed; Git reported existing LF/CRLF conversion notices. |

The first concurrent frontend run had one 5-second Simulation test timeout (1,283 passed); the complete rerun with four workers passed without changing Simulation code or increasing test timeouts. The first development-script run rejected its missing-Host synthetic request; after correcting the fixture, all 40 passed. Intermediate targeted runs also caught expectation mismatches and nested exit-plan JSON persistence; those were corrected and included in subsequent passing runs.

## Limits and preservation

- The original offline run skipped eight PostgreSQL checks because Docker was unavailable. The authorized follow-up below ran all eight against a disposable PostgreSQL database successfully. The optional workspace capture remains unverified because it is not installed. Passing these targeted checks does not establish all production PostgreSQL behavior.
- Real Supabase sign-in, live broker execution, deployed proxy behavior and a hostile-origin browser demonstration were not exercised. The browser/auth transport cases use controlled fixtures and middleware tests.
- Aborting a request cannot undo a mutation already accepted by the server under the original identity. The fix prevents later cross-identity dispatch, replacement-user ledger writes and continuation of the old reconciliation queue. Reconciliation can be explicitly restarted after reviewing the current account.
- No migrations, production database operations, archive restores, billing changes or authenticated broker calls were performed. Databento import guards, historical filesystem locations and saved-result bounds were not changed.
- Existing uncommitted files were preserved using an initial hash manifest and source snapshots. Final comparison found no missing original files and only intended implementation/test/documentation changes. The Supabase storage policy is byte-identical to its initial state, including its preexisting uncommitted edits. The investigation report and original audit probes are also unchanged. No commit was created.

Local validation logs and the initial source snapshot are retained at `C:\Users\drews\AppData\Local\Temp\topsignal-fixes-20261026-6e9yb0ic`.

## PostgreSQL acceptance follow-up

At the user's request, Docker Desktop was started and the cached `postgres:16.15-bookworm` image used for a dedicated local test container. Database `topsignal_audit_voynvajp` was confirmed empty before applying `db/schema.sql`. The migration runner validated and baselined all 56 current migration checksums, then passed its read-only status check. No historical migration files were modified or replayed, and no `databento_*` tables were created.

The container published only `127.0.0.1:61534`, used its own Docker bridge and temporary in-memory PostgreSQL data directory, and had no repository or existing database volume mounts. The empty database was about 7.7 MB and about 9.8 MB after schema creation. It contained only schema metadata, instrument seeds and synthetic test fixtures.

A task-local harness cleared inherited provider credentials and libpq routing variables, disabled dotenv and live execution, and invoked `backend/tools/run_offline_tests.py`. After that runner's environment reset and socket guard were installed, the harness supplied only the owned database's explicit loopback URL through `TOPSIGNAL_TEST_POSTGRES_URL`. The application's default database remained in-memory SQLite; the PostgreSQL fixtures used their separate PostgreSQL engines. No configured production connection was loaded.

Both PostgreSQL modules ran together with verbose results: **8 passed in 1.13 seconds**, no skips, no failures, and `external connections blocked=0` (no external socket connection attempts).

| PostgreSQL behavior checked | Result |
| --- | --- |
| Transaction-local statement/lock/idle timeouts and recovery after a cancelled query | Passed |
| Two-worker lease exclusion and rejection of an expired owner after takeover | Passed |
| Partial trade-day writer waits and cannot downgrade a complete sync | Passed |
| Journal merge rejects a concurrent autosave committed after its snapshot | Passed |
| Journal statistics pull rejects a concurrent date/body autosave before final lock | Passed |
| Stale provider writer preserves concurrently completed P&L | Passed |
| Audited import provenance wins when provider locks first | Passed |
| Audited import provenance wins when importer locks first | Passed |

No application or test changes were needed. After execution, accounts, journals, trade events, trade-day sync rows, import batches and worker leases were all verified empty. The owned container, its temporary database and its empty network were removed; the disposable credential was discarded. Other Docker containers and databases were not operated on. Docker Desktop remains running.

The follow-up's initial source snapshot, verbose test log, schema verification, fixture-cleanup counts and container identity are retained in `C:\Users\drews\AppData\Local\Temp\topsignal-pg-20261026-voynvajp`. Its hash comparison verifies that the only repository change in this follow-up is this validation document. Existing uncommitted application work, the storage policy and all migration files remain unchanged.
