# Database Setup And Migrations

This folder contains:

- `schema.sql`: full schema for a fresh PostgreSQL database
- `migrations/`: incremental SQL updates for older databases
- `backend/tools/migrate_db.py`: ordered, checksummed migration runner

The runner records applied filenames and SHA-256 checksums in
`topsignal_schema_migrations`, serializes upgrades with a PostgreSQL advisory
lock, and applies each migration in its own transaction.

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

Then validate the current schema, initialize its migration ledger without
replaying historical upgrades, and verify it. Baseline mode is intentionally
limited to a brand-new database whose application tables are still empty:

```powershell
npm run db:baseline
npm run db:check
```

## Existing Database

If the database already exists, use the migration runner rather than piping SQL
files manually:

```powershell
npm run db:migrate
npm run db:check
```

Do not baseline a populated pre-runner database and do not replay the entire
history over a schema that is already current. Use this one-time, backup-first
adoption sequence during a maintenance window with application writers stopped:

```powershell
# 1. Create and verify a database backup outside TopSignal.
# 2. Bring legacy compatibility-managed schema objects to this release.
npm run db:init
# 3. Validate every ORM table/column, required index, critical constraint,
#    nullable audit FK, and existing bot quantities before recording history.
npm run db:adopt-current
npm run db:check
```

`db:adopt-current` never replays historical data migrations. It requires an
explicit populated-database acknowledgement, refuses a partial ledger, and
records history only after current-schema validation succeeds. Restore the
backup and investigate rather than bypassing a failed validation.

The quantity-safety migration deliberately fails if a legacy bot configuration
contains fractional, non-finite, non-positive, or greater-than-10,000 contract
quantities. Review and correct those rows before retrying; the migration will
not preserve an unvalidated live-order safety state.

The list below documents the historical order. The runner discovers this order
from filenames, records checksums, stops on the first error, and will not rerun
an applied migration.

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
20260630_add_topbot_adaptive_strategy.sql
20260709_add_bot_execution_safety.sql
20260709_add_bot_backtests.sql
20260709_add_topbot_adaptive_strategy.sql
20260710_add_bot_submission_unknown_status.sql
20260710_add_cached_account_balance.sql
20260710_enforce_bot_quantity_safety.sql
20260710_preserve_bot_order_attempt_audit.sql
20260711_add_databento_historical_market_data.sql
20260711_seed_nq_es_instrument_metadata.sql
20260723_add_topstep_trade_imports.sql
20260724_add_account_trade_data_source.sql
20260724_restore_express_trade_data_source.sql
20260725_harden_topstep_trade_imports.sql
20260725_live_account_archiving.sql
```

`20260711_add_databento_historical_market_data.sql` remains in the checksummed
ledger for installations that already applied it, but the migration runner
records it as a retired no-op when it is still pending.

Legacy manual PowerShell application loop (recovery/debugging only; prefer the
migration runner above):

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
  "20260630_add_topbot_adaptive_strategy.sql",
  "20260709_add_bot_execution_safety.sql",
  "20260709_add_bot_backtests.sql",
  "20260709_add_topbot_adaptive_strategy.sql",
  "20260710_add_bot_submission_unknown_status.sql",
  "20260710_add_cached_account_balance.sql",
  "20260710_enforce_bot_quantity_safety.sql",
  "20260710_preserve_bot_order_attempt_audit.sql",
  "20260711_seed_nq_es_instrument_metadata.sql",
  "20260723_add_topstep_trade_imports.sql",
  "20260724_add_account_trade_data_source.sql",
  "20260724_restore_express_trade_data_source.sql",
  "20260725_harden_topstep_trade_imports.sql",
  "20260725_live_account_archiving.sql"
)

foreach ($name in $migrations) {
  Get-Content ".\db\migrations\$name" | docker exec -i topsignal_db psql -U topsignal -d topsignal
}
```

## Compatibility Patches On Startup

The backend also applies some safe Postgres compatibility patches in `backend/app/db.py` during startup.

Those patches currently help older dev databases by:

- adding missing `accounts` columns such as `display_name`, `account_state`, `trade_data_source`, and `is_main`
- backfilling journal versioning and image support
- ensuring multi-tenant `user_id` columns and related indexes
- creating `provider_credentials` when absent
- seeding default `instrument_metadata`

Treat those patches as a temporary safety net, not as the primary
schema-upgrade path. Production deployments should run migrations before
starting application instances and use `TOPSIGNAL_DB_SCHEMA_INIT=skip`.

For faster local dev startup, the root `npm run dev` backend wrapper sets
`TOPSIGNAL_DB_SCHEMA_INIT=skip` unless you override it. Apply and verify
migrations explicitly when schema-related code changes:

```powershell
npm run db:migrate
npm run db:check
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

Only upgraded installations that already retain the optional legacy
`databento_*` compatibility tables can query those tables directly.

Recent trade-event sample:

```sql
select id, account_id, symbol, side, size, price, pnl, fees, trade_timestamp
from projectx_trade_events
order by trade_timestamp desc
limit 20;
```

## Notes

- This repo does not include `db/seed.sql`
- Databento is the historical market-data source for backtests; canonical raw bars, rolls, and mmap timeframes are local cache artifacts. Fresh schemas omit the retired relational market tables, while upgraded installations retain any existing `databento_*` tables untouched and production replay never queries them
- ProjectX remains the account, execution, position, journaling, analytics, and order-routing source
- The legacy `trades` table still exists for old `/metrics/*` routes
