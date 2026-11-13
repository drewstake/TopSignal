# Supabase storage policy and quota recovery

## Current deployment boundary (verified November 13, 2026)

Supabase is **partially retired**. Execution/application records are authoritative
in local PostgreSQL on peteamdpc; research and recordings stay on its filesystem.
The compact-record mirror is active, journal object storage remains configured
in Supabase, and the independent recorder still reads startup credentials from
cloud PostgreSQL. Hosted sign-in uses local trusted-device authentication;
cloud Auth remains an alternative. See [live evidence](audits/2026-11-13/peteamdpc-health.md)
and [recovery guidance](local-first-storage.md). Historical incident sections
below retain their original dates; they do not describe the current release.

Prepared local-only code and its pending maintenance steps are documented in
[Supabase retirement](supabase-retirement.md). Journal records/images now also
have a verified encrypted historical archive on the host and off device. This
does not authorize source/cloud deletion or imply the old deployed APIs stopped.

## Permanent storage boundary

Databento backtest history belongs on the backend filesystem, not in Supabase.
Use `backend/tools/build_databento_cache.py` to build the local Parquet and
memory-mapped replay files. `TOPSIGNAL_DATABENTO_CACHE_DIR` selects the location;
the development default is `backend/storage/databento`.

| Data | Location and policy |
| --- | --- |
| Trade history sync coverage/revisions, frozen trade-review bundles and copied replay results | Local cache `trade-history-sync-v1/` and `trade-review-v1/`; keep hashes, originals and off-device receipts; no cloud arrays |
| Original Databento definition/OHLCV ZIPs | Local cache `sources/`; preserve checksums and an off-device backup |
| Bulk historical candles, derived timeframes, replay arrays | Local Parquet/mmap cache; new instruments follow this policy |
| Accounts, executed trades, journals, expenses, credentials, bot configuration | Application database with existing tenant protections |
| Saved backtest results | Application database, with bounded detail; do not embed the complete input candle history |
| Raw observations, ProjectX candle caches, detailed research snapshots | Local SQLite under `TOPSIGNAL_DATABENTO_CACHE_DIR`; cloud definitions remain fenced |
| Trade progress samples, lifecycle timelines, fill revisions, enrollment and recorder metadata | Local `trade-progress-v1/progress.sqlite3` under `TOPSIGNAL_DATABENTO_CACHE_DIR`; never Supabase or the cloud mirror; no automatic retention deletion |
| Exit-study replay checkpoints | Local `topbot-replay-checkpoints-v1/<owner-hash>/<capture-id>.sqlite3` under `TOPSIGNAL_DATABENTO_CACHE_DIR`; derived from worker captures, anchored to each capture's hash chain, never the cloud; 256 MiB per owner and 10 GiB free-space floor stop new checkpoints (replays fall back to full scans); nothing is deleted automatically ([details](topbot-exit-study.md)) |
| Exit-study replay-job state | Local `topbot-exit-study-v1/<owner-hash>/replay-jobs.sqlite3`; attempts, retry times and sanitized failure categories only; 16 MiB cap; first failure plus the 12 most recent attempts per entry are kept |
| Derived liquidity-heatmap frames and checkpoints | Local `depth-heatmap-v1/<owner-hash>/` SQLite under `TOPSIGNAL_DATABENTO_CACHE_DIR`; rebuilt from raw depth segments, so excluded from the archive backup; newest 30 sessions kept (user-approved November 11, 2026; older derived session files are deleted automatically, raw recordings never); 4 GiB per owner and 15 GiB free-space bounds stop derivation ([details](live-liquidity-heatmap.md)) |

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

Inspect local PostgreSQL and C:/D: capacity before local growth. For the active
cloud mirror, run [the read-only storage report](../db/diagnostics/storage_usage.sql) in the
Supabase SQL editor weekly, before increasing mirrored volume or sync
lookback/retention, and before releases that add stored data. Review the
organization Usage page and project disk metrics as well; a single database
query does not include every organization project, WAL, or system overhead.

For deployments using the incident's 0.5 GB Free-plan allowance, use these
internal buffers (the live audit did not reverify billing/plan limits):

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

## September 26, 2026 authorized local research recovery

The user explicitly authorized source recovery for the trading-bot audit. Two
original Databento ZIPs and their metadata (67,898,148 bytes) were restored from
the September 8 recovery archive after matching its SHA-256 and every restored
file against the retirement manifest. `build_databento_cache.py` rebuilt MNQ
1m, 5m, 4h and 1d series under `backend/storage/databento`. Existing v3 experiment
registrations were preserved; the retired research directory was not restored.
No historical rows were imported into Supabase. Recovery details are in
`backend/storage/databento/source-recovery-20260926.json`.

This specific authorization does not authorize other archive restoration or
cloud historical imports. The retained source archives and retirement recovery
ZIP still need an off-device backup destination.

## October 24, 2026 local MNQ history extension

The supplied `GLBX-20261024-DW4PKKXFLB.zip` was checksum-verified and preserved
under `backend/storage/databento/sources/GLBX-20261024-DW4PKKXFLB/`. It contains
179,612 OHLCV-1m records from July 10 through October 23, including spreads.
The existing Definition archive covers all outright contracts in this download.
The local cache builder now merges candle archives in timestamp order, removes
identical overlap before aggregating volume, and rejects conflicting candles.
The rebuild removed 1,923 identical overlapping outright records and added
103,300 continuous MNQ minute candles, bringing that series to 2,635,600 rows.

The 1m, 5m, 4h, and 1d series were rebuilt locally; their earlier OHLCV history
was verified unchanged. The prior immutable cache remains available, with its
pointer saved in `before-extension-20261024.json`. Validation details are in the
source folder's `validation.json` and the cache's
`extension-verification-20261024.json`. The source condition report flags
August 29 as degraded; checksum verification does not certify feed completeness.
No cloud historical rows were written. These local source files still require
an off-device backup destination.

## November 2, 2026 local research cutover

Production release `20261102-local-research-41` moved `market_observations`,
`projectx_market_candles` and `decision_research_snapshots` to
`D:/TopSignal/data/databento/research-store-v1/research.sqlite3` on peteamdpc.
All 442,020 frozen rows were restored and checked on both peteamdpc and the
development PC using exact counts, SHA-256 and SQLite integrity checks.
The existing cache archive job includes this SQLite store using online backup.

After explicit user approval, only those three frozen cloud table contents were
removed. Database allocation fell from 450,546,835 to 85,855,379 bytes, reclaiming
364,691,456 bytes. Table definitions and write fences remain. Accounts, trades,
journals, credentials, bot settings, order attempts and saved results were
preserved. This approval does not authorize other cloud cleanup.

Original sources and complete migration exports are retained off device.
Execution moved to SSD PostgreSQL on November 3 after the account was verified
flat, old cloud writers were fenced, and the final encrypted snapshot was fully
restored on both computers. Release `20261103-local-execution-45` uses local
authority `b15899c2-5a5b-4352-aa5d-162c600188eb` and durably mirrors compact
records asynchronously. Supabase must not be used as an execution fallback.
No additional cloud cleanup was performed or authorized. See the
[deployment evidence](audits/2026-11-02/local-first-storage.md) and
[recovery runbook](local-first-storage.md).

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
