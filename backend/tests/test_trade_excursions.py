import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import PositionLifecycle, ProjectXTradeEvent
from app.services.trade_excursions import attach_trade_excursions

USER = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"
OPEN = datetime(2026, 9, 3, 14, 0, tzinfo=timezone.utc)
CLOSE = OPEN + timedelta(minutes=10)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[PositionLifecycle.__table__, ProjectXTradeEvent.__table__])
    with Session(engine) as session:
        yield session
    engine.dispose()


def seed(db, *, user=USER, account=1, partial=False, duplicates=False):
    for index in range(2 if duplicates else 1):
        db.add(PositionLifecycle(user_id=user, account_id=account, contract_id="MNQ", symbol="MNQ",
                                 opened_at=OPEN, closed_at=CLOSE, side="LONG", max_qty=2 if partial else 1,
                                 mae_usd=-20-index, mfe_usd=60+index))
    event = ProjectXTradeEvent(user_id=user, account_id=account, contract_id="MNQ", symbol="MNQ",
                              side="SELL", size=1, price=20000, pnl=30, trade_timestamp=CLOSE,
                              order_id=f"{user}-{account}", fees=1)
    db.add(event)
    if partial:
        db.add(ProjectXTradeEvent(user_id=user, account_id=account, contract_id="MNQ", symbol="MNQ",
                                  side="SELL", size=1, price=19995, pnl=20,
                                  trade_timestamp=CLOSE-timedelta(minutes=2), order_id="partial", fees=1))
    db.flush()
    return dict(id=event.id, account_id=account, contract_id="MNQ", size=1, entry_time=OPEN, exit_time=CLOSE)


def test_single_position_populates_trade_metrics(db):
    trade = seed(db)
    result = attach_trade_excursions(db, user_id=USER, account_id=1, trades=[trade])[0]
    assert (result["mae"], result["mfe"], result["excursion_scope"]) == (-20, 60, "trade")


def test_partial_exit_outside_page_does_not_duplicate_position_metrics(db):
    trade = seed(db, partial=True)
    result = attach_trade_excursions(db, user_id=USER, account_id=1, trades=[trade])[0]
    assert result["mae"] is None and result["mfe"] is None
    assert result["position_mfe"] == 60 and result["excursion_scope"] == "position"


@pytest.mark.parametrize("user,account", [(OTHER, 1), (USER, 2)])
def test_other_tenant_or_account_never_supplies_excursions(db, user, account):
    trade = seed(db, user=user, account=account)
    result = attach_trade_excursions(db, user_id=USER, account_id=1, trades=[trade])[0]
    assert result["excursion_scope"] == "unavailable"


def test_ambiguous_duplicate_lifecycles_not_selected(db):
    trade = seed(db, duplicates=True)
    result = attach_trade_excursions(db, user_id=USER, account_id=1, trades=[trade])[0]
    assert result["excursion_scope"] == "ambiguous" and result["mfe"] is None


def test_stream_started_after_entry_cannot_claim_full_trade_coverage(db):
    trade = seed(db)
    trade["entry_time"] = OPEN-timedelta(minutes=5)
    result = attach_trade_excursions(db, user_id=USER, account_id=1, trades=[trade])[0]
    assert result["excursion_scope"] == "unavailable"


def test_initial_zero_without_observed_excursion_is_unavailable(db):
    trade = seed(db)
    life = db.query(PositionLifecycle).one()
    life.mae_usd = life.mfe_usd = 0
    life.mae_timestamp = life.mfe_timestamp = OPEN
    db.flush()
    result = attach_trade_excursions(db, user_id=USER, account_id=1, trades=[trade])[0]
    assert result["mae"] is None and result["mfe"] is None
    assert result["excursion_scope"] == "unavailable"
