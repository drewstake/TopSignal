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
