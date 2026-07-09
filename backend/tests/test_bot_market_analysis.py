from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.bot_schemas import BotMarketAnalysisOut
from app.services.bot_market_analysis import (
    ANALYSIS_VERSION,
    PROBABILITY_METHOD,
    build_market_analysis,
)
from app.services.bot_service import build_signal_trade_evaluation


BASE = datetime(2026, 7, 9, 13, 30, tzinfo=timezone.utc)


def _candle(index: int, close: float, *, partial: bool = False, volume: float = 100) -> SimpleNamespace:
    return SimpleNamespace(
        candle_timestamp=BASE + timedelta(minutes=index * 5),
        open_price=close - 0.2,
        high_price=close + 0.6,
        low_price=close - 0.6,
        close_price=close,
        volume=volume,
        is_partial=partial,
    )


def _analyze(candles, *, action="HOLD", now=None):
    latest_closed = next((row for row in reversed(candles) if not row.is_partial), None)
    default_now = (
        latest_closed.candle_timestamp + timedelta(minutes=5)
        if latest_closed is not None
        else BASE + timedelta(hours=1)
    )
    return build_market_analysis(
        candles=candles,
        timeframe_unit="minute",
        timeframe_unit_number=5,
        fast_period=5,
        slow_period=13,
        signal_action=action,
        stale_after_seconds=600,
        now=now or default_now,
    )


def _weight_total(payload):
    weights = payload["scenario_weights"]
    return weights["bullish"] + weights["bearish"] + weights["sideways"]


def test_canonical_contract_is_versioned_schema_valid_and_weights_total_100():
    candles = [_candle(index, 100 + index, volume=180 if index == 39 else 100) for index in range(40)]

    payload = _analyze(candles, action="BUY")

    assert payload["analysis_version"] == ANALYSIS_VERSION == "market_analysis_v2"
    assert payload["probability_method"] == PROBABILITY_METHOD == "heuristic_scenario_weight"
    assert _weight_total(payload) == 100
    assert all(isinstance(value, int) for value in payload["scenario_weights"].values())
    assert payload["bullish_probability"] == payload["scenario_weights"]["bullish"]
    assert payload["bearish_probability"] == payload["scenario_weights"]["bearish"]
    assert payload["sideways_probability"] == payload["scenario_weights"]["sideways"]
    assert payload["trend"] == "bullish"
    assert payload["market_regime"] == "trend"
    assert payload["features"]["vwap"]["location"] == "above"
    assert payload["features"]["volume"]["relative_volume"] == 1.8
    assert payload["score_drivers"]["bullish"]
    assert BotMarketAnalysisOut.model_validate(payload).analysis_version == ANALYSIS_VERSION

    payload["scenario_weights"] = {"bullish": 50, "bearish": 20, "sideways": 20}
    with pytest.raises(ValueError, match="scenario weights must total 100"):
        BotMarketAnalysisOut.model_validate(payload)


def test_partial_bar_never_changes_any_closed_bar_feature():
    closed = [_candle(index, 100 + index * 0.25) for index in range(60)]
    partial = _candle(60, 10_000, partial=True, volume=1_000_000)
    now = closed[-1].candle_timestamp + timedelta(minutes=5)

    baseline = _analyze(closed, now=now)
    with_partial = _analyze([*closed, partial], now=now)

    for key in [
        "current_price",
        "trend",
        "trend_strength",
        "market_regime",
        "scenario_weights",
        "expected_move",
        "support_levels",
        "resistance_levels",
        "features",
    ]:
        assert with_partial[key] == baseline[key]
    assert with_partial["provenance"]["closed_candle_count"] == 60
    assert with_partial["provenance"]["partial_candle_count"] == 1
    assert any("closed bars only" in warning for warning in with_partial["data_quality"]["warnings"])


def test_partial_only_input_is_insufficient_and_is_not_used_as_price():
    candles = [_candle(index, 9_000 + index, partial=True) for index in range(12)]

    payload = _analyze(candles)

    assert payload["current_price"] is None
    assert payload["candle_timestamp"] is None
    assert payload["trend"] == "neutral"
    assert payload["data_quality"]["status"] == "insufficient"
    assert payload["provenance"]["closed_candle_count"] == 0
    assert payload["provenance"]["partial_candle_count"] == 12
    assert _weight_total(payload) == 100
    assert any("not substituted" in warning for warning in payload["data_quality"]["warnings"])


def test_stale_insufficient_and_gap_quality_are_explicit():
    insufficient = _analyze([_candle(index, 100 + index * 0.1) for index in range(3)])
    with_gap = [
        _candle(index if index < 15 else index + 2, 100 + index * 0.2)
        for index in range(35)
    ]
    stale_now = with_gap[-1].candle_timestamp + timedelta(hours=2)
    stale = _analyze(with_gap, now=stale_now)

    assert insufficient["data_quality"]["status"] == "insufficient"
    assert "at_least_25_closed_candles" in insufficient["data_quality"]["missing_inputs"]
    assert stale["provenance"]["is_stale"] is True
    assert stale["data_quality"]["status"] == "stale"
    assert stale["provenance"]["gap_count"] == 1
    assert stale["provenance"]["detected_gaps"][0]["missing_bars"] == 2


def test_trend_and_range_regime_boundaries_are_deterministic():
    rising = [_candle(index, 100 + index * 0.5) for index in range(60)]
    falling = [_candle(index, 130 - index * 0.5) for index in range(60)]
    flat = [_candle(index, 100 + (0.01 if index % 2 else -0.01)) for index in range(60)]

    bullish = _analyze(rising)
    bearish = _analyze(falling)
    ranging = _analyze(flat)

    assert bullish["trend"] == "bullish"
    assert bullish["market_regime"] == "trend"
    assert bearish["trend"] == "bearish"
    assert bearish["market_regime"] == "trend"
    assert ranging["trend"] == "neutral"
    assert ranging["market_regime"] == "range"
    assert 40 <= ranging["features"]["volatility"]["percentile"] <= 60


def test_multi_timeframe_alignment_uses_only_complete_closed_aggregates():
    closed = [_candle(index, 100 + index * 0.5) for index in range(420)]
    partial = _candle(420, 1, partial=True, volume=1_000_000)
    now = closed[-1].candle_timestamp + timedelta(minutes=5)

    baseline = _analyze(closed, now=now)
    contaminated = _analyze([*closed, partial], now=now)
    alignment = baseline["features"]["multi_timeframe_alignment"]

    assert alignment == contaminated["features"]["multi_timeframe_alignment"]
    assert alignment["status"] == "bullish"
    assert len(alignment["timeframes"]) >= 2
    assert alignment["conflicting_timeframes"] == 0


def _signal_trade_evaluation(candles, *, stop=99.0, target=102.0, current_day_pnl=None):
    config = SimpleNamespace(
        symbol="MNQ",
        contract_id="CON.F.US.MNQ.M26",
        order_size=1,
        max_daily_loss=250,
        strategy_type="sma_cross",
    )
    signal = SimpleNamespace(
        action="BUY",
        raw_payload={"entry_price": 100.0, "stop_loss": stop, "take_profit": target},
        price=100.0,
        candle_timestamp=candles[-1].candle_timestamp if candles else BASE,
    )
    analysis = {"trend": "bullish", "trend_strength": 80, "risk_notes": []}
    result = build_signal_trade_evaluation(
        candles=candles,
        config=config,
        signal=signal,
        analysis=analysis,
        current_day_pnl=current_day_pnl,
        tick_size=0.25,
        tick_value=0.5,
        point_value=2.0,
    )
    return result, analysis


def test_signal_trade_evaluation_uses_real_context_and_never_fabricates_news():
    candles = [_candle(index, 98 + index * (2 / 29)) for index in range(30)]

    result, _ = _signal_trade_evaluation(candles, current_day_pnl=-240)

    assert result is not None
    assert result["features"]["estimated_dollar_risk"] == 2.0
    assert result["features"]["projected_day_pnl"] == -242.0
    assert result["features"]["daily_loss_danger"] is True
    assert "market_context.news_risk" in result["missing_inputs"]
    assert result["decision"] == "avoid"


def test_signal_trade_evaluation_refuses_partial_only_context_and_is_advisory_on_bad_geometry():
    partials = [_candle(index, 100 + index, partial=True) for index in range(10)]
    assert _signal_trade_evaluation(partials)[0] is None

    closed = [_candle(index, 100) for index in range(30)]
    result, analysis = _signal_trade_evaluation(closed, stop=101.0)

    assert result is None
    assert any("Invalid trade geometry" in note for note in analysis["risk_notes"])
