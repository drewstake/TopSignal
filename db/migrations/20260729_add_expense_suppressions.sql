create table if not exists expense_suppressions (
  user_id uuid not null default '00000000-0000-0000-0000-000000000000',
  source text not null,
  account_id bigint not null,
  created_at timestamptz not null default now(),
  constraint expense_suppressions_pkey primary key (user_id, source, account_id),
  constraint expense_suppressions_source_nonempty_check check (length(trim(source)) > 0),
  constraint expense_suppressions_account_id_positive_check check (account_id > 0)
);
