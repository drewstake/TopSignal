# TopSignal adversarial audit — 2026-09-22

Follow-up: the findings below record the original audit. See [fixes.md](fixes.md) for the implemented corrections and current verification.

Baseline: `main`, `df83159`. Product files were not changed. This audit retains diagnostic tests, an isolated launcher, measurements and this report under `docs/audits/2026-09-22/`.

## Outcome and priorities

Six confirmed product defects, one failing test contract, and one material UI capability gap were found. Fix the misleading financial displays first: stale balance reconstruction after imports, the winning-only profit factor, and mixed-currency cash-flow totals. No critical exploit, unauthorized trade or cross-tenant disclosure was demonstrated. That is a bounded observation, not a security certification.

| ID | Priority | Finding | Evidence |
|---|---|---|---|
| AUD-01 | P2 | Import refresh leaves balance reconstruction anchored to the old balance | Actual UI + saved-account API |
| AUD-02 | P2 | Winning-only profit factor is zero; Summary recommends action for a nonexistent negative edge | UI, code, strict expected-failure test |
| AUD-06 | P2 | Non-USD financial records accepted and summed into USD totals | Authenticated isolated API tests + SQL/UI inspection |
| AUD-08 | P2, test infrastructure | Frontend suite fails on stale Stop Automation wording | Full frontend suite |
| AUD-04 | P3 | Mixed timezone-aware/naive expense filters return 500 | Authenticated isolated ASGI test + loopback HTTP |
| AUD-03 | P3 | Oversized pagination/account integers return 500 | Authenticated isolated ASGI tests + loopback HTTP |
| AUD-05 | P3 | Valid extreme dates overflow financial-range arithmetic | Authenticated isolated ASGI test + loopback HTTP |
| AUD-07 | Improvement | Add Expense cannot create four supported expense categories | Actual UI + schemas/submit implementation |

P2 means a normal-use correctness or development-reliability issue worth scheduling promptly. P3 is an edge-case validation defect. No result here establishes denial-of-service at scale.

## Environment and safety

Read repository `AGENTS.md`, README and `docs/supabase-storage-policy.md` before testing. The Journal and Data/backtest pages are intentionally retired; their retained APIs are not evidence that the pages should still exist.

Manual work used a separate loopback UI on `127.0.0.1:5185` and API on `127.0.0.1:8015`, with SQLite at `/tmp/topsignal-audit-20260922/audit.sqlite3`. Inherited application secrets were excluded, dotenv loading disabled, broker/streaming/worker/live execution disabled, and outbound Python sockets restricted to loopback. The browser displayed the Offline workspace banner. The user's connected application on port 5173 was not used for audit mutations.

Fixture data: one $10,000 Live CSV account; one repository trade fixture (gross $202.50, fees/commission $3.72, net $198.78); one $49.99 expense; one $100.01 payout; one synthetic non-tradable ProjectX practice account. No live orders or real notifications were sent. No production records, credentials, historical archives, retention settings, migrations, spend caps or billing were changed. Databento filesystem storage and SQLite-only guards were preserved. Tests involving credentials used deliberately fake signing keys and identities.

No installed computer-use SKILL.md was discoverable. The available computer-use tool documentation was read and followed, including file-upload and browser troubleshooting guidance. Browser actions used the actual application UI, not a replacement mock page. Demo Mode was used only where explicitly stated below.

## Architecture and intended behavior

- **Shell and authentication:** React/Vite, lazy routes in `frontend/src/app/routes.tsx`, account selection and query state in `AppShell.tsx`, Supabase authentication and API bearer tokens. Backend authentication middleware binds the tenant; account/credential operations must use that ownership scope. Offline and Demo modes have different intentional restrictions.
- **Six active pages:** Dashboard, Accounts, Trades, Expenses, Bot and Themes. `/journal` and `/data` redirect to Dashboard while retaining the account query. Existing retired components are not discoverable product workflows.
- **Account/import flow:** shell or Dashboard upload → CSV/XLSX preview → staged token confirmation → deduplicated trade persistence → summary/calendar/trade refresh. CSV balances derive from the initial balance plus imported net results. Provider accounts use saved ProjectX metadata and explicit provider refresh.
- **Analytics:** owned-account trade reads → lifecycle normalization → `projectx_metrics` summaries, daily calendar and point-basis metrics → baseline/compact cards, reconstructed balance path, copy stats and textual advice. Copy Trade Mode aggregates analytics; its confirmation explicitly says it does not place orders.
- **Financial ledger:** expense/payout schemas → tenant-scoped CRUD → integer-cent SQL aggregates → monthly calendar, all-time and trailing-period cash flow. Current UI labels these totals USD.
- **Trading controls:** Bot UI → runtime/account eligibility gates → bot configurations and runs → worker lease/heartbeat, provider reconciliation, guarded execution, protective/timed exits. Ordinary Stop differs from account-wide emergency flatten. Manual-order test routes are consequential trading routes despite their names and were not used with a real provider.
- **Background/integrations:** ProjectX REST plus market price/depth streams; bot worker lease/fencing and timed-exit processing; market-data/context refresh, public events and decision research; Supabase identity/database and optional journal image storage/AI recap. These interfaces were inspected and exercised through existing fake-provider/offline suites, not external accounts.
- **Historical/backtest flow:** retained backtest/replay services and results; Databento archives/cache remain local under `TOPSIGNAL_DATABENTO_CACHE_DIR`; the relational ingestion guard requires SQLite. Full input candle arrays must not become cloud result JSON or legacy historical table data.

The endpoint inventory lists **88 application route registrations**, including the three included market routers and the journal recap alias. Framework-generated OpenAPI routes are excluded. See [endpoint-inventory.csv](endpoint-inventory.csv) and the feature-to-control/handler/test mapping in [coverage.md](coverage.md). The inventory is a map, not a claim that every route received a newly authored test.

## Confirmed product defects

### AUD-01 — P2: imported P&L refreshes before the account balance

**Impact:** the estimated balance path displays incorrect start/high/low values immediately after a successful import. It can mislead assessment of account progression even though the imported trade and server balance are correct.

**Reproduce:** in the isolated workspace, create a Live CSV account with opening balance $10,000. Import `backend/tests/fixtures/topstep_trade_export_utf8.csv`, preview and confirm its one trade. Before reloading, inspect Dashboard's Estimated Balance Path and `GET /api/accounts?refresh_provider=false`. Then reload the page.

**Expected:** new net P&L +$198.78 and current balance $10,198.78 produce start/low $10,000 and high $10,198.78 immediately.

**Actual:** immediately after import the path showed start/low **$9,801.22**, high **$10,000**, while the saved-account API already reported **$10,198.78**. Reload corrected the path to start $10,000/high $10,198.78. The trade remained a single persisted record; re-upload was correctly detected as a duplicate.

**Cause/source:** `frontend/src/pages/dashboard/DashboardPage.tsx:1387` reloads summary, calendar and trade datasets, but not shell accounts. Its import callback is wired at line 3500; the balance anchor reads `selectedAccount.balance` at line 2402. `frontend/src/lib/api.ts:1257` invalidates read caches but does not update the mounted shell's account state.

**Fix:** refresh/update the account in the same completion flow, or return and apply the authoritative post-import balance. Gate the reconstructed chart until its P&L and account balance belong to the same refresh. Add an integration regression that checks the path without a page reload, including an account switch while confirmation is pending.

### AUD-02 — P2: no-loss profit factor becomes zero and misleading advice

**Impact:** baseline Dashboard and Trades portray an all-winning sample as having a zero profit factor. Summary then labels the sample “Negative edge” and says to cut size until expectancy turns positive even though the displayed expectancy is positive. Compact mode disagrees with baseline mode.

**Reproduce:** import the same single winning fixture; inspect baseline Profit factor, open Summary, then enable Compact Dashboard.

**Expected:** a no-loss sample has an explicit no-loss/undefined/unbounded state. It must not be treated as evidence of negative edge. Empty, break-even-only and losing-only samples need distinct semantics.

**Actual:** baseline PF **0.00**, net **+$198.78**, win rate **100%**, expectancy **+$198.78/trade**. Summary's negative-edge advice conflicts with those values. Compact mode displays **∞ / No losing trades** for the same sample.

**Cause/source:** `backend/app/services/projectx_metrics.py:152` returns 0 whenever gross loss is zero. `frontend/src/components/dashboard/CopyFullStatsButton.tsx:829` treats any finite PF below 1 as negative edge; `frontend/src/pages/dashboard/components/DashboardOverview.tsx:21` formats the number. Diagnostic `test_winning_only_profit_factor_is_not_zero` is a strict xfail.

**Fix:** define a JSON-safe ratio state (for example nullable ratio plus a no-loss flag), apply it consistently across cards, summaries and exports, and avoid using unavailable ratios as negative evidence. Do not emit raw Infinity in JSON. Preserve low-sample warnings without inventing negative expectancy.

### AUD-06 — P2: currencies are accepted but not separated in totals

**Impact:** API clients can create valid-looking non-USD expenses/payouts that silently distort USD cash-flow reporting. UI users cannot see or select a conversion policy.

**Reproduce:** authenticated local API: POST an expense dated 2026-09-21 with `amount_cents:10000,category:"other",currency:"USD"`; POST another dated 2026-09-22 with the same amount/category and `currency:"EUR"`. Both return 201. GET `/api/expenses/totals?range=all_time`. The retained diagnostic verifies `total_amount_cents == 20000`. Using distinct dates avoids the expense deduplication key. EUR payout creation also returns 201.

**Expected:** for a USD-only product, reject unsupported units; otherwise group by currency or perform an explicit auditable conversion before aggregation.

**Actual:** arbitrary currencies are stored, then cents are pooled without currency grouping. Expenses UI labels all recorded activity USD and uses dollar formatting. A $100 expense and €100 expense become a purported $200 total.

**Cause/source:** `backend/app/expense_schemas.py:44`, `backend/app/payout_schemas.py:29` accept unrestricted currency strings; `backend/app/main.py:1213` groups expense totals by category and line 1263 groups financial-summary rows by date/category, not currency. Payout daily aggregation at line 1270 likewise omits currency.

**Fix:** enforce the supported currency in create schemas and validate legacy records before summing, or implement currency-aware reporting end to end. Do not silently relabel or bulk-convert existing records. Two strict xfails and one explicitly temporary current-behavior diagnostic retain evidence.

### AUD-04 — P3: mixed timezone filters crash expense totals

**Reproduce:** GET `/api/expenses/totals?range=all_time&start_created_at=2026-01-01T00:00:00&end_created_at=2026-01-02T00:00:00Z` with fixture authentication (or audit offline backend).

**Expected:** normalize both timestamps consistently or return a clear 4xx requiring offsets. **Actual:** HTTP 500 due to comparing offset-naive and offset-aware datetimes. Loopback reproduction completed in roughly 2.3 ms; this is not a timeout.

**Cause/source:** `backend/app/main.py:1196` directly compares values before `_as_utc` is applied to SQL filters. A normalized helper already exists at line 4939.

**Fix:** reuse normalized range validation or require timezone-aware inputs, then use the same normalized values for validation and querying. Retained strict xfail covers the mixed pair; add inverse-offset, DST and equal-instant cases when fixing.

### AUD-03 — P3: oversized query integers reach the database

**Reproduce:** GET `/api/expenses?offset=1267650600228229401496703205376`, then GET `/api/expenses?account_id=1267650600228229401496703205376` with fixture authentication.

**Expected:** bounded validation returns 400/422. **Actual:** both return HTTP 500 when Python's unbounded integer reaches SQLite integer binding. Ordinary negative offsets, excessive page size and reversed dates correctly return 4xx.

**Cause/source:** `backend/app/main.py:1129` constrains offset only from below; `_validate_account_id` at line 4922 and `_validate_pagination` at line 4932 lack upper bounds. Confirmed on SQLite; PostgreSQL behavior for this exact input was not measured.

**Fix:** constrain IDs to their supported database domain and offsets to a documented maximum (or use cursor pagination). Apply the same contract consistently to related routes. Two strict xfails retain the exact cases.

### AUD-05 — P3: extreme valid dates overflow range helpers

**Reproduce:** GET `/api/expenses/financial-summary?as_of_date=0001-01-01` with fixture authentication.

**Expected:** supported date domain enforced with 4xx, or safe arithmetic. **Actual:** HTTP 500; subtracting trailing months from year 1 underflows. Normal current-date summaries pass.

**Cause/source:** `backend/app/main.py:4861` builds range starts using `_subtract_local_months` without a supported lower bound. A strict xfail retains this case. Additional loopback probes also returned 500 for a summary start in year 1 and a P&L-calendar end in year 9999; those probes are supplementary, not separate thoroughly traced findings.

**Fix:** define and validate a supported financial date domain before arithmetic, including lookback/bucket padding, and return a stable 4xx for unsupported boundaries. Check both low and high endpoints, including leap days.

## Test infrastructure finding

### AUD-08 — P2: one frontend test asserts obsolete Stop wording

**Reproduce:** `backend/.venv/bin/python docs/audits/2026-09-22/run_isolated.py frontend-tests-compatible`.

**Actual:** 963 tests pass and one fails at `frontend/src/pages/bot/BotPage.accountState.test.tsx:691`, expecting `/does not cancel broker orders or close positions/i`.

**Expected:** the test verifies ordinary Stop invokes only the stop path, while respecting the current timed-exit behavior.

**Cause/source:** `frontend/src/pages/bot/BotPage.tsx:1103` explains that Stop prevents new entries without immediately flattening, while protective orders and pending timed exits remain active. The old wording assertion fails before its subsequent stop-versus-emergency call assertions run.

**Fix:** update the wording expectation to the intended protective/timed-exit contract and retain the behavioral checks (`stop` called; account emergency flatten not called). Do not restore the old unconditional wording just to satisfy the test. Product behavior was not changed during the audit.

## Usability / workflow improvements

### AUD-07: supported expense categories cannot be created through Add Expense

**Observed:** filters and API support Evaluation Fee, Activation Fee, Reset Fee, Data Fee, Other and Refund. Add Expense only allows Evaluation/Activation for a standard account and forces Evaluation for other account types. There is no path in that dialog to record an operational data fee or refund accurately.

**Reproduce:** Expenses → Add Expense → inspect Stage for standard and non-standard account types; compare with the Category ledger filter. Sources: `frontend/src/pages/expenses/ExpensesPage.tsx:1316`, submission around line 699, and `backend/app/expense_schemas.py:10`.

**Benefit/fix:** separate generic expense entry from combine lifecycle presets; expose the supported categories with appropriate amount/refund rules. This is a confirmed UI/API capability gap; prioritization depends on whether generic operational expenses remain an intended user workflow. Avoid forcing users to misclassify records.

**Connection errors:** with the isolated backend stopped, Expenses displays “Failed to fetch” in several panels. Refresh correctly restores values after restart. An actionable message explaining service availability and offering one retry entry point would reduce confusion; this is an improvement, not a claim of data loss. During the observed error snapshot, range cards also retained “Loading…” labels; prolonged loading was not established.

**Developer runtime:** Node 25.9.0's experimental WebStorage caused widespread localStorage test failures under the default frontend invocation. `NODE_OPTIONS=--no-experimental-webstorage` reduced this to the single AUD-08 failure. Pin a supported Node/test environment or explicitly install a test storage adapter. The original 139 failures/10 unhandled errors were environment compatibility noise, not 139 application bugs.

## Performance measurements and optimization candidates

[benchmark_metrics.py](benchmark_metrics.py) constructs synthetic in-memory trades and times `compute_trade_summary` plus four point-basis payoffs, three repetitions each. No historical files or cloud records are imported.

| Rows | Median CPU time |
|---:|---:|
| 1,000 | 6.48 ms |
| 10,000 | 65.36 ms |
| 100,000 | 667.58 ms |

[Raw measurements](benchmark-results.json) show approximately linear growth. At 100k rows the computation alone costs about two-thirds of a second; this excludes database retrieval, serialization, network and rendering. No quadratic regression or request latency SLA breach is demonstrated. Before optimizing, profile an isolated large-account request end to end and count repeated summary/point-basis passes. Reusing compatible intermediate aggregates could reduce CPU if that path is frequent; cache invalidation must preserve tenant, account, date and import-version boundaries. Do not introduce unbounded caches or persist historical input arrays to Supabase.

Frontend production build passed (4.76 s in this run). The largest reported main bundle was about 371 kB uncompressed / 119 kB gzip; BotPage about 364/113 kB and Dashboard about 222/58 kB. Pages are lazy-loaded. These sizes alone do not justify a rewrite; actual slow-device/render and network waterfall profiling remains unperformed.

## Suspected issues and non-findings

No unconfirmed security suspicion is promoted to a finding. A briefly observed prior-account chart during account-switch loading was not established as persistent stale state; this remains a targeted slow-network follow-up. Existing account-race tests passed. Date-filter interactions with rapidly changing snapshots were also not counted as a bug without a stable reproduction.

Malformed CSV rejection, duplicate import handling, account name HTML escaping, cross-tenant expense access denial, missing/expired bearer rejection, negative non-refund expense rejection, theme persistence and backend reconnection passed the stated checks. These observations do not establish complete protection against all input/security classes.

## Test results and coverage limits

See [test-results.md](test-results.md), [manual-reproductions.md](manual-reproductions.md), and [coverage.md](coverage.md).

- Existing backend: **2,111 passed, 9 skipped**. Eight skips require disposable PostgreSQL; one requires an optional local market capture. No external socket attempt was made in the guarded run.
- Existing frontend, compatible environment: **963 passed, 1 failed** (AUD-08).
- New audit tests: **11 passed, 7 strict xfails**. The xfails document defects, not passing behavior. One passing diagnostic intentionally documents the bad mixed-currency aggregation and should be removed/replaced when fixed.
- Frontend build and lint: **passed**.
- Dev script tests: **35 passed, 5 Windows-only skipped**.

All six active pages were visited. Actual UI checks covered fixture imports, duplicate and malformed files, account lifecycle, analytics/calendar, trade filtering, financial creation/filter/reconnect, themes, responsive layout, and Demo chart interactions. This is not exhaustive control/state coverage. Browser control repeatedly timed out after the Copy Trade Mode confirmation; its acceptance and resulting state could not be verified, so remaining manual controls were marked blocked/untested. Viewport override was reset; closing the blocked audit tab also timed out.

Further progress requires a working browser-control session for the remaining listed controls; a disposable Supabase identity for real login/logout/session-expiry UI; a disposable PostgreSQL database for lock/fencing acceptance; a safe broker simulator/feed for live-stream/order lifecycles; and optional local historical fixtures for full replay acceptance. Real credentials or production data are not substitutes for those environments. Concurrency fakes and SQLite results do not prove PostgreSQL locking, multi-process worker behavior or broker outcomes.

No product fixes, commits or pushes were made in this audit. The earlier request to commit work preceded this explicitly audit-only request; findings remain available for a follow-up implementation.
