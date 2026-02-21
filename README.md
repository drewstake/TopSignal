# TopSignal

TopSignal is a local trading analytics dashboard for reviewing ProjectX/TopstepX accounts, syncing trade history into Postgres, and inspecting account-level performance from a React UI.

<img width="689" height="1242" alt="image" src="https://github.com/user-attachments/assets/fe1faa81-ff4f-49f9-8857-77d1611de2f5" />


This README is implementation-based and reflects the current code in this repo.

## 1. Project Overview

### What it is
TopSignal is a full-stack app with:
- A React frontend (`frontend/`) for account selection, trade review, and dashboard metrics.
- A FastAPI backend (`backend/`) that fetches ProjectX data, normalizes it, stores it in Postgres, and serves internal APIs.

### Main purpose/use case
- Keep a local, queryable store of ProjectX trade events so dashboard views do not depend on hitting the broker API for every page load.
- Analyze recent performance by account (PnL summary, recent trades, daily PnL calendar, date-filtered trade review).

### Key features currently implemented
- ProjectX account discovery (active accounts only).
- On-demand trade sync to Postgres with dedupe and backfill logic.
- Account-level summary metrics from locally stored ProjectX events.
- Daily PnL calendar (UTC days) with click-to-filter trade table.
- Trades page filters by date range and symbol search.
- Active account persistence across pages via query string + `localStorage`.

### Who it is for
- Primary user: you (active trader) reviewing execution and performance.
- Secondary users: collaborators/recruiters evaluating architecture and data engineering decisions.

## 2. Tech Stack

| Layer | Technologies | Notes |
|---|---|---|
| Frontend | React 19, TypeScript, React Router 7, Vite 7 | Component-local state via React hooks; no Redux/Zustand. |
| Styling/UI | Tailwind CSS, custom UI primitives | No external chart library; charts/heatmaps use custom SVG and styled tables. |
| Backend | FastAPI, SQLAlchemy ORM, Pydantic v2 | REST API + service layer for ProjectX sync and metrics. |
| Database | PostgreSQL 16 (Docker) | Persistent via named Docker volume `topsignal_pgdata`. |
| External API | ProjectX/TopstepX Gateway | Auth via login key -> bearer token; endpoints are called server-side only. |
| Tooling | npm, concurrently, uvicorn, pytest | Root script runs backend + frontend together. |

## 3. Project Structure

```text
TopSignal/
|-- backend/
|   |-- app/
|   |   |-- main.py                  # FastAPI routes
|   |   |-- db.py                    # SQLAlchemy engine/session/init
|   |   |-- models.py                # ORM models: accounts, trades, projectx_trade_events
|   |   |-- schemas.py               # Legacy trade response schema
|   |   |-- metrics_schemas.py       # Legacy metrics response schemas
|   |   |-- projectx_schemas.py      # ProjectX response schemas
|   |   `-- services/
|   |       |-- projectx_client.py   # External API wrapper + token cache
|   |       |-- projectx_trades.py   # Sync, dedupe, storage, serialization
|   |       |-- projectx_metrics.py  # Summary + daily calendar metrics from event rows
|   |       `-- metrics.py           # Legacy metrics from trades table
|   |-- tests/                       # Unit tests for client parsing/sync/metrics
|   `-- requirements.txt
|-- frontend/
|   |-- src/
|   |   |-- app/                     # Router + app shell
|   |   |-- lib/                     # API client, shared types, account selection helpers
|   |   |-- pages/
|   |   |   |-- dashboard/           # Active dashboard page (summary + calendar + trades)
|   |   |   |-- accounts/            # Active accounts page
|   |   |   `-- trades/              # Active trades page
|   |   |-- components/ui/           # Reusable UI primitives
|   |   `-- mock/                    # Mock datasets for unrouted prototype pages
|   |-- package.json
|   `-- vite.config.ts
|-- db/
|   |-- schema.sql                   # Full SQL schema + indexes
|   `-- migrations/                  # Manual SQL migrations
|-- docker-compose.yml               # Postgres service
|-- package.json                     # Root scripts (run backend+frontend)
`-- README.md
```

### Active vs partial code paths
- Active UI routes: `/`, `/accounts`, `/trades` (see `frontend/src/app/routes.tsx`).
- Unrouted prototype pages exist under `frontend/src/pages/overview`, `frontend/src/pages/analytics`, `frontend/src/pages/journal` and consume `frontend/src/mock/data.ts`.
- Backend `/metrics/*` + `/trades` endpoints are implemented but currently not used by routed frontend pages.

## 4. How It Works (End-to-End Data Flow)

1. Frontend loads accounts:
   - Pages call `accountsApi.getAccounts()` -> `GET /api/accounts`.
   - Backend calls ProjectX `/api/Account/search` with `onlyActiveAccounts: true`.
   - Response is normalized to `{id, name, balance, status}` and returned to UI.

2. User selects active account:
   - Account ID is written to URL query param `?account=<id>`.
   - Same ID is stored in browser `localStorage` key `topsignal.activeAccountId`.
   - AppShell reads query first, then falls back to stored account.

3. Dashboard/Trades fetch local account analytics:
   - `GET /api/accounts/{id}/summary`
   - `GET /api/accounts/{id}/trades`
   - `GET /api/accounts/{id}/pnl-calendar` (dashboard only)
   - These queries are served from Postgres table `projectx_trade_events`.

4. Auto-sync behavior:
   - If local data is missing, summary/trades/calendar endpoints trigger sync automatically.
   - Calendar route can also trigger backfill if the default 6-month window is not fully covered locally.

5. Manual sync behavior:
   - UI "Sync" buttons call `POST /api/accounts/{id}/trades/refresh`.
   - Backend fetches from ProjectX `/api/Trade/search`, normalizes rows, excludes voided rows, deduplicates, and inserts only new events.

6. Storage and readback:
   - Synced events are persisted in Postgres `projectx_trade_events`.
   - UI reloads summary/trades views from local DB after sync.

7. Filters:
   - Dashboard day filter: clicking a calendar day sends UTC day range (`start=00:00:00Z`, `end=23:59:59.999Z`) to `/api/accounts/{id}/trades`.
   - Trades page date filters: same UTC boundary conversion from date inputs.
   - Trades page symbol filter is client-side only (text match after fetch).

8. Refresh/polling model:
   - Frontend uses manual refresh/sync buttons; no interval polling loop is currently wired.
   - Backend has a poll-based helper `stream_user_trades(...)` in `projectx_client.py`, but it is not exposed by an API route.

## 5. API Integration (External APIs)

### External service used
TopSignal currently integrates with ProjectX/TopstepX Gateway only.

### Base URL configuration
- Primary env var: `PROJECTX_API_BASE_URL`
- Accepted aliases in code:
  - `PROJECTX_BASE_URL`
  - `PROJECTX_GATEWAY_URL`
  - `TOPSTEP_API_BASE_URL`
  - `TOPSTEPX_API_BASE_URL`

### Authentication flow
1. Backend posts credentials to `/api/Auth/loginKey` with:
   - `userName`
   - `apiKey`
2. Receives bearer token.
3. Token cached in-process with expiry and 60s safety window.
4. If a request returns 401, token cache is cleared and request is retried once.

### Upstream endpoints called by backend

| Upstream Endpoint | Method | Purpose | How response is used |
|---|---|---|---|
| `/api/Auth/loginKey` | `POST` | Login key auth | Produces bearer token for subsequent API calls. |
| `/api/Account/search` | `POST` | List accounts | Mapped to internal account shape for `/api/accounts`. |
| `/api/Trade/search` | `POST` | Fetch trade history by account + time range | Normalized and stored in `projectx_trade_events`; drives local summary/trades/calendar. |

### Upstream payload/normalization details
- Trade fetch payload fields: `accountId`, `startTimestamp`, optional `endTimestamp`.
- Normalized event fields include: account id, symbol/contract id, side, size, price, timestamp, fees, pnl, order id, source trade id, raw payload.
- Voided/canceled rows are excluded (`voided=true` and variants).
- Event ordering is normalized by timestamp ascending before storage.

### API limits/constraints visible in code
- No explicit ProjectX rate-limit handling is implemented.
- Sync windows are split into chunks (`PROJECTX_SYNC_CHUNK_DAYS`, default 90) to reduce dropped results from oversized windows.

## 6. Backend API Endpoints (TopSignal API)

### Route catalog

| Method | Path | Query/Body | Response | Used by frontend |
|---|---|---|---|---|
| `GET` | `/health` | None | `{ "status": "ok" }` | No |
| `GET` | `/trades` | `limit`, optional `account_id` | `TradeOut[]` from `trades` table | No (legacy API) |
| `GET` | `/metrics/summary` | optional `account_id` | `SummaryMetricsOut` | No (legacy API) |
| `GET` | `/metrics/pnl-by-hour` | optional `account_id` | `HourPnlOut[]` | No (legacy API) |
| `GET` | `/metrics/pnl-by-day` | optional `account_id` | `DayPnlOut[]` | No (legacy API) |
| `GET` | `/metrics/pnl-by-symbol` | optional `account_id` | `SymbolPnlOut[]` | No (legacy API) |
| `GET` | `/metrics/streaks` | optional `account_id` | `StreakMetricsOut` | No (legacy API) |
| `GET` | `/metrics/behavior` | optional `account_id` | `BehaviorMetricsOut` | No (legacy API) |
| `GET` | `/api/accounts` | None | `ProjectXAccountOut[]` | Dashboard, Accounts, Trades pages |
| `POST` | `/api/accounts/{account_id}/trades/refresh` | optional `start`, `end` | `{ fetched_count, inserted_count }` | Dashboard, Accounts, Trades pages |
| `GET` | `/api/accounts/{account_id}/trades` | `limit` (1-1000), optional `start`, `end`, `symbol`, `refresh` | `ProjectXTradeOut[]` | Dashboard, Accounts, Trades pages |
| `GET` | `/api/accounts/{account_id}/summary` | optional `start`, `end`, `refresh` | `ProjectXTradeSummaryOut` | Dashboard, Accounts, Trades pages |
| `GET` | `/api/accounts/{account_id}/pnl-calendar` | optional `start`, `end`, `refresh` | `ProjectXPnlCalendarDayOut[]` | Dashboard page |

### Example response shapes

```json
// GET /api/accounts
[
  {
    "id": 13048312,
    "name": "Combine Account",
    "balance": 50743.22,
    "status": "ACTIVE"
  }
]
```

```json
// GET /api/accounts/{account_id}/summary
{
  "realized_pnl": 1250.5,
  "gross_pnl": 1250.5,
  "fees": 88.4,
  "net_pnl": 1162.1,
  "win_rate": 58.33,
  "avg_win": 210.11,
  "avg_loss": -147.85,
  "max_drawdown": -620.4,
  "trade_count": 42
}
```

### Error handling behavior
- `400`:
  - Invalid `account_id` (must be positive).
  - Invalid date range (`start > end`).
- `500`:
  - Missing local server configuration (for example required ProjectX env vars not present).
- `502`:
  - Upstream ProjectX API returned an error or failed request.
- Frontend API helper extracts JSON `detail` where available and shows that message in UI.

## 7. Data Storage

### Where data is stored

| Location | Data | Persistence |
|---|---|---|
| PostgreSQL (`topsignal` DB) | Accounts/trades/event history/metrics source data | Persistent via Docker volume `topsignal_pgdata` |
| Backend process memory | ProjectX auth token cache (`_TOKEN_CACHE`) | Temporary (resets on backend restart) |
| Browser `localStorage` | Active account ID (`topsignal.activeAccountId`) | Persistent in browser until cleared |
| Frontend component state | Loaded accounts/trades/summary/filter state | Temporary (runtime only) |

### What gets stored in Postgres
- `accounts`: account identity metadata.
- `trades`: app-level trade table (legacy metrics endpoints).
- `projectx_trade_events`: normalized ProjectX trade events, including `raw_payload` JSON from upstream.

### Storage notes
- `projectx_trade_events` is the primary live data source for the routed UI.
- No local JSON/CSV file cache is used for API responses.
- No Redis/in-memory DB layer is used.

## 8. Data Models / Schema

### Database tables

| Table | Key fields | Meaning |
|---|---|---|
| `accounts` | `id`, `provider`, `external_id`, `name`, `created_at` | Account registry with unique (`provider`, `external_id`). |
| `trades` | `account_id`, `symbol`, `side`, `opened_at`, `closed_at`, `qty`, `entry_price`, `exit_price`, `pnl`, `fees`, `is_rule_break`, `rule_break_type` | Legacy app trade model used by `/metrics/*` and `/trades` legacy endpoints. |
| `projectx_trade_events` | `account_id`, `contract_id`, `symbol`, `side`, `size`, `price`, `trade_timestamp`, `fees`, `pnl`, `order_id`, `source_trade_id`, `raw_payload` | Normalized ProjectX events; unique on (`account_id`, `order_id`, `trade_timestamp`). |

### API-facing models (selected)
- `ProjectXAccountOut`: `id`, `name`, `balance`, `status`.
- `ProjectXTradeOut`: normalized trade event row for UI table.
- `ProjectXTradeSummaryOut`: net/gross/fees/win-rate/drawdown/trade-count metrics.
- `ProjectXPnlCalendarDayOut`: `date`, `trade_count`, `gross_pnl`, `fees`, `net_pnl`.

### Computed/derived fields
- Net PnL values are computed server-side (gross realized minus effective fees).
- Max drawdown is computed from cumulative equity path over ordered events.
- Win rate uses closed trades only (rows with broker-reported `pnl`).
- Calendar groups by UTC date.

## 9. Metrics Logic

TopSignal currently has two metric pipelines.

### A) ProjectX event metrics (actively used by routed UI)
Source: `projectx_trade_events` via `projectx_metrics.py`.

- Realized PnL per row:
  - `realized = pnl` if `pnl` exists, else `0.0`.
- Effective fee per row:
  - If `pnl` is `null` (open leg), fee contribution is `0`.
  - If `pnl` exists (closing row), fee is included.
- Fee normalization before metric sample:
  - For rows with non-null `pnl`, stored `fees` are doubled in serializer/sample conversion to represent round-trip fee.
- Summary formulas:
  - `gross_pnl = sum(realized_values)`
  - `fees = sum(effective_fees)`
  - `net_pnl = sum(realized - effective_fee)`
  - `win_rate = wins / closed_trade_count * 100`
  - `avg_win = mean(positive closed pnls)`
  - `avg_loss = mean(negative closed pnls)`
  - `max_drawdown = minimum(equity - peak)` over cumulative `net` sequence
- Daily calendar:
  - Ignores rows where `pnl` is null.
  - Groups by UTC `trade_timestamp` date.
  - Returns daily `trade_count`, `gross_pnl`, `fees`, `net_pnl`.

### B) Legacy trade-table metrics (implemented, not used by routed pages)
Source: `trades` table via `services/metrics.py`.

- Net PnL per trade:
  - If `trade.pnl` exists: use it.
  - Else fallback: `qty * (exit - entry) * direction` (LONG=+1, SHORT=-1).
  - Then subtract `fees`.
- Includes:
  - Summary (`net_pnl`, `win_rate`, `profit_factor`, `expectancy`, hold-time stats, drawdown).
  - PnL by hour/day/symbol.
  - Streak metrics (`current`, `longest`, and PnL after 1/2/3+ loss streaks).
  - Behavior metrics (`average_position_size`, `max_position_size`, rule-break counts/PnL).

### Assumptions and limitations in current metric logic
- Broker-provided `pnl` is trusted; missing `pnl` rows are not reconstructed into synthetic PnL.
- ProjectX summary `trade_count` counts all event rows loaded for metrics (including rows where `pnl` is null).
- Legacy fallback PnL formula does not apply contract multipliers (explicit TODO in code).
- All date grouping/filtering is UTC-based.

## 10. Configuration (`.env`)

### Backend environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | Yes | None | SQLAlchemy connection URL for Postgres. |
| `PROJECTX_API_BASE_URL` | Yes (or alias) | None | ProjectX Gateway base URL. |
| `PROJECTX_USERNAME` | Yes (or alias) | None | Username for login-key auth. |
| `PROJECTX_API_KEY` | Yes (or alias) | None | API key for login-key auth. |
| `PROJECTX_INITIAL_LOOKBACK_DAYS` | No | `365` | Default first-sync lookback if `start` is omitted. |
| `PROJECTX_SYNC_CHUNK_DAYS` | No | `90` | Chunk size for long sync windows. |

Accepted aliases:
- Base URL: `PROJECTX_BASE_URL`, `PROJECTX_GATEWAY_URL`, `TOPSTEP_API_BASE_URL`, `TOPSTEPX_API_BASE_URL`
- Username: `PROJECTX_USER_NAME`, `TOPSTEP_USERNAME`, `TOPSTEPX_USERNAME`
- API key: `TOPSTEP_API_KEY`, `TOPSTEPX_API_KEY`, `PX_API_KEY`

### Frontend environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `VITE_API_BASE_URL` | No | `http://localhost:8000` | Base URL used by frontend fetch wrapper. |

Variables seen in some local `.env` files but not currently used by backend code:
- `PROJECTX_USER_HUB_URL`
- `PROJECTX_MARKET_HUB_URL`

### Safe example (`backend/.env`)

```env
DATABASE_URL=postgresql+psycopg://topsignal:topsignal_password@localhost:5432/topsignal
PROJECTX_API_BASE_URL=https://api.topstepx.com
PROJECTX_USERNAME=your_username
PROJECTX_API_KEY=your_api_key
PROJECTX_INITIAL_LOOKBACK_DAYS=365
PROJECTX_SYNC_CHUNK_DAYS=90
```

Optional frontend override (`frontend/.env.local`):

```env
VITE_API_BASE_URL=http://localhost:8000
```

## 11. Setup & Run Instructions

### Prerequisites
- Docker Desktop (or compatible Docker engine)
- Node.js + npm
- Python (with a virtual environment for backend)

### 1) Start Postgres

```powershell
docker compose up -d
```

### 2) Apply schema/migrations

```powershell
Get-Content .\db\schema.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
Get-Content .\db\migrations\20260220_add_rule_break_fields.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
Get-Content .\db\migrations\20260220_add_projectx_trade_events.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
Get-Content .\db\migrations\20260221_add_projectx_trade_day_syncs.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
```

### 3) Install dependencies

Backend:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Frontend:

```powershell
cd ..\frontend
npm install
cd ..
```

### 4) Configure environment
- Create `backend/.env` using the safe example above.

### 5) Run both services from repo root

```powershell
npm run dev
```

Expected local URLs:
- Frontend: `http://localhost:5173`
- Backend: `http://localhost:8000`

### Common commands

```powershell
# backend tests
cd backend
.\.venv\Scripts\python.exe -m pytest -q

# frontend build
cd ..\frontend
npm run build
```

### Troubleshooting
- `DATABASE_URL is not set`:
  - Add `DATABASE_URL` to `backend/.env`.
- 500 errors on `/api/accounts*`:
  - Check ProjectX env vars in `backend/.env`.
- 502 errors on `/api/accounts*`:
  - Upstream ProjectX request/auth failed; inspect credentials/base URL.
- Frontend cannot call backend:
  - Ensure backend is on `:8000`; optionally set `VITE_API_BASE_URL`.
- CORS errors:
  - Backend currently allows only `http://localhost:5173`.

## 12. Usage Guide

1. Open `http://localhost:5173`.
2. Go to `Accounts`:
   - Load available ProjectX accounts (active accounts from upstream).
   - Click an account row to set it as active.
   - Use "Sync Latest Trades" to pull/store new events.
3. Go to `Dashboard`:
   - Review summary cards (net PnL, win rate, avg win/loss, fees, trade count).
   - Use PnL Calendar to inspect daily net performance.
   - Click a calendar day to filter trade events for that UTC day.
4. Go to `Trades`:
   - Filter by date range (`Start`/`End`) and optional symbol text search.
   - Change fetch limit (100/200/500/1000).
   - Page through results.
   - Sync latest events for the selected date range.

### "Active accounts only" behavior
- The backend sends `onlyActiveAccounts: true` to ProjectX account search.
- Additional defensive filtering excludes rows where `canTrade` is false.
- There is no UI toggle to include inactive accounts in current implementation.

## 13. Known Issues / Limitations

- `npm run stop` references `stop.ps1`, but `stop.ps1` is currently missing.
- `db/README.md` references `db/seed.sql`, but `db/seed.sql` is not present.
- Routed UI does not consume legacy `/metrics/*` and `/trades` endpoints yet.
- Unrouted prototype pages (`overview/analytics/journal`) still use mock data.
- CORS origin is hardcoded to `http://localhost:5173` (single-origin local setup).
- No automated migration tool (Alembic) is configured; SQL migrations are manual.
- Requirements include packages (for example `websockets`) that are not currently used by active backend routes.
- ProjectX summary `trade_count` may differ from table views because metric loading includes rows with `pnl = null`.

## 14. Security Notes

- Keep all secrets in environment files only (`backend/.env`) and out of source control.
- Rotate credentials immediately if an API key is exposed.
- Do not commit:
  - Broker API keys
  - Real usernames tied to trading accounts
  - Production database credentials
- Verify `.gitignore` coverage before adding any new env files (especially frontend env files).
- Store only the minimum required upstream payload fields; note `raw_payload` currently persists full upstream rows in Postgres.

## 15. Future Improvements (Realistic Next Steps)

1. Wire routed UI to legacy metrics endpoints (or remove legacy path) to avoid split analytics pipelines.
2. Add Alembic migrations and versioned DB upgrade flow.
3. Add background sync scheduler and/or expose server-side polling stream route.
4. Add server-side symbol filtering support to Trades page request params (backend already supports `symbol`).
5. Add integration tests covering FastAPI routes + DB + ProjectX client stubs.
6. Expand CORS/env-driven deployment configuration for non-local environments.
