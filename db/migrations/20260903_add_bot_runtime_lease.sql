-- Elect one recurring bot evaluator across API processes and hosts. BotRun is
-- still the durable per-bot authorization; this row only coordinates workers.

alter table accounts
  add column if not exists provider_simulated boolean;

alter table accounts
  add column if not exists provider_classification_observed_at timestamptz;

-- Provider positions are account-netted. Keep the newest explicitly running
-- live bot and close unsafe legacy siblings before enforcing that invariant.
with ranked_live_runs as (
  select
    id,
    row_number() over (
      partition by user_id, account_id
      order by started_at desc, id desc
    ) as running_rank
  from bot_runs
  where status = 'running'
    and not dry_run
)
update bot_runs as run
set
  status = 'blocked',
  stopped_at = coalesce(run.stopped_at, now()),
  stop_reason = coalesce(
    nullif(btrim(run.stop_reason), ''),
    'migration_blocked_duplicate_live_account_run'
  ),
  last_heartbeat_at = now()
from ranked_live_runs
where run.id = ranked_live_runs.id
  and ranked_live_runs.running_rank > 1;

create unique index if not exists uq_bot_runs_one_live_running_per_account
  on bot_runs (user_id, account_id)
  where status = 'running' and not dry_run;

create table if not exists bot_runtime_leases (
  lease_name text primary key,
  owner_id text not null,
  acquired_at timestamptz not null,
  heartbeat_at timestamptz not null,
  expires_at timestamptz not null,
  details jsonb not null default '{}'::jsonb,
  constraint bot_runtime_leases_expiry_after_heartbeat_check
    check (expires_at > heartbeat_at)
);

create index if not exists idx_bot_runtime_leases_expires
  on bot_runtime_leases (expires_at);

-- Browser clients never need direct access to the process-coordination row.
-- RLS is defense in depth if a future default grant accidentally exposes it.
alter table bot_runtime_leases enable row level security;

do $topsignal_bot_runtime_lease_acl$
declare
  api_role text;
begin
  foreach api_role in array array['anon', 'authenticated']
  loop
    if exists (select 1 from pg_roles where rolname = api_role) then
      execute format(
        'revoke all privileges on table public.bot_runtime_leases from %I',
        api_role
      );
    end if;
  end loop;
end
$topsignal_bot_runtime_lease_acl$;
