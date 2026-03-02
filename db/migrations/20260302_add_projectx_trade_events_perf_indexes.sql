-- Speeds dashboard/account query patterns on projectx_trade_events.

create index if not exists idx_projectx_trade_events_user_account_ts
  on projectx_trade_events (user_id, account_id, trade_timestamp desc);

create index if not exists idx_projectx_trade_events_user_account_closed_ts_nonvoided
  on projectx_trade_events (user_id, account_id, trade_timestamp desc, id desc)
  where pnl is not null
    and lower(coalesce(raw_payload->>'voided', 'false')) <> 'true';
