from datetime import datetime, timezone

from app.services.trade_plan_evaluator import MarketContext, TradePlan, TradePlanEvaluator


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
