-- Enforce the provider's whole-contract and absolute quantity safety bounds.
-- Fail the upgrade instead of leaving legacy rows outside the validated safety
-- contract. Operators must review and correct those rows before retrying.

do $migration$
begin
  if exists (
    select 1
    from bot_configs
    where order_size is null
       or order_size::text in ('NaN', 'Infinity', '-Infinity')
       or order_size <= 0
       or order_size > 10000
       or order_size <> trunc(order_size)
       or max_contracts is null
       or max_contracts::text in ('NaN', 'Infinity', '-Infinity')
       or max_contracts <= 0
       or max_contracts > 10000
       or max_contracts <> trunc(max_contracts)
       or max_open_position is null
       or max_open_position::text in ('NaN', 'Infinity', '-Infinity')
       or max_open_position <= 0
       or max_open_position > 10000
       or max_open_position <> trunc(max_open_position)
  ) then
    raise exception
      'Unsafe bot contract quantities exist; correct order_size, max_contracts, and max_open_position before retrying this migration.';
  end if;
end
$migration$;

alter table bot_configs
  drop constraint if exists bot_configs_order_size_supported_check;
alter table bot_configs
  add constraint bot_configs_order_size_supported_check
  check (order_size <= 10000 and order_size = trunc(order_size));

alter table bot_configs
  drop constraint if exists bot_configs_max_contracts_supported_check;
alter table bot_configs
  add constraint bot_configs_max_contracts_supported_check
  check (max_contracts <= 10000 and max_contracts = trunc(max_contracts));

alter table bot_configs
  drop constraint if exists bot_configs_max_open_position_supported_check;
alter table bot_configs
  add constraint bot_configs_max_open_position_supported_check
  check (max_open_position <= 10000 and max_open_position = trunc(max_open_position));
