import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module
from app.db import Base
from app.expense_schemas import FinancialSummaryOut
from app.main import get_financial_summary
from app.models import Expense, Payout


CURRENT_USER = "00000000-0000-0000-0000-000000000000"
OTHER_USER = "11111111-1111-1111-1111-111111111111"


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[Expense.__table__, Payout.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=[Payout.__table__, Expense.__table__])
        engine.dispose()


def _expense(
    db_session,
    *,
    expense_date: date,
    amount_cents: int,
    category: str = "evaluation_fee",
    account_id: int = 123,
    user_id: str = CURRENT_USER,
):
    db_session.add(
        Expense(
            user_id=user_id,
            account_id=account_id,
            provider="topstep",
            expense_date=expense_date,
            amount_cents=amount_cents,
            currency="USD",
            category=category,
            account_type="standard",
            plan_size="50k",
            tags=[],
        )
    )


def _payout(
    db_session,
    *,
    payout_date: date,
    amount_cents: int,
    user_id: str = CURRENT_USER,
):
    db_session.add(
        Payout(
            user_id=user_id,
            payout_date=payout_date,
            amount_cents=amount_cents,
            currency="USD",
        )
    )


def test_financial_summary_preserves_ranges_totals_and_account_filter(db_session, monkeypatch):
    _expense(db_session, expense_date=date(2025, 3, 31), amount_cents=10_000)
    _expense(db_session, expense_date=date(2026, 7, 1), amount_cents=5_000, category="data_fee")
    _expense(db_session, expense_date=date(2026, 7, 20), amount_cents=2_500, category="other", account_id=999)
    _expense(
        db_session,
        expense_date=date(2026, 7, 20),
        amount_cents=99_999,
        account_id=123,
        user_id=OTHER_USER,
    )
    _payout(db_session, payout_date=date(2025, 4, 15), amount_cents=20_000)
    _payout(db_session, payout_date=date(2026, 7, 10), amount_cents=50_000)
    _payout(db_session, payout_date=date(2026, 7, 11), amount_cents=99_999, user_id=OTHER_USER)
    db_session.commit()
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: CURRENT_USER)

    summary = get_financial_summary(as_of_date=date(2026, 7, 25), db=db_session)
    validated = FinancialSummaryOut.model_validate(summary)

    assert validated.first_cash_flow_date == date(2025, 3, 31)
    assert validated.expense_totals.total_amount_cents == 17_500
    assert validated.expense_totals.by_category["evaluation_fee"].amount_cents == 10_000
    assert validated.payout_totals.total_amount_cents == 70_000
    assert validated.spend_since_last_payout.last_payout_date == date(2026, 7, 10)
    assert validated.spend_since_last_payout.total_amount_cents == 2_500
    assert validated.spend_since_last_payout.expense_count == 1

    ranges = {item.key: item for item in validated.ranges}
    assert list(item.key for item in validated.ranges) == [
        "one_month",
        "three_months",
        "six_months",
        "year_to_date",
        "one_year",
        "anniversary_year_1",
        "anniversary_year_2",
        "all_time",
    ]
    assert ranges["one_month"].start_date == date(2026, 6, 25)
    assert ranges["one_month"].expense_totals.total_amount_cents == 7_500
    assert ranges["anniversary_year_1"].end_date == date(2026, 3, 30)
    assert ranges["anniversary_year_1"].expense_totals.total_amount_cents == 10_000
    assert ranges["anniversary_year_2"].end_date == date(2026, 7, 25)
    assert ranges["anniversary_year_2"].payout_totals.total_amount_cents == 50_000
    assert ranges["all_time"].start_date is None
    assert ranges["all_time"].end_date is None

    account_summary = FinancialSummaryOut.model_validate(
        get_financial_summary(as_of_date=date(2026, 7, 25), account_id=123, db=db_session)
    )
    assert account_summary.expense_totals.total_amount_cents == 15_000
    assert account_summary.spend_since_last_payout.total_amount_cents == 0
    assert account_summary.payout_totals.total_amount_cents == 70_000


def test_financial_summary_uses_two_bounded_aggregate_queries(db_session, monkeypatch):
    _expense(db_session, expense_date=date(2026, 7, 1), amount_cents=5_000)
    _payout(db_session, payout_date=date(2026, 7, 10), amount_cents=50_000)
    db_session.commit()
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: CURRENT_USER)
    statements: list[str] = []

    def record_statement(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(db_session.get_bind(), "before_cursor_execute", record_statement)
    try:
        get_financial_summary(as_of_date=date(2026, 7, 25), db=db_session)
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", record_statement)

    assert len(statements) == 2
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)


def test_financial_summary_is_user_scoped_and_preserves_future_all_time_payout_behavior(db_session, monkeypatch):
    _expense(db_session, expense_date=date(2026, 8, 1), amount_cents=4_000)
    _payout(db_session, payout_date=date(2026, 8, 2), amount_cents=9_000)
    db_session.commit()
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: CURRENT_USER)

    summary = FinancialSummaryOut.model_validate(
        get_financial_summary(as_of_date=date(2026, 7, 25), db=db_session)
    )
    ranges = {item.key: item for item in summary.ranges}

    assert summary.expense_totals.total_amount_cents == 0
    assert summary.payout_totals.total_amount_cents == 9_000
    assert summary.spend_since_last_payout.last_payout_date == date(2026, 8, 2)
    assert summary.spend_since_last_payout.total_amount_cents == 0
    assert ranges["one_month"].payout_totals.total_amount_cents == 0
    assert ranges["all_time"].payout_totals.total_amount_cents == 9_000

    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: OTHER_USER)
    other_summary = FinancialSummaryOut.model_validate(
        get_financial_summary(as_of_date=date(2026, 7, 25), db=db_session)
    )
    assert other_summary.first_cash_flow_date is None
    assert other_summary.expense_totals.count == 0
    assert other_summary.payout_totals.count == 0
    assert len(other_summary.ranges) == 6


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        utc_value = datetime(2026, 7, 26, 2, 0, tzinfo=ZoneInfo("UTC"))
        if tz is None:
            return utc_value.replace(tzinfo=None)
        return utc_value.astimezone(tz)


def test_financial_summary_defaults_to_new_york_calendar_date(db_session, monkeypatch):
    monkeypatch.setattr(main_module, "datetime", _FrozenDatetime)
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: CURRENT_USER)

    summary = FinancialSummaryOut.model_validate(get_financial_summary(db=db_session))

    assert summary.as_of_date == date(2026, 7, 25)
    assert summary.expense_totals.end_date == date(2026, 7, 25)


def test_financial_summary_clamps_leap_day_anniversary_ranges(db_session, monkeypatch):
    _expense(db_session, expense_date=date(2024, 2, 29), amount_cents=1_000)
    db_session.commit()
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: CURRENT_USER)

    summary = FinancialSummaryOut.model_validate(
        get_financial_summary(as_of_date=date(2026, 3, 1), db=db_session)
    )
    anniversary_ranges = [
        item
        for item in summary.ranges
        if item.key.startswith("anniversary_year_")
    ]

    assert [
        (item.key, item.start_date, item.end_date)
        for item in anniversary_ranges
    ] == [
        ("anniversary_year_1", date(2024, 2, 29), date(2025, 2, 27)),
        ("anniversary_year_2", date(2025, 2, 28), date(2026, 2, 27)),
        ("anniversary_year_3", date(2026, 2, 28), date(2026, 3, 1)),
    ]
