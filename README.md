# TopSignal

TopSignal brings ProjectX accounts and Topstep trade exports into one futures trading workspace. Review trading performance, track costs and payouts, and follow TopBot’s Micro E-mini Nasdaq-100 (MNQ) practice trades.

[Run locally](#local-quick-start) · [Find the code](#where-to-find-things) · [Explore the features](#feature-tour)

![TopSignal demo dashboard showing net P&L, win rate, profit factor, drawdown and the estimated balance path](docs/screenshots/metrics-2026-11-18.png)

*Captured November 18, 2026. The account screens use local demo data. The TopBot chart and order book show MNQ market data from peteamdpc.*

## Local quick start

Use a fresh checkout, Python 3.11 or 3.12, and Node.js 24 with npm. Run these commands from the repository root in PowerShell.

```powershell
python -m venv backend/.venv
.\backend\.venv\Scripts\python.exe -m pip install -r backend/requirements-test.txt
npm ci
npm --prefix frontend ci
npm run dev:offline
```

On macOS or Linux, create the environment with `python3` and use `backend/.venv/bin/python` for the pip command. The test requirements include the runtime packages. Each npm install uses its own lockfile.

Open **http://127.0.0.1:5174** once the launcher reports that the backend is ready. It starts Vite and FastAPI and, on first use, creates an empty SQLite database at `backend/storage/offline/topsignal.sqlite3`. A fresh checkout works offline without environment files, PostgreSQL, broker credentials, market data or access to peteamdpc.

Try **Demo mode** for sample data, or create a **Live Account (CSV)** and preview a trade export. Press Ctrl+C in the launcher terminal to stop both servers. [Offline development guide](docs/offline-development.md)

<a id="frontend-routes"></a>

## Where to find things

| Feature or area | Code entry points |
| --- | --- |
| Navigation and API requests | [Frontend guide](frontend/README.md) · [Router](frontend/src/app/routes.tsx) · [App shell and account picker](frontend/src/app/AppShell.tsx) · [Browser API client](frontend/src/lib/api.ts) · [FastAPI handlers and routers](backend/app/main.py) |
| Accounts (`/accounts`) | [Accounts page](frontend/src/pages/accounts/AccountsPage.tsx) · [ProjectX account discovery](backend/app/services/projectx_accounts.py) |
| Dashboard (`/` and `/dashboard`) | [Dashboard page](frontend/src/pages/dashboard/DashboardPage.tsx) · [Account metrics](backend/app/services/projectx_metrics.py) · [Risk forecast](backend/app/services/account_risk_forecast.py) |
| Trades (`/trades`) and Dashboard import | [Dashboard import panel](frontend/src/pages/dashboard/components/TradeImportPanel.tsx) · [File validation and import](backend/app/services/trade_imports.py) · [ProjectX trade ingestion](backend/app/services/projectx_trades.py) · [Trades page](frontend/src/pages/trades/TradesPage.tsx) |
| Expenses (`/expenses`) | [Expenses and payouts](frontend/src/pages/expenses/ExpensesPage.tsx) · [Inferred combine fees](backend/app/services/combine_expenses.py) |
| Appearance (`/appearance`) | [Theme and zoom controls](frontend/src/pages/themes/ThemePage.tsx) · [Palette definitions](frontend/src/lib/theme.ts) |
| TopBot (`/bot`) and Logs (`/bot?view=logs`) | [Bot page](frontend/src/pages/bot/BotPage.tsx) · [Practice controls](frontend/src/pages/bot/TopBotStrategyPanel.tsx) · [Account preset](backend/app/services/topbot.py) · [Depth signal](backend/app/services/topbot_strategy.py) · [Position management](backend/app/services/topbot_runner.py) |
| Bot configuration and execution | [Strategy registry](backend/app/services/bot_strategy_registry.py) · [API schemas](backend/app/bot_schemas.py) · [Bot service](backend/app/services/bot_service.py) · [Worker and lease handling](backend/app/bot_worker.py) |
| Session order flow, imbalance study and recording | [Session volume](backend/app/services/session_order_flow.py) · [Imbalance study](backend/app/services/imbalance_study.py) · [Standalone depth recorder](backend/tools/record_market_depth.py) · [Account trade-progress recorder](backend/app/services/trade_progress_recorder.py) |
| Forward results (`/bot-results/:botId`) | [Bot results page](frontend/src/pages/bot/BotForwardResultsPage.tsx) · [Forward observations and simulated accounts](backend/app/services/forward_results.py) |
| Recorders (`/recorders`) | [Trade paths and recorder status](frontend/src/pages/recorders/RecordersPage.tsx) · [Recorded signal replay](backend/app/services/recorded_signals.py) |
| Database and host | [Data model](db/data-model.md) · [Schema](db/schema.sql) · [Migrations](db/migrations) · [Migration runner](backend/tools/migrate_db.py) · [Release and task scripts](scripts) · [Operator tools](backend/tools) |

Shortcuts preserve query parameters. `/data` opens Dashboard, `/themes` opens Appearance, `/logs` opens the Bot Logs view and `/backtesting` opens Recorders.

## Feature tour

For account states, dashboard controls and examples of how the app syncs trades, see the [account workspace walkthrough](docs/account-workspace.md).

### Choose the account to review

In **Accounts**, refresh ProjectX discovery, compare balances and last trade times, and include hidden or missing accounts when looking for older activity. Set a main account, give an account a local display name, or switch the active account used across the workspace. For manually imported trades, create a separate Live CSV account from the Dashboard with a name and opening balance. [Account management](docs/account-workspace.md#accounts)

![Trading Accounts table showing four demo accounts, their balances and status, and the designated main account](docs/screenshots/accounts-2026-11-18.png)

### Read account performance

On the **Dashboard**, choose a date range to compare net profit and loss (P&L), win rate, drawdown, and long versus short performance. **Copy Trade Mode** combines leader and follower results for analytics only. Open **Account Risk Forecast** to see how often simulated paths reach a loss limit under the rules and starting balance you enter. [Risk forecast settings and method](docs/account-risk-forecast.md)

### Review a trading day

Select a day on the Dashboard’s P&L calendar to filter the recent trade feed to that futures session.

![July P&L calendar with daily results, weekly totals and July 24 selected](docs/screenshots/pnl-calendar-2026-11-18.png)

Follow the **Estimated Balance Path** to see how daily gains and losses accumulated across the selected range.

![Estimated Balance Path reconstructed from the demo account’s daily net P&L](docs/screenshots/daily-account-bal-2026-11-18.png)

With a day selected, the **Trade Events** table shows the entries, exits, direction, size and P&L behind that day’s result.

![Trade Events filtered to July 24, showing one losing MES trade and one winning MNQ trade](docs/screenshots/recent-trade-events-2026-11-18.png)

### Import trades and inspect executions

On the Dashboard, preview a Topstep CSV or XLSX export for a Live CSV account, check gross P&L, fees, commissions and duplicates, then confirm to add the new trades. In **Trades**, filter by date and symbol, sync a ProjectX account’s selected window, or open a trade to review its recorded path where coverage is available. [Topstep import rules](docs/topstep-live-trade-imports.md) · [Trade-review workflow](docs/trade-review-workflow.md)

![Trades Desk showing date and symbol filters, performance metrics and the execution feed](docs/screenshots/trades-2026-11-18.png)

### Track expenses and payouts

Use **Expenses** to log evaluation fees, resets, data costs and refunds, and to record payouts separately. Compare payouts minus spend across time periods, or use the combine spend tracker to reconcile inferred evaluation purchases with logged expenses. [Expenses and payouts walkthrough](docs/account-workspace.md#expenses-and-payouts)

![Expenses and Payouts showing recorded spend, payouts, net cash flow and monthly totals](docs/screenshots/expenses-2026-11-18.png)

### Set the workspace theme and zoom

In **Appearance**, preview a palette and adjust the scale of the whole workspace. Preferences persist in this browser and apply across screens. [Appearance settings](docs/account-workspace.md#appearance)

![Appearance page with the app zoom control and theme swatches, with Midnight selected](docs/screenshots/themes-2026-11-18.png)

### Run and monitor TopBot

In **Bot**, follow the MNQ chart, order book and strategy decisions for the selected account. By default, TopBot’s `dom_contrarian_v1` preset looks for a 10% imbalance in resting bids and asks sustained for 30 seconds, then trades against the heavier side if risk checks pass. On an eligible practice account, you can start the bot, select fixed or trailing exits, or choose **Stop new entries** to leave protection active on an open position. [Practice strategy](docs/topbot-contrarian.md) · [Exit settings](docs/topbot-fixed-session-exits.md)

![TopBot’s five-minute MNQ candlestick and volume chart beside the order book of resting bids and asks](docs/screenshots/bot-2026-11-18.png)

The **Logs** view shows TopBot diagnostics, host restarts and market-feed interruptions. Filter events by severity or search for a message when investigating a gap in activity. [Bot diagnostics](docs/bot-diagnostics.md) · [Production logs](docs/ssh-production-logs.md)

### Read session order flow

The **Session order flow** panel in Bot counts contracts from recorded market trades labeled buy or sell by ProjectX. Compare their difference (delta) with the observed price move over 5, 15 or 60 minutes, and check the coverage indicator before interpreting a partial session. [Session order-flow method](docs/session-order-flow.md) · [Recovery runbook](docs/session-order-flow-recovery.md)

### Study what happens after an imbalance

In **Recorders**, select a capture date and one or more recorded worker signals. Compare two stop and target pairs on the same entries, with modeled fees included. The **Imbalance signal study** also shows subsequent price moves at 30 seconds, 2 minutes and 5 minutes, matched controls, skipped signals and linked practice fills. [Imbalance study](docs/topbot-imbalance-study.md) · [Worker evidence](docs/topbot-worker-evidence.md) · [Exit study](docs/topbot-exit-study.md)

### Review independent recordings

The host’s standalone depth recorder captures MNQ order-book updates, quotes and market trades for the **Liquidity heatmap** and **Live & recorded liquidity** panels in **Recorders**. Use **Liquidity before the move** to inspect the book around a selected moment. A separate API-managed recorder samples open positions on enrolled ProjectX accounts about once a second, including manual trades. Both collect independently of bot arming, account selection, the browser and the viewing PC. Open **Recorders** to check capture status and review trade paths. [Recorded liquidity](docs/order-book-history.md) · [Depth recording](docs/topbot-depth-recording.md) · [Trade-progress recording](docs/trade-progress-recording.md)

### Review forward simulations

Open **Bot results** at `/bot-results/:botId` for a bot you own to see how its recorded dry-run decisions played out as new candles arrived. The report shows simulated positions, closed trades, net P&L and rejection reasons, alongside forecast outcomes. [Paper simulation timing and eligibility](docs/topbot-paper-v2.md)

The retired **Simulation** screen let you place trades with simulated funds in a browser-generated market. Its former `/simulation` address now returns to `/`.

## Development profiles

| Root command | Services and storage |
| --- | --- |
| `npm run dev:offline` | Disconnected SQLite workspace on 5174. Broker access, worker and both live-order flags are disabled by the launcher. |
| `npm run dev:local` | The same SQLite workspace and port with ProjectX access and a dry-run worker. Provider reads need valid credentials. Both live-order flags stay off. |
| `npm run dev:cloud` | Historical command name for the configured account workspace on `http://localhost:5173`. It applies pending migrations to its configured PostgreSQL database before startup. Point it at a dedicated local development database. |
| `npm run dev` | Starts the connected SQLite profile and the configured PostgreSQL profile. Each frontend waits for its own API, on available loopback ports starting at 8000. |

Use one 5174 profile at a time. Restart the launcher after editing `backend/.env` or `frontend/.env.local`. `npm run dev:live` and `npm run dev:local:live` enable the two order-routing flags for the connected SQLite workspace. [Launcher definitions](package.json) · [Profile settings](scripts/offline-env.cjs)

**PostgreSQL development.** For schema work, the [Compose service](docker-compose.yml) runs PostgreSQL 16 on loopback port 5432. In a fresh development checkout with an empty local Compose volume, use these development-only settings in `backend/.env`.

```dotenv
DATABASE_URL=postgresql+psycopg://topsignal:topsignal_password@127.0.0.1:5432/topsignal
AUTH_REQUIRED=false
ALLOWED_ORIGINS=http://localhost:5173
ALLOWED_ORIGIN_REGEX=
TOPSIGNAL_DB_SCHEMA_INIT=skip
TOPSIGNAL_BOT_WORKER_ENABLED=false
TOPSIGNAL_LIVE_EXECUTION_ENABLED=false
TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION=false
```

Start the database with `docker compose up -d db`. Follow the [fresh database procedure](db/README.md#fresh-database) to apply `schema.sql`, baseline the empty schema and check the ledger. Then run `npm run dev:cloud`. The launcher supplies `VITE_API_BASE_URL`. Anonymous access is restricted to a fully local development runtime. To develop authenticated flows, use a separately provisioned development owner and local auth store. The [environment reference](.env.example) includes **host** database, cache and auth paths. Keep private keys and host settings out of development profiles. [Local sign-in and provisioning](docs/local-sign-in.md)

## Hosted operation and recovery

On **peteamdpc**, a Windows task serves the built frontend and supervises the FastAPI backend and bot worker. The independent depth recorder, Discord reader and execution backup helper also run on the host. The API and depth recorder run at scheduler priority 4, above optional derivation and backup work. Open the [practice dashboard](http://127.0.0.1:4173/bot?account=29593203) through the private viewing-PC connection. [Hosted access](docs/hosted-dashboard-connection.md) · [Windows operations](docs/windows-24x7-operations.md) · [Release procedure](docs/releasing.md)

**Local PostgreSQL on peteamdpc is the sole execution authority.** It owns accounts, trades, encrypted provider credentials, bot state and saved results. The runtime verifies the database’s authority ID and write protections so only that instance can grant the worker lease and authorize execution. Trusted-device sign-in uses a separate protected local SQLite store. [Authority checks](backend/app/execution_storage.py) · [Local storage and restore](docs/local-first-storage.md)

Original Databento archives, Parquet candles, memory-mapped replay arrays, raw depth, worker evidence and trade-progress recordings live under `TOPSIGNAL_DATABENTO_CACHE_DIR`. The host uses `D:/TopSignal/data/databento` and development defaults to `backend/storage/databento`. Keep original sources with the cache and verify its off-device backup separately from the PostgreSQL and auth backups. [Cache builder](backend/tools/build_databento_cache.py) · [Storage policy](docs/supabase-storage-policy.md)

The November 13 maintenance retired the Supabase mirror and archived Journal. Cloud records and encrypted archives remain recovery material. Consult the receipt for outstanding full-cache restore and reboot checks, and the recovery procedure for the release 87 rollback restriction. [Maintenance receipt](docs/audits/2026-11-13/storage-maintenance.md) · [Retirement and recovery](docs/supabase-retirement.md)

## Checks

Run from the repository root after the quick-start installs. The backend runner disables dotenv, selects in-memory SQLite and allocates temporary test fixtures.

```powershell
.\backend\.venv\Scripts\python.exe backend/tools/run_offline_tests.py
npm --prefix frontend test
npm --prefix frontend run lint -- --max-warnings=0
npm --prefix frontend run build
$env:Path = (Resolve-Path backend/.venv/Scripts).Path + ";" + $env:Path
npm run test:dev-scripts
npm audit
node scripts/audit-frontend.cjs
```

For the four-worker backend run, pass `-q -n 4 --dist=loadfile` to the Python runner. The PowerShell `PATH` line selects the test interpreter for the script regressions. On macOS or Linux, activate `backend/.venv` in your shell instead. The database CI job creates disposable PostgreSQL 16 databases for fresh schema, upgrades, privilege checks, populated adoption, concurrency and worker fencing. Its SQL deliberately changes test data and roles. Run it only against an isolated cluster. [CI commands](.github/workflows/ci.yml)
