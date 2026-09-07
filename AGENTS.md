# Storage rules for TopSignal

Read [the Supabase storage policy](docs/supabase-storage-policy.md) before changing
historical-data imports, backtest persistence, database migrations, retention,
or deployment storage.

- Keep Databento archives, bulk historical candles, Parquet files, and replay
  arrays on the backend filesystem under `TOPSIGNAL_DATABENTO_CACHE_DIR`.
  Use `backend/tools/build_databento_cache.py`; do not populate the legacy
  `databento_*` PostgreSQL tables or restore their historical rows to Supabase.
- Preserve the SQLite-only relational import guard and local replay behavior.
  New symbols, timeframes, and datasets follow the same filesystem policy.
- Keep cloud records needed for accounts, journals, trades, credentials, bot
  configuration, and saved results. Do not persist full input candle series
  inside result JSON or add unbounded historical caches to cloud tables.
- Before bulk database writes, inspect storage usage and estimate growth.
  Do not silently upgrade billing, disable spend caps, or purge user/audit data.
  Destructive cleanup requires explicit user authorization and a verified backup.
- Keep original Databento source archives with the cache and back them up off
  device. Git and Supabase backups do not include these ignored local files.
