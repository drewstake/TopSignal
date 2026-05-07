alter table bot_configs
  add column if not exists strategy_params jsonb not null default '{}'::jsonb;

alter table bot_configs
  drop constraint if exists bot_configs_strategy_type_check;

update bot_configs
set strategy_type = 'sma_cross'
where strategy_type is null
   or strategy_type not in ('sma_cross', 'support_resistance');

alter table bot_configs
  alter column strategy_type set default 'sma_cross';

alter table bot_configs
  add constraint bot_configs_strategy_type_check
  check (strategy_type in ('sma_cross', 'support_resistance'));
