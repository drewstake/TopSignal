-- Adds rule-break tracking columns used by dashboard behavior metrics.
alter table trades
  add column if not exists is_rule_break boolean not null default false;

alter table trades
  add column if not exists rule_break_type text;

create index if not exists idx_trades_is_rule_break
  on trades (is_rule_break);
