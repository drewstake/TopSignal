-- Speeds lifecycle context lookups for recent trade-event responses.

create index if not exists idx_projectx_trade_events_user_account_contract_ts_nonvoided
  on projectx_trade_events (user_id, account_id, contract_id, trade_timestamp desc, id desc)
  where lower(coalesce(raw_payload->>'voided', 'false')) <> 'true';
