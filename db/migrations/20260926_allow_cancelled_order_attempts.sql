-- Expand the audit status vocabulary without rewriting or deleting order records.
-- No new rows, indexes, or historical storage. Apply before the updated reconciler.
alter table public.bot_order_attempts
    drop constraint if exists bot_order_attempts_status_check;
alter table public.bot_order_attempts
    add constraint bot_order_attempts_status_check
    check (status in ('pending','dry_run','submitted','submission_unknown','blocked','rejected','error','cancelled'));
