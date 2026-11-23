"""Add missing inferred combine fees after authoritative account discovery."""

from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..models import Account, Expense, ExpenseSuppression
from .topstep_labs import is_labs_account_name, one_time_fee_from_account_name

# Keep aligned with the application's combineTracker/expensePresets defaults.
# These are inferred fees, not provider billing transactions.
_STANDARD_CENTS = {"50k": 4900, "100k": 9900, "150k": 14900}
_DLL_CENTS = {"50k": 8500, "100k": 12900, "150k": 19900}
_NEW_YORK = ZoneInfo("America/New_York")


def add_missing_combine_expenses(
    db: Session, accounts: list[Account], *, user_id: str, observed_at: datetime,
) -> int:
    """Caller holds the account mutation lock through commit; never delete rows."""
    eligible = {}
    for account in accounts:
        if (
            account.user_id != user_id
            or account.trade_data_source != "projectx" or account.archived_at is not None
            or account.account_state not in {"ACTIVE", "LOCKED_OUT"}
        ):
            continue
        name = (account.name or "").strip().upper()
        first_seen = account.first_seen_at or observed_at
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=timezone.utc)
        purchase_date = first_seen.astimezone(_NEW_YORK).date()
        match = re.match(r"^(50|100|150)KTC", name)
        fee = one_time_fee_from_account_name(name, purchase_date)
        if fee is None and (not match or is_labs_account_name(name)):
            continue
        eligible[int(account.external_id)] = (purchase_date, match[1] + "k" if match else None, name, fee)
    if not eligible:
        return 0

    # Existing paid amounts (including discounted/manual entries) are authoritative.
    recorded = {
        row[0] for row in db.query(Expense.account_id).filter(
            Expense.user_id == user_id, Expense.account_id.in_(eligible),
            Expense.category == "evaluation_fee",
        ).all()
    }
    suppressed = {
        row[0] for row in db.query(ExpenseSuppression.account_id).filter(
            ExpenseSuppression.user_id == user_id,
            ExpenseSuppression.source == "combine_tracker",
            ExpenseSuppression.account_id.in_(eligible),
        ).all()
    }
    created = 0
    for account_id, (purchase_date, plan_size, name, fee) in eligible.items():
        if account_id in recorded or account_id in suppressed:
            continue
        dll = bool(re.search(r"(?:^|[^A-Z0-9])DLL(?:$|[^A-Z0-9])", name)) or "DAILY LOSS LIMIT" in name
        db.add(Expense(
            user_id=user_id, account_id=account_id, provider="topstep",
            expense_date=purchase_date,
            amount_cents=fee.fee_cents if fee else (_DLL_CENTS if dll else _STANDARD_CENTS)[plan_size],
            currency="USD", category="evaluation_fee",
            account_type=None if fee else "no_activation" if dll else "standard",
            plan_size=None if fee else plan_size,
            source_id=f"{fee.source}:account:{account_id}" if fee else None,
            description=(
                f"Auto tracked {fee.label}; one-time fee (inferred)"
                if fee else f"Auto tracked combine purchase ({plan_size.upper()}{' DLL' if dll else ''})"
            ),
            tags=["combine_tracker", "auto", *(
                [fee.source, fee.product_tag, "one_time_fee", "quantity_1"]
                if fee else ["dll"] if dll else []
            )],
        ))
        created += 1
    return created
