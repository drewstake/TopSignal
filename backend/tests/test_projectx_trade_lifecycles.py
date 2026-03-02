import os
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.models import ProjectXTradeEvent
from app.services.projectx_trades import derive_trade_execution_lifecycles, list_trade_events


def _dt(minute: int) -> datetime:
    return datetime(2026, 3, 1, 14, minute, tzinfo=timezone.utc)


def _make_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXTradeEvent.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, SessionLocal()


def test_derive_trade_execution_lifecycles_infers_entry_exit_details_from_open_legs():
    engine, db = _make_session()
    try:
        db.add_all(
            [
                ProjectXTradeEvent(
                    account_id=7101,
                    contract_id="CON.F.US.SIL.K26",
                    symbol="CON.F.US.SIL.K26",
                    side="SELL",
                    size=1.0,
                    price=95.805,
                    trade_timestamp=_dt(0),
                    fees=1.02,
                    pnl=None,
                    order_id="ORD-OPEN-1",
                    source_trade_id="SRC-OPEN-1",
                ),
                ProjectXTradeEvent(
                    account_id=7101,
                    contract_id="CON.F.US.SIL.K26",
                    symbol="CON.F.US.SIL.K26",
                    side="SELL",
                    size=1.0,
                    price=95.765,
                    trade_timestamp=_dt(1),
                    fees=1.02,
                    pnl=None,
                    order_id="ORD-OPEN-2",
                    source_trade_id="SRC-OPEN-2",
                ),
                ProjectXTradeEvent(
                    account_id=7101,
                    contract_id="CON.F.US.SIL.K26",
                    symbol="CON.F.US.SIL.K26",
                    side="BUY",
                    size=1.0,
                    price=95.58,
                    trade_timestamp=_dt(2),
                    fees=1.02,
                    pnl=225.0,
                    order_id="ORD-CLOSE-1",
                    source_trade_id="SRC-CLOSE-1",
                ),
                ProjectXTradeEvent(
                    account_id=7101,
                    contract_id="CON.F.US.SIL.K26",
                    symbol="CON.F.US.SIL.K26",
                    side="BUY",
                    size=1.0,
                    price=95.58,
                    trade_timestamp=_dt(3),
                    fees=1.02,
                    pnl=185.0,
                    order_id="ORD-CLOSE-2",
                    source_trade_id="SRC-CLOSE-2",
                ),
            ]
        )
        db.commit()

        closed_rows = list_trade_events(db, account_id=7101, limit=10)
        lifecycle_by_trade_id = derive_trade_execution_lifecycles(
            db,
            account_id=7101,
            closed_rows=closed_rows,
        )
        by_source = {row.source_trade_id: lifecycle_by_trade_id[int(row.id)] for row in closed_rows}

        first_close = by_source["SRC-CLOSE-1"]
        second_close = by_source["SRC-CLOSE-2"]

        assert first_close.entry_timestamp == _dt(0)
        assert first_close.exit_timestamp == _dt(2)
        assert round(float(first_close.duration_minutes or 0.0), 4) == 2.0
        assert round(float(first_close.entry_price or 0.0), 3) == 95.805
        assert round(first_close.exit_price, 3) == 95.58

        assert second_close.entry_timestamp == _dt(1)
        assert second_close.exit_timestamp == _dt(3)
        assert round(float(second_close.duration_minutes or 0.0), 4) == 2.0
        assert round(float(second_close.entry_price or 0.0), 3) == 95.765
        assert round(second_close.exit_price, 3) == 95.58
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeEvent.__table__])
        engine.dispose()

