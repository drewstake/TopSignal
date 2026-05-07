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
  "20260511_add_opening_rvol_breakout_bot_strategy.sql"
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
- seeding default `instrument_metadata`

Treat those patches as a safety net, not as the primary schema-upgrade path.

For faster local dev startup, the root `npm run dev` backend wrapper sets `TOPSIGNAL_DB_SCHEMA_INIT=skip` unless you override it. Run the compatibility pass explicitly when you change schema-related code or apply new migrations:

```powershell
npm run db:init
```

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
