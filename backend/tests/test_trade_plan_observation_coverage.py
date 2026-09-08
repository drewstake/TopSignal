from datetime import datetime, timedelta, timezone

from app.services.trade_plan_evaluator import (
    MarketContext, TradePlan, _trend_alignment, build_market_context_from_ohlcv,
)


BASE = datetime(2026, 7, 9, 12, tzinfo=timezone.utc)


def candles(count, minutes=5):
    return [dict(timestamp=BASE + timedelta(minutes=index * minutes),
                 open=100 + index, high=102 + index, low=99 + index,
                 close=101 + index, volume=100) for index in range(count)]


def test_missing_timeframes_are_not_neutral_votes_or_conflicts():
    plan = TradePlan("MNQ", "long", 100, 95, 110, 1, BASE)
    context = MarketContext(current_price=100, trend5m="bullish")
    score, aligned, conflicting, higher_conflict = _trend_alignment(plan, context)
    assert (score, aligned, conflicting, higher_conflict) == (15, 1, 0, False)
    assert context.trend15m == "unknown"


def test_advisory_uses_actual_complete_higher_timeframes():
    context = build_market_context_from_ohlcv(candles(80), timestamp=BASE + timedelta(minutes=400))
    assert context.trend5m == "bullish"
    assert context.trend15m == "bullish"
    assert context.trend1h == context.trend4h == "unknown"
    assert context.ema21_15m is not None
    assert context.ema21_1h is None
    assert context.ma200_5m is None


def test_non_five_minute_source_is_not_mislabeled_and_partial_buckets_are_excluded():
    rows = candles(42, minutes=1)
    context = build_market_context_from_ohlcv(rows, timestamp=BASE + timedelta(minutes=42),
                                             timeframe_unit="minute", timeframe_unit_number=1)
    assert context.atr5m is None  # Only eight complete five-minute bars.
    assert context.trend5m == context.trend15m == "unknown"
    hourly = build_market_context_from_ohlcv(candles(30, minutes=60),
                                            timestamp=BASE + timedelta(hours=30),
                                            timeframe_unit="hour", timeframe_unit_number=1)
    assert hourly.atr5m is None
    assert hourly.trend5m == "unknown"
    assert hourly.trend1h == "bullish"


def test_missing_base_bar_invalidates_higher_bucket_and_future_bar_cannot_change_price():
    rows = candles(42)
    complete = build_market_context_from_ohlcv(rows, timestamp=BASE + timedelta(minutes=210))
    missing = build_market_context_from_ohlcv(rows[1:], timestamp=BASE + timedelta(minutes=210))
    assert complete.trend15m == "bullish"
    assert missing.trend15m == "unknown"  # Thirteen complete bars cannot meet the 14-bar minimum.
    extra = dict(candles(43)[-1], close=100_000, high=100_001)
    future = build_market_context_from_ohlcv([*rows, extra], timestamp=BASE + timedelta(minutes=210))
    assert future.current_price == complete.current_price


def test_missing_volume_is_not_zero_or_a_partially_computed_vwap():
    rows = candles(30)
    rows[-1]["volume"] = None
    missing = build_market_context_from_ohlcv(rows, timestamp=BASE + timedelta(minutes=150))
    assert missing.current_volume is None
    assert missing.relative_volume is None
    assert missing.vwap is None
    rows[-1]["volume"] = 0
    zero = build_market_context_from_ohlcv(rows, timestamp=BASE + timedelta(minutes=150))
    assert zero.current_volume == 0
    assert zero.relative_volume == 0
    assert zero.vwap is not None
