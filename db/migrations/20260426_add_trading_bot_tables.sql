create table if not exists projectx_market_candles (
  id bigserial primary key,
  user_id uuid not null default '00000000-0000-0000-0000-000000000000',
  contract_id text not null,
  symbol text,
  live boolean not null default false,
  unit text not null check (unit in ('second','minute','hour','day','week','month')),
  unit_number integer not null check (unit_number > 0),
  candle_timestamp timestamptz not null,
  open_price numeric(18,6) not null,
  high_price numeric(18,6) not null,
  low_price numeric(18,6) not null,
  close_price numeric(18,6) not null,
  volume numeric(18,6) not null default 0,
  is_partial boolean not null default false,
  source text not null default 'projectx',
  raw_payload jsonb,
  fetched_at timestamptz not null default now(),
  unique (user_id, contract_id, live, unit, unit_number, candle_timestamp)
);

create index if not exists idx_projectx_market_candles_contract_ts
  on projectx_market_candles (user_id, contract_id, live, unit, unit_number, candle_timestamp);

create table if not exists bot_configs (
  id bigserial primary key,
  user_id uuid not null default '00000000-0000-0000-0000-000000000000',
  account_id bigint not null,
  name text not null,
  provider text not null default 'projectx',
  enabled boolean not null default false,
  execution_mode text not null default 'dry_run' check (execution_mode in ('dry_run','live')),
  strategy_type text not null default 'sma_cross' check (strategy_type in ('sma_cross')),
  contract_id text not null,
  symbol text,
  timeframe_unit text not null default 'minute' check (timeframe_unit in ('second','minute','hour','day','week','month')),
  timeframe_unit_number integer not null default 5 check (timeframe_unit_number > 0),
  lookback_bars integer not null default 200 check (lookback_bars >= 25),
  fast_period integer not null default 9 check (fast_period > 0),
  slow_period integer not null default 21 check (slow_period > fast_period),
  order_size numeric(18,6) not null default 1 check (order_size > 0),
  max_contracts numeric(18,6) not null default 1 check (max_contracts > 0),
  max_daily_loss numeric(18,6) not null default 250 check (max_daily_loss >= 0),
  max_trades_per_day integer not null default 3 check (max_trades_per_day >= 0),
  max_open_position numeric(18,6) not null default 1 check (max_open_position > 0),
  allowed_contracts jsonb not null default '[]'::jsonb,
  trading_start_time text not null default '09:30',
  trading_end_time text not null default '15:45',
  cooldown_seconds integer not null default 300 check (cooldown_seconds >= 0),
  max_data_staleness_seconds integer not null default 600 check (max_data_staleness_seconds > 0),
  allow_market_depth boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_bot_configs_user_account
  on bot_configs (user_id, account_id);

create index if not exists idx_bot_configs_user_enabled
  on bot_configs (user_id, enabled);

create table if not exists bot_runs (
  id bigserial primary key,
  user_id uuid not null default '00000000-0000-0000-0000-000000000000',
  bot_config_id bigint not null references bot_configs(id) on delete cascade,
  account_id bigint not null,
  status text not null default 'running' check (status in ('running','stopped','blocked','error')),
  dry_run boolean not null default true,
  started_at timestamptz not null default now(),
  stopped_at timestamptz,
  stop_reason text,
  last_heartbeat_at timestamptz,
  raw_state jsonb
);

create index if not exists idx_bot_runs_config_started
  on bot_runs (user_id, bot_config_id, started_at);

create index if not exists idx_bot_runs_account_status
  on bot_runs (user_id, account_id, status);

create table if not exists bot_decisions (
  id bigserial primary key,
  user_id uuid not null default '00000000-0000-0000-0000-000000000000',
  bot_config_id bigint not null references bot_configs(id) on delete cascade,
  bot_run_id bigint references bot_runs(id) on delete set null,
  account_id bigint not null,
  contract_id text not null,
  symbol text,
  decision_type text not null check (decision_type in ('signal','risk_reject','order_attempt','lifecycle')),
  action text not null check (action in ('BUY','SELL','HOLD','NONE','STOP')),
  reason text not null,
  candle_timestamp timestamptz,
  price numeric(18,6),
  quantity numeric(18,6),
  raw_payload jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_bot_decisions_config_created
  on bot_decisions (user_id, bot_config_id, created_at);

create table if not exists bot_order_attempts (
  id bigserial primary key,
  user_id uuid not null default '00000000-0000-0000-0000-000000000000',
  bot_config_id bigint not null references bot_configs(id) on delete cascade,
  bot_run_id bigint references bot_runs(id) on delete set null,
  bot_decision_id bigint references bot_decisions(id) on delete set null,
  account_id bigint not null,
  contract_id text not null,
  side text not null check (side in ('BUY','SELL')),
  order_type text not null default 'market' check (order_type in ('market','limit','stop','trailing_stop')),
  size numeric(18,6) not null check (size > 0),
  limit_price numeric(18,6),
  stop_price numeric(18,6),
  trail_price numeric(18,6),
  status text not null default 'pending' check (status in ('pending','dry_run','submitted','blocked','rejected','error')),
  provider_order_id text,
  rejection_reason text,
  raw_request jsonb,
  raw_response jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_bot_order_attempts_config_created
  on bot_order_attempts (user_id, bot_config_id, created_at);

create index if not exists idx_bot_order_attempts_account_created
  on bot_order_attempts (user_id, account_id, created_at);

create table if not exists bot_risk_events (
  id bigserial primary key,
  user_id uuid not null default '00000000-0000-0000-0000-000000000000',
  bot_config_id bigint not null references bot_configs(id) on delete cascade,
  bot_run_id bigint references bot_runs(id) on delete set null,
  account_id bigint not null,
  severity text not null default 'warning' check (severity in ('info','warning','critical')),
  code text not null,
  message text not null,
  raw_payload jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_bot_risk_events_config_created
  on bot_risk_events (user_id, bot_config_id, created_at);
