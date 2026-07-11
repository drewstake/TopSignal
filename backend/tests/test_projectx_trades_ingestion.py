import os
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.models import DEFAULT_USER_ID, ProjectXTradeEvent
from app.services.projectx_trades import list_trade_events, store_trade_events


def _make_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXTradeEvent.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, SessionLocal()


def _event(*, pnl):
    return {
        "account_id": 8801,
        "contract_id": "CON.F.US.MNQ.H26",
        "symbol": "MNQ",
        "side": "BUY",
        "size": 1.0,
        "price": 20500.0,
        "timestamp": datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc),
        "fees": 1.4,
        "pnl": pnl,
        "order_id": "ORD-8801-1",
        "source_trade_id": "SRC-8801-1",
        "status": "FILLED",
        "raw_payload": {"id": "SRC-8801-1", "profitAndLoss": pnl},
    }


def test_store_trade_events_updates_existing_open_row_when_closed_pnl_arrives():
    engine, db_session = _make_session()
    try:
        first_inserted = store_trade_events(db_session, [_event(pnl=None)], user_id=DEFAULT_USER_ID)
        db_session.commit()

        second_inserted = store_trade_events(db_session, [_event(pnl=45.0)], user_id=DEFAULT_USER_ID)
        db_session.commit()

        rows = (
            db_session.query(ProjectXTradeEvent)
            .filter(ProjectXTradeEvent.account_id == 8801)
            .filter(ProjectXTradeEvent.source_trade_id == "SRC-8801-1")
            .all()
        )

        assert first_inserted == 1
        assert second_inserted == 0
        assert len(rows) == 1
        assert float(rows[0].pnl) == 45.0
    finally:
        db_session.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeEvent.__table__])
        engine.dispose()


def test_store_trade_events_tombstones_an_execution_later_marked_voided():
    engine, db_session = _make_session()
    try:
        store_trade_events(db_session, [_event(pnl=45.0)], user_id=DEFAULT_USER_ID)
        db_session.commit()
        voided_event = _event(pnl=45.0)
        voided_event["voided"] = True
        voided_event["raw_payload"] = {
            "id": "SRC-8801-1",
            "profitAndLoss": 45.0,
            "voided": True,
        }

        inserted = store_trade_events(db_session, [voided_event], user_id=DEFAULT_USER_ID)
        db_session.commit()

        row = db_session.query(ProjectXTradeEvent).one()
        assert inserted == 0
        assert row.raw_payload["voided"] is True
        assert list_trade_events(
            db_session,
            account_id=8801,
            user_id=DEFAULT_USER_ID,
            limit=100,
        ) == []
    finally:
        db_session.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeEvent.__table__])
        engine.dispose()
