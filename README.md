# TopSignal

TopSignal is a trading analytics app for ProjectX/TopstepX accounts. It syncs trade executions into PostgreSQL, serves analytics through a FastAPI backend, and renders account, trade, expense, and journal workflows in a React frontend.

## What It Does

- Syncs ProjectX account and trade data into a local or hosted Postgres database.
- Computes account summaries, drawdown metrics, PnL calendars, and trade-level analytics from stored data.
- Lets you mark a main account, inspect last trade activity, and review missing/hidden accounts.
- Stores per-account daily journal entries with autosave, optimistic concurrency, and image uploads.
- Tracks trading expenses with totals rollups and combine-spend helpers.
- Supports optional Supabase auth for multi-user cloud usage.

## Current Product Surface

Routed pages in the frontend:

- `Dashboard`: summary metrics, PnL calendar, trade drill-down, derived stats.
- `Accounts`: active/main account selection, inactive and missing account visibility, last-trade lookup.
- `Trades`: date-range and symbol filtering with summary stats and refresh.
- `Expenses`: expense CRUD and totals.
- `Journal`: account-scoped daily journaling with autosave, archive toggle, trade-stat pulls, and image uploads.

Not currently exposed as routed product features:

- `frontend/src/pages/overview`
- `frontend/src/pages/analytics`
- A dedicated UI for ProjectX credential management, even though backend endpoints exist for it.

## Stack

- Frontend: React 19, TypeScript, React Router 7, Vite 7, Tailwind CSS
- Backend: FastAPI, SQLAlchemy, Pydantic v2
- Database: PostgreSQL
- Auth: optional Supabase JWT verification and Google sign-in through Supabase
- Testing: Pytest and Vitest

## Architecture

```text
React frontend
  -> FastAPI API
  -> ProjectX account/trade sync services
  -> PostgreSQL
  -> analytics responses back to the UI
```

Important implementation notes:

- The primary analytics dataset is `projectx_trade_events`, not the legacy `trades` table.
- Backend startup runs `init_db()`, which creates mapped tables and applies a few compatibility patches for older Postgres schemas.
- Frontend account and account-scoped reads are cached in memory for 10 minutes.
- Trade sync is local-first: pages read from the database and only hit ProjectX when the cache is empty, stale for a requested day, or explicitly refreshed.

## Repository Layout

```text
TopSignal/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── auth.py
│   │   ├── db.py
│   │   ├── models.py
│   │   └── services/
│   ├── requirements.txt
│   └── tests/
├── db/
│   ├── schema.sql
│   ├── migrations/
│   └── README.md
├── frontend/
│   ├── src/
│   ├── package.json
│   └── vite.config.ts
├── docker-compose.yml
├── .env.example
└── package.json
```

## Prerequisites

- Node.js `^20.19.0 || >=22.12.0`
- Python 3.11+
- npm
- Docker, if you want the local Postgres path from `docker-compose.yml`
- `psql`, if you want to apply `db/schema.sql` and `db/migrations/*.sql` manually

## Setup Modes

There are three realistic ways to run this project.

### 1. Plain Local Postgres (fastest local dev path)

Use this when you want a local-only app without Supabase auth.

1. Start Postgres:

```bash
docker compose up -d db
```

2. Create `backend/.env`:

```dotenv
DATABASE_URL=postgresql+psycopg://topsignal:topsignal_password@127.0.0.1:5432/topsignal
PROJECTX_API_BASE_URL=https://api.topstepx.com
PROJECTX_USERNAME=your_projectx_username
PROJECTX_API_KEY=your_projectx_api_key
AUTH_REQUIRED=false
```

3. Create `frontend/.env.local`:

```dotenv
VITE_API_BASE_URL=http://localhost:8000
```

4. Install dependencies:

```bash
python3 -m venv backend/.venv
./backend/.venv/bin/pip install -r backend/requirements.txt
npm install
npm --prefix frontend install
```

5. Start both apps:

```bash
npm run dev
```

This uses:

- frontend on `http://localhost:5173`
- backend on `http://localhost:8000`

Notes:

- Root `npm run dev` expects the backend interpreter at `backend/.venv/bin/python`.
- In this mode, the frontend does not show a sign-in screen because no Supabase config is present.
- ProjectX credentials are read from env vars by default in local-only mode.

### 2. Hosted Supabase + Local App

Use this when you want cloud Postgres and Supabase auth/storage.

1. Copy values from `.env.example`.
2. Point `DATABASE_URL` at your hosted Postgres instance.
3. Set backend auth vars:

```dotenv
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_JWKS_URL=https://<project-ref>.supabase.co/auth/v1/.well-known/jwks.json
SUPABASE_JWT_ISSUER=https://<project-ref>.supabase.co/auth/v1
SUPABASE_JWT_AUDIENCE=authenticated
AUTH_REQUIRED=true
```

4. Set frontend auth vars:

```dotenv
VITE_API_BASE_URL=http://localhost:8000
VITE_SUPABASE_URL=https://<project-ref>.supabase.co
VITE_SUPABASE_ANON_KEY=your_supabase_anon_key
```

5. Start the app the same way as local mode.

Notes:

- When the frontend has Supabase config, it requires a session before rendering the app.
- The current sign-in flow is Google OAuth through Supabase.
- Backend and frontend Supabase settings need to match; otherwise bearer token validation will fail.

### 3. Local Supabase Stack

Use this if you already run a local Supabase stack and want local auth/storage.

Use the local profile from `.env.example`:

```dotenv
DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:54322/postgres
SUPABASE_URL=http://127.0.0.1:54321
SUPABASE_JWT_SECRET=your_local_supabase_jwt_secret
SUPABASE_JWKS_URL=http://127.0.0.1:54321/auth/v1/.well-known/jwks.json
SUPABASE_JWT_ISSUER=http://127.0.0.1:54321/auth/v1
SUPABASE_JWT_AUDIENCE=authenticated
AUTH_REQUIRED=true
```

```dotenv
VITE_API_BASE_URL=http://localhost:8000
VITE_SUPABASE_URL=http://127.0.0.1:54321
VITE_SUPABASE_ANON_KEY=your_local_supabase_anon_key
```

Important:

- This repo does not include a `supabase/` project directory or migration config.
- `docker-compose.yml` only provisions plain Postgres, not the full Supabase stack.

## Configuration

`.env.example` documents the hosted and local Supabase profiles. The table below adds the repo-specific behavior that matters when wiring the app up.

### Backend

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | yes | SQLAlchemy connection URL. Backend boot fails without it. |
| `PROJECTX_API_BASE_URL` | yes, unless using an alias | ProjectX base URL. Aliases also accepted. |
| `PROJECTX_USERNAME` | yes for env-based credentials | ProjectX username. Aliases also accepted. |
| `PROJECTX_API_KEY` | yes for env-based credentials | ProjectX API key. Aliases also accepted. |
| `AUTH_REQUIRED` | no | If unset, auth is required only when `SUPABASE_URL` is set. |
| `SUPABASE_URL` | no | Enables Supabase auth and optional journal image storage. |
| `SUPABASE_JWKS_URL` | no | Custom JWKS URL for JWT validation. |
| `SUPABASE_JWT_ISSUER` | no | Expected JWT issuer. |
| `SUPABASE_JWT_AUDIENCE` | no | Expected JWT audience. |
| `SUPABASE_JWT_SECRET` | local Supabase only | Needed for HS-signed local tokens. |
| `CREDENTIALS_ENCRYPTION_KEY` | recommended, required outside local DB mode | Encrypts stored ProjectX credentials in `provider_credentials`. |
| `ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS` | no | Lets the backend fall back to env-based ProjectX credentials when auth is enabled. |
| `ALLOW_INSECURE_LOCAL_CREDENTIALS_KEY` | no | Allows a local-only fallback encryption key when using local DB mode. |
| `PROJECTX_INITIAL_LOOKBACK_DAYS` | no | First sync lookback window. Default `365`. |
| `PROJECTX_SYNC_CHUNK_DAYS` | no | Chunk size for refresh windows. Default `90`. |
| `PROJECTX_DAY_SYNC_LIMIT` | no | Page limit for single-day trade sync. Default `1000`. |
| `PROJECTX_YESTERDAY_REFRESH_MINUTES` | no | Staleness threshold for yesterday's single-day cache. Default `180`. |
| `PROJECTX_ACCOUNT_MISSING_BUFFER_SECONDS` | no | Delay before marking absent provider accounts as `MISSING`. Default `300`. |
| `PROJECTX_LAST_TRADE_LOOKBACK_DAYS` | no | Provider lookback window for last-trade resolution. Default `3650`. |
| `ALLOWED_ORIGINS` | no | Exact CORS origins. Defaults to `http://localhost:5173`. |
| `ALLOWED_ORIGIN_REGEX` | no | Regex CORS fallback. Defaults to local hosts only. |
| `ALLOW_QUERY_BEARER_TOKENS` | no | Accepts `access_token` in query params, useful for some direct file/image requests. |
| `JOURNAL_IMAGE_STORAGE_BACKEND` | no | `local` or `supabase`. Defaults to `local`. |
| `JOURNAL_IMAGE_STORAGE_DIR` | no | Local image directory override. Defaults to `backend/storage/journal_images`. |
| `SUPABASE_STORAGE_BUCKET` | when using Supabase image storage | Target bucket for journal images. |
| `SUPABASE_SERVICE_ROLE_KEY` | when using Supabase image storage | Service role key used for journal image upload/download/delete. |
| `PROJECTX_STREAMING_ENABLED` | no | Enables the optional streaming lifecycle runtime. Off by default. |
| `PROJECTX_MARKET_HUB_URL` | when streaming | SignalR market hub URL. |
| `PROJECTX_USER_HUB_URL` | when streaming | SignalR user hub URL. |
| `PROJECTX_MARKET_HUB_SUBSCRIBE_MESSAGE` | optional | JSON SignalR subscribe message for market events. |
| `PROJECTX_USER_HUB_SUBSCRIBE_MESSAGE` | optional | JSON SignalR subscribe message for user events. |

ProjectX env aliases accepted by the client:

- Base URL: `PROJECTX_BASE_URL`, `PROJECTX_GATEWAY_URL`, `TOPSTEP_API_BASE_URL`, `TOPSTEPX_API_BASE_URL`
- Username: `PROJECTX_USER_NAME`, `TOPSTEP_USERNAME`, `TOPSTEPX_USERNAME`
- API key: `TOPSTEP_API_KEY`, `TOPSTEPX_API_KEY`, `PX_API_KEY`

### Frontend

| Variable | Required | Purpose |
| --- | --- | --- |
| `VITE_API_BASE_URL` | no | Backend base URL. Defaults to `http://127.0.0.1:8000`. |
| `VITE_SUPABASE_URL` | optional | Enables Supabase auth in the frontend. |
| `VITE_SUPABASE_ANON_KEY` | required when `VITE_SUPABASE_URL` is set | Public anon key for Supabase auth. |
| `VITE_PERF_LOGS` | optional | Enables additional frontend API performance logging outside dev mode. |

## Auth and Credentials

There are two distinct concepts in this app:

1. User authentication to TopSignal.
2. ProjectX provider credentials used by the backend to call ProjectX.

How they work today:

- If Supabase is configured in the frontend, users must sign in before the app renders.
- If `AUTH_REQUIRED=true`, the backend expects a valid bearer token on `/api/*`, `/metrics/*`, and `/trades`.
- ProjectX credentials can be stored per user in `provider_credentials` through:
  - `GET /api/me/providers/projectx/credentials/status`
  - `PUT /api/me/providers/projectx/credentials`
  - `DELETE /api/me/providers/projectx/credentials`
- The current routed frontend does not expose a dedicated credentials screen.
- In local dev, the backend can fall back to `PROJECTX_USERNAME` and `PROJECTX_API_KEY` from env vars.

## Trade Sync Model

Trade sync behavior is one of the core design points in this repo.

- `POST /api/accounts/{id}/trades/refresh` performs explicit sync into `projectx_trade_events`.
- The first sync uses a configurable lookback window.
- Incremental sync overlaps the previous edge by 5 minutes to reduce missed executions.
- Single-day requests track completeness in `projectx_trade_day_syncs`.
- Voided provider executions are discarded before insert.
- Inserts are deduplicated by:
  - `(user_id, account_id, source_trade_id)` when available
  - `(user_id, account_id, order_id, trade_timestamp)` as a fallback
- Summary and calendar endpoints read from local storage and avoid unnecessary provider backfills unless cache is empty or `refresh=true`.

## Journal Behavior

Journal entries are account-scoped and date-scoped.

- One entry per account per `entry_date`
- Autosave with optimistic concurrency through a `version` column
- `409 version_conflict` responses include the server copy so the UI can reconcile
- Optional trade-stat pull into a journal entry
- Image uploads support `png`, `jpeg`, `jpg`, and `webp`
- Image size limit is 10 MB per file
- Local image storage defaults to `backend/storage/journal_images`

## API Overview

### Health and auth

- `GET /health`
- `GET /api/auth/me`

### ProjectX credential endpoints

- `GET /api/me/providers/projectx/credentials/status`
- `PUT /api/me/providers/projectx/credentials`
- `DELETE /api/me/providers/projectx/credentials`

### Accounts and trade analytics

- `GET /api/accounts`
- `POST /api/accounts/{account_id}/main`
- `GET /api/accounts/{account_id}/last-trade`
- `POST /api/accounts/{account_id}/trades/refresh`
- `GET /api/accounts/{account_id}/trades`
- `GET /api/accounts/{account_id}/summary`
- `GET /api/accounts/{account_id}/summary-with-point-bases`
- `GET /api/accounts/{account_id}/pnl-calendar`

### Journal

- `GET /api/accounts/{account_id}/journal`
- `POST /api/accounts/{account_id}/journal`
- `GET /api/accounts/{account_id}/journal/days`
- `PATCH /api/accounts/{account_id}/journal/{entry_id}`
- `DELETE /api/accounts/{account_id}/journal/{entry_id}`
- `POST /api/accounts/{account_id}/journal/{entry_id}/images`
- `GET /api/accounts/{account_id}/journal/{entry_id}/images`
- `GET /api/journal-images/{image_id}`
- `DELETE /api/accounts/{account_id}/journal/{entry_id}/images/{image_id}`
- `POST /api/accounts/{account_id}/journal/{entry_id}/pull-trade-stats`

### Expenses

- `POST /api/expenses`
- `GET /api/expenses`
- `GET /api/expenses/totals`
- `PATCH /api/expenses/{expense_id}`
- `DELETE /api/expenses/{expense_id}`

### Legacy endpoints still in the backend

- `GET /trades`
- `GET /metrics/summary`
- `GET /metrics/pnl-by-hour`
- `GET /metrics/pnl-by-day`
- `GET /metrics/pnl-by-symbol`
- `GET /metrics/streaks`
- `GET /metrics/behavior`

The routed frontend primarily uses the `/api/accounts/*`, `/api/expenses*`, and journal endpoints.

## Database and Migrations

Primary schema sources:

- `backend/app/models.py`: current SQLAlchemy models
- `db/schema.sql`: hand-maintained bootstrap schema
- `db/migrations/*.sql`: incremental SQL migrations

Important nuance:

- `db/schema.sql` is not the only source of truth. Some newer schema changes live in `db/migrations/`.
- For a fresh local dev database, backend startup is usually enough because `init_db()` creates mapped tables automatically.
- For explicit/manual provisioning, apply `db/schema.sql` and then run every file in `db/migrations/` in filename order.

Example manual apply:

```bash
psql "$DATABASE_URL" -f db/schema.sql
for file in db/migrations/*.sql; do
  psql "$DATABASE_URL" -f "$file"
done
```

The most important tables in current use are:

- `accounts`
- `projectx_trade_events`
- `projectx_trade_day_syncs`
- `position_lifecycles`
- `journal_entries`
- `journal_entry_images`
- `provider_credentials`
- `expenses`

Legacy analytics tables still exist:

- `trades`

Additional database notes live in:

- `db/README.md`
- `db.md`

## Development Commands

Install frontend dependencies:

```bash
npm --prefix frontend install
```

Install backend dependencies:

```bash
python3 -m venv backend/.venv
./backend/.venv/bin/pip install -r backend/requirements.txt
```

Run both apps:

```bash
npm run dev
```

Run frontend build:

```bash
npm --prefix frontend run build
```

Run frontend lint:

```bash
npm --prefix frontend run lint
```

Run frontend tests:

```bash
npm --prefix frontend run test
```

Run backend tests:

```bash
cd backend
./.venv/bin/python -m pytest
```

Run backend only:

```bash
cd backend
./.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

Run frontend only:

```bash
npm --prefix frontend run dev
```

## Known Gaps and Honest Status

- There is no dedicated routed UI for storing ProjectX credentials yet.
- Supabase local mode is documented, but this repo does not include Supabase project scaffolding.
- The backend still exposes legacy `/metrics/*` and `/trades` endpoints backed by the older `trades` table.
- Prototype frontend pages exist but are not part of the routed app.
- There is no formal migration runner such as Alembic in this repo; schema changes are tracked through SQL files plus startup compatibility patches.

## Recommended Reading Order

If you are new to the codebase, start here:

1. `frontend/src/app/routes.tsx`
2. `frontend/src/app/AppShell.tsx`
3. `frontend/src/lib/api.ts`
4. `backend/app/main.py`
5. `backend/app/services/projectx_trades.py`
6. `backend/app/models.py`
