create table if not exists expenses (
  id bigserial primary key,
  account_id bigint,
  provider text not null default 'topstep',
  expense_date date not null,
  amount_cents integer not null check (amount_cents >= 0),
  currency text not null default 'USD',
  category text not null check (category in ('evaluation_fee', 'activation_fee', 'reset_fee', 'data_fee', 'other')),
  account_type text check (account_type in ('no_activation', 'standard', 'practice')),
  plan_size text check (plan_size in ('50k', '100k', '150k')),
  description text,
  tags text[] not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_expenses_expense_date
  on expenses (expense_date desc);

create index if not exists idx_expenses_account_id
  on expenses (account_id);

create index if not exists idx_expenses_category
  on expenses (category);

create unique index if not exists uq_expenses_dedupe
  on expenses (
    expense_date,
    category,
    coalesce(account_type, ''),
    coalesce(plan_size, ''),
    coalesce(account_id, 0),
    amount_cents
  );
