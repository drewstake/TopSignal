# Database Setup And Migrations

This folder contains:

- `schema.sql`: full schema for a fresh PostgreSQL database
- `migrations/`: incremental SQL updates for older databases

TopSignal does not currently ship a migration runner. Apply SQL directly with `psql`.

## Local Postgres Container

Start the bundled database:

```powershell
docker compose up -d db
```

The container from `docker-compose.yml` is:

- container name: `topsignal_db`
- database: `topsignal`
- user: `topsignal`
- password: `topsignal_password`

## Fresh Database

Apply the full schema:

```powershell
Get-Content .\db\schema.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
```

## Existing Database

If the database already exists and predates newer features, apply the missing migrations in order.

Current migration list:

```text
20260220_add_projectx_trade_events.sql
20260220_add_rule_break_fields.sql
20260221_add_journal_entries.sql
20260221_add_projectx_trade_day_syncs.sql
20260222_journal_entry_images_and_versioning.sql
20260226_add_expenses.sql
20260301_add_account_state_fields.sql
20260302_add_instrument_metadata_and_position_lifecycles.sql
20260302_add_multitenant_auth_and_provider_credentials.sql
20260302_add_projectx_trade_events_perf_indexes.sql
20260307_add_payouts.sql
20260309_add_account_display_name.sql
20260426_add_projectx_trade_events_lifecycle_index.sql
20260426_add_trading_bot_tables.sql
20260427_relax_legacy_bot_schema.sql
20260428_drop_legacy_bot_config_unique_constraint.sql
20260506_add_support_resistance_bot_strategy.sql
20260506_add_liquidity_sweep_retest_bot_strategy.sql
20260506_add_orb_fibonacci_pullback_bot_strategy.sql
20260507_add_projectx_trade_day_sync_windows.sql
20260507_add_unique_main_account_index.sql
20260507_add_user_scoped_infra_indexes.sql
20260507_add_bollinger_mean_reversion_bot_strategy.sql
20260507_add_fvg_sweep_mss_bot_strategy.sql
20260507_add_relative_strength_vs_spy_bot_strategy.sql
20260508_add_atr_adjusted_relative_strength_bot_strategy.sql
20260508_add_ema_trend_pullback_bot_strategy.sql
20260508_add_vwap_gap_retrace_bot_strategy.sql
20260509_add_supertrend_pivot_bot_strategy.sql
20260510_add_ema_scalping_bot_strategy.sql
20260510_add_pullback_trap_reversal_bot_strategy.sql
20260511_add_opening_rvol_breakout_bot_strategy.sql
20260522_add_expense_source_id.sql
20260709_add_bot_execution_safety.sql
20260709_add_bot_backtests.sql
20260709_add_topbot_adaptive_strategy.sql
```

Example PowerShell application loop:

```powershell
$migrations = @(
  "20260220_add_projectx_trade_events.sql",
  "20260220_add_rule_break_fields.sql",
  "20260221_add_journal_entries.sql",
  "20260221_add_projectx_trade_day_syncs.sql",
  "20260222_journal_entry_images_and_versioning.sql",
  "20260226_add_expenses.sql",
  "20260301_add_account_state_fields.sql",
  "20260302_add_instrument_metadata_and_position_lifecycles.sql",
  "20260302_add_multitenant_auth_and_provider_credentials.sql",
  "20260302_add_projectx_trade_events_perf_indexes.sql",
  "20260307_add_payouts.sql",
  "20260309_add_account_display_name.sql",
  "20260426_add_projectx_trade_events_lifecycle_index.sql",
  "20260426_add_trading_bot_tables.sql",
  "20260427_relax_legacy_bot_schema.sql",
  "20260428_drop_legacy_bot_config_unique_constraint.sql",
  "20260506_add_support_resistance_bot_strategy.sql",
  "20260506_add_liquidity_sweep_retest_bot_strategy.sql",
  "20260506_add_orb_fibonacci_pullback_bot_strategy.sql",
  "20260507_add_projectx_trade_day_sync_windows.sql",
  "20260507_add_unique_main_account_index.sql",
  "20260507_add_user_scoped_infra_indexes.sql",
  "20260507_add_bollinger_mean_reversion_bot_strategy.sql",
  "20260507_add_fvg_sweep_mss_bot_strategy.sql",
  "20260507_add_relative_strength_vs_spy_bot_strategy.sql",
  "20260508_add_atr_adjusted_relative_strength_bot_strategy.sql",
  "20260508_add_ema_trend_pullback_bot_strategy.sql",
  "20260508_add_vwap_gap_retrace_bot_strategy.sql",
  "20260509_add_supertrend_pivot_bot_strategy.sql",
  "20260510_add_ema_scalping_bot_strategy.sql",
  "20260510_add_pullback_trap_reversal_bot_strategy.sql",
  "20260511_add_opening_rvol_breakout_bot_strategy.sql",
  "20260522_add_expense_source_id.sql",
  "20260709_add_bot_execution_safety.sql",
  "20260709_add_bot_backtests.sql",
  "20260709_add_topbot_adaptive_strategy.sql"
)

foreach ($name in $migrations) {
  Get-Content ".\db\migrations\$name" | docker exec -i topsignal_db psql -U topsignal -d topsignal
}
```

## Compatibility Patches On Startup

The backend also applies some safe Postgres compatibility patches in `backend/app/db.py` during startup.

Those patches currently help older dev databases by:

- adding missing `accounts` columns such as `display_name`, `account_state`, and `is_main`
- backfilling journal versioning and image support
- ensuring multi-tenant `user_id` columns and related indexes
- creating `provider_credentials` when absent
- adding the bot run error/heartbeat fields and bot execution idempotency indexes
- preserving unsupported legacy bot strategy rows without silently converting their strategy type
- seeding default `instrument_metadata`

Treat those patches as a safety net, not as the primary schema-upgrade path.

For faster local dev startup, the root `npm run dev` backend wrapper sets `TOPSIGNAL_DB_SCHEMA_INIT=skip` unless you override it. Run the compatibility pass explicitly when you change schema-related code or apply new migrations:

```powershell
npm run db:init
```

### Bot execution-safety migration

`20260709_add_bot_execution_safety.sql` must be applied to existing databases before running the new execution code when startup compatibility initialization is disabled. It:

- adds `last_evaluated_at` and `last_error` to `bot_runs`
- closes older duplicate running rows and installs a partial unique index allowing one running run per user and bot configuration
- adds correlation and idempotency identifiers to bot decisions
- adds `duplicate_skip` to the decision-type constraint
- adds execution mode, correlation, and idempotency identifiers to order attempts
- backfills legacy attempt execution mode conservatively and installs the partial unique actionable-attempt index

The migration does not resubmit or otherwise reinterpret existing `pending` attempts. Reconcile those rows with ProjectX before manual intervention; automatic retry is intentionally unsafe when the provider outcome is unknown.

### Bot backtest migration

`20260709_add_bot_backtests.sql` creates the user-scoped `bot_backtests` table and its lookup indexes. Each row preserves the bot and execution-setting snapshots, requested and actual ranges, instrument tick metadata, the SHA-256 candle-input fingerprint, engine version, metrics, equity/drawdown series, period summaries, trade ledger, and warnings returned by one completed replay. `bot_config_id` uses `on delete set null`, so deleting a mutable bot configuration does not destroy its historical backtest evidence.

Apply this migration before using `POST /api/bots/{id}/backtests` when startup compatibility initialization is disabled. Backtests read stored candles; the migration does not fetch market data, rerun historical results, or invoke an order path.

## Verifying The Schema

Connect with `psql`:

```powershell
docker exec -it topsignal_db psql -U topsignal -d topsignal
```

Useful checks:

```sql
\dt
select count(*) from accounts;
select count(*) from projectx_trade_events;
select count(*) from journal_entries;
select count(*) from expenses;
select count(*) from payouts;
select count(*) from bot_runs;
select count(*) from bot_order_attempts;
select count(*) from bot_backtests;
```

Bot safety-index checks:

```sql
select indexname, indexdef
from pg_indexes
where indexname in (
  'uq_bot_runs_one_running_per_config',
  'uq_bot_order_attempts_idempotency_key'
);

select id, user_id, bot_config_id, correlation_id, idempotency_key, created_at
from bot_order_attempts
where status = 'pending'
order by created_at;
```

Backtest snapshot checks:

```sql
select
  id,
  user_id,
  bot_config_id,
  engine_version,
  strategy_type,
  bar_count,
  input_fingerprint,
  created_at
from bot_backtests
order by created_at desc
limit 20;
```

Recent trade-event sample:

```sql
select id, account_id, symbol, side, size, price, pnl, fees, trade_timestamp
from projectx_trade_events
order by trade_timestamp desc
limit 20;
```

## Notes

- This repo does not include `db/seed.sql`
- For the current product, `projectx_trade_events` is the primary analytics dataset
- The legacy `trades` table still exists for old `/metrics/*` routes
