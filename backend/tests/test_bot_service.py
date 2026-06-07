import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
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
    PositionLifecycle,
    ProjectXMarketCandle,
    ProjectXTradeEvent,
)
from app.bot_schemas import BotConfigCreateIn, BotConfigUpdateIn, BotStartIn
from app.services.bot_service import (
    evaluate_bollinger_rsi_reversal,
    create_bot_config,
    delete_bot_config,
    evaluate_bollinger_mean_reversion,
    evaluate_atr_adjusted_relative_strength,
    evaluate_donchian_breakout,
    evaluate_delayed_orb_confirmation,
    evaluate_ema_scalping,
    evaluate_ema_trend_pullback,
    evaluate_bot_config,
    evaluate_opening_rvol_breakout,
    evaluate_relative_strength_vs_spy,
    evaluate_pullback_trap_reversal,
    evaluate_fvg_sweep_mss,
    evaluate_fisher_transform_mean_reversion,
    evaluate_liquidity_sweep_retest,
    evaluate_macd_support_resistance,
    evaluate_sma_cross,
    evaluate_supertrend_pivot_points,
    evaluate_support_resistance_levels,
    evaluate_vwap_atr_mean_reversion,
    evaluate_vwap_gap_retrace,
    fetch_and_store_delayed_orb_candles,
    fetch_and_store_market_candles,
    fetch_and_store_opening_rvol_breakout_candles,
    fetch_and_store_relative_strength_spy_candles,
    fetch_and_store_supertrend_pivot_candles,
    fetch_and_store_support_resistance_candles,
    fetch_and_store_vwap_gap_retrace_candles,
    build_bot_market_analysis,
    list_market_candles,
    SignalResult,
    start_bot_run,
    stop_latest_bot_run,
    update_bot_config,
)
from app.services.projectx_client import ProjectXClientError
import app.main as main_module
import app.services.bot_service as bot_service_module


def _dt(minutes_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


def _make_candle(timestamp: datetime, close: float, **overrides) -> ProjectXMarketCandle:
    values = {
        "user_id": "00000000-0000-0000-0000-000000000000",
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
        "volume": 1,
        "is_partial": False,
    }
    values.update(overrides)
    return ProjectXMarketCandle(**values)


def _make_hour_candle(timestamp: datetime, close: float, low: float, high: float, unit_number: int) -> ProjectXMarketCandle:
    return _make_candle(
        timestamp,
        close,
        unit="hour",
        unit_number=unit_number,
        open_price=close,
        low_price=low,
        high_price=high,
    )


def _make_day_candle(timestamp: datetime, close: float, low: float, high: float) -> ProjectXMarketCandle:
    return _make_candle(
        timestamp,
        close,
        unit="day",
        unit_number=1,
        open_price=close,
        low_price=low,
        high_price=high,
    )


def _make_minute_candle(
    timestamp: datetime,
    *,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float,
) -> ProjectXMarketCandle:
    return _make_candle(
        timestamp,
        close,
        unit="minute",
        unit_number=1,
        open_price=open_price,
        high_price=high,
        low_price=low,
        volume=volume,
    )


def _make_daily_candle(timestamp: datetime, close: float) -> ProjectXMarketCandle:
    return _make_candle(
        timestamp,
        close,
        unit="day",
        unit_number=1,
        open_price=close,
        high_price=close,
        low_price=close,
    )


def _regular_session_bar(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 4, day, hour, minute, tzinfo=timezone.utc)


def _bars_from_candles(candles: list[ProjectXMarketCandle]) -> list[dict[str, float | datetime]]:
    return [
        {
            "timestamp": candle.candle_timestamp,
            "open": float(candle.open_price),
            "high": float(candle.high_price),
            "low": float(candle.low_price),
            "close": float(candle.close_price),
            "volume": float(candle.volume),
        }
        for candle in candles
    ]


def _make_delayed_orb_session(
    base: datetime,
    *,
    range_high: float,
    range_low: float,
    confirmation_minutes: int,
    direction: str,
    extra_breakout_candles: int = 0,
) -> list[ProjectXMarketCandle]:
    midpoint = (range_high + range_low) / 2
    opening_range = [
        _make_minute_candle(
            base + timedelta(minutes=index),
            open_price=midpoint,
            high=range_high if index == 4 else range_high - 0.5,
            low=range_low if index == 2 else range_low + 0.5,
            close=midpoint + (0.1 if index % 2 else -0.1),
            volume=10,
        )
        for index in range(15)
    ]
    breakout: list[ProjectXMarketCandle] = []
    for index in range(confirmation_minutes + extra_breakout_candles):
        timestamp = base + timedelta(minutes=15 + index)
        if direction == "long":
            base_price = range_high + 0.4 + index * 0.1
            breakout.append(
                _make_minute_candle(
                    timestamp,
                    open_price=base_price,
                    high=base_price + 0.4,
                    low=base_price + 0.1,
                    close=base_price + 0.2,
                    volume=12 + index,
                )
            )
        else:
            base_price = range_low - 0.4 - index * 0.1
            breakout.append(
                _make_minute_candle(
                    timestamp,
                    open_price=base_price,
                    high=base_price + 0.1,
                    low=base_price - 0.3,
                    close=base_price - 0.2,
                    volume=12 + index,
                )
            )
    return opening_range + breakout


def _make_bollinger_base_candles(
    base: datetime,
    *,
    count: int = 60,
    low_close: float = 99.0,
    high_close: float = 101.0,
) -> list[ProjectXMarketCandle]:
    candles: list[ProjectXMarketCandle] = []
    for index in range(count):
        close = low_close if index % 2 == 0 else high_close
        candles.append(
            _make_candle(
                base + timedelta(minutes=index * 5),
                close,
                open_price=100.0,
                high_price=close + 0.5,
                low_price=close - 0.5,
                volume=10,
            )
        )
    return candles


def _make_price_sequence(
    start: datetime,
    prices: list[float],
    *,
    high_offset: float = 0.3,
    low_offset: float = 0.3,
) -> list[ProjectXMarketCandle]:
    return [
        _make_candle(
            start + timedelta(minutes=index * 5),
            close,
            open_price=close,
            high_price=close + high_offset,
            low_price=close - low_offset,
        )
        for index, close in enumerate(prices)
    ]


def _make_bollinger_rsi_long_candles() -> list[ProjectXMarketCandle]:
    base = datetime(2026, 4, 1, 13, 0, tzinfo=timezone.utc)
    return [
        _make_candle(base + timedelta(minutes=0), 100.0, open_price=100.0, high_price=100.2, low_price=99.8, volume=10),
        _make_candle(base + timedelta(minutes=5), 100.2, open_price=100.0, high_price=100.3, low_price=99.9, volume=10),
        _make_candle(base + timedelta(minutes=10), 100.4, open_price=100.2, high_price=100.5, low_price=100.1, volume=10),
        _make_candle(base + timedelta(minutes=15), 100.1, open_price=100.4, high_price=100.45, low_price=100.0, volume=10),
        _make_candle(base + timedelta(minutes=20), 98.8, open_price=100.0, high_price=100.05, low_price=98.6, volume=15),
        _make_candle(base + timedelta(minutes=25), 99.2, open_price=98.7, high_price=99.3, low_price=98.7, volume=15),
    ]


def _make_bollinger_rsi_short_candles() -> list[ProjectXMarketCandle]:
    base = datetime(2026, 4, 1, 14, 0, tzinfo=timezone.utc)
    return [
        _make_candle(base + timedelta(minutes=0), 100.0, open_price=100.0, high_price=100.2, low_price=99.8, volume=10),
        _make_candle(base + timedelta(minutes=5), 99.8, open_price=100.0, high_price=100.1, low_price=99.7, volume=10),
        _make_candle(base + timedelta(minutes=10), 99.6, open_price=99.8, high_price=99.9, low_price=99.5, volume=10),
        _make_candle(base + timedelta(minutes=15), 99.9, open_price=99.6, high_price=100.0, low_price=99.6, volume=10),
        _make_candle(base + timedelta(minutes=20), 101.2, open_price=99.9, high_price=101.4, low_price=99.8, volume=15),
        _make_candle(base + timedelta(minutes=25), 100.6, open_price=101.0, high_price=101.1, low_price=100.4, volume=15),
    ]


def _as_test_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _make_bot_create_payload(name: str = "Test Bot") -> BotConfigCreateIn:
    return BotConfigCreateIn(
        name=name,
        account_id=9001,
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


def _make_analysis_config() -> BotConfig:
    return BotConfig(
        user_id="00000000-0000-0000-0000-000000000000",
        account_id=9001,
        name="Analysis Bot",
        enabled=True,
        execution_mode="dry_run",
        strategy_type="sma_cross",
        strategy_params={},
        contract_id="CON.F.US.MNQ.M26",
        symbol="MNQ",
        timeframe_unit="minute",
        timeframe_unit_number=5,
        lookback_bars=50,
        fast_period=5,
        slow_period=13,
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


def _make_analysis_signal(action: str = "HOLD", price: float | None = None) -> SignalResult:
    return SignalResult(
        action=action,
        reason="test signal",
        candle_timestamp=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
        price=price,
        raw_payload={},
    )


def test_bot_market_analysis_leans_bullish_for_rising_candles():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    candles = [
        _make_candle(
            base + timedelta(minutes=index * 5),
            100 + index,
            open_price=99.6 + index,
            high_price=100.6 + index,
            low_price=99.3 + index,
            volume=180 if index == 29 else 100,
        )
        for index in range(30)
    ]

    analysis = build_bot_market_analysis(
        candles=candles,
        config=_make_analysis_config(),
        signal=_make_analysis_signal("BUY", price=float(candles[-1].close_price)),
    )

    assert analysis["trend"] == "bullish"
    assert analysis["trend_strength"] > 50
    assert analysis["bullish_probability"] > analysis["bearish_probability"]
    assert analysis["bullish_probability"] + analysis["bearish_probability"] + analysis["sideways_probability"] == 100
    assert analysis["nearest_support"] is not None
    assert analysis["invalidation_level"] is not None
    assert "not financial advice" in analysis["summary"]


def test_bot_market_analysis_leans_bearish_for_falling_candles():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    candles = [
        _make_candle(
            base + timedelta(minutes=index * 5),
            130 - index,
            open_price=130.4 - index,
            high_price=130.7 - index,
            low_price=129.4 - index,
            volume=180 if index == 29 else 100,
        )
        for index in range(30)
    ]

    analysis = build_bot_market_analysis(
        candles=candles,
        config=_make_analysis_config(),
        signal=_make_analysis_signal("SELL", price=float(candles[-1].close_price)),
    )

    assert analysis["trend"] == "bearish"
    assert analysis["trend_strength"] > 50
    assert analysis["bearish_probability"] > analysis["bullish_probability"]
    assert analysis["bullish_probability"] + analysis["bearish_probability"] + analysis["sideways_probability"] == 100
    assert analysis["nearest_resistance"] is not None
    assert analysis["invalidation_level"] is not None


def test_bot_market_analysis_prefers_sideways_for_flat_candles():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    closes = [100.0, 100.1, 99.9, 100.05, 99.95] * 6
    candles = [
        _make_candle(
            base + timedelta(minutes=index * 5),
            close,
            open_price=close,
            high_price=close + 0.35,
            low_price=close - 0.35,
            volume=100,
        )
        for index, close in enumerate(closes)
    ]

    analysis = build_bot_market_analysis(
        candles=candles,
        config=_make_analysis_config(),
        signal=_make_analysis_signal("HOLD", price=float(candles[-1].close_price)),
    )

    assert analysis["trend"] == "neutral"
    assert analysis["sideways_probability"] > analysis["bullish_probability"]
    assert analysis["sideways_probability"] > analysis["bearish_probability"]
    assert analysis["bullish_probability"] + analysis["bearish_probability"] + analysis["sideways_probability"] == 100


def test_bot_market_analysis_handles_insufficient_candles_with_neutral_probabilities():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    candles = [
        _make_candle(base + timedelta(minutes=index * 5), 100 + index)
        for index in range(3)
    ]

    analysis = build_bot_market_analysis(
        candles=candles,
        config=_make_analysis_config(),
        signal=_make_analysis_signal("HOLD", price=float(candles[-1].close_price)),
    )

    assert analysis["trend"] == "neutral"
    assert analysis["trend_strength"] == 0
    assert analysis["bullish_probability"] == 33
    assert analysis["bearish_probability"] == 33
    assert analysis["sideways_probability"] == 34
    assert analysis["expected_move"] is None
    assert any("at least 10" in note for note in analysis["risk_notes"])


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


def test_opening_rvol_breakout_generates_buy_signal_on_green_opening_candle():
    candles = [
        _make_candle(_regular_session_bar(1, 13, 25), 99.8, open_price=99.8, high_price=100.0, low_price=99.6, volume=80),
        _make_candle(_regular_session_bar(1, 13, 30), 100.2, open_price=99.9, high_price=100.4, low_price=99.7, volume=100),
        _make_candle(_regular_session_bar(2, 13, 25), 100.3, open_price=100.2, high_price=100.5, low_price=100.1, volume=85),
        _make_candle(_regular_session_bar(2, 13, 30), 100.8, open_price=100.3, high_price=101.0, low_price=100.2, volume=110),
        _make_candle(_regular_session_bar(3, 13, 25), 100.6, open_price=100.7, high_price=100.9, low_price=100.4, volume=90),
        _make_candle(_regular_session_bar(3, 13, 30), 100.4, open_price=100.7, high_price=100.9, low_price=100.2, volume=90),
        _make_candle(_regular_session_bar(4, 13, 25), 100.5, open_price=100.4, high_price=100.7, low_price=100.3, volume=95),
        _make_candle(_regular_session_bar(4, 13, 30), 102.2, open_price=100.4, high_price=102.4, low_price=100.2, volume=350),
    ]

    signal = evaluate_opening_rvol_breakout(
        candles,
        session_start_time="09:30",
        strategy_params={
            "relative_volume_lookback_days": 3,
            "min_relative_volume": 2,
            "min_opening_volume": 100,
            "min_body_to_range_ratio": 0.5,
            "atr_period": 3,
            "atr_stop_multiple": 1,
            "take_profit_r_multiple": 2,
        },
    )

    assert signal.action == "BUY"
    assert "RVOL breakout" in signal.reason
    assert signal.raw_payload["relative_volume"] == pytest.approx(3.5)
    assert signal.raw_payload["stop_loss"] < signal.price
    assert signal.raw_payload["take_profit"] > signal.price


def test_opening_rvol_breakout_holds_when_relative_volume_is_below_threshold():
    candles = [
        _make_candle(_regular_session_bar(1, 13, 25), 99.8, open_price=99.8, high_price=100.0, low_price=99.6, volume=80),
        _make_candle(_regular_session_bar(1, 13, 30), 100.2, open_price=99.9, high_price=100.4, low_price=99.7, volume=100),
        _make_candle(_regular_session_bar(2, 13, 25), 100.3, open_price=100.2, high_price=100.5, low_price=100.1, volume=85),
        _make_candle(_regular_session_bar(2, 13, 30), 100.8, open_price=100.3, high_price=101.0, low_price=100.2, volume=110),
        _make_candle(_regular_session_bar(3, 13, 25), 100.6, open_price=100.7, high_price=100.9, low_price=100.4, volume=90),
        _make_candle(_regular_session_bar(3, 13, 30), 100.4, open_price=100.7, high_price=100.9, low_price=100.2, volume=90),
        _make_candle(_regular_session_bar(4, 13, 25), 100.5, open_price=100.4, high_price=100.7, low_price=100.3, volume=95),
        _make_candle(_regular_session_bar(4, 13, 30), 101.4, open_price=100.4, high_price=101.5, low_price=100.3, volume=120),
    ]

    signal = evaluate_opening_rvol_breakout(
        candles,
        session_start_time="09:30",
        strategy_params={
            "relative_volume_lookback_days": 3,
            "min_relative_volume": 2,
            "min_opening_volume": 100,
            "min_body_to_range_ratio": 0.5,
            "atr_period": 3,
            "atr_stop_multiple": 1,
            "take_profit_r_multiple": 2,
        },
    )

    assert signal.action == "HOLD"
    assert "below the minimum" in signal.reason
    assert signal.raw_payload["relative_volume"] == pytest.approx(1.2)


def test_opening_rvol_breakout_fetches_5_minute_history():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    class StubClient:
        def __init__(self):
            self.calls = []

        def retrieve_bars(self, **kwargs):
            self.calls.append(kwargs)
            return []

    client = StubClient()
    config = BotConfig(
        user_id="00000000-0000-0000-0000-000000000000",
        account_id=9001,
        name="Opening RVOL",
        enabled=False,
        execution_mode="dry_run",
        strategy_type="opening_rvol_breakout",
        strategy_params={},
        contract_id="CON.F.US.MNQ.M26",
        symbol="MNQ",
        timeframe_unit="minute",
        timeframe_unit_number=15,
        lookback_bars=25,
        fast_period=9,
        slow_period=21,
        order_size=1,
        max_contracts=1,
        max_daily_loss=250,
        max_trades_per_day=1,
        max_open_position=1,
        allowed_contracts=["CON.F.US.MNQ.M26"],
        trading_start_time="09:30",
        trading_end_time="15:45",
        cooldown_seconds=0,
        max_data_staleness_seconds=600,
    )

    try:
        rows = fetch_and_store_opening_rvol_breakout_candles(
            db,
            user_id="00000000-0000-0000-0000-000000000000",
            config=config,
            client=client,
            strategy_params={"relative_volume_lookback_days": 20, "atr_period": 14},
        )

        assert rows == []
        assert len(client.calls) == 1
        assert client.calls[0]["unit"] == 2
        assert client.calls[0]["unit_number"] == 5
        assert client.calls[0]["limit"] >= 500
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_atr_adjusted_relative_strength_generates_buy_signal():
    base = datetime(2026, 4, 1, 13, 30, tzinfo=timezone.utc)
    asset_candles = [
        _make_candle(base + timedelta(minutes=0), 100, open_price=99.5, high_price=101, low_price=99, volume=100, symbol="AAPL"),
        _make_candle(base + timedelta(minutes=5), 101, open_price=100, high_price=102, low_price=100, volume=110, symbol="AAPL"),
        _make_candle(base + timedelta(minutes=10), 102, open_price=101, high_price=103, low_price=101, volume=120, symbol="AAPL"),
        _make_candle(base + timedelta(minutes=15), 105, open_price=102, high_price=106, low_price=102, volume=130, symbol="AAPL"),
        _make_candle(base + timedelta(minutes=20), 108, open_price=105, high_price=109, low_price=105, volume=360, symbol="AAPL"),
    ]
    benchmark_candles = [
        _make_candle(base + timedelta(minutes=0), 100, open_price=99.8, high_price=101, low_price=99.4, volume=100, symbol="SPY", contract_id="SPY"),
        _make_candle(base + timedelta(minutes=5), 100.5, open_price=100.1, high_price=101, low_price=100, volume=100, symbol="SPY", contract_id="SPY"),
        _make_candle(base + timedelta(minutes=10), 101, open_price=100.5, high_price=101.5, low_price=100.5, volume=100, symbol="SPY", contract_id="SPY"),
        _make_candle(base + timedelta(minutes=15), 101.5, open_price=101, high_price=102, low_price=101, volume=100, symbol="SPY", contract_id="SPY"),
        _make_candle(base + timedelta(minutes=20), 102, open_price=101.5, high_price=102.5, low_price=101.5, volume=100, symbol="SPY", contract_id="SPY"),
    ]

    signal = evaluate_atr_adjusted_relative_strength(
        asset_candles,
        benchmark_candles=benchmark_candles,
        session_start_time="09:30",
        strategy_params={
            "benchmark_symbol": "SPY",
            "move_lookback_bars": 2,
            "atr_period": 2,
            "relative_volume_period": 2,
            "relative_volume_cap": 3,
            "long_score_threshold": 1,
            "short_score_threshold": -1,
            "ema_period": 3,
            "stop_structure_window": 3,
            "stop_atr_multiple": 0.1,
            "take_profit_r_multiple": 2,
        },
    )

    assert signal.action == "BUY"
    assert "ATR-adjusted relative strength" in signal.reason
    assert signal.raw_payload["final_score"] > 1
    assert signal.raw_payload["stop_loss"] < signal.price
    assert signal.raw_payload["take_profit"] > signal.price


def test_atr_adjusted_relative_strength_generates_sell_signal():
    base = datetime(2026, 4, 1, 13, 30, tzinfo=timezone.utc)
    asset_candles = [
        _make_candle(base + timedelta(minutes=0), 110, open_price=110.5, high_price=111, low_price=109.5, volume=120, symbol="AAPL"),
        _make_candle(base + timedelta(minutes=5), 109, open_price=109.5, high_price=110, low_price=108.5, volume=120, symbol="AAPL"),
        _make_candle(base + timedelta(minutes=10), 108, open_price=108.5, high_price=109, low_price=107.5, volume=120, symbol="AAPL"),
        _make_candle(base + timedelta(minutes=15), 104, open_price=107, high_price=107.5, low_price=103.5, volume=130, symbol="AAPL"),
        _make_candle(base + timedelta(minutes=20), 100, open_price=103, high_price=103.5, low_price=99.5, volume=360, symbol="AAPL"),
    ]
    benchmark_candles = [
        _make_candle(base + timedelta(minutes=0), 100, open_price=99.8, high_price=100.8, low_price=99.5, volume=100, symbol="SPY", contract_id="SPY"),
        _make_candle(base + timedelta(minutes=5), 100.5, open_price=100.1, high_price=101, low_price=100, volume=100, symbol="SPY", contract_id="SPY"),
        _make_candle(base + timedelta(minutes=10), 101, open_price=100.6, high_price=101.4, low_price=100.6, volume=100, symbol="SPY", contract_id="SPY"),
        _make_candle(base + timedelta(minutes=15), 101.5, open_price=101.1, high_price=101.9, low_price=101, volume=100, symbol="SPY", contract_id="SPY"),
        _make_candle(base + timedelta(minutes=20), 102, open_price=101.6, high_price=102.4, low_price=101.5, volume=100, symbol="SPY", contract_id="SPY"),
    ]

    signal = evaluate_atr_adjusted_relative_strength(
        asset_candles,
        benchmark_candles=benchmark_candles,
        session_start_time="09:30",
        strategy_params={
            "benchmark_symbol": "SPY",
            "move_lookback_bars": 2,
            "atr_period": 2,
            "relative_volume_period": 2,
            "relative_volume_cap": 3,
            "long_score_threshold": 1,
            "short_score_threshold": -1,
            "ema_period": 3,
            "stop_structure_window": 3,
            "stop_atr_multiple": 0.1,
            "take_profit_r_multiple": 2,
        },
    )

    assert signal.action == "SELL"
    assert "ATR-adjusted relative strength" in signal.reason
    assert signal.raw_payload["final_score"] < -1
    assert signal.raw_payload["stop_loss"] > signal.price
    assert signal.raw_payload["take_profit"] < signal.price


def test_atr_adjusted_relative_strength_holds_when_score_stays_inside_thresholds():
    base = datetime(2026, 4, 1, 13, 30, tzinfo=timezone.utc)
    asset_candles = [
        _make_candle(base + timedelta(minutes=0), 100, open_price=99.8, high_price=100.8, low_price=99.5, volume=100, symbol="AAPL"),
        _make_candle(base + timedelta(minutes=5), 100.4, open_price=100.1, high_price=100.9, low_price=100, volume=105, symbol="AAPL"),
        _make_candle(base + timedelta(minutes=10), 100.8, open_price=100.5, high_price=101.2, low_price=100.4, volume=110, symbol="AAPL"),
        _make_candle(base + timedelta(minutes=15), 101.1, open_price=100.9, high_price=101.4, low_price=100.8, volume=112, symbol="AAPL"),
        _make_candle(base + timedelta(minutes=20), 101.3, open_price=101.1, high_price=101.6, low_price=101.0, volume=118, symbol="AAPL"),
    ]
    benchmark_candles = [
        _make_candle(base + timedelta(minutes=0), 100, open_price=99.9, high_price=100.7, low_price=99.6, volume=100, symbol="SPY", contract_id="SPY"),
        _make_candle(base + timedelta(minutes=5), 100.2, open_price=100.0, high_price=100.8, low_price=99.9, volume=100, symbol="SPY", contract_id="SPY"),
        _make_candle(base + timedelta(minutes=10), 100.5, open_price=100.3, high_price=101.0, low_price=100.2, volume=100, symbol="SPY", contract_id="SPY"),
        _make_candle(base + timedelta(minutes=15), 100.8, open_price=100.6, high_price=101.2, low_price=100.5, volume=100, symbol="SPY", contract_id="SPY"),
        _make_candle(base + timedelta(minutes=20), 101.0, open_price=100.8, high_price=101.4, low_price=100.7, volume=100, symbol="SPY", contract_id="SPY"),
    ]

    signal = evaluate_atr_adjusted_relative_strength(
        asset_candles,
        benchmark_candles=benchmark_candles,
        session_start_time="09:30",
        strategy_params={
            "benchmark_symbol": "SPY",
            "move_lookback_bars": 2,
            "atr_period": 2,
            "relative_volume_period": 2,
            "relative_volume_cap": 3,
            "long_score_threshold": 2,
            "short_score_threshold": -2,
            "ema_period": 3,
            "stop_structure_window": 3,
            "stop_atr_multiple": 0.1,
            "take_profit_r_multiple": 2,
        },
    )

    assert signal.action == "HOLD"
    assert "remained between" in signal.reason
    assert -2 < signal.raw_payload["final_score"] < 2


def test_pullback_trap_reversal_generates_buy_signal_on_bullish_reclaim():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    candles = [
        _make_minute_candle(base + timedelta(minutes=0), open_price=99.8, high=100.3, low=99.7, close=100.0, volume=90),
        _make_minute_candle(base + timedelta(minutes=5), open_price=100.6, high=102.3, low=100.4, close=102.0, volume=95),
        _make_minute_candle(base + timedelta(minutes=10), open_price=102.2, high=104.3, low=102.0, close=104.0, volume=100),
        _make_minute_candle(base + timedelta(minutes=15), open_price=104.1, high=106.4, low=103.9, close=106.0, volume=105),
        _make_minute_candle(base + timedelta(minutes=20), open_price=106.0, high=106.2, low=104.8, close=105.0, volume=100),
        _make_minute_candle(base + timedelta(minutes=25), open_price=105.0, high=105.1, low=103.6, close=104.0, volume=110),
        _make_minute_candle(base + timedelta(minutes=30), open_price=104.0, high=104.2, low=102.9, close=103.5, volume=120),
        _make_minute_candle(base + timedelta(minutes=35), open_price=104.9, high=105.4, low=102.3, close=105.2, volume=260),
    ]

    signal = evaluate_pullback_trap_reversal(
        candles,
        fast_period=2,
        slow_period=4,
        strategy_params={
            "pullback_lookback_bars": 3,
            "micro_level_window": 2,
            "volume_baseline_bars": 4,
            "volume_spike_multiple": 1.5,
            "wick_to_body_ratio_min": 1.5,
            "stop_buffer_percent": 0,
            "take_profit_r_multiple": 2,
            "trend_confirmation_bars": 2,
            "min_countertrend_bars": 2,
            "pullback_range_multiplier": 1.2,
            "prior_swing_window": 4,
        },
    )

    assert signal.action == "BUY"
    assert "uptrend pullback trap" in signal.reason
    assert signal.raw_payload["micro_level"] == pytest.approx(105.0)
    assert signal.raw_payload["stop_loss"] == pytest.approx(102.3)
    assert signal.raw_payload["take_profit"] == pytest.approx(111.0)


def test_pullback_trap_reversal_generates_sell_signal_on_bearish_failure():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    candles = [
        _make_minute_candle(base + timedelta(minutes=0), open_price=110.2, high=110.4, low=109.8, close=110.0, volume=90),
        _make_minute_candle(base + timedelta(minutes=5), open_price=109.2, high=109.4, low=107.8, close=108.0, volume=95),
        _make_minute_candle(base + timedelta(minutes=10), open_price=107.2, high=107.4, low=105.8, close=106.0, volume=100),
        _make_minute_candle(base + timedelta(minutes=15), open_price=105.2, high=105.4, low=103.8, close=104.0, volume=105),
        _make_minute_candle(base + timedelta(minutes=20), open_price=104.0, high=105.2, low=103.7, close=105.0, volume=100),
        _make_minute_candle(base + timedelta(minutes=25), open_price=105.0, high=106.2, low=104.8, close=106.0, volume=110),
        _make_minute_candle(base + timedelta(minutes=30), open_price=106.0, high=107.1, low=105.9, close=106.5, volume=120),
        _make_minute_candle(base + timedelta(minutes=35), open_price=105.1, high=107.5, low=104.6, close=104.8, volume=260),
    ]

    signal = evaluate_pullback_trap_reversal(
        candles,
        fast_period=2,
        slow_period=4,
        strategy_params={
            "pullback_lookback_bars": 3,
            "micro_level_window": 2,
            "volume_baseline_bars": 4,
            "volume_spike_multiple": 1.5,
            "wick_to_body_ratio_min": 1.5,
            "stop_buffer_percent": 0,
            "take_profit_r_multiple": 2,
            "trend_confirmation_bars": 2,
            "min_countertrend_bars": 2,
            "pullback_range_multiplier": 1.2,
            "prior_swing_window": 4,
        },
    )

    assert signal.action == "SELL"
    assert "downtrend pullback trap" in signal.reason
    assert signal.raw_payload["micro_level"] == pytest.approx(105.0)
    assert signal.raw_payload["stop_loss"] == pytest.approx(107.5)
    assert signal.raw_payload["take_profit"] == pytest.approx(99.4)


def test_bollinger_mean_reversion_generates_buy_signal_on_fresh_lower_band_break():
    base = datetime(2026, 4, 1, 13, 0, tzinfo=timezone.utc)
    candles = _make_bollinger_base_candles(base, low_close=98.0, high_close=102.0)
    candles.append(
        _make_candle(
            base + timedelta(minutes=60 * 5),
            90.0,
            open_price=100.0,
            high_price=90.4,
            low_price=86.4,
            volume=25,
        )
    )

    signal = evaluate_bollinger_mean_reversion(
        candles,
        strategy_params={
            "bollinger_period": 60,
            "bollinger_stddev": 4.0,
            "atr_period": 14,
            "atr_stop_buffer": 0.5,
            "take_profit_mode": "middle_band",
            "news_blackout_windows": [],
        },
    )

    assert signal.action == "BUY"
    assert "fresh 60-bar 4.00 sigma Bollinger break" in signal.reason
    assert signal.raw_payload["take_profit_mode"] == "middle_band"
    assert signal.raw_payload["stop_loss"] < signal.price
    assert signal.raw_payload["take_profit"] > signal.price


def test_bollinger_mean_reversion_generates_sell_signal_with_fixed_r_target():
    base = datetime(2026, 4, 1, 13, 0, tzinfo=timezone.utc)
    candles = _make_bollinger_base_candles(base, low_close=98.0, high_close=102.0)
    candles.append(
        _make_candle(
            base + timedelta(minutes=60 * 5),
            110.0,
            open_price=100.0,
            high_price=113.6,
            low_price=109.6,
            volume=25,
        )
    )

    signal = evaluate_bollinger_mean_reversion(
        candles,
        strategy_params={
            "bollinger_period": 60,
            "bollinger_stddev": 4.0,
            "atr_period": 14,
            "atr_stop_buffer": 0.5,
            "take_profit_mode": "fixed_r",
            "take_profit_r_multiple": 2.0,
            "news_blackout_windows": [],
        },
    )

    assert signal.action == "SELL"
    assert signal.raw_payload["take_profit_mode"] == "fixed_r"
    assert signal.raw_payload["stop_loss"] > signal.price
    assert signal.raw_payload["take_profit"] < signal.price


def test_bollinger_mean_reversion_holds_inside_news_blackout_window():
    base = datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc)
    candles = _make_bollinger_base_candles(base, low_close=98.0, high_close=102.0)
    candles.append(
        _make_candle(
            datetime(2026, 4, 1, 14, 0, tzinfo=timezone.utc),
            90.0,
            open_price=100.0,
            high_price=90.4,
            low_price=86.4,
            volume=25,
        )
    )

    signal = evaluate_bollinger_mean_reversion(
        candles,
        strategy_params={
            "bollinger_period": 60,
            "bollinger_stddev": 4.0,
            "atr_period": 14,
            "atr_stop_buffer": 0.5,
            "take_profit_mode": "middle_band",
            "news_blackout_windows": ["09:55-10:05"],
        },
    )

    assert signal.action == "HOLD"
    assert "news blackout window" in signal.reason


def test_bollinger_rsi_reversal_generates_buy_signal_after_green_confirmation():
    signal = evaluate_bollinger_rsi_reversal(
        _make_bollinger_rsi_long_candles(),
        strategy_params={
            "rsi_period": 3,
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "bollinger_period": 5,
            "bollinger_stddev": 1,
            "adx_period": 3,
            "adx_max": 100,
            "swing_stop_lookback_bars": 4,
            "stop_buffer_percent": 0.1,
            "take_profit_mode": "middle_band",
            "take_profit_r_multiple": 2.0,
        },
    )

    assert signal.action == "BUY"
    assert "next candle closing green" in signal.reason
    assert signal.raw_payload["take_profit_mode"] == "middle_band"
    assert signal.raw_payload["stop_loss"] < signal.price
    assert signal.raw_payload["take_profit"] > signal.price


def test_bollinger_rsi_reversal_generates_sell_signal_with_vwap_target():
    signal = evaluate_bollinger_rsi_reversal(
        _make_bollinger_rsi_short_candles(),
        strategy_params={
            "rsi_period": 3,
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "bollinger_period": 5,
            "bollinger_stddev": 1,
            "adx_period": 3,
            "adx_max": 100,
            "swing_stop_lookback_bars": 4,
            "stop_buffer_percent": 0.1,
            "take_profit_mode": "vwap",
            "take_profit_r_multiple": 2.0,
        },
    )

    assert signal.action == "SELL"
    assert signal.raw_payload["take_profit_mode"] == "vwap"
    assert signal.raw_payload["stop_loss"] > signal.price
    assert signal.raw_payload["take_profit"] < signal.price


def test_bollinger_rsi_reversal_holds_when_adx_is_above_range_threshold():
    signal = evaluate_bollinger_rsi_reversal(
        _make_bollinger_rsi_long_candles(),
        strategy_params={
            "rsi_period": 3,
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "bollinger_period": 5,
            "bollinger_stddev": 1,
            "adx_period": 3,
            "adx_max": 1,
            "swing_stop_lookback_bars": 4,
            "stop_buffer_percent": 0.1,
            "take_profit_mode": "middle_band",
            "take_profit_r_multiple": 2.0,
        },
    )

    assert signal.action == "HOLD"
    assert "range threshold" in signal.reason


def test_vwap_atr_mean_reversion_generates_buy_signal_with_r_multiple_target():
    base = datetime(2026, 4, 1, 14, 0, tzinfo=timezone.utc)
    closes = [100.1 if index % 2 == 0 else 99.9 for index in range(24)] + [99.8, 99.4, 99.0, 98.6, 98.2, 97.8, 97.4, 97.0]
    candles = _make_price_sequence(base, closes, high_offset=0.2, low_offset=0.2)

    signal = evaluate_vwap_atr_mean_reversion(
        candles,
        strategy_params={
            "stretch_atr_multiple": 1.0,
            "adx_max": 100,
            "take_profit_mode": "r_multiple",
            "take_profit_r_multiple": 1.5,
        },
    )

    assert signal.action == "BUY"
    assert signal.raw_payload["range_filter"]["passed"] is True
    assert signal.raw_payload["setup"]["long"] is True
    assert signal.raw_payload["stop_loss"] < signal.price
    assert signal.raw_payload["take_profit"] > signal.price
    assert signal.raw_payload["reward_r"] == pytest.approx(1.5)


def test_vwap_atr_mean_reversion_generates_sell_signal_with_half_vwap_target():
    base = datetime(2026, 4, 1, 14, 0, tzinfo=timezone.utc)
    closes = [100.1 if index % 2 == 0 else 99.9 for index in range(24)] + [100.2, 100.6, 101.0, 101.4, 101.8, 102.2, 102.6, 103.0]
    candles = _make_price_sequence(base, closes, high_offset=0.2, low_offset=0.2)

    signal = evaluate_vwap_atr_mean_reversion(
        candles,
        strategy_params={
            "stretch_atr_multiple": 1.0,
            "adx_max": 100,
            "take_profit_mode": "half_vwap_distance",
        },
    )

    assert signal.action == "SELL"
    assert signal.raw_payload["setup"]["short"] is True
    assert signal.raw_payload["stop_loss"] > signal.price
    assert signal.raw_payload["take_profit"] < signal.price
    assert signal.raw_payload["take_profit"] == pytest.approx(
        signal.price + (signal.raw_payload["session_vwap"] - signal.price) * 0.5
    )


def test_vwap_atr_mean_reversion_blocks_trade_when_range_filter_fails():
    base = datetime(2026, 4, 1, 14, 0, tzinfo=timezone.utc)
    closes = [110 - index * 0.45 for index in range(32)]
    candles = _make_price_sequence(base, closes, high_offset=0.25, low_offset=0.25)

    signal = evaluate_vwap_atr_mean_reversion(
        candles,
        strategy_params={
            "stretch_atr_multiple": 0.75,
            "adx_max": 5,
            "flat_vwap_threshold_bps": 1,
        },
    )

    assert signal.action == "HOLD"
    assert signal.raw_payload["setup"]["long"] is True
    assert signal.raw_payload["range_filter"]["passed"] is False
    assert "ADX" in signal.reason


def test_ema_scalping_generates_buy_signal_on_bullish_marubozu():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    candles = _make_price_sequence(
        base,
        [100, 100, 100, 100, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110],
        high_offset=0.0,
        low_offset=0.0,
    )
    candles.append(
        _make_candle(
            base + timedelta(minutes=75),
            114,
            open_price=110,
            high_price=114,
            low_price=110,
            close_price=114,
        )
    )

    signal = evaluate_ema_scalping(candles, fast_period=9, slow_period=15)

    assert signal.action == "BUY"
    assert "bullish marubozu" in signal.reason
    assert signal.raw_payload["stop_loss"] == pytest.approx(110.0)
    assert signal.raw_payload["take_profit"] == pytest.approx(122.0)


def test_ema_scalping_generates_sell_signal_on_bearish_pin_bar():
    base = datetime(2026, 4, 2, 10, 0, tzinfo=timezone.utc)
    candles = _make_price_sequence(
        base,
        [110, 110, 109, 108, 107, 106, 105, 104, 103, 102, 101, 100, 99, 98, 97],
        high_offset=0.0,
        low_offset=0.0,
    )
    candles.append(
        _make_candle(
            base + timedelta(minutes=75),
            95,
            open_price=95.4,
            high_price=98.2,
            low_price=94.9,
            close_price=95.0,
        )
    )

    signal = evaluate_ema_scalping(candles, fast_period=9, slow_period=15)

    assert signal.action == "SELL"
    assert "bearish pin bar" in signal.reason
    assert signal.raw_payload["stop_loss"] == pytest.approx(98.2)
    assert signal.raw_payload["take_profit"] == pytest.approx(88.6)


def test_ema_scalping_holds_when_emas_are_flat():
    base = datetime(2026, 4, 3, 10, 0, tzinfo=timezone.utc)
    candles = _make_price_sequence(base, [100] * 15, high_offset=0.0, low_offset=0.0)
    candles.append(
        _make_candle(
            base + timedelta(minutes=75),
            100.1,
            open_price=100.0,
            high_price=100.1,
            low_price=100.0,
            close_price=100.1,
        )
    )

    signal = evaluate_ema_scalping(candles, fast_period=9, slow_period=15)

    assert signal.action == "HOLD"
    assert "too flat" in signal.reason


def test_fisher_transform_mean_reversion_generates_buy_signal_after_oversold_reversal():
    base = datetime(2026, 4, 1, 14, 0, tzinfo=timezone.utc)
    candles = _make_price_sequence(
        base,
        [
            99.76,
            99.84,
            99.92,
            100.0,
            100.08,
            100.16,
            99.76,
            99.84,
            99.92,
            100.0,
            100.08,
            100.16,
            99.76,
            99.84,
            99.92,
            100.0,
            100.08,
            100.16,
            99.76,
            99.84,
            99.92,
            100.0,
            100.08,
            100.16,
            99.76,
            99.84,
            99.92,
            100.0,
            100.08,
            100.16,
            99.76,
            99.84,
            99.92,
            100.0,
            100.08,
            100.16,
            99.76,
            99.84,
            99.92,
            100.0,
            100.08,
            100.16,
            99.76,
            99.84,
            99.92,
            99.6,
            99.1,
            98.6,
            98.0,
            97.5,
            97.1,
            96.8,
            96.6,
            96.9,
            97.3,
        ],
    )

    signal = evaluate_fisher_transform_mean_reversion(
        candles,
        fast_period=20,
        slow_period=50,
        strategy_params={"ema_slope_max_percent": 0.6},
    )

    assert signal.action == "BUY"
    assert "Fisher reversal" in signal.reason
    assert signal.raw_payload["stretch"]["below_vwap"] is True
    assert signal.raw_payload["stop_loss"] < signal.price
    assert signal.raw_payload["take_profit"] > signal.price


def test_fisher_transform_mean_reversion_generates_sell_signal_after_overbought_reversal():
    base = datetime(2026, 4, 1, 14, 0, tzinfo=timezone.utc)
    candles = _make_price_sequence(
        base,
        [
            99.76,
            99.84,
            99.92,
            100.0,
            100.08,
            100.16,
            99.76,
            99.84,
            99.92,
            100.0,
            100.08,
            100.16,
            99.76,
            99.84,
            99.92,
            100.0,
            100.08,
            100.16,
            99.76,
            99.84,
            99.92,
            100.0,
            100.08,
            100.16,
            99.76,
            99.84,
            99.92,
            100.0,
            100.08,
            100.16,
            99.76,
            99.84,
            99.92,
            100.0,
            100.08,
            100.16,
            99.76,
            99.84,
            99.92,
            100.0,
            100.08,
            100.16,
            99.76,
            99.84,
            99.92,
            100.3,
            100.7,
            101.0,
            101.4,
            101.8,
            102.1,
            102.4,
            102.6,
            102.4,
            102.1,
        ],
    )

    signal = evaluate_fisher_transform_mean_reversion(
        candles,
        fast_period=20,
        slow_period=50,
        strategy_params={"ema_slope_max_percent": 0.6},
    )

    assert signal.action == "SELL"
    assert "Fisher reversal" in signal.reason
    assert signal.raw_payload["stretch"]["above_vwap"] is True
    assert signal.raw_payload["stop_loss"] > signal.price
    assert signal.raw_payload["take_profit"] < signal.price


def test_ema_trend_pullback_generates_buy_signal_on_20_ema_pullback():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    closes = [
        100,
        100.1,
        100.41,
        100.35,
        100.21,
        100.33,
        99.93,
        99.59,
        99.99,
        99.75,
        100.15,
        100.58,
        100.59,
        100.9,
        101.34,
        101.86,
        101.58,
        101.33,
        101.32,
        101.4,
        101.29,
        100.87,
        101.05,
        101.68,
        101.65,
        101.81,
        101.8,
        101.86,
        102.39,
        102.3,
        102.58,
        102.68,
        102.84,
        103.42,
        103.07,
        103.55,
        103.45,
        103.73,
        104.18,
        104.47,
        104.47,
        104.96,
        104.63,
        104.9,
        104.9,
        105.05,
        105.56,
        106.01,
        106.27,
        105.76,
        105.41,
        105.52,
        105.32,
        105.01,
        104.37,
        103.96,
        103.65,
        104.4,
        104.4,
        104.66,
    ]
    candles = []
    for index, close in enumerate(closes[:-1]):
        previous_close = closes[index - 1] if index else close
        bullish = close >= previous_close
        open_price = close - 0.12 if bullish else close + 0.12
        low_price = min(open_price, close) - 0.18
        high_price = max(open_price, close) + 0.18
        candles.append(
            _make_candle(
                base + timedelta(minutes=index * 5),
                close,
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                volume=120 + (index % 4) * 10,
            )
        )
    candles.append(
        _make_candle(
            base + timedelta(minutes=(len(closes) - 1) * 5),
            closes[-1],
            open_price=104.46,
            high_price=104.81,
            low_price=104.41,
            volume=320,
        )
    )

    signal = evaluate_ema_trend_pullback(candles, fast_period=5, slow_period=13, strategy_params={})

    assert signal.action == "BUY"
    assert "20/50 EMA trend pullback" in signal.reason
    assert signal.raw_payload["fast_period"] == 20
    assert signal.raw_payload["slow_period"] == 50
    assert signal.raw_payload["stop_loss"] < signal.price
    assert signal.raw_payload["partial_take_profit"] > signal.price
    assert signal.raw_payload["take_profit"] == signal.raw_payload["final_take_profit"]
    assert signal.raw_payload["take_profit"] > signal.raw_payload["partial_take_profit"]
    assert signal.raw_payload["rsi"] == pytest.approx(54.567097644854435)


def test_ema_trend_pullback_generates_sell_signal_on_20_ema_pullback():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    closes = [
        160,
        159.92,
        159.94,
        160.25,
        159.8,
        160.04,
        159.38,
        159.63,
        159.25,
        158.61,
        158.83,
        159.23,
        159.28,
        159.47,
        159.24,
        158.84,
        158.9,
        159.12,
        158.75,
        158.49,
        158.12,
        157.74,
        158.02,
        157.36,
        156.71,
        156.24,
        156.43,
        156.39,
        156.55,
        156.65,
        156.96,
        156.94,
        156.29,
        155.73,
        155.51,
        154.97,
        154.64,
        154.27,
        154.68,
        155.07,
        155.14,
        154.93,
        154.71,
        154.12,
        153.95,
        153.44,
        153.36,
        153.59,
        153.36,
        153.93,
        154.38,
        154.5,
        154.35,
        154.94,
        155.42,
        155.0,
        154.58,
        155.22,
        154.42,
        154.56,
    ]
    candles = []
    for index, close in enumerate(closes[:-1]):
        previous_close = closes[index - 1] if index else close
        bullish = close >= previous_close
        open_price = close + 0.12 if not bullish else close - 0.12
        low_price = min(open_price, close) - 0.18
        high_price = max(open_price, close) + 0.18
        candles.append(
            _make_candle(
                base + timedelta(minutes=index * 5),
                close,
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                volume=120 + (index % 4) * 10,
            )
        )
    candles.append(
        _make_candle(
            base + timedelta(minutes=(len(closes) - 1) * 5),
            closes[-1],
            open_price=154.76,
            high_price=154.81,
            low_price=154.41,
            volume=320,
        )
    )

    signal = evaluate_ema_trend_pullback(candles, fast_period=5, slow_period=13, strategy_params={})

    assert signal.action == "SELL"
    assert "20/50 EMA trend pullback" in signal.reason
    assert signal.raw_payload["fast_period"] == 20
    assert signal.raw_payload["slow_period"] == 50
    assert signal.raw_payload["stop_loss"] > signal.price
    assert signal.raw_payload["partial_take_profit"] < signal.price
    assert signal.raw_payload["take_profit"] == signal.raw_payload["final_take_profit"]
    assert signal.raw_payload["take_profit"] < signal.raw_payload["partial_take_profit"]
    assert signal.raw_payload["rsi"] == pytest.approx(45.64029383872642)


def test_create_bot_config_for_ema_trend_pullback_forces_20_50_periods():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [Account.__table__, BotConfig.__table__]
    Base.metadata.create_all(bind=engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    try:
        db.add(
            Account(
                user_id=user_id,
                provider="projectx",
                external_id="9001",
                name="Practice 9001",
                account_state="ACTIVE",
                can_trade=True,
                is_visible=True,
            )
        )
        db.flush()

        payload = _make_bot_create_payload(name="EMA Pullback").model_copy(
            update={
                "strategy_type": "ema_trend_pullback",
                "fast_period": 3,
                "slow_period": 8,
                "strategy_params": {},
            }
        )
        row = create_bot_config(db, user_id=user_id, payload=payload)

        assert row.fast_period == 20
        assert row.slow_period == 50
        assert row.strategy_params == {
            "rsi_period": 14,
            "volume_average_period": 20,
            "swing_lookback_bars": 5,
            "long_rsi_min": 40.0,
            "long_rsi_max": 55.0,
            "short_rsi_min": 45.0,
            "short_rsi_max": 60.0,
            "partial_take_profit_r_multiple": 1.0,
            "final_take_profit_r_multiple": 2.0,
        }
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=tables)
        engine.dispose()


def test_support_resistance_generates_buy_signal_near_filtered_support():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    higher_timeframe = [
        _make_hour_candle(base + timedelta(hours=index * 4), close=120 + index, low=118 + index, high=122 + index, unit_number=4)
        for index in range(5)
    ]
    lower_timeframe = [
        _make_hour_candle(base + timedelta(hours=0), close=104, low=103, high=105, unit_number=1),
        _make_hour_candle(base + timedelta(hours=1), close=103, low=102, high=106, unit_number=1),
        _make_hour_candle(base + timedelta(hours=2), close=101, low=99, high=104, unit_number=1),
        _make_hour_candle(base + timedelta(hours=3), close=102, low=101, high=103, unit_number=1),
        _make_hour_candle(base + timedelta(hours=4), close=103, low=102, high=104, unit_number=1),
        _make_hour_candle(base + timedelta(hours=5), close=99.2, low=99.1, high=100, unit_number=1),
    ]

    signal = evaluate_support_resistance_levels(
        higher_timeframe_candles=higher_timeframe,
        lower_timeframe_candles=lower_timeframe,
        strategy_params={"level_tolerance_percent": 0.25},
    )

    assert signal.action == "BUY"
    assert "4H support" not in signal.reason
    assert "1H support 99" in signal.reason
    assert signal.raw_payload["stop_loss"] == pytest.approx(98.01)
    assert signal.raw_payload["take_profit"] == pytest.approx(101.58)


def test_support_resistance_generates_sell_signal_near_filtered_resistance():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    higher_timeframe = [
        _make_hour_candle(base + timedelta(hours=index * 4), close=90 + index, low=88 + index, high=92 + index, unit_number=4)
        for index in range(5)
    ]
    lower_timeframe = [
        _make_hour_candle(base + timedelta(hours=0), close=95, low=94, high=96, unit_number=1),
        _make_hour_candle(base + timedelta(hours=1), close=96, low=95, high=97, unit_number=1),
        _make_hour_candle(base + timedelta(hours=2), close=100, low=98, high=101, unit_number=1),
        _make_hour_candle(base + timedelta(hours=3), close=97, low=96, high=98, unit_number=1),
        _make_hour_candle(base + timedelta(hours=4), close=98, low=97, high=99, unit_number=1),
        _make_hour_candle(base + timedelta(hours=5), close=100.8, low=100, high=100.9, unit_number=1),
    ]

    signal = evaluate_support_resistance_levels(
        higher_timeframe_candles=higher_timeframe,
        lower_timeframe_candles=lower_timeframe,
        strategy_params={"level_tolerance_percent": 0.25},
    )

    assert signal.action == "SELL"
    assert "1H resistance 101" in signal.reason
    assert signal.raw_payload["stop_loss"] == pytest.approx(102.01)
    assert signal.raw_payload["take_profit"] == pytest.approx(98.38)


def test_supertrend_pivot_generates_buy_signal_after_daily_pivot_reclaim():
    base = datetime(2026, 4, 1, 14, 0, tzinfo=timezone.utc)
    signal_candles = [
        _make_candle(base + timedelta(minutes=index * 15), close, open_price=open_price, high_price=high, low_price=low, unit="minute", unit_number=15)
        for index, (open_price, high, low, close) in enumerate(
            [
                (96.5, 97.0, 95.5, 96.0),
                (95.5, 96.0, 94.5, 95.0),
                (95.5, 96.5, 95.0, 96.0),
                (96.5, 97.5, 96.0, 97.0),
                (97.5, 98.5, 97.0, 98.0),
                (98.5, 99.5, 98.0, 99.0),
                (99.5, 101.5, 99.4, 101.0),
            ]
        )
    ]
    daily_candles = [_make_day_candle(datetime(2026, 3, 31, 21, 0, tzinfo=timezone.utc), close=100, low=90, high=110)]

    signal = evaluate_supertrend_pivot_points(
        signal_timeframe_candles=signal_candles,
        daily_candles=daily_candles,
        strategy_params={
            "supertrend_period": 3,
            "supertrend_multiplier": 1.5,
            "pivot_tolerance_percent": 0.2,
            "stop_beyond_level_percent": 0.05,
            "take_profit_r_multiple": 2.0,
            "chop_lookback_bars": 4,
            "chop_max_flips": 3,
            "chop_max_range_percent": 5.0,
        },
    )

    assert signal.action == "BUY"
    assert "reclaiming daily P 100" in signal.reason
    assert signal.raw_payload["trigger_level"]["name"] == "P"
    assert signal.raw_payload["take_profit_source"] == "2R"
    assert signal.raw_payload["stop_loss"] < signal.price
    assert signal.raw_payload["take_profit"] > signal.price


def test_supertrend_pivot_generates_sell_signal_after_daily_pivot_rejection():
    base = datetime(2026, 4, 1, 14, 0, tzinfo=timezone.utc)
    signal_candles = [
        _make_candle(base + timedelta(minutes=index * 15), close, open_price=open_price, high_price=high, low_price=low, unit="minute", unit_number=15)
        for index, (open_price, high, low, close) in enumerate(
            [
                (104.5, 105.0, 103.5, 104.0),
                (103.5, 104.0, 102.5, 103.0),
                (102.5, 103.0, 101.5, 102.0),
                (101.5, 102.0, 100.5, 101.0),
                (100.8, 101.1, 99.8, 100.0),
                (100.4, 100.7, 99.2, 99.6),
                (100.6, 100.8, 98.8, 99.2),
            ]
        )
    ]
    daily_candles = [_make_day_candle(datetime(2026, 3, 31, 21, 0, tzinfo=timezone.utc), close=100, low=90, high=110)]

    signal = evaluate_supertrend_pivot_points(
        signal_timeframe_candles=signal_candles,
        daily_candles=daily_candles,
        strategy_params={
            "supertrend_period": 3,
            "supertrend_multiplier": 1.5,
            "pivot_tolerance_percent": 0.2,
            "stop_beyond_level_percent": 0.05,
            "take_profit_r_multiple": 2.0,
            "chop_lookback_bars": 4,
            "chop_max_flips": 3,
            "chop_max_range_percent": 5.0,
        },
    )

    assert signal.action == "SELL"
    assert "rejecting daily P 100" in signal.reason
    assert signal.raw_payload["trigger_level"]["name"] == "P"
    assert signal.raw_payload["take_profit_source"] == "2R"
    assert signal.raw_payload["stop_loss"] > signal.price
    assert signal.raw_payload["take_profit"] < signal.price


def test_supertrend_pivot_holds_when_supertrend_is_choppy_in_tight_range():
    base = datetime(2026, 4, 1, 14, 0, tzinfo=timezone.utc)
    signal_candles = [
        _make_candle(base + timedelta(minutes=index * 15), close, open_price=open_price, high_price=high, low_price=low, unit="minute", unit_number=15)
        for index, (open_price, high, low, close) in enumerate(
            [
                (100.0, 100.5, 99.5, 100.4),
                (100.4, 100.5, 99.5, 99.6),
                (99.6, 100.6, 99.4, 100.5),
                (100.5, 100.6, 99.4, 99.5),
                (99.5, 100.7, 99.3, 100.6),
                (100.6, 100.7, 99.3, 99.4),
                (99.4, 100.8, 99.2, 100.7),
            ]
        )
    ]
    daily_candles = [_make_day_candle(datetime(2026, 3, 31, 21, 0, tzinfo=timezone.utc), close=100, low=90, high=110)]

    signal = evaluate_supertrend_pivot_points(
        signal_timeframe_candles=signal_candles,
        daily_candles=daily_candles,
        strategy_params={
            "supertrend_period": 2,
            "supertrend_multiplier": 0.3,
            "pivot_tolerance_percent": 0.2,
            "stop_beyond_level_percent": 0.05,
            "take_profit_r_multiple": 2.0,
            "chop_lookback_bars": 6,
            "chop_max_flips": 2,
            "chop_max_range_percent": 2.0,
        },
    )

    assert signal.action == "HOLD"
    assert "Skipping trade" in signal.reason
    assert signal.raw_payload["chop"]["is_choppy"] is True


def test_liquidity_sweep_retest_generates_buy_signal_after_reclaim_and_retest():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    higher_timeframe = [
        _make_hour_candle(base + timedelta(hours=0), close=100, low=99, high=101, unit_number=4),
        _make_hour_candle(base + timedelta(hours=4), close=101, low=100, high=102, unit_number=4),
        _make_hour_candle(base + timedelta(hours=8), close=102, low=101, high=103, unit_number=4),
        _make_hour_candle(base + timedelta(hours=12), close=103, low=102, high=104, unit_number=4),
        _make_hour_candle(base + timedelta(hours=16), close=104, low=103, high=105, unit_number=4),
    ]
    lower_timeframe = [
        _make_hour_candle(base + timedelta(hours=0), close=103, low=102, high=104, unit_number=1),
        _make_hour_candle(base + timedelta(hours=1), close=101, low=100, high=103, unit_number=1),
        _make_hour_candle(base + timedelta(hours=2), close=102, low=99, high=103, unit_number=1),
        _make_hour_candle(base + timedelta(hours=3), close=103, low=101, high=104, unit_number=1),
        _make_hour_candle(base + timedelta(hours=4), close=104, low=102, high=105, unit_number=1),
        _make_hour_candle(base + timedelta(hours=5), close=98.8, low=98.5, high=101, unit_number=1),
        _make_hour_candle(base + timedelta(hours=6), close=99.4, low=99.1, high=100.2, unit_number=1),
        _make_hour_candle(base + timedelta(hours=7), close=99.6, low=99.0, high=100.4, unit_number=1),
    ]

    signal = evaluate_liquidity_sweep_retest(
        higher_timeframe_candles=higher_timeframe,
        lower_timeframe_candles=lower_timeframe,
        fast_period=2,
        slow_period=3,
        strategy_params={
            "level_tolerance_percent": 0.25,
            "reclaim_within_bars": 2,
            "retest_within_bars": 3,
            "stop_beyond_sweep_percent": 0.05,
            "take_profit_mode": "2r",
        },
    )

    assert signal.action == "BUY"
    assert "reclaimed 1H liquidity low 99" in signal.reason
    assert signal.raw_payload["higher_timeframe_bias"]["bias"] == "bullish"
    assert signal.raw_payload["stop_loss"] == pytest.approx(98.45075)
    assert signal.raw_payload["take_profit"] == pytest.approx(101.8985)
    assert signal.raw_payload["trigger_level"]["price"] == pytest.approx(99.0)


def test_liquidity_sweep_retest_uses_next_liquidity_pool_for_short_target():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    higher_timeframe = [
        _make_hour_candle(base + timedelta(hours=0), close=103, low=102, high=104, unit_number=4),
        _make_hour_candle(base + timedelta(hours=4), close=102, low=101, high=103, unit_number=4),
        _make_hour_candle(base + timedelta(hours=8), close=100, low=97, high=101, unit_number=4),
        _make_hour_candle(base + timedelta(hours=12), close=99, low=98, high=100, unit_number=4),
        _make_hour_candle(base + timedelta(hours=16), close=98, low=97.5, high=99, unit_number=4),
    ]
    lower_timeframe = [
        _make_hour_candle(base + timedelta(hours=0), close=95, low=94, high=96, unit_number=1),
        _make_hour_candle(base + timedelta(hours=1), close=96, low=95, high=97, unit_number=1),
        _make_hour_candle(base + timedelta(hours=2), close=100, low=98, high=101, unit_number=1),
        _make_hour_candle(base + timedelta(hours=3), close=97, low=96, high=98, unit_number=1),
        _make_hour_candle(base + timedelta(hours=4), close=98, low=97, high=99, unit_number=1),
        _make_hour_candle(base + timedelta(hours=5), close=101.8, low=100.5, high=102.4, unit_number=1),
        _make_hour_candle(base + timedelta(hours=6), close=100.8, low=100, high=101.0, unit_number=1),
        _make_hour_candle(base + timedelta(hours=7), close=100.4, low=99.8, high=101.0, unit_number=1),
    ]

    signal = evaluate_liquidity_sweep_retest(
        higher_timeframe_candles=higher_timeframe,
        lower_timeframe_candles=lower_timeframe,
        fast_period=2,
        slow_period=3,
        strategy_params={
            "level_tolerance_percent": 0.25,
            "reclaim_within_bars": 2,
            "retest_within_bars": 3,
            "stop_beyond_sweep_percent": 0.05,
            "take_profit_mode": "next_liquidity",
        },
    )

    assert signal.action == "SELL"
    assert "holding the retest from below" in signal.reason
    assert signal.raw_payload["higher_timeframe_bias"]["bias"] == "bearish"
    assert signal.raw_payload["take_profit"] == pytest.approx(97.0)
    assert signal.raw_payload["target_level"]["side"] == "support"
    assert signal.raw_payload["target_source"] == "next_4h_support"


def test_liquidity_sweep_retest_blocks_signal_when_higher_timeframe_bias_is_neutral():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    higher_timeframe = [
        _make_hour_candle(base + timedelta(hours=0), close=100, low=99, high=101, unit_number=4),
        _make_hour_candle(base + timedelta(hours=4), close=100, low=99, high=101, unit_number=4),
        _make_hour_candle(base + timedelta(hours=8), close=100, low=99, high=101, unit_number=4),
        _make_hour_candle(base + timedelta(hours=12), close=100, low=99, high=101, unit_number=4),
        _make_hour_candle(base + timedelta(hours=16), close=100, low=99, high=101, unit_number=4),
    ]
    lower_timeframe = [
        _make_hour_candle(base + timedelta(hours=0), close=103, low=102, high=104, unit_number=1),
        _make_hour_candle(base + timedelta(hours=1), close=101, low=100, high=103, unit_number=1),
        _make_hour_candle(base + timedelta(hours=2), close=102, low=99, high=103, unit_number=1),
        _make_hour_candle(base + timedelta(hours=3), close=103, low=101, high=104, unit_number=1),
        _make_hour_candle(base + timedelta(hours=4), close=104, low=102, high=105, unit_number=1),
        _make_hour_candle(base + timedelta(hours=5), close=98.8, low=98.5, high=101, unit_number=1),
        _make_hour_candle(base + timedelta(hours=6), close=99.4, low=99.1, high=100.2, unit_number=1),
        _make_hour_candle(base + timedelta(hours=7), close=99.6, low=99.0, high=100.4, unit_number=1),
    ]

    signal = evaluate_liquidity_sweep_retest(
        higher_timeframe_candles=higher_timeframe,
        lower_timeframe_candles=lower_timeframe,
        fast_period=2,
        slow_period=3,
        strategy_params={"level_tolerance_percent": 0.25},
    )

    assert signal.action == "HOLD"
    assert "4H bias is neutral" in signal.reason
    assert signal.raw_payload["higher_timeframe_bias"]["bias"] == "neutral"


def test_macd_support_resistance_generates_buy_signal_near_support_with_atr_trailing_stop():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    higher_timeframe = [
        _make_hour_candle(base + timedelta(hours=index * 4), close=120 + index, low=118 + index, high=122 + index, unit_number=4)
        for index in range(5)
    ]
    lower_timeframe = [
        _make_hour_candle(base + timedelta(hours=0), close=100.0, low=99.5, high=100.5, unit_number=1),
        _make_hour_candle(base + timedelta(hours=1), close=99.0, low=98.5, high=99.5, unit_number=1),
        _make_hour_candle(base + timedelta(hours=2), close=98.0, low=97.5, high=98.5, unit_number=1),
        _make_hour_candle(base + timedelta(hours=3), close=97.0, low=96.5, high=97.5, unit_number=1),
        _make_hour_candle(base + timedelta(hours=4), close=96.0, low=95.9, high=96.5, unit_number=1),
        _make_hour_candle(base + timedelta(hours=5), close=97.2, low=96.8, high=97.5, unit_number=1),
        _make_hour_candle(base + timedelta(hours=6), close=95.7, low=95.7, high=96.2, unit_number=1),
        _make_hour_candle(base + timedelta(hours=7), close=96.0, low=95.9, high=96.3, unit_number=1),
    ]

    signal = evaluate_macd_support_resistance(
        higher_timeframe_candles=higher_timeframe,
        lower_timeframe_candles=lower_timeframe,
        fast_period=2,
        slow_period=3,
        strategy_params={
            "bars_per_timeframe": 20,
            "swing_window": 3,
            "level_tolerance_percent": 0.35,
            "signal_period": 2,
            "atr_period": 3,
            "initial_stop_atr_multiplier": 1.5,
            "trailing_stop_mode": "atr",
            "trailing_atr_multiplier": 2.0,
        },
    )

    assert signal.action == "BUY"
    assert "signal-line crossover" in signal.reason
    assert signal.raw_payload["trigger_level"]["side"] == "support"
    assert signal.raw_payload["stop_loss"] < signal.price
    assert "take_profit" not in signal.raw_payload
    assert signal.raw_payload["trailing_stop"]["mode"] == "atr"


def test_macd_support_resistance_generates_sell_signal_near_resistance_with_moving_average_trailing_stop():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    higher_timeframe = [
        _make_hour_candle(base + timedelta(hours=index * 4), close=90 + index, low=88 + index, high=92 + index, unit_number=4)
        for index in range(5)
    ]
    lower_timeframe = [
        _make_hour_candle(base + timedelta(hours=0), close=96.0, low=95.5, high=96.4, unit_number=1),
        _make_hour_candle(base + timedelta(hours=1), close=97.0, low=96.5, high=97.4, unit_number=1),
        _make_hour_candle(base + timedelta(hours=2), close=98.0, low=97.5, high=98.4, unit_number=1),
        _make_hour_candle(base + timedelta(hours=3), close=99.0, low=98.5, high=99.4, unit_number=1),
        _make_hour_candle(base + timedelta(hours=4), close=100.0, low=99.5, high=100.0, unit_number=1),
        _make_hour_candle(base + timedelta(hours=5), close=98.8, low=98.4, high=99.1, unit_number=1),
        _make_hour_candle(base + timedelta(hours=6), close=100.2, low=99.9, high=100.2, unit_number=1),
        _make_hour_candle(base + timedelta(hours=7), close=99.8, low=99.6, high=100.0, unit_number=1),
    ]

    signal = evaluate_macd_support_resistance(
        higher_timeframe_candles=higher_timeframe,
        lower_timeframe_candles=lower_timeframe,
        fast_period=2,
        slow_period=3,
        strategy_params={
            "bars_per_timeframe": 20,
            "swing_window": 3,
            "level_tolerance_percent": 0.45,
            "signal_period": 2,
            "atr_period": 3,
            "initial_stop_atr_multiplier": 1.5,
            "trailing_stop_mode": "moving_average",
            "trailing_ma_period": 4,
        },
    )

    assert signal.action == "SELL"
    assert "signal-line crossover" in signal.reason
    assert signal.raw_payload["trigger_level"]["side"] == "resistance"
    assert signal.raw_payload["stop_loss"] > signal.price
    assert "take_profit" not in signal.raw_payload
    assert signal.raw_payload["trailing_stop"]["mode"] == "moving_average"
    assert signal.raw_payload["trailing_stop"]["reference_price"] is not None


def test_macd_support_resistance_holds_when_price_is_not_near_a_level():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    higher_timeframe = [
        _make_hour_candle(base + timedelta(hours=index * 4), close=120 + index, low=118 + index, high=122 + index, unit_number=4)
        for index in range(5)
    ]
    lower_timeframe = [
        _make_hour_candle(base + timedelta(hours=0), close=100.0, low=99.5, high=100.5, unit_number=1),
        _make_hour_candle(base + timedelta(hours=1), close=99.0, low=98.5, high=99.5, unit_number=1),
        _make_hour_candle(base + timedelta(hours=2), close=98.0, low=97.5, high=98.5, unit_number=1),
        _make_hour_candle(base + timedelta(hours=3), close=97.0, low=96.5, high=97.5, unit_number=1),
        _make_hour_candle(base + timedelta(hours=4), close=96.0, low=95.9, high=96.5, unit_number=1),
        _make_hour_candle(base + timedelta(hours=5), close=97.2, low=96.8, high=97.5, unit_number=1),
        _make_hour_candle(base + timedelta(hours=6), close=97.6, low=97.3, high=97.9, unit_number=1),
        _make_hour_candle(base + timedelta(hours=7), close=97.8, low=97.5, high=98.1, unit_number=1),
    ]

    signal = evaluate_macd_support_resistance(
        higher_timeframe_candles=higher_timeframe,
        lower_timeframe_candles=lower_timeframe,
        fast_period=2,
        slow_period=3,
        strategy_params={
            "bars_per_timeframe": 20,
            "swing_window": 3,
            "level_tolerance_percent": 0.15,
            "signal_period": 2,
            "atr_period": 3,
        },
    )

    assert signal.action == "HOLD"
    assert "not within" in signal.reason


def test_vwap_gap_retrace_generates_buy_signal_on_gap_up_vwap_rejection():
    previous_close = _make_minute_candle(
        datetime(2026, 4, 1, 19, 59, tzinfo=timezone.utc),
        open_price=100,
        high=100.2,
        low=99.8,
        close=100,
        volume=1_000,
    )
    current_session = [
        _make_minute_candle(datetime(2026, 4, 2, 13, 30, tzinfo=timezone.utc), open_price=103, high=104, low=102.8, close=103.8, volume=1_000),
        _make_minute_candle(datetime(2026, 4, 2, 13, 31, tzinfo=timezone.utc), open_price=103.8, high=104.4, low=103.6, close=104.2, volume=1_100),
        _make_minute_candle(datetime(2026, 4, 2, 13, 32, tzinfo=timezone.utc), open_price=104.2, high=104.5, low=103.9, close=104.0, volume=900),
        _make_minute_candle(datetime(2026, 4, 2, 13, 33, tzinfo=timezone.utc), open_price=104.0, high=104.1, low=103.6, close=103.8, volume=850),
        _make_minute_candle(datetime(2026, 4, 2, 13, 34, tzinfo=timezone.utc), open_price=103.8, high=103.9, low=103.2, close=103.4, volume=800),
        _make_minute_candle(datetime(2026, 4, 2, 13, 35, tzinfo=timezone.utc), open_price=103.4, high=104.0, low=103.6, close=103.95, volume=1_200),
    ]

    signal = evaluate_vwap_gap_retrace([previous_close, *current_session])

    assert signal.action == "BUY"
    assert "gap up" in signal.reason
    assert signal.raw_payload["gap_percent"] == pytest.approx(3.0)
    assert signal.raw_payload["targets"]["day_extreme"] == pytest.approx(104.5)
    assert signal.raw_payload["stop_loss"] == pytest.approx(103.726, rel=1e-3)


def test_vwap_gap_retrace_generates_sell_signal_on_gap_down_vwap_rejection():
    previous_close = _make_minute_candle(
        datetime(2026, 4, 1, 19, 59, tzinfo=timezone.utc),
        open_price=100,
        high=100.2,
        low=99.8,
        close=100,
        volume=1_000,
    )
    current_session = [
        _make_minute_candle(datetime(2026, 4, 2, 13, 30, tzinfo=timezone.utc), open_price=97, high=97.4, low=96.6, close=96.8, volume=1_000),
        _make_minute_candle(datetime(2026, 4, 2, 13, 31, tzinfo=timezone.utc), open_price=96.8, high=97.0, low=96.2, close=96.4, volume=1_100),
        _make_minute_candle(datetime(2026, 4, 2, 13, 32, tzinfo=timezone.utc), open_price=96.4, high=96.7, low=95.9, close=96.1, volume=900),
        _make_minute_candle(datetime(2026, 4, 2, 13, 33, tzinfo=timezone.utc), open_price=96.1, high=96.3, low=95.8, close=95.9, volume=850),
        _make_minute_candle(datetime(2026, 4, 2, 13, 34, tzinfo=timezone.utc), open_price=95.9, high=96.4, low=95.7, close=96.2, volume=800),
        _make_minute_candle(datetime(2026, 4, 2, 13, 35, tzinfo=timezone.utc), open_price=96.25, high=96.5, low=95.8, close=95.95, volume=1_200),
    ]

    signal = evaluate_vwap_gap_retrace([previous_close, *current_session])

    assert signal.action == "SELL"
    assert "gap down" in signal.reason
    assert signal.raw_payload["gap_percent"] == pytest.approx(-3.0)
    assert signal.raw_payload["targets"]["day_extreme"] == pytest.approx(95.7)
    assert signal.raw_payload["stop_loss"] == pytest.approx(96.423, rel=1e-3)


def test_vwap_gap_retrace_holds_outside_entry_window():
    previous_close = _make_minute_candle(
        datetime(2026, 4, 1, 19, 59, tzinfo=timezone.utc),
        open_price=100,
        high=100.2,
        low=99.8,
        close=100,
        volume=1_000,
    )
    current_session = [
        _make_minute_candle(datetime(2026, 4, 2, 13, 30, tzinfo=timezone.utc), open_price=103, high=104, low=102.8, close=103.8, volume=1_000),
        _make_minute_candle(datetime(2026, 4, 2, 13, 31, tzinfo=timezone.utc), open_price=103.8, high=104.4, low=103.6, close=104.2, volume=1_100),
        _make_minute_candle(datetime(2026, 4, 2, 13, 32, tzinfo=timezone.utc), open_price=104.2, high=104.5, low=103.9, close=104.0, volume=900),
        _make_minute_candle(datetime(2026, 4, 2, 13, 46, tzinfo=timezone.utc), open_price=103.9, high=104.1, low=103.7, close=104.0, volume=1_200),
    ]

    signal = evaluate_vwap_gap_retrace([previous_close, *current_session])

    assert signal.action == "HOLD"
    assert "entries are limited to 5-15 minutes after the open" in signal.reason


def test_vwap_gap_retrace_fetches_one_minute_candles_with_extended_limit():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    class StubClient:
        def __init__(self):
            self.calls = []

        def retrieve_bars(self, **kwargs):
            self.calls.append(kwargs)
            return []

    client = StubClient()
    config = BotConfig(
        user_id="00000000-0000-0000-0000-000000000000",
        account_id=9001,
        name="VWAP Gap",
        enabled=False,
        execution_mode="dry_run",
        strategy_type="vwap_gap_retrace",
        strategy_params={},
        contract_id="CON.F.US.MNQ.M26",
        symbol="MNQ",
        timeframe_unit="minute",
        timeframe_unit_number=5,
        lookback_bars=200,
        fast_period=9,
        slow_period=21,
        order_size=1,
        max_contracts=1,
        max_daily_loss=250,
        max_trades_per_day=3,
        max_open_position=1,
        allowed_contracts=["CON.F.US.MNQ.M26"],
        trading_start_time="09:30",
        trading_end_time="15:45",
        cooldown_seconds=0,
        max_data_staleness_seconds=180,
    )

    try:
        rows = fetch_and_store_vwap_gap_retrace_candles(
            db,
            user_id="00000000-0000-0000-0000-000000000000",
            config=config,
            client=client,
            strategy_params={},
        )

        assert rows == []
        assert [(call["unit"], call["unit_number"], call["limit"]) for call in client.calls] == [(2, 1, 2000)]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_support_resistance_fetches_4h_and_1h_candles_with_100_bar_limit():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    class StubClient:
        def __init__(self):
            self.calls = []

        def retrieve_bars(self, **kwargs):
            self.calls.append(kwargs)
            return []

    client = StubClient()
    config = BotConfig(
        user_id="00000000-0000-0000-0000-000000000000",
        account_id=9001,
        name="SR Bot",
        enabled=False,
        execution_mode="dry_run",
        strategy_type="support_resistance",
        strategy_params={"level_tolerance_percent": 0.25},
        contract_id="CON.F.US.MNQ.M26",
        symbol="MNQ",
        timeframe_unit="hour",
        timeframe_unit_number=1,
        lookback_bars=100,
        fast_period=9,
        slow_period=21,
        order_size=1,
        max_contracts=1,
        max_daily_loss=250,
        max_trades_per_day=3,
        max_open_position=1,
        allowed_contracts=["CON.F.US.MNQ.M26"],
        trading_start_time="00:00",
        trading_end_time="23:59",
        cooldown_seconds=0,
        max_data_staleness_seconds=7200,
    )

    try:
        rows = fetch_and_store_support_resistance_candles(
            db,
            user_id="00000000-0000-0000-0000-000000000000",
            config=config,
            client=client,
            strategy_params={"level_tolerance_percent": 0.25},
        )

        assert rows == {"4H": [], "1H": []}
        assert [(call["unit"], call["unit_number"], call["limit"]) for call in client.calls] == [(3, 4, 100), (3, 1, 100)]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_supertrend_pivot_fetches_signal_and_daily_candles():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    class StubClient:
        def __init__(self):
            self.calls = []

        def retrieve_bars(self, **kwargs):
            self.calls.append(kwargs)
            return []

    client = StubClient()
    config = BotConfig(
        user_id="00000000-0000-0000-0000-000000000000",
        account_id=9001,
        name="ST Pivot Bot",
        enabled=False,
        execution_mode="dry_run",
        strategy_type="supertrend_pivot",
        strategy_params={},
        contract_id="CON.F.US.MNQ.M26",
        symbol="MNQ",
        timeframe_unit="minute",
        timeframe_unit_number=15,
        lookback_bars=250,
        fast_period=9,
        slow_period=21,
        order_size=1,
        max_contracts=1,
        max_daily_loss=250,
        max_trades_per_day=3,
        max_open_position=1,
        allowed_contracts=["CON.F.US.MNQ.M26"],
        trading_start_time="00:00",
        trading_end_time="23:59",
        cooldown_seconds=0,
        max_data_staleness_seconds=1800,
    )

    try:
        rows = fetch_and_store_supertrend_pivot_candles(
            db,
            user_id="00000000-0000-0000-0000-000000000000",
            config=config,
            client=client,
            strategy_params={},
        )

        assert rows == {"signal": [], "1D": []}
        assert [(call["unit"], call["unit_number"], call["limit"]) for call in client.calls] == [(2, 15, 250), (4, 1, 10)]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_fvg_sweep_mss_generates_buy_signal_on_structure_break():
    fvg_candles = [
        _make_candle(datetime(2026, 4, 1, 9, 30, tzinfo=timezone.utc), 100, unit_number=15, open_price=99, high_price=100, low_price=98, volume=50),
        _make_candle(datetime(2026, 4, 1, 9, 45, tzinfo=timezone.utc), 101, unit_number=15, open_price=100, high_price=102, low_price=100, volume=55),
        _make_candle(datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc), 102, unit_number=15, open_price=101, high_price=103, low_price=101, volume=60),
        _make_candle(datetime(2026, 4, 1, 10, 15, tzinfo=timezone.utc), 105, unit_number=15, open_price=104, high_price=106, low_price=104, volume=58),
        _make_candle(datetime(2026, 4, 1, 10, 30, tzinfo=timezone.utc), 106, unit_number=15, open_price=105, high_price=107, low_price=105, volume=52),
    ]
    structure_candles = [
        _make_candle(datetime(2026, 4, 1, 10, 20, tzinfo=timezone.utc), 105.8, open_price=105.6, high_price=106.0, low_price=105.2),
        _make_candle(datetime(2026, 4, 1, 10, 25, tzinfo=timezone.utc), 105.0, open_price=105.7, high_price=106.2, low_price=104.8),
        _make_candle(datetime(2026, 4, 1, 10, 30, tzinfo=timezone.utc), 106.1, open_price=105.2, high_price=106.6, low_price=105.1),
        _make_candle(datetime(2026, 4, 1, 10, 35, tzinfo=timezone.utc), 105.9, open_price=105.6, high_price=106.2, low_price=103.8),
        _make_candle(datetime(2026, 4, 1, 10, 40, tzinfo=timezone.utc), 106.7, open_price=105.9, high_price=106.9, low_price=105.8),
    ]

    signal = evaluate_fvg_sweep_mss(
        fvg_candles=fvg_candles,
        structure_candles=structure_candles,
        strategy_params={"swing_window": 3, "target_mode": "2r", "stop_buffer_percent": 0},
    )

    assert signal.action == "BUY"
    assert "structure break above 106.6" in signal.reason
    assert signal.raw_payload["stop_loss"] == pytest.approx(103.8)
    assert signal.raw_payload["take_profit"] == pytest.approx(112.5)


def test_fvg_sweep_mss_generates_sell_signal_on_structure_break():
    fvg_candles = [
        _make_candle(datetime(2026, 4, 1, 9, 30, tzinfo=timezone.utc), 112, unit_number=15, open_price=111, high_price=113, low_price=111, volume=45),
        _make_candle(datetime(2026, 4, 1, 9, 45, tzinfo=timezone.utc), 111, unit_number=15, open_price=112, high_price=112, low_price=110, volume=48),
        _make_candle(datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc), 110, unit_number=15, open_price=111, high_price=111, low_price=109, volume=50),
        _make_candle(datetime(2026, 4, 1, 10, 15, tzinfo=timezone.utc), 108, unit_number=15, open_price=109, high_price=108, low_price=107, volume=52),
        _make_candle(datetime(2026, 4, 1, 10, 30, tzinfo=timezone.utc), 107, unit_number=15, open_price=108, high_price=108, low_price=106, volume=47),
    ]
    structure_candles = [
        _make_candle(datetime(2026, 4, 1, 10, 20, tzinfo=timezone.utc), 107.2, open_price=107.3, high_price=107.8, low_price=107.0),
        _make_candle(datetime(2026, 4, 1, 10, 25, tzinfo=timezone.utc), 107.8, open_price=107.4, high_price=108.2, low_price=106.9),
        _make_candle(datetime(2026, 4, 1, 10, 30, tzinfo=timezone.utc), 106.9, open_price=107.6, high_price=108.0, low_price=106.4),
        _make_candle(datetime(2026, 4, 1, 10, 35, tzinfo=timezone.utc), 107.9, open_price=107.6, high_price=110.3, low_price=106.8),
        _make_candle(datetime(2026, 4, 1, 10, 40, tzinfo=timezone.utc), 106.3, open_price=107.8, high_price=107.9, low_price=106.2),
    ]

    signal = evaluate_fvg_sweep_mss(
        fvg_candles=fvg_candles,
        structure_candles=structure_candles,
        strategy_params={"swing_window": 3, "target_mode": "2r", "stop_buffer_percent": 0},
    )

    assert signal.action == "SELL"
    assert "structure break below 106.4" in signal.reason
    assert signal.raw_payload["stop_loss"] == pytest.approx(110.3)
    assert signal.raw_payload["take_profit"] == pytest.approx(98.3)


def test_fvg_sweep_mss_holds_when_gap_is_invalidated_by_strong_volume_close():
    fvg_candles = [
        _make_candle(datetime(2026, 4, 1, 9, 30, tzinfo=timezone.utc), 100, unit_number=15, open_price=99, high_price=100, low_price=98, volume=50),
        _make_candle(datetime(2026, 4, 1, 9, 45, tzinfo=timezone.utc), 101, unit_number=15, open_price=100, high_price=102, low_price=100, volume=55),
        _make_candle(datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc), 102, unit_number=15, open_price=101, high_price=103, low_price=101, volume=60),
        _make_candle(datetime(2026, 4, 1, 10, 15, tzinfo=timezone.utc), 105, unit_number=15, open_price=104, high_price=106, low_price=104, volume=58),
        _make_candle(datetime(2026, 4, 1, 10, 30, tzinfo=timezone.utc), 101, unit_number=15, open_price=101.8, high_price=102, low_price=100.5, volume=120),
    ]
    structure_candles = [
        _make_candle(datetime(2026, 4, 1, 10, 20, tzinfo=timezone.utc), 105.4, open_price=105.3, high_price=105.7, low_price=105.1),
        _make_candle(datetime(2026, 4, 1, 10, 25, tzinfo=timezone.utc), 105.1, open_price=105.4, high_price=105.5, low_price=104.9),
        _make_candle(datetime(2026, 4, 1, 10, 30, tzinfo=timezone.utc), 104.8, open_price=105.0, high_price=105.1, low_price=104.6),
        _make_candle(datetime(2026, 4, 1, 10, 35, tzinfo=timezone.utc), 104.5, open_price=104.8, high_price=104.9, low_price=104.2),
        _make_candle(datetime(2026, 4, 1, 10, 40, tzinfo=timezone.utc), 104.3, open_price=104.5, high_price=104.6, low_price=104.0),
        _make_candle(datetime(2026, 4, 1, 10, 45, tzinfo=timezone.utc), 104.1, open_price=104.3, high_price=104.4, low_price=103.9),
        _make_candle(datetime(2026, 4, 1, 10, 50, tzinfo=timezone.utc), 104.0, open_price=104.1, high_price=104.2, low_price=103.8),
    ]

    signal = evaluate_fvg_sweep_mss(
        fvg_candles=fvg_candles,
        structure_candles=structure_candles,
        strategy_params={"swing_window": 3, "volume_lookback_bars": 3, "strong_volume_multiplier": 1.2},
    )

    assert signal.action == "HOLD"
    assert "invalidated" in signal.reason.lower()


def test_delayed_orb_confirmation_generates_buy_signal_after_five_full_minutes_above_range():
    base = datetime(2026, 4, 1, 13, 30, tzinfo=timezone.utc)
    candles = _make_delayed_orb_session(
        base,
        range_high=100.0,
        range_low=95.0,
        confirmation_minutes=5,
        direction="long",
    )

    signal = evaluate_delayed_orb_confirmation(
        candles,
        strategy_params={
            "opening_range_minutes": 15,
            "confirmation_minutes": 5,
            "stop_mode": "inside_range",
            "target_mode": "2r",
        },
        session_start_time="09:30",
    )

    assert signal.action == "BUY"
    assert "BUY after 5 full minutes outside the 15-minute opening range" in signal.reason
    assert signal.raw_payload["opening_range_high"] == pytest.approx(100.0)
    assert signal.raw_payload["opening_range_low"] == pytest.approx(95.0)
    assert signal.raw_payload["confirmation_state"]["long_streak_minutes"] == 5
    assert signal.raw_payload["stop_loss"] == pytest.approx(100.0)
    assert signal.raw_payload["risk"] == pytest.approx(1.0)
    assert signal.raw_payload["take_profit"] == pytest.approx(103.0)
    assert len(signal.raw_payload["confirmation_candles"]) == 5


def test_delayed_orb_confirmation_supports_short_measured_move_with_opposite_side_stop():
    base = datetime(2026, 4, 1, 13, 30, tzinfo=timezone.utc)
    candles = _make_delayed_orb_session(
        base,
        range_high=110.0,
        range_low=100.0,
        confirmation_minutes=5,
        direction="short",
    )

    signal = evaluate_delayed_orb_confirmation(
        candles,
        strategy_params={
            "opening_range_minutes": 15,
            "confirmation_minutes": 5,
            "stop_mode": "opposite_side",
            "target_mode": "measured_move",
        },
        session_start_time="09:30",
    )

    assert signal.action == "SELL"
    assert "SELL after 5 full minutes outside the 15-minute opening range" in signal.reason
    assert signal.raw_payload["confirmation_state"]["short_streak_minutes"] == 5
    assert signal.raw_payload["stop_loss"] == pytest.approx(110.0)
    assert signal.raw_payload["risk"] == pytest.approx(11.0)
    assert signal.raw_payload["take_profit"] == pytest.approx(89.0)


def test_delayed_orb_confirmation_holds_after_breakout_extends_beyond_entry_window():
    base = datetime(2026, 4, 1, 13, 30, tzinfo=timezone.utc)
    candles = _make_delayed_orb_session(
        base,
        range_high=100.0,
        range_low=95.0,
        confirmation_minutes=5,
        direction="long",
        extra_breakout_candles=1,
    )

    signal = evaluate_delayed_orb_confirmation(
        candles,
        strategy_params={
            "opening_range_minutes": 15,
            "confirmation_minutes": 5,
            "stop_mode": "inside_range",
            "target_mode": "2r",
        },
        session_start_time="09:30",
    )

    assert signal.action == "HOLD"
    assert "extended beyond the entry window" in signal.reason
    assert signal.raw_payload["confirmation_state"]["long_streak_minutes"] == 6


def test_delayed_orb_fetches_current_session_as_one_minute_bars(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    frozen_now = datetime(2026, 4, 1, 14, 5, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now if tz is None else frozen_now.astimezone(tz)

    class StubClient:
        def __init__(self):
            self.calls = []

        def retrieve_bars(self, **kwargs):
            self.calls.append(kwargs)
            return []

    client = StubClient()
    config = BotConfig(
        user_id="00000000-0000-0000-0000-000000000000",
        account_id=9001,
        name="Delayed ORB",
        enabled=False,
        execution_mode="dry_run",
        strategy_type="delayed_orb_confirmation",
        strategy_params={
            "opening_range_minutes": 15,
            "confirmation_minutes": 5,
            "stop_mode": "inside_range",
            "target_mode": "2r",
        },
        contract_id="CON.F.US.MNQ.M26",
        symbol="MNQ",
        timeframe_unit="minute",
        timeframe_unit_number=1,
        lookback_bars=100,
        fast_period=9,
        slow_period=21,
        order_size=1,
        max_contracts=1,
        max_daily_loss=250,
        max_trades_per_day=3,
        max_open_position=1,
        allowed_contracts=["CON.F.US.MNQ.M26"],
        trading_start_time="09:30",
        trading_end_time="15:45",
        cooldown_seconds=0,
        max_data_staleness_seconds=180,
    )
    monkeypatch.setattr(bot_service_module, "datetime", FrozenDateTime)

    try:
        rows = fetch_and_store_delayed_orb_candles(
            db,
            user_id="00000000-0000-0000-0000-000000000000",
            config=config,
            client=client,
            strategy_params=config.strategy_params,
        )

        assert rows == {"1m": [], "1D": []}
        assert len(client.calls) == 1
        assert client.calls[0]["unit"] == 2
        assert client.calls[0]["unit_number"] == 1
        assert client.calls[0]["start"] == datetime(2026, 4, 1, 13, 30, tzinfo=timezone.utc)
        assert client.calls[0]["end"] == frozen_now
        assert client.calls[0]["limit"] == 41
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_delayed_orb_blocks_new_entry_after_one_losing_session_trade(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        Account.__table__,
        PositionLifecycle.__table__,
        ProjectXMarketCandle.__table__,
        BotConfig.__table__,
        BotDecision.__table__,
        BotOrderAttempt.__table__,
        BotRiskEvent.__table__,
        ProjectXTradeEvent.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    intraday = _make_delayed_orb_session(
        datetime(2026, 4, 1, 13, 30, tzinfo=timezone.utc),
        range_high=100.0,
        range_low=95.0,
        confirmation_minutes=5,
        direction="long",
    )
    signal = evaluate_delayed_orb_confirmation(
        intraday,
        strategy_params={
            "opening_range_minutes": 15,
            "confirmation_minutes": 5,
            "stop_mode": "inside_range",
            "target_mode": "2r",
        },
        session_start_time="09:30",
    )
    assert signal.action == "BUY"

    user_id = "00000000-0000-0000-0000-000000000000"
    frozen_now = datetime(2026, 4, 1, 13, 50, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now if tz is None else frozen_now.astimezone(tz)

    monkeypatch.setattr(bot_service_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(
        bot_service_module,
        "fetch_candles_and_evaluate_strategy",
        lambda db, user_id, config, client: (intraday, signal),
    )

    try:
        account = Account(
            user_id=user_id,
            provider="projectx",
            external_id="9001",
            name="Practice 9001",
            account_state="ACTIVE",
            can_trade=True,
            is_visible=True,
        )
        config = BotConfig(
            user_id=user_id,
            account_id=9001,
            name="Delayed ORB",
            enabled=True,
            execution_mode="dry_run",
            strategy_type="delayed_orb_confirmation",
            strategy_params={
                "opening_range_minutes": 15,
                "confirmation_minutes": 5,
                "stop_mode": "inside_range",
                "target_mode": "2r",
                "stop_after_losses_per_session": 1,
            },
            contract_id="CON.F.US.MNQ.M26",
            symbol="MNQ",
            timeframe_unit="minute",
            timeframe_unit_number=1,
            lookback_bars=390,
            fast_period=9,
            slow_period=21,
            order_size=1,
            max_contracts=1,
            max_daily_loss=250,
            max_trades_per_day=3,
            max_open_position=1,
            allowed_contracts=["CON.F.US.MNQ.M26"],
            trading_start_time="09:30",
            trading_end_time="15:45",
            cooldown_seconds=0,
            max_data_staleness_seconds=180,
        )
        db.add_all([account, config])
        db.add(
            PositionLifecycle(
                user_id=user_id,
                account_id=9001,
                contract_id="CON.F.US.MNQ.M26",
                symbol="MNQ",
                opened_at=datetime(2026, 4, 1, 13, 40, tzinfo=timezone.utc),
                closed_at=datetime(2026, 4, 1, 13, 44, tzinfo=timezone.utc),
                side="LONG",
                max_qty=1,
                realized_pnl_usd=-50.0,
            )
        )
        db.flush()

        result = evaluate_bot_config(
            db,
            user_id=user_id,
            config=config,
            account=account,
            client=object(),
            dry_run=True,
        )
        assert result.order_attempt is None
        assert {event.code for event in result.risk_events} == {"session_loss_limit_reached"}
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=list(reversed(tables)))
        engine.dispose()


def test_donchian_breakout_generates_buy_signal_and_reduces_size_when_atr_is_high():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    candles = [
        _make_candle(
            base + timedelta(minutes=index * 5),
            100,
            open_price=100,
            high_price=102,
            low_price=98,
        )
        for index in range(20)
    ]
    candles.append(
        _make_candle(
            base + timedelta(minutes=100),
            103,
            open_price=100,
            high_price=103.5,
            low_price=99.5,
        )
    )

    signal = evaluate_donchian_breakout(
        candles,
        strategy_params={
            "entry_period": 20,
            "exit_period": 10,
            "atr_period": 14,
            "atr_size_reference_percent": 1.0,
            "min_size_scale": 0.25,
        },
        base_order_size=4,
    )

    assert signal.action == "BUY"
    assert "Donchian breakout" in signal.reason
    assert signal.raw_payload["effective_order_size"] == 1.0
    assert signal.raw_payload["target_position_qty"] == 1.0
    assert signal.raw_payload["size_scale"] < 1.0
    assert signal.raw_payload["stop_loss"] < signal.price
    assert signal.raw_payload["take_profit"] > signal.price


def test_donchian_breakout_exits_long_on_opposite_exit_channel():
    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    candles = [
        _make_candle(
            base + timedelta(minutes=index * 5),
            100,
            open_price=100,
            high_price=102,
            low_price=98,
        )
        for index in range(20)
    ]
    candles.append(
        _make_candle(
            base + timedelta(minutes=100),
            97,
            open_price=100,
            high_price=100.5,
            low_price=96.5,
        )
    )

    signal = evaluate_donchian_breakout(
        candles,
        strategy_params={"entry_period": 20, "exit_period": 10, "atr_period": 14},
        position_state=bot_service_module.OpenPositionState(
            net_qty=2.0,
            avg_entry_price=100.0,
            opened_at=base,
        ),
        base_order_size=1,
    )

    assert signal.action == "SELL"
    assert "exit channel" in signal.reason
    assert signal.raw_payload["signal_category"] == "exit"
    assert signal.raw_payload["exit_reason"] == "opposite_exit_channel"
    assert signal.raw_payload["effective_order_size"] == 2.0
    assert signal.raw_payload["target_position_qty"] == 0.0


def test_relative_strength_vs_spy_generates_buy_signal_on_pullback():
    base = datetime(2026, 4, 1, 14, 30, tzinfo=timezone.utc)
    asset_closes = [
        100.0, 100.5, 101.0, 101.5, 102.0, 102.5, 103.0, 103.5, 104.0, 104.5,
        105.0, 105.5, 106.0, 106.5, 107.0, 107.5, 108.0, 108.5, 109.0, 109.5,
        110.0, 110.5, 111.0, 111.5, 112.0, 112.5, 113.0, 112.8, 112.4, 112.3,
    ]
    benchmark_closes = [
        400.0, 400.1, 400.2, 400.3, 400.4, 400.5, 400.6, 400.7, 400.8, 400.9,
        401.0, 401.1, 401.2, 401.3, 401.4, 401.5, 401.6, 401.7, 401.8, 401.9,
        402.0, 402.1, 402.2, 402.3, 402.4, 402.5, 402.6, 401.8, 400.8, 399.8,
    ]

    asset_candles = []
    benchmark_candles = []
    for index, close in enumerate(asset_closes):
        timestamp = base + timedelta(minutes=index * 5)
        previous_close = asset_closes[index - 1] if index > 0 else close - 0.25
        asset_candles.append(
            _make_candle(
                timestamp,
                close,
                open_price=previous_close,
                low_price=min(previous_close, close) - 0.2,
                high_price=max(previous_close, close) + 0.2,
                volume=450 if index == len(asset_closes) - 1 else 100,
            )
        )
        benchmark_previous_close = benchmark_closes[index - 1] if index > 0 else benchmark_closes[0] - 0.1
        benchmark_candles.append(
            _make_candle(
                timestamp,
                benchmark_closes[index],
                contract_id="CON.F.US.SPY.M26",
                symbol="SPY",
                open_price=benchmark_previous_close,
                low_price=min(benchmark_previous_close, benchmark_closes[index]) - 0.1,
                high_price=max(benchmark_previous_close, benchmark_closes[index]) + 0.1,
                volume=150,
            )
        )

    signal = evaluate_relative_strength_vs_spy(
        asset_candles=asset_candles,
        benchmark_candles=benchmark_candles,
        strategy_params={
            "entry_level_tolerance_percent": 2.0,
            "minimum_relative_strength_percent": 0.1,
            "minimum_benchmark_move_percent": 0.05,
        },
    )

    assert signal.action == "BUY"
    assert "BUY on relative strength vs SPY" in signal.reason
    assert signal.raw_payload["relative_volume"] > 2
    assert signal.raw_payload["take_profit"] > signal.price
    assert signal.raw_payload["stop_loss"] < signal.price


def test_relative_strength_vs_spy_generates_sell_signal_on_failed_bounce():
    base = datetime(2026, 4, 1, 14, 30, tzinfo=timezone.utc)
    asset_closes = [
        120.0, 119.7, 119.4, 119.1, 118.8, 118.5, 118.2, 117.9, 117.6, 117.3,
        117.0, 116.7, 116.4, 116.1, 115.8, 115.5, 115.2, 114.9, 114.6, 114.3,
        114.0, 113.7, 113.4, 113.1, 112.8, 112.5, 112.2, 112.3, 112.25, 112.15,
    ]
    benchmark_closes = [
        400.0, 400.2, 400.4, 400.6, 400.8, 401.0, 401.2, 401.4, 401.6, 401.8,
        402.0, 402.2, 402.4, 402.6, 402.8, 403.0, 403.2, 403.4, 403.6, 403.8,
        404.0, 404.2, 404.4, 404.6, 404.8, 405.0, 405.2, 405.3, 405.5, 405.7,
    ]

    asset_candles = []
    benchmark_candles = []
    for index, close in enumerate(asset_closes):
        timestamp = base + timedelta(minutes=index * 5)
        previous_close = asset_closes[index - 1] if index > 0 else close + 0.25
        asset_candles.append(
            _make_candle(
                timestamp,
                close,
                open_price=previous_close,
                low_price=min(previous_close, close) - 0.15,
                high_price=max(previous_close, close) + 0.15,
                volume=420 if index == len(asset_closes) - 1 else 100,
            )
        )
        benchmark_previous_close = benchmark_closes[index - 1] if index > 0 else benchmark_closes[0] - 0.1
        benchmark_candles.append(
            _make_candle(
                timestamp,
                benchmark_closes[index],
                contract_id="CON.F.US.SPY.M26",
                symbol="SPY",
                open_price=benchmark_previous_close,
                low_price=min(benchmark_previous_close, benchmark_closes[index]) - 0.1,
                high_price=max(benchmark_previous_close, benchmark_closes[index]) + 0.1,
                volume=150,
            )
        )

    signal = evaluate_relative_strength_vs_spy(
        asset_candles=asset_candles,
        benchmark_candles=benchmark_candles,
        strategy_params={
            "entry_level_tolerance_percent": 2.0,
            "minimum_relative_strength_percent": 0.1,
            "minimum_benchmark_move_percent": 0.05,
        },
    )

    assert signal.action == "SELL"
    assert "SELL on relative weakness vs SPY" in signal.reason
    assert signal.raw_payload["relative_volume"] > 2
    assert signal.raw_payload["take_profit"] < signal.price
    assert signal.raw_payload["stop_loss"] > signal.price


def test_relative_strength_vs_spy_fetches_symbol_and_spy_five_minute_candles():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    class StubClient:
        def __init__(self):
            self.calls = []

        def search_contracts(self, *, search_text: str, live: bool = False):
            if search_text == "SPY":
                return [{"id": "CON.F.US.SPY.M26", "symbol_id": "SPY", "active_contract": True}]
            return []

        def retrieve_bars(self, **kwargs):
            self.calls.append(kwargs)
            return []

    client = StubClient()
    config = BotConfig(
        user_id="00000000-0000-0000-0000-000000000000",
        account_id=9001,
        name="RS vs SPY",
        enabled=False,
        execution_mode="dry_run",
        strategy_type="relative_strength_spy",
        strategy_params={"benchmark_symbol": "SPY"},
        contract_id="CON.F.US.MNQ.M26",
        symbol="MNQ",
        timeframe_unit="minute",
        timeframe_unit_number=5,
        lookback_bars=25,
        fast_period=9,
        slow_period=21,
        order_size=1,
        max_contracts=1,
        max_daily_loss=250,
        max_trades_per_day=3,
        max_open_position=1,
        allowed_contracts=["CON.F.US.MNQ.M26"],
        trading_start_time="00:00",
        trading_end_time="23:59",
        cooldown_seconds=0,
        max_data_staleness_seconds=900,
    )

    try:
        rows = fetch_and_store_relative_strength_spy_candles(
            db,
            user_id="00000000-0000-0000-0000-000000000000",
            config=config,
            client=client,
            strategy_params={"benchmark_symbol": "SPY"},
        )

        assert rows == {"5m": [], "SPY": []}
        assert [(call["contract_id"], call["unit"], call["unit_number"], call["limit"]) for call in client.calls] == [
            ("CON.F.US.MNQ.M26", 2, 5, 50),
            ("CON.F.US.SPY.M26", 2, 5, 50),
        ]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_fetch_market_candles_deduplicates_provider_timestamps():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    timestamp = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)

    class StubClient:
        def retrieve_bars(self, **_kwargs):
            return [
                {
                    "timestamp": timestamp,
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10,
                    "volume": 1,
                },
                {
                    "timestamp": timestamp,
                    "open": 12,
                    "high": 14,
                    "low": 11,
                    "close": 13,
                    "volume": 2,
                },
            ]

    try:
        rows = fetch_and_store_market_candles(
            db,
            user_id="00000000-0000-0000-0000-000000000000",
            client=StubClient(),
            contract_id="CON.F.US.MNQ.M26",
            symbol="MNQ",
            live=False,
            start=timestamp - timedelta(minutes=5),
            end=timestamp + timedelta(minutes=5),
            unit="minute",
            unit_number=5,
            limit=500,
        )

        assert len(rows) == 1
        assert rows[0].close_price == 13.0
        assert rows[0].volume == 2.0
        assert db.query(ProjectXMarketCandle).count() == 1
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_list_market_candles_returns_cached_rows_sorted_with_limit():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    try:
        db.add_all(
            [
                _make_candle(datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc), 10),
                _make_candle(datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc), 11),
                _make_candle(datetime(2026, 4, 1, 10, 10, tzinfo=timezone.utc), 12),
                _make_candle(
                    datetime(2026, 4, 1, 10, 15, tzinfo=timezone.utc),
                    13,
                    is_partial=True,
                ),
            ]
        )
        db.commit()

        rows = list_market_candles(
            db,
            user_id=user_id,
            contract_id="CON.F.US.MNQ.M26",
            live=False,
            start=datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 10, 20, tzinfo=timezone.utc),
            unit="minute",
            unit_number=5,
            limit=2,
            include_partial_bar=False,
        )

        assert [float(row.close_price) for row in rows] == [11.0, 12.0]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_create_bot_config_rejects_duplicate_name_for_user():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [Account.__table__, BotConfig.__table__]
    Base.metadata.create_all(bind=engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    try:
        db.add(
            Account(
                user_id=user_id,
                provider="projectx",
                external_id="9001",
                name="Practice 9001",
                account_state="ACTIVE",
                can_trade=True,
                is_visible=True,
            )
        )
        db.flush()
        create_bot_config(db, user_id=user_id, payload=_make_bot_create_payload(name="Opening Drive"))
        db.flush()

        with pytest.raises(ValueError, match="already exists"):
            create_bot_config(db, user_id=user_id, payload=_make_bot_create_payload(name=" opening drive "))
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=tables)
        engine.dispose()


def test_create_bot_config_rejects_invalid_ema_scalping_timeframe():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [Account.__table__, BotConfig.__table__]
    Base.metadata.create_all(bind=engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    try:
        db.add(
            Account(
                user_id=user_id,
                provider="projectx",
                external_id="9001",
                name="Practice 9001",
                account_state="ACTIVE",
                can_trade=True,
                is_visible=True,
            )
        )
        db.flush()

        payload = _make_bot_create_payload(name="MNQ 9/15 EMA Scalping").model_copy(
            update={
                "strategy_type": "ema_scalping",
                "timeframe_unit": "minute",
                "timeframe_unit_number": 1,
                "fast_period": 9,
                "slow_period": 15,
            }
        )

        with pytest.raises(ValueError, match="3-minute or 5-minute timeframe"):
            create_bot_config(db, user_id=user_id, payload=payload)
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=tables)
        engine.dispose()


def test_update_bot_config_rejects_duplicate_name_for_user():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [BotConfig.__table__]
    Base.metadata.create_all(bind=engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    try:
        first = BotConfig(
            user_id=user_id,
            account_id=9001,
            name="Opening Drive",
            enabled=False,
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
        second = BotConfig(
            user_id=user_id,
            account_id=9001,
            name="Reversal",
            enabled=False,
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
        db.add_all([first, second])
        db.flush()

        with pytest.raises(ValueError, match="already exists"):
            update_bot_config(
                db,
                user_id=user_id,
                bot_config_id=second.id,
                payload=BotConfigUpdateIn(name=" opening drive "),
            )
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=tables)
        engine.dispose()


def test_delete_bot_config_removes_activity_rows():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        BotConfig.__table__,
        BotRun.__table__,
        BotDecision.__table__,
        BotOrderAttempt.__table__,
        BotRiskEvent.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    try:
        config = BotConfig(
            user_id=user_id,
            account_id=9001,
            name="Delete Me",
            enabled=False,
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
        run = BotRun(user_id=user_id, bot_config_id=config.id, account_id=9001, dry_run=True, status="stopped")
        db.add(run)
        db.flush()
        decision = BotDecision(
            user_id=user_id,
            bot_config_id=config.id,
            bot_run_id=run.id,
            account_id=9001,
            contract_id="CON.F.US.MNQ.M26",
            symbol="MNQ",
            decision_type="signal",
            action="HOLD",
            reason="test",
        )
        db.add(decision)
        db.flush()
        db.add(
            BotOrderAttempt(
                user_id=user_id,
                bot_config_id=config.id,
                bot_run_id=run.id,
                bot_decision_id=decision.id,
                account_id=9001,
                contract_id="CON.F.US.MNQ.M26",
                side="BUY",
                order_type="market",
                size=1,
                status="dry_run",
            )
        )
        db.add(
            BotRiskEvent(
                user_id=user_id,
                bot_config_id=config.id,
                bot_run_id=run.id,
                account_id=9001,
                severity="info",
                code="test",
                message="test",
            )
        )
        db.commit()

        delete_bot_config(db, user_id=user_id, bot_config_id=config.id)
        db.commit()

        assert db.query(BotConfig).count() == 0
        assert db.query(BotRun).count() == 0
        assert db.query(BotDecision).count() == 0
        assert db.query(BotOrderAttempt).count() == 0
        assert db.query(BotRiskEvent).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=tables)
        engine.dispose()


def test_candles_endpoint_returns_local_cache_without_provider_call(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: user_id)
    monkeypatch.setattr(
        main_module,
        "_projectx_client_for_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider should not be called")),
    )

    try:
        db.add(_make_candle(datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc), 10))
        db.commit()

        payload = main_module.get_projectx_market_candles(
            contract_id="CON.F.US.MNQ.M26",
            start=datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc),
            unit="minute",
            unit_number=5,
            limit=500,
            refresh=False,
            db=db,
        )

        assert len(payload) == 1
        assert payload[0]["close"] == 10.0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_candles_endpoint_fetches_full_history_when_cache_only_has_recent_tail(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: user_id)

    class StubClient:
        def __init__(self):
            self.calls = []

        def retrieve_bars(self, **kwargs):
            self.calls.append(kwargs)
            return [
                {
                    "timestamp": datetime(2026, 4, 1, 9, 30, tzinfo=timezone.utc),
                    "open": 1,
                    "high": 1,
                    "low": 1,
                    "close": 1,
                    "volume": 1,
                },
                {
                    "timestamp": datetime(2026, 4, 1, 9, 35, tzinfo=timezone.utc),
                    "open": 2,
                    "high": 2,
                    "low": 2,
                    "close": 2,
                    "volume": 1,
                },
            ]

    client = StubClient()
    monkeypatch.setattr(main_module, "_projectx_client_for_user", lambda *_args, **_kwargs: client)

    try:
        fetched_at = datetime(2026, 4, 1, 11, 0, tzinfo=timezone.utc)
        db.add_all(
            [
                _make_candle(datetime(2026, 4, 1, 10, 45, tzinfo=timezone.utc), 10, fetched_at=fetched_at),
                _make_candle(datetime(2026, 4, 1, 10, 50, tzinfo=timezone.utc), 11, fetched_at=fetched_at),
                _make_candle(datetime(2026, 4, 1, 10, 55, tzinfo=timezone.utc), 12, fetched_at=fetched_at),
                _make_candle(datetime(2026, 4, 1, 11, 0, tzinfo=timezone.utc), 13, fetched_at=fetched_at),
            ]
        )
        db.commit()

        payload = main_module.get_projectx_market_candles(
            contract_id="CON.F.US.MNQ.M26",
            start=datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 11, 5, tzinfo=timezone.utc),
            unit="minute",
            unit_number=5,
            limit=300,
            refresh=False,
            db=db,
        )

        assert len(client.calls) == 1
        assert client.calls[0]["start"] == datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc)
        assert [row["timestamp"] for row in payload] == [
            datetime(2026, 4, 1, 9, 30, tzinfo=timezone.utc),
            datetime(2026, 4, 1, 9, 35, tzinfo=timezone.utc),
            datetime(2026, 4, 1, 10, 45, tzinfo=timezone.utc),
            datetime(2026, 4, 1, 10, 50, tzinfo=timezone.utc),
            datetime(2026, 4, 1, 10, 55, tzinfo=timezone.utc),
            datetime(2026, 4, 1, 11, 0, tzinfo=timezone.utc),
        ]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_candles_endpoint_fetches_only_missing_closed_tail(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: user_id)

    class StubClient:
        def __init__(self):
            self.calls = []

        def retrieve_bars(self, **kwargs):
            self.calls.append(kwargs)
            return [
                {
                    "timestamp": datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc),
                    "open": 11,
                    "high": 12,
                    "low": 10,
                    "close": 11,
                    "volume": 1,
                }
            ]

    client = StubClient()
    monkeypatch.setattr(main_module, "_projectx_client_for_user", lambda *_args, **_kwargs: client)

    try:
        db.add(_make_candle(datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc), 10))
        db.commit()

        payload = main_module.get_projectx_market_candles(
            contract_id="CON.F.US.MNQ.M26",
            start=datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 10, 10, tzinfo=timezone.utc),
            unit="minute",
            unit_number=5,
            limit=500,
            refresh=False,
            db=db,
        )

        assert [row["close"] for row in payload] == [10.0, 11.0]
        assert len(client.calls) == 1
        assert client.calls[0]["start"] == datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc)
        assert client.calls[0]["end"] == datetime(2026, 4, 1, 10, 10, tzinfo=timezone.utc)
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_candles_endpoint_revalidates_stale_recent_closed_bar(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: user_id)

    class StubClient:
        def __init__(self):
            self.calls = []

        def retrieve_bars(self, **kwargs):
            self.calls.append(kwargs)
            return [
                {
                    "timestamp": datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
                    "open": 27410,
                    "high": 27412,
                    "low": 27397,
                    "close": 27405,
                    "volume": 10,
                }
            ]

    client = StubClient()
    monkeypatch.setattr(main_module, "_projectx_client_for_user", lambda *_args, **_kwargs: client)

    try:
        db.add(
            _make_candle(
                datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
                27405,
                open_price=27410,
                high_price=27412,
                low_price=27401,
                fetched_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            )
        )
        db.commit()

        payload = main_module.get_projectx_market_candles(
            contract_id="CON.F.US.MNQ.M26",
            start=datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 10, 6, tzinfo=timezone.utc),
            unit="minute",
            unit_number=5,
            limit=500,
            refresh=False,
            db=db,
        )

        assert len(client.calls) == 1
        assert client.calls[0]["start"] == datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc)
        assert payload[0]["low"] == 27397.0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_candles_endpoint_resolves_legacy_symbol_to_cached_contract(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: user_id)

    class StubClient:
        def search_contracts(self, *, search_text, live):
            assert search_text == "MNQ"
            assert live is False
            return [
                {
                    "id": "CON.F.US.MNQ.M26",
                    "name": "MNQM6",
                    "active_contract": True,
                    "symbol_id": "F.US.MNQ",
                }
            ]

        def retrieve_bars(self, **_kwargs):
            raise AssertionError("cached resolved candles should avoid provider history")

    monkeypatch.setattr(main_module, "_projectx_client_for_user", lambda *_args, **_kwargs: StubClient())

    try:
        db.add(
            _make_candle(
                datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
                10,
                contract_id="CON.F.US.MNQ.M26",
                symbol="F.US.MNQ",
            )
        )
        db.commit()

        payload = main_module.get_projectx_market_candles(
            contract_id="MNQ",
            symbol="MNQ",
            start=datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc),
            unit="minute",
            unit_number=5,
            limit=500,
            refresh=False,
            db=db,
        )

        assert len(payload) == 1
        assert payload[0]["contract_id"] == "CON.F.US.MNQ.M26"
        assert payload[0]["symbol"] == "F.US.MNQ"
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_candles_endpoint_falls_back_to_active_symbol_contract_when_saved_contract_has_no_bars(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: user_id)

    class StubClient:
        def __init__(self):
            self.history_contract_ids = []

        def search_contracts(self, *, search_text, live):
            assert search_text == "MNQ"
            assert live is False
            return [
                {
                    "id": "CON.F.US.MNQ.M26",
                    "name": "MNQM6",
                    "active_contract": True,
                    "symbol_id": "MNQ",
                }
            ]

        def retrieve_bars(self, **kwargs):
            self.history_contract_ids.append(kwargs["contract_id"])
            if kwargs["contract_id"] == "CON.F.US.MNQ.H26":
                return []
            if kwargs["contract_id"] == "CON.F.US.MNQ.M26":
                return [
                    {
                        "timestamp": datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc),
                        "open": 10,
                        "high": 11,
                        "low": 9,
                        "close": 10.5,
                        "volume": 1,
                    }
                ]
            raise AssertionError(f"unexpected contract: {kwargs['contract_id']}")

    client = StubClient()
    monkeypatch.setattr(main_module, "_projectx_client_for_user", lambda *_args, **_kwargs: client)

    try:
        payload = main_module.get_projectx_market_candles(
            contract_id="CON.F.US.MNQ.H26",
            symbol="MNQ",
            start=datetime(2026, 5, 13, 9, 55, tzinfo=timezone.utc),
            end=datetime(2026, 5, 13, 10, 5, tzinfo=timezone.utc),
            unit="minute",
            unit_number=5,
            limit=500,
            refresh=False,
            db=db,
        )

        assert client.history_contract_ids == ["CON.F.US.MNQ.H26", "CON.F.US.MNQ.M26"]
        assert len(payload) == 1
        assert payload[0]["contract_id"] == "CON.F.US.MNQ.M26"
        assert payload[0]["close"] == 10.5
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_candles_endpoint_returns_cached_rows_when_refresh_provider_fails(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: user_id)

    class StubClient:
        def retrieve_bars(self, **_kwargs):
            raise ProjectXClientError("ProjectX request timed out.", status_code=504)

    monkeypatch.setattr(main_module, "_projectx_client_for_user", lambda *_args, **_kwargs: StubClient())

    try:
        db.add(_make_candle(datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc), 10))
        db.commit()

        payload = main_module.get_projectx_market_candles(
            contract_id="CON.F.US.MNQ.M26",
            start=datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc),
            unit="minute",
            unit_number=5,
            limit=500,
            refresh=True,
            db=db,
        )

        assert len(payload) == 1
        assert payload[0]["close"] == 10.0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_candles_endpoint_refresh_replaces_cached_window(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: user_id)

    class StubClient:
        def retrieve_bars(self, **_kwargs):
            return [
                {
                    "timestamp": datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
                    "open": 11,
                    "high": 12,
                    "low": 10,
                    "close": 11,
                    "volume": 1,
                },
                {
                    "timestamp": datetime(2026, 4, 1, 10, 10, tzinfo=timezone.utc),
                    "open": 13,
                    "high": 14,
                    "low": 12,
                    "close": 13,
                    "volume": 1,
                },
            ]

    monkeypatch.setattr(main_module, "_projectx_client_for_user", lambda *_args, **_kwargs: StubClient())

    try:
        db.add_all(
            [
                _make_candle(datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc), 10),
                _make_candle(datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc), 999),
            ]
        )
        db.commit()

        payload = main_module.get_projectx_market_candles(
            contract_id="CON.F.US.MNQ.M26",
            start=datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 10, 15, tzinfo=timezone.utc),
            unit="minute",
            unit_number=5,
            limit=500,
            refresh=True,
            db=db,
        )

        assert [row["timestamp"] for row in payload] == [
            datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 1, 10, 10, tzinfo=timezone.utc),
        ]
        assert [row["close"] for row in payload] == [11.0, 13.0]

        cached_rows = list_market_candles(
            db,
            user_id=user_id,
            contract_id="CON.F.US.MNQ.M26",
            live=False,
            start=datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 10, 15, tzinfo=timezone.utc),
            unit="minute",
            unit_number=5,
            limit=500,
        )
        assert [_as_test_utc(row.candle_timestamp) for row in cached_rows] == [
            datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 1, 10, 10, tzinfo=timezone.utc),
        ]
        assert [row.close_price for row in cached_rows] == [11.0, 13.0]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_candles_endpoint_partial_refresh_does_not_prune_closed_cache(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: user_id)

    class StubClient:
        def retrieve_bars(self, **_kwargs):
            return [
                {
                    "timestamp": datetime(2026, 4, 1, 10, 2, tzinfo=timezone.utc),
                    "open": 12,
                    "high": 13,
                    "low": 11,
                    "close": 12,
                    "volume": 1,
                    "is_partial": True,
                }
            ]

    monkeypatch.setattr(main_module, "_projectx_client_for_user", lambda *_args, **_kwargs: StubClient())

    try:
        db.add_all(
            [
                _make_candle(datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc), 10, unit_number=1),
                _make_candle(datetime(2026, 4, 1, 10, 1, tzinfo=timezone.utc), 11, unit_number=1),
            ]
        )
        db.commit()

        main_module.get_projectx_market_candles(
            contract_id="CON.F.US.MNQ.M26",
            start=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 10, 2, tzinfo=timezone.utc),
            unit="minute",
            unit_number=1,
            limit=5,
            include_partial_bar=True,
            refresh=True,
            db=db,
        )

        cached_rows = list_market_candles(
            db,
            user_id=user_id,
            contract_id="CON.F.US.MNQ.M26",
            live=False,
            start=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 10, 2, tzinfo=timezone.utc),
            unit="minute",
            unit_number=1,
            limit=5,
            include_partial_bar=True,
        )
        assert [_as_test_utc(row.candle_timestamp) for row in cached_rows] == [
            datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 1, 10, 1, tzinfo=timezone.utc),
            datetime(2026, 4, 1, 10, 2, tzinfo=timezone.utc),
        ]
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def test_fetch_market_candles_resolves_legacy_symbol_before_history_call():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    class StubClient:
        def search_contracts(self, *, search_text, live):
            assert search_text == "MNQ"
            assert live is False
            return [
                {
                    "id": "CON.F.US.MNQ.M26",
                    "name": "MNQM6",
                    "active_contract": True,
                    "symbol_id": "F.US.MNQ",
                }
            ]

        def retrieve_bars(self, **kwargs):
            assert kwargs["contract_id"] == "CON.F.US.MNQ.M26"
            return [
                {
                    "timestamp": datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": 1,
                }
            ]

    try:
        rows = fetch_and_store_market_candles(
            db,
            user_id="00000000-0000-0000-0000-000000000000",
            client=StubClient(),
            contract_id="MNQ",
            symbol="MNQ",
            live=False,
            start=datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc),
            end=datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc),
            unit="minute",
            unit_number=5,
            limit=500,
        )

        assert len(rows) == 1
        assert rows[0].contract_id == "CON.F.US.MNQ.M26"
        assert rows[0].symbol == "F.US.MNQ"
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


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


def test_macd_support_resistance_evaluation_serializes_trailing_stop_plan_into_order_attempt():
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

    base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    higher_timeframe = [
        _make_hour_candle(base + timedelta(hours=index * 4), close=120 + index, low=118 + index, high=122 + index, unit_number=4)
        for index in range(5)
    ]
    lower_timeframe = [
        _make_hour_candle(base + timedelta(hours=0), close=100.0, low=99.5, high=100.5, unit_number=1),
        _make_hour_candle(base + timedelta(hours=1), close=99.0, low=98.5, high=99.5, unit_number=1),
        _make_hour_candle(base + timedelta(hours=2), close=98.0, low=97.5, high=98.5, unit_number=1),
        _make_hour_candle(base + timedelta(hours=3), close=97.0, low=96.5, high=97.5, unit_number=1),
        _make_hour_candle(base + timedelta(hours=4), close=96.0, low=95.9, high=96.5, unit_number=1),
        _make_hour_candle(base + timedelta(hours=5), close=97.2, low=96.8, high=97.5, unit_number=1),
        _make_hour_candle(base + timedelta(hours=6), close=95.7, low=95.7, high=96.2, unit_number=1),
        _make_hour_candle(base + timedelta(hours=7), close=96.0, low=95.9, high=96.3, unit_number=1),
    ]

    class StubClient:
        place_order_called = False

        def retrieve_bars(self, **kwargs):
            if kwargs["unit_number"] == 4:
                return _bars_from_candles(higher_timeframe)
            if kwargs["unit_number"] == 1:
                return _bars_from_candles(lower_timeframe)
            raise AssertionError(f"unexpected unit_number: {kwargs['unit_number']}")

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
            name="MACD SR Bot",
            enabled=True,
            execution_mode="dry_run",
            strategy_type="macd_support_resistance",
            strategy_params={
                "bars_per_timeframe": 20,
                "swing_window": 3,
                "level_tolerance_percent": 0.35,
                "signal_period": 2,
                "atr_period": 3,
                "initial_stop_atr_multiplier": 1.5,
                "trailing_stop_mode": "atr",
                "trailing_atr_multiplier": 2.0,
            },
            contract_id="CON.F.US.MNQ.M26",
            symbol="MNQ",
            timeframe_unit="hour",
            timeframe_unit_number=1,
            lookback_bars=100,
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
            max_data_staleness_seconds=315360000,
        )
        db.add(config)
        db.flush()

        result = evaluate_bot_config(
            db,
            user_id="00000000-0000-0000-0000-000000000000",
            config=config,
            account=account,
            client=StubClient(),
            dry_run=True,
        )
        assert result.decision.action == "BUY"
        assert result.order_attempt is not None
        strategy_plan = result.order_attempt.raw_request["strategyOrderPlan"]
        assert strategy_plan["strategy_type"] == "macd_support_resistance"
        assert strategy_plan["trailing_stop"]["mode"] == "atr"
        assert "take_profit" not in strategy_plan
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=list(reversed(tables)))
        engine.dispose()


def test_donchian_reversal_uses_double_size_to_flip_position_without_risk_block():
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
            base = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
            rows = [
                {"timestamp": base + timedelta(minutes=index * 5), "open": 100, "high": 102, "low": 98, "close": 100, "volume": 1}
                for index in range(20)
            ]
            rows.append(
                {
                    "timestamp": base + timedelta(minutes=100),
                    "open": 100,
                    "high": 100.5,
                    "low": 95,
                    "close": 95,
                    "volume": 1,
                }
            )
            return rows

        def place_order(self, **_kwargs):
            raise AssertionError("dry-run evaluation should not submit provider orders")

    user_id = "00000000-0000-0000-0000-000000000000"
    try:
        account = Account(
            user_id=user_id,
            provider="projectx",
            external_id="9001",
            name="Practice 9001",
            account_state="ACTIVE",
            can_trade=True,
            is_visible=True,
        )
        db.add(account)
        config = BotConfig(
            user_id=user_id,
            account_id=9001,
            name="Donchian Flip",
            enabled=True,
            execution_mode="dry_run",
            strategy_type="donchian_breakout",
            strategy_params={"entry_period": 20, "exit_period": 10, "atr_period": 14},
            contract_id="CON.F.US.MNQ.M26",
            symbol="MNQ",
            timeframe_unit="minute",
            timeframe_unit_number=5,
            lookback_bars=50,
            fast_period=9,
            slow_period=21,
            order_size=1,
            max_contracts=1,
            max_daily_loss=250,
            max_trades_per_day=3,
            max_open_position=1,
            allowed_contracts=["CON.F.US.MNQ.M26"],
            trading_start_time="00:00",
            trading_end_time="23:59",
            cooldown_seconds=0,
            max_data_staleness_seconds=315360000,
        )
        db.add(config)
        db.flush()
        db.add(
            ProjectXTradeEvent(
                user_id=user_id,
                account_id=9001,
                contract_id="CON.F.US.MNQ.M26",
                symbol="MNQ",
                side="BUY",
                size=1,
                price=100,
                trade_timestamp=datetime(2026, 4, 1, 9, 55, tzinfo=timezone.utc),
                order_id="entry-1",
            )
        )
        db.flush()

        result = evaluate_bot_config(
            db,
            user_id=user_id,
            config=config,
            account=account,
            client=StubClient(),
            dry_run=True,
        )
        assert result.decision.action == "SELL"
        assert result.order_attempt is not None
        assert result.order_attempt.size == 2.0
        assert result.order_attempt.raw_request["size"] == 2
        assert result.order_attempt.raw_request["strategyOrderPlan"]["signal_category"] == "reversal"
        assert result.risk_events == []
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


def test_evaluate_endpoint_rejects_live_routing_request(monkeypatch):
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: "00000000-0000-0000-0000-000000000000")

    with pytest.raises(HTTPException) as exc_info:
        main_module.evaluate_trading_bot(
            1,
            payload=BotStartIn(dry_run=False, confirm_live_order_routing=True),
            db=None,
        )

    assert exc_info.value.status_code == 400
    assert "dry-run only" in exc_info.value.detail


def test_evaluation_uses_resolved_contract_for_decision_and_order_attempt():
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
        def search_contracts(self, *, search_text, live):
            assert search_text == "MNQ"
            assert live is False
            return [
                {
                    "id": "CON.F.US.MNQ.M26",
                    "name": "MNQM6",
                    "active_contract": True,
                    "symbol_id": "F.US.MNQ",
                }
            ]

        def retrieve_bars(self, **kwargs):
            assert kwargs["contract_id"] == "CON.F.US.MNQ.M26"
            return [
                {"timestamp": _dt(3), "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1},
                {"timestamp": _dt(2), "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1},
                {"timestamp": _dt(1), "open": 9, "high": 9, "low": 9, "close": 9, "volume": 1},
                {"timestamp": _dt(0), "open": 20, "high": 20, "low": 20, "close": 20, "volume": 1},
            ]

        def place_order(self, **_kwargs):
            raise AssertionError("dry-run evaluation should not submit provider orders")

    user_id = "00000000-0000-0000-0000-000000000000"
    try:
        account = Account(
            user_id=user_id,
            provider="projectx",
            external_id="9001",
            name="Practice 9001",
            account_state="ACTIVE",
            can_trade=True,
            is_visible=True,
        )
        db.add(account)
        db.flush()
        payload = _make_bot_create_payload(name="Resolved Contract Bot").model_copy(
            update={"contract_id": "MNQ", "allowed_contracts": []}
        )
        config = create_bot_config(db, user_id=user_id, payload=payload)
        config.enabled = True
        db.flush()

        result = evaluate_bot_config(
            db,
            user_id=user_id,
            config=config,
            account=account,
            client=StubClient(),
            dry_run=True,
        )
        db.commit()

        assert result.risk_events == []
        assert result.decision.contract_id == "CON.F.US.MNQ.M26"
        assert result.decision.symbol == "F.US.MNQ"
        assert result.order_attempt is not None
        assert result.order_attempt.contract_id == "CON.F.US.MNQ.M26"
        assert result.order_attempt.raw_request["contractId"] == "CON.F.US.MNQ.M26"
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=list(reversed(tables)))
        engine.dispose()


def test_start_bot_run_supersedes_existing_running_run():
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
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    class StubClient:
        def retrieve_bars(self, **_kwargs):
            return [
                {"timestamp": _dt(3), "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1},
                {"timestamp": _dt(2), "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1},
                {"timestamp": _dt(1), "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1},
                {"timestamp": _dt(0), "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1},
            ]

    user_id = "00000000-0000-0000-0000-000000000000"
    try:
        account = Account(
            user_id=user_id,
            provider="projectx",
            external_id="9001",
            name="Practice 9001",
            account_state="ACTIVE",
            can_trade=True,
            is_visible=True,
        )
        db.add(account)
        db.flush()
        config = create_bot_config(db, user_id=user_id, payload=_make_bot_create_payload(name="Start Once"))
        db.flush()

        first = start_bot_run(db, user_id=user_id, bot_config_id=config.id, client=StubClient(), dry_run=True)
        db.commit()
        second = start_bot_run(db, user_id=user_id, bot_config_id=config.id, client=StubClient(), dry_run=True)
        db.commit()

        runs = db.query(BotRun).order_by(BotRun.started_at.asc(), BotRun.id.asc()).all()
        assert first.run is not None
        assert second.run is not None
        assert len(runs) == 2
        assert [run.status for run in runs] == ["stopped", "running"]
        assert runs[0].stop_reason == "superseded_by_manual_start"
        assert db.query(BotRun).filter(BotRun.status == "running").count() == 1
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=list(reversed(tables)))
        engine.dispose()


def test_stop_without_active_run_links_lifecycle_decision_to_created_run():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        BotConfig.__table__,
        BotRun.__table__,
        BotDecision.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    user_id = "00000000-0000-0000-0000-000000000000"
    try:
        config = BotConfig(
            user_id=user_id,
            account_id=9001,
            name="Stop Audit",
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

        run = stop_latest_bot_run(db, user_id=user_id, bot_config_id=config.id)
        db.commit()

        decision = db.query(BotDecision).one()
        assert run.id is not None
        assert decision.bot_run_id == run.id
        assert decision.decision_type == "lifecycle"
        assert decision.action == "STOP"
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=list(reversed(tables)))
        engine.dispose()
