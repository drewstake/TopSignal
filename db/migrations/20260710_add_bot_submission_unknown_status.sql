-- Preserve ambiguous live submissions until they can be reconciled with the provider.

alter table bot_order_attempts
  drop constraint if exists bot_order_attempts_status_check;

alter table bot_order_attempts
  add constraint bot_order_attempts_status_check
  check (status in ('pending','dry_run','submitted','submission_unknown','blocked','rejected','error'));
