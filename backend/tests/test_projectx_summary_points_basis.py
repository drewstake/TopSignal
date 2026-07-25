import os
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.models import InstrumentMetadata, ProjectXTradeEvent
from app.services.projectx_trades import (
    get_trade_event_pnl_calendar,
    list_trade_events,
    summarize_trade_events,
)


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


def test_summarize_trade_events_filters_points_metrics_to_requested_symbol():
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

        # MNQ basis uses only MNQ trades.
        assert summary_mnq["avgPointGain"] == 2.0
        assert summary_mnq["avgPointLoss"] is None
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


def test_get_trade_event_pnl_calendar_filters_closed_rows_and_respects_inclusive_bounds():
    engine, db = _make_session()
    start = datetime(2026, 3, 1, 23, 0, tzinfo=timezone.utc)
    end = datetime(2026, 3, 2, 22, 59, 59, 999000, tzinfo=timezone.utc)
    try:
        db.add_all(
            [
                ProjectXTradeEvent(
                    account_id=5003,
                    contract_id="CON.F.US.MNQ.H26",
                    symbol="MNQ",
                    side="BUY",
                    size=1.0,
                    price=20000.0,
                    trade_timestamp=start,
                    fees=0.5,
                    pnl=10.0,
                    order_id="ORD-4",
                ),
                ProjectXTradeEvent(
                    account_id=5003,
                    contract_id="CON.F.US.MNQ.H26",
                    symbol="MNQ",
                    side="SELL",
                    size=1.0,
                    price=20001.0,
                    trade_timestamp=end,
                    fees=0.5,
                    pnl=20.0,
                    order_id="ORD-5",
                ),
                ProjectXTradeEvent(
                    account_id=5003,
                    contract_id="CON.F.US.MNQ.H26",
                    symbol="MNQ",
                    side="BUY",
                    size=1.0,
                    price=20002.0,
                    trade_timestamp=datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc),
                    fees=99.0,
                    pnl=None,
                    order_id="ORD-OPEN",
                ),
                ProjectXTradeEvent(
                    account_id=5003,
                    contract_id="CON.F.US.MNQ.H26",
                    symbol="MNQ",
                    side="BUY",
                    size=1.0,
                    price=20003.0,
                    trade_timestamp=datetime(2026, 3, 2, 13, 0, tzinfo=timezone.utc),
                    fees=0.5,
                    pnl=100.0,
                    order_id="ORD-VOID",
                    raw_payload={"voided": True},
                ),
                ProjectXTradeEvent(
                    account_id=5003,
                    contract_id="CON.F.US.MNQ.H26",
                    symbol="MNQ",
                    side="BUY",
                    size=1.0,
                    price=19999.0,
                    trade_timestamp=start - timedelta(microseconds=1),
                    fees=0.5,
                    pnl=100.0,
                    order_id="ORD-BEFORE",
                ),
                ProjectXTradeEvent(
                    account_id=5003,
                    contract_id="CON.F.US.MNQ.H26",
                    symbol="MNQ",
                    side="SELL",
                    size=1.0,
                    price=20004.0,
                    trade_timestamp=end + timedelta(microseconds=1),
                    fees=0.5,
                    pnl=100.0,
                    order_id="ORD-AFTER",
                ),
            ]
        )
        db.commit()

        calendar = get_trade_event_pnl_calendar(db, account_id=5003, start=start, end=end)

        assert calendar == [
            {
                "date": "2026-03-02",
                "trade_count": 2,
                "gross_pnl": 30.0,
                "fees": 2.0,
                "non_commission_fees": 2.0,
                "commissions": 0.0,
                "net_pnl": 28.0,
                "win_count": 2,
                "loss_count": 0,
                "breakeven_count": 0,
            }
        ]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeEvent.__table__, InstrumentMetadata.__table__])
        engine.dispose()


def test_trade_ranges_use_authoritative_trade_date_for_imports_and_timestamp_for_provider_rows():
    engine, db = _make_session()
    start = datetime(2026, 3, 1, 23, 0, tzinfo=timezone.utc)
    end = datetime(2026, 3, 2, 22, 59, 59, 999999, tzinfo=timezone.utc)
    try:
        db.add_all(
            [
                # The imported trading date is in range even though its exit
                # timestamp is not.
                ProjectXTradeEvent(
                    account_id=5004,
                    contract_id="CON.F.US.MNQ.H26",
                    symbol="MNQ",
                    side="BUY",
                    size=1.0,
                    price=20000.0,
                    trade_timestamp=datetime(2026, 3, 5, 14, 0, tzinfo=timezone.utc),
                    fees=0.0,
                    commissions=0.0,
                    fee_scope="round_turn",
                    pnl=100.0,
                    trade_date=date(2026, 3, 2),
                    order_id="IMPORTED-IN",
                ),
                # The timestamp is in range, but the authoritative imported
                # trading date is not.
                ProjectXTradeEvent(
                    account_id=5004,
                    contract_id="CON.F.US.MNQ.H26",
                    symbol="MNQ",
                    side="BUY",
                    size=1.0,
                    price=20001.0,
                    trade_timestamp=datetime(2026, 3, 2, 14, 0, tzinfo=timezone.utc),
                    fees=0.0,
                    commissions=0.0,
                    fee_scope="round_turn",
                    pnl=200.0,
                    trade_date=date(2026, 3, 3),
                    order_id="IMPORTED-OUT",
                ),
                # Provider rows have no trade_date and retain timestamp bounds.
                ProjectXTradeEvent(
                    account_id=5004,
                    contract_id="CON.F.US.MNQ.H26",
                    symbol="MNQ",
                    side="BUY",
                    size=1.0,
                    price=20002.0,
                    trade_timestamp=datetime(2026, 3, 2, 15, 0, tzinfo=timezone.utc),
                    fees=0.0,
                    pnl=50.0,
                    order_id="PROVIDER-IN",
                ),
                ProjectXTradeEvent(
                    account_id=5004,
                    contract_id="CON.F.US.MNQ.H26",
                    symbol="MNQ",
                    side="BUY",
                    size=1.0,
                    price=20003.0,
                    trade_timestamp=datetime(2026, 3, 5, 15, 0, tzinfo=timezone.utc),
                    fees=0.0,
                    pnl=400.0,
                    order_id="PROVIDER-OUT",
                ),
            ]
        )
        db.commit()

        rows = list_trade_events(
            db,
            account_id=5004,
            limit=10,
            start=start,
            end=end,
        )
        assert {row.order_id for row in rows} == {"IMPORTED-IN", "PROVIDER-IN"}

        summary = summarize_trade_events(
            db,
            account_id=5004,
            start=start,
            end=end,
        )
        assert summary["trade_count"] == 2
        assert summary["gross_pnl"] == 150.0
        assert summary["net_pnl"] == 150.0

        calendar = get_trade_event_pnl_calendar(
            db,
            account_id=5004,
            start=start,
            end=end,
        )
        assert calendar == [
            {
                "date": "2026-03-02",
                "trade_count": 2,
                "gross_pnl": 150.0,
                "fees": 0.0,
                "non_commission_fees": 0.0,
                "commissions": 0.0,
                "net_pnl": 150.0,
                "win_count": 2,
                "loss_count": 0,
                "breakeven_count": 0,
            }
        ]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeEvent.__table__, InstrumentMetadata.__table__])
        engine.dispose()
