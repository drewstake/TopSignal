create table if not exists payouts (
  id bigserial primary key,
  user_id uuid not null default '00000000-0000-0000-0000-000000000000',
  payout_date date not null,
  amount_cents integer not null check (amount_cents > 0),
  currency text not null default 'USD',
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_payouts_payout_date
  on payouts (user_id, payout_date desc);
