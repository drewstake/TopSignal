-- Refunds reduce recorded spend and remain separate from trading payouts.
-- Preserve every existing expense and its source identity.
alter table expenses drop constraint if exists expenses_amount_cents_check;
alter table expenses drop constraint if exists expenses_amount_cents_nonnegative_check;
alter table expenses drop constraint if exists expenses_category_check;

alter table expenses add constraint expenses_category_check
  check (category in ('evaluation_fee', 'activation_fee', 'reset_fee', 'data_fee', 'other', 'refund'));
alter table expenses add constraint expenses_amount_cents_sign_check
  check (
    (category = 'refund' and amount_cents < 0)
    or (category <> 'refund' and amount_cents >= 0)
  );
