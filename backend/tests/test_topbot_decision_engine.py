import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import BotConfig, ProjectXMarketCandle
import app.services.bot_service as bot_service_module
from app.services.bot_service import SignalResult, TopBotMarketContext, evaluate_topbot_adaptive_strategy


def _make_candle(timestamp: datetime, close: float, **overrides) -> ProjectXMarketCandle:
    values = {
        "user_id": "00000000-0000-0000-0000-000000000000",
        "contract_id": "CON.F.US.MNQ.M26",
        "symbol": "MNQ",
        "live": False,
        "unit": "minute",
        "unit_number": 5,
        "candle_timestamp": timestamp,
        "open_price": close - 0.25,
        "high_price": close + 0.75,
        "low_price": close - 0.75,
        "close_price": close,
        "volume": 100,
        "is_partial": False,
    }
    values.update(overrides)
    return ProjectXMarketCandle(**values)


def _rising_candles(now: datetime) -> list[ProjectXMarketCandle]:
    base = now - timedelta(minutes=5 * 39)
    return [
        _make_candle(
            base + timedelta(minutes=5 * index),
            100 + index * 0.6,
            volume=180 if index >= 36 else 100,
        )
        for index in range(40)
    ]


def _flat_candles(now: datetime) -> list[ProjectXMarketCandle]:
    base = now - timedelta(minutes=5 * 39)
    closes = [100.0, 100.15, 99.9, 100.1, 99.95] * 8
    return [
        _make_candle(base + timedelta(minutes=5 * index), close, volume=100)
        for index, close in enumerate(closes)
    ]


def _config(**overrides) -> BotConfig:
    values = {
        "user_id": "00000000-0000-0000-0000-000000000000",
        "account_id": 9001,
        "name": "TopBot Adaptive",
        "enabled": True,
        "execution_mode": "dry_run",
        "strategy_type": "topbot_adaptive",
        "strategy_params": {},
        "contract_id": "CON.F.US.MNQ.M26",
        "symbol": "MNQ",
        "timeframe_unit": "minute",
        "timeframe_unit_number": 5,
        "lookback_bars": 200,
        "fast_period": 9,
        "slow_period": 21,
        "order_size": 1,
        "max_contracts": 1,
        "max_daily_loss": 250,
        "max_trades_per_day": 3,
        "max_open_position": 1,
        "allowed_contracts": ["CON.F.US.MNQ.M26"],
        "trading_start_time": "09:30",
        "trading_end_time": "15:45",
        "cooldown_seconds": 0,
        "max_data_staleness_seconds": 600,
    }
    values.update(overrides)
    return BotConfig(**values)


def _signal(action: str, price: float, *, stop: float | None = None, target: float | None = None) -> SignalResult:
    payload = {"strategy_type": "test_source", "entry_price": price}
    if stop is not None:
        payload["stop_loss"] = stop
    if target is not None:
        payload["take_profit"] = target
        risk = abs(price - stop) if stop is not None else None
        if risk and risk > 0:
            payload["risk"] = risk
            payload["reward_r_multiple"] = abs(target - price) / risk
    return SignalResult(
        action=action,
        reason=f"{action} test vote",
        candle_timestamp=datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc),
        price=price,
        raw_payload=payload,
    )


def test_topbot_adaptive_strong_confluence_creates_buy_plan():
    now = datetime(2026, 6, 15, 14, 35, tzinfo=timezone.utc)
    candles = _rising_candles(now)
    price = float(candles[-1].close_price)
    signals = [
        ("ema_trend_pullback", _signal("BUY", price, stop=price - 2, target=price + 4)),
        ("relative_strength_spy", _signal("BUY", price, stop=price - 2, target=price + 4)),
        ("donchian_breakout", _signal("BUY", price, stop=price - 2, target=price + 4)),
    ]

    result = evaluate_topbot_adaptive_strategy(candles, strategy_signals=signals, config=_config(), now=now)

    payload = result.raw_payload["topbot_adaptive"]
    assert result.action == "BUY"
    assert payload["long_score"] >= 70
    assert payload["grade"] in {"A", "B"}
    assert result.raw_payload["stop_loss"] == price - 2
    assert result.raw_payload["take_profit"] == price + 4
    assert result.raw_payload["trailing_stop"]["enabled"] is False
    assert result.raw_payload["break_even"]["enabled"] is True
    assert result.raw_payload["time_stop"]["enabled"] is True


def test_topbot_adaptive_conflicting_signals_hold():
    now = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)
    candles = _flat_candles(now)
    price = float(candles[-1].close_price)
    signals = [
        ("ema_scalping", _signal("BUY", price, stop=price - 1, target=price + 2)),
        ("support_resistance", _signal("BUY", price, stop=price - 1, target=price + 2)),
        ("bollinger_rsi_reversal", _signal("SELL", price, stop=price + 1, target=price - 2)),
        ("vwap_atr_mean_reversion", _signal("SELL", price, stop=price + 1, target=price - 2)),
    ]

    result = evaluate_topbot_adaptive_strategy(candles, strategy_signals=signals, config=_config(), now=now)

    assert result.action == "HOLD"
    rejections = result.raw_payload["topbot_adaptive"]["rejection_reasons"]
    assert any("Opposing strategy votes" in reason or "conflicted" in reason for reason in rejections)


def test_topbot_adaptive_requires_extra_confirmation_during_early_session():
    now = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)
    candles = _rising_candles(now)
    price = float(candles[-1].close_price)
    signals = [
        ("ema_trend_pullback", _signal("BUY", price, stop=price - 2, target=price + 4)),
        ("donchian_breakout", _signal("BUY", price, stop=price - 2, target=price + 4)),
    ]

    result = evaluate_topbot_adaptive_strategy(candles, strategy_signals=signals, config=_config(), now=now)

    assert result.action == "HOLD"
    rejections = result.raw_payload["topbot_adaptive"]["rejection_reasons"]
    assert any("Early-session" in reason for reason in rejections)


def test_topbot_adaptive_blocks_entries_outside_preferred_session_window():
    now = datetime(2026, 6, 15, 17, 0, tzinfo=timezone.utc)
    candles = _rising_candles(now)
    price = float(candles[-1].close_price)
    signals = [
        ("ema_trend_pullback", _signal("BUY", price, stop=price - 2, target=price + 4)),
        ("relative_strength_spy", _signal("BUY", price, stop=price - 2, target=price + 4)),
        ("donchian_breakout", _signal("BUY", price, stop=price - 2, target=price + 4)),
    ]

    result = evaluate_topbot_adaptive_strategy(candles, strategy_signals=signals, config=_config(), now=now)

    assert result.action == "HOLD"
    rejections = result.raw_payload["topbot_adaptive"]["rejection_reasons"]
    assert any("empirical session filter" in reason for reason in rejections)


def test_topbot_adaptive_blocks_short_when_regime_is_unknown(monkeypatch):
    now = datetime(2026, 6, 15, 14, 45, tzinfo=timezone.utc)
    candles = _rising_candles(now)
    price = float(candles[-1].close_price)

    def fake_market_context(*_args, **_kwargs):
        return TopBotMarketContext(
            latest_price=price,
            candle_timestamp=now,
            trend="bearish",
            trend_strength=50,
            volatility_state="normal",
            volume_state="normal",
            market_regime="unknown",
            atr=1.0,
            vwap=price + 1,
            nearest_support=None,
            nearest_resistance=None,
            active_fvg_count=0,
            session_timing="ny_am",
            warnings=[],
        )

    monkeypatch.setattr(bot_service_module, "_build_topbot_market_context", fake_market_context)
    signals = [
        ("ema_trend_pullback", _signal("SELL", price, stop=price + 2, target=price - 4)),
        ("relative_strength_spy", _signal("SELL", price, stop=price + 2, target=price - 4)),
        ("donchian_breakout", _signal("SELL", price, stop=price + 2, target=price - 4)),
    ]

    result = evaluate_topbot_adaptive_strategy(candles, strategy_signals=signals, config=_config(), now=now)

    assert result.action == "HOLD"
    rejections = result.raw_payload["topbot_adaptive"]["rejection_reasons"]
    assert any("blocks SELL entries while regime is unknown" in reason for reason in rejections)


def test_topbot_adaptive_requires_stronger_long_continuation_plan():
    now = datetime(2026, 6, 15, 15, 35, tzinfo=timezone.utc)
    candles = _rising_candles(now)
    price = float(candles[-1].close_price)
    signals = [
        ("ema_trend_pullback", _signal("BUY", price, stop=price - 2, target=price + 3.4)),
        ("relative_strength_spy", _signal("BUY", price, stop=price - 2, target=price + 3.4)),
        ("donchian_breakout", _signal("BUY", price, stop=price - 2, target=price + 3.4)),
    ]

    result = evaluate_topbot_adaptive_strategy(candles, strategy_signals=signals, config=_config(), now=now)

    assert result.action == "HOLD"
    rejections = result.raw_payload["topbot_adaptive"]["rejection_reasons"]
    assert any("Long" in reason and "reward/risk" in reason for reason in rejections)


def test_topbot_adaptive_low_reward_risk_is_rejected():
    now = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)
    candles = _rising_candles(now)
    price = float(candles[-1].close_price)
    signals = [
        ("ema_trend_pullback", _signal("BUY", price, stop=price - 2, target=price + 2.4)),
        ("relative_strength_spy", _signal("BUY", price, stop=price - 2, target=price + 2.4)),
        ("donchian_breakout", _signal("BUY", price, stop=price - 2, target=price + 2.4)),
    ]

    result = evaluate_topbot_adaptive_strategy(candles, strategy_signals=signals, config=_config(), now=now)

    assert result.action == "RISK_REJECT"
    assert "Reward/risk" in result.reason


def test_topbot_adaptive_missing_stop_is_rejected_after_score_threshold():
    now = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)
    candles = _rising_candles(now)
    price = float(candles[-1].close_price)
    signals = [
        ("ema_trend_pullback", _signal("BUY", price, target=price + 4)),
        ("relative_strength_spy", _signal("BUY", price, target=price + 4)),
        ("donchian_breakout", _signal("BUY", price, target=price + 4)),
    ]

    result = evaluate_topbot_adaptive_strategy(
        candles,
        strategy_signals=signals,
        strategy_params={"minimum_score": 50, "minimum_confidence": 40},
        config=_config(),
        now=now,
    )

    assert result.action == "RISK_REJECT"
    assert "complete entry, stop, and target" in result.reason


def test_topbot_adaptive_no_or_stale_candles_hold():
    now = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)
    no_candles = evaluate_topbot_adaptive_strategy(
        [],
        strategy_signals=[("ema_trend_pullback", _signal("BUY", 100, stop=98, target=104))],
        config=_config(),
        now=now,
    )
    assert no_candles.action == "HOLD"

    stale_candles = _rising_candles(now - timedelta(hours=2))
    stale = evaluate_topbot_adaptive_strategy(
        stale_candles,
        strategy_signals=[("ema_trend_pullback", _signal("BUY", 100, stop=98, target=104))],
        config=_config(max_data_staleness_seconds=60),
        now=now,
    )
    assert stale.action == "HOLD"
    assert "stale" in stale.reason


def test_topbot_adaptive_expired_contract_warns_or_blocks():
    now = datetime(2026, 7, 1, 14, 0, tzinfo=timezone.utc)
    candles = _rising_candles(now)
    price = float(candles[-1].close_price)
    signals = [
        ("ema_trend_pullback", _signal("BUY", price, stop=price - 2, target=price + 4)),
        ("relative_strength_spy", _signal("BUY", price, stop=price - 2, target=price + 4)),
        ("donchian_breakout", _signal("BUY", price, stop=price - 2, target=price + 4)),
    ]

    warning_result = evaluate_topbot_adaptive_strategy(candles, strategy_signals=signals, config=_config(), now=now)
    warnings = warning_result.raw_payload["topbot_adaptive"]["market_context"]["warnings"]
    assert any("expired" in warning for warning in warnings)

    blocked_result = evaluate_topbot_adaptive_strategy(
        candles,
        strategy_signals=signals,
        strategy_params={"block_expired_contracts": True},
        config=_config(),
        now=now,
    )
    assert blocked_result.action == "RISK_REJECT"
