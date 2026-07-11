-- Keep live execution audit rows after a bot configuration is removed.

alter table bot_order_attempts
  alter column bot_config_id drop not null;

alter table bot_order_attempts
  drop constraint if exists bot_order_attempts_bot_config_id_fkey;

alter table bot_order_attempts
  add constraint bot_order_attempts_bot_config_id_fkey
  foreign key (bot_config_id) references bot_configs(id) on delete set null;
