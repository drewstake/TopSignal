# Local development without Supabase

`npm run dev` starts both the regular cloud app and the connected local
ProjectX workspace. Each uses its own backend and settings; a cloud failure
does not stop the local workspace. Ctrl+C stops both. Use `npm run dev:cloud`
to start just the regular cloud/login app, or `npm run dev:local` to start just
the connected local workspace. Stop an existing standalone local/offline
server before starting both because they use the same local port `5174`.

Run from the repository root with the existing dependencies installed:

```powershell
npm run dev:offline
```

Open **http://127.0.0.1:5174**. Google sign-in is not required. The backend
binds to loopback and selects a free port starting at 8000; the frontend
automatically uses that backend. Port 5174 is reserved for the offline workspace
so its browser preferences do not mix with the normal development site.

The local development sign-in page also has **Continue offline** directly below
**Continue with Google**. It opens this separate workspace; start the command
above first if it is not already running. The link does not change the cloud
server's authentication settings or copy cloud data. It appears only on local
development addresses. For Topstep account refreshes with local storage, start
`npm run dev:local` instead; the same button opens that connected local profile.
`dev:offline` deliberately clears broker credentials, so a request to configure
ProjectX credentials in that profile does not mean your existing keys were lost.

The command overrides cloud settings for its child processes without editing
your `.env` files. It creates a separate SQLite database at
`backend/storage/offline/topsignal.sqlite3` and stores journal images under
`backend/storage/offline/journal_images`. These files persist across restarts
and are ignored by Git; back them up separately if you need to keep the work.

Explicitly deleted local ProjectX account IDs are kept per user in
`backend/storage/offline/topsignal.deleted-projectx-accounts.json`. Account
refresh skips those IDs so a broker response cannot recreate removed records.
Back up this companion file with the SQLite database. It applies only to that
local database and does not change the broker account or cloud workspace.

The workspace starts empty. Use CSV imports and local editing, or turn on the
existing **Demo mode** switch to explore the dashboard with read-only sample
data. Cloud accounts, trades, journals, and saved results are not copied. Work
saved offline does not automatically sync to Supabase later.

The app no longer shows the Data tab or Databento backtest panel. The local
Databento and research directories were retired on September 8, 2026; a verified
recovery archive remains under `backend/storage/backups/retired-databento-research-20260908T154039Z`.
Forward testing does not need those files, and startup does not rebuild them.
Broker credentials are cleared, streaming and the bot worker are disabled, and
live order execution is disabled. Broker sync and downloads requiring external
services are unavailable; local pages and CSV workflows can
be developed without Supabase. SQLite does not replace PostgreSQL integration
testing: validate PostgreSQL migrations and provider workflows in normal mode.

Saved bot runs from a connected local session do not prevent this disconnected
workspace from opening. They remain saved; offline mode does not resume them.
Bot status still reports the unavailable worker and broker connection.

Stop with Ctrl+C. To return to your normal configured environment, use
`npm run dev:cloud` and its printed URL. The offline command does not change production
authentication or Supabase billing settings.

## Local storage with Topstep connectivity

Run `npm run dev:local` to use the same SQLite workspace and URL with the
Topstep/ProjectX API enabled. Stop `dev:offline` first because they share port
5174 and the same database. This mode keeps `PROJECTX_*`/`TOPSTEP*` settings
from `backend/.env` and permits the existing local environment credential
fallback (`PROJECTX_USERNAME` and `PROJECTX_API_KEY`). Supabase remains disabled.

The dashboard refreshes broker accounts automatically. Use **Sync Latest Trades**
to fetch trades into SQLite. API market-data requests and account-scoped
streams can use the broker connection. Internet access and valid Topstep API
credentials are required. The old process-global streamer remains disabled.
The recurring bot worker is enabled in this profile. Choose **Dry Run** on the
Bot page to start the selected account's TopBot. It evaluates closed candles
without sending orders; dry runs can resume when this local server restarts.
Both live execution gates remain disabled. Choose **Stop Automation** to stop
the dry run. Local changes still do not automatically sync to Supabase.
