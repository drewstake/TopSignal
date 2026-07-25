create table if not exists trade_import_batches (
  id bigserial primary key,
  user_id uuid not null default '00000000-0000-0000-0000-000000000000',
  account_id bigint not null,
  source_file_name text not null,
  file_sha256 text not null,
  total_rows integer not null default 0,
  inserted_rows integer not null default 0,
  duplicate_rows integer not null default 0,
  imported_at timestamptz not null default now(),
  constraint trade_import_batches_sha256_length_check
    check (length(file_sha256) = 64),
  constraint trade_import_batches_counts_nonnegative_check
    check (total_rows >= 0 and inserted_rows >= 0 and duplicate_rows >= 0),
  constraint uq_trade_import_batches_account_file
    unique (user_id, account_id, file_sha256)
);

create index if not exists idx_trade_import_batches_user_account_imported
  on trade_import_batches (user_id, account_id, imported_at desc);

alter table projectx_trade_events
  add column if not exists commissions numeric(18,6),
  add column if not exists fee_scope text not null default 'per_side',
  add column if not exists trade_date date,
  add column if not exists entry_timestamp timestamptz,
  add column if not exists entry_price numeric(18,6),
  add column if not exists import_batch_id bigint;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'projectx_trade_events_fee_scope_check'
  ) then
    alter table projectx_trade_events
      add constraint projectx_trade_events_fee_scope_check
      check (fee_scope in ('per_side','round_turn'));
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'projectx_trade_events_import_batch_id_fkey'
  ) then
    alter table projectx_trade_events
      add constraint projectx_trade_events_import_batch_id_fkey
      foreign key (import_batch_id)
      references trade_import_batches(id)
      on delete set null;
  end if;
end $$;

create index if not exists idx_projectx_trade_events_import_batch
  on projectx_trade_events (import_batch_id);
