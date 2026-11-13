-- Provider executions may contain several distinct leg IDs at the same order
-- and timestamp. No historical rows are removed or rewritten by this migration.
alter table projectx_trade_events drop constraint if exists uq_projectx_trade_events_account_order_ts;
alter table projectx_trade_events drop constraint if exists projectx_trade_events_user_id_account_id_order_id_trade_timesta;
drop index if exists uq_projectx_trade_events_account_order_ts;
create unique index uq_projectx_trade_events_account_order_ts
  on projectx_trade_events (user_id, account_id, order_id, trade_timestamp)
  where source_trade_id is null;
