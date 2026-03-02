-- Add tenant scoping + per-user provider credentials for Supabase-first deployment.

alter table if exists accounts
  add column if not exists user_id uuid not null default '00000000-0000-0000-0000-000000000000';
alter table if exists trades
  add column if not exists user_id uuid not null default '00000000-0000-0000-0000-000000000000';
alter table if exists projectx_trade_events
  add column if not exists user_id uuid not null default '00000000-0000-0000-0000-000000000000';
alter table if exists projectx_trade_day_syncs
  add column if not exists user_id uuid not null default '00000000-0000-0000-0000-000000000000';
alter table if exists position_lifecycles
  add column if not exists user_id uuid not null default '00000000-0000-0000-0000-000000000000';
alter table if exists journal_entries
  add column if not exists user_id uuid not null default '00000000-0000-0000-0000-000000000000';
alter table if exists journal_entry_images
  add column if not exists user_id uuid not null default '00000000-0000-0000-0000-000000000000';
alter table if exists expenses
  add column if not exists user_id uuid not null default '00000000-0000-0000-0000-000000000000';

alter table if exists accounts drop constraint if exists uq_accounts_provider_external_id;
drop index if exists uq_accounts_provider_external_id;
create unique index if not exists uq_accounts_provider_external_id
  on accounts (user_id, provider, external_id);
drop index if exists idx_accounts_is_main;
drop index if exists idx_accounts_account_state;
create index if not exists idx_accounts_is_main on accounts (user_id, is_main);
create index if not exists idx_accounts_account_state on accounts (user_id, account_state);

alter table if exists projectx_trade_events drop constraint if exists uq_projectx_trade_events_account_source_trade;
alter table if exists projectx_trade_events drop constraint if exists uq_projectx_trade_events_account_order_ts;
drop index if exists uq_projectx_trade_events_account_source_trade;
drop index if exists uq_projectx_trade_events_account_order_ts;
create unique index if not exists uq_projectx_trade_events_account_source_trade
  on projectx_trade_events (user_id, account_id, source_trade_id);
create unique index if not exists uq_projectx_trade_events_account_order_ts
  on projectx_trade_events (user_id, account_id, order_id, trade_timestamp);

alter table if exists projectx_trade_day_syncs drop constraint if exists uq_projectx_trade_day_syncs_account_date;
drop index if exists uq_projectx_trade_day_syncs_account_date;
create unique index if not exists uq_projectx_trade_day_syncs_account_date
  on projectx_trade_day_syncs (user_id, account_id, trade_date);

alter table if exists journal_entries drop constraint if exists uq_journal_entries_account_entry_date;
drop index if exists uq_journal_entries_account_entry_date;
create unique index if not exists uq_journal_entries_account_entry_date
  on journal_entries (user_id, account_id, entry_date);
drop index if exists idx_journal_entries_account_archived_date_updated;
drop index if exists idx_journal_entries_account_mood_date;
create index if not exists idx_journal_entries_account_archived_date_updated
  on journal_entries (user_id, account_id, is_archived, entry_date desc, updated_at desc);
create index if not exists idx_journal_entries_account_mood_date
  on journal_entries (user_id, account_id, mood, entry_date desc);

drop index if exists idx_journal_entry_images_account_date;
drop index if exists idx_journal_entry_images_journal_entry;
create index if not exists idx_journal_entry_images_account_date
  on journal_entry_images (user_id, account_id, entry_date);
create index if not exists idx_journal_entry_images_journal_entry
  on journal_entry_images (user_id, journal_entry_id);

drop index if exists idx_position_lifecycles_account_opened;
drop index if exists idx_position_lifecycles_contract_opened;
create index if not exists idx_position_lifecycles_account_opened
  on position_lifecycles (user_id, account_id, opened_at desc);
create index if not exists idx_position_lifecycles_contract_opened
  on position_lifecycles (user_id, contract_id, opened_at desc);

drop index if exists idx_expenses_expense_date;
drop index if exists idx_expenses_account_id;
drop index if exists idx_expenses_category;
drop index if exists uq_expenses_dedupe;
create index if not exists idx_expenses_expense_date on expenses (user_id, expense_date);
create index if not exists idx_expenses_account_id on expenses (user_id, account_id);
create index if not exists idx_expenses_category on expenses (user_id, category);
create unique index if not exists uq_expenses_dedupe
  on expenses (
    user_id,
    expense_date,
    category,
    coalesce(account_type, ''),
    coalesce(plan_size, ''),
    coalesce(account_id, 0),
    amount_cents
  );

create table if not exists provider_credentials (
  id bigserial primary key,
  user_id uuid not null default '00000000-0000-0000-0000-000000000000',
  provider text not null,
  username_encrypted text not null,
  api_key_encrypted text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists uq_provider_credentials_user_provider
  on provider_credentials (user_id, provider);
create index if not exists idx_provider_credentials_user_provider
  on provider_credentials (user_id, provider);
