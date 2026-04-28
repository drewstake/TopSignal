# TopSignal

TopSignal is a trading analytics and journaling application for ProjectX/TopstepX-style futures accounts. It syncs account and execution data from the provider, stores it in PostgreSQL, computes account-level performance and risk metrics, and presents those results in a React dashboard with account management, trade review, expense tracking, payout logging, trading-bot controls, and a daily trading journal.

This repository contains:

- a React + TypeScript frontend in `frontend/`
- a FastAPI backend in `backend/`
- a PostgreSQL schema and raw SQL migrations in `db/`

## Why This Project Exists

ProjectX exposes account and trade data, but the raw provider API is not a good day-to-day analytics workspace by itself. TopSignal exists to solve that gap.

It is built for traders who want to:

- keep a local or cloud-backed history of their ProjectX trades
- analyze performance without re-querying the provider for every page load
- understand risk, drawdown, expectancy, and pacing in plain numbers
- journal trading days per account with autosave and trade-stat snapshots
- track real cash costs such as evaluation fees, activations, resets, data fees, and actual payouts
- test simple rule-based bot decisions against ProjectX candles with a server-side audit trail

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
- Account-level performance summaries
- Trading-day PnL calendar
- Trade event feed with lifecycle-derived entry/exit fields
- Expense CRUD and totals
- Payout tracking and payout totals
- Daily journal entries with autosave, optimistic concurrency, trade-stat pulls, and image uploads
- Trading bot configuration, dry-run execution controls, signal charting, and bot activity review
- Optional Supabase authentication for multi-user deployments

## Product Walkthrough

### Dashboard

The dashboard is the main account analytics surface. It is account-scoped and shows:

- headline performance and edge metrics
- drawdown and risk-control context
- long-vs-short breakdowns
- payoff and activity metrics
- sustainability scoring
- a trading-day PnL calendar
- a daily account-balance curve derived from the calendar
- a recent trade-event feed

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
- Clicking a PnL-calendar day filters the trade feed to that trading day.
- Calendar days can open or create a journal entry for that date/account.
- The dashboard uses `summary-with-point-bases` so it can render one summary request plus point-payoff comparisons instead of fanning out multiple summary calls.

### Accounts

The Accounts page is the account-management surface for ProjectX accounts.

A user can:

- view discovered accounts
- see current balance, state, and last known trade timestamp
- toggle hidden and missing accounts into view
- mark one account as the main account
- set the active account used across the rest of the app
- override the provider account name with a local display name
- merge journal history from an older account into a replacement account
- resolve the last trade timestamp from the provider when local data is stale or absent

Accounts page:

![Accounts management page](images/accounts.png)

TopSignal tracks four account states:

- `ACTIVE`: visible and tradable
- `LOCKED_OUT`: account exists but cannot trade
- `HIDDEN`: provider returned it as not visible
- `MISSING`: previously seen, now absent from provider results after a buffer window

### Trades

The Trades page is the execution-review surface.

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

A user can:

- create, list, filter, paginate, and delete expenses
- group spend by date range and category
- track evaluation fees, activation fees, reset fees, data fees, and other costs
- optionally associate an expense with an account, plan size, and account type
- record payouts separately from expenses
- view payout totals, averages, and counts

The page also contains a combine spend helper that:

- infers active combine accounts from account-name prefixes
- keeps a client-side spend ledger in browser storage
- can sync inferred evaluation purchases into the `expenses` table

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
- copy journal content for recent entries
- merge one account's journal history into another account without deleting the source account history

Journal workspace:

![Trading journal page](images/journal.png)

Notable journal behavior:

- Autosave uses optimistic concurrency with a `version` column.
- If a stale save collides with newer server state, the API returns `409 version_conflict` and the UI can reload the server version.
- Journal images are stored either locally on disk or in Supabase Storage, depending on configuration.
- Trade stats can be pulled by explicit trade IDs, explicit date range, or the entry's trading day.
- Journal merge matches entries by `entry_date`. `skip` keeps the destination entry for that date; `overwrite` replaces the destination entry content with the source entry.
- Journal merge copies entries into the destination account and leaves the source account untouched. When image copying is enabled, new destination image records and files are created so source images are not orphaned or shared.

### Bot

The Bot page is the account-scoped rule-execution workspace for ProjectX market data.

A user can:

- create, edit, select, and delete named bot configurations
- bind a bot to a ProjectX account and contract
- search ProjectX contracts from the configuration form
- configure the current `sma_cross` strategy with timeframe, lookback bars, fast SMA, and slow SMA settings
- set risk controls such as order size, max contracts, max daily loss, max trades per day, max open position, trading session, cooldown, and max data staleness
- start a dry-run bot run, evaluate the strategy once, or stop the latest run
- review the latest decision, candle timestamp, decision reason, risk blocks, and order-attempt status
- inspect recent decisions, order attempts, risk events, and run history

The page also includes a Signal Chart backed by ProjectX candles. It supports selectable chart timeframes, live/last price display, fast and slow SMA overlays, VWAP, buy/sell signal markers, computed buy-side and sell-side liquidity levels, drawing tools, refresh, and y-axis fit controls.

Important bot behaviors:

- The routed page lives at `/bot` and accepts the same active-account query parameter used by the app shell.
- New configurations default to dry-run mode and are saved disabled.
- The current UI only starts dry-run runs; live order routing requires backend support plus explicit live confirmation and is not exposed by the page controls.
- Bot decisions, runs, order attempts, and risk events are persisted server-side for auditability.
- Candle reads use ProjectX market-data endpoints and a small frontend candle cache for chart responsiveness.

### Not Currently Routed

The repository also contains `overview/` and `analytics/` pages, but they are not wired into the current router.

Their current status in code:

- `frontend/src/pages/overview/*`: prototype UI backed by mock data
- `frontend/src/pages/analytics/*`: prototype UI backed by mock data

These should be treated as design experiments, not current product features.

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
- Bot configuration and audit history use `bot_configs`, `bot_runs`, `bot_decisions`, `bot_order_attempts`, and `bot_risk_events`.
- ProjectX market candles are cached in `projectx_market_candles` for bot charting and replay-style reads.

### External Integrations

| Integration | Purpose |
| --- | --- |
| ProjectX API | Account discovery, provider auth, trade history sync, last-trade lookup, contract search, market candles, and optional bot order routing |
| Supabase Auth | Optional JWT-based user auth |
| Supabase Storage | Optional journal image storage backend |
| ProjectX market/user hubs | Optional streaming lifecycle tracking |

## System Structure

### Frontend

- `frontend/src/app/`: app shell and router
- `frontend/src/pages/`: routed pages plus unrouted prototypes
- `frontend/src/pages/bot/`: bot configuration page, signal chart, candle cache, and chart data helpers
- `frontend/src/lib/api.ts`: shared API client, request helpers, caches, and in-flight dedupe
- `frontend/src/lib/types.ts`: frontend API types
- `frontend/src/utils/`: metric helpers and formatting

### Backend

- `backend/app/main.py`: FastAPI app and route definitions
- `backend/app/models.py`: SQLAlchemy models
- `backend/app/bot_schemas.py`: bot API request/response schemas
- `backend/app/db.py`: engine/session setup and startup schema compatibility patches
- `backend/app/auth.py`: auth middleware helpers and JWT validation
- `backend/app/services/`: ProjectX sync, analytics, journaling, image storage, payout, streaming, and bot helpers

### Database

- `db/schema.sql`: current schema for fresh database setup
- `db/migrations/*.sql`: additive schema evolution

## Data Flow

### 1. Account Sync Flow

When the frontend requests `GET /api/accounts`:

1. the backend creates a `ProjectXClient` for the current user
2. it calls ProjectX account search
3. it normalizes provider account flags into TopSignal account states
4. it upserts local `accounts` rows
5. it marks older accounts as `MISSING` if they disappear from provider results for longer than the configured buffer
6. it joins locally stored last-trade timestamps from `projectx_trade_events`
7. it returns a frontend-friendly account list

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

That five-minute overlap makes ingestion more robust around provider timing drift and duplicate delivery.

#### Chunking and deduplication

Trade history requests are chunked by `PROJECTX_SYNC_CHUNK_DAYS`. Ingested events are deduplicated by:

- `(user_id, account_id, source_trade_id)` when the provider gives a stable execution ID
- otherwise `(user_id, account_id, order_id, trade_timestamp)`

Voided or canceled provider rows are ignored.

#### Single-day cache behavior

For single-day trade-range requests, TopSignal uses `projectx_trade_day_syncs` to decide whether to re-sync:

- today: always refresh from provider
- yesterday: refresh only if missing, partial, stale, or explicitly requested
- older days: use the local cache when the day was previously marked `complete`, unless explicitly refreshed

This keeps normal navigation cheap while still handling late-arriving fills around today and yesterday.

### 3. Trade Analytics Flow

Trade analytics are derived from normalized execution events.

Key rules in code:

- rows with `pnl = null` are treated as open-leg or half-turn events and do not count as closed trades
- open-leg rows also do not reduce net PnL through fees in the summary logic
- trading-day grouping uses a New York trading session boundary of `6:00 PM ET -> 5:59:59 PM ET next day`
- entry and exit timing for the trade feed is inferred from execution history rather than stored directly by the provider

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

The combine spend helper is separate from core expense storage:

- it lives in browser storage
- it infers combine purchases from active account names
- it can create missing evaluation-fee rows in the backend

### 6. Bot Flow

Bot configurations are user-owned records tied to a ProjectX account and contract.

Typical bot workflow:

1. the frontend loads selectable accounts and `GET /api/bots`
2. the user searches ProjectX contracts and saves a named bot configuration
3. the Signal Chart requests ProjectX candles for the bot contract and selected chart timeframe
4. `POST /api/bots/{id}/evaluate` computes one SMA-cross decision and persists it
5. `POST /api/bots/{id}/start` creates or updates a run, evaluates the strategy, and records any dry-run order attempt or risk block
6. `POST /api/bots/{id}/stop` stops the latest running bot run
7. `GET /api/bots/{id}/activity` returns recent runs, decisions, order attempts, and risk events for the activity tables

Risk checks can block execution for disabled bots, non-active accounts, disallowed contracts, stale data, daily trade limits, session windows, position limits, cooldowns, and daily loss constraints.

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
5. run the root dev command

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
AUTH_REQUIRED=false
```

Recommended minimum frontend variables:

```dotenv
VITE_API_BASE_URL=http://localhost:8000
```

If you want authenticated cloud mode, also set the Supabase variables shown in `.env.example`.

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
```

#### 5. Run the app

```powershell
npm run dev
```

That starts:

- backend on `http://localhost:8000`
- frontend on `http://localhost:5173`

### Environment Variables

The repo-level `.env.example` is the source of truth for starter env profiles. Important variables include:

#### Backend variables

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy database connection URL |
| `PROJECTX_API_BASE_URL` | Base URL for ProjectX API |
| `PROJECTX_USERNAME` | Legacy env-based TopstepX username used for ProjectX API auth |
| `PROJECTX_API_KEY` | Legacy env-based TopstepX API key generated from `Settings -> API` |
| `AUTH_REQUIRED` | Forces API auth on or off |
| `SUPABASE_URL` | Enables Supabase-aware auth and optional storage |
| `SUPABASE_JWKS_URL` | Custom JWKS endpoint for JWT validation |
| `SUPABASE_JWT_ISSUER` | Expected JWT issuer |
| `SUPABASE_JWT_AUDIENCE` | Expected JWT audience |
| `SUPABASE_JWT_SECRET` | Shared secret for local HS-signed tokens |
| `CREDENTIALS_ENCRYPTION_KEY` | Fernet key for encrypting stored provider credentials |
| `ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS` | Allows env credentials as fallback in authenticated deployments |
| `ALLOW_INSECURE_LOCAL_CREDENTIALS_KEY` | Allows local-only encryption-key fallback |
| `PROJECTX_INITIAL_LOOKBACK_DAYS` | First-sync history window |
| `PROJECTX_SYNC_CHUNK_DAYS` | Trade-sync chunk size |
| `PROJECTX_DAY_SYNC_LIMIT` | Per-page trade-day fetch limit |
| `PROJECTX_YESTERDAY_REFRESH_MINUTES` | Staleness threshold for yesterday refresh |
| `PROJECTX_ACCOUNT_MISSING_BUFFER_SECONDS` | Delay before absent accounts become `MISSING` |
| `PROJECTX_LAST_TRADE_LOOKBACK_DAYS` | Provider lookback for last-trade resolution |
| `ALLOWED_ORIGINS` | Exact CORS allowlist |
| `ALLOWED_ORIGIN_REGEX` | Regex-based CORS allowlist |
| `ALLOW_QUERY_BEARER_TOKENS` | Allows `access_token` query param auth for special cases |
| `TOPSIGNAL_DB_SCHEMA_INIT` | `full` runs startup schema compatibility patches; `skip` bypasses them for faster dev startup |
| `JOURNAL_IMAGE_STORAGE_BACKEND` | `local` or `supabase` |
| `JOURNAL_IMAGE_STORAGE_DIR` | Local journal image directory |
| `SUPABASE_STORAGE_BUCKET` | Storage bucket for journal images |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-side key for Supabase Storage operations |
| `PROJECTX_STREAMING_ENABLED` | Enables optional streaming runtime |
| `PROJECTX_MARKET_HUB_URL` | Market SignalR/websocket hub URL |
| `PROJECTX_USER_HUB_URL` | User SignalR/websocket hub URL |
| `PROJECTX_MARKET_HUB_SUBSCRIBE_MESSAGE` | Optional custom subscription payload |
| `PROJECTX_USER_HUB_SUBSCRIBE_MESSAGE` | Optional custom subscription payload |

#### Frontend variables

| Variable | Purpose |
| --- | --- |
| `VITE_API_BASE_URL` | Backend base URL |
| `VITE_SUPABASE_URL` | Supabase project URL |
| `VITE_SUPABASE_ANON_KEY` | Supabase anon key |
| `VITE_PERF_LOGS` | Enable frontend API perf logging |

### Common Commands

| Command | Purpose |
| --- | --- |
| `npm run db:init` | Run backend schema compatibility initialization explicitly |
| `npm run dev` | Run backend and frontend together |
| `npm run dev:backend` | Run backend dev script |
| `npm run dev:frontend` | Run frontend dev script |
| `npm --prefix frontend run build` | Production frontend build |
| `npm --prefix frontend run lint` | Frontend lint |
| `npm --prefix frontend run test` | Frontend tests |
| `backend\.venv\Scripts\python -m pytest backend\tests` | Backend tests |

## Current Limitations

- The main routed app is strong around dashboard, trades, journal, expenses, payouts, and bot dry-run workflows, but `overview/` and `analytics/` are still unrouted prototypes backed by mock data.
- There is backend support for per-user ProjectX credentials, but there is no dedicated frontend credentials-management screen in the current routed UI.
- The repository still carries the legacy `trades` table and `/metrics/*` routes beside the newer `projectx_trade_events` pipeline.
- The accounts endpoint performs provider sync inline, which can make the first load noticeably slower on large account sets.
- The optional streaming lifecycle runtime persists position data, but the current UI does not expose those records directly.
- The bot page exposes dry-run start/evaluate/stop controls, while live order routing remains backend-gated and intentionally absent from the current UI.
- There is no formal migration runner; schema evolution relies on raw SQL plus startup compatibility helpers.
- Expense combine tracking is partly client-side, so it is not a fully server-authoritative accounting subsystem.

## Documentation Map

- `db.md`: database design, persistence model, and trade-ingestion notes
- `db/README.md`: fresh schema and migration application instructions
- `docs/perf-notes.md`: dashboard and accounts performance findings and fixes
- `frontend/README.md`: frontend architecture, routes, API client behavior, and local frontend development

## Summary

TopSignal is best understood as a local-first trading intelligence layer on top of ProjectX:

- ProjectX is the upstream data source
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
