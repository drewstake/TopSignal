with ranked_main_accounts as (
  select
    id,
    row_number() over (
      partition by user_id, provider
      order by created_at desc nulls last, id desc
    ) as main_rank
  from accounts
  where is_main
)
update accounts
set is_main = false
where id in (
  select id
  from ranked_main_accounts
  where main_rank > 1
);

create unique index if not exists uq_accounts_one_main_per_user_provider
  on accounts (user_id, provider)
  where is_main;
