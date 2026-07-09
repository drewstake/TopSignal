-- Add durable bot execution identifiers and enforce a single running run per bot.

alter table bot_runs
  add column if not exists last_evaluated_at timestamptz;

alter table bot_runs
  add column if not exists last_error text;

-- Older application versions could create more than one running row. Preserve
-- the newest row and close older rows before installing the unique index.
with ranked_running as (
  select
    id,
    row_number() over (
      partition by user_id, bot_config_id
      order by started_at desc, id desc
    ) as running_rank
  from bot_runs
  where status = 'running'
)
update bot_runs as run
set
  status = 'stopped',
  stopped_at = coalesce(run.stopped_at, now()),
  stop_reason = coalesce(
    nullif(btrim(run.stop_reason), ''),
    'migration_superseded_duplicate_running_run'
  )
from ranked_running
where run.id = ranked_running.id
  and ranked_running.running_rank > 1;

create unique index if not exists uq_bot_runs_one_running_per_config
  on bot_runs (user_id, bot_config_id)
  where status = 'running';

alter table bot_decisions
  add column if not exists correlation_id text;

alter table bot_decisions
  add column if not exists idempotency_key text;

alter table bot_decisions
  drop constraint if exists bot_decisions_type_check;

alter table bot_decisions
  add constraint bot_decisions_type_check
  check (decision_type in ('signal','risk_reject','order_attempt','lifecycle','duplicate_skip'));

alter table bot_order_attempts
  add column if not exists execution_mode text;

alter table bot_order_attempts
  add column if not exists correlation_id text;

alter table bot_order_attempts
  add column if not exists idempotency_key text;

-- A linked run is the best historical source for effective mode. An unlinked
-- submitted row must have routed live; all ambiguous legacy rows fail safe to
-- dry-run rather than being labeled live based on mutable bot configuration.
update bot_order_attempts as attempt
set execution_mode = case
  when attempt.bot_run_id is not null then coalesce(
    (
      select case when run.dry_run then 'dry_run' else 'live' end
      from bot_runs as run
      where run.id = attempt.bot_run_id
    ),
    'dry_run'
  )
  when attempt.status = 'dry_run' then 'dry_run'
  when attempt.status = 'submitted' then 'live'
  else 'dry_run'
end
where attempt.execution_mode is null
   or attempt.execution_mode not in ('dry_run', 'live');

alter table bot_order_attempts
  alter column execution_mode set default 'dry_run',
  alter column execution_mode set not null;

alter table bot_order_attempts
  drop constraint if exists bot_order_attempts_execution_mode_check;

alter table bot_order_attempts
  add constraint bot_order_attempts_execution_mode_check
  check (execution_mode in ('dry_run','live'));

create unique index if not exists uq_bot_order_attempts_idempotency_key
  on bot_order_attempts (user_id, bot_config_id, idempotency_key)
  where idempotency_key is not null;
