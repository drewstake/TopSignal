-- Preserve Live CSV history while allowing users to remove dormant accounts
-- from normal selectors. Archived accounts may never remain the main account.

alter table accounts
  add column if not exists archived_at timestamptz;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'accounts_archived_not_main_check'
  ) then
    alter table accounts
      add constraint accounts_archived_not_main_check
      check (archived_at is null or not is_main);
  end if;
end $$;

create index if not exists idx_accounts_user_archived
  on accounts (user_id, archived_at);
