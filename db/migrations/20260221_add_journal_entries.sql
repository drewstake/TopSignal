create table if not exists journal_entries (
  id bigserial primary key,
  account_id bigint not null,
  entry_date date not null,
  title text not null,
  mood text not null check (mood in ('Focused','Neutral','Frustrated','Confident')),
  tags text[] not null default '{}',
  body text not null default '',
  is_archived boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_journal_entries_account_archived_date_updated
  on journal_entries (account_id, is_archived, entry_date desc, updated_at desc);

create index if not exists idx_journal_entries_account_mood_date
  on journal_entries (account_id, mood, entry_date desc);
