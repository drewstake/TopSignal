# Local development profiles

From the repository root, follow the [quick start](../README.md#local-quick-start) to install Python and Node dependencies. **`npm run dev:offline`** starts an isolated SQLite workspace at **http://127.0.0.1:5174**. Use this profile for UI work, sample data, CSV trade imports and account review. It starts with an empty database. Demo mode lets you explore the dashboard with read-only sample data.

The supervisor binds FastAPI to loopback, chooses an available port starting at 8000 and points Vite at the ready backend. Port 5174 is reserved for the SQLite profiles so their browser preferences stay separate from the configured account workspace. If that port is in use, use the existing instance or stop it in its terminal with Ctrl+C before switching profiles.

## What stays on this computer

- `backend/storage/offline/topsignal.sqlite3` holds checkout-local application data. The launcher also uses `backend/storage/offline/topsignal.deleted-projectx-accounts.json` for per-user account exclusions when needed. Keep the companion file with the database when backing up this workspace.
- The disconnected launcher clears broker credentials for its child processes, disables the worker and both order-routing gates, and selects full SQLite schema initialization. Existing saved runs stay in the database. The configuration is in [offline-env.cjs](../scripts/offline-env.cjs).
- A fresh checkout needs neither a private `.env` nor an owner store for this mode. Start with a separate development checkout if the original has host cache paths, credentials or local-auth settings. The `offline` profile selects its own application database but can inherit a custom `TOPSIGNAL_DATABENTO_CACHE_DIR`.
- The Backtesting screen can open without history. To display historical candles it needs the backend’s filesystem cache. Its model replay also needs a registered saved model. Startup does not download either. [Databento history transfer](databento-history-transfer.md)

Anonymous requests require a loopback peer and Host. Browser origins must match the launcher’s allowlist. The SQLite workspace and the production database have independent data. See the [database setup guide](../db/README.md) for PostgreSQL schema and migration tests.

## Connected development

`npm start` and `npm run dev:local` refresh expenses, payouts and combine-fee suppression IDs from the signed-in owner on peteamdpc before serving local code on 5174. The launcher reads through the existing private connection, revokes its temporary trusted-device session, then applies the complete ledgers in one SQLite transaction. If the pull fails, the launcher reports the failure and stops. `npm start` also opens the Bot page for account 29593203. Both commands use the connected ProjectX development settings and a dry-run worker with live orders disabled.

The refreshed workspace is `backend/storage/peteamdpc-dev/topsignal.sqlite3`. On first use, the launcher seeds it from the original offline SQLite file and copies its account exclusions if present. Other application data continues to use its existing local or ProjectX sources. Each successful restart restores the current host ledgers. When a refresh would replace different local ledger rows, the launcher verifies a compressed JSON backup in `backend/storage/peteamdpc-dev/ledger-backups/` first. Changes on 5174 stay local. The original offline workspace remains available with `npm run dev:offline`.

`npm run dev:cloud` is the historical name for the configured account workspace at `http://localhost:5173`. Its migrations run against `backend/.env`. Point it at a dedicated local PostgreSQL development database before launching it. `npm run dev` starts that workspace alongside the original connected SQLite profile. A configured-profile migration failure can leave the SQLite workspace running. Ctrl+C stops the supervisors launched by that terminal. [Launcher scripts](../package.json) · [Local authentication](local-sign-in.md)

The **Continue offline** link on the local development sign-in screen opens the 5174 workspace. Start its server first. Restart the supervisor after changing backend or frontend environment values so its children inherit the new settings.

## Offline backend tests

From the repository root on Windows:

```powershell
.\backend\.venv\Scripts\python.exe backend/tools/run_offline_tests.py
```

The runner disables dotenv, uses an in-memory application database, disables broker routing and blocks external connections in its process. It allocates a short temporary directory on Windows. To keep fixtures, pass `--basetemp` pointing at a **new disposable directory**. Pytest may clear that directory before the run. [Runner](../backend/tools/run_offline_tests.py) · [CI and PostgreSQL tests](../.github/workflows/ci.yml)
