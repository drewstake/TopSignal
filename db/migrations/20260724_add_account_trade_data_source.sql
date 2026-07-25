alter table accounts
  add column if not exists trade_data_source text;

update accounts
set trade_data_source = 'projectx'
where trade_data_source is null;

alter table accounts
  alter column trade_data_source set default 'projectx',
  alter column trade_data_source set not null;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'accounts_trade_data_source_check'
  ) then
    alter table accounts
      add constraint accounts_trade_data_source_check
      check (trade_data_source in ('projectx','csv_import'));
  end if;
end $$;
