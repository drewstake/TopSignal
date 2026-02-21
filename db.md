# TopSignal Database Notes (Interview Ready)

## 30-second answer
TopSignal stores trade data in a local PostgreSQL database (Docker) and accesses it through SQLAlchemy in the FastAPI backend.  
I keep two trade datasets:
- `projectx_trade_events`: normalized broker trade events synced from ProjectX/TopstepX.
- `trades`: app-level trade records for dashboard metrics.

The main persistence path today is the ProjectX sync flow, which fetches events from the broker API, removes voided/canceled rows, deduplicates by `(account_id, order_id, trade_timestamp)`, and commits to Postgres.

## How data is stored
- Database engine: PostgreSQL 16 in Docker (`docker-compose.yml`).
- Persistence: Docker named volume `topsignal_pgdata` keeps data across container restarts.
- Backend ORM: SQLAlchemy (`backend/app/db.py`) using `DATABASE_URL` from `backend/.env`.
- Tables are mapped in `backend/app/models.py` and created at backend startup via `Base.metadata.create_all(...)`.
- SQL schema/migration scripts also exist under `db/` for explicit setup.

## What is stored (tables and purpose)

### 1) `accounts`
Purpose: one row per trading account.

Core columns:
- `id` (PK)
- `provider`
- `external_id`
- `name`
- `created_at`

Constraint:
- Unique `(provider, external_id)` so one external account is not duplicated.

### 2) `trades`
Purpose: app-level trade records used by `/trades` and `/metrics/*` endpoints.

Core columns:
- `id` (PK)
- `account_id` (FK -> `accounts.id`)
- `symbol`, `side` (`LONG` or `SHORT`)
- `opened_at`, `closed_at`
- `qty`, `entry_price`, `exit_price`
- `pnl`, `fees`
- `notes`
- `is_rule_break`, `rule_break_type`
- `created_at`

Notes:
- `closed_at`, `exit_price`, and `pnl` can be null.
- This table is read by metrics services for closed-trade analytics.

### 3) `projectx_trade_events`
Purpose: normalized raw trade events synced from ProjectX/TopstepX and used by:
- `/api/accounts/{account_id}/trades`
- `/api/accounts/{account_id}/summary`
- `/api/accounts/{account_id}/pnl-calendar`

Core columns:
- `id` (PK)
- `account_id`
- `contract_id`, `symbol`
- `side` (`BUY`, `SELL`, `UNKNOWN`)
- `size`, `price`
- `trade_timestamp`
- `fees`, `pnl`
- `order_id`, `source_trade_id`
- `raw_payload` (JSON from upstream response)
- `created_at`

Constraint:
- Unique `(account_id, order_id, trade_timestamp)` for dedupe safety.

## Ingestion/sync behavior (important interview detail)
- Sync endpoint: `POST /api/accounts/{account_id}/trades/refresh`
- Source endpoint upstream: ProjectX `/api/Trade/search`
- Normalization extracts account, symbol/contract, side, size, price, timestamp, fees, pnl, ids, and stores original payload.
- Voided/canceled executions are filtered out.
- Local dedupe is done before insert; only new events are committed.
- Auto-sync runs on account trade/summary reads when local storage is empty.
- First-time sync defaults to a lookback window (365 days by default), with chunked requests (90-day chunks) to reduce missed events.

## Data actually shown in UI
- Trade screens primarily read from `projectx_trade_events` (latest broker-synced data).
- Some metrics routes still compute from `trades` (app trade table).

## How to prove this quickly (live demo)
From project root:

```powershell
docker compose up -d db
docker exec -it topsignal_db psql -U topsignal -d topsignal
```

Inside `psql`:

```sql
\dt
SELECT COUNT(*) FROM projectx_trade_events;
SELECT COUNT(*) FROM trades;
SELECT id, account_id, symbol, side, size, price, pnl, fees, trade_timestamp
FROM projectx_trade_events
ORDER BY trade_timestamp DESC
LIMIT 20;
```

## Exact code locations
- DB connection/session: `backend/app/db.py`
- Table models: `backend/app/models.py`
- ProjectX sync + persistence: `backend/app/services/projectx_trades.py`
- ProjectX API normalization: `backend/app/services/projectx_client.py`
- API routes using stored trades: `backend/app/main.py`
- Containerized Postgres: `docker-compose.yml`
