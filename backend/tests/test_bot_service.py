import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.models import (
    Account,
    BotConfig,
    BotDecision,
    BotOrderAttempt,
    BotRiskEvent,
    BotRun,
    ProjectXMarketCandle,
    ProjectXTradeEvent,
)
from app.services.bot_service import evaluate_bot_config, evaluate_sma_cross


def _dt(minutes_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


def _make_candle(timestamp: datetime, close: float) -> ProjectXMarketCandle:
    return ProjectXMarketCandle(
        user_id="00000000-0000-0000-0000-000000000000",
        contract_id="CON.F.US.MNQ.M26",
        symbol="MNQ",
        live=False,
        unit="minute",
        unit_number=5,
        candle_timestamp=timestamp,
        open_price=close,
        high_price=close,
        low_price=close,
        close_price=close,
        volume=1,
        is_partial=False,
    )


def test_sma_cross_generates_buy_signal_on_latest_closed_candle():
    candles = [
        _make_candle(datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc), 10),
        _make_candle(datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc), 10),
        _make_candle(datetime(2026, 4, 1, 10, 10, tzinfo=timezone.utc), 9),
        _make_candle(datetime(2026, 4, 1, 10, 15, tzinfo=timezone.utc), 20),
    ]

    signal = evaluate_sma_cross(candles, fast_period=2, slow_period=3)

    assert signal.action == "BUY"
    assert "SMA crossover" in signal.reason
    assert signal.price == 20.0


def test_dry_run_evaluation_logs_order_attempt_without_submitting():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        Account.__table__,
        ProjectXMarketCandle.__table__,
        BotConfig.__table__,
        BotRun.__table__,
        BotDecision.__table__,
        BotOrderAttempt.__table__,
        BotRiskEvent.__table__,
        ProjectXTradeEvent.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    class StubClient:
        place_order_called = False

        def retrieve_bars(self, **_kwargs):
            return [
                {"timestamp": _dt(3), "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1},
                {"timestamp": _dt(2), "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1},
                {"timestamp": _dt(1), "open": 9, "high": 9, "low": 9, "close": 9, "volume": 1},
                {"timestamp": _dt(0), "open": 20, "high": 20, "low": 20, "close": 20, "volume": 1},
            ]

        def place_order(self, **_kwargs):
            self.place_order_called = True
            raise AssertionError("dry-run evaluation should not place provider orders")

    try:
        account = Account(
            user_id="00000000-0000-0000-0000-000000000000",
            provider="projectx",
            external_id="9001",
            name="Practice 9001",
            account_state="ACTIVE",
            can_trade=True,
            is_visible=True,
        )
        db.add(account)
        config = BotConfig(
            user_id="00000000-0000-0000-0000-000000000000",
            account_id=9001,
            name="Test Bot",
            enabled=True,
            execution_mode="dry_run",
            contract_id="CON.F.US.MNQ.M26",
            symbol="MNQ",
            timeframe_unit="minute",
            timeframe_unit_number=5,
            lookback_bars=25,
            fast_period=2,
            slow_period=3,
            order_size=1,
            max_contracts=1,
            max_daily_loss=250,
            max_trades_per_day=3,
            max_open_position=1,
            allowed_contracts=["CON.F.US.MNQ.M26"],
            trading_start_time="00:00",
            trading_end_time="23:59",
            cooldown_seconds=0,
            max_data_staleness_seconds=3600,
        )
        db.add(config)
        db.flush()
        run = BotRun(
            user_id="00000000-0000-0000-0000-000000000000",
            bot_config_id=config.id,
            account_id=9001,
            dry_run=True,
            status="running",
        )
        db.add(run)
        db.flush()
        client = StubClient()

        result = evaluate_bot_config(
            db,
            user_id="00000000-0000-0000-0000-000000000000",
            config=config,
            account=account,
            client=client,
            run=run,
            dry_run=True,
        )
        db.commit()

        assert result.decision.action == "BUY"
        assert result.order_attempt is not None
        assert result.order_attempt.status == "dry_run"
        assert client.place_order_called is False
        assert db.query(BotDecision).count() == 1
        assert db.query(BotOrderAttempt).count() == 1
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=list(reversed(tables)))
        engine.dispose()


def test_live_evaluation_is_blocked_without_explicit_confirmation():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        Account.__table__,
        ProjectXMarketCandle.__table__,
        BotConfig.__table__,
        BotRun.__table__,
        BotDecision.__table__,
        BotOrderAttempt.__table__,
        BotRiskEvent.__table__,
        ProjectXTradeEvent.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    class StubClient:
        def retrieve_bars(self, **_kwargs):
            return [
                {"timestamp": _dt(3), "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1},
                {"timestamp": _dt(2), "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1},
                {"timestamp": _dt(1), "open": 9, "high": 9, "low": 9, "close": 9, "volume": 1},
                {"timestamp": _dt(0), "open": 20, "high": 20, "low": 20, "close": 20, "volume": 1},
            ]

        def place_order(self, **_kwargs):
            raise AssertionError("risk gate should block provider order submission")

    try:
        account = Account(
            user_id="00000000-0000-0000-0000-000000000000",
            provider="projectx",
            external_id="9002",
            name="Practice 9002",
            account_state="ACTIVE",
            can_trade=True,
            is_visible=True,
        )
        config = BotConfig(
            user_id="00000000-0000-0000-0000-000000000000",
            account_id=9002,
            name="Live Block Test",
            enabled=True,
            execution_mode="live",
            contract_id="CON.F.US.MNQ.M26",
            symbol="MNQ",
            timeframe_unit="minute",
            timeframe_unit_number=5,
            lookback_bars=25,
            fast_period=2,
            slow_period=3,
            order_size=1,
            max_contracts=1,
            max_daily_loss=250,
            max_trades_per_day=3,
            max_open_position=1,
            allowed_contracts=["CON.F.US.MNQ.M26"],
            trading_start_time="00:00",
            trading_end_time="23:59",
            cooldown_seconds=0,
            max_data_staleness_seconds=3600,
        )
        db.add_all([account, config])
        db.flush()

        result = evaluate_bot_config(
            db,
            user_id="00000000-0000-0000-0000-000000000000",
            config=config,
            account=account,
            client=StubClient(),
            dry_run=False,
            confirm_live_order_routing=False,
        )
        db.commit()

        assert result.order_attempt is None
        assert {event.code for event in result.risk_events} == {"live_order_confirmation_missing"}
        assert db.query(BotRiskEvent).count() == 1
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=list(reversed(tables)))
        engine.dispose()

