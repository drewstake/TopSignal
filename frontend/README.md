# TopSignal frontend

The account workspace uses React 19, TypeScript, React Router and Vite. Start the UI and its matching FastAPI server together with the [repository quick start](../README.md#local-quick-start). Each launcher waits for API readiness and supplies the backend address to Vite.

## Finding a feature

| Area | Entry points |
| --- | --- |
| Pages and navigation | [Router](src/app/routes.tsx), [shell and account picker](src/app/AppShell.tsx) and [route-to-screen index](../README.md#frontend-routes) |
| Sign-in and owner changes | [AccountAccess](src/app/AccountAccess.tsx) and [local session client](src/lib/localAuth.ts) |
| Requests and cache invalidation | [API client](src/lib/api.ts) and [shared types](src/lib/types.ts) |
| Dashboard metrics and imports | [Dashboard components](src/pages/dashboard/components), [dashboard metrics](src/pages/dashboard/metrics) and [shared metric helpers](src/utils/metrics) |
| Practice controls and historical research | [Bot page](src/pages/bot/BotPage.tsx), [forward results](src/pages/bot/BotForwardResultsPage.tsx) and [Backtesting page](src/pages/backtesting/BacktestingPage.tsx) |
| Reusable UI and styling | [UI primitives](src/components/ui), [metric components](src/components/metrics) and [styles](src/styles) |

The API client attaches local session tokens, deduplicates in-flight requests and invalidates cached reads after mutations. Account selection, appearance preferences and the dashboard’s analytics-only Copy Trade Mode also use browser state. [Account walkthrough](../docs/account-workspace.md)

## Run and check the frontend

From the repository root, install with `npm --prefix frontend ci`. For a frontend-only session with an already running development API, set `VITE_API_BASE_URL` in `frontend/.env.local` to that API’s loopback URL and run `npm --prefix frontend run dev`. Vite exposes `VITE_*` values to the browser. Broker credentials and encryption keys belong on the backend.

```powershell
npm --prefix frontend test
npm --prefix frontend run lint -- --max-warnings=0
npm --prefix frontend run build
```

The build runs TypeScript before producing `frontend/dist`. The host serves prebuilt assets through the Windows supervisor. [Package scripts](package.json) · [Hosted dashboard connection](../docs/hosted-dashboard-connection.md)
