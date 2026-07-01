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


def _candle(timestamp: datetime, close: float, **overrides) -> ProjectXMarketCandle:
    values = {
        "user_id": USER_ID,
        "contract_id": "CON.F.US.MNQ.M26",
        "symbol": "MNQ",
        "live": False,
        "unit": "minute",
        "unit_number": 5,
        "candle_timestamp": timestamp,
        "open_price": close,
        "high_price": close,
        "low_price": close,
        "close_price": close,
        "volume": 100,
        "is_partial": False,
    }
    values.update(overrides)
    return ProjectXMarketCandle(**values)


def _config(name: str = "MNQ Backtest", *, strategy_type: str = "sma_cross", lookback_bars: int = 25) -> BotConfig:
    return BotConfig(
        user_id=USER_ID,
        account_id=1,
        name=name,
        strategy_type=strategy_type,
        strategy_params={},
        contract_id="CON.F.US.MNQ.M26",
        symbol="MNQ",
        timeframe_unit="minute",
        timeframe_unit_number=5,
        lookback_bars=lookback_bars,
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


def test_run_bot_backtest_applies_break_even_before_original_stop(monkeypatch):
    db = _make_db()
    start = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)
    config = _config("MNQ Managed Break Even")
    db.add(config)
    db.flush()
    candles = [_candle(start + timedelta(minutes=5 * index), 100.0) for index in range(26)]
    candles.extend(
        [
            _candle(
                start + timedelta(minutes=5 * 26),
                101.0,
                open_price=100.0,
                high_price=102.0,
                low_price=100.5,
            ),
            _candle(
                start + timedelta(minutes=5 * 27),
                99.5,
                open_price=101.0,
                high_price=100.5,
                low_price=99.5,
            ),
        ]
    )
    for candle in candles:
        db.add(candle)
    db.commit()

    emitted = False

    def fake_evaluate_strategy_signal(*, candles, **_kwargs):
        nonlocal emitted
        timestamp = candles[-1].candle_timestamp
        if not emitted:
            emitted = True
            return SignalResult(
                action="BUY",
                reason="managed break-even entry",
                candle_timestamp=timestamp,
                price=100.0,
                raw_payload={
                    "entry_price": 100.0,
                    "stop_loss": 98.0,
                    "take_profit": 110.0,
                    "break_even": {"enabled": True, "trigger_r": 0.75},
                    "profit_lock": {"enabled": False},
                    "time_stop": {"enabled": False},
                },
            )
        return SignalResult(action="HOLD", reason="test hold", candle_timestamp=timestamp, price=100.0, raw_payload={})

    monkeypatch.setattr(bot_backtesting_module, "_evaluate_strategy_signal", fake_evaluate_strategy_signal)

    result = run_bot_backtest(
        db,
        user_id=USER_ID,
        config=config,
        client=object(),
        start=start,
        end=start + timedelta(minutes=5 * (len(candles) - 1)),
        limit=100,
    )

    trade = result["trades"][0]
    assert trade["exit_reason"] == "break_even"
    assert trade["exit_price"] == 100.0
    assert trade["points"] == 0.0


def test_run_bot_backtest_applies_profit_lock_before_original_stop(monkeypatch):
    db = _make_db()
    start = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)
    config = _config("MNQ Managed Profit Lock")
    db.add(config)
    db.flush()
    candles = [_candle(start + timedelta(minutes=5 * index), 100.0) for index in range(26)]
    candles.extend(
        [
            _candle(
                start + timedelta(minutes=5 * 26),
                102.5,
                open_price=100.0,
                high_price=102.5,
                low_price=101.0,
            ),
            _candle(
                start + timedelta(minutes=5 * 27),
                100.4,
                open_price=102.5,
                high_price=101.0,
                low_price=100.4,
            ),
        ]
    )
    for candle in candles:
        db.add(candle)
    db.commit()

    emitted = False

    def fake_evaluate_strategy_signal(*, candles, **_kwargs):
        nonlocal emitted
        timestamp = candles[-1].candle_timestamp
        if not emitted:
            emitted = True
            return SignalResult(
                action="BUY",
                reason="managed profit-lock entry",
                candle_timestamp=timestamp,
                price=100.0,
                raw_payload={
                    "entry_price": 100.0,
                    "stop_loss": 98.0,
                    "take_profit": 110.0,
                    "break_even": {"enabled": True, "trigger_r": 0.75},
                    "profit_lock": {"enabled": True, "trigger_r": 1.25, "lock_r": 0.25},
                    "time_stop": {"enabled": False},
                },
            )
        return SignalResult(action="HOLD", reason="test hold", candle_timestamp=timestamp, price=100.0, raw_payload={})

    monkeypatch.setattr(bot_backtesting_module, "_evaluate_strategy_signal", fake_evaluate_strategy_signal)

    result = run_bot_backtest(
        db,
        user_id=USER_ID,
        config=config,
        client=object(),
        start=start,
        end=start + timedelta(minutes=5 * (len(candles) - 1)),
        limit=100,
    )

    trade = result["trades"][0]
    assert trade["exit_reason"] == "profit_lock"
    assert trade["exit_price"] == 100.5
    assert trade["points"] == 0.5


def test_run_bot_backtest_applies_atr_trailing_stop(monkeypatch):
    db = _make_db()
    start = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)
    config = _config("MNQ Managed ATR Trail")
    db.add(config)
    db.flush()
    candles = [_candle(start + timedelta(minutes=5 * index), 100.0) for index in range(26)]
    candles.extend(
        [
            _candle(
                start + timedelta(minutes=5 * 26),
                104.0,
                open_price=100.0,
                high_price=104.0,
                low_price=103.5,
            ),
            _candle(
                start + timedelta(minutes=5 * 27),
                102.75,
                open_price=104.0,
                high_price=103.5,
                low_price=102.75,
            ),
        ]
    )
    for candle in candles:
        db.add(candle)
    db.commit()

    emitted = False

    def fake_evaluate_strategy_signal(*, candles, **_kwargs):
        nonlocal emitted
        timestamp = candles[-1].candle_timestamp
        if not emitted:
            emitted = True
            return SignalResult(
                action="BUY",
                reason="managed trailing entry",
                candle_timestamp=timestamp,
                price=100.0,
                raw_payload={
                    "entry_price": 100.0,
                    "stop_loss": 95.0,
                    "take_profit": 110.0,
                    "trailing_stop": {"enabled": True, "mode": "atr", "atr_multiplier": 1.0, "atr": 1.0},
                    "break_even": {"enabled": False},
                    "profit_lock": {"enabled": False},
                    "time_stop": {"enabled": False},
                },
            )
        return SignalResult(action="HOLD", reason="test hold", candle_timestamp=timestamp, price=100.0, raw_payload={})

    monkeypatch.setattr(bot_backtesting_module, "_evaluate_strategy_signal", fake_evaluate_strategy_signal)

    result = run_bot_backtest(
        db,
        user_id=USER_ID,
        config=config,
        client=object(),
        start=start,
        end=start + timedelta(minutes=5 * (len(candles) - 1)),
        limit=100,
    )

    trade = result["trades"][0]
    assert trade["exit_reason"] == "trailing_stop"
    assert trade["exit_price"] == 103.0
    assert trade["points"] == 3.0


def test_run_bot_backtest_applies_time_stop(monkeypatch):
    db = _make_db()
    start = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)
    config = _config("MNQ Managed Time Stop")
    db.add(config)
    db.flush()
    closes = [100.0] * 26 + [100.5, 101.0, 101.5, 102.0]
    for index, close in enumerate(closes):
        db.add(_candle(start + timedelta(minutes=5 * index), close))
    db.commit()

    emitted = False

    def fake_evaluate_strategy_signal(*, candles, **_kwargs):
        nonlocal emitted
        timestamp = candles[-1].candle_timestamp
        if not emitted:
            emitted = True
            return SignalResult(
                action="BUY",
                reason="managed time-stop entry",
                candle_timestamp=timestamp,
                price=100.0,
                raw_payload={
                    "entry_price": 100.0,
                    "stop_loss": 95.0,
                    "take_profit": 110.0,
                    "time_stop": {"enabled": True, "bars": 2},
                },
            )
        return SignalResult(action="HOLD", reason="test hold", candle_timestamp=timestamp, price=100.0, raw_payload={})

    monkeypatch.setattr(bot_backtesting_module, "_evaluate_strategy_signal", fake_evaluate_strategy_signal)

    result = run_bot_backtest(
        db,
        user_id=USER_ID,
        config=config,
        client=object(),
        start=start,
        end=start + timedelta(minutes=5 * (len(closes) - 1)),
        limit=100,
    )

    trade = result["trades"][0]
    assert trade["exit_reason"] == "time_stop"
    assert trade["exit_price"] == 101.0
    assert trade["bars_held"] == 2
