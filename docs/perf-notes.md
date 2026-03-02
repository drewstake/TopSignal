# Dashboard + Accounts Performance Notes

## Scope
- Date: 2026-03-02
- Environment: local dev (`http://localhost:5173` + `http://localhost:8000`)
- Data profile: user `3af8acf1-a33e-4a0c-ae16-0b84d7c518cb` (646 accounts, 876 trade events)
- Capture method: Playwright network timings (TTFB/total/size) + backend `Server-Timing` header (after instrumentation)

## Baseline (Before Fixes)

### Exact Requests Fired

#### Initial dashboard load (10 requests)
1. `GET /api/accounts?show_inactive=true&show_missing=false`
2. `GET /api/accounts/19528587/summary`
3. `GET /api/accounts/19528587/summary?pointsBasis=MNQ`
4. `GET /api/accounts/19528587/summary?pointsBasis=MES`
5. `GET /api/accounts/19528587/summary?pointsBasis=MGC`
6. `GET /api/accounts/19528587/summary?pointsBasis=SIL`
7. `GET /api/accounts/19528587/pnl-calendar?all_time=true`
8. `GET /api/accounts/19528587/trades?limit=200`
9. `GET /api/accounts/19528587/trades?limit=1000`
10. `GET /api/accounts/19528587/journal/days?start_date=2026-03-01&end_date=2026-03-31`

#### Accounts tab open (2 requests)
1. `GET /api/accounts?show_inactive=true&show_missing=false`
2. `GET /api/accounts?show_inactive=true&show_missing=false`

### Initial Dashboard Load: Slowest 3 Requests

| Rank | Endpoint | Payload | Server vs Network | Row Count | Root Cause |
| --- | --- | --- | --- | --- | --- |
| 1 | `GET /api/accounts?show_inactive=true&show_missing=false` | `119,282 B` | `TTFB 3239.5 ms`, download `1.4 ms` | `646` accounts | Endpoint always calls ProjectX `list_accounts()` + sync before responding; large overfetch for dashboard/selectable use case. |
| 2 | `GET /api/accounts/19528587/journal/days?start_date=2026-03-01&end_date=2026-03-31` | `23 B` | `TTFB 3256.3 ms`, download `3.0 ms` | `1` day | Request itself is cheap, but queued behind concurrent dashboard fan-out (connection/thread contention). |
| 3 | `GET /api/accounts/19528587/trades?limit=1000` | `2,063 B` | `TTFB 2942.9 ms`, download `1.8 ms` | `5` trades | Competes with 9 other startup requests; dashboard also does a second trades fetch (`limit=200`) in parallel. |

### Accounts Tab Open: Slowest Requests

| Rank | Endpoint | Payload | Server vs Network | Row Count | Root Cause |
| --- | --- | --- | --- | --- | --- |
| 1 | `GET /api/accounts?show_inactive=true&show_missing=false` | `119,282 B` | `TTFB 6534.1 ms`, download `1.3 ms` | `646` accounts | Non-default accounts query path had no frontend cache; same expensive endpoint re-fetched. |
| 2 | `GET /api/accounts?show_inactive=true&show_missing=false` | `119,282 B` | `TTFB 3288.6 ms`, download `1.6 ms` | `646` accounts | Duplicate in dev due effect re-run + uncached request. |
| 3 | n/a | n/a | n/a | n/a | Only 2 requests were issued for this route transition. |

## End-to-End Trace (Frontend -> Backend -> Query)

1. `GET /api/accounts?...`
- Frontend call sites:
  - `DashboardPage` / `AppShell` -> `accountsApi.getSelectableAccounts()`
  - `AccountsPage` -> `accountsApi.getAccounts({ showInactive: true, showMissing: false })`
- Backend route: `list_projectx_accounts()` in `backend/app/main.py`
- Heavy path:
  - `client.list_accounts()` (provider API call)
  - `sync_projectx_accounts(...)`
  - `_load_last_trade_timestamps(...)` aggregate query on `projectx_trade_events`

2. `GET /api/accounts/{id}/journal/days?...`
- Frontend: `DashboardPage.loadJournalDays()`
- Backend route: `list_projectx_account_journal_days()` in `backend/app/main.py`
- Query: `list_journal_days()` in `backend/app/services/journal.py`
  - `SELECT DISTINCT entry_date ... WHERE user_id/account_id/date range`

3. `GET /api/accounts/{id}/trades?limit=1000`
- Frontend: `DashboardPage.loadMetricsTrades()`
- Backend route: `list_projectx_account_trades()` in `backend/app/main.py`
- Query/service path:
  - `list_trade_events()` in `backend/app/services/projectx_trades.py`
  - `derive_trade_execution_lifecycles(...)` for returned rows

## Database Inspection (EXPLAIN ANALYZE)

- `list_trade_events` query shape confirmed and indexed.
- Added migration index now used by planner:
  - `idx_projectx_trade_events_user_account_closed_ts_nonvoided`
- Migration: `db/migrations/20260302_add_projectx_trade_events_perf_indexes.sql`

## Fixes Implemented

1. Consolidated summary fan-out into one request
- Added `GET /api/accounts/{account_id}/summary-with-point-bases`
- Replaced 5 dashboard summary requests (`summary + 4 pointsBasis variants`) with 1
- Files:
  - `backend/app/main.py`
  - `backend/app/services/projectx_trades.py`
  - `backend/app/projectx_schemas.py`
  - `frontend/src/lib/api.ts`
  - `frontend/src/lib/types.ts`
  - `frontend/src/pages/dashboard/DashboardPage.tsx`

2. Added shared option-aware account caching + in-flight dedupe
- Caches `/api/accounts` by `(showInactive, showMissing)` query key
- `getSelectableAccounts()` now reuses the same cached dataset
- Eliminates Accounts-tab re-fetch right after dashboard
- File: `frontend/src/lib/api.ts`

3. Added lightweight regression instrumentation
- Backend headers:
  - `Server-Timing: app;dur=...`
  - `X-Server-Time-Ms: ...`
- Frontend perf logs:
  - Request start/end in shared API client with total/server/network/bytes
  - Dashboard and Accounts page load markers
- Files:
  - `backend/app/main.py`
  - `frontend/src/lib/api.ts`
  - `frontend/src/pages/dashboard/DashboardPage.tsx`
  - `frontend/src/pages/accounts/AccountsPage.tsx`

4. Added targeted Postgres indexes
- File: `db/migrations/20260302_add_projectx_trade_events_perf_indexes.sql`

## After Fixes (Same Measurement Flow)

### Exact Requests Fired

#### Initial dashboard load (6 requests)
1. `GET /api/accounts?show_inactive=true&show_missing=false`
2. `GET /api/accounts/19528587/summary-with-point-bases`
3. `GET /api/accounts/19528587/pnl-calendar?all_time=true`
4. `GET /api/accounts/19528587/trades?limit=200`
5. `GET /api/accounts/19528587/trades?limit=1000`
6. `GET /api/accounts/19528587/journal/days?start_date=2026-03-01&end_date=2026-03-31`

#### Accounts tab open
- `0` requests

### Initial Dashboard Load (top 3)

| Rank | Endpoint | Payload | Server vs Network | Row Count |
| --- | --- | --- | --- | --- |
| 1 | `GET /api/accounts?show_inactive=true&show_missing=false` | `119,282 B` | server `3265.1 ms`, network `2.9 ms` | `646` accounts |
| 2 | `GET /api/accounts/19528587/summary-with-point-bases` | `985 B` | server `2512.9 ms`, network `2.7 ms` | summary `trade_count=5`, `4` point bases |
| 3 | `GET /api/accounts/19528587/trades?limit=200` | `2,063 B` | server `1018.8 ms`, network `2.3 ms` | `5` trades |

### Accounts Tab Open (after)

- `0` network requests (served from frontend cache)

## Before/After Delta

| Metric | Before | After | Change |
| --- | --- | --- | --- |
| Dashboard API request count on initial load | 10 | 6 | `-40%` |
| Summary API calls on dashboard initial load | 5 | 1 | `-80%` |
| Trades metrics request (`/trades?limit=1000`) | 2944.7 ms | 1015.9 ms | `-65.5%` |
| Accounts tab network requests on open | 2 | 0 | eliminated |
| Accounts tab slowest request | 6535.4 ms | n/a | eliminated |

## Remaining Bottleneck

- `GET /api/accounts?...` is still dominated by provider sync latency (~3.2s server time).  
- This endpoint is now avoided on immediate Accounts tab navigation via cache, but initial dashboard still pays this sync cost.
