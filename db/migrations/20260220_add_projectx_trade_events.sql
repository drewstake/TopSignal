create table if not exists projectx_trade_events (
  id bigserial primary key,
  account_id bigint not null,
  contract_id text not null,
  symbol text,
  side text not null check (side in ('BUY','SELL','UNKNOWN')),
  size numeric(18,6) not null,
  price numeric(18,6) not null,
  trade_timestamp timestamptz not null,
  fees numeric(18,6) not null default 0,
  pnl numeric(18,6),
  order_id text not null,
  source_trade_id text,
  raw_payload jsonb,
  created_at timestamptz not null default now(),
  unique (account_id, order_id, trade_timestamp)
);

create index if not exists idx_projectx_trade_events_account_ts
  on projectx_trade_events (account_id, trade_timestamp desc);

create index if not exists idx_projectx_trade_events_symbol_ts
  on projectx_trade_events (symbol, trade_timestamp desc);
