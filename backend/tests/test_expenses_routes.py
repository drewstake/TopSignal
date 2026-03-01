import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module
from app.db import Base
from app.expense_schemas import ExpenseCreateIn
from app.main import create_expense, get_expense_totals
from app.models import Expense


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[Expense.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=[Expense.__table__])
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
