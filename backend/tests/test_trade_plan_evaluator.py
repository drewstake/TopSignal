from datetime import datetime, timezone

import pytest

from app.services.trade_plan_evaluator import MarketContext, TradePlan, TradePlanEvaluator
from app.trade_plan_schemas import MarketContextIn, TradeEvaluationResultOut, TradePlanIn


def _plan(
    *,
    direction: str = "long",
    entry: float = 100.0,
    stop: float = 96.0,
    target: float = 110.0,
) -> TradePlan:
    return TradePlan(
        symbol="MNQ",
        direction=direction,
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
        quantity=1,
        timestamp=datetime(2026, 4, 1, 14, 30, tzinfo=timezone.utc),
        account_balance=50_000,
        current_day_pnl=0,
        max_daily_loss=1_000,
    )


def _context(
    *,
    current_price: float = 100.0,
    high_of_day: float = 113.0,
    low_of_day: float = 92.0,
    vwap: float = 98.0,
    atr5m: float = 3.0,
    trend5m: str = "bullish",
    trend15m: str = "bullish",
    trend1h: str = "bullish",
    trend4h: str = "bullish",
    market_regime: str = "trend",
    news_risk: str = "low",
    time_of_day: str = "ny_am",
) -> MarketContext:
    return MarketContext(
        current_price=current_price,
        high_of_day=high_of_day,
        low_of_day=low_of_day,
        previous_day_high=116.0,
        previous_day_low=90.0,
        previous_close=97.0,
        open_price=96.0,
        vwap=vwap,
        value_area_high=112.0,
        value_area_low=94.0,
        ema21_5m=99.0,
        ema21_15m=98.5,
        ema21_1h=98.0,
        ema21_4h=97.0,
        atr5m=atr5m,
        current_day_range=high_of_day - low_of_day,
        current_volume=1_200,
        average_volume_at_time=1_000,
        relative_volume=1.2,
        trend5m=trend5m,
        trend15m=trend15m,
        trend1h=trend1h,
        trend4h=trend4h,
        market_regime=market_regime,
        news_risk=news_risk,
        time_of_day=time_of_day,
    )


def _evaluate(plan: TradePlan, context: MarketContext):
    return TradePlanEvaluator().evaluate(plan, context)


def test_strong_trend_continuation_trade_scores_take():
    result = _evaluate(_plan(), _context())

    assert result.total_score >= 85
    assert result.grade == "A"
    assert result.decision == "take"
    assert result.confidence == "high"
    assert result.features.risk_reward_ratio == 2.5
    assert result.features.vwap_supports_direction is True
    assert result.features.trend_alignment_score == 100


def test_bad_chasing_trade_waits_and_warns():
    result = _evaluate(
        _plan(entry=112.0, stop=110.0, target=118.0),
        _context(
            current_price=112.0,
            high_of_day=112.5,
            vwap=100.0,
            trend1h="bearish",
            trend4h="bearish",
            market_regime="unknown",
        ),
    )

    assert result.features.entry_chasing is True
    assert result.features.bad_location is True
    assert result.decision in {"wait", "avoid"}
    assert any("chasing" in warning for warning in result.warnings)


def test_trade_with_bad_risk_reward_is_avoided():
    result = _evaluate(_plan(entry=100.0, stop=95.0, target=102.0), _context())

    assert result.features.risk_reward_ratio == 0.4
    assert result.decision == "avoid"
    assert result.total_score < 55
    assert any("below 1.0R" in warning for warning in result.warnings)


def test_long_below_vwap_waits():
    result = _evaluate(
        _plan(entry=98.0, stop=95.0, target=104.0),
        _context(current_price=98.0, vwap=100.0),
    )

    assert result.features.vwap_supports_direction is False
    assert result.features.bad_location is True
    assert result.decision == "wait"
    assert any("VWAP" in warning for warning in result.warnings)


def test_short_above_vwap_waits():
    result = _evaluate(
        _plan(direction="short", entry=102.0, stop=105.0, target=96.0),
        _context(
            current_price=102.0,
            high_of_day=108.0,
            low_of_day=92.0,
            vwap=100.0,
            trend5m="bearish",
            trend15m="bearish",
            trend1h="bearish",
            trend4h="bearish",
        ),
    )

    assert result.features.vwap_supports_direction is False
    assert result.features.bad_location is True
    assert result.decision == "wait"
    assert any("VWAP" in warning for warning in result.warnings)


def test_stop_too_tight_warns():
    result = _evaluate(_plan(entry=100.0, stop=99.0, target=108.0), _context(atr5m=3.0))

    assert result.features.is_stop_too_tight is True
    assert result.features.stop_atr_multiple == 1 / 3
    assert any("less than 0.5 ATR" in warning for warning in result.warnings)


def test_target_unrealistic_warns_outside_breakout_regime():
    result = _evaluate(
        _plan(entry=100.0, stop=97.0, target=116.0),
        _context(high_of_day=120.0, atr5m=3.0, market_regime="range"),
    )

    assert result.features.target_atr_multiple > 4
    assert result.features.is_target_realistic is False
    assert any("more than 4 ATR" in warning for warning in result.warnings)


def test_chop_regime_penalizes_otherwise_good_trade():
    trend_result = _evaluate(_plan(), _context(market_regime="trend"))
    chop_result = _evaluate(_plan(), _context(market_regime="chop"))

    assert chop_result.total_score < trend_result.total_score
    assert chop_result.decision in {"wait", "avoid"}
    assert any("chop" in warning.lower() for warning in chop_result.warnings)


def test_high_news_risk_forces_avoid():
    result = _evaluate(_plan(), _context(news_risk="high"))

    assert result.decision == "avoid"
    assert result.category_scores["account_news_penalty"] == 0
    assert any("News risk is high" in warning for warning in result.warnings)


def test_known_instrument_prices_are_tick_normalized_and_risk_uses_point_value():
    plan = _plan(entry=100.12, stop=96.12, target=110.12)
    plan = TradePlan(**{**plan.__dict__, "quantity": 2})

    result = _evaluate(plan, _context())

    assert result.features.tick_size == 0.25
    assert result.features.tick_value == 0.5
    assert result.features.point_value == 2.0
    assert result.features.normalized_entry_price == 100.0
    assert result.features.normalized_stop_loss == 96.0
    assert result.features.normalized_take_profit == 110.0
    assert result.features.prices_normalized_to_tick is True
    assert result.features.risk_points == 4.0
    assert result.features.reward_points == 10.0
    assert result.features.risk_ticks == 16.0
    assert result.features.reward_ticks == 40.0
    assert result.features.r_multiple == 2.5
    assert result.features.estimated_dollar_risk == 16.0
    assert result.features.estimated_dollar_reward == 40.0
    assert result.features.breakeven_win_rate == pytest.approx(28.57142857)


def test_explicit_instrument_values_remain_internally_consistent():
    point_value_result = _evaluate(
        TradePlan(**{**_plan().__dict__, "point_value": 10.0}),
        _context(),
    )
    tick_size_result = _evaluate(
        TradePlan(**{**_plan().__dict__, "tick_size": 0.5}),
        _context(),
    )

    assert point_value_result.features.tick_size == 0.25
    assert point_value_result.features.tick_value == 2.5
    assert point_value_result.features.point_value == 10.0
    assert point_value_result.features.estimated_dollar_risk == 40.0
    assert tick_size_result.features.tick_size == 0.5
    assert tick_size_result.features.tick_value == 1.0
    assert tick_size_result.features.point_value == 2.0
    assert tick_size_result.features.estimated_dollar_risk == 8.0


def test_conflicting_explicit_instrument_values_are_rejected():
    plan = TradePlan(
        **{
            **_plan().__dict__,
            "tick_size": 0.25,
            "tick_value": 0.5,
            "point_value": 10.0,
        }
    )

    with pytest.raises(ValueError, match="tick_value must equal tick_size"):
        _evaluate(plan, _context())


def test_input_schemas_reject_non_finite_prices():
    with pytest.raises(ValueError):
        TradePlanIn(
            symbol="MNQ",
            direction="long",
            entry_price=float("inf"),
            stop_loss=96,
            take_profit=108,
            timestamp=datetime(2026, 4, 1, 14, 30, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError):
        MarketContextIn(current_price=float("inf"))


@pytest.mark.parametrize(
    ("direction", "stop", "target", "expected"),
    [
        ("long", 101.0, 110.0, "Long stop must be below entry"),
        ("long", 95.0, 99.0, "Long target must be above entry"),
        ("short", 99.0, 90.0, "Short stop must be above entry"),
        ("short", 105.0, 101.0, "Short target must be below entry"),
    ],
)
def test_invalid_directional_geometry_is_rejected(direction, stop, target, expected):
    with pytest.raises(ValueError, match=expected):
        _evaluate(
            _plan(direction=direction, entry=100.0, stop=stop, target=target),
            _context(),
        )


def test_tick_normalization_cannot_collapse_stop_onto_entry():
    with pytest.raises(ValueError, match="after tick normalization"):
        _evaluate(
            _plan(entry=100.12, stop=100.11, target=101.0),
            _context(),
        )


def test_long_and_short_mirrors_have_symmetric_scores():
    long_context = _context(
        current_price=100.0,
        high_of_day=113.0,
        low_of_day=92.0,
        vwap=98.0,
    )
    short_context = MarketContext(
        current_price=100.0,
        high_of_day=108.0,
        low_of_day=87.0,
        previous_day_high=110.0,
        previous_day_low=84.0,
        previous_close=103.0,
        open_price=104.0,
        vwap=102.0,
        value_area_high=106.0,
        value_area_low=88.0,
        ema21_5m=101.0,
        ema21_15m=101.5,
        ema21_1h=102.0,
        ema21_4h=103.0,
        atr5m=3.0,
        current_day_range=21.0,
        current_volume=1_200,
        average_volume_at_time=1_000,
        relative_volume=1.2,
        trend5m="bearish",
        trend15m="bearish",
        trend1h="bearish",
        trend4h="bearish",
        market_regime="trend",
        news_risk="low",
        time_of_day="ny_am",
    )

    long_result = _evaluate(_plan(entry=100.0, stop=96.0, target=108.0), long_context)
    short_result = _evaluate(
        _plan(direction="short", entry=100.0, stop=104.0, target=92.0),
        short_context,
    )

    assert long_result.features.risk_points == short_result.features.risk_points
    assert long_result.features.reward_points == short_result.features.reward_points
    assert long_result.features.trend_alignment_score == short_result.features.trend_alignment_score
    assert long_result.category_scores == short_result.category_scores
    assert long_result.total_score == short_result.total_score


def test_missing_account_and_news_context_is_not_fabricated():
    plan = TradePlan(
        symbol="UNKNOWN",
        direction="long",
        entry_price=100,
        stop_loss=96,
        take_profit=108,
        quantity=1,
        timestamp=datetime(2026, 4, 1, 14, 30, tzinfo=timezone.utc),
    )
    context = _context(news_risk="unknown")

    result = _evaluate(plan, context)

    assert result.features.tick_size is None
    assert result.features.point_value is None
    assert result.features.estimated_dollar_risk is None
    assert result.features.account_risk_percent is None
    assert result.features.projected_day_pnl is None
    assert result.features.daily_loss_danger is None
    assert result.features.drawdown_danger is None
    assert result.features.should_reduce_size is None
    assert result.category_scores["account_news_penalty"] == 3
    assert "market_context.news_risk" in result.missing_inputs
    assert "trade_plan.account_balance" in result.missing_inputs
    assert "trade_plan.current_day_pnl" in result.missing_inputs
    assert "trade_plan.max_daily_loss" in result.missing_inputs
    assert "trade_plan.trailing_drawdown" in result.missing_inputs
    assert result.data_confidence == "low"
    assert result.decision != "take"
    insufficient_data_cap = next(cap for cap in result.caps if cap.code == "insufficient_data")
    assert insufficient_data_cap.maximum == 69
    assert result.total_score <= insufficient_data_cap.maximum
    assert insufficient_data_cap.reason in result.top_negative_drivers


def test_daily_limit_is_not_evaluated_without_current_day_pnl():
    plan = TradePlan(
        symbol="MNQ",
        direction="long",
        entry_price=100,
        stop_loss=96,
        take_profit=108,
        quantity=1,
        timestamp=datetime(2026, 4, 1, 14, 30, tzinfo=timezone.utc),
        account_balance=50_000,
        max_daily_loss=100,
    )

    result = _evaluate(plan, _context())

    assert result.features.estimated_dollar_risk == 8.0
    assert result.features.projected_day_pnl is None
    assert result.features.daily_loss_remaining_before_trade is None
    assert result.features.daily_loss_remaining_after_trade is None
    assert result.features.daily_loss_danger is None
    assert "trade_plan.current_day_pnl" in result.missing_inputs


def test_drawdown_danger_forces_avoid_and_exposes_cap():
    plan = TradePlan(
        **{
            **_plan(entry=100.0, stop=96.0, target=108.0).__dict__,
            "trailing_drawdown": 8.0,
        }
    )

    result = _evaluate(plan, _context())

    assert result.features.estimated_dollar_risk == 8.0
    assert result.features.drawdown_risk_percent == 100.0
    assert result.features.drawdown_danger is True
    assert result.decision == "avoid"
    drawdown_cap = next(cap for cap in result.caps if cap.code == "drawdown_danger")
    assert drawdown_cap.maximum == 54
    assert result.total_score <= drawdown_cap.maximum


def test_scoring_contract_is_versioned_auditable_and_schema_valid():
    result = _evaluate(_plan(), _context(market_regime="chop"))
    payload = result.to_payload()

    assert result.scoring_model_version == "trade_plan_v2.0.0"
    assert sum(result.category_maximums.values()) == 100
    assert result.category_scores == payload["category_awarded_points"]
    assert all(
        0 <= result.category_scores[name] <= maximum
        for name, maximum in result.category_maximums.items()
    )
    assert result.penalties
    assert result.top_positive_drivers
    assert result.top_negative_drivers
    chop_cap = next(cap for cap in result.caps if cap.code == "chop_regime")
    assert chop_cap.maximum == 69
    assert result.total_score <= 69
    assert set(result.evaluation_dimensions) == {
        "setup_quality",
        "market_direction_bias",
        "execution_risk",
    }
    assert TradeEvaluationResultOut.model_validate(payload).total_score == result.total_score
