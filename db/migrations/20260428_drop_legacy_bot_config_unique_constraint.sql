alter table bot_configs
  drop constraint if exists uq_bot_configs_user_account;

drop index if exists uq_bot_configs_user_account;

create index if not exists idx_bot_configs_user_account
  on bot_configs (user_id, account_id);
