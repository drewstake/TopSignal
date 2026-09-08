# Supabase storage policy and quota recovery

## Permanent storage boundary

Databento backtest history belongs on the backend filesystem, not in Supabase.
Use `backend/tools/build_databento_cache.py` to build the local Parquet and
memory-mapped replay files. `TOPSIGNAL_DATABENTO_CACHE_DIR` selects the location;
the development default is `backend/storage/databento`.

| Data | Location and policy |
| --- | --- |
| Original Databento definition/OHLCV ZIPs | Local cache `sources/`; preserve checksums and an off-device backup |
| Bulk historical candles, derived timeframes, replay arrays | Local Parquet/mmap cache; new instruments follow this policy |
| Accounts, executed trades, journals, expenses, credentials, bot configuration | Application database with existing tenant protections |
| Saved backtest results | Application database, with bounded detail; do not embed the complete input candle history |
| Existing ProjectX candle cache and other growing tables | Measure before expanding lookback or retention; bulk research history stays local |

The legacy `backend/tools/import_databento.py` and its service entry points now
reject non-SQLite database imports before archive inspection or connection.
SQLite relational imports exist only for repository fixtures. Production
backtests already read the local cache. Do not remove this guard, repopulate
legacy cloud tables, or reintroduce cloud candle imports through migrations,
ad-hoc SQL, backtest results, or a new importer.

Empty legacy table definitions and small import/roll metadata may remain for
compatibility; their presence is not permission to refill the candle table.
Historical migration files are checksummed: do not rewrite them to fix storage.

Follow [the transfer guide](databento-history-transfer.md) to import or move
history. Keep the source archives at their final location and rebuild after a
move. Git clones and Supabase backups do not carry the local cache. A deployed
backend needs persistent local storage plus its own backup, not an ephemeral
filesystem.

## Capacity checks before growth

Run [the read-only storage report](../db/diagnostics/storage_usage.sql) in the
Supabase SQL editor weekly, before a bulk import, before increasing sync
lookback/retention, and before releases that add stored data. Review the
organization Usage page and project disk metrics as well; a single database
query does not include every organization project, WAL, or system overhead.

For the current 0.5 GB Free-plan database allowance, use these internal buffers:

- At **350 MB** (decimal), investigate growth and prepare a capacity/archival plan.
- At **400 MB**, stop optional bulk writes until the plan is reviewed.
- Estimate the new rows, payload size, indexes, and headroom before enabling a
  new persisted dataset. If it cannot fit, keep research data local or obtain
  explicit approval for a capacity change.

These are manual operating thresholds, not automatic write limits or a running
monitor. Confirm current plan limits in Supabase; limits and enforcement can
change. Other tables can still exhaust the quota even with Databento blocked.
Do not automatically delete journals, trades, saved results, order attempts,
or reconciliation/audit records to make space. Retention changes require a
reviewed export/backup and explicit authorization for deletion. Preserve normal
trade capture and reconciliation when pausing optional research imports.

## September 7, 2026 incident

The database reached approximately **1,414 MiB**, including **1,222 MiB** in
`public.databento_ohlcv_1m`. Supabase returned HTTP 402 with
`exceed_db_size_quota`, blocking Google sign-in and PostgREST. The application
already used filesystem history, so the old cloud candle copy was redundant.

After explicit authorization, 3,779,541 cloud candles and their associated
Databento metadata were exported. Every gzip archive was decompressed, checked
against SHA-256, parsed, and verified against the database row count before
cleanup. Only the candle table was truncated, without CASCADE. The database
dropped to approximately **192 MiB**, and the candle table to **24 KiB**. All six
local MNQ replay timeframes loaded afterward; saved results were not deleted.

Local recovery records on the original machine:

- `backend/storage/backups/supabase-databento-20260907T145144Z/README.md`
- The same directory's `manifest.json` and `*.csv.gz` archives
- `backend/storage/maintenance/restore-supabase-candles.py`

These paths are ignored by Git and must be preserved separately. Restoring the
cloud candle backup would recreate the storage problem and is not required to
use local backtests. Do not run the incident cleanup or restore scripts as
routine maintenance.

## September 8, 2026 local history retirement

At the user's request, the five local `databento`, `databento-calendar-v5`,
`databento-calendar-v6`, `databento-format4-reference`, and `research` directories
were removed after creating and verifying a complete recovery ZIP. All 5,670
archived files were decompressed and SHA-256 checked against their originals;
the original files were rechecked immediately before deletion.

Recovery records are in
`backend/storage/backups/retired-databento-research-20260908T154039Z`.
The removed files totaled 7,366,517,902 bytes. The archive plus its manifest uses
about 998 MB, reclaiming about 6.37 GB net. This recovery copy is local, not an
off-device backup. No application database records were removed.

The Data tab and backtest panel have also been removed from the app. Forward
testing continues to use ProjectX market data. Do not automatically restore or
rebuild the retired history; the retained replay tools need an explicit restore
or new import before reuse. The filesystem/cloud boundary above still applies.

## If sign-in or service health fails again

1. Capture the actual HTTP status and error from Auth/PostgREST. An unhealthy
   badge alone does not distinguish a quota restriction from a service failure.
2. For `402` / `exceed_db_size_quota`, check current usage and the largest tables
   with the storage report. Check disk/WAL separately if needed. SQL row-count
   estimates can be stale; zero estimated rows does not prove a table is empty.
3. Stop the responsible optional import. Back up and verify affected data,
   confirm local history coverage if applicable, and obtain authorization for
   a specific cleanup. Do not disable authentication to work around the outage.
4. Verify physical sizes after the cleanup transaction commits. `TRUNCATE`
   reclaims the emptied table's space without a follow-up `VACUUM`; ordinary
   deletion has different reclamation behavior. Do not run `VACUUM FULL` or
   alter WAL settings blindly to clear a billing restriction.
5. Recheck Auth and PostgREST. Do not declare sign-in fixed merely because
   usage fell. During this incident, the usage dashboard updated but HTTP 402
   persisted. An AI support reply contradicted both the dashboard and published
   billing-period guidance. Do not promise a reset time: ask human support to
   confirm the triggering metric, measured value, limit, and release condition.
6. Keep billing changes under explicit user control. Send support the project
   reference, error, timestamps, post-cleanup sizes, and relevant dashboard
   readings. Never include API keys, database passwords, or user data exports.

References (checked September 7, 2026):

- [Supabase database size and Fair Use restrictions](https://supabase.com/docs/guides/platform/database-size)
- [Supabase disk usage](https://supabase.com/docs/guides/platform/manage-your-usage/disk-size)
- [PostgreSQL TRUNCATE](https://www.postgresql.org/docs/current/sql-truncate.html)
- [Supabase support](https://supabase.com/dashboard/support/new)
