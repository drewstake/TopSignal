alter table if exists expenses
  add column if not exists source_id text;

drop index if exists uq_expenses_dedupe;

create unique index if not exists uq_expenses_dedupe
  on expenses (
    user_id,
    expense_date,
    category,
    coalesce(account_type, ''),
    coalesce(plan_size, ''),
    coalesce(source_id, ''),
    coalesce(account_id, 0),
    amount_cents
  );
