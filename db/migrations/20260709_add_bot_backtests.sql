-- Persist reproducible, user-scoped bot backtest inputs and result snapshots.

create table if not exists bot_backtests (
  id bigserial primary key,
  user_id uuid not null default '00000000-0000-0000-0000-000000000000',
  bot_config_id bigint references bot_configs(id) on delete set null,
  account_id bigint not null,
  engine_version text not null,
  strategy_type text not null,
  contract_id text not null,
  symbol text,
  timeframe_unit text not null,
  timeframe_unit_number integer not null,
  requested_start timestamptz not null,
  requested_end timestamptz not null,
  actual_start timestamptz not null,
  actual_end timestamptz not null,
  starting_balance numeric(18,6) not null,
  commission_per_contract numeric(18,6) not null default 0,
  slippage_ticks numeric(18,6) not null default 0,
  tick_size numeric(18,6) not null,
  tick_value numeric(18,6) not null,
  bar_count integer not null,
  input_fingerprint text not null,
  config_snapshot jsonb not null,
  assumptions_snapshot jsonb not null,
  result_snapshot jsonb not null,
  created_at timestamptz not null default now(),
  constraint bot_backtests_requested_range_check check (requested_end > requested_start),
  constraint bot_backtests_actual_range_check check (actual_end >= actual_start),
  constraint bot_backtests_starting_balance_positive_check check (starting_balance > 0),
  constraint bot_backtests_commission_nonnegative_check check (commission_per_contract >= 0),
  constraint bot_backtests_slippage_nonnegative_check check (slippage_ticks >= 0),
  constraint bot_backtests_tick_size_positive_check check (tick_size > 0),
  constraint bot_backtests_tick_value_positive_check check (tick_value > 0),
  constraint bot_backtests_timeframe_positive_check check (timeframe_unit_number > 0),
  constraint bot_backtests_bar_count_positive_check check (bar_count > 0)
);

create index if not exists idx_bot_backtests_user_config_created
  on bot_backtests (user_id, bot_config_id, created_at desc);

create index if not exists idx_bot_backtests_user_created
  on bot_backtests (user_id, created_at desc);
