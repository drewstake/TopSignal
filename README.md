# TopSignal

Quick Interview Summary
- Full-stack trading analytics app: React + FastAPI + Postgres.
- Pulls account/trade data from ProjectX/TopstepX server-side, normalizes it, and persists it locally.
- Uses local DB as the primary analytics source so UI pages are fast and mostly API-independent after sync.
- Implements two analytics paths: active ProjectX event metrics and legacy `trades`-table metrics.
- Includes account-scoped journaling with debounced autosave and soft-archive support.
- Emphasizes practical data engineering concerns: dedupe, sync windows, day-level cache completeness, and UTC-safe filtering.

## 1) Elevator Pitch (Interview Version)
TopSignal is a local-first trading analytics dashboard I built to review ProjectX/TopstepX performance without depending on live broker calls for every page load. The backend authenticates with ProjectX, fetches account and trade data, normalizes it, and stores trade events in Postgres with dedupe and sync tracking. The frontend then reads from my own API for account summaries, daily PnL calendar, trade history, and journaling workflows. I built it because I wanted a reliable way to inspect performance patterns, not just raw broker screens, and I wanted full control over metrics logic. The technically interesting parts are the sync strategy (initial lookback + incremental overlap + day-level completeness), event normalization, and metric computation pipeline for drawdowns, expectancy, and directional analysis. I also keep legacy metrics endpoints and a newer ProjectX event pipeline side-by-side, which makes design tradeoffs very explicit. The project is intentionally practical: it solves a real workflow and exposes clear next steps for scale, reliability, and analytics depth.

## 2) Project Overview
Main use case
- Track active ProjectX accounts.
- Sync broker trade events into local Postgres.
- Review account performance through summary metrics, calendar PnL, and filtered trade events.
- Maintain account-scoped trade journal entries.

Core features
- ProjectX active account discovery (`GET /api/accounts`).
- Manual + automatic trade sync (`POST /api/accounts/{id}/trades/refresh` and cache-on-read behavior).
- Account summary analytics (`GET /api/accounts/{id}/summary`).
- Daily PnL calendar (`GET /api/accounts/{id}/pnl-calendar`).
- Trade event list with date range filtering (`GET /api/accounts/{id}/trades`).
- Journal CRUD with autosave and archive toggle (`/api/accounts/{id}/journal*`).

Typical user workflow
1. Open app, choose active account from header select.
2. Sync latest trades.
3. Review dashboard metrics and calendar.
4. Click a calendar day to inspect that day's trade events.
5. Use Trades page for broader date range and symbol search.
6. Capture notes in Journal page with autosave.

Primary data focus
- Accounts: `id`, `name`, `balance`, `status`.
- Trade events: execution-side rows with timestamp, side, size, price, fees, broker PnL.
- Derived metrics: PnL, win/loss distribution, drawdown stats, daily stability, directional breakdown.
- Journal entries: per-account daily notes with mood/tags/body.

## 3) Architecture Summary
High-level layers
- Frontend (`frontend/`): React app, routing, filters, rendering cards/tables.
- Backend (`backend/`): FastAPI routes, ProjectX integration, normalization, metric calculation, journal services.
- Storage (`Postgres`): durable trade/journal data and day-sync status.
- External API (`ProjectX/TopstepX Gateway`): upstream accounts and trade history.

Responsibilities by layer
- Frontend: user interaction, account/date selection, displaying metrics/tables, optimistic autosave UX states.
- Backend: secure upstream API calls, token management, dedupe/upsert, cache rules, metric calculations.
- DB: persistent event source-of-truth for analytics pages.

ASCII data-flow diagram
```text
User
  -> React Frontend (routes/pages)
  -> FastAPI Backend API
  -> ProjectX Gateway (Auth + Account + Trade endpoints)
  -> FastAPI normalization/sync services
  -> Postgres (projectx_trade_events, journal_entries, sync tables)
  -> FastAPI metric/query responses
  -> Frontend cards/tables/journal UI
```

## 4) Tech Stack
| Layer | Tech | Why used here |
|---|---|---|
| Frontend | React 19, TypeScript, React Router 7, Vite 7 | Fast local UI iteration, typed API integration, route-level pages. |
| Frontend Styling | Tailwind CSS + custom UI primitives | Consistent card/table/dashboard styling without heavy UI framework overhead. |
| Backend API | FastAPI | Quick typed REST endpoints with Pydantic response models. |
| Backend Data Access | SQLAlchemy ORM | Query composition, model definitions, DB abstraction. |
| Validation/Schema | Pydantic v2 | Typed API payloads for journal/trades/metrics responses. |
| Database | PostgreSQL 16 (Docker container) | Durable local cache for trade events and journal data. |
| External API | ProjectX/TopstepX Gateway | Source for accounts and broker trade history. |
| Testing | Pytest (backend), Vitest (frontend) | Formula and workflow regression safety. |
| Runtime tooling | `uvicorn`, `npm`, `concurrently` | Local backend/frontend dev workflow from root script. |

## 5) Project Structure
Major tree
```text
TopSignal/
|-- backend/
|   |-- app/
|   |   |-- main.py
|   |   |-- db.py
|   |   |-- models.py
|   |   |-- projectx_schemas.py
|   |   |-- journal_schemas.py
|   |   |-- metrics_schemas.py
|   |   |-- schemas.py
|   |   `-- services/
|   |       |-- projectx_client.py
|   |       |-- projectx_trades.py
|   |       |-- projectx_metrics.py
|   |       |-- journal.py
|   |       `-- metrics.py
|   |-- tests/
|   `-- requirements.txt
|-- frontend/
|   |-- src/
|   |   |-- app/
|   |   |-- lib/
|   |   |-- pages/
|   |   |-- utils/
|   |   |-- components/
|   |   `-- mock/
|   |-- package.json
|   `-- vite.config.ts
|-- db/
|   |-- schema.sql
|   `-- migrations/
|-- docker-compose.yml
|-- package.json
|-- db.md
`-- README.md
```

What each important folder does
- `backend/app/main.py`: all API route handlers and request validation.
- `backend/app/services/projectx_client.py`: upstream HTTP client, token caching, response normalization.
- `backend/app/services/projectx_trades.py`: sync windows, day-level cache logic, dedupe/upsert, serialization.
- `backend/app/services/projectx_metrics.py`: summary and calendar metric computation from event samples.
- `backend/app/services/journal.py`: journal filtering, normalization, create/update/archive logic.
- `backend/app/models.py`: SQLAlchemy models for all DB tables.
- `backend/tests/`: formula and sync behavior tests.
- `frontend/src/lib/api.ts`: frontend API client + accounts TTL cache.
- `frontend/src/pages/dashboard/DashboardPage.tsx`: main analytics UI and range/day filtering.
- `frontend/src/pages/trades/TradesPage.tsx`: trade list and summary with filters and sync action.
- `frontend/src/pages/journal/JournalPage.tsx`: autosave journal workflow.
- `frontend/src/utils/` and `frontend/src/utils/metrics/`: frontend-derived metric formulas.
- `db/schema.sql` + `db/migrations/*.sql`: SQL schema and manual migrations.

Important status callouts
- Routed frontend pages: `Dashboard`, `Accounts`, `Trades`, `Journal`.
- Unrouted prototype pages exist (`overview`, `analytics`) and use `frontend/src/mock/data.ts`.
- Legacy backend endpoints (`/metrics/*`, `/trades`) are implemented but not currently used by routed pages.
- No custom React hooks directory; page logic is mostly in component-local hooks.

## 6) End-to-End Data Flow (Detailed)
How the app starts
1. Postgres container starts (`docker-compose.yml`).
2. Backend boots (`uvicorn app.main:app`) and runs `init_db()` on startup.
3. Frontend boots (`vite`) and mounts router.
4. App shell immediately requests accounts from backend.

### Accounts loading flow
1. Frontend calls `accountsApi.getAccounts()` from `frontend/src/lib/api.ts`.
2. Request goes to `GET /api/accounts`.
3. Backend builds `ProjectXClient.from_env()`.
4. Backend calls ProjectX `POST /api/Account/search` with `{ "onlyActiveAccounts": true }`.
5. Client normalization maps account fields and filters `canTrade === false`.
6. Response returned as `[{ id, name, balance, status }]`.
7. Frontend caches this list for 30 seconds (`ACCOUNTS_CACHE_TTL_MS`) and stores active account id in:
   - URL query: `?account=<id>`
   - `localStorage`: `topsignal.activeAccountId`

### Trade events loading flow
1. Frontend calls `GET /api/accounts/{account_id}/trades` with `limit` and optional UTC `start/end`.
2. Backend validates account id and time range.
3. Backend runs `ensure_trade_cache_for_request(...)`:
   - If request is a single UTC day and that day is stale/missing, run day sync logic.
   - Otherwise, sync only when `refresh=true` or no local trades exist.
4. Sync path uses `ProjectXClient.fetch_trade_history(...)` against `POST /api/Trade/search`.
5. Upstream rows are normalized:
   - side, size, price, timestamp, fees, pnl, order/source ids.
   - voided rows are excluded.
6. Deduping/upsert:
   - first by `(account_id, source_trade_id)` when available.
   - fallback by `(account_id, order_id, trade_timestamp)`.
   - DB constraints enforce uniqueness.
7. Query returns rows from `projectx_trade_events` where `pnl IS NOT NULL` (closed rows only), sorted newest first.
8. Frontend renders trades table.

### Metrics/statistics generation flow
1. Frontend calls summary and calendar endpoints:
   - `GET /api/accounts/{id}/summary`
   - `GET /api/accounts/{id}/pnl-calendar`
2. Backend loads event samples from `projectx_trade_events`.
3. Backend computes:
   - PnL core, win/loss, drawdown, activity, hold-duration metrics (`compute_trade_summary`).
   - daily buckets (`compute_daily_pnl_calendar`).
4. Frontend computes additional derived metrics from backend responses and trade rows:
   - direction split, payoff extras, swing extras, sustainability score, activity pacing.
5. UI cards update.

### Caching/sync behavior flow
1. Manual sync button (`AppShell`, `TradesPage`) calls `POST /api/accounts/{id}/trades/refresh`.
2. Refresh fetches windows in chunks (`PROJECTX_SYNC_CHUNK_DAYS`, default 90 days).
3. First sync default lookback is env-controlled (`PROJECTX_INITIAL_LOOKBACK_DAYS`, default 365).
4. Incremental sync uses 5-minute overlap to reduce missed edge events.
5. Single-day cache completeness is tracked in `projectx_trade_day_syncs`:
   - today: always refresh when single-day requested.
   - yesterday: refresh if stale (`PROJECTX_YESTERDAY_REFRESH_MINUTES`, default 180) or partial.
   - older days: skip provider call if marked complete unless explicit refresh.
6. Auth token is cached in backend memory until expiry (with 60-second safety window).

What happens on refresh/date/account changes
- Account change:
  - URL/localStorage updates.
  - pages reload data for that account.
- Dashboard range change:
  - summary/calendar/metrics-trade requests rerun.
  - selected calendar day filter clears.
- Dashboard day click:
  - sends exact UTC day `start/end` to trades endpoint.
- Trades page date change:
  - converts selected date to UTC start/end and refetches.
- Sync completion event:
  - `AppShell` dispatches `account-trades-synced` window event.
  - Dashboard listens and reloads for same account.

## 7) External API Integrations
### API 1: ProjectX / TopstepX Gateway
Purpose in app
- Account discovery and broker trade history ingestion.

Base URL configuration
- Primary: `PROJECTX_API_BASE_URL`
- Aliases accepted: `PROJECTX_BASE_URL`, `PROJECTX_GATEWAY_URL`, `TOPSTEP_API_BASE_URL`, `TOPSTEPX_API_BASE_URL`

Auth method
1. `POST /api/Auth/loginKey` with `{ userName, apiKey }`.
2. Extract bearer token (`token`/`accessToken`/`jwt`/`jwtToken`).
3. Add `Authorization: Bearer <token>` to authenticated requests.
4. On 401: clear token cache and retry once.

Endpoints called by this project
| Upstream endpoint | Method | App purpose | Request params/body used | Response fields depended on |
|---|---|---|---|---|
| `/api/Auth/loginKey` | POST | acquire token | `userName`, `apiKey` | token fields + optional expiry fields |
| `/api/Account/search` | POST | list active accounts | `{ onlyActiveAccounts: true }` | `id/accountId`, name variants, balance variants, status variants, `canTrade` |
| `/api/Trade/search` | POST | trade event sync | `accountId`, `startTimestamp`, optional `endTimestamp`, optional `limit`, `offset` | timestamp variants, side variants, size/price/fees/pnl variants, ids, `voided`, status |

Request parsing/normalization details
- Timestamp parsing supports:
  - ISO with `Z`
  - ISO with variable fractional precision
  - offsets with/without colon
  - epoch seconds/milliseconds
- Side normalization:
  - strings: BUY/LONG/BID -> BUY, SELL/SHORT/ASK -> SELL
  - numeric: 0 -> BUY, 1 -> SELL
- Voided rows are skipped.

Error handling/retries/fallback
- HTTP errors from upstream are wrapped with code/message.
- 401 gets one retry after token reset.
- Non-JSON upstream responses raise explicit client error.
- Missing env config raises local configuration error.

Known integration limitations from code
- No explicit rate-limit/backoff strategy beyond basic retry-on-401.
- No websocket/SignalR integration in active routes (polling helper exists but is not wired to API routes).
- "All-time" UI view only includes what has been synced locally (first sync lookback default is 365 days unless configured).

## 8) Backend API (My App's API)
Route table
| Method | Route | Purpose | Query / Body | Response shape | Frontend usage | Notes |
|---|---|---|---|---|---|---|
| GET | `/health` | liveness check | none | `{status}` | not used | simple health endpoint |
| GET | `/trades` | legacy trade rows from `trades` table | `limit`, `account_id` | `TradeOut[]` | not used | legacy pipeline |
| GET | `/metrics/summary` | legacy summary metrics | `account_id` | `SummaryMetricsOut` | not used | legacy pipeline |
| GET | `/metrics/pnl-by-hour` | legacy hourly PnL | `account_id` | `HourPnlOut[]` | not used | legacy pipeline |
| GET | `/metrics/pnl-by-day` | legacy weekday PnL | `account_id` | `DayPnlOut[]` | not used | legacy pipeline |
| GET | `/metrics/pnl-by-symbol` | legacy symbol metrics | `account_id` | `SymbolPnlOut[]` | not used | legacy pipeline |
| GET | `/metrics/streaks` | legacy streak stats | `account_id` | `StreakMetricsOut` | not used | legacy pipeline |
| GET | `/metrics/behavior` | legacy behavior stats | `account_id` | `BehaviorMetricsOut` | not used | legacy pipeline |
| GET | `/api/accounts` | list ProjectX accounts | none | `ProjectXAccountOut[]` | AppShell + all routed pages | upstream call |
| GET | `/api/accounts/{account_id}/journal` | list journal entries | `start_date`, `end_date`, `mood`, `q`, `include_archived`, `limit`, `offset` | `{ items, total }` | JournalPage | `limit` 1..200, `offset>=0` |
| POST | `/api/accounts/{account_id}/journal` | create journal entry | body: `entry_date`, `title`, `mood`, `tags`, `body` | `JournalEntryOut` | JournalPage | returns `201` |
| PATCH | `/api/accounts/{account_id}/journal/{entry_id}` | update/archive journal entry | partial body fields | `JournalEntryOut` | JournalPage | archive-only fast path supported |
| POST | `/api/accounts/{account_id}/trades/refresh` | force sync from ProjectX | `start`, `end` | `{ fetched_count, inserted_count }` | AppShell, TradesPage | validates range |
| GET | `/api/accounts/{account_id}/trades` | list local trade events | `limit`, `start`, `end`, `symbol`, `refresh` | `ProjectXTradeOut[]` | Dashboard, TradesPage | returns closed rows (`pnl != null`) |
| GET | `/api/accounts/{account_id}/summary` | summary metrics from local events | `start`, `end`, `refresh` | `ProjectXTradeSummaryOut` | Dashboard, TradesPage | auto-sync if local empty |
| GET | `/api/accounts/{account_id}/pnl-calendar` | day buckets from local events | `start`, `end`, `all_time`, `refresh` | `ProjectXPnlCalendarDayOut[]` | Dashboard | default window is last 6 months if no dates and `all_time=false` |

Example requests/responses
```http
GET /api/accounts
```

```json
[
  {
    "id": 13048312,
    "name": "Combine Account",
    "balance": 50743.22,
    "status": "ACTIVE"
  }
]
```

```http
GET /api/accounts/13048312/trades?limit=200&start=2026-02-01T00:00:00.000Z&end=2026-02-21T23:59:59.999Z
```

```json
[
  {
    "id": 991,
    "account_id": 13048312,
    "contract_id": "CON.F.US.MES.H26",
    "symbol": "CON.F.US.MES.H26",
    "side": "SELL",
    "size": 3.0,
    "price": 6858.75,
    "timestamp": "2026-02-06T11:12:09+00:00",
    "fees": 2.22,
    "pnl": 1312.5,
    "order_id": "2397509693",
    "source_trade_id": "2074009852"
  }
]
```

```http
POST /api/accounts/13048312/trades/refresh?start=2026-02-01T00:00:00.000Z&end=2026-02-21T23:59:59.999Z
```

```json
{
  "fetched_count": 412,
  "inserted_count": 37
}
```

```http
POST /api/accounts/13048312/journal
Content-Type: application/json
```

```json
{
  "entry_date": "2026-02-21",
  "title": "Opening plan",
  "mood": "Focused",
  "tags": ["nq", "discipline"],
  "body": "Wait for pullback confirmation."
}
```

Error response examples
```json
{
  "detail": "account_id must be a positive integer"
}
```

```json
{
  "detail": "start must be before end"
}
```

```json
{
  "detail": "ProjectX request failed (401): Unauthorized"
}
```

Status code notes
- `200`: successful reads/updates.
- `201`: journal entry creation.
- `400`: validation failures (range checks, empty update payload, invalid ids).
- `404`: journal entry not found for account.
- `500`: missing local server config (for example missing env vars).
- `502`: upstream ProjectX/API gateway errors.

## 9) Data Storage and Persistence
Where data is stored
| Location | Data | Persisted? | Keying |
|---|---|---|---|
| Postgres `projectx_trade_events` | normalized trade events + raw upstream payload | yes | account + source trade id and/or order+timestamp |
| Postgres `projectx_trade_day_syncs` | per-account per-day sync completeness | yes | `(account_id, trade_date)` |
| Postgres `journal_entries` | account journal records | yes | `id`, `account_id` |
| Postgres `accounts` | legacy account registry | yes | `(provider, external_id)` |
| Postgres `trades` | legacy app trade table | yes | `id` + `account_id` |
| Backend process memory | ProjectX bearer token cache | no | single in-process cache |
| Frontend process memory | accounts API TTL cache + component state | no | request lifecycle |
| Browser `localStorage` | `topsignal.activeAccountId` | yes (browser) | account id string |

Persisted vs temporary
- Persisted:
  - event rows, day-sync rows, journal entries, legacy tables.
- Temporary:
  - auth token cache in backend process.
  - in-memory frontend cache and component state.

Caching strategy and invalidation
- Frontend account list cache: 30s TTL + in-flight request dedupe.
- Day-level trade cache status:
  - today always refreshed for single-day pulls.
  - yesterday refresh based on staleness threshold.
  - older days reused if marked complete.
- Manual refresh endpoints bypass stale cache concerns.

Dedupe/upsert behavior
- Incoming events sorted by `(timestamp, order_id)`.
- Existing rows looked up by source trade id first, fallback key second.
- Existing row gets updated; missing row inserted.
- DB constraints guard duplicates:
  - unique `(account_id, source_trade_id)`
  - unique `(account_id, order_id, trade_timestamp)`

## 10) Data Models / Types / Schema
### Database model: `projectx_trade_events` (active analytics source)
| Field | Type | Meaning | Source | Notes |
|---|---|---|---|---|
| `id` | bigserial | local PK | DB | internal |
| `account_id` | bigint | upstream account id | ProjectX | required |
| `contract_id` | text | contract identifier | ProjectX | required |
| `symbol` | text nullable | symbol text | ProjectX | falls back to `contract_id` in serializers |
| `side` | text | execution side | ProjectX normalized | constrained to `BUY/SELL/UNKNOWN` |
| `size` | numeric(18,6) | fill quantity | ProjectX | required |
| `price` | numeric(18,6) | fill price | ProjectX | required |
| `trade_timestamp` | timestamptz | event timestamp | ProjectX normalized UTC | required |
| `fees` | numeric(18,6) | per-row fee value | ProjectX | serialized as round-trip for rows with PnL |
| `pnl` | numeric(18,6) nullable | broker realized PnL | ProjectX | null usually means open/half-turn row |
| `order_id` | text | order id | ProjectX | required |
| `source_trade_id` | text nullable | broker trade/execution id | ProjectX | preferred dedupe key |
| `status` | text nullable | upstream status | ProjectX | optional |
| `raw_payload` | jsonb | original upstream row | ProjectX | used for voided filtering and debugging |
| `created_at` | timestamptz | row insertion time | DB | default `now()` |

### Database model: `projectx_trade_day_syncs`
| Field | Type | Meaning | Source | Notes |
|---|---|---|---|---|
| `id` | bigserial | local PK | DB | internal |
| `account_id` | bigint | account | app | required |
| `trade_date` | date | UTC trade day | app | required |
| `sync_status` | text | `partial` or `complete` | app | cache completeness flag |
| `last_synced_at` | timestamptz | last provider sync time | app | used for yesterday staleness logic |
| `row_count` | integer nullable | local rows for that day | app | diagnostic |
| `created_at` | timestamptz | created time | DB | default |
| `updated_at` | timestamptz | updated time | app | updated on each sync |

### Database model: `journal_entries`
| Field | Type | Meaning | Source | Notes |
|---|---|---|---|---|
| `id` | bigserial | entry id | DB | internal |
| `account_id` | bigint | account scope | app | required |
| `entry_date` | date | journal date | client payload | required |
| `title` | text | entry title | client payload | required, max 160 chars (service-level) |
| `mood` | text | mood category | client payload | enum-like check constraint |
| `tags` | text[] | normalized tags | client payload | lowercased, deduped, max limits enforced |
| `body` | text | free-form notes | client payload | max 20k chars (service-level) |
| `is_archived` | boolean | soft archive flag | client payload/app | default false |
| `created_at` | timestamptz | created time | DB | default |
| `updated_at` | timestamptz | last update | app/DB | updated on writes |

### Database models: legacy tables
`accounts`
| Field | Type | Meaning | Source | Notes |
|---|---|---|---|---|
| `id` | bigint | account PK | DB | unique row id |
| `provider` | text | broker/provider name | app | required |
| `external_id` | text | provider account id | app | unique with provider |
| `name` | text nullable | display name | app | optional |
| `created_at` | timestamptz | created time | DB | default |

`trades`
| Field | Type | Meaning | Source | Notes |
|---|---|---|---|---|
| `id` | bigint | trade PK | DB | internal |
| `account_id` | bigint FK | account link | app | required |
| `symbol` | text | instrument | app | required |
| `side` | text | `LONG/SHORT` | app | check constraint |
| `opened_at` | timestamptz | open timestamp | app | required |
| `closed_at` | timestamptz nullable | close timestamp | app | null for open rows |
| `qty` | numeric(18,6) | size | app | required |
| `entry_price` | numeric(18,6) | entry | app | required |
| `exit_price` | numeric(18,6) nullable | exit | app | optional |
| `pnl` | numeric(18,2) nullable | realized pnl | app | optional |
| `fees` | numeric(18,2) nullable | fees | app | optional |
| `notes` | text nullable | notes | app | optional |
| `is_rule_break` | bool | rule break flag | app | default false |
| `rule_break_type` | text nullable | rule break label | app | optional |
| `created_at` | timestamptz | created time | DB | default |

### Key API/TypeScript models used by routed frontend
`AccountInfo`
| Field | Type | Meaning | Source | Notes |
|---|---|---|---|---|
| `id` | number | account id | backend `/api/accounts` | positive int |
| `name` | string | display name | backend | fallback to `Account <id>` if needed |
| `balance` | number | account balance/equity | backend normalized upstream fields | can come from different upstream keys |
| `status` | string | account status text | backend normalized | derived from upstream status/canTrade |

`AccountTrade`
| Field | Type | Meaning | Source | Notes |
|---|---|---|---|---|
| `id` | number | local row id | backend trades endpoint | from DB |
| `account_id` | number | account id | backend | |
| `contract_id` | string | contract identifier | backend | |
| `symbol` | string | symbol text | backend | fallback to contract if missing upstream |
| `side` | string | execution side | backend | `BUY/SELL/UNKNOWN` |
| `size` | number | quantity | backend | |
| `price` | number | fill price | backend | |
| `timestamp` | ISO datetime string | event time UTC | backend | |
| `fees` | number | fee shown in UI | backend | doubled for rows with non-null pnl |
| `pnl` | number \| null | broker pnl | backend | null rows excluded from routed list endpoints |
| `order_id` | string | order id | backend | |
| `source_trade_id` | string \| null | provider trade id | backend | |
| `mfe` / `mae` | number \| null optional | favorable/adverse excursion | currently absent in backend payload | UI formulas can consume if added |

`AccountSummary` and `AccountPnlCalendarDay`
- `AccountSummary` includes the full server metric payload documented in Section 11.
- `AccountPnlCalendarDay` fields: `date`, `trade_count`, `gross_pnl`, `fees`, `net_pnl`.

`JournalEntry`
| Field | Type | Meaning | Source | Notes |
|---|---|---|---|---|
| `id` | number | entry id | backend journal routes | |
| `account_id` | number | account scope | backend | |
| `entry_date` | `YYYY-MM-DD` | entry date | backend | |
| `title` | string | title | backend | |
| `mood` | union | Focused/Neutral/Frustrated/Confident | backend validation | |
| `tags` | string[] | normalized tags | backend | deduped/lowercased |
| `body` | string | free text | backend | |
| `is_archived` | boolean | soft archive state | backend | |
| `created_at`/`updated_at` | ISO datetime | timestamps | backend | |

## 11) Metrics and Trading Analytics Logic (Very Detailed)
This project has three metric layers:
1. Active backend metrics from `projectx_trade_events` (`projectx_metrics.py`).
2. Legacy backend metrics from `trades` table (`metrics.py`).
3. Frontend-derived analytics from backend responses (`frontend/src/utils/*`).

### 11.1 Active backend summary metrics (`compute_trade_summary`)
Input set
- Ordered `TradeMetricSample[]` rows with: `timestamp`, `pnl`, `fees`, `order_id`, `symbol`, `side`, `size`, `price`.
- `pnl=null` rows are treated as open/half-turn rows.

Core normalization rules
- Realized value per row: `realized = pnl if pnl != null else 0`.
- Effective fee per row: `fee = fees if pnl != null else 0`.
- Trade-event fee serialization currently doubles stored `fees` for closed rows to reflect round-trip costs.

Metric formulas and behavior
| Metric | Formula / Logic | Inputs | Realized vs unrealized | Time grouping / notes |
|---|---|---|---|---|
| `realized_pnl` | sum of per-row realized values | row `pnl` | realized only | all loaded rows |
| `gross_pnl` | same as `realized_pnl` | row `pnl` | realized only | alias in current implementation |
| `fees` | sum effective fees | row `fees`, `pnl` nullability | fee on closed rows only | all loaded rows |
| `net_pnl` | sum `(realized - effective_fee)` | `pnl`, `fees` | realized only | all loaded rows |
| `trade_count` | count of rows where `pnl != null` | row `pnl` | realized only | closed-trade count |
| `execution_count` | total row count | all rows | includes open/half-turn rows | event count |
| `half_turn_count` | unique non-null `order_id` count else execution count | `order_id` | n/a | rough order count |
| `win_count` | count closed net values > 0 | closed net values | realized | |
| `loss_count` | count closed net values < 0 | closed net values | realized | |
| `breakeven_count` | closed count - wins - losses | closed net values | realized | |
| `win_rate` | `(wins / trade_count) * 100` | closed net values | realized | |
| `profit_factor` | `sum(gross wins) / abs(sum(gross losses))` | closed gross pnl values | realized | 0 if no losses |
| `avg_win` | mean of positive closed net values | closed net values | realized | |
| `avg_loss` | mean of negative closed net values | closed net values | realized | negative value |
| `expectancy_per_trade` | mean closed net value | closed net values | realized | |
| `tail_risk_5pct` | mean of worst `ceil(5%)` closed net trades, capped to <=0 | closed net values | realized | downside tail proxy |
| `max_drawdown` | min of `(equity - running_peak)` over ordered net path | ordered net values | realized | cumulative by event order |
| `average_drawdown` | mean trough drawdown of drawdown episodes | drawdown episodes | realized | |
| `risk_drawdown_score` | `abs(max_dd)/max(peak_equity, abs(max_dd), 1) * 100` | drawdown episode stats | realized | 0..100 style |
| `max_drawdown_length_hours` | max duration of drawdown episode | drawdown episodes | realized | hours |
| `recovery_time_hours` | trough->recovery duration for max drawdown episode | max DD episode | realized | unrecovered uses last timestamp |
| `average_recovery_length_hours` | average trough->recovery duration across recovered episodes | recovered episodes | realized | |
| `green_days` | number of days with daily net > 0 | daily net buckets | realized | UTC day bucket |
| `red_days` | number of days with daily net < 0 | daily net buckets | realized | UTC day bucket |
| `flat_days` | active days - green - red | daily net buckets | realized | UTC day bucket |
| `active_days` | unique UTC days in loaded sample | sample timestamps | includes days that net to 0 | UTC |
| `day_win_rate` | `(green_days / active_days) * 100` | day counts | realized | |
| `avg_trades_per_day` | `trade_count / active_days` | trade_count, active_days | realized | |
| `efficiency_per_hour` | `net_pnl / active_hours` | net_pnl, active_hours | realized | active_hours from first->last trade span per day |
| `profit_per_day` | `net_pnl / active_days` | net_pnl, active_days | realized | |
| `avg_win_duration_minutes` | avg hold duration of closed winners | inferred lot matching + closed winners | realized only | symbol-scoped LIFO lot matching |
| `avg_loss_duration_minutes` | avg hold duration of closed losers | inferred lot matching + closed losers | realized only | symbol-scoped LIFO lot matching |

Common pitfalls/edge handling in this pipeline
- No unrealized PnL metric; null-`pnl` rows contribute 0 realized PnL.
- Fees for null-`pnl` rows are intentionally ignored in net metrics.
- Hold duration depends on available in-range lot context; close-only rows without local opens can be skipped.
- All day grouping is UTC.

### 11.2 Active backend daily calendar (`compute_daily_pnl_calendar`)
| Metric | Logic |
|---|---|
| `trade_count` | count of rows with non-null `pnl` on each UTC day |
| `gross_pnl` | sum of realized values for closed rows on day |
| `fees` | sum of effective fees for closed rows on day |
| `net_pnl` | sum of `(realized - fee)` for closed rows on day |

Notes
- Rows with `pnl=null` are excluded from calendar buckets.
- Returned sorted by date ascending.

### 11.3 Legacy backend metrics (`services/metrics.py`)
These routes exist and are tested but are not used by routed frontend pages.

Key formulas
- Net trade PnL: if `trade.pnl` exists, use it; else fallback to `qty * (exit-entry) * direction`; then subtract fees.
- `direction = +1` for LONG, `-1` for SHORT.
- Profit factor, expectancy, win rate, hold-time means, drawdown, streak stats, behavior metrics are computed from closed trades.

Important limitation
- Fallback PnL path has explicit TODO: no contract multiplier support yet.

### 11.4 Frontend-derived analytics (dashboard extras)
Computed from backend summary + trades + daily calendar.

| Metric | Formula / logic | Input fields | Current status |
|---|---|---|---|
| Drawdown % of net | `abs(max_drawdown)/abs(net_pnl)*100` | summary max drawdown, net pnl | implemented |
| Breakeven win rate | `abs(avg_loss)/(abs(avg_win)+abs(avg_loss))*100` | avg win/loss | implemented |
| Direction % | long/short trade counts percentage split | closed trades side mapping | implemented |
| Direction expectancy/PF/avg win/loss | computed separately for long vs short groups | `trade.side`, `trade.pnl` | implemented |
| Direction large-loss rate | losses beyond `2 * abs(side avg loss)` | directional PnL arrays | implemented |
| Swing extras | median day pnl, avg green/red, red-day %, nuke ratio, green/red size ratio | calendar daily net + summary profit/day | implemented |
| Stability score | `clamp(100 - abs(worst_day_pct_of_net),0,100)` | worst day % metric | implemented |
| Sustainability score | mean of Swing/Outlier/Risk subscores | summary + swing metrics | implemented |
| WR cushion | `current_win_rate - breakeven_win_rate` | summary + breakeven metric | implemented |
| P95 loss | 95th percentile of loss magnitudes (needs >=5 losses) | trade pnl distribution | implemented |
| Capture | `avgWin / avg(MFE of winners)` | optional `trade.mfe` | partial (usually N/A now) |
| Containment | `abs(avgLoss) / avg(abs(MAE of losers))` | optional `trade.mae` | partial (usually N/A now) |

Directional side assumption used in frontend
- `SELL` is interpreted as closing long exposure.
- `BUY` is interpreted as closing short exposure.
- `LONG`/`SHORT` side strings are also accepted directly.

Planned/not fully implemented metrics behavior
- MFE/MAE-based capture/containment formulas exist, but backend does not currently send `mfe`/`mae`, so these render as missing in normal flow.

## 12) Configuration and Environment Variables
### Backend env vars
| Variable | Required | Default | Description | Consumed in |
|---|---|---|---|---|
| `DATABASE_URL` | yes | none | SQLAlchemy connection URL | `backend/app/db.py` |
| `PROJECTX_API_BASE_URL` | yes (or alias) | none | ProjectX base URL | `backend/app/services/projectx_client.py` |
| `PROJECTX_USERNAME` | yes (or alias) | none | ProjectX username | `backend/app/services/projectx_client.py` |
| `PROJECTX_API_KEY` | yes (or alias) | none | ProjectX API key | `backend/app/services/projectx_client.py` |
| `PROJECTX_INITIAL_LOOKBACK_DAYS` | no | `365` | first-sync lookback window | `backend/app/services/projectx_trades.py` |
| `PROJECTX_SYNC_CHUNK_DAYS` | no | `90` | sync chunk size in days | `backend/app/services/projectx_trades.py` |
| `PROJECTX_DAY_SYNC_LIMIT` | no | `1000` | page limit for single-day sync | `backend/app/services/projectx_trades.py` |
| `PROJECTX_YESTERDAY_REFRESH_MINUTES` | no | `180` | freshness threshold for yesterday | `backend/app/services/projectx_trades.py` |

Accepted aliases in code
- Base URL aliases: `PROJECTX_BASE_URL`, `PROJECTX_GATEWAY_URL`, `TOPSTEP_API_BASE_URL`, `TOPSTEPX_API_BASE_URL`
- Username aliases: `PROJECTX_USER_NAME`, `TOPSTEP_USERNAME`, `TOPSTEPX_USERNAME`
- API key aliases: `TOPSTEP_API_KEY`, `TOPSTEPX_API_KEY`, `PX_API_KEY`

Frontend env vars
| Variable | Required | Default | Description | Consumed in |
|---|---|---|---|---|
| `VITE_API_BASE_URL` | no | `http://localhost:8000` | frontend API base URL | `frontend/src/lib/api.ts` |

Vars present in local backend `.env` but not used by current backend code
- `PROJECTX_USER_HUB_URL`
- `PROJECTX_MARKET_HUB_URL`

Frontend vs backend env behavior
- Frontend requires `VITE_` prefix to expose env vars at build time.
- Backend reads env at runtime via `python-dotenv` + `os.getenv`.

Secrets and gitignore guidance
- Keep broker credentials only in local env files (not committed).
- Ensure `.gitignore` contains at minimum:
  - `backend/.env`
  - `backend/.venv/`
  - `frontend/node_modules/`
  - `node_modules/`
  - `frontend/.env.local` (recommended add)

Safe example (`backend/.env`)
```env
DATABASE_URL=postgresql+psycopg://topsignal:topsignal_password@localhost:5432/topsignal
PROJECTX_API_BASE_URL=https://api.topstepx.com
PROJECTX_USERNAME=your_username
PROJECTX_API_KEY=your_api_key
PROJECTX_INITIAL_LOOKBACK_DAYS=365
PROJECTX_SYNC_CHUNK_DAYS=90
PROJECTX_DAY_SYNC_LIMIT=1000
PROJECTX_YESTERDAY_REFRESH_MINUTES=180
```

## 13) Setup and Local Development
Prerequisites
- Docker Desktop (or equivalent Docker engine)
- Node.js + npm
- Python 3.x (for backend virtualenv)

Install dependencies
```powershell
# repo root
npm install

# backend deps
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd ..

# frontend deps
cd frontend
npm install
cd ..
```

Start database
```powershell
docker compose up -d
```

Initialize schema and migrations (manual SQL flow currently)
```powershell
Get-Content .\db\schema.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
Get-Content .\db\migrations\20260220_add_rule_break_fields.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
Get-Content .\db\migrations\20260220_add_projectx_trade_events.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
Get-Content .\db\migrations\20260221_add_projectx_trade_day_syncs.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
Get-Content .\db\migrations\20260221_add_journal_entries.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
```

Run backend only
```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Run frontend only
```powershell
cd frontend
npm run dev
```

Run both together from root
```powershell
npm run dev
```

Build/test/lint commands
```powershell
# backend tests
cd backend
.\.venv\Scripts\python.exe -m pytest -q

# frontend tests
cd ..\frontend
npm test

# frontend lint
npm run lint

# frontend build
npm run build
```

Current test status from repo inspection
- Backend: `42 passed` (pytest).
- Frontend: `23 passed` (vitest).
- Backend emits deprecation warnings (`@app.on_event`, Pydantic class config style).

## 14) Usage Walkthrough
1. Start DB/backend/frontend.
2. Open `http://localhost:5173`.
3. Choose active account in header selector.
4. Click `Sync Latest Trades` in header to pull/store newest events.
5. Dashboard:
   - use preset range buttons or custom date range.
   - read summary cards, direction/payoff/swing/risk/sustainability sections.
   - use PnL calendar to click a day and filter recent trades to that UTC day.
6. Trades page:
   - set start/end date, symbol search text, and result limit.
   - click `Sync Latest` to refresh range data.
   - page through results.
7. Accounts page:
   - inspect account status and balances.
   - click row to set active account.
8. Journal page:
   - filter by date/mood/text/archived.
   - create new entry.
   - edit title/mood/tags/body with debounced autosave.
   - archive/unarchive selected entry.

How to view active accounts only
- Backend always requests `onlyActiveAccounts: true` from ProjectX and also filters `canTrade=false`.
- There is no UI toggle for inactive accounts in current implementation.

How metrics refresh
- Manual: sync buttons trigger refresh endpoint.
- Automatic:
  - summary/calendar load from local DB.
  - if local trades are missing, backend performs initial sync.
  - single-day trade requests can trigger day-specific sync if cache is stale/incomplete.

If data looks wrong
- Fees look lower/higher than expected:
  - stored fees are normalized to round-trip on closed rows in serialization.
- Missing today/yesterday rows:
  - run manual sync, then retry page.
- Suspected duplicates:
  - check upstream row ids and local unique-key rules (`source_trade_id` / `order_id+timestamp`).
- Old ranges missing:
  - increase `PROJECTX_INITIAL_LOOKBACK_DAYS` or run refresh with explicit `start`.

## 15) Error Handling and Edge Cases
| Scenario | Current behavior | Gaps / notes |
|---|---|---|
| Upstream API failure | backend wraps as `ProjectXClientError` and returns `502` (or `500` for missing local config) | no retry/backoff except 401 token retry |
| Empty upstream responses | normalization returns empty arrays; UI shows empty states | expected behavior |
| Partial fills / open legs (`pnl=null`) | treated as zero realized; excluded from trade_count and calendar counts | no synthetic PnL reconstruction |
| Missing fees | defaults to `0.0`; open-leg fees excluded from net summary | assumes broker fee fields are trustworthy |
| Duplicate events | dedupe + upsert + DB unique constraints | still depends on upstream id quality |
| Timezone boundaries | all date filters converted/compared in UTC; calendar grouped by UTC date | no account-local timezone mode |
| Stale cache data | day-sync table + refresh heuristics for today/yesterday + manual refresh endpoint | no global scheduler |
| Invalid account IDs | `400` for `account_id <= 0` | positive but nonexistent account ids can still trigger upstream errors/empty data |
| Backend unavailable | frontend fetch throws and surfaces readable message in page UI | no offline queue/retry UX |
| Journal invalid payload | service validation returns `400`; missing entry returns `404` | no server-side conflict resolution |

## 16) Performance and Design Decisions
Why split frontend/backend
- Keeps ProjectX credentials server-side.
- Centralizes normalization/dedupe logic once.
- Lets frontend focus on rendering and local interactions.

Why cache/persist trade data
- Avoid repeated heavy upstream reads.
- Support fast dashboard interactions from local DB.
- Enable deterministic metric computation from a stable local dataset.

Optimizations already implemented
- Frontend accounts cache with TTL + in-flight request dedupe.
- Backend token cache with expiry and safety window.
- Sync windows chunked by days to reduce oversized fetch risk.
- Incremental sync overlap (5 minutes) for late-arriving edges.
- `load_only(...)` in trade list query to reduce ORM column load.
- Day-level sync completeness table to avoid unnecessary provider calls.

Tradeoffs chosen
- Simplicity over distributed scale:
  - single-process token cache, no background worker queue.
- Freshness vs provider cost:
  - reads mostly from DB; refresh explicit or cache-triggered.
- Manual SQL migrations over migration framework:
  - easy to inspect locally, but more operational overhead.

What could break at larger scale
- Concurrent sync requests across multiple app instances could duplicate upstream workload.
- No distributed locking or shared cache for token/sync state.
- No event streaming pipeline; large historical backfills are still request-driven.
- Frontend trade filtering is partly client-side, which can miss matches if fetch limit is too low.

How to improve for scale
- Add background sync worker and queue.
- Add migration tooling (Alembic) with versioned upgrades.
- Move token/sync locks to shared infrastructure (Redis, DB locks).
- Add server-side pagination and stronger query APIs.

## 17) Known Issues / Current Limitations
- `npm run stop` references `stop.ps1`, but `stop.ps1` is missing in repo root.
- `db/README.md` references `db/seed.sql`, but `db/seed.sql` is not present.
- No committed `.env.example` template; setup depends on manual env creation.
- Routed frontend does not consume legacy backend routes (`/metrics/*`, `/trades`).
- `overview` and `analytics` pages/components are present but unrouted and mock-data based.
- `pages/trades/components/*` contains mock-data table/filter/drawer components that are not wired into the routed `TradesPage`.
- Dashboard summary and calendar can both trigger sync when local cache is empty, causing redundant upstream calls on first load.
- MFE/MAE-dependent payoff metrics exist in frontend formulas but backend does not currently provide MFE/MAE fields.
- Direction/payoff extras rely on at most `METRIC_TRADE_LIMIT=1000` rows from frontend fetch; large ranges can show missing directional extras.
- CORS is local-only oriented (`localhost`/`127.0.0.1` regex + explicit `http://localhost:5173`).
- No application auth or user isolation; account id is selected client-side.
- Upstream rate limits are not explicitly modeled with backoff/circuit-breaker logic.
- Backend tests report deprecation warnings (`FastAPI on_event`, Pydantic class config style).

## 18) Future Improvements
1. Add `.env.example` files for backend/frontend and tighten onboarding.
2. Introduce Alembic migrations and remove manual SQL migration steps.
3. Add background sync scheduler for automatic day refresh and backfills.
4. Expose websocket/poll stream endpoint if near-real-time updates are needed.
5. Persist and serve MFE/MAE so capture/containment metrics become fully usable.
6. Unify legacy and active metric pipelines (or remove legacy endpoints).
7. Add server-side pagination and richer trade queries to reduce client-side filtering limits.
8. Add authentication and per-user account isolation.
9. Add observability: structured logging, sync latency metrics, and endpoint metrics.
10. Expand test suite to true API integration tests with TestClient + DB fixtures.

## 19) Interview Q&A Prep (Very Important)
1. **Q: Why did you split this into React + FastAPI + Postgres instead of a pure frontend app?**  
   **A:** I needed to keep broker credentials server-side and own the normalization/metric logic. FastAPI centralizes ProjectX calls, dedupe, and analytics so the frontend consumes a stable local API.

2. **Q: How does trade data move from ProjectX into your UI?**  
   **A:** The backend calls `/api/Trade/search`, normalizes rows, filters voided events, dedupes/upserts into `projectx_trade_events`, then summary/trades/calendar routes query Postgres and return typed payloads to React pages.

3. **Q: How do you prevent duplicate trade events?**  
   **A:** I use service-level dedupe keyed by `(account_id, source_trade_id)` with fallback `(account_id, order_id, timestamp)` plus DB unique constraints on both patterns.

4. **Q: How do you handle stale vs fresh data?**  
   **A:** Manual refresh is always available. On read, backend can sync when local data is empty. For single-day queries, I track day completeness in `projectx_trade_day_syncs` and refresh today/yesterday using explicit rules.

5. **Q: What is your drawdown calculation approach?**  
   **A:** I compute cumulative net equity over ordered events, track running peak, and take the minimum `equity - peak` as max drawdown. I also derive episode lengths and recovery timing.

6. **Q: How are fees treated in metrics?**  
   **A:** Fees are only applied on rows with broker-reported realized PnL (`pnl != null`). Closed-row fees are normalized as round-trip in serialization/sample conversion.

7. **Q: What happens with partial/open legs where PnL is null?**  
   **A:** They are stored, but treated as zero realized PnL in summary math and excluded from calendar trade counts and routed trade lists (which filter `pnl IS NOT NULL`).

8. **Q: What's one bug/edge case you explicitly handled?**  
   **A:** Timestamp parsing from ProjectX had variant formats; I added normalization for variable fractional precision and multiple UTC offset formats, then covered it with tests.

9. **Q: How did you validate metric correctness?**  
   **A:** I added backend unit tests for summary/calendar formulas and frontend unit tests for derived metrics like breakeven win rate, sustainability scoring, and payoff extras.

10. **Q: Why do you still have legacy `/metrics/*` endpoints?**  
    **A:** They were from the earlier `trades`-table pipeline. I kept them to preserve earlier work while migrating to the ProjectX event pipeline; they are now a cleanup/refactor target.

11. **Q: What are the most important tradeoffs in your current design?**  
    **A:** Simplicity and local reliability over distributed scale. I intentionally chose manual refresh and local DB caching instead of a complex streaming architecture.

12. **Q: What would you improve first if this became production-facing?**  
    **A:** Add auth and user isolation, migration tooling, background sync workers, and observability. Then I'd unify metrics pipelines and add MFE/MAE persistence for fuller analytics.

13. **Q: How do you handle timezone correctness for daily metrics?**  
    **A:** The system is UTC-normalized end-to-end: date inputs convert to UTC boundaries, backend comparisons are in UTC, and calendar grouping is by UTC day.

14. **Q: Where can this break under higher load?**  
    **A:** Concurrent first-load sync calls can duplicate provider work, there's no distributed lock/token cache, and client-side filtering becomes limiting for large histories without server-side pagination.

15. **Q: What makes this project technically interesting beyond UI?**  
    **A:** The interesting part is the data lifecycle: upstream schema variability, deterministic normalization, dedupe/upsert guarantees, cache completeness tracking, and transparent metric formulas that are test-backed.

