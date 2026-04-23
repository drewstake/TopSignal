# TopSignal

TopSignal is a ProjectX/TopstepX trading review app. It syncs account and execution data into PostgreSQL, computes account-level performance metrics, and exposes those results through a FastAPI backend and a React frontend with dashboards, trade review, expenses, payouts, and a daily journal.

## Current Product Surface

The routed app currently ships these user-facing areas:

- `Dashboard`: account summary, point-payoff comparisons, sustainability/risk metrics, PnL calendar, daily balance curve, and recent trade events
- `Accounts`: account discovery, active-account selection, main-account selection, display-name overrides, state visibility, and journal-history merge
- `Trades`: date-range review, symbol search, trade sync, filtered execution feed, and summary metrics
- `Expenses`: expense CRUD, server-side totals, combine-spend helper, and payout tracking
- `Journal`: account-scoped daily entries with autosave, optimistic concurrency, image uploads, and trade-stat snapshots

Not currently routed:

- `frontend/src/pages/overview/*`
- `frontend/src/pages/analytics/*`

Those folders are still prototype or design-only code, not shipped product screens.

## Architecture

```mermaid
flowchart LR
    A["React + TypeScript frontend"] --> B["FastAPI backend"]
    B --> C["ProjectX API"]
    B --> D["PostgreSQL"]
    B --> E["Supabase Auth (optional)"]
    B --> F["Supabase Storage (optional journal images)"]
    G["ProjectX streaming runtime (optional)"] --> B
```

### Stack

| Layer | Implementation |
| --- | --- |
| Frontend | React 19, TypeScript, Vite 7, React Router 7, Tailwind CSS |
| Backend | FastAPI, SQLAlchemy 2, Pydantic v2 |
| Database | PostgreSQL 16 |
| Auth | Optional Supabase JWT validation |
| Storage | Local disk or Supabase Storage for journal images |
| Tests | Vitest (frontend), Pytest (backend) |

## Data Model At A Glance

The app has two trade pipelines:

- `projectx_trade_events`: current source of truth for dashboard, trades page, PnL calendar, and journal trade-stat pulls
- `trades`: legacy table still used by old `/metrics/*` and `/trades` endpoints

Other key tables:

- `accounts`
- `projectx_trade_day_syncs`
- `instrument_metadata`
- `position_lifecycles`
- `journal_entries`
- `journal_entry_images`
- `expenses`
- `payouts`
- `provider_credentials`

See [db.md](db.md) and [db/README.md](db/README.md) for the database details.

## Key Behaviors

### Accounts

- `GET /api/accounts` is both a read endpoint and the main ProjectX account reconciliation step
- Account states are `ACTIVE`, `LOCKED_OUT`, `HIDDEN`, and `MISSING`
- One account can be marked as `is_main`
- A local `display_name` override can replace the provider account name in the UI

### Trades And Metrics

- The backend syncs ProjectX trades into `projectx_trade_events`
- Trade history is chunked and deduplicated
- Day-level cache completeness is tracked in `projectx_trade_day_syncs`
- The dashboard uses `summary-with-point-bases` to avoid multiple summary fan-out requests
- Trading-day grouping uses the New York session boundary: `6:00 PM ET -> 5:59:59 PM ET next day`

### Journal

- Entries are unique per `(user_id, account_id, entry_date)`
- Autosave uses optimistic concurrency with a `version` column
- Image storage can be local disk or Supabase Storage
- Journal history can be copied from an old account into a replacement account

### Expenses And Payouts

- Expenses support CRUD plus aggregated totals by category and date range
- Practice-account expenses are intentionally blocked
- Payouts are stored separately from expenses and tracked through `/api/payouts*`
- The combine tracker lives on the frontend and can create inferred evaluation-fee rows in the backend

## Repository Layout

```text
TopSignal/
|-- backend/
|   |-- app/
|   |-- requirements.txt
|   `-- tests/
|-- db/
|   |-- migrations/
|   |-- README.md
|   `-- schema.sql
|-- docs/
|   `-- perf-notes.md
|-- frontend/
|   |-- src/
|   `-- README.md
|-- images/
|-- scripts/
|-- .env.example
|-- db.md
|-- docker-compose.yml
`-- package.json
```

## Local Development

### Prerequisites

- Node.js 20+
- Python 3.11+
- npm
- PostgreSQL 16
- Docker if you want the included local Postgres container

### Fastest Anonymous Local Setup

This is the simplest dev path if you do not need Supabase auth locally.

1. Start Postgres:

```powershell
docker compose up -d db
```

2. Create `backend/.env` with at least:

```dotenv
DATABASE_URL=postgresql+psycopg://topsignal:topsignal_password@127.0.0.1:5432/topsignal
PROJECTX_API_BASE_URL=https://api.topstepx.com
PROJECTX_USERNAME=your_topstepx_username
PROJECTX_API_KEY=your_topstepx_api_key
AUTH_REQUIRED=false
```

3. Create `frontend/.env.local`:

```dotenv
VITE_API_BASE_URL=http://localhost:8000
```

4. Install dependencies:

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python -m pip install -r backend\requirements.txt
npm install
npm --prefix frontend install
```

5. Apply the database schema:

```powershell
Get-Content .\db\schema.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
```

6. Run the app:

```powershell
npm run dev
```

That starts:

- backend on `http://localhost:8000`
- frontend on `http://localhost:5173`

### Cloud Or Supabase-Based Setups

`.env.example` includes two reference profiles:

- cloud mode with hosted Supabase
- local Supabase CLI mode

Use those when you want authenticated multi-user behavior instead of anonymous local mode.

## Environment Variables

The repo-level [.env.example](.env.example) is the source of truth for starter env profiles. The most important variables are:

### Backend

- `DATABASE_URL`
- `PROJECTX_API_BASE_URL`
- `PROJECTX_USERNAME`
- `PROJECTX_API_KEY`
- `AUTH_REQUIRED`
- `SUPABASE_URL`
- `SUPABASE_JWKS_URL`
- `SUPABASE_JWT_ISSUER`
- `SUPABASE_JWT_AUDIENCE`
- `SUPABASE_JWT_SECRET`
- `CREDENTIALS_ENCRYPTION_KEY`
- `ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS`
- `ALLOW_INSECURE_LOCAL_CREDENTIALS_KEY`
- `PROJECTX_INITIAL_LOOKBACK_DAYS`
- `PROJECTX_SYNC_CHUNK_DAYS`
- `PROJECTX_DAY_SYNC_LIMIT`
- `PROJECTX_YESTERDAY_REFRESH_MINUTES`
- `PROJECTX_ACCOUNT_MISSING_BUFFER_SECONDS`
- `PROJECTX_LAST_TRADE_LOOKBACK_DAYS`
- `ALLOWED_ORIGINS`
- `ALLOWED_ORIGIN_REGEX`
- `ALLOW_QUERY_BEARER_TOKENS`
- `JOURNAL_IMAGE_STORAGE_BACKEND`
- `JOURNAL_IMAGE_STORAGE_DIR`
- `SUPABASE_STORAGE_BUCKET`
- `SUPABASE_SERVICE_ROLE_KEY`
- `PROJECTX_STREAMING_ENABLED`
- `PROJECTX_MARKET_HUB_URL`
- `PROJECTX_USER_HUB_URL`
- `PROJECTX_MARKET_HUB_SUBSCRIBE_MESSAGE`
- `PROJECTX_USER_HUB_SUBSCRIBE_MESSAGE`

### Frontend

- `VITE_API_BASE_URL`
- `VITE_SUPABASE_URL`
- `VITE_SUPABASE_ANON_KEY`
- `VITE_PERF_LOGS`

## Useful Commands

```powershell
npm run dev
npm run dev:backend
npm run dev:frontend
npm --prefix frontend run build
npm --prefix frontend run lint
npm --prefix frontend run test
backend\.venv\Scripts\python -m pytest backend\tests
```

## Documentation Map

- [db.md](db.md): database design, current persistence model, and trade-ingestion notes
- [db/README.md](db/README.md): fresh schema and migration application instructions
- [docs/perf-notes.md](docs/perf-notes.md): dashboard/accounts performance findings and the fixes reflected in code
- [frontend/README.md](frontend/README.md): frontend architecture, routes, API client behavior, and local frontend development

## Current Limitations

- The repo still contains the legacy `trades` analytics path beside the newer `projectx_trade_events` pipeline
- `GET /api/accounts` performs provider sync inline, so first-load latency is still dominated by ProjectX
- There is backend support for stored ProjectX credentials, but no dedicated routed credentials-management page yet
- `position_lifecycles` is persisted only when the optional streaming runtime is enabled
- The routed UI does not currently expose lifecycle/MAE/MFE data directly
- Schema evolution is raw SQL plus startup compatibility patches, not a formal migration runner

## Testing

Frontend:

```powershell
npm --prefix frontend run test
```

Backend:

```powershell
backend\.venv\Scripts\python -m pytest backend\tests
```

## Where To Start In Code

- App routes: `frontend/src/app/routes.tsx`
- App shell and account picker: `frontend/src/app/AppShell.tsx`
- Shared frontend API client: `frontend/src/lib/api.ts`
- Main backend routes: `backend/app/main.py`
- Trade sync and summaries: `backend/app/services/projectx_trades.py`
- Database models: `backend/app/models.py`

If you are onboarding to the codebase, trace the dashboard flow first. That path touches most of the real product architecture.
