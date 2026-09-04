# TopSignal

TopSignal is a trading analytics and journaling application for ProjectX/TopstepX-style futures accounts. It syncs account and execution data from the provider or imports reviewed Topstep Live trade exports, stores it in PostgreSQL, computes account-level performance and risk metrics, and presents those results in a React dashboard with account management, trade review, expense tracking, payout logging, trading-bot controls, and a daily trading journal.

This repository contains:

- a React + TypeScript frontend in `frontend/`
- a FastAPI backend in `backend/`
- a PostgreSQL schema and raw SQL migrations in `db/`

For an unattended personal-device deployment, follow the checked-in
[Windows 24/7 operations guide](docs/windows-24x7-operations.md). It covers the
dedicated non-administrator task identity, immutable release ACLs, prebuilt
loopback control UI, single-worker supervisor, safe dry-run defaults,
migrations, health checks, local logs, reboot recovery, power settings, and
backup/restore drills.

## Why This Project Exists

ProjectX exposes account and trade data, but the raw provider API is not a good day-to-day analytics workspace by itself. TopSignal exists to solve that gap.

It is built for traders who want to:

- keep a local or cloud-backed history of their ProjectX trades
- analyze performance without re-querying the provider for every page load
- understand risk, drawdown, expectancy, and pacing in plain numbers
- journal trading days per account with autosave and trade-stat snapshots
- track real cash costs such as evaluation fees, activations, resets, data fees, and actual payouts
- test rule-based bot decisions against imported Databento history with a server-side audit trail

## What The App Does

At a high level, TopSignal:

1. pulls account and execution data from ProjectX
2. normalizes and stores it in PostgreSQL
3. derives account, trade, day, and behavior metrics from stored data
4. exposes those results through a FastAPI API
5. renders them in a frontend focused on trading review workflows

Core features in the current routed app:

- Account discovery and account-state tracking
- Main-account selection
- Local account display-name overrides
- Manual trade sync from ProjectX
- Reviewed Topstep CSV/XLSX trade imports with duplicate prevention and source-file audit history
- Account-level performance summaries
- Analytics-only Copy Trade Mode for combined leader/follower dashboard views
- Trading-day PnL calendar
- Trade event feed with lifecycle-derived entry/exit fields
- Expense CRUD, spend summaries, and payout-minus-spend net ranges
- Payout tracking, payout totals, and spend-since-last-payout context
- Daily journal entries with autosave, optimistic concurrency, trade-stat pulls, and image uploads
- Trading bot configuration, dry-run execution controls, signal charting, market analysis, trade-plan evaluation, and bot activity review
- Workspace theme selection with live palette previews
- Optional Supabase authentication for multi-user deployments

## Product Walkthrough

The screenshots below are representative local captures from the routed app. Account balances, trade rows, fees, and dates will reflect whichever ProjectX account and database are connected in your environment.

### Dashboard

The dashboard is the main account analytics surface. It is account-scoped by default and can switch into an analytics-only Copy Trade Mode for combined leader/follower account review.

It shows:

- headline performance and edge metrics
- drawdown and risk-control context
- long-vs-short breakdowns
- payoff and activity metrics
- sustainability scoring
- a trading-day PnL calendar
- a Topstep trade-file preview/import panel with exact gross P&L, fees, commissions, and net P&L
- a daily account-balance curve derived from the calendar
- a recent trade-event feed
- optional combined copied-account stats for leader/follower workflows

Dashboard overview:

![Dashboard performance, edge, risk, and sustainability panels](images/metrics.png)

Calendar drill-down:

![Trading-day PnL calendar with journal markers](images/PnL_Calender.png)

Balance curve derived from the selected trading range:

![Daily account balance curve](images/daily_account_bal.png)

Recent execution review feed:

![Recent trade events table](images/recent_trade_events.png)

Important dashboard behaviors:

- The active account comes from the global account picker in the app shell.
- The page can sync trades, change time range, and drill into a specific trading day.
- Topstep CSV/XLSX exports can be reviewed and confirmed directly above the P&L calendar; duplicate source IDs are skipped.
- Clicking a PnL-calendar day filters the trade feed to that trading day.
- Calendar days can open or create a journal entry for that date/account.
- `Summary` opens a coach-style trading summary for the selected dashboard range, including verdict, sample quality, top levers, risks, improvements, and a short action plan.
- `Copy Full Stats` copies a text payload for the selected dashboard range.
- `Copy Trade Mode` can combine the selected leader account with up to four follower accounts for adjusted dashboard totals.
- When Copy Trade Mode is active and calculable, headline metrics, daily PnL, balance context, and calendar data use combined leader/follower results. Otherwise the dashboard shows the selected account only.
- Copy Trade Mode shows leader contribution, follower contribution, copied account count, follower-only PnL, warnings, and resettable likely-uncopy event tracking.
- Copy Trade Mode settings and uncopy-event reset timestamps are stored in browser storage. This mode does not place orders or enable live trade copying.
- The dashboard uses `summary-with-point-bases` so it can render one summary request plus point-payoff comparisons instead of fanning out multiple summary calls.

### Accounts

The Accounts page is the account-management surface for ProjectX-synced and
Live CSV accounts.

A user can:

- view discovered accounts
- see current balance, state, and last known trade timestamp
- toggle hidden and missing accounts into view
- mark one account as the main account
- set the active account used across the rest of the app
- override the provider account name with a local display name
- use the dashboard's `Live Account (CSV)` control to switch to a separate
  local Live account backed by manually imported Topstep trades
- create Live accounts with a name and opening balance; TopSignal generates and
  hides the internal routing key and derives current balance from imported net P&L
- merge journal history from an older account into a replacement account
- resolve the last trade timestamp from the provider when local data is stale or absent

Accounts page:

![Accounts management page](images/accounts.png)

TopSignal tracks four account states:

- `ACTIVE`: visible and tradable
- `LOCKED_OUT`: account exists but cannot trade
- `HIDDEN`: provider returned it as not visible
- `MISSING`: previously seen, now absent from provider results after a buffer window

Each account has a trade-data source assigned when it is created:

- `projectx`: sync account metadata and trades through the ProjectX API
- `csv_import`: keep analytics local and update trades through reviewed
  Topstep CSV/XLSX imports

The dashboard control switches between separate ProjectX and Live CSV account
records; it never converts an Express/XFA account into a CSV account. Live CSV
accounts disable ProjectX refreshes and Copy Trade Mode. They do not delete or
change stored trades, journals, or ProjectX credentials for other accounts.

Account selectors prioritize the main account inside account-type groups so Express, combine, other, and practice accounts are easier to scan during trading workflows.

### Trades

The Trades page is the execution-review surface.

Trades page:

![Trade review filters, summary cards, and execution feed](images/trades.png)

A user can:

- filter trades by date range
- filter trades by symbol text
- choose a row cap
- refresh data from the local cache
- explicitly sync the selected date window from ProjectX
- inspect summary metrics for the filtered window
- review trade events with inferred entry time, exit time, duration, entry price, exit price, and PnL

If an account is currently `MISSING`, the page still shows locally stored data and does not claim live provider sync is available.

### Expenses And Payouts

The Expenses page tracks paid account costs, operating costs, and recorded payouts.

Expenses and payouts page:

![Expenses, combine spend tracker, and payout logging page](images/expenses.png)

A user can:

- create, list, filter, paginate, and delete expenses
- group spend by date range and category
- track evaluation fees, activation fees, reset fees, data fees, and other costs
- optionally associate an expense with an account, plan size, and account type
- record payouts separately from expenses
- view payout totals, averages, and counts
- see recorded spend, spend since the latest payout, and net after payouts
- compare payout-minus-spend cards for 1 month, 3 months, 6 months, YTD, 1 year, anniversary years from the first cash-flow date, and all time

The page also contains a combine spend helper that:

- infers active combine accounts from account-name prefixes
- keeps a client-side spend ledger in browser storage
- can sync inferred evaluation purchases into the `expenses` table
- reconciles generated rows with manually logged or imported combine expenses
- prefers a manual combine expense over a generated row for the same account

This combine tracker is implemented on the frontend and is not a standalone backend subsystem.

### Journal

The Journal page is an account-scoped daily trading journal.

A user can:

- create one journal entry per account per date
- filter entries by date range and mood
- edit title, mood, tags, and notes
- rely on debounced autosave
- archive or unarchive entries
- paste images into the entry workspace
- pull a trade-stat snapshot into the journal entry
- generate or append a Gemini-backed AI Recap for the selected trading date and account
- copy journal content for recent entries
- merge one account's journal history into another account without deleting the source account history

Journal workspace:

![Trading journal page](images/journal.png)

Notable journal behavior:

- Autosave uses optimistic concurrency with a `version` column.
- If a stale save collides with newer server state, the API returns `409 version_conflict` and the UI can reload the server version.
- Journal images are stored either locally on disk or in Supabase Storage, depending on configuration.
- Trade stats can be pulled by explicit trade IDs, explicit date range, or the entry's trading day.
- AI Recap generation uses the backend Gemini client and the existing `GEMINI_*` environment variables listed in this README.
- AI Recap skips days with no trades instead of creating or updating a journal entry.
- Journal merge matches entries by `entry_date`. `skip` keeps the destination entry for that date; `overwrite` replaces the destination entry content with the source entry.
- Journal merge copies entries into the destination account and leaves the source account untouched. When image copying is enabled, new destination image records and files are created so source images are not orphaned or shared.

### Bot

The Bot page is the account-scoped strategy and execution workspace. ProjectX remains the
account/execution/order-routing provider; historical backtests use Databento exclusively.

Bot control page:

![Bot signal chart, strategy configuration, and activity panels](images/bot.png)

A user can:

- create, edit, select, and delete named bot configurations
- bind a bot to a ProjectX account and contract
- search ProjectX contracts from the configuration form
- choose from multiple strategy types, including SMA Cross, EMA Scalping, Support/Resistance, Donchian Breakout, FVG Sweep + MSS, Liquidity Sweep + Retest, Supertrend Pivot, RVOL Breakout, relative-strength strategies, Bollinger/VWAP/Fisher mean reversion, ORB variants, and pullback/trap strategies
- set risk controls such as order size, max contracts, max daily loss, max trades per day, max open position, trading session, cooldown, and max data staleness
- start a continuously armed dry-run, explicitly arm eligible live routing, evaluate once, or stop future automation
- review the latest decision, candle timestamp, decision reason, risk blocks, and order-attempt status
- inspect market bias, scenario weights, expected move, invalidation, nearby levels, volatility, volume, reasoning, and risk notes
- review trade-plan grades when a strategy produces entry, stop, and target prices
- inspect recent decisions, order attempts, risk events, and run history

The page also includes a Signal Chart backed by ProjectX candles. It supports selectable chart timeframes, live/last price display, strategy overlays, VWAP, buy/sell signal markers, computed buy-side and sell-side liquidity levels, drawing tools, refresh, data-gap repair, and y-axis fit controls.

The Analysis panel is generated from backend bot evaluation output when available. If backend analysis is missing but enough chart data is loaded, the UI can fall back to local chart-context analysis. The panel marks stale evaluations when newer bars have printed since the last run. Directional probabilities are heuristic scenario weights, not predictions or financial advice.

When a strategy emits a complete trade idea, bot evaluation can attach a trade-plan readout with a 0-100 score, letter grade, `take`/`wait`/`avoid` decision, confidence, risk-reward context, trend alignment, stop/ATR context, positives, warnings, and suggested adjustments.

Important bot behaviors:

- The routed page lives at `/bot` and accepts the same active-account query parameter used by the app shell.
- New configurations default to dry-run mode and are saved disabled.
- Dry-run is the default. Live routing requires a live-mode configuration, an explicit confirmation, both server-side live gates, a healthy leased worker, and a fresh provider-hub classification proving the account is simulated.
- **Stop Automation** is local-only: it prevents future evaluations but does not cancel working broker orders or close positions. The separately typed-confirmation emergency control cancels and flattens the entire account, then reports success only after ProjectX verifies no orders or positions remain.
- Live/funded accounts are fail-closed. This supports Topstep's simulated account workflows; ProjectX API automation must not be used for a Live Funded Account.
- Bot decisions, runs, order attempts, and risk events are persisted server-side for auditability.
- Historical backtests read only the local fingerprinted Databento Parquet/mmap cache. They never fetch or fall back to ProjectX candles.
- The interactive signal chart and live/dry-run evaluation retain their existing closed-bar cache and frontend cache for responsiveness.

### Themes

The Themes page is the routed appearance workspace for choosing the app's visual palette.

Themes page:

![Theme palette gallery and live preview](images/themes.png)

A user can:

- preview each built-in workspace palette
- apply a theme across the routed app
- compare status, metric, control, and surface colors before switching

Themes are stored client-side and applied through CSS variables, so they affect the shell, dashboard, trade review, journal, bot, and supporting controls without a backend migration.

### Routed Pages

The current router includes these product surfaces:

- `/`: dashboard
- `/accounts`: account management
- `/trades`: execution review
- `/expenses`: expenses and payouts
- `/journal`: daily journal
- `/bot`: bot configuration and dry-run review
- `/themes`: appearance and palette selection

There are no separate `overview/` or `analytics/` prototype route directories in the current tree.

## Architecture

```mermaid
flowchart LR
    A["React frontend"] --> B["FastAPI backend"]
    B --> C["ProjectX API"]
    B --> D["PostgreSQL"]
    B --> E["Supabase Auth (optional)"]
    B --> F["Supabase Storage (optional, journal images)"]
    G["ProjectX streaming runtime (optional)"] --> B
    B --> H["Derived metrics + cached trade history"]
    H --> A
```

### Frontend Stack

| Layer | Implementation |
| --- | --- |
| Framework | React 19 |
| Language | TypeScript |
| Routing | React Router 7 |
| Build tool | Vite 7 |
| Styling | Tailwind CSS + custom UI primitives |
| Auth client | `@supabase/supabase-js` when Supabase env vars are present |
| Tests | Vitest |

### Backend Stack

| Layer | Implementation |
| --- | --- |
| API framework | FastAPI |
| ORM | SQLAlchemy 2 |
| Validation | Pydantic v2 |
| DB driver | `psycopg` |
| Auth verification | PyJWT + JWKS or shared secret |
| Tests | Pytest |

### Database

TopSignal is PostgreSQL-first. The schema is defined in `db/schema.sql`, with incremental SQL migrations in `db/migrations/`.

Important implementation detail:

- The current app's main analytics dataset is `projectx_trade_events`, not the legacy `trades` table.
- The legacy `/metrics/*` endpoints and `/trades` endpoint still read from `trades`.
- The account dashboard, trade review, PnL calendar, and journal trade-stat flows use `projectx_trade_events`.
- Confirmed file imports use the same `projectx_trade_events` analytics path and are audited in `trade_import_batches`.
- Bot configuration and audit history use `bot_configs`, `bot_runs`, `bot_decisions`, `bot_order_attempts`, and `bot_risk_events`.
- Canonical Databento definitions, raw 1-minute bars, statistics, and no-lookahead roll decisions live in the versioned local Parquet/mmap cache. Fresh PostgreSQL schemas omit the older `databento_*` market tables. Upgraded installations may retain them for compatibility; they are never dropped automatically and production backtests do not read them.
- `projectx_market_candles` remains a compatibility cache for the interactive/live bot workspace and is never a backtest source.
- Expense rows include `source_id` so imported or generated rows can be deduplicated by source identity without colliding with a manual row that has the same date, amount, category, and account fields.

### External Integrations

| Integration | Purpose |
| --- | --- |
| ProjectX API | Account discovery, provider auth, executions, positions, trade/journal sync, contract lookup, and optional order routing |
| Databento DBN archives | Sole historical OHLCV and contract-definition source for backtesting |
| Supabase Auth | Optional JWT-based user auth |
| Supabase Storage | Optional journal image storage backend |
| ProjectX market/user hubs | Tenant-scoped streaming adapters; process-global user lifecycle persistence is disabled |

## System Structure

### Frontend

- `frontend/src/app/`: app shell and router
- `frontend/src/pages/`: routed product pages
- `frontend/src/pages/bot/`: bot configuration page, signal chart, candle cache, and chart data helpers
- `frontend/src/pages/themes/`: theme gallery and live appearance preview
- `frontend/src/lib/api.ts`: shared API client, request helpers, caches, and in-flight dedupe
- `frontend/src/lib/types.ts`: frontend API types
- `frontend/src/utils/`: metric helpers and formatting

### Backend

- `backend/app/main.py`: FastAPI app and route definitions
- `backend/app/models.py`: SQLAlchemy models
- `backend/app/bot_schemas.py`: bot API request/response schemas
- `backend/app/trade_plan_schemas.py`: trade-plan evaluation request/response schemas
- `backend/app/db.py`: engine/session setup and startup schema compatibility patches
- `backend/app/auth.py`: auth middleware helpers and JWT validation
- `backend/app/services/`: ProjectX sync, analytics, journaling, image storage, payout, streaming, and bot helpers

### Database

- `db/schema.sql`: current schema for fresh database setup
- `db/migrations/*.sql`: additive schema evolution

## Data Flow

### 1. Account Sync Flow

When the frontend requests `GET /api/accounts`:

1. the backend checks whether any account still uses the `projectx` trade-data source
2. when provider sync is needed, it creates a `ProjectXClient` and calls ProjectX account search
3. it normalizes provider account flags into TopSignal account states
4. it upserts ProjectX-backed `accounts` rows without overwriting CSV-import accounts
5. it marks older ProjectX accounts as `MISSING` if they disappear from provider results for longer than the configured buffer
6. it joins locally stored last-trade timestamps from `projectx_trade_events`
7. it returns a frontend-friendly account list; CSV-import accounts are never marked as having stale provider data

This means the accounts endpoint is both a read endpoint and the main account-state reconciliation step.

### 2. Trade Sync Flow

#### Initial sync

If an account has no local trade data and the app requests summary, trades, or calendar data, the backend can backfill history from:

- `now - PROJECTX_INITIAL_LOOKBACK_DAYS`
- up to the requested end time or current time

#### Incremental sync

If local history already exists and the request does not specify a custom start:

- the backend checks the earliest and latest local timestamps
- it may backfill older history if the local earliest timestamp is newer than the configured lookback floor
- it always adds an incremental sync window from `latest_local - 5 minutes` to `now`
- it refreshes a recent trailing window controlled by `PROJECTX_RECENT_REFRESH_DAYS` so late provider changes can fill in updated PnL, fees, or lifecycle fields

That five-minute overlap makes ingestion more robust around provider timing drift and duplicate delivery.

#### Chunking and deduplication

Trade history requests are chunked by `PROJECTX_SYNC_CHUNK_DAYS` and paged by `PROJECTX_DAY_SYNC_LIMIT`. Ingested events are deduplicated by:

- `(user_id, account_id, source_trade_id)` when the provider gives a stable execution ID
- otherwise `(user_id, account_id, order_id, trade_timestamp)`

Voided or canceled provider rows are ignored. Existing local rows can be updated when ProjectX later returns completed PnL, fee, or lifecycle fields for rows that were previously incomplete.

#### Single-day cache behavior

For single-day trade-range requests, TopSignal uses `projectx_trade_day_syncs` to decide whether to re-sync:

- today: normal dashboard reads use the local cache; explicit sync refreshes from provider
- yesterday: refresh only if missing, partial, stale, or explicitly requested
- older days: use the local cache when the day was previously marked `complete`, unless explicitly refreshed

Repeated or truncated provider pages keep the day marked `partial` rather than `complete`, which lets later sync attempts repair the day. This keeps normal navigation cheap while still handling late-arriving fills around today and yesterday.

#### Topstep Live file import

The dashboard accepts Topstep `.csv` and `.xlsx` trade exports in two phases.
Use `Add Live Account` to create a local, read-only account from the account
name and opening balance shown by Topstep. TopSignal adds imported all-time net
P&L to that opening balance and generates a hidden internal routing key, so no
account ID is requested or displayed for Live accounts. The `Live Account
(CSV)` control then selects that separate account; it never repurposes the
selected Express account. Selecting a Live account turns off Copy Trade Mode
and prevents paused Express accounts
from being auto-selected as copy followers.

1. preview parses and validates the file, calculates new-row gross P&L,
   non-commission fees, commissions, net P&L, wins/losses, and marks duplicate
   rows for review
2. confirm verifies the reviewed file hash and transactionally inserts only new
   trades

Each confirmed file records its original name, SHA-256, import timestamp, and
row counts in `trade_import_batches`. Topstep `Id` is the primary account-scoped
trade identity, with the existing order/exit-timestamp key as a compatibility
fallback, so re-uploading the same file or an overlapping date range does not
overwrite or duplicate existing provider/imported trades. Missing accounts
remain selectable so their locally imported history stays available. Uploads
are limited to 10 MB and 5,000 trades, and the review table displays 100 rows
per page.

### 3. Trade Analytics Flow

Trade analytics are derived from normalized execution events.

Key rules in code:

- rows with `pnl = null` are treated as open-leg or half-turn events and do not count as closed trades
- open-leg rows also do not reduce net PnL through fees in the summary logic
- trading-day grouping uses a New York trading session boundary of `6:00 PM ET -> 5:59:59 PM ET next day`
- entry and exit timing for provider trades is inferred from execution history; imported closed trades preserve the export's explicit entry/exit fields

The backend computes summaries from `projectx_trade_events`, then the frontend computes several additional display-only metrics from the returned summary and trade feed.

### 4. Journal Data Flow

Journal entries are keyed by `(user_id, account_id, entry_date)`.

Typical journal workflow:

1. frontend creates or loads an entry for a specific trading date
2. the user edits title, mood, tags, and notes
3. a debounced autosave queue sends `PATCH` requests after `800ms`
4. the backend validates the expected `version`
5. on success, the entry version increments
6. on conflict, the API returns `409` with the server copy

Image flow:

1. the user pastes an image into the editor
2. frontend uploads it
3. backend validates size and MIME type
4. backend stores the file locally or in Supabase Storage
5. backend persists a `journal_entry_images` row and returns a backend-served URL path

Trade-stat snapshot flow:

1. the user asks to pull stats into a journal entry
2. the backend optionally refreshes the relevant trade window from ProjectX first
3. it computes a snapshot from closed trades in the selected window
4. it stores that snapshot in `journal_entries.stats_json`

AI Recap flow:

1. the user clicks AI Recap for the selected account and trading date on the Journal page
2. the frontend flushes pending journal autosave work, then calls the account-scoped AI recap endpoint
3. the backend loads closed trades for that account and trading date
4. if the day has no trades, the backend skips recap generation and does not create or update a journal entry
5. if trades exist, the backend calls Gemini using the existing `GEMINI_*` backend environment variables listed in this README
6. the recap is created as a new journal entry or appended to the existing entry as a managed AI recap section

Journal merge flow:

1. the user chooses an old account and a new account from the Accounts page
2. the frontend submits `POST /api/journal/merge` with `skip` or `overwrite`
3. the backend validates that both accounts belong to the current user
4. it copies source entries into the destination account by `entry_date`
5. if `include_images=true`, it copies image files and creates new `journal_entry_images` rows for the destination entry
6. it returns a merge summary with transferred, skipped, overwritten, and copied-image counts

### 5. Expense And Payout Flow

Expenses are CRUD records in the `expenses` table. Totals are aggregated server-side by date range, category, and account.

Payouts are stored separately in the `payouts` table and summarized through payout-specific endpoints.

The Expenses page combines those two tables into cash-flow summaries:

- recorded spend
- spend since the latest recorded payout
- net after payouts
- payout-minus-spend ranges for fixed windows, anniversary years, and all time

The combine spend helper is separate from core expense storage:

- it lives in browser storage
- it infers combine purchases from active account names
- it can create missing evaluation-fee rows in the backend
- it reconciles account-inferred purchases with expense-derived purchases
- it uses `source_id` and tags to avoid duplicate imported or generated combine rows

### 6. Bot Flow

Bot configurations are user-owned records tied to a ProjectX account and contract.

Typical bot workflow:

1. the frontend loads selectable accounts and `GET /api/bots`
2. the user searches ProjectX contracts and saves a named bot configuration
3. the Signal Chart requests ProjectX candles for the bot contract and selected chart timeframe
4. `POST /api/bots/{id}/evaluate` computes one selected-strategy decision, persists it, and returns market analysis plus optional trade-plan evaluation
5. `POST /api/bots/{id}/start` creates a durable, explicitly armed run, evaluates once, and allows the single leased worker to adopt continuous runs after restart
6. `POST /api/bots/{id}/stop` locally stops the latest running bot run without claiming to alter broker state
7. `POST /api/bots/{id}/emergency-flatten` durably stops automation, cancels all account orders, closes all account positions, and verifies the account is flat
8. `GET /api/bots/runtime/status` reports worker, lease, provider, classification, and unresolved-submission safety checks
9. `GET /api/bots/{id}/activity` returns recent runs, decisions, order attempts, and risk events for the activity tables
10. `POST /api/bots/{id}/backtests` binary-slices a prebuilt Databento continuous-root mmap and runs an order-routing-free deterministic replay

Risk checks can block execution for disabled bots, non-active accounts, disallowed contracts, stale data, daily trade limits, session windows, position limits, cooldowns, and daily loss constraints.

Trade-plan evaluation is also exposed directly through `POST /api/trade-plan/evaluate`. It scores a proposed trade plan against market context and returns score, grade, `take`/`wait`/`avoid` decision, confidence, reasons, warnings, positives, suggested adjustments, feature values, and category scores.

ProjectX market-data reads have two refresh modes:

- `refresh=true` forces a provider read for the requested edge of the chart window
- `repair=true` forces a full-window fetch so interior candle gaps can be filled

If a provider fetch fails but cached candles cover the request, the backend can return cached candles as a fallback. The chart also uses `/api/projectx/market-price/stream` when the optional streaming runtime is enabled, while keeping REST candles as the canonical closed-bar source.

### 7. Frontend Caching

The frontend has small in-memory caches in `frontend/src/lib/api.ts`:

- account lists: cached for 10 minutes
- account-scoped summary, trades, and PnL-calendar reads: cached for 10 minutes
- journal day markers: cached per account and query
- bot chart candles: cached in browser storage by market and timeframe
- duplicate in-flight requests are deduplicated

Mutation calls invalidate affected cache entries.

## Local Development

### Prerequisites

- Node.js 20+
- Python 3.11+
- npm
- PostgreSQL 16 locally or a hosted PostgreSQL/Supabase database
- Docker, if you want the included local Postgres container

### Fastest Local Setup

The simplest path is:

1. start the local Postgres container
2. create backend and frontend env files
3. install backend and frontend dependencies
4. apply the schema
5. build the local Databento Parquet/mmap cache for backtesting
6. run the root dev command

#### 1. Start PostgreSQL

```powershell
docker compose up -d db
```

#### 2. Create env files

Backend env file: `backend/.env`

Frontend env file: `frontend/.env.local`

Recommended minimum backend variables for local anonymous mode:

```dotenv
DATABASE_URL=postgresql+psycopg://topsignal:topsignal_password@127.0.0.1:5432/topsignal
PROJECTX_API_BASE_URL=https://api.topstepx.com
PROJECTX_USERNAME=your_topstepx_username
PROJECTX_API_KEY=your_topstepx_api_key
ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS=true
AUTH_REQUIRED=false
TOPSIGNAL_DB_SCHEMA_INIT=skip
```

Recommended minimum frontend variables:

```dotenv
VITE_API_BASE_URL=http://localhost:8000
```

If you want authenticated cloud mode, also set the Supabase variables shown in `.env.example`.
When `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` are configured, the frontend
bootstraps a Supabase session before rendering the routed app. If there is no active
session, it shows a Google OAuth sign-in screen first.

#### 3. Install dependencies

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python -m pip install -r backend\requirements.txt
npm install
npm --prefix frontend install
```

#### 4. Apply the database schema

```powershell
Get-Content .\db\schema.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
npm run db:baseline
npm run db:check
```

#### 5. Build the local Databento backtest cache

Databento ZIPs are the canonical backtest market-data source. This command discovers the
ten known MNQ, MES, NQ, and ES jobs in your Downloads folder, validates them, and builds
partitioned Parquet plus memory-mapped arrays without connecting to PostgreSQL or Supabase:

The Backtest card can select MNQ, MES, NQ, or ES independently for each run. This changes
only the Databento replay instrument and its contract economics; it never rewrites the
saved bot's ProjectX trading contract.

```powershell
backend\.venv\Scripts\python backend\tools\build_databento_cache.py --downloads
```

To build only selected materialized timeframes, repeat `--timeframe` or use commas. The
1-minute base is always retained, and an unchanged rerun reuses the existing fingerprinted
cache:

```powershell
backend\.venv\Scripts\python backend\tools\build_databento_cache.py `
  --downloads `
  --timeframe 1m,5m,15m `
  --timeframe 1h,4h,1d
```

You can instead pass ZIP paths positionally and select a cache directory with
`--cache-dir`. Use `--force` only to rebuild the matching immutable version and `--json`
for automation output.

Application requests never build or rewrite a missing timeframe. Re-run the build tool
with the required `--timeframe` so cache publication and integrity validation remain an
explicit offline step.

After building, benchmark a cold mmap open against repeated warm replays. The direct
benchmark defaults to the same lazy, binary-sliced mmap sequence used by production; it
does not allocate one Python object per source bar. The tool checks semantic digests
across every sample and reports input preparation, replay, and cache-stat phases:

```powershell
backend\.venv\Scripts\python backend\tools\benchmark_databento_cache.py `
  --root MNQ --timeframe 5m --days 30

backend\.venv\Scripts\python backend\tools\benchmark_databento_cache.py `
  --root ES --timeframe 1m --days 7 `
  --profile backend\storage\profiles\databento-warm.prof --json

backend\.venv\Scripts\python backend\tools\benchmark_databento_cache.py `
  --root MNQ --timeframe 5m --days 30 `
  --input-mode eager --max-rows 500000 --json

backend\.venv\Scripts\python backend\tools\benchmark_databento_cache.py `
  --root MNQ --timeframe 5m --days 30 `
  --cold-repeats 1 --warm-repeats 5 `
  --sqlite-persistence --json
```

`--input-mode eager` is an explicit comparison mode that materializes Python candle
objects; `--max-rows` applies only to that mode. `--sqlite-persistence` additionally
times `create_bot_backtest` through an in-memory SQLite commit, verifies that its cold
sample used the canonical lazy mmap source (and not materialization), verifies cold
result-cache misses and warm hits, and requires identical input fingerprints and
semantic snapshots for every persisted run.

See `docs/databento-backtest-benchmarks.md` for the checked cold/warm results
from the supplied archives and the exact benchmark methodology.

#### 6. Run the app

```powershell
npm run dev
```

That starts:

- backend on `http://localhost:8000` when available, otherwise the next open port
- frontend on `http://localhost:5173` when available, otherwise Vite's next open port

`npm run dev` runs a small supervisor that prefixes backend/frontend logs, restarts processes that exit early during startup a limited number of times, and stops the sibling process if one side exits permanently. Before Uvicorn starts accepting requests, the backend wrapper transactionally applies any pending database migrations using the same environment snapshot as the backend. The supervisor waits for the backend `/ready` check before starting Vite, so the frontend cannot issue its initial API requests against a backend that is still starting. It also selects the first open backend port at or above `TOPSIGNAL_DEV_BACKEND_PORT` (default `8000`) and passes the matching `VITE_API_BASE_URL` to the frontend process.

`npm run dev:backend` loads `backend/.env`, applies pending transactional migrations, and only then starts Uvicorn. It defaults `TOPSIGNAL_DB_SCHEMA_INIT=skip` because the migration runner establishes the required schema before application startup. If the preferred backend port is busy, it uses the next open port. On Windows, the wrapper manages reload itself to avoid Uvicorn reload control-event issues; set `TOPSIGNAL_DEV_BACKEND_UVICORN_RELOAD=1` to force Uvicorn's native reload there.

Development reload boundaries are intentional:

- A browser reload only reloads the current frontend bundle. It cannot change the backend process environment.
- Vite HMR reloads frontend source modules. Restart the Vite process after changing frontend `VITE_*` environment values.
- Uvicorn reloads changed Python code in a child process, but that child inherits the environment snapshot captured when the Node dev wrapper started. It does not re-read changed values from `backend/.env`.
- After changing `backend/.env`—especially `CREDENTIALS_ENCRYPTION_KEY`, ProjectX settings, auth settings, or database settings—stop and restart the full `npm run dev` supervisor (or the standalone `npm run dev:backend` wrapper). The dev wrapper prints this same instruction when it detects a `backend/.env` change. A successful direct provider login in a separate shell does not prove that the running app has the same environment or can decrypt the signed-in user's stored credential.

### Environment Variables

The repo-level `.env.example` is the source of truth for starter env profiles. Important variables include:

#### Backend variables

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy database connection URL |
| `MIGRATION_DATABASE_URL` | Optional direct/session-mode PostgreSQL URL used only by the migration runner; transaction pooler port `6543` is rejected |
| `PROJECTX_API_BASE_URL` | Base URL for ProjectX API |
| `PROJECTX_USERNAME` | Legacy env-based TopstepX username used for ProjectX API auth |
| `PROJECTX_API_KEY` | Legacy env-based TopstepX API key generated from `Settings -> API` |
| `AUTH_REQUIRED` | Requires API auth by default; `false` is accepted only in a fully local runtime |
| `SUPABASE_URL` | Enables Supabase-aware auth and optional storage |
| `SUPABASE_JWKS_URL` | Custom JWKS endpoint for JWT validation |
| `SUPABASE_JWT_ISSUER` | Expected JWT issuer |
| `SUPABASE_JWT_AUDIENCE` | Expected JWT audience |
| `SUPABASE_JWT_ALGORITHMS` | Optional comma-separated narrowing of reviewed JWT algorithms (`HS256`, `RS256`, `ES256`) |
| `SUPABASE_JWT_SECRET` | Shared secret for local HS-signed tokens |
| `CREDENTIALS_ENCRYPTION_KEY` | Fernet key for encrypting stored provider credentials |
| `ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS` | Explicitly allows one server-wide ProjectX identity only in a fully local, single-user runtime |
| `ALLOW_INSECURE_LOCAL_CREDENTIALS_KEY` | Explicitly allows the deterministic encryption key only with a local/SQLite database; never use it for real credentials |
| `PROJECTX_INITIAL_LOOKBACK_DAYS` | First-sync history window |
| `PROJECTX_RECENT_REFRESH_DAYS` | Recent trailing sync window used to catch late provider updates |
| `PROJECTX_SYNC_CHUNK_DAYS` | Trade-sync chunk size |
| `PROJECTX_DAY_SYNC_LIMIT` | Per-page trade-day fetch limit |
| `PROJECTX_YESTERDAY_REFRESH_MINUTES` | Staleness threshold for yesterday refresh |
| `PROJECTX_ACCOUNT_MISSING_BUFFER_SECONDS` | Delay before absent accounts become `MISSING` |
| `PROJECTX_ACCOUNT_STALE_AFTER_SECONDS` | Age after which a cache-only account response is labeled stale; defaults to 900 seconds |
| `PROJECTX_LAST_TRADE_LOOKBACK_DAYS` | Provider lookback for last-trade resolution |
| `GEMINI_API_KEY` | Server-side Gemini API key used by AI journal recap generation |
| `GEMINI_MODEL` | Gemini model for AI journal recap generation; defaults to `gemini-3.1-flash-lite` |
| `GEMINI_API_BASE_URL` | Optional Gemini API base URL override |
| `GEMINI_TIMEOUT_SECONDS` | Optional Gemini request timeout override |
| `GEMINI_RETRY_ATTEMPTS` | Optional total attempts for retryable Gemini HTTP errors; defaults to `3` |
| `GEMINI_RETRY_BACKOFF_SECONDS` | Optional base delay for Gemini retries; defaults to `0.75` |
| `ALLOWED_ORIGINS` | Exact CORS allowlist |
| `ALLOWED_ORIGIN_REGEX` | Regex-based CORS allowlist |
| `ALLOW_QUERY_BEARER_TOKENS` | Allows `access_token` query param auth for special cases |
| `TOPSIGNAL_DB_SCHEMA_INIT` | `full` runs startup schema compatibility patches; `skip` bypasses them for faster dev startup |
| `BACKTEST_MAX_CONCURRENT_GLOBAL` | Global concurrent backtest limit; defaults to `2` |
| `BACKTEST_MAX_CONCURRENT_PER_USER` | Validated per-user backtest setting; the newest Run supersedes and cooperatively cancels that user's active replay, so only one result can persist at a time |
| `TOPSIGNAL_BACKTEST_MEMORY_BUDGET_BYTES` | Maximum estimated in-memory replay working set; defaults to 1.5 GiB |
| `TOPSIGNAL_BACKTEST_EVALUATOR_WORK_BUDGET` | Maximum strategy-aware estimated replay work before a run is rejected; defaults to `1000000000` weighted bar visits |
| `TOPSIGNAL_BACKTEST_MAX_SERIES_POINTS` | Maximum persisted equity/drawdown chart points before deterministic sampling; defaults to `50000` |
| `TOPSIGNAL_DATABENTO_CACHE_DIR` | Persistent local directory for canonical Databento Parquet and memory-mapped replay artifacts; defaults to `backend/storage/databento` |
| `TOPSIGNAL_BACKTEST_CACHE_MAX_ENTRIES` | Maximum number of prepared replay entries retained by the in-process LRU; defaults to `8` |
| `TOPSIGNAL_BACKTEST_CACHE_MAX_BYTES` | Maximum estimated size of the in-process replay LRU; defaults to `536870912` bytes (512 MiB) |
| `TOPSIGNAL_BACKTEST_PROXY_CACHE_ROWS` | Maximum recently accessed lazy candle proxies retained per opened mmap stream; defaults to `16384` rows |
| `TOPSIGNAL_BACKTEST_RESULT_CACHE_MAX_ENTRIES` | Maximum exact deterministic replay results retained for repeated Run requests; defaults to `8` |
| `TOPSIGNAL_BACKTEST_RESULT_CACHE_MAX_BYTES` | Conservative estimated memory ceiling for cached replay results; defaults to `268435456` bytes (256 MiB) |
| `TOPSIGNAL_DEV_BACKEND_PORT` | Preferred backend port for local dev; defaults to `8000` and falls forward when busy |
| `TOPSIGNAL_LIVE_EXECUTION_ENABLED` | Enables one server-side live-routing gate when set to a true value; defaults disabled, is never sufficient by itself, and is ignored in tests |
| `TOPSIGNAL_BOT_WORKER_ENABLED` | Enables the database-leased recurring closed-candle evaluator; defaults disabled |
| `TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION` | Independent worker live-routing gate; defaults disabled and is never sufficient by itself |
| `TOPSIGNAL_BOT_WORKER_POLL_SECONDS` | Worker scheduler polling interval; defaults to `5` seconds |
| `TOPSIGNAL_BOT_CANDLE_CLOSE_GRACE_SECONDS` | Delay after a computed candle boundary before evaluation; defaults to `2` seconds |
| `TOPSIGNAL_BOT_MAX_SCHEDULE_JITTER_SECONDS` | Deterministic maximum worker evaluation jitter; defaults to `3` seconds |
| `TOPSIGNAL_BOT_WORKER_LEASE_TTL_SECONDS` | Database worker lease duration; defaults to `45` seconds |
| `TOPSIGNAL_BOT_WORKER_HEARTBEAT_SECONDS` | Worker lease-renewal interval; defaults to `10` seconds and must be less than half the TTL |
| `TOPSIGNAL_BOT_PROVIDER_PROBE_SECONDS` | Provider health-probe interval for armed runs; defaults to `60` seconds |
| `TOPSIGNAL_BOT_MAX_RETRY_BACKOFF_SECONDS` | Maximum retry delay after transient provider/runtime errors; defaults to `300` seconds |
| `TOPSIGNAL_DEV_BACKEND_UVICORN_RELOAD` | On Windows, set to `1` to use Uvicorn's native reload instead of wrapper-managed backend reload |
| `JOURNAL_IMAGE_STORAGE_BACKEND` | `local` or `supabase` |
| `JOURNAL_IMAGE_STORAGE_DIR` | Local journal image directory |
| `SUPABASE_STORAGE_BUCKET` | Storage bucket for journal images |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-side key for Supabase Storage operations |
| `PROJECTX_STREAMING_ENABLED` | Reserved streaming gate; defaults to `false`, and process-global startup is deliberately refused because it cannot prove tenant ownership |
| `PROJECTX_MARKET_HUB_URL` | Market SignalR/websocket hub URL used only by an explicitly scoped runtime |
| `PROJECTX_USER_HUB_URL` | User SignalR/websocket hub URL used only by a runtime bound to one authenticated user and owned account |
| `PROJECTX_MARKET_HUB_SUBSCRIBE_MESSAGE` | Optional custom subscription payload |
| `PROJECTX_USER_HUB_SUBSCRIBE_MESSAGE` | Optional custom subscription payload |

ProjectX user-hub lifecycle persistence is never started from server-wide environment credentials. The process-global stream remains disabled. For each explicitly armed live run, the leased bot worker instead creates a demand-driven runtime with an authenticated `user_id`, owned account ID, and per-user client factory; its state is keyed by user, account, and contract and is stopped when that account no longer has an eligible run.

#### Frontend variables

| Variable | Purpose |
| --- | --- |
| `VITE_API_BASE_URL` | Backend base URL |
| `VITE_SUPABASE_URL` | Supabase project URL |
| `VITE_SUPABASE_ANON_KEY` | Supabase anon key |
| `VITE_PERF_LOGS` | Enable frontend API perf logging |
| `VITE_DEMO_MODE` | Start the frontend in read-only demo mode with masked account, journal, and money fields |

Frontend auth behavior:

- If both `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` are present, the app treats Supabase auth as enabled.
- With Supabase auth enabled, the app requires Supabase session bootstrap before the routed app is shown.
- If session bootstrap does not find an active session, the user sees a Google OAuth sign-in screen.
- Google must be enabled in Supabase Auth providers for sign-in to work.

### Common Commands

| Command | Purpose |
| --- | --- |
| `npm run db:init` | Run backend schema compatibility initialization explicitly |
| `npm run db:adopt-current` | After a verified backup, validate and adopt a populated pre-runner database into the migration ledger without replaying history |
| `npm run db:baseline` | Validate a database created from current `schema.sql` and initialize its migration ledger |
| `npm run db:migrate` | Apply pending PostgreSQL migrations transactionally |
| `npm run db:check` | Check migration version/checksum state without changing the database |
| `npm run dev` | Run backend and frontend together |
| `npm run dev:backend` | Run backend dev script |
| `npm run dev:frontend` | Run frontend dev script |
| `npm run prod` | From an ACL-hardened non-Git release, apply migrations and supervise the loopback API, bot worker, and prebuilt control UI on Windows |
| `npm run prod:backend` | Backward-compatible alias of `npm run prod`; it also supervises the production control UI |
| `npm --prefix frontend run build` | Production frontend build |
| `npm --prefix frontend run lint` | Frontend lint |
| `npm --prefix frontend run test` | Frontend tests |
| `cd backend; .\.venv\Scripts\python -m pytest` | Backend tests |

## Current Limitations

- The main routed app is strong around dashboard, trades, journal, expenses, payouts, themes, and bot dry-run workflows.
- There is backend support for per-user ProjectX credentials, but there is no dedicated frontend credentials-management screen in the current routed UI.
- The repository still carries the legacy `trades` table and `/metrics/*` routes beside the newer `projectx_trade_events` pipeline.
- The accounts endpoint performs provider sync inline, which can make the first load noticeably slower on large account sets.
- The optional streaming lifecycle runtime persists position data, while the bot worker starts account-scoped user-hub streams only for explicitly armed live runs.
- Live order routing remains deliberately multi-gated and limited to freshly verified simulated accounts. Passing tests and a green readiness endpoint do not replace a multi-session Practice soak or operator supervision.
- Schema evolution uses the checksummed migration runner; startup compatibility helpers remain as a temporary local-development safety net.
- Backtest concurrency limits are process-local. Use a single API worker until a distributed queue or lease is added for multi-worker or multi-replica deployments.
- Expense combine tracking is partly client-side, so it is not a fully server-authoritative accounting subsystem.

## Documentation Map

- `db.md`: database design, persistence model, and trade-ingestion notes
- `db/README.md`: fresh schema and migration application instructions
- `docs/perf-notes.md`: dashboard and accounts performance findings and fixes
- `docs/windows-24x7-operations.md`: personal-Windows-laptop deployment, startup, readiness, recovery, and Practice-soak checklist
- `frontend/README.md`: frontend architecture, routes, API client behavior, and local frontend development

## Summary

TopSignal is best understood as a local-first trading intelligence layer with separated providers:

- ProjectX owns accounts, executions, positions, trade/journal synchronization, and order routing
- Databento is the sole historical market-data source for backtesting
- PostgreSQL is the local analytics cache and journal/expense store
- FastAPI is the normalization and metrics layer
- React is the trader-facing review workspace

If you are onboarding to the codebase, start with the dashboard flow and trace it through:

- `frontend/src/pages/dashboard/DashboardPage.tsx`
- `frontend/src/lib/api.ts`
- `backend/app/main.py`
- `backend/app/services/projectx_trades.py`
- `backend/app/services/projectx_metrics.py`

That path shows most of the application's real architecture.
