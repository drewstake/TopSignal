-- Retain the last provider balance so cached account analytics remain
-- reachable when ProjectX is temporarily unavailable.

alter table accounts
  add column if not exists balance numeric(18,6);
