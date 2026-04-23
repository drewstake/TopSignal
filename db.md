# TopSignal Database Guide

This document describes the database as it exists in the current codebase, not the earlier single-table prototype.

## Summary

TopSignal uses PostgreSQL as a local-first analytics cache and application store.

It persists:

- ProjectX account metadata
- normalized ProjectX trade events
- daily trade-sync completeness state
- journal entries and journal images
- expenses and payouts
- optional per-user encrypted provider credentials
- optional position lifecycle snapshots from the streaming runtime

The main analytics path today is `projectx_trade_events`, not the legacy `trades` table.

## Persistence Strategy

The repo uses three layers together:

1. `db/schema.sql` for fresh databases
2. raw SQL files in `db/migrations/` for incremental upgrades
3. backend startup compatibility patches in `backend/app/db.py`

There is no Alembic or other migration runner in this repository.

The startup compatibility code currently backfills:

- older `accounts` columns such as `display_name`, `account_state`, and `is_main`
- journal versioning and image-storage support
- multi-tenant `user_id` columns and related indexes
- `provider_credentials`
- default `instrument_metadata`

Those startup patches help older dev databases boot, but they are not a replacement for keeping the schema current.

## Core Tables

### `accounts`

Local representation of ProjectX accounts.

Important columns:

- `user_id`
- `provider`
- `external_id`
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
- `pnl`
- `order_id`
- `source_trade_id`
- `status`
- `raw_payload`

Deduplication rules:

- preferred unique key: `(user_id, account_id, source_trade_id)`
- fallback unique key: `(user_id, account_id, order_id, trade_timestamp)`

Behavior notes:

- voided or canceled provider rows are skipped at ingest time
- `pnl = null` rows are treated as open-leg or half-turn events and do not count as closed trades

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

Default rows are seeded at startup for `MNQ`, `MES`, `MGC`, and `SIL`.

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

### `journal_entries`

One journal entry per account per trading date.

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

Binary image storage itself is either:

- local disk
- Supabase Storage

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

### Journal

Journal writes are versioned updates against `journal_entries`.

Image uploads:

1. store the binary in the configured storage backend
2. store metadata in `journal_entry_images`

Trade-stat pulls compute a snapshot and store it in `journal_entries.stats_json`.

### Expenses and payouts

- expenses write to `expenses`
- payout records write to `payouts`
- totals are aggregated server-side

The combine-spend helper is partly client-side and may create inferred expense rows through the API.

## Fresh Install vs Existing Database

For a fresh database:

- apply `db/schema.sql`

For an existing database:

- apply the missing SQL files in `db/migrations/`

The ordered migration list is documented in [db/README.md](db/README.md).

## Important Implementation Notes

- Every major product table is multi-tenant and includes `user_id`
- Local anonymous mode uses the synthetic default UUID `00000000-0000-0000-0000-000000000000`
- The backend contains compatibility patches for older Postgres dev databases
- The main routed analytics read from `projectx_trade_events`
- The legacy `/metrics/*` routes still read from `trades`

## Where To Inspect In Code

- models: `backend/app/models.py`
- DB setup and compatibility patches: `backend/app/db.py`
- API routes: `backend/app/main.py`
- trade sync and summary logic: `backend/app/services/projectx_trades.py`
- journal services: `backend/app/services/journal.py`
- credentials storage: `backend/app/services/projectx_credentials.py`

## Related Docs

- [README.md](README.md)
- [db/README.md](db/README.md)
