-- Tighten multi-tenant query paths used by legacy auth-gated routes.

alter table if exists payouts
  add column if not exists user_id uuid not null default '00000000-0000-0000-0000-000000000000';

create index if not exists idx_trades_user_account_opened_at
  on trades (user_id, account_id, opened_at desc);

create index if not exists idx_trades_user_closed_at
  on trades (user_id, closed_at desc)
  where closed_at is not null;

create index if not exists idx_trades_user_symbol_opened_at
  on trades (user_id, symbol, opened_at desc);

create index if not exists idx_trades_user_rule_break
  on trades (user_id, is_rule_break);

create index if not exists idx_projectx_trade_events_user_account_ts
  on projectx_trade_events (user_id, account_id, trade_timestamp desc);

create index if not exists idx_projectx_trade_events_user_account_ts_id
  on projectx_trade_events (user_id, account_id, trade_timestamp desc, id desc);

create index if not exists idx_projectx_trade_events_user_account_closed_ts_nonvoided
  on projectx_trade_events (user_id, account_id, trade_timestamp desc, id desc)
  where pnl is not null
    and lower(coalesce(raw_payload->>'voided', 'false')) <> 'true';

create index if not exists idx_projectx_trade_events_user_account_contract_ts_nonvoided
  on projectx_trade_events (user_id, account_id, contract_id, trade_timestamp desc, id desc)
  where lower(coalesce(raw_payload->>'voided', 'false')) <> 'true';

create index if not exists idx_expenses_user_date_id
  on expenses (user_id, expense_date, id);

create index if not exists idx_payouts_user_date_id
  on payouts (user_id, payout_date, id);
