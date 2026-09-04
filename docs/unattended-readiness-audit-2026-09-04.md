# TopSignal unattended-operation audit — 2026-09-04

**NO-GO: 24/7 unattended live trading. NO-GO: 24/7 unattended provider Practice execution. NO-GO: certifying 24/7 operation on the second laptop before its acceptance checks. GO: the bounded, isolated offline dry-run workflow tested here.**

All implementation changes are local and uncommitted. No commit, push, merge,
deployment, scheduled-task installation, external database migration, or broker
order action was performed. The audit did not arm the installed bot. Test runs
used fixtures, disabled dotenv loading, disabled execution gates, temporary
SQLite, and mocked providers. The offline runner blocks external socket
connections and fails even if a test catches that guard's exception. Separately
running user sessions were not monitored against broker statements.

## Baseline and coverage

`git fetch origin main` succeeded. Both HEAD and fetched origin/main were
`56bea96953793b74fbdeb540f142923a4b6bbe43` (Fix historical PnL calendar
synchronization). `git merge-base --is-ancestor 56bea96 origin/main` returned
0. The initial working tree was clean; no branch/history change was needed.

The baseline inventory contained 464 tracked files, including 113 Python files,
53 SQL files, 238 TypeScript/TSX files, deployment scripts, configuration,
documentation, fixtures and assets. Review combined repository-wide inventory
and static scans with detailed tracing of startup, authentication, worker
ownership, provider transport, order gates, persistence, trading-day accounting,
UI controls and deployment. This is not a formal proof or a line-coverage claim.
Only `.env.example` was tracked. A limited private-key/known-token-pattern scan
found no matches; actual operator `.env` values were not inspected.

## Material findings and disposition

| ID / severity | Problem | Resolution and regression evidence |
|---|---|---|
| C1 Critical — fixed | Persisted live confirmations could survive worker recovery. | Ownership recovery stops all provider-routing runs and clears routing confirmation before streams/evaluation; configuration remains independent. Dry-run alone resumes. Worker recovery tests and the UI confirmation regression cover this. |
| C2 Critical — fixed | Unexpected exceptions/malformed submissions could become definite failures and allow new attempts after possible broker acceptance. | Preserve `submission_unknown`; block new exposure until reconciliation. Matching tags require matching account, contract and known order identity. Redirect, interrupted, oversized and invalid responses retain ambiguity. Execution-safety and client regressions cover these cases. |
| C3 Critical — fixed locally; integration pending | Cancellation/shutdown could lose ownership handles while a synchronous evaluator or lease renewal remained active. | Revoke mutation permission immediately, shield/drain actual threads, retain incomplete shutdown handles, reject runtime replacement, and release ownership only after cleanup. Cancellation, lease, two-worker and soak regressions pass. PostgreSQL acceptance remains unexecuted. |
| C4 Critical — fixed | Different app identities could connect the same physical broker account and bypass user-scoped exclusion. | Live admission and final mutation reject shared account ownership, foreign live runs and foreign unresolved/recent submissions, including after an account-row deletion. No foreign account details are returned. |
| C5 Critical — fixed | Permissive classification, malformed identifiers or invalid quotes could look authoritative. | Require explicit provider metadata, integral IDs, valid void/P&L fields, finite positive prices and genuine timestamps. Future, undated, crossed and out-of-order quotes fail closed. Live request booleans are strict. |
| H1 High — fixed locally | Reconciliation could expire while provider calls or database locks were pending. | Add a 15-second monotonic final-preflight budget, checked after locks; recheck account active/tradable/classified state, emergency latch, worker ownership and shutdown at the mutation boundary. Provider losses activate cooldown before local sync. |
| H2 High — fixed locally; PostgreSQL behavior pending | Database waits were not explicitly bounded, remote libpq defaults permitted TLS downgrade, and SQL errors could expose bound values. | Remote production application/migration URLs require `sslmode=verify-full` and an explicit readable CA file before any connection; force TLS instead of GSS and reject host/service overrides. Connect/pool wait 10s; application transaction statement timeout 30s, lock timeout 5s, idle-in-transaction timeout 60s; keepalives and secret-safe errors/parameters. Migration connect 10s, lock 15s, statement 300s, plus supervisor deadline. Readiness reflection uses the transaction-bound connection. |
| H3 High — fixed | Multi-day sync could commit a truncated page as a successful refresh and advance past missing fills. Legacy metrics also disagreed on missing P&L. | Reject incomplete chunks before persistence; successful retry recovers the same range. All legacy metrics reject missing/invalid authoritative P&L with actionable HTTP 503, rather than guessing futures dollars from price points. Existing calendar/DST/sync suites and new integrity tests pass. |
| H4 High — fixed | Authenticated transports could follow redirects or accept insecure credential-bearing endpoints; startup/storage errors could leak secrets. | Configured broker endpoints require TLS without userinfo/query/fragment; authenticated transports reject redirects and bound bodies. SignalR adds a runtime token query that must never be logged. Gemini/storage use the shared no-redirect transport; Gemini requires HTTPS and storage allows HTTP only for explicit loopback development/test. Credential representations omit secrets. Safe formatting redacts recognizable tokens/URL query data; migration/storage errors are sanitized. Production validates authentication, encryption, supported database and unsafe escape hatches before startup work. |
| H5 High — fixed locally; hardware acceptance pending | Windows stop had no durable restart suppression; forced termination was the normal shutdown path. | Persistent `STOP` latch, private shutdown signal, graceful Uvicorn exit, bounded fallback termination, private ACL checks, limited task identity, exclusive supervisor lock and kill-on-close job containment. Harmless fixture tests verify parser, locks, timeout and task logic. |
| H6 High — fixed dependency finding | Frontend transitive dependencies had one high, one moderate and one low advisory. | Compatible lockfile updates for Browserslist, humanfs and selector parser dependencies. Full frontend audit now reports zero vulnerabilities; frontend tests/lint/build pass. |
| M1 Medium — fixed | Reconnect loops, nonfinite timers, cache accumulation and child-task cleanup could degrade long-running processes. | Bounded exponent/backoff, Retry-After, strict timer validation, expired-cache pruning, off-event-loop authentication and explicit task/thread cleanup. One healthy account no longer masks another account's failure. |
| M2 Medium — fixed | Generic readiness failures were unactionable; unknown provider state could lack a failed public check. | `/ready` returns stable reason plus the checks actually required for the workload, preserving safe 503 even if rollback fails. `/health` remains liveness; worker health drives bounded process recovery separately from provider readiness. |
| M3 Medium — fixed | Log size, frontend connections, request-frame buffering and slow uploads could consume resources without suitable bounds. | Size-rotated logs, frontend connection/socket bounds, API concurrency cap, total request-body deadline, collapsed tiny request frames and body limits. |
| M4 Medium — fixed | A BUY signal with an unavailable VWAP target could subtract `None` and crash evaluation. | Both BUY and SELL missing-target fixtures now return HOLD. |
| M5 Medium — open | Broad Python lint and typing checks are not clean. | Crash-oriented Python static checks pass; broad results are recorded below and were triaged for material defects. No mass suppression or unsafe automated refactor was applied. Complete the remaining typing/lint work before claiming all quality checks pass. |
| M6 Medium — open | Streaming MAE/MFE excursion state is memory resident; reconnect/restart/persistence failures can leave telemetry gaps. | No claim of complete excursion history. Trading uses separately reconciled broker order/position/fill data. Gap-aware durable telemetry/reconstruction remains separate work. |

## Remaining acceptance blockers

1. **Real-money account support and broker permission.** Current code deliberately
   refuses non-simulated accounts. Topstep currently prohibits automated ProjectX
   API trading in Live Funded Accounts. This is an architectural/account-policy
   blocker, not a flag to bypass. See [Topstep's LFA parameters](https://help.topstep.com/en/articles/10657969-live-funded-account-parameters).
2. **Actual broker execution evidence.** No verified Practice account was used.
   Demonstrate classification refresh, contract rollover, broker-held bracket/OCO
   protection, partial fills, rejects, delayed fills, ambiguous accepted requests,
   reconnect reconciliation, limit/cooldown behavior and emergency flatness.
   A local stop or failed request does not prove no exposure. Maximum daily loss
   is an entry gate, not a guarantee against gaps/slippage or a broker loss-limit substitute.
3. **Real PostgreSQL evidence.** Docker CLI exists, but its daemon was unavailable;
   no PostgreSQL server/test URL was provisioned. Eight integration tests skip.
   Validate fresh schema, upgrade/adoption/checksums, concurrent ownership and
   transaction timeouts against a disposable PostgreSQL database. Prove the
   production CA/hostname TLS handshake and rejection of an untrusted certificate
   or wrong hostname; the local fixtures validate configuration without a server.
   Then perform
   a backup/restore drill. Do not run these acceptance tests on trading data.
4. **Second-laptop operations.** Verify the limited task identity, sealed release,
   ACLs, cold boot without login, crash/kill recovery, stop latch across reboot,
   sleep/lid/power settings, time sync, network loss, disk growth and thermal load.
   No settings or tasks were installed on this or the second machine.
5. **Independent alert delivery and operator coverage.** Select/provision a monitor
   and prove alarms arrive when the laptop is powered off or disconnected. A
   local logfile alone cannot alert on loss of the entire laptop.
6. **Total transport deadline.** Socket timeouts and body caps do not prove an
   OS-enforced deadline across DNS stalls or indefinitely trickling responses.
   Worker staleness and the supervisor are recovery layers; outstanding broker
   requests still require reconciliation. Fault-test this on the target machine.
7. **Exclusive account operation.** All participating workers must share the same
   database. Separate laptop-local databases cannot coordinate. Other bots,
   manual sessions and copier activity cannot be fenced by TopSignal's database.
8. **Operator decisions.** Choose eligible accounts, actual contracts, trading
   windows, broker loss controls, risk budgets, alert recipient and escalation
   coverage. The audit did not invent or modify those account settings.

## Verification results

Commands ran from `C:\Users\drews\Development\TopSignal`, unless noted. Python
was 3.12.10; Node was 24.19.0. The Python venv was retained. Temporary static
analysis packages were installed only under ignored `tmp/audit-20260904/static-tools`.

| Check / command | Exact final result |
|---|---|
| `git fetch origin main`; `git rev-parse HEAD origin/main`; `git merge-base --is-ancestor 56bea96 origin/main` | Fetch successful; both refs `56bea96953793b74fbdeb540f142923a4b6bbe43`; ancestry exit 0. |
| `backend/.venv/Scripts/python.exe backend/tools/run_offline_tests.py` | **1,394 passed, 8 skipped in 23.04s**, exit 0. Guard: external connections blocked=0. Skips are the six existing PostgreSQL concurrency cases and two new PostgreSQL fencing/timeout cases. |
| `$env:SOAK_DURATION_SECONDS='60'`; `backend/.venv/Scripts/python.exe backend/tools/run_offline_tests.py tests/test_bot_worker_soak.py -q -s` | **1 passed in 61.29s**, exit 0; observed counters below. |
| Frontend full suite (`npm --prefix frontend test`, running `vitest run`) | **97 test files, 760 tests passed**, 10.33s, exit 0. |
| `npm --prefix frontend run lint -- --max-warnings=0` | Passed, exit 0. |
| `npm --prefix frontend run build` (`tsc -b && vite build`) | TypeScript check and production build passed, exit 0; Vite 3.98s. |
| `npm run test:dev-scripts` | **16 passed, 0 failed, 0 skipped**, 3.8473021s, exit 0. Includes PowerShell 5.1/7 parsing and harmless Windows process/lock fixtures. |
| `backend/.venv/Scripts/python.exe -m compileall -q backend/app backend/tools scripts` | Passed, exit 0. |
| `backend/.venv/Scripts/python.exe -m ruff check --isolated --select E9,F63,F7,F82 backend/app backend/tools backend/tests scripts` | All checks passed, exit 0. This is a narrow syntax/undefined-name check, not the broad lint result. |
| `backend/.venv/Scripts/python.exe -m ruff check --isolated backend/app backend/tools backend/tests scripts --output-format concise` | **FAILED: 489 errors**, exit 1; 276 marked fixable and 53 additional unsafe fixes available. They were not automatically applied. |
| `backend/.venv/Scripts/python.exe -m mypy backend/app --ignore-missing-imports --follow-imports=skip` | **FAILED: 311 errors in 14 files; 51 source files checked**, exit 1. Includes SQLAlchemy typing and optional/union mismatches. Limited import following means this is not an exhaustive dependency type check. |
| `npm audit --json`; `npm --prefix frontend audit --json` | Both report **0 vulnerabilities**, including development dependencies. |
| `backend/.venv/Scripts/python.exe -m pip_audit -r backend/requirements.txt --no-deps --disable-pip --format json --output tmp/audit-20260904/pip-audit.json` | No known vulnerabilities found, exit 0. |
| `backend/.venv/Scripts/python.exe -m pip check` | No broken requirements found, exit 0. |
| `git diff --check`; `git diff --cached --name-only` | Whitespace check passed; staging area empty. |

Ruff was 0.16.6, mypy 2.3.1 and pip-audit 2.10.1. For the two temporary
static tools, `PYTHONPATH` pointed to
`C:\Users\drews\Development\TopSignal\tmp\audit-20260904\static-tools`.
Raw suite, lint, build and dependency logs are in the local ignored
[`tmp/audit-20260904`](../tmp/audit-20260904/) directory. No Python packaging
build target is defined; Python syntax/dependencies, startup fixtures and the
production supervisor were checked instead. A real production database/backend
boot remains an acceptance gate.

For comparison, the 113 Python files from unchanged HEAD were copied into an
ignored directory and analyzed without executing their application code. That
baseline produced **452 broad Ruff errors** and **317 mypy errors in 15 files
(48 files checked)**. The comparison helper and raw results are preserved in
the audit directory. This establishes existing debt; it does not excuse new
diagnostics or turn either failing final check into a pass.

Final offline soak used two real asynchronous worker runtimes and a temporary
file-backed SQLite database; provider reads and strategy evaluation were fakes.
It recorded 62 evaluations, maximum evaluation concurrency 1, 670 fake provider
reads, 4 injected DB failures, 1 injected provider failure, two owners, **zero
mutations**, zero checked-out connections, zero remaining asynchronous tasks and
zero thread growth. Runtime configuration was accelerated for the fixture.
This is a 60-second lifecycle test, not a multi-session strategy/Practice soak
or a benchmark of long-term memory use with real market data.

The backend offline guard reported zero unexpected external connections.
A dedicated negative regression deliberately attempted a documentation-reserved
address in a child process; the guard blocked it before network I/O and failed
that child run as expected. No audit test sent a real broker order request.

The first post-change frontend run had one obsolete expectation for the old
restart warning (759 passed, 1 failed). The test was updated to require explicit
restart disarming/rearming text; the full rerun passed. Broad Python analysis
failures remain open rather than being represented as green.

### Safety-control regression map

The full suite includes these representative tests in
`backend/tests/test_bot_execution_safety.py`; provider mutations in these tests
are fixture methods, never broker requests.

| Control | Representative evidence |
|---|---|
| Contracts and per-contract position | `test_live_risk_uses_authoritative_provider_position_instead_of_signal_default` |
| Account-wide positions and working exposure | `test_account_wide_gross_limit_includes_working_orders_on_other_contracts` |
| Trades/day | `test_provider_entry_fill_consumes_account_daily_trade_limit` |
| Daily loss and proposed stop risk | `test_live_daily_loss_preflight_uses_every_trade_history_page`; `test_sma_entry_stop_risk_must_fit_remaining_account_loss_budget`; `test_max_daily_loss_is_sticky_and_terminal_for_continuous_run` |
| Trading hours/closures | `test_outside_session_is_transient_for_continuous_run`; `test_exchange_closure_blocks_entries_but_not_verified_full_exit` |
| Loss/rejection cooldown | `test_authoritative_loss_cooldown_blocks_before_local_trade_sync`; `test_rejected_order_cooldown_blocks_until_configured_interval_expires` |
| Stale data and expiring reconciliation | `test_configured_positive_staleness_threshold_controls_order_routing`; `test_authoritative_preflight_expiry_after_database_lock_blocks_routing` |
| Emergency stop/flatten uncertainty | `test_account_emergency_flatten_stops_every_bot_before_client_failure`; `test_unconfirmed_emergency_flatten_leaves_ambiguous_attempt_unresolved`; `test_pending_account_emergency_suppresses_duplicate_provider_mutations` |
| Idempotence/concurrency | `test_repeated_actionable_candle_creates_one_attempt_and_duplicate_skip_audit`; `test_uniqueness_toctou_race_uses_savepoint_and_keeps_session_usable` |
| Ambiguous submission | `test_network_timeout_marks_submission_unknown_for_reconciliation`; `test_malformed_submission_response_remains_unknown` |
| Reconciliation identity | `test_reconciliation_keeps_open_or_pending_provider_order_unresolved`; `test_reconciliation_requires_matching_order_identity` |

These establish mocked control behavior. Actual broker-held protection, fills
during disconnect and PostgreSQL transaction interleavings still need the
acceptance evidence listed above.

### Running missing PostgreSQL acceptance safely

Provision a **disposable** database named `topsignal_audit_<suffix>` and apply
`db/schema.sql` to it. Use credentials restricted to that database; never use
the existing trading URL. With dotenv disabled and both execution flags false:

```powershell
$env:PYTHON_DOTENV_DISABLED = '1'
$env:DATABASE_URL = 'sqlite+pysqlite:///:memory:'
$env:TOPSIGNAL_LIVE_EXECUTION_ENABLED = 'false'
$env:TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION = 'false'
$env:TOPSIGNAL_TEST_POSTGRES_URL = '<disposable postgresql+psycopg URL>'
Set-Location C:\Users\you\Development\TopSignal\backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_postgres_concurrency.py tests/test_postgres_bot_safety.py
```

The offline runner deliberately removes the PostgreSQL URL; use the explicit
acceptance command above only after provisioning the disposable target.
CI now contains a dedicated worker fencing/timeout PostgreSQL lane, but the
modified CI was not pushed or run remotely. Passing it would still not prove
the target laptop's actual database/network failure behavior.

## Second Windows laptop setup

Use the complete [Windows operations runbook](windows-24x7-operations.md). Its
commands are instructions for later execution, not actions performed here.

1. Review this local diff and arrange transfer of the reviewed source; an
   ordinary clone of origin/main does not include these uncommitted fixes.
2. Prepare the personal Windows laptop: encrypted disk, stable power/network,
   synchronized clock, deliberate update/reboot window and no sleep/lid suspend
   while scheduled operation is expected. Record hardware/load evidence.
3. Create a separate non-Git release under
   `C:\ProgramData\TopSignal\releases\<version>` and writable
   `C:\ProgramData\TopSignal\runtime`. Exclude local environments, secrets,
   caches, storage and audit output when copying source.
4. Create the release's Python venv from a service-readable machine-wide Python,
   install pinned requirements, run `npm ci`
   for the frontend, and build using `scripts/build-production-frontend.ps1`.
   Use only publishable Supabase frontend keys and loopback API URL.
5. Supply the backend PostgreSQL and separate migration URL, authentication
   issuer/audience, Fernet key and per-user encrypted provider credentials.
   Keep `TOPSIGNAL_LIVE_EXECUTION_ENABLED=false` and
   `TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION=false`.
6. Back up and restore-test data/key/storage. Apply the explicit migration
   preparation in the runbook; do not blindly baseline/adopt an existing DB.
7. Create the limited `TopSignalSvc` identity; apply and verify the documented
   read/execute release ACLs and exclusive private runtime/secret ACLs.
8. Perform the operator-shell supervisor smoke test, check `/health`,
   `/health/worker?require_enabled=true`, `/ready`, authenticated runtime status,
   log creation and graceful stop. Only start an explicitly dry-run configuration.
   This does not prove the limited task identity; keep its interactive-logon
   restrictions and prove that identity through the scheduled launch next.
9. When those checks pass, use the runbook's
   `install-windows-startup-task.ps1 -RepoRoot ... -RuntimeRoot ... -ServiceUser ...`
   command. Verify startup without login, crash/reboot recovery, persistent STOP,
   one worker and no automatic provider-routing rearm.
10. Configure and fault-test independent alerts, retention and backup schedules.
    Complete the staged evidence gates before considering provider execution.

## Monitoring and emergency stop

Poll loopback `/health`, `/health/worker?require_enabled=true`, and `/ready`
every 10 seconds; alert after three failures. Allow the worker probe its
120-second startup grace. Poll UI port 4173 every 30 seconds and alert after two
failures. Capture safe readiness `reason`/`failed_checks`. Send an external heartbeat and alert after 90 seconds
missing. Check disk/time/process resources every minute: warn below 10 GiB free,
stop/investigate below 5 GiB, investigate clock drift over one second or sustained
resource growth. Alert immediately on `submission_unknown`, worker cancellation,
incomplete shutdown, unexpected restart/disarming and critical account errors.
Expected closed-market inactivity is not permission to ignore a readiness failure.

The authenticated runtime endpoint is `/api/bots/runtime/status`. `/ready` can
return `database_unavailable`, `schema_outdated`, `worker_not_started`, or
`bot_runtime_not_ready`. Provider degradation should alert; it must not trigger
blind repeated restarts. Supervision, monitoring and readiness are separate.

1. Select the exact bot/account in the control UI and click **Stop Automation**.
2. If exposure must be removed, use the separately confirmed **Emergency:
   Flatten Account …** only with a deliberate operator decision and working provider
   access; independently verify zero orders and positions. Timeout/409/unknown
   is not success. If unavailable, use the provider platform/support directly.
3. To stop the local process and prevent restart/reboot recovery, run:

   ```powershell
   powershell -NoProfile -File 'C:\ProgramData\TopSignal\releases\<version>\scripts\stop-production.ps1' -RuntimeRoot 'C:\ProgramData\TopSignal\runtime'
   Test-Path 'C:\ProgramData\TopSignal\runtime\STOP'
   Get-ScheduledTask -TaskName 'TopSignal Personal Device' | Select-Object State
   Get-NetTCPConnection -State Listen -LocalPort 8000,4173 -ErrorAction SilentlyContinue
   ```

   Expect STOP=true, task disabled/stopped and no listeners after shutdown.
   This command does not contact the broker or flatten positions. If the UI is
   unavailable, stop locally first, then inspect exposure through the provider.
4. Preserve audit IDs, timestamps and logs. Do not delete ambiguous attempts or
   clear the latch to force trading. Resolve broker state and the fault first.
5. Resume only via the runbook's explicit latch/task recovery and fresh checks;
   provider-routing rearm is a separate deliberate action.

## Rollout gates

1. **Offline/live-data dry-run:** both routing flags false; several full sessions,
   duplicate-worker/reboot/outage drills, zero broker mutations and stable resources.
2. **Verified Practice:** supervised account verification first, then at least
   five complete trading sessions covering reconnect, loss/cooldown, protective
   orders, rejects/partial fills, ambiguous response and emergency-flat drills.
   No production supervisor or Practice account was armed in this audit.
3. **Supervised live minimum size:** blocked for real-money accounts in this
   implementation and for Topstep LFA ProjectX automation under current rules.
   A permitted broker/account integration requires separate review and explicit
   authorization. If “live” means real API routing on an eligible simulated
   account, keep minimum size and a present operator until Practice evidence passes.
4. **Unattended:** only after all applicable blockers are closed, independent
   alerts and restore/reboot drills pass, account risk controls are verified,
   and an operator signs off on the exact release/hardware/account combination.

Topstep documents personal-device order-origin restrictions and recommends
Practice because there is no separate API sandbox: [API access rules](https://help.topstep.com/en/articles/11187768-topstepx-api-access).
Recheck account terms, contract sessions/holiday calendars, rollover and device
session rules before rollout. This audit does not certify strategy profitability.

## Files changed

The local change set contains 46 modified tracked files and 17 new files (63 total). Generated builds, test caches, and ignored audit logs/tools are excluded.

- `.env.example`
- `.github/workflows/ci.yml`
- `.gitignore`
- `backend/app/bot_schemas.py`
- `backend/app/bot_worker.py`
- `backend/app/database_security.py`
- `backend/app/db.py`
- `backend/app/main.py`
- `backend/app/production_logging.py`
- `backend/app/request_limits.py`
- `backend/app/services/bot_risk.py`
- `backend/app/services/bot_service.py`
- `backend/app/services/credentialed_http.py`
- `backend/app/services/gemini_client.py`
- `backend/app/services/journal_storage.py`
- `backend/app/services/metrics.py`
- `backend/app/services/projectx_client.py`
- `backend/app/services/projectx_credentials.py`
- `backend/app/services/projectx_hubs.py`
- `backend/app/services/projectx_order_book.py`
- `backend/app/services/projectx_streaming_runtime.py`
- `backend/app/services/projectx_trades.py`
- `backend/app/services/streaming_pnl_tracker.py`
- `backend/logging.production.json`
- `backend/tests/test_auth_middleware.py`
- `backend/tests/test_bot_execution_safety.py`
- `backend/tests/test_bot_risk_hardening.py`
- `backend/tests/test_bot_schema_safety.py`
- `backend/tests/test_bot_service.py`
- `backend/tests/test_bot_worker_soak.py`
- `backend/tests/test_bot_worker.py`
- `backend/tests/test_credentialed_http.py`
- `backend/tests/test_database_security.py`
- `backend/tests/test_db_engine_options.py`
- `backend/tests/test_gemini_client.py`
- `backend/tests/test_journal_storage_security.py`
- `backend/tests/test_legacy_metrics_integrity.py`
- `backend/tests/test_migration_runner.py`
- `backend/tests/test_offline_runner.py`
- `backend/tests/test_postgres_bot_safety.py`
- `backend/tests/test_production_operations.py`
- `backend/tests/test_projectx_client.py`
- `backend/tests/test_projectx_hubs.py`
- `backend/tests/test_projectx_order_book.py`
- `backend/tests/test_projectx_trades_sync.py`
- `backend/tests/test_readiness.py`
- `backend/tests/test_runtime_shutdown.py`
- `backend/tests/test_streaming_pnl_tracker.py`
- `backend/tests/test_streaming_runtime_isolation.py`
- `backend/tests/test_upload_request_limits.py`
- `backend/tools/migrate_db.py`
- `backend/tools/run_offline_tests.py`
- `docs/unattended-readiness-audit-2026-09-04.md`
- `docs/windows-24x7-operations.md`
- `frontend/package-lock.json`
- `frontend/src/pages/bot/BotPage.accountState.test.tsx`
- `frontend/src/pages/bot/BotPage.tsx`
- `scripts/install-windows-startup-task.ps1`
- `scripts/production-deployment.test.cjs`
- `scripts/run-production.ps1`
- `scripts/serve-production-backend.py`
- `scripts/serve-production-frontend.py`
- `scripts/stop-production.ps1`
