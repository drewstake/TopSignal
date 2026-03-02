import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module
from app.db import Base
from app.main import (
    get_projectx_account_last_trade,
    list_projectx_accounts,
    set_projectx_main_account,
)
from app.models import Account, ProjectXTradeEvent


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


def test_accounts_route_default_view_shows_active_plus_main_with_state_sync(db_session, monkeypatch):
    class StubClient:
        def list_accounts(self, *, only_active_accounts=True):
            assert only_active_accounts is False
            return [
                {"id": 7001, "name": "Alpha", "balance": 25000.0, "can_trade": False, "is_visible": True},
                {"id": 7002, "name": "Bravo", "balance": 50000.0, "can_trade": True, "is_visible": True},
            ]

    monkeypatch.setattr(main_module.ProjectXClient, "from_env", lambda: StubClient())

    db_session.add(
        Account(
            provider="projectx",
            external_id="7999",
            name="Main Legacy",
            account_state="ACTIVE",
            is_main=True,
            first_seen_at=datetime.now(timezone.utc) - timedelta(days=7),
            last_seen_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    db_session.commit()

    payload = list_projectx_accounts(show_inactive=False, show_missing=False, db=db_session)
    by_id = {int(account["id"]): account for account in payload}

    assert sorted(by_id.keys()) == [7002, 7999]
    assert by_id[7002]["account_state"] == "ACTIVE"
    assert by_id[7002]["is_main"] is False
    assert by_id[7999]["account_state"] == "MISSING"
    assert by_id[7999]["is_main"] is True

    locked_out_row = (
        db_session.query(Account)
        .filter(Account.provider == "projectx")
        .filter(Account.external_id == "7001")
        .one()
    )
    assert locked_out_row.account_state == "LOCKED_OUT"
    assert locked_out_row.first_seen_at is not None
    assert locked_out_row.last_seen_at is not None


def test_accounts_route_filters_inactive_and_missing_states(db_session, monkeypatch):
    class StubClient:
        def list_accounts(self, *, only_active_accounts=True):
            assert only_active_accounts is False
            return [
                {"id": 7101, "name": "Active", "balance": 10000.0, "can_trade": True, "is_visible": True},
                {"id": 7102, "name": "Locked", "balance": 20000.0, "can_trade": False, "is_visible": True},
                {"id": 7103, "name": "Hidden", "balance": 30000.0, "can_trade": True, "is_visible": False},
            ]

    monkeypatch.setattr(main_module.ProjectXClient, "from_env", lambda: StubClient())
    db_session.add(
        Account(
            provider="projectx",
            external_id="7199",
            name="Missing",
            account_state="ACTIVE",
            first_seen_at=datetime.now(timezone.utc) - timedelta(days=3),
            last_seen_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    db_session.commit()

    with_inactive = list_projectx_accounts(show_inactive=True, show_missing=False, db=db_session)
    ids_with_inactive = sorted(int(row["id"]) for row in with_inactive)
    assert ids_with_inactive == [7101, 7102, 7103]

    with_missing = list_projectx_accounts(show_inactive=False, show_missing=True, db=db_session)
    ids_with_missing = sorted(int(row["id"]) for row in with_missing)
    assert ids_with_missing == [7101, 7199]

    by_id = {int(row["id"]): row for row in with_missing}
    assert by_id[7199]["account_state"] == "MISSING"


def test_set_main_account_endpoint_keeps_single_main_flag(db_session):
    db_session.add_all(
        [
            Account(provider="projectx", external_id="7201", name="One", account_state="ACTIVE", is_main=False),
            Account(provider="projectx", external_id="7202", name="Two", account_state="ACTIVE", is_main=False),
        ]
    )
    db_session.commit()

    first = set_projectx_main_account(account_id=7201, db=db_session)
    second = set_projectx_main_account(account_id=7202, db=db_session)

    assert first == {"account_id": 7201, "is_main": True}
    assert second == {"account_id": 7202, "is_main": True}

    rows = (
        db_session.query(Account)
        .filter(Account.provider == "projectx")
        .order_by(Account.external_id.asc())
        .all()
    )
    assert [bool(row.is_main) for row in rows] == [False, True]


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
