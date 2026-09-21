from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Account, Expense, ExpenseSuppression
from app.services.combine_expenses import add_missing_combine_expenses
from app.services.projectx_accounts import serialize_account_main_mutation, sync_projectx_accounts

USER = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"
NOW = datetime(2026, 9, 21, 2, tzinfo=timezone.utc)


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Account.__table__, Expense.__table__, ExpenseSuppression.__table__])
    with Session(engine) as session:
        yield session
    engine.dispose()


def discover(db, names, user=USER, now=NOW):
    with serialize_account_main_mutation(db, user_id=user):
        rows = sync_projectx_accounts(db, [
            {"id": account_id, "name": name, "can_trade": True, "is_visible": True}
            for account_id, name in names.items()
        ], user_id=user, now_utc=now)
        db.flush()
        count = add_missing_combine_expenses(db, rows, user_id=user, observed_at=now)
        db.commit()
    return count


def test_discovery_adds_fees_once_and_uses_first_seen_eastern_date(db):
    names = {1: "50KTC-SKU-V2-DLL-123", 2: "100KTC-123", 3: "150KTC-123"}
    assert discover(db, names) == 3
    assert discover(db, names, now=datetime(2026, 9, 22, 12, tzinfo=timezone.utc)) == 0
    rows = db.query(Expense).order_by(Expense.account_id).all()
    assert [row.amount_cents for row in rows] == [8500, 9900, 14900]
    assert all(row.expense_date == date(2026, 9, 20) for row in rows)
    assert rows[0].tags == ["combine_tracker", "auto", "dll"]
    assert rows[0].account_type == "no_activation"


def test_preserves_existing_manual_and_duplicate_fees_and_suppressions(db):
    for amount in [3900, 4900]:
        db.add(Expense(user_id=USER, account_id=1, expense_date=date(2026, 9, 1),
                       category="evaluation_fee", amount_cents=amount, tags=[]))
    db.add(ExpenseSuppression(user_id=USER, source="combine_tracker", account_id=2))
    db.commit()
    assert discover(db, {1: "50KTC-1", 2: "50KTC-2", 3: "50KTC-3"}) == 1
    assert db.query(Expense).count() == 3
    assert sorted(row.amount_cents for row in db.query(Expense).filter_by(account_id=1)) == [3900, 4900]


def test_user_expenses_and_suppressions_are_isolated(db):
    db.add(ExpenseSuppression(user_id=OTHER, source="combine_tracker", account_id=1))
    db.commit()
    assert discover(db, {1: "50KTC-1"}) == 1
    assert discover(db, {1: "50KTC-1"}, user=OTHER) == 0
    assert discover(db, {2: "50KTC-2"}, user=OTHER) == 1
    assert discover(db, {2: "50KTC-2"}) == 1


def test_ignores_practice_express_hidden_archived_and_csv_accounts(db):
    assert discover(db, {1: "PRACTICE", 2: "EXPRESS-50K"}) == 0
    for account_id, attrs in [
        (3, {"trade_data_source": "csv_import"}),
        (4, {"archived_at": NOW}),
        (5, {"account_state": "HIDDEN"}),
        (6, {"account_state": "MISSING"}),
    ]:
        account = Account(user_id=USER, external_id=str(account_id), provider="projectx",
                          name="50KTC-123", trade_data_source="projectx", account_state="ACTIVE")
        for key, value in attrs.items():
            setattr(account, key, value)
        db.add(account)
    db.flush()
    assert add_missing_combine_expenses(db, db.query(Account).all(), user_id=USER, observed_at=NOW) == 0
    assert db.query(Expense).count() == 0


def test_uses_provider_name_and_backfills_missing_fee_on_next_discovery(db):
    rows = sync_projectx_accounts(db, [{"id": 1, "name": "150KTC-DLL-123", "can_trade": False, "is_visible": True}], user_id=USER, now_utc=NOW)
    rows[0].display_name = "My favorite"
    db.commit()
    assert add_missing_combine_expenses(db, rows, user_id=USER, observed_at=NOW) == 1
    db.commit()
    assert db.query(Expense).one().amount_cents == 19900
