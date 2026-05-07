import os
from datetime import date

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module
from app.db import Base
from app.main import create_payout, delete_payout, get_payout_totals, list_payouts, update_payout
from app.models import Payout
from app.payout_schemas import PayoutCreateIn, PayoutUpdateIn


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[Payout.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=[Payout.__table__])
        engine.dispose()


def _create_payout(db_session, *, payout_date: date, amount: float, notes: str | None = None):
    return create_payout(
        payload=PayoutCreateIn(
            payout_date=payout_date,
            amount=amount,
            notes=notes,
        ),
        db=db_session,
    )


def test_create_payout_success(db_session):
    created = _create_payout(db_session, payout_date=date(2026, 3, 1), amount=1250.75, notes="First payout")

    assert created.id > 0
    assert created.payout_date == date(2026, 3, 1)
    assert created.amount_cents == 125075
    assert created.amount == 1250.75
    assert created.notes == "First payout"


def test_reject_zero_amount_payout(db_session):
    with pytest.raises(HTTPException) as exc_info:
        create_payout(
            payload=PayoutCreateIn(
                payout_date=date(2026, 3, 1),
                amount=0,
            ),
            db=db_session,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "amount_cents must be > 0"


def test_list_payouts_newest_first(db_session):
    older = _create_payout(db_session, payout_date=date(2026, 2, 28), amount=900.0)
    newer = _create_payout(db_session, payout_date=date(2026, 3, 2), amount=1100.0, notes="Newest")

    listed = list_payouts(limit=200, offset=0, db=db_session)

    assert listed["total"] == 2
    assert listed["items"][0].id == newer.id
    assert listed["items"][1].id == older.id


def test_payout_totals_include_total_count_and_average(db_session):
    _create_payout(db_session, payout_date=date(2026, 3, 1), amount=1000.0)
    _create_payout(db_session, payout_date=date(2026, 3, 5), amount=500.0)

    totals = get_payout_totals(db=db_session)

    assert totals["total_amount_cents"] == 150000
    assert totals["count"] == 2
    assert totals["average_amount_cents"] == 75000
    assert totals["average_amount"] == 750.0


def test_update_payout_changes_amount_date_and_notes(db_session):
    created = _create_payout(db_session, payout_date=date(2026, 3, 1), amount=1250.0, notes="Original")

    updated = update_payout(
        payout_id=created.id,
        payload=PayoutUpdateIn(
            payout_date=date(2026, 3, 15),
            amount_cents=200075,
            notes="  Final payout  ",
        ),
        db=db_session,
    )

    assert updated.payout_date == date(2026, 3, 15)
    assert updated.amount_cents == 200075
    assert updated.amount == 2000.75
    assert updated.notes == "Final payout"


def test_update_payout_can_clear_notes(db_session):
    created = _create_payout(db_session, payout_date=date(2026, 3, 1), amount=1250.0, notes="Original")

    updated = update_payout(
        payout_id=created.id,
        payload=PayoutUpdateIn(notes=None),
        db=db_session,
    )

    assert updated.notes is None


def test_empty_payout_update_payload_is_rejected():
    with pytest.raises(ValidationError):
        PayoutUpdateIn()


def test_invalid_payout_update_leaves_row_unchanged(db_session):
    created = _create_payout(db_session, payout_date=date(2026, 3, 1), amount=1250.0)

    with pytest.raises(HTTPException) as exc_info:
        update_payout(
            payout_id=created.id,
            payload=PayoutUpdateIn(amount_cents=0),
            db=db_session,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "amount_cents must be > 0"
    row = db_session.query(Payout).filter(Payout.id == created.id).one()
    assert row.amount_cents == 125000


def test_payout_routes_are_scoped_to_authenticated_user(db_session, monkeypatch):
    created = _create_payout(db_session, payout_date=date(2026, 3, 3), amount=820.0)
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: "other-user")

    listed = list_payouts(limit=200, offset=0, db=db_session)
    totals = get_payout_totals(db=db_session)

    assert listed == {"items": [], "total": 0}
    assert totals["total_amount_cents"] == 0
    assert totals["average_amount_cents"] == 0
    assert totals["count"] == 0

    with pytest.raises(HTTPException) as exc_info:
        delete_payout(payout_id=created.id, db=db_session)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "payout not found"


def test_list_payouts_rejects_invalid_pagination(db_session):
    with pytest.raises(HTTPException) as exc_info:
        list_payouts(limit=0, offset=0, db=db_session)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "limit must be between 1 and 500"


def test_delete_payout_removes_row(db_session):
    created = _create_payout(db_session, payout_date=date(2026, 3, 3), amount=820.0)

    response = delete_payout(payout_id=created.id, db=db_session)

    assert response.status_code == 204
    assert db_session.query(Payout).count() == 0
