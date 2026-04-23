# Dashboard And Accounts Performance Notes

## Purpose

This document records the main dashboard/accounts performance work that is reflected in the current codebase and explains the remaining bottleneck.

## Measurement Context

- Original capture date: `2026-03-02`
- Environment: local dev (`http://localhost:5173` + `http://localhost:8000`)
- Capture method: browser network timings plus backend `Server-Timing` / `X-Server-Time-Ms`

Even though the measurements were taken in March, the optimizations described below are still present in the current repository.

## Main Problem That Was Fixed

The initial dashboard and accounts flows were doing too much duplicate work:

- the dashboard fanned out multiple summary requests for different point bases
- the accounts page re-requested the same expensive account list immediately after dashboard load
- lightweight reads were getting queued behind the expensive `/api/accounts` sync path

The biggest remaining cost was and still is ProjectX account sync inside `GET /api/accounts`.

## Implemented Fixes

### 1. Consolidated summary fan-out

Added:

- `GET /api/accounts/{account_id}/summary-with-point-bases`

Effect:

- replaces separate summary calls for multiple point bases with one request
- reduces dashboard startup request count and backend work

Relevant code:

- `backend/app/main.py`
- `backend/app/services/projectx_trades.py`
- `backend/app/projectx_schemas.py`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/types.ts`
- `frontend/src/pages/dashboard/DashboardPage.tsx`

### 2. Added shared account-list caching and in-flight dedupe

The frontend now caches `/api/accounts` by query options and deduplicates in-flight requests.

Effect:

- `getSelectableAccounts()` reuses the same cached data source
- immediate dashboard -> accounts navigation avoids a second provider-backed fetch

Relevant code:

- `frontend/src/lib/api.ts`

### 3. Added request instrumentation

The backend now returns:

- `Server-Timing: app;dur=...`
- `X-Server-Time-Ms: ...`

The frontend can log:

- total request time
- server time
- network time
- payload size

Relevant code:

- `backend/app/main.py`
- `frontend/src/lib/api.ts`
- `frontend/src/pages/dashboard/DashboardPage.tsx`
- `frontend/src/pages/accounts/AccountsPage.tsx`

### 4. Added targeted Postgres indexes

Performance-specific indexes were added for the newer trade-event pipeline.

Relevant migration:

- `db/migrations/20260302_add_projectx_trade_events_perf_indexes.sql`

## Request Shape After The Fixes

### Initial dashboard load

Typical startup flow is now:

1. `GET /api/accounts?show_inactive=true&show_missing=false`
2. `GET /api/accounts/{id}/summary-with-point-bases`
3. `GET /api/accounts/{id}/pnl-calendar?...`
4. `GET /api/accounts/{id}/trades?limit=200`
5. `GET /api/accounts/{id}/trades?limit=1000`
6. `GET /api/accounts/{id}/journal/days?...`

### Accounts tab navigation after dashboard

With the frontend cache hot:

- `0` additional network requests

## What Still Dominates Latency

The main remaining bottleneck is:

- `GET /api/accounts`

Why it is slow:

- it always performs ProjectX account discovery and reconciliation before returning
- it also loads local state such as `last_trade_at`
- provider latency dominates server time

This means:

- the app is much better about avoiding duplicate fetches
- first-load dashboard latency is still largely tied to ProjectX response time

## Current Code Behaviors That Matter For Perf

### Frontend caches

`frontend/src/lib/api.ts` currently caches:

- account lists for 10 minutes
- account-scoped summaries for 10 minutes
- account-scoped trade lists for 10 minutes
- PnL calendars for 10 minutes
- journal day markers for 10 minutes

It also deduplicates duplicate in-flight reads.

### Trade sync behavior

The newer trade-event pipeline is local-first:

- cached historical days can be reused
- explicit refresh can force provider sync
- empty caches still trigger first-load sync/backfill as needed

That behavior keeps normal navigation much cheaper than a provider-first design.

## Recommended Next Optimization

If more performance work is needed, the next high-value target is still account sync.

Most likely direction:

- move ProjectX account reconciliation off the hot path for normal account-list reads

That could mean:

- background refresh
- a shorter-lived account-sync cache
- splitting "read local account list" from "force provider reconciliation"

## Related Files

- `backend/app/main.py`
- `backend/app/services/projectx_trades.py`
- `frontend/src/lib/api.ts`
- `frontend/src/pages/dashboard/DashboardPage.tsx`
- `frontend/src/pages/accounts/AccountsPage.tsx`
