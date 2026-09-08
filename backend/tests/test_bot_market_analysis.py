from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.bot_schemas import BotMarketAnalysisOut
from app.services.bot_market_analysis import (
    ANALYSIS_VERSION,
    PROBABILITY_METHOD,
    _candle_end,
    _execution_risk_score,
    _multi_timeframe_alignment,
    _normalize_rows,
    _setup_quality_score,
    build_market_analysis,
)
from app.services.bot_service import _candle_delivery_delay_seconds, _market_candle_close_timestamp, build_signal_trade_evaluation


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
        unit="minute",
        unit_number=5,
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


def test_setup_quality_and_execution_risk_measure_confluence_not_only_data_completeness():
    weak_choppy_setup = _setup_quality_score(
        data_confidence=100,
        trend_direction="bullish",
        trend_strength=15,
        regime="chop",
        mtf_status="bullish",
        volume_state="low",
        vwap_location="below",
        has_levels=True,
    )
    strong_aligned_setup = _setup_quality_score(
        data_confidence=100,
        trend_direction="bullish",
        trend_strength=80,
        regime="trend",
        mtf_status="bullish",
        volume_state="normal",
        vwap_location="above",
        has_levels=True,
    )
    weak_choppy_risk = _execution_risk_score(
        volatility_state="normal",
        volume_state="low",
        regime="chop",
        trend_strength=15,
        mtf_status="bullish",
        is_stale=False,
        gap_count=0,
    )

    assert weak_choppy_setup < 60
    assert strong_aligned_setup >= 80
    assert weak_choppy_risk >= 60


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


def test_high_atr_rank_and_cooling_ranges_are_explicitly_different_windows():
    candles = []
    for index in range(140):
        candle = _candle(index, 100)
        width = 1 if index < 120 else 30 if index < 134 else 5
        candle.high_price = 100 + width / 2
        candle.low_price = 100 - width / 2
        candles.append(candle)
    payload = _analyze(candles)
    volatility = payload["features"]["volatility"]

    assert volatility["percentile"] > 85
    assert volatility["recent_range_state"] == "cooling"
    assert volatility["state_basis"] == "recent_range_change"
    assert volatility["period"] == 14
    assert volatility["reference_observations"] == 100
    assert volatility["reference_window_end"] == payload["provenance"]["latest_candle_end_timestamp"]
    assert "separately from the ATR percentile" in " ".join(payload["reasoning"])
    assert "percentile (low)" not in " ".join(payload["reasoning"])


def test_unavailable_context_is_separate_from_candle_quality():
    payload = _analyze([_candle(index, 100 + index * 0.1) for index in range(420)])

    assert {"news_context", "macro_context", "cross_market_context", "level_1_quotes", "observed_volume_profile"} <= set(payload["data_quality"]["missing_inputs"])
    assert "candle-only" in payload["score_definitions"]["data_quality"]["missing_data"]
    assert "predictive confidence" in payload["score_definitions"]["data_quality"]["interpretation"]
    assert "%" not in payload["summary"]
    assert "conviction" not in payload["summary"]


def test_missing_and_zero_latest_volume_never_reuse_an_older_observation():
    candles = [_candle(index, 100, volume=100) for index in range(60)]
    candles[-1].volume = 0
    zero = _analyze(candles)
    assert zero["features"]["volume"] == {"relative_volume": 0.0, "state": "low"}

    candles[-1].volume = None
    missing = _analyze(candles)
    assert missing["features"]["volume"] == {"relative_volume": None, "state": "unavailable"}
    assert missing["features"]["vwap"]["value"] is None
    assert any("no confirmation" in value for value in missing["explanation"]["limitations"])


def test_missing_feature_history_is_unavailable_not_normal_or_confirmation():
    payload = _analyze([_candle(index, 100) for index in range(12)])
    assert payload["features"]["volatility"]["atr"] is None
    assert payload["features"]["volatility"]["state"] == "unavailable"
    assert payload["market_regime"] == "unknown"
    insufficient = _analyze([_candle(0, 100)])
    assert insufficient["features"]["trend"]["agreement"] == "unavailable"
    assert insufficient["features"]["volume"]["state"] == "unavailable"
    assert insufficient["explanation"]["supporting_evidence"] == []
    BotMarketAnalysisOut.model_validate(insufficient)


def test_candle_marked_complete_is_excluded_until_its_interval_ends():
    candles = [_candle(index, 100 + index * 0.1) for index in range(60)]
    end = candles[-1].candle_timestamp + timedelta(minutes=5)
    baseline = _analyze(candles, now=end)
    contaminated = _analyze([*candles, _candle(60, 10_000)], now=end + timedelta(minutes=1))

    assert contaminated["current_price"] == baseline["current_price"]
    assert contaminated["features"] == baseline["features"]
    assert contaminated["provenance"]["unconfirmed_closed_candle_count"] == 1
    assert contaminated["provenance"]["latest_candle_end_timestamp"] == end.isoformat()


def test_indicators_do_not_stitch_prices_across_contracts():
    old = [_candle(index, 10_000 + index) for index in range(30)]
    active = [_candle(index + 30, 100 + index * 0.1) for index in range(30)]
    for candle in old:
        candle.contract_id = "CON.F.US.MNQ.M26"
    for candle in active:
        candle.contract_id = "CON.F.US.MNQ.U26"

    baseline = _analyze(active)
    mixed = _analyze([*old, *active])
    assert mixed["features"] == baseline["features"]
    assert mixed["provenance"]["excluded_contract_candle_count"] == 30
    assert mixed["provenance"]["resolved_contract_id"] == "CON.F.US.MNQ.U26"


def test_market_closures_pause_candle_age_but_do_not_hide_prior_missing_bars():
    friday_close = datetime(2026, 7, 10, 21, 0, tzinfo=timezone.utc)
    candles = [_candle(index, 100 + index * 0.1) for index in range(30)]
    for index, candle in enumerate(candles):
        candle.candle_timestamp = friday_close - timedelta(minutes=(30 - index) * 5)
    saturday = datetime(2026, 7, 11, 15, tzinfo=timezone.utc)
    closed = _analyze(candles, now=saturday)
    assert closed["provenance"]["freshness_status"] == "market_closed"
    assert closed["provenance"]["open_session_age_seconds"] == 0
    assert closed["provenance"]["is_stale"] is False

    old = _analyze(candles[:-12], now=saturday)
    assert old["provenance"]["freshness_status"] == "stale"
    assert old["provenance"]["open_session_age_seconds"] == 3600


def test_quiet_interval_is_not_stale_before_another_bar_could_close():
    candles = [_candle(index, 100) for index in range(30)]
    end = candles[-1].candle_timestamp + timedelta(minutes=5)
    payload = build_market_analysis(candles=candles, timeframe_unit="minute", timeframe_unit_number=5,
        fast_period=5, slow_period=13, signal_action="HOLD", stale_after_seconds=30,
        now=end + timedelta(minutes=4))
    assert payload["provenance"]["is_stale"] is False
    stale = build_market_analysis(candles=candles, timeframe_unit="minute", timeframe_unit_number=5,
        fast_period=5, slow_period=13, signal_action="HOLD", stale_after_seconds=30,
        now=end + timedelta(minutes=6))
    assert stale["provenance"]["is_stale"] is True


def test_observed_vwap_is_scoped_to_available_globex_candles():
    payload = _analyze([_candle(index, 100 + index) for index in range(30)])
    vwap = payload["features"]["vwap"]
    assert vwap["scope"] == "observed_session_candles"
    assert vwap["complete_session"] is False
    assert vwap["window_start"] == BASE.isoformat()
    assert vwap["window_start"] != vwap["session_start"]
    assert any("strategy's RTH VWAP" in item for item in payload["explanation"]["limitations"])


def test_direction_strength_and_component_agreement_are_independent_of_bot_signal():
    candles = [_candle(index, 100 + index * 0.15) for index in range(60)]
    buy, sell, hold = [_analyze(candles, action=action) for action in ("BUY", "SELL", "HOLD")]
    for key in ("summary", "explanation", "features", "scenario_weights", "score_drivers", "invalidation_level"):
        assert buy[key] == sell[key] == hold[key]
    trend = hold["features"]["trend"]
    assert trend["direction"] == "bullish"
    assert trend["agreement"] == "aligned"
    assert trend["strength_label"] in {"weak", "moderate", "strong"}
    assert len(trend["components"]) == 3
    assert hold["features"]["multi_timeframe_alignment"]["timeframes"][0]["direction"] == trend["direction"]


def test_mismatched_timeframes_are_excluded_and_aggregate_close_times_are_available():
    candles = [_candle(index, 100 + index * 0.1) for index in range(420)]
    wrong_timeframe = _candle(419, 9000)
    wrong_timeframe.unit = "minute"
    wrong_timeframe.unit_number = 1
    payload = _analyze([*candles, wrong_timeframe])
    assert payload["current_price"] == candles[-1].close_price
    assert payload["provenance"]["excluded_timeframe_candle_count"] == 1
    frames = payload["features"]["multi_timeframe_alignment"]["timeframes"]
    assert len(frames) > 1
    assert frames[0]["fast_period"] == 5
    assert frames[0]["slow_period"] == 13
    assert frames[0]["latest_candle_end_timestamp"] == payload["provenance"]["latest_candle_end_timestamp"]
    assert all(datetime.fromisoformat(frame["latest_candle_end_timestamp"]) <= datetime.fromisoformat(payload["provenance"]["latest_candle_end_timestamp"]) for frame in frames)


def test_flat_or_outdated_higher_timeframes_do_not_confirm_a_direction():
    rows = _normalize_rows([_candle(index, 100) for index in range(420)])
    source_trend = {"direction": "bullish", "fast_period": 5, "slow_period": 13}
    alignment = _multi_timeframe_alignment(rows, source_unit="minute", source_number=5, base_trend=source_trend)
    assert alignment["status"] == "neutral"
    assert alignment["aligned_timeframes"] == 1

    # The only recent bar cannot form any higher-timeframe aggregate. Old
    # complete aggregates are not evidence about this newer snapshot.
    rows.append({**rows[-1], "timestamp": rows[-1]["timestamp"] + timedelta(days=2)})
    outdated = _multi_timeframe_alignment(rows, source_unit="minute", source_number=5, base_trend=source_trend)
    assert outdated["status"] == "unavailable"
    assert len(outdated["timeframes"]) == 1


def test_mixed_direction_evidence_supports_neutral_read_and_vwap_is_separate_counterpoint():
    closes = [100 + index * 0.5 for index in range(60)]
    closes[-4:] = [closes[-5] - index * 0.5 for index in range(1, 5)]
    payload = _analyze([_candle(index, close) for index, close in enumerate(closes)])

    assert payload["features"]["trend"]["direction"] == "neutral"
    assert payload["features"]["trend"]["agreement"] == "mixed"
    explanation = payload["explanation"]
    evidence = explanation["supporting_evidence"]
    assert any("Fast versus slow EMA: upward" in item for item in evidence)
    assert any("Price change per bar over five bars: downward" in item for item in evidence)
    assert any("Upward evidence:" in item and "VWAP" in item for item in explanation["conflicting_evidence"])
    assert not any("EMA" in item for item in explanation["conflicting_evidence"])
    assert "Check the" not in payload["summary"]


def test_a_snapshot_fetched_while_forming_does_not_become_closed_after_time_passes():
    candles = [_candle(index, 100 + index * 0.1) for index in range(60)]
    forming_snapshot = _candle(60, 9000)
    forming_snapshot.fetched_at = forming_snapshot.candle_timestamp + timedelta(minutes=2)
    now = forming_snapshot.candle_timestamp + timedelta(minutes=10)

    baseline = _analyze(candles, now=now)
    result = _analyze([*candles, forming_snapshot], now=now)
    assert result["features"] == baseline["features"]
    assert result["provenance"]["unconfirmed_closed_candle_count"] == 1
    assert any("fetch preceded the close" in item for item in result["data_quality"]["warnings"])


@pytest.mark.parametrize("opened, number, expected", [
    ("2026-09-01T00:00:00+00:00", 1, "2026-10-01T00:00:00+00:00"),
    ("2024-02-01T00:00:00+00:00", 1, "2024-03-01T00:00:00+00:00"),
    ("2026-02-01T00:00:00+00:00", 1, "2026-03-01T00:00:00+00:00"),
    ("2026-12-01T00:00:00+00:00", 1, "2027-01-01T00:00:00+00:00"),
    ("2026-11-01T00:00:00+00:00", 3, "2027-02-01T00:00:00+00:00"),
    ("2024-01-31T12:00:00+00:00", 1, "2024-02-01T12:00:00+00:00"),
])
def test_calendar_month_ends_match_execution_in_leap_years_and_year_rollovers(opened, number, expected):
    candle = _candle(0, 100)
    candle.candle_timestamp = datetime.fromisoformat(opened)
    candle.unit, candle.unit_number = "month", number
    actual = _candle_end(candle.candle_timestamp, "month", number)
    assert actual.isoformat() == expected
    assert _market_candle_close_timestamp(candle) == actual


def _monthly_analysis(candles, now):
    return build_market_analysis(candles=candles, timeframe_unit="month", timeframe_unit_number=1,
        fast_period=5, slow_period=13, signal_action="HOLD", stale_after_seconds=30,
        configured_symbol="MNQ", now=now)


def test_monthly_eligibility_and_reference_windows_use_actual_calendar_close():
    candles = []
    timestamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for index in range(17):
        candle = _candle(index, 100 + index)
        candle.candle_timestamp = timestamp
        candle.unit, candle.unit_number = "month", 1
        candles.append(candle)
        timestamp = _candle_end(timestamp, "month", 1)
    result = _monthly_analysis(candles, timestamp)
    assert result["provenance"]["closed_candle_count"] == 17
    assert result["provenance"]["gap_count"] == 0
    assert result["provenance"]["latest_candle_end_timestamp"] == timestamp.isoformat()
    assert result["features"]["volatility"]["reference_window_end"] == timestamp.isoformat()
    assert result["features"]["multi_timeframe_alignment"]["timeframes"][0]["latest_candle_end_timestamp"] == timestamp.isoformat()
    assert result["features"]["vwap"]["window_end"] == timestamp.isoformat()
    BotMarketAnalysisOut.model_validate(result)

    before_close = _monthly_analysis(candles, timestamp - timedelta(seconds=1))
    assert before_close["provenance"]["closed_candle_count"] == 16
    candles[-1].fetched_at = timestamp - timedelta(seconds=1)
    fetched_early = _monthly_analysis(candles, timestamp + timedelta(days=2))
    assert fetched_early["provenance"]["closed_candle_count"] == 16


def test_monthly_freshness_uses_next_calendar_boundary_on_the_open_session_clock():
    candle = _candle(0, 100)
    candle.candle_timestamp = datetime(2026, 9, 1, tzinfo=timezone.utc)
    candle.unit, candle.unit_number = "month", 1
    next_due = datetime(2026, 11, 1, tzinfo=timezone.utc)
    at_boundary = _monthly_analysis([candle], next_due)
    assert at_boundary["provenance"]["latest_candle_end_timestamp"] == "2026-10-01T00:00:00+00:00"
    assert at_boundary["provenance"]["next_expected_candle_end_timestamp"] == next_due.isoformat()
    assert at_boundary["provenance"]["is_stale"] is False
    assert _candle_delivery_delay_seconds(candle, symbol="MNQ", now=next_due) == 0
    # November 1 is Sunday; its closed hours do not consume delivery grace.
    after_reopen = datetime(2026, 11, 2, tzinfo=timezone.utc)
    stale = _monthly_analysis([candle], after_reopen)
    assert stale["provenance"]["is_stale"] is True
    assert _candle_delivery_delay_seconds(candle, symbol="MNQ", now=after_reopen) == 3600
