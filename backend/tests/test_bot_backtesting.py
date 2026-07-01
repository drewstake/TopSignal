import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.models import BotConfig, ProjectXMarketCandle
from app.services.bot_service import SignalResult
from app.services.bot_backtesting import run_bot_backtest
import app.services.bot_backtesting as bot_backtesting_module


USER_ID = "00000000-0000-0000-0000-000000000000"


def _make_db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _candle(timestamp: datetime, close: float) -> ProjectXMarketCandle:
    return ProjectXMarketCandle(
        user_id=USER_ID,
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
        volume=100,
        is_partial=False,
    )


def test_run_bot_backtest_replays_cached_candles_and_returns_stats():
    db = _make_db()
    start = datetime(2026, 3, 1, 14, 0, tzinfo=timezone.utc)
    closes = [10.0] * 22 + [10.0, 9.0, 8.0, 20.0, 22.0, 24.0]
    config = BotConfig(
        user_id=USER_ID,
        account_id=1,
        name="MNQ SMA Backtest",
        strategy_type="sma_cross",
        strategy_params={},
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
        trading_start_time="09:30",
        trading_end_time="15:45",
        cooldown_seconds=0,
        max_data_staleness_seconds=600,
        allow_market_depth=False,
    )
    db.add(config)
    db.flush()
    for index, close in enumerate(closes):
        db.add(_candle(start + timedelta(minutes=5 * index), close))
    db.commit()

    result = run_bot_backtest(
        db,
        user_id=USER_ID,
        config=config,
        client=object(),
        start=start,
        end=start + timedelta(minutes=5 * (len(closes) - 1)),
        limit=100,
    )

    assert result["candles_processed"] == len(closes)
    assert result["signals_evaluated"] > 0
    assert result["summary"]["trade_count"] == 1
    assert result["summary"]["win_rate"] == 100.0
    assert result["summary"]["gross_pnl"] == 8.0
    assert result["summary"]["net_pnl"] == 8.0
    assert result["analysis"]["by_side"]["BUY"]["trade_count"] == 1
    assert result["analysis"]["best_trade"]["duration_minutes"] > 0
    assert result["trades"][0]["side"] == "BUY"
    assert result["trades"][0]["exit_reason"] == "end_of_backtest"
    assert "max_favorable_points" in result["trades"][0]


def test_run_bot_backtest_default_start_uses_farthest_timeframe_window():
    db = _make_db()
    end = datetime(2026, 5, 1, 16, 0, tzinfo=timezone.utc)
    start = end - timedelta(minutes=5 * 99)
    config = BotConfig(
        user_id=USER_ID,
        account_id=1,
        name="MNQ Max Window Backtest",
        strategy_type="sma_cross",
        strategy_params={},
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
        trading_start_time="09:30",
        trading_end_time="15:45",
        cooldown_seconds=0,
        max_data_staleness_seconds=600,
        allow_market_depth=False,
    )
    db.add(config)
    db.flush()
    for index in range(100):
        db.add(_candle(start + timedelta(minutes=5 * index), 10.0))
    db.commit()

    result = run_bot_backtest(
        db,
        user_id=USER_ID,
        config=config,
        client=object(),
        start=None,
        end=end,
        limit=100,
    )

    assert result["start"] == start
    assert result["candles_processed"] == 100


def test_run_bot_backtest_skips_signal_evaluation_outside_session(monkeypatch):
    db = _make_db()
    start = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)
    config = BotConfig(
        user_id=USER_ID,
        account_id=1,
        name="MNQ Session Skip Backtest",
        strategy_type="sma_cross",
        strategy_params={},
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
        max_trades_per_day=0,
        max_open_position=1,
        allowed_contracts=["CON.F.US.MNQ.M26"],
        trading_start_time="09:30",
        trading_end_time="15:45",
        cooldown_seconds=0,
        max_data_staleness_seconds=600,
        allow_market_depth=False,
    )
    db.add(config)
    db.flush()
    for index in range(50):
        db.add(_candle(start + timedelta(minutes=5 * index), 10.0))
    db.commit()

    evaluated_at: list[datetime] = []

    def fake_evaluate_strategy_signal(*, candles, **_kwargs):
        timestamp = candles[-1].candle_timestamp
        evaluated_at.append(timestamp)
        return SignalResult(action="HOLD", reason="test hold", candle_timestamp=timestamp, price=10.0, raw_payload={})

    monkeypatch.setattr(bot_backtesting_module, "_evaluate_strategy_signal", fake_evaluate_strategy_signal)

    result = run_bot_backtest(
        db,
        user_id=USER_ID,
        config=config,
        client=object(),
        start=start,
        end=start + timedelta(minutes=5 * 49),
        limit=100,
    )

    assert result["signals_evaluated"] == len(evaluated_at)
    assert 0 < len(evaluated_at) < result["candles_processed"] - 25
    assert all(timestamp.hour >= 14 and timestamp.minute >= 30 or timestamp.hour > 14 for timestamp in evaluated_at)
