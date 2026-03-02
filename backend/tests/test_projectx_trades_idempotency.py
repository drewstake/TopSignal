import os
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.models import ProjectXTradeEvent
from app.services.projectx_trades import refresh_account_trades


class _StubClient:
    def __init__(self, events):
        self._events = events

    def fetch_trade_history(self, account_id, start, end=None, *, limit=None, offset=None):
        return list(self._events)


def test_refresh_account_trades_is_idempotent_for_duplicate_source_trade_id():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXTradeEvent.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db_session = SessionLocal()

    try:
        event_timestamp = datetime(2026, 2, 20, 15, 30, tzinfo=timezone.utc)
        client = _StubClient(
            [
                {
                    "account_id": 8801,
                    "contract_id": "CON.F.US.MNQ.H26",
                    "symbol": "MNQ",
                    "side": "BUY",
                    "size": 1.0,
                    "price": 20500.0,
                    "timestamp": event_timestamp,
                    "fees": 1.4,
                    "pnl": 45.0,
                    "order_id": "ORD-8801-1",
                    "source_trade_id": "SRC-8801-1",
                    "status": "FILLED",
                    "raw_payload": {"id": "SRC-8801-1"},
                }
            ]
        )

        first = refresh_account_trades(
            db_session,
            client,
            account_id=8801,
            start=event_timestamp,
            end=event_timestamp,
        )
        second = refresh_account_trades(
            db_session,
            client,
            account_id=8801,
            start=event_timestamp,
            end=event_timestamp,
        )

        row_count = (
            db_session.query(ProjectXTradeEvent)
            .filter(ProjectXTradeEvent.account_id == 8801)
            .filter(ProjectXTradeEvent.source_trade_id == "SRC-8801-1")
            .count()
        )

        assert first["inserted_count"] == 1
        assert second["inserted_count"] == 0
        assert row_count == 1
    finally:
        db_session.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeEvent.__table__])
        engine.dispose()
