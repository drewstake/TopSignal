import os
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.models import PositionLifecycle
from app.services.streaming_pnl_tracker import (
    ClosedPositionLifecycle,
    StreamingPnlTracker,
    save_position_lifecycle_mae_mfe,
)


def _dt(hour: int, minute: int = 0) -> str:
    return datetime(2026, 3, 1, hour, minute, tzinfo=timezone.utc).isoformat()


def test_streaming_tracker_tracks_mae_mfe_and_closes_once():
    closed: list[ClosedPositionLifecycle] = []
    tracker = StreamingPnlTracker(
        point_value_by_symbol={"MNQ": 2.0},
        on_lifecycle_closed=closed.append,
    )

    opened = tracker.ingest_position_event(
        {
            "accountId": 99,
            "contractId": "CON.F.US.MNQ.H26",
            "symbol": "MNQ",
            "netQty": 1,
            "avgPrice": 100.0,
            "updatedAt": _dt(9, 0),
        }
    )
    assert opened is True
    assert "CON.F.US.MNQ.H26" in tracker.tracker_by_contract_id
    assert tracker.tracker_by_contract_id["CON.F.US.MNQ.H26"].mae_usd == 0.0
    assert tracker.tracker_by_contract_id["CON.F.US.MNQ.H26"].mfe_usd == 0.0

    tracker.ingest_market_event(
        {
            "contractId": "CON.F.US.MNQ.H26",
            "symbol": "MNQ",
            "bidPrice": 99.0,
            "askPrice": 99.0,
            "timestamp": _dt(9, 1),
        }
    )
    tracker.ingest_market_event(
        {
            "contractId": "CON.F.US.MNQ.H26",
            "symbol": "MNQ",
            "lastPrice": 102.0,
            "timestamp": _dt(9, 2),
        }
    )

    tracker.ingest_position_event(
        {
            "accountId": 99,
            "contractId": "CON.F.US.MNQ.H26",
            "symbol": "MNQ",
            "netQty": 0,
            "avgPrice": 100.0,
            "realizedPnl": 3.5,
            "updatedAt": _dt(9, 3),
        }
    )
    # Repeated close should not persist again.
    tracker.ingest_position_event(
        {
            "accountId": 99,
            "contractId": "CON.F.US.MNQ.H26",
            "symbol": "MNQ",
            "netQty": 0,
            "avgPrice": 100.0,
            "updatedAt": _dt(9, 4),
        }
    )

    assert "CON.F.US.MNQ.H26" not in tracker.tracker_by_contract_id
    assert len(closed) == 1
    assert closed[0].mae_usd == -2.0
    assert closed[0].mfe_usd == 4.0
    assert closed[0].side == "LONG"
    assert closed[0].realized_pnl_usd == 3.5


def test_streaming_tracker_handles_position_flip_as_close_then_open():
    closed: list[ClosedPositionLifecycle] = []
    tracker = StreamingPnlTracker(
        point_value_by_symbol={"MNQ": 2.0},
        on_lifecycle_closed=closed.append,
    )

    tracker.ingest_position_event(
        {
            "accountId": 77,
            "contractId": "CON.F.US.MNQ.H26",
            "symbol": "MNQ",
            "netQty": 1,
            "avgPrice": 100.0,
            "updatedAt": _dt(10, 0),
        }
    )
    tracker.ingest_market_event(
        {
            "contractId": "CON.F.US.MNQ.H26",
            "symbol": "MNQ",
            "lastPrice": 98.0,
            "timestamp": _dt(10, 1),
        }
    )

    tracker.ingest_position_event(
        {
            "accountId": 77,
            "contractId": "CON.F.US.MNQ.H26",
            "symbol": "MNQ",
            "netQty": -1,
            "avgPrice": 101.0,
            "realizedPnl": -1.0,
            "updatedAt": _dt(10, 2),
        }
    )

    assert len(closed) == 1
    assert closed[0].side == "LONG"
    assert closed[0].mae_usd == -4.0
    assert "CON.F.US.MNQ.H26" in tracker.tracker_by_contract_id
    assert tracker.tracker_by_contract_id["CON.F.US.MNQ.H26"].side == "SHORT"


def test_streaming_tracker_recomputes_unrealized_on_market_and_position_updates():
    tracker = StreamingPnlTracker(point_value_by_symbol={"MES": 5.0})

    tracker.ingest_position_event(
        {
            "accountId": 88,
            "contractId": "CON.F.US.MES.H26",
            "symbol": "MES",
            "netQty": -2,
            "avgPrice": 50.0,
            "updatedAt": _dt(11, 0),
        }
    )
    tracker.ingest_market_event(
        {
            "contractId": "CON.F.US.MES.H26",
            "symbol": "MES",
            "lastPrice": 49.0,
            "timestamp": _dt(11, 1),
        }
    )
    tracker.ingest_market_event(
        {
            "contractId": "CON.F.US.MES.H26",
            "symbol": "MES",
            "lastPrice": 51.0,
            "timestamp": _dt(11, 2),
        }
    )
    tracker.ingest_position_event(
        {
            "accountId": 88,
            "contractId": "CON.F.US.MES.H26",
            "symbol": "MES",
            "netQty": -3,
            "avgPrice": 50.0,
            "updatedAt": _dt(11, 3),
        }
    )

    lifecycle = tracker.tracker_by_contract_id["CON.F.US.MES.H26"]
    assert lifecycle.mfe_usd == 10.0
    assert lifecycle.mae_usd == -15.0


def test_streaming_tracker_exposes_latest_market_price_update():
    tracker = StreamingPnlTracker()

    tracker.ingest_market_event(
        {
            "contractId": "CON.F.US.MNQ.H26",
            "symbol": "MNQ",
            "lastPrice": 17425.25,
            "timestamp": _dt(12, 1),
        }
    )

    by_contract = tracker.get_market_price_update(contract_id="CON.F.US.MNQ.H26")
    by_symbol = tracker.get_market_price_update(symbol="MNQ")

    assert by_contract is not None
    assert by_contract.mark_price == 17425.25
    assert by_contract.timestamp == datetime.fromisoformat(_dt(12, 1))
    assert by_symbol == by_contract


def test_save_position_lifecycle_mae_mfe_persists_row():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[PositionLifecycle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    try:
        opened_at = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
        closed_at = datetime(2026, 3, 1, 12, 5, tzinfo=timezone.utc)
        row = save_position_lifecycle_mae_mfe(
            db,
            account_id=42,
            contract_id="CON.F.US.MGC.H26",
            symbol="MGC",
            opened_at=opened_at,
            closed_at=closed_at,
            mae_usd=-25.0,
            mfe_usd=40.0,
            realized_pnl_usd=10.0,
            side="LONG",
            max_qty=2.0,
            avg_entry_at_open=2050.5,
            mae_points=-1.25,
            mfe_points=2.0,
            mae_timestamp=opened_at,
            mfe_timestamp=closed_at,
        )
        db.commit()

        assert int(row.account_id) == 42
        persisted = db.query(PositionLifecycle).all()
        assert len(persisted) == 1
        assert float(persisted[0].mae_usd) == -25.0
        assert float(persisted[0].mfe_usd) == 40.0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[PositionLifecycle.__table__])
        engine.dispose()
