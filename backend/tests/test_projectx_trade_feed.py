import os
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module
from app.auth import DEFAULT_USER_ID
from app.db import Base
from app.main import list_projectx_account_trades
from app.models import Account, ProjectXTradeEvent
from app.services.projectx_trades import list_trade_events


OTHER_USER_ID = "11111111-1111-1111-1111-111111111111"


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


def _ts(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 2, 10, hour, minute, tzinfo=timezone.utc)


def _add_account(db_session, *, account_id: int, user_id: str = DEFAULT_USER_ID):
    db_session.add(
        Account(
            user_id=user_id,
            provider="projectx",
            external_id=str(account_id),
            name=f"Account {account_id}",
            account_state="ACTIVE",
        )
    )


def _add_trade(
    db_session,
    *,
    event_id: int,
    account_id: int = 9101,
    user_id: str = DEFAULT_USER_ID,
    symbol: str | None = "MNQ",
    contract_id: str = "CON.F.US.MNQ.H26",
    timestamp: datetime | None = None,
    pnl: float | None = 25.0,
):
    db_session.add(
        ProjectXTradeEvent(
            id=event_id,
            user_id=user_id,
            account_id=account_id,
            contract_id=contract_id,
            symbol=symbol,
            side="BUY",
            size=1.0,
            price=20500.0 + event_id,
            trade_timestamp=timestamp or _ts(10),
            fees=1.0,
            pnl=pnl,
            order_id=f"ORD-{event_id}",
            source_trade_id=f"SRC-{event_id}",
        )
    )


def test_trades_endpoint_requires_owned_account_before_serving_cached_rows(db_session):
    _add_account(db_session, account_id=9101, user_id=OTHER_USER_ID)
    _add_trade(db_session, event_id=1, account_id=9101)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        list_projectx_account_trades(account_id=9101, db=db_session)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Account not found."


def test_trades_endpoint_returns_404_for_unknown_account_before_provider_sync(db_session, monkeypatch):
    monkeypatch.setattr(
        main_module,
        "_projectx_client_for_user",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("provider should not be called")),
    )

    with pytest.raises(HTTPException) as exc_info:
        list_projectx_account_trades(account_id=9199, db=db_session)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Account not found."


def test_validate_time_range_normalizes_mixed_naive_and_aware_datetimes():
    main_module._validate_time_range(
        start=datetime(2026, 2, 10, 10, 0),
        end=datetime(2026, 2, 10, 10, 1, tzinfo=timezone.utc),
    )

    with pytest.raises(HTTPException) as exc_info:
        main_module._validate_time_range(
            start=datetime(2026, 2, 10, 10, 2),
            end=datetime(2026, 2, 10, 10, 1, tzinfo=timezone.utc),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "start must be before end"


def test_list_trade_events_filters_orders_and_limits_closed_rows(db_session):
    _add_trade(db_session, event_id=1, timestamp=_ts(10), symbol="MNQ")
    _add_trade(db_session, event_id=2, timestamp=_ts(10), symbol="MES", contract_id="CON.F.US.MES.H26")
    _add_trade(db_session, event_id=3, timestamp=_ts(11), symbol="MNQ", pnl=None)
    _add_trade(db_session, event_id=4, timestamp=_ts(12), symbol="NQ", contract_id="CON.F.US.NQ.H26")
    _add_trade(db_session, event_id=5, account_id=9102, timestamp=_ts(13), symbol="MNQ")
    _add_trade(db_session, event_id=6, user_id=OTHER_USER_ID, timestamp=_ts(14), symbol="MNQ")
    db_session.commit()

    rows = list_trade_events(db_session, account_id=9101, user_id=DEFAULT_USER_ID, limit=2)

    assert [int(row.id) for row in rows] == [4, 2]

    filtered_rows = list_trade_events(
        db_session,
        account_id=9101,
        user_id=DEFAULT_USER_ID,
        limit=10,
        start=datetime(2026, 2, 10, 9, 59),
        end=_ts(10),
        symbol_query="mnq",
    )

    assert [int(row.id) for row in filtered_rows] == [1]


def test_list_trade_events_symbol_filter_matches_contract_when_symbol_is_missing(db_session):
    _add_trade(
        db_session,
        event_id=7,
        symbol=None,
        contract_id="CON.F.US.MGC.M26",
        timestamp=_ts(15),
    )
    db_session.commit()

    rows = list_trade_events(
        db_session,
        account_id=9101,
        user_id=DEFAULT_USER_ID,
        limit=10,
        symbol_query="mgc",
    )

    assert [int(row.id) for row in rows] == [7]
