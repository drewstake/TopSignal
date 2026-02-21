# TopSignal

TopSignal is a local trading dashboard for account/trade review and metrics.

## Stack

- Frontend: React + TypeScript + Vite
- Backend: FastAPI + SQLAlchemy
- Database: Postgres (Docker)

## ProjectX Accounts + Trades Feature

The app now includes a full ProjectX account/trade flow:

- `GET /api/accounts`: list ProjectX accounts
- `GET /api/accounts/{account_id}/trades`: list stored trade events (fills/trades)
- `GET /api/accounts/{account_id}/summary`: summary metrics from stored trade events
- `GET /api/accounts/{account_id}/pnl-calendar`: daily PnL aggregation for calendar views
- `POST /api/accounts/{account_id}/trades/refresh`: pull latest events from ProjectX and persist

Trade data collection behavior:

- Trade events are pulled from ProjectX on demand using `/api/Trade/search`.
- Events are stored locally in `projectx_trade_events`.
- Deduplication key: `account_id + order_id + trade_timestamp`.
- First sync now pulls up to the last 365 days by default (`PROJECTX_INITIAL_LOOKBACK_DAYS`) and auto-backfills older missing history inside that window if local data is partial.
- Large sync windows are split into chunks (default 90 days via `PROJECTX_SYNC_CHUNK_DAYS`) to reduce missed events from oversized API responses.
- The `GET /api/accounts/{account_id}/trades` and `GET /api/accounts/{account_id}/summary` routes auto-sync when local storage is empty.

UI paths:

- `/accounts`: account list + active account selection + sync + recent events preview
- `/trades`: filters, summary cards, and paginated trade-event table for active account

## Environment

Create `backend/.env` with:

```env
DATABASE_URL=postgresql+psycopg://topsignal:topsignal_password@localhost:5432/topsignal
PROJECTX_API_BASE_URL=<your_projectx_gateway_base_url>
PROJECTX_USERNAME=<your_projectx_username>
PROJECTX_API_KEY=<your_projectx_api_key>
```

## Local Setup

### 1) Start Postgres

From repo root:

```powershell
docker compose up -d
```

### 2) Apply schema

```powershell
docker exec -i topsignal_db psql -U topsignal -d topsignal < .\db\schema.sql
```

If your database already exists, also apply migrations:

```powershell
docker exec -i topsignal_db psql -U topsignal -d topsignal < .\db\migrations\20260220_add_rule_break_fields.sql
docker exec -i topsignal_db psql -U topsignal -d topsignal < .\db\migrations\20260220_add_projectx_trade_events.sql
```

### 3) Install dependencies

Backend:

```powershell
cd backend
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Frontend:

```powershell
cd frontend
npm install
```

### 4) Run app

From repo root:

```powershell
npm run dev
```

- Backend: `http://localhost:8000`
- Frontend: `http://localhost:5173`

## Tests

Backend metric unit tests:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest -q
```
