alter table projectx_trade_events
  add column if not exists status text;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'uq_projectx_trade_events_account_source_trade'
  ) and not exists (
    select 1
    from projectx_trade_events
    where source_trade_id is not null
    group by account_id, source_trade_id
    having count(*) > 1
  ) then
    alter table projectx_trade_events
      add constraint uq_projectx_trade_events_account_source_trade
      unique (account_id, source_trade_id);
  end if;
end;
$$;

create table if not exists projectx_trade_day_syncs (
  id bigserial primary key,
  account_id bigint not null,
  trade_date date not null,
  sync_status text not null default 'partial' check (sync_status in ('partial','complete')),
  last_synced_at timestamptz not null default now(),
  row_count integer,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (account_id, trade_date)
);

create index if not exists idx_projectx_trade_day_syncs_account_date
  on projectx_trade_day_syncs (account_id, trade_date desc);
