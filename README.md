# TopSignal

TopSignal is a personal trading workspace for reviewing ProjectX accounts, imported Topstep trades, expenses and payouts. Its React dashboard talks to a FastAPI backend that also runs TopBot, the MNQ practice bot. Historical backtesting and forward research are available alongside account review.

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

Open **http://127.0.0.1:5174** after the launcher reports that the backend is ready. The workspace starts empty. Use Demo mode to explore sample data or create a Live CSV account and preview a trade export. Ctrl+C stops both servers. This profile runs Vite, FastAPI and a checkout-local SQLite database at `backend/storage/offline/topsignal.sqlite3`. It needs no environment files, PostgreSQL service, broker credentials, market-data download or connection to peteamdpc. [Offline development](docs/offline-development.md)

## Where to find things

| I’m working on… | Start here |
| --- | --- |
| A screen or navigation item | [Router](frontend/src/app/routes.tsx) → [app shell](frontend/src/app/AppShell.tsx) → [page components](frontend/src/pages). The [frontend guide](frontend/README.md) maps the shared client and UI code. |
| An API request | [Browser API client](frontend/src/lib/api.ts) → [FastAPI handlers and router registration](backend/app/main.py) → [services](backend/app/services). Market data has its own [router](backend/app/market_data_routes.py). |
| Account analytics or trade import | [Dashboard](frontend/src/pages/dashboard/DashboardPage.tsx), [trade ingestion](backend/app/services/projectx_trades.py), [metrics](backend/app/services/projectx_metrics.py), [file imports](backend/app/services/trade_imports.py). [Walkthrough and screenshots](docs/account-workspace.md) |
| Bot configuration and lifecycle | [API schemas](backend/app/bot_schemas.py), [persisted models](backend/app/models.py), [bot service](backend/app/services/bot_service.py) and [leased worker](backend/app/bot_worker.py). |
| Strategy selection | [Strategy registry](backend/app/services/bot_strategy_registry.py) declares identifiers, evaluator dispatch, timeframes, history and replay support. Some entries serve legacy configurations and research fixtures. |
| TopBot practice behavior | [Account preset](backend/app/services/topbot.py), [contrarian depth signal](backend/app/services/topbot_strategy.py), [position management](backend/app/services/topbot_runner.py) and [strategy panel](frontend/src/pages/bot/TopBotStrategyPanel.tsx). |
| Historical backtests | [Backtesting UI](frontend/src/pages/backtesting), [historical jobs](backend/app/services/topbot_backtest_jobs.py), [saved-model replay](backend/app/services/topbot_backtest.py) and [bot-configuration backtests](backend/app/services/bot_backtesting.py). |
| Market depth or trade recording | [Standalone depth recorder](backend/tools/record_market_depth.py), [depth segments](backend/tools/depth_recording.py), [API-managed account recorder](backend/app/services/trade_progress_recorder.py) and [trade-progress store](backend/app/services/trade_progress_store.py). |
| Database schema or migrations | [Data model](db/data-model.md), [fresh schema](db/schema.sql), [incremental SQL](db/migrations), [checksummed runner](backend/tools/migrate_db.py) and [setup procedure](db/README.md). |
| Host operations | [Windows task installers and release scripts](scripts), [operator tools](backend/tools), [Windows operations](docs/windows-24x7-operations.md) and [release procedure](docs/releasing.md). |
| Storage or recovery | [Storage policy](docs/supabase-storage-policy.md), [local storage and restore](docs/local-first-storage.md), [Journal and mirror recovery](docs/supabase-retirement.md) and [dated maintenance receipt](docs/audits/2026-11-13/storage-maintenance.md). |

## Frontend routes

| Address | Screen |
| --- | --- |
| `/`, `/dashboard` | [Dashboard](frontend/src/pages/dashboard/DashboardPage.tsx) — P&L, risk forecast, trading-day calendar, trade imports and analytics-only Copy Trade Mode |
| `/accounts` | [Accounts](frontend/src/pages/accounts/AccountsPage.tsx) — account selection, discovery, visibility and display names |
| `/trades` | [Trades](frontend/src/pages/trades/TradesPage.tsx) — executions, filters and recorded trade progress |
| `/expenses` | [Expenses](frontend/src/pages/expenses/ExpensesPage.tsx) — costs, refunds and payouts |
| `/bot` | [Bot](frontend/src/pages/bot/BotPage.tsx) — practice controls, order book, liquidity history, strategy activity and Logs (`?view=logs`) |
| `/bot-results/:botId` | [Bot results](frontend/src/pages/bot/BotForwardResultsPage.tsx) — owner-scoped forward research and simulated performance outside the main navigation |
| `/backtesting` | [Backtesting](frontend/src/pages/backtesting/BacktestingPage.tsx) — historical MNQ chart and TopBot model replay |
| `/appearance` | [Appearance](frontend/src/pages/themes/ThemePage.tsx) — browser zoom and theme preferences |

The router redirects `/logs` to `/bot?view=logs`, `/themes` to `/appearance` and `/data` to `/dashboard`, carrying the query parameters across. The standalone Simulation page was retired. Its former `/simulation` address takes the catch-all redirect to `/`. Journal was removed and archived.

## Development profiles

| Root command | Services and storage |
| --- | --- |
| `npm run dev:offline` | Disconnected SQLite workspace on 5174. Broker access, worker and both live-order flags are disabled by the launcher. |
| `npm run dev:local` | The same SQLite workspace and port with ProjectX access and a dry-run worker. Provider reads need valid credentials. Both live-order flags stay off. |
| `npm run dev:cloud` | Historical command name for the configured account workspace on `http://localhost:5173`. It applies pending migrations to its configured PostgreSQL database before startup. Use a separate development database. |
| `npm run dev` | Starts the connected SQLite profile and the configured PostgreSQL profile. Each frontend waits for its own API, on available loopback ports starting at 8000. |

Use one 5174 profile at a time. Restart the launcher after editing `backend/.env` or `frontend/.env.local`. The `dev:live` and `dev:local:live` variants explicitly change the connected workspace’s order-routing gates. [Launcher definitions](package.json) · [Profile settings](scripts/offline-env.cjs)

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

## Trading, replay and recording

- **TopBot** uses the `dom_contrarian_v1` practice preset. By default a 10% imbalance sustained for 30 seconds across up to 100 observed levels per side qualifies a contrarian entry. The default 25-point stop trails after a 25-point favorable move. An account can select fixed exits. The worker enforces account, risk and execution gates. **Stop new entries** leaves protection and position management active. [Strategy](docs/topbot-contrarian.md) · [Fixed exits](docs/topbot-fixed-session-exits.md)
- **Backtesting** reads installed Databento history from Parquet and memory-mapped arrays. The Backtesting page runs the registered mathematical model on eligible dates after its training cutoff. Candle replay and forward DOM signals use different inputs. The configuration replay API can persist bounded results in `bot_backtests`. Source ZIPs, input candles and recordings stay on disk. See the transfer guide before building history with `backend/tools/build_databento_cache.py`. [History transfer](docs/databento-history-transfer.md) · [Replay benchmarks](docs/databento-backtest-benchmarks.md)
- **Forward research** uses recorded decisions, fills, simulated accounts and independently observed depth. The imbalance study tracks qualifications and matched outcomes even when no order fills. TopBot is the implemented practice bot. A second trading bot is future work. [Worker evidence](docs/topbot-worker-evidence.md) · [Imbalance study](docs/topbot-imbalance-study.md) · [Exit study](docs/topbot-exit-study.md)

## Hosted operation and recovery

**Local PostgreSQL on peteamdpc is the sole execution authority.** It holds application accounts, trades, encrypted provider credentials, bot state and saved results. Authentication uses a protected local SQLite owner/session store. The API and bot worker, independent depth recorder, Discord reader and execution backup helper run on the host. The API and depth-recorder tasks use scheduler priority 4. The viewing PC reaches the [practice dashboard](http://127.0.0.1:4173/bot?account=29593203) through the private connection. [Hosted access](docs/hosted-dashboard-connection.md) · [Production logs](docs/ssh-production-logs.md)

Bulk market data, research, worker evidence and trade-progress recordings live under `TOPSIGNAL_DATABENTO_CACHE_DIR`. The default development path is `backend/storage/databento`. Passive collection runs independently of bot arming, account selection, the browser and the viewing PC. Coverage gaps stay visible. [Depth recording](docs/topbot-depth-recording.md) · [Trade progress](docs/trade-progress-recording.md) · [Session order-flow recovery](docs/session-order-flow-recovery.md)

The November 13 maintenance recorded six owned accounts enrolled under an eight-account cap with explicit opt-outs. It also retired the compact cloud mirror and archived Journal. Execution fences remain enabled. Preserve historical migrations, cloud audit data, encrypted archives and original Databento sources. Full-cache restore and reboot acceptance have separate evidence from a focused PostgreSQL/auth backup. Consult the dated receipt before recovery, including its release-87 rollback restriction. [Maintenance checkpoint](docs/audits/2026-11-13/storage-maintenance.md) · [Retirement procedure](docs/supabase-retirement.md)

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

For product details, see the [account workspace walkthrough](docs/account-workspace.md), [Topstep import rules](docs/topstep-live-trade-imports.md), [risk forecast method](docs/account-risk-forecast.md) and [trade-review workflow](docs/trade-review-workflow.md).
