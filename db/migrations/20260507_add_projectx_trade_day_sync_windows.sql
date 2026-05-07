-- Track the exact UTC interval certified by each ProjectX day-sync cache row.

alter table if exists projectx_trade_day_syncs
  add column if not exists window_start timestamptz;

alter table if exists projectx_trade_day_syncs
  add column if not exists window_end timestamptz;
