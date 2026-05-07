import os
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module
from app.db import Base
from app.models import Account, Trade

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[Account.__table__, Trade.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=[Trade.__table__, Account.__table__])
        engine.dispose()


def _seed_trade(
    db_session,
    *,
    user_id: str,
    account_id: int,
    external_id: str,
    opened_at: datetime,
    pnl: float,
    fees: float,
    symbol: str = "MNQ",
) -> None:
    db_session.add(
        Account(
            id=account_id,
            user_id=user_id,
            provider="projectx",
            external_id=external_id,
            name=external_id,
        )
    )
    db_session.add(
        Trade(
            user_id=user_id,
            account_id=account_id,
            symbol=symbol,
            side="LONG",
            opened_at=opened_at,
            closed_at=opened_at,
            qty=1,
            entry_price=100,
            exit_price=101,
            pnl=pnl,
            fees=fees,
        )
    )
    db_session.commit()


def test_legacy_trades_route_filters_to_authenticated_user(db_session, monkeypatch):
    _seed_trade(
        db_session,
        user_id=USER_A,
        account_id=1001,
        external_id="1001",
        opened_at=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
        pnl=100,
        fees=5,
    )
    _seed_trade(
        db_session,
        user_id=USER_B,
        account_id=2001,
        external_id="2001",
        opened_at=datetime(2026, 5, 2, 10, 0, tzinfo=timezone.utc),
        pnl=999,
        fees=0,
    )
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: USER_A)

    rows = main_module.list_trades(limit=100, db=db_session)

    assert [int(row.account_id) for row in rows] == [1001]


def test_legacy_metrics_routes_filter_to_authenticated_user(db_session, monkeypatch):
    _seed_trade(
        db_session,
        user_id=USER_A,
        account_id=1001,
        external_id="1001",
        opened_at=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
        pnl=100,
        fees=5,
        symbol="MNQ",
    )
    _seed_trade(
        db_session,
        user_id=USER_B,
        account_id=2001,
        external_id="2001",
        opened_at=datetime(2026, 5, 2, 10, 0, tzinfo=timezone.utc),
        pnl=999,
        fees=0,
        symbol="ES",
    )
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: USER_A)

    summary = main_module.metrics_summary(db=db_session)
    by_symbol = main_module.metrics_pnl_by_symbol(db=db_session)

    assert summary["trade_count"] == 1
    assert summary["net_pnl"] == 95.0
    assert by_symbol == [{"symbol": "MNQ", "trade_count": 1, "pnl": 95.0, "win_rate": 100.0}]


def test_legacy_metrics_routes_reject_invalid_account_id(db_session, monkeypatch):
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: USER_A)

    with pytest.raises(HTTPException) as exc_info:
        main_module.metrics_summary(account_id=0, db=db_session)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "account_id must be a positive integer"
