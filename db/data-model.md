# TopSignal Database Guide

PostgreSQL on peteamdpc is the sole execution authority. This guide describes the application tables, including retained historical definitions. For the storage boundary and verified recovery scope, see [local-first storage](../docs/local-first-storage.md).

## Summary

TopSignal uses PostgreSQL as a local-first analytics cache and application store.

It persists:

- ProjectX account metadata
- normalized ProjectX trade events and confirmed Topstep file imports
- Topstep import-batch provenance (source filename, file hash, import time, and row counts)
- daily trade-sync completeness state
- retained Journal rows and historical image metadata
- expenses and payouts
- optional per-user encrypted provider credentials
- optional position lifecycle snapshots from the streaming runtime
- user-scoped bot configurations, deterministic backtest snapshots, lifecycle runs, decisions, risk events, and order-attempt audit records
- on upgraded installations only, any pre-existing global Databento relational tables; fresh schemas omit them, while existing tables remain untouched for compatibility

ProjectX remains the source for API-connected accounts, executions, positions
and order routing. Topstep Live account executions can also enter
through the reviewed CSV/XLSX import flow. The local cache retains historical Databento market data. Both API and imported trade analytics use
`projectx_trade_events`, not the legacy `trades` table.

## Persistence Strategy

The repo uses three layers together:

1. `db/schema.sql` for fresh databases
2. checksummed raw SQL files in `db/migrations/` for incremental upgrades
3. backend startup compatibility patches in `backend/app/db.py`

`backend/tools/migrate_db.py` applies the ordered SQL migrations and records
their SHA-256 checksums in `topsignal_schema_migrations`.

The startup compatibility code currently backfills:

- older `accounts` columns such as `display_name`, `account_state`, `trade_data_source`, and `is_main`
- journal versioning and image-storage support
- multi-tenant `user_id` columns and related indexes
- `provider_credentials`
- bot run error/evaluation fields and bot execution idempotency indexes
- default `instrument_metadata`

Those startup patches help older dev databases boot, but they are not a replacement for keeping the schema current.

## Core Tables

### `accounts`

Local representation of ProjectX accounts.

Important columns:

- `user_id`
- `provider`
- `external_id`
- `trade_data_source` (`projectx` or `csv_import`)
- `name`
- `display_name`
- `account_state`
- `can_trade`
- `is_visible`
- `first_seen_at`
- `last_seen_at`
- `last_missing_at`
- `is_main`

Notes:

- uniqueness is `(user_id, provider, external_id)`
- account display in the UI resolves from `display_name` first, then provider `name`
- account states are `ACTIVE`, `LOCKED_OUT`, `HIDDEN`, and `MISSING`
- `trade_data_source` is assigned at account creation; application routes reject
  attempts to convert an existing ProjectX account into a CSV-import account or
  vice versa
- CSV-import accounts remain locally available and are excluded from ProjectX
  account-missing transitions and provider trade refreshes
- trade data sources are immutable after account creation; ProjectX and Live
  CSV data must use separate account records

### `projectx_trade_events`

Normalized ProjectX execution events and closed-trade rows. This is the current source of truth for the routed app's analytics.

Important columns:

- `user_id`
- `account_id`
- `contract_id`
- `symbol`
- `side`
- `size`
- `price`
- `trade_timestamp`
- `fees`
- `commissions`
- `fee_scope`
- `pnl`
- `trade_date`
- `entry_timestamp`
- `entry_price`
- `order_id`
- `source_trade_id`
- `status`
- `raw_payload`
- `import_batch_id`

Deduplication rules:

- preferred unique key: `(user_id, account_id, source_trade_id)`
- fallback unique key for rows with no source trade ID: `(user_id, account_id, order_id, trade_timestamp)`

Behavior notes:

- voided executions retain their evidence and are excluded from performance
- `pnl = null` rows are treated as open-leg or half-turn events and do not count as closed trades
- provider rows use `fee_scope = per_side`; imported rows use `round_turn` and
  preserve the exact exported commission so costs are never inferred twice
- imported `trade_date` is Topstep's authoritative futures trading day

### `trade_import_batches`

Audit record for each confirmed Topstep trade file.

Important columns:

- `user_id`
- `account_id`
- `source_file_name`
- `file_sha256`
- `imported_at`
- `total_rows`
- `inserted_rows`
- `duplicate_rows`

The unique key `(user_id, account_id, file_sha256)` makes confirming the same
file idempotent. Individual overlapping trades are independently deduplicated
by the `projectx_trade_events` source-trade key and its existing
order/timestamp compatibility key.

### `projectx_trade_day_syncs`

Tracks whether an account/day pair is only partially cached or fully complete.

Important columns:

- `user_id`
- `account_id`
- `trade_date`
- `sync_status`
- `last_synced_at`
- `row_count`

This table is used to avoid unnecessary provider calls for already-complete historical days.

### `instrument_metadata`

Lookup table for point conversions.

Important columns:

- `symbol`
- `tick_size`
- `tick_value`

Default rows are seeded at startup for `MNQ`, `MES`, `NQ`, `ES`, `MGC`, and `SIL`.

### Legacy Databento compatibility tables

These global tables are intentionally not user-scoped. They may remain on an
installation that applied the retired relational-market-data migration or used
the legacy importer. Fresh schemas omit them, and baseline/adoption validation
does not require them. Their ORM definitions and importer remain available only
for compatibility. Existing tables and imported rows are left untouched: no
startup, migration, or cache-building path drops them. The canonical historical source is the local versioned Parquet/mmap cache.

- `databento_import_batches` records the Databento job/archive identity,
  archive SHA-256, manifest, progress counts, status, and error/timestamp
  provenance. Job IDs and archive hashes are unique, so retrying an archive is
  idempotent.
- `databento_import_files` records each DBN/zstd member and its SHA-256 under a
  batch. `(batch_id, filename)` is unique.
- `databento_instruments` maps `(dataset, instrument_id)` to its raw and root
  symbols, lifecycle timestamps, instrument/security classes, nanounit tick
  metadata, latest definition timestamp, and source-file hash.
- `databento_ohlcv_1m` stores the immutable raw one-minute bars. Its primary key
  is `(dataset, instrument_id, ts_event)`; OHLC values are signed 64-bit integer
  nanounits, volume is nonnegative, and each row retains the source-file hash
  and exchange trading date.
- `databento_roll_schedule` maps `(root_symbol, trading_date)` to the selected
  outright contract and policy version. `decision_session_date`, prior/current
  instrument ID, and prior-session current/candidate volumes make each roll
  decision auditable. Non-initial decisions are constrained to use a session
  strictly before the trading date, preventing same-session lookahead.

The raw-bar primary key keeps the legacy importer retry-safe. It is no longer
on the Run hot path.

### `position_lifecycles`

Optional lifecycle records for advanced MAE/MFE tracking when the streaming runtime is enabled.

Important columns:

- `user_id`
- `account_id`
- `contract_id`
- `symbol`
- `opened_at`
- `closed_at`
- `side`
- `max_qty`
- `realized_pnl_usd`
- `mae_usd`
- `mfe_usd`
- `mae_points`
- `mfe_points`

These rows are not required for the core routed product.

### `bot_configs`

Server-owned, user-scoped TopBot configuration. Important columns include `user_id`, provider account ID, contract and symbol, `strategy_type`, normalized `strategy_params`, timeframe and history settings, risk limits, trading-session controls, `enabled`, and `execution_mode`.

`execution_mode` defaults to `dry_run`. It does not authorize live routing on its own: live execution also requires an explicit live request, explicit per-request confirmation, the disabled-by-default server environment gate, a non-test runtime, an eligible account, and all normal risk gates.

### `bot_backtests`

Immutable, user-scoped evidence for completed deterministic replays. Important columns include:

- `user_id`, nullable `bot_config_id`, and the snapshotted `account_id`
- `engine_version`, `strategy_type`, contract, symbol, and timeframe
- requested and actual start/end timestamps
- starting balance, per-side commission, slippage ticks, tick size, and tick value
- replayed bar count and SHA-256 `input_fingerprint`
- JSON snapshots of the replay-relevant bot configuration, execution assumptions, and returned result

The input fingerprint covers the ordered closed warm-up and execution candles used by the engine. The result snapshot contains metrics, equity and drawdown series, daily/monthly aggregates, trade ledger, and sample-quality warnings. Together with the engine version and assumptions, these values make a saved run auditable without depending on the bot configuration's later mutable state.

`bot_config_id` is nullable and uses `on delete set null`; removing a bot configuration therefore preserves historical results. Reads and writes are scoped by `user_id`. Backtest rows are never order attempts and do not authorize or record live routing.

### `bot_runs`

Lifecycle and health record for a started bot. Important columns include:

- `user_id`, `bot_config_id`, and `account_id`
- `status` and `dry_run`
- `started_at` and `stopped_at`
- `last_heartbeat_at` and `last_evaluated_at`
- `stop_reason` and sanitized `last_error`
- `raw_state`, including the current phase and last closed-candle checkpoint

The valid forward transitions are `running -> stopped`, `running -> blocked`, and `running -> error`. Terminal rows never transition back to running; a later start creates a new row. `uq_bot_runs_one_running_per_config` is a partial unique index over `(user_id, bot_config_id)` for rows whose status is `running`.

### `bot_decisions`

Replayable, user-scoped signal and lifecycle audit records. Signal decisions store the closed-candle timestamp, action, reason, price, quantity, correlation ID, and actionable idempotency key when one exists. Decision types are `signal`, `risk_reject`, `order_attempt`, `lifecycle`, and `duplicate_skip`.

When a repeated or racing evaluation loses the actionable-attempt uniqueness claim, its decision is retained as `duplicate_skip`. Its audit payload references the original attempt and records the same idempotency key, while no second order-attempt row or provider call is created.

### `bot_order_attempts`

Durable execution claims written before any external order submission. Important columns include:

- user, bot, run, decision, account, and contract identifiers
- `execution_mode`, side, type, size, and status
- `correlation_id` and `idempotency_key`
- provider order ID and rejection reason
- request/response audit payloads and timestamps

For an actionable BUY or SELL, the server derives a versioned key from `(user_id, bot_config_id, closed_candle_timestamp_utc, action, execution_mode)`. `uq_bot_order_attempts_idempotency_key` uniquely constrains `(user_id, bot_config_id, idempotency_key)` when the key is non-null. The insert is attempted inside a savepoint so a uniqueness race can be converted into a durable duplicate-skip decision without rolling back the surrounding evaluation audit.

Attempt statuses are `pending`, `dry_run`, `submitted`, `submission_unknown`, `blocked`, `rejected`, `error` and `cancelled`. A `pending` attempt is deliberately never auto-retried. It may represent a crash between the durable local claim and final local recording of the provider result, including the ambiguous case where ProjectX accepted the order. Operators must reconcile the deterministic provider custom tag plus the local correlation/idempotency identifiers before any manual recovery.

### `bot_risk_events`

User-scoped hard-gate audit events tied to a bot and optional run. These rows preserve severity, stable code, human-readable message, and non-secret supporting payload. Risk-blocked evaluations return `risk_blocked`; a started run transitions to `blocked` and the bot configuration is disabled.

### Bot execution operation contract

Evaluation responses expose `evaluated`, `held`, `risk_blocked`, `duplicate_skipped`, `dry_run_attempt`, `submitted`, or `error`, along with a request correlation ID and any actionable idempotency/original-attempt reference. The same identifiers connect API responses, structured application logs, decisions, and attempts.

The [leased worker](../backend/app/bot_worker.py) evaluates eligible running configurations and coordinates account-scoped provider access. Review unresolved order attempts before resuming execution. [Bot diagnostics](../docs/bot-diagnostics.md)

### `journal_entries`

Historical entries are retained after Journal retirement. The former feature stored one entry per account per trading date.

Important columns:

- `user_id`
- `account_id`
- `entry_date`
- `title`
- `mood`
- `tags`
- `body`
- `version`
- `stats_source`
- `stats_json`
- `stats_pulled_at`
- `is_archived`

Important rule:

- uniqueness is `(user_id, account_id, entry_date)`

### `journal_entry_images`

Metadata for image attachments associated with journal entries.

Important columns:

- `user_id`
- `journal_entry_id`
- `account_id`
- `entry_date`
- `filename`
- `mime_type`
- `byte_size`
- `width`
- `height`

The encrypted Journal archive contains the referenced image contents. Preserve the verified host and off-device copies. [Archive and recovery](../docs/supabase-retirement.md)

### `expenses`

Stores account fees and operating costs.

Important columns:

- `user_id`
- `account_id`
- `provider`
- `expense_date`
- `amount_cents`
- `currency`
- `category`
- `account_type`
- `plan_size`
- `description`
- `tags`

Allowed categories:

- `evaluation_fee`
- `activation_fee`
- `reset_fee`
- `data_fee`
- `other`

Allowed account types:

- `no_activation`
- `standard`
- `practice`

Allowed plan sizes:

- `50k`
- `100k`
- `150k`

There is also a unique dedupe index across the practical identifying fields for an expense row.

### `payouts`

Stores realized payouts separately from expenses.

Important columns:

- `user_id`
- `payout_date`
- `amount_cents`
- `currency`
- `notes`

This table powers `/api/payouts`, `/api/payouts/totals`, and the payout section inside the Expenses page.

### `provider_credentials`

Encrypted per-user ProjectX credentials for authenticated deployments.

Important columns:

- `user_id`
- `provider`
- `username_encrypted`
- `api_key_encrypted`

Notes:

- uniqueness is `(user_id, provider)`
- a real `CREDENTIALS_ENCRYPTION_KEY` should be set in non-local environments

### `trades`

Legacy app-defined trade table. It still exists and is still used by old `/metrics/*` and `/trades` endpoints.

Important columns:

- `user_id`
- `account_id`
- `symbol`
- `side`
- `opened_at`
- `closed_at`
- `qty`
- `entry_price`
- `exit_price`
- `pnl`
- `fees`
- `notes`
- `is_rule_break`
- `rule_break_type`

This table is no longer the primary source for the routed dashboard, trades, or journal statistics.

## Current Data Flows

### Account sync

`GET /api/accounts`:

1. calls ProjectX account search
2. normalizes provider account flags
3. upserts `accounts`
4. marks long-missing rows as `MISSING`
5. joins locally known `last_trade_at` values from `projectx_trade_events`

### Trade sync

Trade sync is local-first:

- first request with no local data can trigger a backfill
- incremental sync adds overlap from the latest local timestamp
- day-scoped requests consult `projectx_trade_day_syncs`

Trade ingestion writes to `projectx_trade_events`, not `trades`.

### Historical market-data import and replay

`backend/tools/build_databento_cache.py` validates the DBN/zstd archives and
converts definitions, one-minute OHLCV, and available statistics into local
Parquet partitions for MNQ, MES, NQ, and ES. It resolves Databento instrument
IDs as of each calendar day, filters outright futures from spreads, and writes
volume roll schedules using only the preceding completed Globex session.

Fingerprint-scoped NumPy arrays materialize each requested timeframe. Runtime
loads use memory mapping plus binary-search slicing, so only user/configuration
reads and bounded saved configuration replay results need the application database.
The legacy relational importer is restricted to SQLite fixtures. Keep original
archives and their off-device backups. [Storage policy](../docs/supabase-storage-policy.md)

### Expenses and payouts

- expenses write to `expenses`
- payout records write to `payouts`
- totals are aggregated server-side

The combine-spend helper is partly client-side and may create inferred expense rows through the API.

## Fresh Install vs Existing Database

Use the [setup and migration guide](README.md) for fresh baselining, incremental upgrades and backup-first adoption.

## Important Implementation Notes

- Every major product table is multi-tenant and includes `user_id`
- Local anonymous mode uses the synthetic default UUID `00000000-0000-0000-0000-000000000000`
- The backend contains compatibility patches for older Postgres dev databases
- The main routed analytics read from `projectx_trade_events`
- Prior runs remain in `bot_backtests` for account-scoped reporting
- `projectx_market_candles` is retained for live/evaluation workflows, not historical backtest acquisition
- The legacy `/metrics/*` routes still read from `trades`

## Where To Inspect In Code

- models: `backend/app/models.py`
- DB setup and compatibility patches: `backend/app/db.py`
- API routes: `backend/app/main.py`
- saved historical result reader: `backend/app/services/account_risk_forecast.py`
- trade sync and summary logic: `backend/app/services/projectx_trades.py`
- Journal recovery: [archive tool](../backend/tools/archive_journal.py)
- credentials storage: `backend/app/services/projectx_credentials.py`

## Related Docs

- [Developer guide](../README.md)
- [Database setup](README.md)
