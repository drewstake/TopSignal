import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module
from app.db import Base
from app.main import get_projectx_account_last_trade, list_projectx_accounts
from app.models import ProjectXTradeEvent


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXTradeEvent.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeEvent.__table__])
        engine.dispose()


def test_accounts_route_includes_last_trade_timestamp_from_local_events(db_session, monkeypatch):
    class StubClient:
        def list_accounts(self, *, only_active_accounts=True):
            assert only_active_accounts is False
            return [
                {"id": 7001, "name": "Alpha", "balance": 25000.0, "status": "INACTIVE"},
                {"id": 7002, "name": "Bravo", "balance": 50000.0, "status": "ACTIVE"},
            ]

    client = StubClient()
    monkeypatch.setattr(main_module.ProjectXClient, "from_env", lambda: client)

    db_session.add_all(
        [
            ProjectXTradeEvent(
                id=1,
                account_id=7001,
                contract_id="CON.F.US.MES.H26",
                symbol="MES",
                side="BUY",
                size=1.0,
                price=6000.0,
                trade_timestamp=datetime(2026, 2, 1, 14, 0, tzinfo=timezone.utc),
                fees=1.2,
                order_id="A-1",
            ),
            ProjectXTradeEvent(
                id=2,
                account_id=7001,
                contract_id="CON.F.US.MES.H26",
                symbol="MES",
                side="SELL",
                size=1.0,
                price=5990.0,
                trade_timestamp=datetime(2026, 2, 3, 20, 30, tzinfo=timezone.utc),
                fees=1.2,
                order_id="A-2",
            ),
            ProjectXTradeEvent(
                id=3,
                account_id=9999,
                contract_id="CON.F.US.MNQ.H26",
                symbol="MNQ",
                side="BUY",
                size=1.0,
                price=20500.0,
                trade_timestamp=datetime(2026, 2, 2, 15, 0, tzinfo=timezone.utc),
                fees=1.2,
                order_id="IGNORED",
            ),
        ]
    )
    db_session.commit()

    payload = list_projectx_accounts(only_active_accounts=False, db=db_session)
    by_id = {int(account["id"]): account for account in payload}

    assert by_id[7001]["last_trade_at"] == datetime(2026, 2, 3, 20, 30, tzinfo=timezone.utc)
    assert by_id[7002]["last_trade_at"] is None


def test_account_last_trade_endpoint_returns_local_value_without_provider_call(db_session, monkeypatch):
    class StubClient:
        def fetch_last_trade_timestamp(self, account_id, *, lookback_days):
            raise AssertionError("provider call should not happen when local timestamp exists")

    monkeypatch.setattr(main_module.ProjectXClient, "from_env", lambda: StubClient())

    db_session.add(
        ProjectXTradeEvent(
            id=10,
            account_id=7010,
            contract_id="CON.F.US.MNQ.H26",
            symbol="MNQ",
            side="BUY",
            size=1.0,
            price=20500.0,
            trade_timestamp=datetime(2026, 2, 4, 9, 15, tzinfo=timezone.utc),
            fees=1.2,
            order_id="LOCAL-1",
        )
    )
    db_session.commit()

    payload = get_projectx_account_last_trade(account_id=7010, refresh=False, db=db_session)
    assert payload["last_trade_at"] == datetime(2026, 2, 4, 9, 15, tzinfo=timezone.utc)
    assert payload["source"] == "local"


def test_account_last_trade_endpoint_uses_provider_when_local_missing(db_session, monkeypatch):
    class StubClient:
        def __init__(self):
            self.calls = []

        def fetch_last_trade_timestamp(self, account_id, *, lookback_days):
            self.calls.append((account_id, lookback_days))
            if account_id == 7020:
                return datetime(2026, 1, 29, 17, 5, tzinfo=timezone.utc)
            return None

    client = StubClient()
    monkeypatch.setattr(main_module.ProjectXClient, "from_env", lambda: client)

    payload = get_projectx_account_last_trade(account_id=7020, refresh=False, db=db_session)
    assert payload["last_trade_at"] == datetime(2026, 1, 29, 17, 5, tzinfo=timezone.utc)
    assert payload["source"] == "provider"
    assert client.calls == [(7020, 3650)]
