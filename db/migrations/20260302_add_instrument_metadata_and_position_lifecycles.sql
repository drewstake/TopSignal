create table if not exists instrument_metadata (
  id bigserial primary key,
  symbol text not null unique,
  tick_size numeric(18,6) not null check (tick_size > 0),
  tick_value numeric(18,6) not null check (tick_value > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

insert into instrument_metadata (symbol, tick_size, tick_value)
values
  ('MNQ', 0.25, 0.50),
  ('MES', 0.25, 1.25),
  ('MGC', 0.10, 1.00),
  ('SIL', 0.005, 5.00)
on conflict (symbol) do nothing;

create table if not exists position_lifecycles (
  id bigserial primary key,
  account_id bigint not null,
  contract_id text not null,
  symbol text not null,
  opened_at timestamptz not null,
  closed_at timestamptz not null,
  side text not null check (side in ('LONG','SHORT')),
  max_qty numeric(18,6) not null check (max_qty > 0),
  avg_entry_at_open numeric(18,6),
  realized_pnl_usd numeric(18,6),
  mae_usd numeric(18,6),
  mfe_usd numeric(18,6),
  mae_points numeric(18,6),
  mfe_points numeric(18,6),
  mae_timestamp timestamptz,
  mfe_timestamp timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists idx_position_lifecycles_account_opened
  on position_lifecycles (account_id, opened_at desc);

create index if not exists idx_position_lifecycles_contract_opened
  on position_lifecycles (contract_id, opened_at desc);
