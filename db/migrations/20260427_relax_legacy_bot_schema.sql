alter table bot_configs
  drop constraint if exists bot_configs_symbol_check;

alter table bot_configs
  alter column symbol drop not null;

update bot_runs
set execution_mode = case
  when dry_run is false then 'live'
  else 'dry_run'
end
where execution_mode is null;

alter table bot_runs
  alter column execution_mode set default 'dry_run',
  alter column execution_mode drop not null;

update bot_runs
set strategy_slug = 'sma_cross'
where strategy_slug is null or btrim(strategy_slug) = '';

alter table bot_runs
  alter column strategy_slug set default 'sma_cross',
  alter column strategy_slug drop not null,
  alter column parameter_snapshot drop not null,
  alter column risk_snapshot drop not null;
