import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module
from app.db import Base
from app.expense_schemas import ExpenseCreateIn, ExpenseUpdateIn
from app.main import (
    create_expense,
    delete_expense,
    get_combine_tracker_expense_suppressions,
    get_expense_totals,
    list_expenses,
    update_expense,
)
from app.models import Expense, ExpenseSuppression


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[Expense.__table__, ExpenseSuppression.__table__],
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(
            bind=engine,
            tables=[ExpenseSuppression.__table__, Expense.__table__],
        )
        engine.dispose()


def _create_standard_50k_evaluation(db_session, *, expense_date: date, amount: float = 51.0):
    return create_expense(
        payload=ExpenseCreateIn(
            expense_date=expense_date,
            amount=amount,
            category="evaluation_fee",
            account_type="standard",
            plan_size="50k",
            account_id=123,
            tags=["topstep"],
        ),
        db=db_session,
    )


def _create_auto_combine_expense(
    db_session,
    *,
    expense_date: date,
    account_id: int,
    amount_cents: int = 4900,
):
    return create_expense(
        payload=ExpenseCreateIn(
            expense_date=expense_date,
            amount_cents=amount_cents,
            category="evaluation_fee",
            account_type="standard",
            plan_size="50k",
            account_id=account_id,
            tags=["combine_tracker", "auto"],
        ),
        db=db_session,
    )


def test_create_expense_success_standard_50k_evaluation(db_session):
    created = _create_standard_50k_evaluation(db_session, expense_date=date(2026, 2, 20))

    assert created.id > 0
    assert created.category == "evaluation_fee"
    assert created.account_type == "standard"
    assert created.plan_size == "50k"
    assert created.amount_cents == 5100
    assert created.amount == 51.00


def test_reject_practice_account_type(db_session):
    with pytest.raises(HTTPException) as exc_info:
        create_expense(
            payload=ExpenseCreateIn(
                expense_date=date(2026, 2, 20),
                amount=221.0,
                category="evaluation_fee",
                account_type="practice",
                plan_size="150k",
            ),
            db=db_session,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "practice_accounts_are_free"


def test_reject_practice_is_practice_true(db_session):
    with pytest.raises(HTTPException) as exc_info:
        create_expense(
            payload=ExpenseCreateIn(
                expense_date=date(2026, 2, 20),
                amount=51.0,
                category="evaluation_fee",
                account_type="standard",
                plan_size="50k",
                is_practice=True,
            ),
            db=db_session,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "practice_accounts_are_free"


def test_reject_practice_description_contains_practice(db_session):
    with pytest.raises(HTTPException) as exc_info:
        create_expense(
            payload=ExpenseCreateIn(
                expense_date=date(2026, 2, 20),
                amount=51.0,
                category="evaluation_fee",
                account_type="standard",
                plan_size="50k",
                description="Practice account should not be billed",
            ),
            db=db_session,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "practice_accounts_are_free"


def test_update_expense_can_clear_nullable_fields(db_session):
    created = create_expense(
        payload=ExpenseCreateIn(
            expense_date=date(2026, 2, 20),
            amount=51.0,
            category="evaluation_fee",
            account_type="standard",
            plan_size="50k",
            account_id=123,
            description="Initial note",
            tags=["topstep", "february"],
        ),
        db=db_session,
    )

    updated = update_expense(
        expense_id=created.id,
        payload=ExpenseUpdateIn(account_id=None, description=None, tags=None),
        db=db_session,
    )

    assert updated.account_id is None
    assert updated.description is None
    assert updated.tags == []


def test_empty_expense_update_payload_is_rejected():
    with pytest.raises(ValidationError):
        ExpenseUpdateIn()


def test_invalid_expense_update_leaves_row_unchanged(db_session):
    created = _create_standard_50k_evaluation(db_session, expense_date=date(2026, 2, 20))

    with pytest.raises(HTTPException) as exc_info:
        update_expense(
            expense_id=created.id,
            payload=ExpenseUpdateIn(amount_cents=-1),
            db=db_session,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "amount_cents must be >= 0"
    row = db_session.query(Expense).filter(Expense.id == created.id).one()
    assert row.amount_cents == 5100


def test_expense_routes_are_scoped_to_authenticated_user(db_session, monkeypatch):
    created = _create_standard_50k_evaluation(db_session, expense_date=date(2026, 2, 20))
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: "other-user")

    listed = list_expenses(limit=200, offset=0, db=db_session)
    totals = get_expense_totals(range="all_time", db=db_session)

    assert listed == {"items": [], "total": 0}
    assert totals["total_amount_cents"] == 0
    assert totals["count"] == 0

    with pytest.raises(HTTPException) as exc_info:
        delete_expense(expense_id=created.id, db=db_session)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "expense not found"


def test_delete_auto_combine_expense_persists_suppression(db_session):
    created = _create_auto_combine_expense(
        db_session,
        expense_date=date(2026, 7, 29),
        account_id=901,
    )

    response = delete_expense(
        expense_id=created.id,
        suppress_auto_recreation=True,
        db=db_session,
    )

    assert response.status_code == 204
    assert db_session.query(Expense).filter(Expense.id == created.id).first() is None
    assert get_combine_tracker_expense_suppressions(db=db_session) == {
        "account_ids": [901],
    }


def test_combine_suppressions_are_tenant_scoped(db_session, monkeypatch):
    user_a = "10000000-0000-0000-0000-000000000001"
    user_b = "20000000-0000-0000-0000-000000000002"
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: user_a)
    expense_a = _create_auto_combine_expense(
        db_session,
        expense_date=date(2026, 7, 28),
        account_id=111,
    )
    delete_expense(
        expense_id=expense_a.id,
        suppress_auto_recreation=True,
        db=db_session,
    )

    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: user_b)
    assert get_combine_tracker_expense_suppressions(db=db_session) == {
        "account_ids": [],
    }
    expense_b = _create_auto_combine_expense(
        db_session,
        expense_date=date(2026, 7, 29),
        account_id=222,
    )
    delete_expense(
        expense_id=expense_b.id,
        suppress_auto_recreation=True,
        db=db_session,
    )
    assert get_combine_tracker_expense_suppressions(db=db_session) == {
        "account_ids": [222],
    }

    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: user_a)
    assert get_combine_tracker_expense_suppressions(db=db_session) == {
        "account_ids": [111],
    }


def test_delete_manual_expense_does_not_create_suppression(db_session):
    created = _create_standard_50k_evaluation(
        db_session,
        expense_date=date(2026, 7, 29),
    )

    delete_expense(expense_id=created.id, db=db_session)

    assert get_combine_tracker_expense_suppressions(db=db_session) == {
        "account_ids": [],
    }


def test_duplicate_cleanup_can_skip_auto_recreation_suppression(db_session):
    created = _create_auto_combine_expense(
        db_session,
        expense_date=date(2026, 7, 29),
        account_id=902,
    )

    delete_expense(
        expense_id=created.id,
        suppress_auto_recreation=False,
        db=db_session,
    )

    assert get_combine_tracker_expense_suppressions(db=db_session) == {
        "account_ids": [],
    }


def test_legacy_delete_call_defaults_to_no_suppression(db_session):
    created = _create_auto_combine_expense(
        db_session,
        expense_date=date(2026, 7, 29),
        account_id=905,
    )

    delete_expense(expense_id=created.id, db=db_session)

    assert get_combine_tracker_expense_suppressions(db=db_session) == {
        "account_ids": [],
    }


def test_suppression_upsert_is_idempotent_for_duplicate_auto_expenses(db_session):
    older = _create_auto_combine_expense(
        db_session,
        expense_date=date(2026, 7, 28),
        account_id=903,
    )
    newer = _create_auto_combine_expense(
        db_session,
        expense_date=date(2026, 7, 29),
        account_id=903,
    )

    delete_expense(
        expense_id=older.id,
        suppress_auto_recreation=True,
        db=db_session,
    )
    delete_expense(
        expense_id=newer.id,
        suppress_auto_recreation=True,
        db=db_session,
    )

    suppressions = (
        db_session.query(ExpenseSuppression)
        .filter(ExpenseSuppression.account_id == 903)
        .all()
    )
    assert len(suppressions) == 1
    assert get_combine_tracker_expense_suppressions(db=db_session) == {
        "account_ids": [903],
    }


def test_suppression_blocks_auto_recreation_for_owner_only(db_session, monkeypatch):
    user_a = "30000000-0000-0000-0000-000000000003"
    user_b = "40000000-0000-0000-0000-000000000004"
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: user_a)
    created = _create_auto_combine_expense(
        db_session,
        expense_date=date(2026, 7, 28),
        account_id=906,
    )
    delete_expense(
        expense_id=created.id,
        suppress_auto_recreation=True,
        db=db_session,
    )

    with pytest.raises(HTTPException) as exc_info:
        _create_auto_combine_expense(
            db_session,
            expense_date=date(2026, 7, 29),
            account_id=906,
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "expense_sync_suppressed"

    manual_expense = create_expense(
        payload=ExpenseCreateIn(
            expense_date=date(2026, 7, 29),
            amount_cents=5100,
            category="evaluation_fee",
            account_type="standard",
            plan_size="50k",
            account_id=906,
            tags=["manual"],
        ),
        db=db_session,
    )
    assert manual_expense.account_id == 906

    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: user_b)
    other_tenant_expense = _create_auto_combine_expense(
        db_session,
        expense_date=date(2026, 7, 29),
        account_id=906,
    )
    assert other_tenant_expense.account_id == 906


def test_delete_and_suppression_are_committed_atomically(db_session):
    created = _create_auto_combine_expense(
        db_session,
        expense_date=date(2026, 7, 29),
        account_id=904,
    )

    def fail_commit(_session):
        raise RuntimeError("simulated commit failure")

    event.listen(db_session, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="simulated commit failure"):
            delete_expense(
                expense_id=created.id,
                suppress_auto_recreation=True,
                db=db_session,
            )
    finally:
        event.remove(db_session, "before_commit", fail_commit)
        db_session.rollback()

    assert db_session.query(Expense).filter(Expense.id == created.id).one()
    assert (
        db_session.query(ExpenseSuppression)
        .filter(ExpenseSuppression.account_id == 904)
        .first()
        is None
    )


def test_list_expenses_rejects_invalid_pagination(db_session):
    with pytest.raises(HTTPException) as exc_info:
        list_expenses(limit=0, offset=0, db=db_session)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "limit must be between 1 and 500"


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        base = datetime(2026, 2, 27, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        if tz is None:
            return base.replace(tzinfo=None)
        return base.astimezone(tz)


def test_totals_week_month_ytd_all_time_with_fixed_dates(db_session, monkeypatch):
    monkeypatch.setattr(main_module, "datetime", _FrozenDatetime)

    create_expense(
        payload=ExpenseCreateIn(
            expense_date=date(2026, 2, 26),
            amount_cents=5100,
            category="evaluation_fee",
            account_type="standard",
            plan_size="50k",
            account_id=123,
        ),
        db=db_session,
    )
    create_expense(
        payload=ExpenseCreateIn(
            expense_date=date(2026, 2, 24),
            amount_cents=15000,
            category="activation_fee",
            account_type="standard",
            plan_size="50k",
            account_id=123,
        ),
        db=db_session,
    )
    create_expense(
        payload=ExpenseCreateIn(
            expense_date=date(2026, 2, 10),
            amount_cents=2000,
            category="data_fee",
            account_type="no_activation",
            plan_size="100k",
            account_id=123,
        ),
        db=db_session,
    )
    create_expense(
        payload=ExpenseCreateIn(
            expense_date=date(2026, 1, 15),
            amount_cents=3000,
            category="reset_fee",
            account_type="standard",
            plan_size="100k",
            account_id=123,
        ),
        db=db_session,
    )
    create_expense(
        payload=ExpenseCreateIn(
            expense_date=date(2025, 12, 31),
            amount_cents=1200,
            category="other",
            account_type="standard",
            plan_size="50k",
            account_id=123,
        ),
        db=db_session,
    )

    week = get_expense_totals(range="week", db=db_session)
    month = get_expense_totals(range="month", db=db_session)
    ytd = get_expense_totals(range="ytd", db=db_session)
    all_time = get_expense_totals(range="all_time", db=db_session)

    assert week["start_date"] == date(2026, 2, 23)
    assert week["end_date"] == date(2026, 2, 27)
    assert week["total_amount_cents"] == 20100
    assert week["count"] == 2

    assert month["start_date"] == date(2026, 2, 1)
    assert month["total_amount_cents"] == 22100
    assert month["count"] == 3

    assert ytd["start_date"] == date(2026, 1, 1)
    assert ytd["total_amount_cents"] == 25100
    assert ytd["count"] == 4

    assert all_time["start_date"] is None
    assert all_time["end_date"] == date(2026, 2, 27)
    assert all_time["total_amount_cents"] == 26300
    assert all_time["count"] == 5


def test_totals_can_apply_custom_start_date(db_session, monkeypatch):
    monkeypatch.setattr(main_module, "datetime", _FrozenDatetime)

    create_expense(
        payload=ExpenseCreateIn(
            expense_date=date(2026, 2, 24),
            amount_cents=5100,
            category="evaluation_fee",
            account_type="standard",
            plan_size="50k",
            account_id=123,
        ),
        db=db_session,
    )
    create_expense(
        payload=ExpenseCreateIn(
            expense_date=date(2026, 2, 27),
            amount_cents=16800,
            category="evaluation_fee",
            account_type="standard",
            plan_size="100k",
            account_id=123,
        ),
        db=db_session,
    )

    totals = get_expense_totals(
        range="all_time",
        start_date=date(2026, 2, 27),
        db=db_session,
    )

    assert totals["start_date"] == date(2026, 2, 27)
    assert totals["end_date"] == date(2026, 2, 27)
    assert totals["total_amount_cents"] == 16800
    assert totals["count"] == 1


def test_totals_can_apply_custom_created_at_start(db_session, monkeypatch):
    monkeypatch.setattr(main_module, "datetime", _FrozenDatetime)

    create_expense(
        payload=ExpenseCreateIn(
            expense_date=date(2026, 2, 27),
            amount_cents=5100,
            category="evaluation_fee",
            account_type="standard",
            plan_size="50k",
            account_id=123,
        ),
        db=db_session,
    )
    create_expense(
        payload=ExpenseCreateIn(
            expense_date=date(2026, 2, 27),
            amount_cents=16800,
            category="evaluation_fee",
            account_type="standard",
            plan_size="100k",
            account_id=123,
        ),
        db=db_session,
    )

    rows = db_session.query(Expense).order_by(Expense.id.asc()).all()
    rows[0].created_at = datetime(2026, 2, 27, 14, 0, tzinfo=timezone.utc)
    rows[1].created_at = datetime(2026, 2, 27, 14, 30, tzinfo=timezone.utc)
    db_session.commit()

    totals = get_expense_totals(
        range="all_time",
        start_created_at=datetime(2026, 2, 27, 14, 15, tzinfo=timezone.utc),
        db=db_session,
    )

    assert totals["total_amount_cents"] == 16800
    assert totals["count"] == 1


def test_duplicate_insert_returns_conflict(db_session):
    payload = ExpenseCreateIn(
        expense_date=date(2026, 2, 26),
        amount_cents=5100,
        category="evaluation_fee",
        account_type="standard",
        plan_size="50k",
        account_id=123,
    )

    first = create_expense(payload=payload, db=db_session)
    assert first.id > 0

    with pytest.raises(HTTPException) as exc_info:
        create_expense(payload=payload, db=db_session)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "duplicate_expense"
