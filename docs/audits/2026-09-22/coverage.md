# Coverage ledger — TopSignal audit, 2026-09-22

Follow-up fixes and regression results: [fixes.md](fixes.md). The table below preserves baseline audit observations.

Baseline: `df83159`. Status refers only to the stated checks, not every possible behavior.
P = passed observed checks; F = confirmed failure; B = concrete environment/scope blocker; U = not yet exercised.
The complete route registration inventory is in `endpoint-inventory.csv`.

| Feature / controls | Frontend entry | Backend / internal functions | Automated evidence | Manual status |
|---|---|---|---|---|
| Login, logout, session expiry | App.tsx; lib/supabase.ts | auth.py; api_auth_middleware | auth_middleware; audit tenant/auth probes | B: no disposable Supabase identity; connected session left alone |
| Tenant ownership / credential storage | lib/api.ts | auth.py; projectx_credentials.py; owned-account guards | auth_middleware; legacy_trade_user_scope; streaming_runtime_isolation; credentialed_http | B: authenticated browser test environment unavailable |
| Shell, navigation, account query | app/AppShell.tsx; app/routes.tsx | /api/accounts; /api/auth/me | AppShell lifecycle; accountSelection; navigation cache suites | P: six pages visited; HTML account names escaped |
| Empty accounts / provider refresh failure | AccountsPage.tsx | list_projectx_accounts; projectx_accounts.py | accounts route/classification/concurrency suites | P: empty state and explicit missing-credential failure |
| CSV account creation | TradeImportPanel.tsx | create_topstep_live_import_target | topstep_trade_imports; liveCsvDailyFlow | P: negative opening balance rejected; valid account persisted |
| Rename / main / archive / restore | accountManagement.tsx | account mutation routes; serialize_account_main_mutation | accountManagement; projectx_account_concurrency | P: rename, Main, archive-only-Main safeguard, archive/restore and reload persistence |
| File preview / confirm / duplicate | TradeImportPanel.tsx | trade_imports.py; preview/confirm/status handlers | topstep_trade_imports; TradeImportPanel recovery tests | P: 1 fixture row previewed/imported; duplicate refused; reload preserved |
| Malformed / oversized / XLSX import | TradeImportPanel.tsx | trade_imports.py bounded parser and staged manifests | topstep_trade_imports validation, XLSX bounds, DST, concurrency | P: malformed CSV rejected with missing-column list; subsequent valid duplicate recognized; U: manual oversized/XLSX variants |
| Import-derived balance refresh | DashboardPage.tsx | /api/accounts; _load_csv_import_current_balances | AUD-01 manual repro retained | F: old balance anchors new P&L until reload |
| Summary / profit factor | DashboardOverview; summary dialog | projectx_metrics.compute_trade_summary | existing metrics + AUD-02 xfail | F: winning-only PF 0; contradictory Negative edge advice |
| Compact summary / chart | CompactDashboardView; CompactDashboardPerformance | compactDashboardData; account summary/calendar APIs | compact integration and calculations suites | P: PF infinity, daily/cumulative switch, 390px no horizontal overflow |
| Calendar / date filters / copy stats | DashboardPage; PnlCalendarCard | pnl-calendar; trading_day.py | calendar, customDateRange, journal-removal tests | P: presets 1D/1W/1M/6M/All, day selection, nine detail cards, Summary/Escape, Copy Full Stats (3,191 fixture characters); U: custom-range combinations and Copy Month |
| Copy Trade Mode / roster | copyTrade.ts; compactDashboardData | per-account summary/trade reads | copyTrade, copyTradePerformance, compact integration | P: CSV disallows aggregation; B: synthetic ProjectX toggle reaches confirmation, then browser control times out; follower-selection/persistence not verified manually |
| Trade feed / symbol / row limit | TradesPage.tsx | list_projectx_account_trades; execution lifecycles | accountRace; trade serialization/lifecycle/excursion suites | P: fixture trade, INVALID symbol empty result, 1000 cap control; no broker sync for CSV |
| Expenses / payouts / cash flow | ExpensesPage.tsx | expense/payout CRUD, financial-summary | expenses_routes; payouts_routes; audit boundary probes | P: negative amount rejected; 49.99 expense + 100.01 payout = 50.02; F: form only offers two expense categories |
| Financial currencies | ExpensesPage USD dashboard | ExpenseCreateIn; PayoutCreateIn; sums | AUD-06 xfail | B: currency not selectable in UI; API accepts unsupported units |
| Oversized IDs / pagination / dates | API filters | _validate_account_id; financial range helpers | AUD-03/04/05 xfails | F: reproducible HTTP 500; ordinary limits rejected |
| Expense delete / filters / reconciliation | ExpensesPage.tsx | delete_expense; suppressions; combine_expenses.py | ExpensesPage; combine_expenses; expenseReconciliation | P: category Refund yields empty ledger; January filters both ledgers; Clear month restores; backend outage/Refresh recovers values. B: delete confirmation and reconciliation not manually completed before browser-control failure |
| Bot view-only CSV / failure recovery | BotPage; BotAccountGate; ProjectXSignalChart | runtime/status; contracts search; candles | BotPage.accountState; chart request/session suites | P: Dry/Live/Stop disabled; chart attempt fails clearly and offers Retry |
| Dry/live start / stop / emergency flatten | BotPage; ManualOrderPanel | bot_service; manual_order_tests; bot_worker | execution_safety; emergency_flatten; manual_order_tests; topbot_time_exit | B: real broker actions prohibited; tested with recording provider fakes |
| Worker lease / uncertain orders / timed exits | no direct control for internals | bot_worker.py; topbot_time_exit.py | bot execution, time exit, runtime shutdown suites | B: no live provider soak; PostgreSQL lock tests skipped |
| Chart / depth / evaluation / model panels | BotSignalChart; OrderBookPanel; analysis panels | projectx hubs; market context; research services | chart/session/orderBook; market context; depth/model suites | P: Demo 749 bars, all six timeframes, nine overlays, Fit, liquidity compute, line drawing and reload persistence; B: live depth/feed, research refresh; U: other drawing tools and activity disclosures |
| Themes / keyboard / persistence | ThemePage.tsx | local theme state / CSS | theme tests; build/lint | P: all 12 palettes selected; arrow-key navigation; reload persisted Market |
| Demo read-only switching | lib/demoMode.ts | client fixture transport; mutation guard | demo isolation and App tests | P: real fixture replaced with labeled sample accounts; mutations disabled |
| Retired Journal APIs | unrouted JournalPage | journal.py; journal_storage.py | journal routes/conflicts/races/security/AI recap | P: /journal redirects preserving account query; B: UI intentionally removed; retained APIs tested with local fixtures |
| Retired Data/backtests | /data redirect; unrouted backtest panel | bot_backtesting; databento_cache/ingestion | replay/backtesting/storage policy suites | P: /data redirects preserving account query; B: full real-history replay unavailable; no restore or cloud import authorized |
| Startup / readiness / deployment | scripts/dev*.cjs | readiness; DB security; production scripts | full backend; dev-script suite; CI config inspection | P: isolated loopback app starts; B: Windows/PostgreSQL deployment not available |

Final ledger: mixed statuses are intentional and apply to the named subchecks. The audit stopped further manual exploration after repeatable browser-control timeouts at the Copy Trade Mode confirmation. Unfinished controls remain explicit; passing a suite does not establish manual UI or production-provider coverage. No numeric coverage percentage is claimed.

Additional API-only surfaces: market-data inventory/context/public refresh, market events, observations and decision research are in the 88-registration endpoint inventory. Their existing backend suites passed; external refresh and captured-history integration were blocked by the sandbox/network policy. OpenAPI documentation routes are framework-generated and excluded from that inventory.
