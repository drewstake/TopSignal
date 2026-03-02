alter table accounts
  add column if not exists account_state text,
  add column if not exists can_trade boolean,
  add column if not exists is_visible boolean,
  add column if not exists first_seen_at timestamptz,
  add column if not exists last_seen_at timestamptz,
  add column if not exists last_missing_at timestamptz,
  add column if not exists is_main boolean;

update accounts
set account_state = 'ACTIVE'
where account_state is null;

alter table accounts
  alter column account_state set default 'ACTIVE',
  alter column account_state set not null;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'accounts_account_state_check'
  ) then
    alter table accounts
      add constraint accounts_account_state_check
      check (account_state in ('ACTIVE','LOCKED_OUT','HIDDEN','MISSING'));
  end if;
end $$;

update accounts
set is_main = false
where is_main is null;

alter table accounts
  alter column is_main set default false,
  alter column is_main set not null;

create index if not exists idx_accounts_is_main
  on accounts (is_main);

create index if not exists idx_accounts_account_state
  on accounts (account_state);
