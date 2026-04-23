# TopSignal Frontend

This frontend is the trader-facing UI for TopSignal. It is a React 19 + TypeScript + Vite application that talks to the FastAPI backend and renders the current routed product: dashboard, accounts, trades, expenses, payouts, and journal workflows.

## Stack

| Layer | Implementation |
| --- | --- |
| UI | React 19 |
| Language | TypeScript |
| Routing | React Router 7 |
| Build tool | Vite 7 |
| Styling | Tailwind CSS + custom UI primitives |
| Auth client | `@supabase/supabase-js` when Supabase env vars are present |
| Tests | Vitest |

## Routes

The current router lives in `src/app/routes.tsx` and ships:

- `/`: `DashboardPage`
- `/accounts`: `AccountsPage`
- `/trades`: `TradesPage`
- `/expenses`: `ExpensesPage`
- `/journal`: `JournalPage`

The app shell in `src/app/AppShell.tsx` provides:

- the global active-account picker
- the global "Sync Latest Trades" action
- top-level tab navigation
- optional Supabase sign-out controls

## Page Responsibilities

### Dashboard

`src/pages/dashboard/DashboardPage.tsx`

Shows:

- account-level summary metrics
- point-payoff comparisons across supported bases
- sustainability, activity, drawdown, and direction metrics
- recent trade events
- trading-day PnL calendar
- daily account-balance curve
- journal-day markers and day-to-journal navigation

### Accounts

`src/pages/accounts/AccountsPage.tsx`

Supports:

- active-account selection
- main-account selection
- hidden/missing account toggles
- last-trade lookup
- local display-name overrides
- journal-history merge between accounts

### Trades

`src/pages/trades/TradesPage.tsx`

Supports:

- date-range filters
- client-side symbol search
- manual trade sync for the selected range
- account summary cards
- paginated execution feed

### Expenses

`src/pages/expenses/ExpensesPage.tsx`

Supports:

- expense list/filter/pagination
- expense totals
- expense creation and deletion
- combine-spend tracker behavior
- payout list, totals, creation, and deletion

### Journal

`src/pages/journal/JournalPage.tsx`

Supports:

- entry list and entry editor
- debounced autosave
- optimistic concurrency conflict handling
- image uploads and deletion
- archive toggle
- trade-stat snapshot pulls
- copy/export helpers for recent entries

## Shared Frontend Architecture

### API client

`src/lib/api.ts` is the main frontend integration layer.

It provides:

- typed backend requests
- auth-token injection
- request performance logging
- in-memory response caching
- in-flight request deduplication
- cache invalidation after mutations

Current cache TTLs:

- account lists: 10 minutes
- account-scoped summaries/trades/PnL calendars: 10 minutes
- journal day markers: 10 minutes

### Types

`src/lib/types.ts` contains the frontend types for:

- accounts
- trade events
- summaries
- journal entries
- expenses
- payouts
- auth and credential status

### Utilities

Key helpers live in:

- `src/lib/`: API integration, account selection, Supabase wiring, trade sync events, combine tracker, trading-day helpers
- `src/utils/`: formatting and metric helper functions
- `src/utils/metrics/`: derived metric helpers used by the dashboard

### UI primitives

Reusable UI building blocks live under:

- `src/components/ui/`

Metric-specific visual components live under:

- `src/components/metrics/`

## Development

### Install

From the repo root:

```powershell
npm --prefix frontend install
```

Or if you already run root install:

```powershell
npm install
```

### Environment

Create `frontend/.env.local`.

Minimum anonymous local setup:

```dotenv
VITE_API_BASE_URL=http://localhost:8000
```

Optional Supabase-enabled setup:

```dotenv
VITE_API_BASE_URL=http://localhost:8000
VITE_SUPABASE_URL=https://<project-ref>.supabase.co
VITE_SUPABASE_ANON_KEY=your_supabase_anon_key
```

Optional frontend perf logging:

```dotenv
VITE_PERF_LOGS=true
```

### Run

Frontend only:

```powershell
npm --prefix frontend run dev
```

From the repo root, the combined dev command is:

```powershell
npm run dev
```

### Build And Test

```powershell
npm --prefix frontend run build
npm --prefix frontend run lint
npm --prefix frontend run test
```

## File Map

```text
frontend/
|-- src/
|   |-- app/             # router + app shell
|   |-- components/      # shared UI + metric components
|   |-- lib/             # API client, auth, caches, selection state
|   |-- pages/           # routed screens
|   |-- styles/          # global CSS
|   `-- utils/           # formatting + metric helpers
|-- package.json
|-- tailwind.config.js
`-- vite.config.ts
```

## Current Notes

- The frontend assumes the backend is the source of truth for accounts, summaries, trades, journal data, expenses, and payouts
- Some product logic is intentionally client-side, especially the combine tracker
- Supabase auth is optional and only activates when the relevant frontend env vars are present
- The repo still contains unrouted prototype folders for `overview` and `analytics`

## Related Files

- `src/app/AppShell.tsx`
- `src/app/routes.tsx`
- `src/lib/api.ts`
- `src/lib/types.ts`
