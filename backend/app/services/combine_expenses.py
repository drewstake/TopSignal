"""Add missing inferred combine fees after authoritative account discovery."""

from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..models import Account, Expense, ExpenseSuppression

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
        name = (account.name or "").strip().upper()
        match = re.match(r"^(50|100|150)KTC", name)
        if (
            not match or account.user_id != user_id
            or account.trade_data_source != "projectx" or account.archived_at is not None
            or account.account_state not in {"ACTIVE", "LOCKED_OUT"}
        ):
            continue
        eligible[int(account.external_id)] = (account, match[1] + "k", name)
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
    for account_id, (account, plan_size, name) in eligible.items():
        if account_id in recorded or account_id in suppressed:
            continue
        dll = bool(re.search(r"(?:^|[^A-Z0-9])DLL(?:$|[^A-Z0-9])", name)) or "DAILY LOSS LIMIT" in name
        first_seen = account.first_seen_at or observed_at
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=timezone.utc)
        db.add(Expense(
            user_id=user_id, account_id=account_id, provider="topstep",
            expense_date=first_seen.astimezone(_NEW_YORK).date(),
            amount_cents=(_DLL_CENTS if dll else _STANDARD_CENTS)[plan_size],
            currency="USD", category="evaluation_fee",
            account_type="no_activation" if dll else "standard", plan_size=plan_size,
            description=f"Auto tracked combine purchase ({plan_size.upper()}{' DLL' if dll else ''})",
            tags=["combine_tracker", "auto", *(["dll"] if dll else [])],
        ))
        created += 1
    return created
