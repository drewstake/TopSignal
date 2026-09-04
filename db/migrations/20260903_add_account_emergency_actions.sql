-- Persist account-level emergency flatten attempts independently of bot
-- configuration lifecycle. This makes the kill switch available even when an
-- account has no bot configuration and preserves an audit after bot deletion.

create table if not exists account_emergency_actions (
  id bigserial primary key,
  user_id uuid not null default '00000000-0000-0000-0000-000000000000',
  account_id bigint not null,
  provider text not null default 'projectx',
  status text not null default 'pending'
    check (status in ('pending','confirmed_account_flat','unconfirmed')),
  confirmed_flat boolean not null default false,
  reason text not null default 'manual_emergency_flatten',
  risk_code text,
  risk_message text,
  risk_severity text
    check (risk_severity is null or risk_severity in ('info','warning','critical')),
  lease_owner_id text,
  lease_expires_at timestamptz,
  attempt_count integer not null default 1,
  request_payload jsonb not null default '{}'::jsonb,
  result_payload jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz,
  constraint account_emergency_actions_confirmation_consistency_check check (
    (status = 'confirmed_account_flat' and confirmed_flat)
    or (status <> 'confirmed_account_flat' and not confirmed_flat)
  ),
  constraint account_emergency_actions_completion_consistency_check check (
    (status = 'pending' and completed_at is null)
    or (status <> 'pending' and completed_at is not null)
  ),
  constraint account_emergency_actions_pending_lease_check check (
    status <> 'pending'
    or (lease_owner_id is not null and lease_expires_at is not null)
  ),
  constraint account_emergency_actions_attempt_count_check check (
    attempt_count >= 1
  )
);

create index if not exists idx_account_emergency_actions_user_account_created
  on account_emergency_actions (user_id, account_id, created_at desc);

create unique index if not exists uq_account_emergency_actions_one_pending
  on account_emergency_actions (user_id, account_id)
  where status = 'pending';

alter table account_emergency_actions enable row level security;

do $topsignal_account_emergency_actions_acl$
declare
  api_role text;
begin
  foreach api_role in array array['anon', 'authenticated']
  loop
    if exists (select 1 from pg_roles where rolname = api_role) then
      execute format(
        'revoke all privileges on table public.account_emergency_actions from %I',
        api_role
      );
      execute format(
        'revoke all privileges on sequence public.account_emergency_actions_id_seq from %I',
        api_role
      );
    end if;
  end loop;
end
$topsignal_account_emergency_actions_acl$;
