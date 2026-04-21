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
from app.main import (
    get_projectx_account_summary,
    get_projectx_account_summary_with_point_bases,
    list_projectx_account_trades,
)
from app.models import Account, ProjectXTradeEvent
from app.services.projectx_client import ProjectXClientError


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[Account.__table__, ProjectXTradeEvent.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeEvent.__table__, Account.__table__])
        engine.dispose()


class MissingAccountStubClient:
    def fetch_trade_history(self, *args, **kwargs):
        raise ProjectXClientError("missing account", status_code=404)


def _add_trade_event(db_session, *, event_id: int, account_id: int, pnl: float = 125.0, fees: float = 3.0):
    db_session.add(
        ProjectXTradeEvent(
            id=event_id,
            account_id=account_id,
            contract_id="CON.F.US.MNQ.H26",
            symbol="MNQ",
            side="BUY",
            size=1.0,
            price=20500.0,
            trade_timestamp=datetime(2026, 2, 10, 12, 0, tzinfo=timezone.utc),
            fees=fees,
            pnl=pnl,
            order_id=f"ORD-{event_id}",
        )
    )


def test_summary_falls_back_to_local_for_missing_account_on_provider_404(db_session, monkeypatch):
    monkeypatch.setattr(main_module.ProjectXClient, "from_env", lambda: MissingAccountStubClient())
    db_session.add(Account(provider="projectx", external_id="7301", account_state="MISSING", is_main=True))
    _add_trade_event(db_session, event_id=1, account_id=7301, pnl=100.0, fees=2.5)
    db_session.commit()

    payload = get_projectx_account_summary(account_id=7301, refresh=True, db=db_session)

    assert payload["trade_count"] == 1
    assert payload["realized_pnl"] == 100.0
    assert payload["fees"] == 5.0
    assert payload["net_pnl"] == 95.0


def test_summary_raises_gateway_error_for_non_missing_account_on_provider_404(db_session, monkeypatch):
    monkeypatch.setattr(main_module.ProjectXClient, "from_env", lambda: MissingAccountStubClient())
    db_session.add(Account(provider="projectx", external_id="7302", account_state="ACTIVE", is_main=False))
    _add_trade_event(db_session, event_id=2, account_id=7302, pnl=80.0, fees=2.0)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        get_projectx_account_summary(account_id=7302, refresh=True, db=db_session)

    assert exc_info.value.status_code == 502


def test_trades_endpoint_falls_back_to_local_for_missing_account_on_provider_404(db_session, monkeypatch):
    monkeypatch.setattr(main_module.ProjectXClient, "from_env", lambda: MissingAccountStubClient())
    db_session.add(Account(provider="projectx", external_id="7303", account_state="MISSING", is_main=True))
    _add_trade_event(db_session, event_id=3, account_id=7303, pnl=55.0, fees=1.0)
    db_session.commit()

    payload = list_projectx_account_trades(account_id=7303, refresh=True, db=db_session)

    assert len(payload) == 1
    assert payload[0]["account_id"] == 7303


def test_summary_with_point_bases_returns_all_point_basis_payloads(db_session):
    db_session.add(Account(provider="projectx", external_id="7304", account_state="ACTIVE", is_main=True))
    _add_trade_event(db_session, event_id=4, account_id=7304, pnl=125.0, fees=2.0)
    db_session.commit()

    payload = get_projectx_account_summary_with_point_bases(account_id=7304, refresh=False, db=db_session)

    assert payload["summary"]["trade_count"] == 1
    assert payload["summary"]["net_pnl"] == 121.0
    assert set(payload["point_payoff_by_basis"].keys()) == {"MNQ", "MES", "MGC", "SIL"}


def test_summary_fallback_adds_topstep_micro_commission_after_april_12_2026(db_session):
    db_session.add(Account(provider="projectx", external_id="7305", account_state="MISSING", is_main=True))
    db_session.add(
        ProjectXTradeEvent(
            id=5,
            account_id=7305,
            contract_id="CON.F.US.MNQ.M26",
            symbol="MNQ",
            side="BUY",
            size=1.0,
            price=20500.0,
            trade_timestamp=datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc),
            fees=0.37,
            pnl=100.0,
            order_id="ORD-5",
        )
    )
    db_session.commit()

    payload = get_projectx_account_summary(account_id=7305, refresh=False, db=db_session)

    assert payload["fees"] == 1.24
    assert payload["net_pnl"] == 98.76
