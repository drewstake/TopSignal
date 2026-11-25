# Shipping changes to peteamdpc

A request to implement a TopSignal change includes deployment to peteamdpc by
default. Make the change in the local checkout, test it, review the diff, then use
`npm run release -- ship` to commit, push and deploy exactly the intended change.
Follow [the runbook](docs/releasing.md), preserve unrelated work and keep the local
app closed. Verify the served page before telling the user to refresh peteamdpc.
Report the release lane and timing.

If the user asks to prepare and hold for “okay deploy”, push and stage with
`--prepare-only`, then resume the prepared SHA on approval. Surface a research
wait promptly and follow the runbook for release-specific approval.

Honor a narrower request such as “local only” or “don't deploy”. Questions, plans
and reviews call for an answer. Approval to cut research windows short applies
only to the specific release the user approved.

# Running TopSignal on the viewing PC

For requests to run TopSignal locally on this PC, use `npm start`. It refreshes the local Expenses calendar from peteamdpc, runs checkout code on 5174 and opens the Bot page for account 29593203. Port 4173 serves the deployed host dashboard.

# Storage rules for TopSignal

Current architecture: see the [November 13 maintenance receipt](docs/audits/2026-11-13/storage-maintenance.md).
Local PostgreSQL on peteamdpc is the sole execution authority. The application,
trusted-device authentication, independent recorder, Discord reader and backup
helper now use protected local stores. Journal is removed and archived; the
compact-record mirror is retired, its task disabled, capture triggers disabled,
outbox empty and dedicated logins NOLOGIN. Authority/cloud fences remain enabled.
Historical environments and cloud data are recovery material, never fallbacks.
Verify live state and dated receipts; the earlier health audit is a baseline,
not the current architecture or proof that final acceptance is complete.

Follow the [retirement/recovery procedure](docs/supabase-retirement.md). Preserve
encrypted host/off-device Journal archives, historical tables/migrations and
cloud audit records. Release 87 cannot accept the new migration ledger or retired
mirror state; do not blindly roll back to it or reenable the old sync task.
Keep the API and independent recorder tasks at scheduler priority 4; optional
derivation/backup work remains lower priority. Keep collection independent of
bot arm state, account selection and browser/development-PC availability.

Read [the Supabase storage policy](docs/supabase-storage-policy.md) before changing
historical-data imports, backtest persistence, database migrations, retention,
or deployment storage.

- Keep Databento archives, bulk historical candles, Parquet files, and replay
  arrays on the backend filesystem under `TOPSIGNAL_DATABENTO_CACHE_DIR`.
  Use `backend/tools/build_databento_cache.py`; do not populate the legacy
  `databento_*` PostgreSQL tables or restore their historical rows to Supabase.
- Preserve the SQLite-only relational import guard and local replay behavior.
  New symbols, timeframes, and datasets follow the same filesystem policy.
- Keep authoritative accounts, journals, trades, credentials, bot configuration
  and saved results in the application database; preserve existing cloud mirror
  and audit records. Never use the mirror as an execution fallback. Keep raw
  depth, worker evidence and trade-progress recordings on the backend filesystem
  with verified off-device backups. Do not persist full input candle series
  inside result JSON or add unbounded historical caches to cloud tables.
- Before bulk database writes, inspect storage usage and estimate growth.
  Do not silently upgrade billing, disable spend caps, or purge user/audit data.
  Destructive cleanup requires explicit user authorization and a verified backup.
- Keep original Databento source archives with the cache and back them up off
  device. Git and Supabase backups do not include these ignored local files.
- Check backup scope as well as freshness: a focused recording snapshot does not
  replace full-cache or PostgreSQL disaster recovery. Do not enable disabled
  schedules or launch heavy backup scans during read-only health checks.

# Trading clip editing style

- Keep the full trade in chronological order. Speed up the stretches between the entry and exit to fit the requested runtime.
- Ease down to slightly faster than real time around the entry so viewers can see the pending order fill and the position open. For the exit, return to real time before price reaches the stop-loss or take-profit and stay there through the fill so viewers can see the position close.
- Finish with the original P&L calendar screenshot for exactly three seconds, without added text. Start fading the background music on the first calendar frame and reach silence on the final frame.
