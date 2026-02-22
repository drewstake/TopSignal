alter table journal_entries
  add column if not exists version integer;

update journal_entries
set version = 1
where version is null;

alter table journal_entries
  alter column version set default 1,
  alter column version set not null;

alter table journal_entries
  add column if not exists stats_source text,
  add column if not exists stats_json jsonb,
  add column if not exists stats_pulled_at timestamptz;

create unique index if not exists uq_journal_entries_account_entry_date
  on journal_entries (account_id, entry_date);

create table if not exists journal_entry_images (
  id bigserial primary key,
  journal_entry_id bigint not null references journal_entries(id) on delete cascade,
  account_id bigint not null,
  entry_date date not null,
  filename text not null,
  mime_type text not null,
  byte_size integer not null,
  width integer,
  height integer,
  created_at timestamptz not null default now()
);

create index if not exists idx_journal_entry_images_account_date
  on journal_entry_images (account_id, entry_date);

create index if not exists idx_journal_entry_images_journal_entry
  on journal_entry_images (journal_entry_id);
