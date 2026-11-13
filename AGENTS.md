# Storage rules for TopSignal

Current architecture: see the [November 13 live audit](docs/audits/2026-11-13/peteamdpc-health.md).
Local PostgreSQL on peteamdpc is the execution authority. Supabase is **partially
retired**: the compact-record mirror, journal storage and independent recorder
startup still depend on it. Hosted sign-in uses local trusted-device auth;
cloud authentication remains configured as an alternative. Verify live state
and dated receipts; old deployment reports are not current health checks.

The [November 13 remediation](docs/audits/2026-11-13/storage-remediation.md) and
[retirement procedure](docs/supabase-retirement.md) describe prepared local-only
code. Do not confuse it with activated runtime. Journal has a verified encrypted
host/off-device historical archive; keep historical tables/migrations and cloud
recovery records. Mirror retirement must preserve local authority fences and
stop both the consumer and outbox production coherently.

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
