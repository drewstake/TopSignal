-- Harden Topstep Live imports with relational ownership, expiring staged
-- previews, conflict-safe confirmation, and whole-contract quantities.

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'uq_accounts_id_user_external_id'
  ) then
    alter table accounts
      add constraint uq_accounts_id_user_external_id
      unique (id, user_id, external_id);
  end if;
end $$;

alter table trade_import_batches
  add column if not exists account_row_id bigint,
  add column if not exists account_external_id text;

update trade_import_batches as batch
set account_row_id = account.id,
    account_external_id = account.external_id
from accounts as account
where batch.account_row_id is null
  and account.user_id = batch.user_id
  and account.provider = 'projectx'
  and account.external_id = cast(batch.account_id as text);

do $$
begin
  if exists (
    select 1
    from trade_import_batches
    where account_row_id is null
       or account_external_id is null
       or account_external_id <> cast(account_id as text)
  ) then
    raise exception 'orphaned or mismatched trade import batches exist';
  end if;
  if exists (
    select 1
    from trade_import_batches
    where total_rows <> inserted_rows + duplicate_rows
  ) then
    raise exception 'unbalanced trade import batch counts exist';
  end if;
end $$;

alter table trade_import_batches
  alter column account_row_id set not null,
  alter column account_external_id set not null;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'trade_import_batches_counts_balance_check'
  ) then
    alter table trade_import_batches
      add constraint trade_import_batches_counts_balance_check
      check (total_rows = inserted_rows + duplicate_rows);
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'trade_import_batches_external_id_check'
  ) then
    alter table trade_import_batches
      add constraint trade_import_batches_external_id_check
      check (account_external_id = cast(account_id as text));
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'fk_trade_import_batches_owned_account'
  ) then
    alter table trade_import_batches
      add constraint fk_trade_import_batches_owned_account
      foreign key (account_row_id, user_id, account_external_id)
      references accounts (id, user_id, external_id)
      on delete restrict;
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'uq_trade_import_batches_owned_identity'
  ) then
    alter table trade_import_batches
      add constraint uq_trade_import_batches_owned_identity
      unique (id, user_id, account_id, account_row_id, account_external_id);
  end if;
end $$;

create table if not exists trade_import_previews (
  id bigserial primary key,
  token_hash text not null unique,
  user_id uuid not null default '00000000-0000-0000-0000-000000000000',
  account_id bigint not null,
  account_row_id bigint not null,
  account_external_id text not null,
  source_file_name text not null,
  file_sha256 text not null,
  manifest_version integer not null default 1,
  normalized_manifest jsonb,
  preview_rows jsonb,
  dedupe_snapshot text,
  total_rows integer not null default 0,
  new_rows integer not null default 0,
  duplicate_rows integer not null default 0,
  conflict_rows integer not null default 0,
  status text not null default 'pending',
  outcome_code text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  expires_at timestamptz not null,
  retention_until timestamptz not null,
  confirmed_at timestamptz,
  import_batch_id bigint,
  constraint trade_import_previews_hash_length_check
    check (length(token_hash) = 64 and length(file_sha256) = 64),
  constraint trade_import_previews_manifest_version_check
    check (manifest_version > 0),
  constraint trade_import_previews_counts_nonnegative_check
    check (total_rows >= 0 and new_rows >= 0 and duplicate_rows >= 0 and conflict_rows >= 0),
  constraint trade_import_previews_counts_balance_check
    check (total_rows = new_rows + duplicate_rows + conflict_rows),
  constraint trade_import_previews_status_check
    check (status in ('pending','confirming','committed','expired','stale','conflict','failed')),
  constraint trade_import_previews_external_id_check
    check (account_external_id = cast(account_id as text)),
  constraint fk_trade_import_previews_owned_account
    foreign key (account_row_id, user_id, account_external_id)
    references accounts (id, user_id, external_id) on delete restrict,
  constraint fk_trade_import_previews_owned_batch
    foreign key (import_batch_id, user_id, account_id, account_row_id, account_external_id)
    references trade_import_batches (id, user_id, account_id, account_row_id, account_external_id)
    on delete restrict
);

create index if not exists idx_trade_import_previews_user_account_created
  on trade_import_previews (user_id, account_id, created_at desc);

create index if not exists idx_trade_import_previews_expiry
  on trade_import_previews (status, expires_at);

alter table projectx_trade_events
  add column if not exists account_row_id bigint,
  add column if not exists account_external_id text;

-- Imported events must already agree with their batch before ownership values
-- are backfilled. Abort rather than laundering pre-existing cross-account data.
do $$
begin
  if exists (
    select 1
    from projectx_trade_events as event
    join trade_import_batches as batch on batch.id = event.import_batch_id
    where event.user_id <> batch.user_id
       or event.account_id <> batch.account_id
  ) then
    raise exception 'cross-owner imported trade events exist';
  end if;
end $$;

update projectx_trade_events as event
set account_row_id = batch.account_row_id,
    account_external_id = batch.account_external_id
from trade_import_batches as batch
where event.import_batch_id = batch.id;

-- Provider events may remain relationally unbound, but backfill those with an
-- unambiguous account row so future ownership checks are stronger.
update projectx_trade_events as event
set account_row_id = account.id,
    account_external_id = account.external_id
from accounts as account
where event.account_row_id is null
  and account.user_id = event.user_id
  and account.provider = 'projectx'
  and account.external_id = cast(event.account_id as text);

do $$
begin
  if exists (
    select 1
    from projectx_trade_events
    where import_batch_id is not null
      and (account_row_id is null or account_external_id is null)
  ) then
    raise exception 'orphaned imported trade events exist';
  end if;
  if exists (
    select 1
    from projectx_trade_events
    where size <= 0
       or size > 10000
       or size <> trunc(size)
  ) then
    raise exception 'unsafe projectx trade quantities exist';
  end if;
end $$;

alter table projectx_trade_events
  drop constraint if exists projectx_trade_events_import_batch_id_fkey;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'projectx_trade_events_whole_size_check'
  ) then
    alter table projectx_trade_events
      add constraint projectx_trade_events_whole_size_check
      check (size > 0 and size <= 10000 and size = trunc(size));
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'projectx_trade_events_external_id_check'
  ) then
    alter table projectx_trade_events
      add constraint projectx_trade_events_external_id_check
      check (account_external_id is null or account_external_id = cast(account_id as text));
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'projectx_trade_events_import_account_check'
  ) then
    alter table projectx_trade_events
      add constraint projectx_trade_events_import_account_check
      check (import_batch_id is null or (account_row_id is not null and account_external_id is not null));
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'fk_projectx_trade_events_owned_account'
  ) then
    alter table projectx_trade_events
      add constraint fk_projectx_trade_events_owned_account
      foreign key (account_row_id, user_id, account_external_id)
      references accounts (id, user_id, external_id)
      on delete restrict;
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'fk_projectx_trade_events_owned_batch'
  ) then
    alter table projectx_trade_events
      add constraint fk_projectx_trade_events_owned_batch
      foreign key (import_batch_id, user_id, account_id, account_row_id, account_external_id)
      references trade_import_batches (id, user_id, account_id, account_row_id, account_external_id)
      on delete restrict;
  end if;
end $$;
