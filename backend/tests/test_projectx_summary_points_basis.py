import os
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.models import InstrumentMetadata, ProjectXTradeEvent
from app.services.projectx_trades import summarize_trade_events


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 3, 1, hour, minute, tzinfo=timezone.utc)


def _make_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXTradeEvent.__table__, InstrumentMetadata.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, SessionLocal()


def test_summarize_trade_events_supports_points_basis_conversion():
    engine, db = _make_session()
    try:
        db.add_all(
            [
                ProjectXTradeEvent(
                    account_id=5001,
                    contract_id="CON.F.US.MNQ.H26",
                    symbol="MNQ",
                    side="BUY",
                    size=2.0,
                    price=20000.0,
                    trade_timestamp=_dt(9, 0),
                    fees=0.0,
                    pnl=8.0,
                    order_id="ORD-1",
                ),
                ProjectXTradeEvent(
                    account_id=5001,
                    contract_id="CON.F.US.MES.H26",
                    symbol="MES",
                    side="SELL",
                    size=1.0,
                    price=6000.0,
                    trade_timestamp=_dt(9, 1),
                    fees=0.0,
                    pnl=-5.0,
                    order_id="ORD-2",
                ),
            ]
        )
        db.commit()

        summary_auto = summarize_trade_events(db, account_id=5001, points_basis="auto")
        summary_mnq = summarize_trade_events(db, account_id=5001, points_basis="MNQ")

        # Auto mode uses each trade symbol:
        # MNQ gain: 8/(2*2)=2.0, MES loss: abs(-5/(1*5))=1.0
        assert summary_auto["avgPointGain"] == 2.0
        assert summary_auto["avgPointLoss"] == 1.0
        assert summary_auto["pointsBasisUsed"] == "auto"

        # MNQ basis normalizes both trades by MNQ point value (2):
        # Gain: 8/(2*2)=2.0, Loss: abs(-5/(1*2))=2.5
        assert summary_mnq["avgPointGain"] == 2.0
        assert summary_mnq["avgPointLoss"] == 2.5
        assert summary_mnq["pointsBasisUsed"] == "MNQ"

        # Existing dollar metrics stay unchanged.
        assert summary_auto["avg_win"] == summary_mnq["avg_win"]
        assert summary_auto["avg_loss"] == summary_mnq["avg_loss"]
        assert summary_auto["net_pnl"] == summary_mnq["net_pnl"]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeEvent.__table__, InstrumentMetadata.__table__])
        engine.dispose()


def test_summarize_trade_events_prefers_db_instrument_metadata_when_present():
    engine, db = _make_session()
    try:
        db.add(
            InstrumentMetadata(
                symbol="MNQ",
                tick_size=1.0,
                tick_value=10.0,
            )
        )
        db.add(
            ProjectXTradeEvent(
                account_id=5002,
                contract_id="CON.F.US.MNQ.H26",
                symbol="MNQ",
                side="BUY",
                size=1.0,
                price=20000.0,
                trade_timestamp=_dt(10, 0),
                fees=0.0,
                pnl=100.0,
                order_id="ORD-3",
            )
        )
        db.commit()

        summary = summarize_trade_events(db, account_id=5002, points_basis="MNQ")

        # Point value from DB override: 10 / 1 = 10.
        assert summary["avgPointGain"] == 10.0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeEvent.__table__, InstrumentMetadata.__table__])
        engine.dispose()
