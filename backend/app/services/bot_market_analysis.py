from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

from .trading_day import futures_session_is_open


ANALYSIS_VERSION = "market_analysis_v2"
PROBABILITY_METHOD = "heuristic_scenario_weight"
MIN_FEATURE_BARS = 10
MIN_SUFFICIENT_BARS = 25
_EPSILON = 1e-9
_NEW_YORK = ZoneInfo("America/New_York")
_UNIT_SECONDS = {
    "second": 1,
    "minute": 60,
    "hour": 60 * 60,
    "day": 24 * 60 * 60,
    "week": 7 * 24 * 60 * 60,
    "month": 31 * 24 * 60 * 60,
}
_TIMEFRAME_LADDER = [
    ("minute", 1, "1m"),
    ("minute", 5, "5m"),
    ("minute", 15, "15m"),
    ("hour", 1, "1H"),
    ("hour", 4, "4H"),
    ("day", 1, "1D"),
]


def build_market_analysis(
    *,
    candles: Sequence[Any],
    timeframe_unit: str,
    timeframe_unit_number: int,
    fast_period: int,
    slow_period: int,
    signal_action: str,
    stale_after_seconds: int,
    now: datetime | None = None,
    configured_contract_id: str | None = None,
    configured_symbol: str | None = None,
) -> dict[str, Any]:
    """Build the canonical, deterministic closed-bar market-analysis payload."""

    generated_at = _as_utc(now or datetime.now(timezone.utc))
    interval_seconds = _interval_seconds(timeframe_unit, timeframe_unit_number)
    rows = _normalize_rows(candles)
    invalid_count = len(candles) - len(rows)
    aligned_rows = [row for row in rows if
        (row.get("unit") is None or row["unit"] == str(timeframe_unit).lower()) and
        (row.get("unit_number") is None or row["unit_number"] == max(1, int(timeframe_unit_number)))]
    excluded_timeframe_count = len(rows) - len(aligned_rows)
    rows = aligned_rows
    def unconfirmed(row: dict[str, Any]) -> bool:
        candle_end = _candle_end(row["timestamp"], timeframe_unit, timeframe_unit_number)
        return candle_end > generated_at or (row.get("fetched_at") is not None and row["fetched_at"] < candle_end)

    unconfirmed_count = sum(
        1 for row in rows
        if not row["is_partial"] and unconfirmed(row)
    )
    closed = [
        row for row in rows
        if not row["is_partial"] and not unconfirmed(row)
    ]
    partial_count = sum(1 for row in rows if row["is_partial"]) + unconfirmed_count
    # The newest identified contract is the resolved input (including rollovers).
    # Never stitch prices from an expiring contract into its successor's indicators.
    identified = [row for row in closed if row.get("contract_id")]
    input_contract = identified[-1]["contract_id"] if identified else None
    excluded_contract_count = 0
    if input_contract:
        aligned = [row for row in closed if row.get("contract_id") == input_contract]
        excluded_contract_count = len(closed) - len(aligned)
        closed = aligned
    timeframe = {
        "unit": timeframe_unit,
        "unit_number": max(1, int(timeframe_unit_number)),
        "label": _timeframe_label(timeframe_unit, timeframe_unit_number),
    }
    latest_timestamp = closed[-1]["timestamp"] if closed else None
    resolved_contract_id = closed[-1].get("contract_id") if closed else None
    resolved_symbol = closed[-1].get("symbol") if closed else None
    normalized_configured_contract_id = _optional_text(configured_contract_id)
    normalized_configured_symbol = _optional_text(configured_symbol)
    resolved_contract_id = _optional_text(resolved_contract_id) or normalized_configured_contract_id
    resolved_symbol = _optional_text(resolved_symbol) or normalized_configured_symbol
    gaps = _detect_gaps(closed, interval_seconds, symbol=resolved_symbol, unit=timeframe_unit, number=timeframe_unit_number)
    latest_end = _candle_end(latest_timestamp, timeframe_unit, timeframe_unit_number) if latest_timestamp else None
    data_age_seconds = (
        max(0, int((generated_at - latest_end).total_seconds()))
        if latest_end is not None
        else None
    )
    stale_limit = max(1, int(stale_after_seconds))
    market_session_open = futures_session_is_open(generated_at, symbol=resolved_symbol)
    open_age = _open_session_seconds(latest_end, generated_at, symbol=resolved_symbol) if latest_end else None
    freshness_interval = _candle_expected_interval_seconds(latest_end, timeframe_unit, timeframe_unit_number, symbol=resolved_symbol) if latest_end else interval_seconds
    # A quiet quote stream is not a missing closed candle. Wait until another
    # whole interval could have closed, plus the configured delivery grace.
    is_stale = open_age is not None and open_age > freshness_interval + stale_limit
    freshness_status = "unavailable" if latest_end is None else "stale" if is_stale else "fresh" if market_session_open else "market_closed"
    provenance = {
        "closed_candle_count": len(closed),
        "partial_candle_count": partial_count,
        "latest_candle_timestamp": latest_timestamp.isoformat() if latest_timestamp else None,
        "latest_candle_end_timestamp": latest_end.isoformat() if latest_end else None,
        "market_session_open": market_session_open,
        "freshness_status": freshness_status,
        "open_session_age_seconds": open_age,
        "freshness_interval_seconds": freshness_interval,
        "next_expected_candle_end_timestamp": _candle_end(latest_end, timeframe_unit, timeframe_unit_number).isoformat() if latest_end and str(timeframe_unit).lower() == "month" else None,
        "excluded_contract_candle_count": excluded_contract_count,
        "excluded_timeframe_candle_count": excluded_timeframe_count,
        "unconfirmed_closed_candle_count": unconfirmed_count,
        "invalid_candle_count": max(0, invalid_count),
        "data_age_seconds": data_age_seconds,
        "is_stale": is_stale,
        "stale_after_seconds": stale_limit,
        "timeframe": timeframe,
        "detected_gaps": gaps,
        "gap_count": len(gaps),
        "configured_contract_id": normalized_configured_contract_id,
        "resolved_contract_id": resolved_contract_id,
        "resolved_symbol": resolved_symbol,
        "contract_rollover": bool(
            normalized_configured_contract_id
            and resolved_contract_id
            and normalized_configured_contract_id != resolved_contract_id
        ),
        "minimum_feature_bars": MIN_FEATURE_BARS,
        "minimum_sufficient_bars": MIN_SUFFICIENT_BARS,
    }

    missing_inputs = _base_missing_inputs(closed)
    warnings: list[str] = []
    if partial_count:
        warnings.append(
            f"Excluded {partial_count} partial candle{'s' if partial_count != 1 else ''}; all features use closed bars only."
        )
    if not closed and partial_count:
        warnings.append("No closed candles were available; partial candles were not substituted.")
    if is_stale:
        warnings.append(f"No new closed candle for {open_age} scheduled-open seconds; this exceeds one {timeframe['label']} interval plus the {stale_limit}-second delivery grace.")
    if not market_session_open:
        warnings.append("The scheduled futures market is closed; closure time does not age closed-candle freshness. The displayed read is historical until trading resumes.")
    if unconfirmed_count:
        warnings.append(f"Excluded {unconfirmed_count} candle(s) marked complete whose interval had not ended at evaluation time or whose recorded fetch preceded the close.")
    if excluded_contract_count:
        warnings.append(f"Excluded {excluded_contract_count} candle(s) with a different or unidentified contract; indicators use {input_contract} only.")
    if excluded_timeframe_count:
        warnings.append(f"Excluded {excluded_timeframe_count} candle(s) whose recorded timeframe differs from {timeframe['label']}.")
    if invalid_count:
        warnings.append(f"Excluded {invalid_count} invalid or duplicate candle record(s).")
    if gaps:
        warnings.append(f"Detected {len(gaps)} candle gap{'s' if len(gaps) != 1 else ''} in the supplied history.")
    if provenance["contract_rollover"]:
        warnings.append(
            f"Evaluation resolved configured contract {normalized_configured_contract_id} to active contract {resolved_contract_id}."
        )

    if len(closed) < MIN_FEATURE_BARS:
        return _insufficient_payload(
            closed=closed,
            provenance=provenance,
            generated_at=generated_at,
            missing_inputs=missing_inputs,
            warnings=warnings,
        )

    current = closed[-1]
    previous = closed[-2]
    current_price = current["close"]
    previous_close = previous["close"]
    price_change = current_price - previous_close
    price_change_percent = _percent_change(current_price, previous_close)
    closes = [row["close"] for row in closed]
    true_ranges = _true_ranges(closed)
    atr_period = 14
    atr = _average(true_ranges[-atr_period:]) if len(true_ranges) >= atr_period else None
    atr_percent = atr / abs(current_price) * 100 if atr is not None and abs(current_price) > _EPSILON else None
    atr_percentile = _atr_percentile(true_ranges, period=atr_period, lookback=100)
    volatility_state = _volatility_state(true_ranges)
    recent_range_ratio = _recent_range_ratio(true_ranges)
    recent_range_state = _recent_range_state(recent_range_ratio)
    if atr is None:
        missing_inputs.append("atr_history")
    if atr_percentile is None:
        missing_inputs.append("atr_percentile_history")

    relative_volume = _relative_volume(closed)
    volume_state = _volume_state(relative_volume)
    if relative_volume is None:
        missing_inputs.append("relative_volume_baseline")

    requested_fast = max(2, int(fast_period or 9))
    requested_slow = max(requested_fast + 1, int(slow_period or 21))
    effective_fast = min(requested_fast, max(2, len(closes) - 1))
    effective_slow = min(requested_slow, len(closes))
    trend = _trend_read(closes, true_ranges, fast_period=effective_fast, slow_period=effective_slow)
    vwap = _session_vwap(closed)
    vwap_provenance = _vwap_provenance(closed, interval_seconds, unit=timeframe_unit, number=timeframe_unit_number)
    vwap_location = _vwap_location(current_price, vwap, atr)
    if vwap is None:
        missing_inputs.append("session_vwap")

    mtf = _multi_timeframe_alignment(
        closed,
        source_unit=timeframe_unit,
        source_number=timeframe_unit_number,
        base_trend=trend,
    )
    if mtf["status"] == "unavailable":
        missing_inputs.append("multi_timeframe_history")

    supports, resistances = _support_resistance(closed, current_price=current_price)
    nearest_support = supports[0] if supports else None
    nearest_resistance = resistances[0] if resistances else None
    if nearest_support is None:
        missing_inputs.append("nearby_support")
    if nearest_resistance is None:
        missing_inputs.append("nearby_resistance")

    regime = _market_regime(
        trend_direction=trend["direction"],
        trend_strength=trend["strength"],
        volatility_state=volatility_state,
        mtf_status=mtf["status"],
        volume_state=volume_state,
    )
    drivers = _score_drivers(
        trend=trend,
        regime=regime,
        volatility_state=volatility_state,
        relative_volume=relative_volume,
        volume_state=volume_state,
        vwap_location=vwap_location,
        mtf=mtf,
        signal_action=signal_action,
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
    )
    weights = _scenario_weights(
        trend=trend,
        regime=regime,
        volatility_state=volatility_state,
        volume_state=volume_state,
        vwap_location=vwap_location,
        mtf_status=mtf["status"],
        signal_action=signal_action,
        current_price=current_price,
        expected_move=atr,
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
    )
    invalidation = _invalidation_level(
        trend_direction=trend["direction"],
        signal_action=signal_action,
        current_price=current_price,
        expected_move=atr,
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
    )

    missing_inputs = _dedupe(missing_inputs)
    data_quality = _data_quality(
        closed_count=len(closed),
        partial_count=partial_count,
        is_stale=is_stale,
        gap_count=len(gaps),
        missing_inputs=missing_inputs,
        warnings=warnings,
    )
    setup_score = _setup_quality_score(
        data_confidence=data_quality["confidence"],
        trend_direction=trend["direction"],
        trend_strength=trend["strength"],
        regime=regime,
        mtf_status=mtf["status"],
        volume_state=volume_state,
        vwap_location=vwap_location,
        has_levels=nearest_support is not None and nearest_resistance is not None,
    )
    execution_risk_score = _execution_risk_score(
        volatility_state=volatility_state,
        volume_state=volume_state,
        regime=regime,
        trend_strength=trend["strength"],
        mtf_status=mtf["status"],
        is_stale=is_stale,
        gap_count=len(gaps),
    )
    # Descriptive evidence is independent of strategy, session and risk decisions.
    bias_direction = trend["direction"]

    reasoning = [
        (
            f"Latest closed price is {_fmt(current_price)} versus {_fmt(previous_close)} "
            f"({_fmt_signed(price_change_percent)}%)."
        ),
        (
            f"EMA({effective_fast}) is {_fmt(trend['fast_ema'])}; EMA({effective_slow}) is "
            f"{_fmt(trend['slow_ema'])}; movement strength is {trend['strength_label']} and the three directional components are {trend['agreement']}."
        ),
        (
            f"ATR({atr_period}) is {_fmt(atr)} at the "
            f"{_fmt(atr_percentile, digits=0)}th percentile of up to 100 rolling ATR(14) observations."
        ),
        f"Last six bars' average true range / preceding 28 bars is {_fmt_ratio(recent_range_ratio)} ({recent_range_state}); this measures recent change, separately from the ATR percentile.",
        f"Relative volume is {_fmt_ratio(relative_volume)} ({volume_state}); price is {vwap_location} VWAP for the observed portion of the futures session.",
        f"Multi-timeframe alignment is {mtf['status']} across {len(mtf['timeframes'])} available timeframe(s).",
        _level_reasoning(nearest_support, nearest_resistance),
    ]
    risk_notes = [
        "Scenario weights are deterministic heuristics, not calibrated predictions or financial advice.",
        *warnings,
        "This descriptive read uses candles only. Separate decision observations are checked for timing and contract eligibility.",
    ]
    if volatility_state in {"elevated", "extreme"}:
        risk_notes.append(f"Volatility is {volatility_state}; execution slippage and level failure risk can be higher.")
    if volume_state == "low":
        risk_notes.append("Low relative volume reduces confidence in directional follow-through.")
    if regime == "chop":
        risk_notes.append("Choppy price action increases false-break and whipsaw risk.")
    if trend["strength"] < 25:
        risk_notes.append("Directional trend strength is weak, so follow-through conviction is limited.")
    if mtf["status"] == "mixed":
        risk_notes.append("Timeframe trends conflict, increasing directional execution risk.")
    summary = _analysis_summary(
        bias_direction=bias_direction,
        trend_strength=trend["strength"],
        regime=regime,
        weights=weights,
        volume_state=volume_state,
        vwap_location=vwap_location,
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
    )
    explanation = _explanation(trend=trend, regime=regime, drivers=drivers, provenance=provenance,
        nearest_support=nearest_support, nearest_resistance=nearest_resistance, volume_state=volume_state,
        mtf=mtf, vwap_provenance=vwap_provenance)

    return {
        "analysis_version": ANALYSIS_VERSION,
        "probability_method": PROBABILITY_METHOD,
        "scenario_weights": weights,
        "provenance": provenance,
        "data_quality": data_quality,
        "explanation": explanation,
        "score_definitions": _score_definitions(provenance, trend),
        "market_regime": regime,
        "features": {
            "trend": trend,
            "volatility": {
                "atr": _round(atr),
                "atr_percent": _round(atr_percent),
                "percentile": _round(atr_percentile),
                "state": volatility_state,
                "state_basis": "recent_range_change",
                "period": atr_period,
                "reference_observations": min(100, max(0, len(true_ranges) - atr_period + 1)),
                "reference_window_start": _candle_end(closed[max(atr_period - 1, len(closed) - 100)]["timestamp"], timeframe_unit, timeframe_unit_number).isoformat() if atr is not None else None,
                "reference_window_end": latest_end.isoformat() if atr is not None else None,
                "recent_range_ratio": _round(recent_range_ratio),
                "recent_range_state": recent_range_state,
            },
            "volume": {"relative_volume": _round(relative_volume), "state": volume_state},
            "vwap": {"value": _round(vwap), "location": vwap_location, **vwap_provenance},
            "multi_timeframe_alignment": mtf,
            "nearby_levels": {"support": nearest_support, "resistance": nearest_resistance},
        },
        "score_drivers": drivers,
        "setup_quality": {
            "score": setup_score,
            "label": _quality_label(setup_score),
            "drivers": _dedupe([*drivers["neutral"], *drivers[bias_direction]])[:4],
        },
        "market_bias": {
            "direction": bias_direction,
            "strength": trend["strength"],
            "drivers": drivers[bias_direction] if bias_direction in drivers else drivers["neutral"],
        },
        "execution_risk": {
            "risk_score": execution_risk_score,
            "label": _risk_label(execution_risk_score),
            "drivers": _dedupe(risk_notes[1:])[:4],
        },
        "data_confidence": {
            "score": data_quality["confidence"],
            "label": data_quality["status"],
            "drivers": [*warnings, *[f"Missing: {item}" for item in missing_inputs]][:6],
        },
        # Legacy compatibility fields.
        "current_price": _round(current_price),
        "previous_close": _round(previous_close),
        "price_change": _round(price_change),
        "price_change_percent": _round(price_change_percent),
        "trend": trend["direction"],
        "trend_strength": trend["strength"],
        "volatility_state": volatility_state,
        "volume_state": volume_state,
        "support_levels": supports,
        "resistance_levels": resistances,
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        "bullish_probability": weights["bullish"],
        "bearish_probability": weights["bearish"],
        "sideways_probability": weights["sideways"],
        "expected_move": _round(atr),
        "expected_move_percent": _round(atr_percent),
        "invalidation_level": invalidation,
        "summary": summary,
        "reasoning": reasoning,
        "risk_notes": _dedupe(risk_notes),
        "candle_timestamp": latest_timestamp.isoformat() if latest_timestamp else None,
        "generated_at": generated_at.isoformat(),
    }


def _insufficient_payload(
    *,
    closed: list[dict[str, Any]],
    provenance: dict[str, Any],
    generated_at: datetime,
    missing_inputs: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    current_price = closed[-1]["close"] if closed else None
    previous_close = closed[-2]["close"] if len(closed) >= 2 else None
    price_change = current_price - previous_close if current_price is not None and previous_close is not None else None
    price_change_percent = (
        _percent_change(current_price, previous_close)
        if current_price is not None and previous_close is not None
        else None
    )
    missing = _dedupe([*missing_inputs, "trend_history", "atr_history", "multi_timeframe_history"])
    warnings = _dedupe(
        [
            *warnings,
            f"Only {len(closed)} closed candle(s) were available; at least {MIN_FEATURE_BARS} are needed for a reliable heuristic read.",
        ]
    )
    weights = {"bullish": 33, "bearish": 33, "sideways": 34}
    confidence = max(0, min(35, len(closed) * 3))
    data_quality = {
        "status": "stale" if provenance["is_stale"] else "insufficient",
        "confidence": confidence,
        "missing_inputs": missing,
        "warnings": warnings,
    }
    neutral_drivers = ["Insufficient closed-candle history for a directional feature set."]
    return {
        "analysis_version": ANALYSIS_VERSION,
        "probability_method": PROBABILITY_METHOD,
        "scenario_weights": weights,
        "provenance": provenance,
        "data_quality": data_quality,
        "market_regime": "unknown",
        "explanation": {
            "headline": "There is not enough closed-candle history to interpret the market.",
            "supporting_evidence": [], "conflicting_evidence": [], "change_levels": [],
            "limitations": warnings,
            "scope": "Descriptive candle analysis; unavailable features are not neutral evidence and this is not the bot's entry decision.",
        },
        "score_definitions": _score_definitions(provenance, None),
        "features": {
            "trend": {"direction": "neutral", "strength": 0, "strength_label": "unavailable", "agreement": "unavailable", "components": [], "fast_ema": None, "slow_ema": None, "slow_ema_slope": None},
            "volatility": {"atr": None, "atr_percent": None, "percentile": None, "state": "unavailable", "state_basis": "recent_range_change", "period": 14, "reference_observations": 0, "recent_range_ratio": None, "recent_range_state": "unavailable"},
            "volume": {"relative_volume": None, "state": "unavailable"},
            "vwap": {"value": None, "location": "unavailable", "scope": "observed_session_candles", "complete_session": False},
            "multi_timeframe_alignment": {
                "status": "unavailable",
                "aligned_timeframes": 0,
                "conflicting_timeframes": 0,
                "timeframes": [],
            },
            "nearby_levels": {"support": None, "resistance": None},
        },
        "score_drivers": {"bullish": [], "bearish": [], "neutral": neutral_drivers},
        "setup_quality": {"score": confidence, "label": "weak", "drivers": neutral_drivers},
        "market_bias": {"direction": "neutral", "strength": 0, "drivers": neutral_drivers},
        "execution_risk": {"risk_score": 100 - confidence, "label": "high", "drivers": warnings},
        "data_confidence": {"score": confidence, "label": data_quality["status"], "drivers": warnings},
        "current_price": _round(current_price),
        "previous_close": _round(previous_close),
        "price_change": _round(price_change),
        "price_change_percent": _round(price_change_percent),
        "trend": "neutral",
        "trend_strength": 0,
        "volatility_state": "unavailable",
        "volume_state": "unavailable",
        "support_levels": [],
        "resistance_levels": [],
        "nearest_support": None,
        "nearest_resistance": None,
        "bullish_probability": 33,
        "bearish_probability": 33,
        "sideways_probability": 34,
        "expected_move": None,
        "expected_move_percent": None,
        "invalidation_level": None,
        "summary": "There is not enough closed-candle history to interpret the market. Missing features provide no directional evidence.",
        "reasoning": neutral_drivers,
        "risk_notes": _dedupe([
            "Scenario weights are deterministic heuristics, not calibrated predictions or financial advice.",
            *warnings,
            "This descriptive read uses candles only. Separate decision observations are checked for timing and contract eligibility.",
        ]),
        "candle_timestamp": closed[-1]["timestamp"].isoformat() if closed else None,
        "generated_at": generated_at.isoformat(),
    }


def _normalize_rows(candles: Sequence[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candle in candles:
        try:
            timestamp = _as_utc(_value(candle, "candle_timestamp", "timestamp"))
            open_price = float(_value(candle, "open_price", "open"))
            high_price = float(_value(candle, "high_price", "high"))
            low_price = float(_value(candle, "low_price", "low"))
            close_price = float(_value(candle, "close_price", "close"))
            raw_volume = _value(candle, "volume", default=None)
            volume = float(raw_volume) if raw_volume is not None else None
            is_partial = bool(_value(candle, "is_partial", default=False))
            unit = _optional_text(_value(candle, "unit", default=None))
            unit_number = _value(candle, "unit_number", default=None)
            unit_number = int(unit_number) if unit_number is not None else None
            fetched_at = _value(candle, "fetched_at", default=None)
            fetched_at = _as_utc(fetched_at) if fetched_at is not None else None
        except (TypeError, ValueError, OverflowError):
            continue
        values = [open_price, high_price, low_price, close_price]
        if not all(math.isfinite(value) for value in values) or high_price < max(open_price, close_price, low_price) or low_price > min(open_price, close_price):
            continue
        if volume is not None and (not math.isfinite(volume) or volume < 0):
            volume = None
        rows.append(
            {
                "timestamp": timestamp,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
                "is_partial": is_partial,
                "contract_id": _optional_text(_value(candle, "contract_id", default=None)),
                "symbol": _optional_text(_value(candle, "symbol", default=None)),
                "unit": unit.lower() if unit else None,
                "unit_number": unit_number,
                "fetched_at": fetched_at,
            }
        )
    by_timestamp: dict[tuple[datetime, str | None, str | None, int | None], dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: item["timestamp"]):
        key = (row["timestamp"], row["contract_id"], row["unit"], row["unit_number"])
        existing = by_timestamp.get(key)
        if existing is not None and not existing["is_partial"] and row["is_partial"]:
            continue
        by_timestamp[key] = row
    return sorted(by_timestamp.values(), key=lambda item: item["timestamp"])


def _value(candle: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(candle, dict) and name in candle:
            return candle[name]
        if hasattr(candle, name):
            return getattr(candle, name)
    return default


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_utc(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("candle timestamp must be a datetime or ISO string")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _interval_seconds(unit: str, number: int) -> int:
    return max(1, int(number)) * _UNIT_SECONDS.get(str(unit).lower(), 60)


def _candle_end(timestamp: datetime, unit: str, number: int) -> datetime:
    """Return the provider candle's end, matching the execution calendar.

    Monthly provider bars close at the first day of the subsequent month (or
    configured month multiple), preserving the recorded UTC time of day.
    Fixed-duration units retain their existing interval arithmetic.
    """
    opened_at = _as_utc(timestamp)
    if str(unit).strip().lower() == "month":
        month_index = opened_at.year * 12 + opened_at.month - 1 + max(1, int(number))
        return opened_at.replace(year=month_index // 12, month=month_index % 12 + 1, day=1)
    return opened_at + timedelta(seconds=_interval_seconds(unit, number))


def _candle_expected_interval_seconds(closed_at: datetime, unit: str, number: int, *, symbol: str | None) -> int:
    if str(unit).strip().lower() == "month":
        # The next close is a calendar boundary. Count its scheduled-open
        # duration on the same clock as data age; using 31 wall days would
        # incorrectly grant several extra weekends of delivery grace.
        return _open_session_seconds(closed_at, _candle_end(closed_at, unit, number), symbol=symbol)
    return _interval_seconds(unit, number)


def _timeframe_label(unit: str, number: int) -> str:
    normalized = str(unit).lower()
    value = max(1, int(number))
    for ladder_unit, ladder_number, label in _TIMEFRAME_LADDER:
        if normalized == ladder_unit and value == ladder_number:
            return label
    suffix = {"second": "s", "minute": "m", "hour": "H", "day": "D", "week": "W", "month": "M"}
    return f"{value}{suffix.get(normalized, normalized[:1])}"


def _open_session_seconds(start: datetime, end: datetime, *, symbol: str | None) -> int:
    """Count elapsed scheduled-open time using the existing product calendar.

    Calendar boundaries (including historical halts) lie on quarter hours.
    This also handles daylight-saving transitions using UTC boundaries.
    """
    cursor = start
    total = 0.0
    while cursor < end:
        boundary = datetime.fromtimestamp((int(cursor.timestamp()) // 900 + 1) * 900, timezone.utc)
        finish = min(end, boundary)
        if futures_session_is_open(cursor, symbol=symbol):
            total += (finish - cursor).total_seconds()
        cursor = finish
    return max(0, int(total))


def _detect_gaps(rows: list[dict[str, Any]], interval_seconds: int, *, symbol: str | None = None, unit: str | None = None, number: int = 1) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for previous, current in zip(rows, rows[1:]):
        if str(unit).lower() == "month":
            cursor = _candle_end(previous["timestamp"], "month", number)
            missing = 0
            while cursor < current["timestamp"]:
                missing += 1
                cursor = _candle_end(cursor, "month", number)
        else:
            elapsed = int((current["timestamp"] - previous["timestamp"]).total_seconds())
            raw_missing = max(0, int(round(elapsed / interval_seconds)) - 1)
            if raw_missing <= 0:
                continue
            missing = sum(
                1
                for index in range(1, raw_missing + 1)
                if futures_session_is_open(previous["timestamp"] + timedelta(seconds=interval_seconds * index), symbol=symbol)
            )
        if missing <= 0:
            continue
        gaps.append(
            {
                "after_timestamp": previous["timestamp"].isoformat(),
                "before_timestamp": current["timestamp"].isoformat(),
                "missing_bars": missing,
            }
        )
    return gaps[:20]


def _true_ranges(rows: list[dict[str, Any]]) -> list[float]:
    output: list[float] = []
    previous_close: float | None = None
    for row in rows:
        high, low = row["high"], row["low"]
        value = high - low if previous_close is None else max(high - low, abs(high - previous_close), abs(low - previous_close))
        output.append(max(0.0, value))
        previous_close = row["close"]
    return output


def _atr_percentile(true_ranges: list[float], *, period: int, lookback: int) -> float | None:
    if period <= 0 or len(true_ranges) < period + 1:
        return None
    observations = [
        _average(true_ranges[end - period : end])
        for end in range(period, len(true_ranges) + 1)
    ][-max(2, lookback):]
    finite = [value for value in observations if value is not None and math.isfinite(value)]
    if len(finite) < 2:
        return None
    latest = finite[-1]
    less = sum(1 for value in finite if value < latest and not math.isclose(value, latest))
    equal = sum(1 for value in finite if math.isclose(value, latest))
    # Mid-rank ties keep a flat ATR series near the 50th percentile instead of
    # incorrectly classifying every tied latest observation as the maximum.
    return (less + equal * 0.5) / len(finite) * 100


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    multiplier = 2 / (max(1, period) + 1)
    output = [values[0]]
    for value in values[1:]:
        output.append(value * multiplier + output[-1] * (1 - multiplier))
    return output


def _trend_read(
    closes: list[float],
    true_ranges: list[float],
    *,
    fast_period: int,
    slow_period: int,
) -> dict[str, Any]:
    fast_series = _ema(closes, fast_period)
    slow_series = _ema(closes, slow_period)
    fast = fast_series[-1]
    slow = slow_series[-1]
    prior_slow = slow_series[max(0, len(slow_series) - 6)]
    slope = slow - prior_slow
    recent_lookback = min(5, len(closes) - 1)
    recent_delta = closes[-1] - closes[-1 - recent_lookback]
    atr = _average(true_ranges[-min(14, len(true_ranges)):]) or max(abs(closes[-1]) * 0.001, _EPSILON)
    gap_units = (fast - slow) / atr
    slope_units = slope / atr
    recent_units = recent_delta / (atr * max(1, recent_lookback))
    signs = [
        _sign(gap_units, 0.05),
        _sign(slope_units, 0.03),
        _sign(recent_units, 0.05),
    ]
    strength = int(round(min(100, abs(gap_units) * 30 + abs(slope_units) * 25 + abs(recent_units) * 35)))
    direction_score = sum(signs)
    direction = "neutral" if strength < 15 or abs(direction_score) < 2 else ("bullish" if direction_score > 0 else "bearish")
    return {
        "direction": direction,
        "strength": strength,
        "strength_label": "weak" if strength < 30 else "moderate" if strength < 65 else "strong",
        "agreement": "mixed" if 1 in signs and -1 in signs else "flat" if not any(signs) else "aligned" if all(sign == signs[0] for sign in signs) else "mixed",
        "components": [
            {"label": label, "direction": "bullish" if sign > 0 else "bearish" if sign < 0 else "neutral", "value": _round(value)}
            for label, sign, value in zip(
                ["Fast versus slow EMA", "Slow EMA change over five bars", "Price change per bar over five bars"],
                signs, [gap_units, slope_units, recent_units],
            )
        ],
        "fast_period": fast_period,
        "slow_period": slow_period,
        "fast_ema": _round(fast),
        "slow_ema": _round(slow),
        "slow_ema_slope": _round(slope),
    }


def _sign(value: float, threshold: float) -> int:
    return 1 if value > threshold else -1 if value < -threshold else 0


def _relative_volume(rows: list[dict[str, Any]]) -> float | None:
    if len(rows) < 6 or rows[-1]["volume"] is None:
        return None
    baseline = [row["volume"] for row in rows[:-1][-20:]]
    if any(value is None for value in baseline):
        return None
    average = _average(baseline)
    return rows[-1]["volume"] / average if average is not None and average > _EPSILON else None


def _session_vwap(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    latest_local = rows[-1]["timestamp"].astimezone(_NEW_YORK)
    start_date = latest_local.date() if latest_local.time() >= time(18, 0) else latest_local.date() - timedelta(days=1)
    session_start = datetime.combine(start_date, time(18, 0), tzinfo=_NEW_YORK).astimezone(timezone.utc)
    session_rows = [row for row in rows if row["timestamp"] >= session_start]
    if any(row["volume"] is None for row in session_rows):
        return None
    total_volume = sum(row["volume"] for row in session_rows)
    if total_volume <= _EPSILON:
        return None
    return sum(((row["high"] + row["low"] + row["close"]) / 3) * row["volume"] for row in session_rows) / total_volume


def _vwap_provenance(rows: list[dict[str, Any]], interval_seconds: int, *, unit: str | None = None, number: int = 1) -> dict[str, Any]:
    latest_local = rows[-1]["timestamp"].astimezone(_NEW_YORK)
    start_date = latest_local.date() if latest_local.time() >= time(18) else latest_local.date() - timedelta(days=1)
    start = datetime.combine(start_date, time(18), tzinfo=_NEW_YORK).astimezone(timezone.utc)
    observed = [row for row in rows if row["timestamp"] >= start]
    return {
        "scope": "observed_session_candles",
        "window_start": observed[0]["timestamp"].isoformat() if observed else None,
        "window_end": (_candle_end(observed[-1]["timestamp"], unit, number) if unit else observed[-1]["timestamp"] + timedelta(seconds=interval_seconds)).isoformat() if observed else None,
        "session_start": start.isoformat(),
        "complete_session": bool(observed and observed[0]["timestamp"] == start and
            all(row["volume"] is not None for row in observed) and
            not _detect_gaps(observed, interval_seconds, symbol=rows[-1].get("symbol"), unit=unit, number=number)),
    }


def _vwap_location(price: float, vwap: float | None, atr: float | None) -> str:
    if vwap is None:
        return "unavailable"
    tolerance = max((atr or 0) * 0.05, abs(price) * 0.00005)
    return "above" if price > vwap + tolerance else "below" if price < vwap - tolerance else "at"


def _aggregate(rows: list[dict[str, Any]], source_seconds: int, target_seconds: int) -> list[dict[str, Any]]:
    ratio = target_seconds // source_seconds
    if target_seconds <= source_seconds or ratio <= 1 or target_seconds % source_seconds:
        return []
    buckets: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        timestamp = int(row["timestamp"].timestamp())
        buckets.setdefault(timestamp // target_seconds * target_seconds, []).append(row)
    output: list[dict[str, Any]] = []
    for bucket_start, bucket_rows in sorted(buckets.items()):
        ordered = sorted(bucket_rows, key=lambda row: row["timestamp"])
        expected = [bucket_start + index * source_seconds for index in range(ratio)]
        actual = [int(row["timestamp"].timestamp()) for row in ordered]
        if actual != expected:
            continue
        output.append(
            {
                "timestamp": datetime.fromtimestamp(bucket_start, timezone.utc),
                "open": ordered[0]["open"],
                "high": max(row["high"] for row in ordered),
                "low": min(row["low"] for row in ordered),
                "close": ordered[-1]["close"],
                "volume": sum(row["volume"] for row in ordered) if all(row["volume"] is not None for row in ordered) else None,
                "is_partial": False,
                "contract_id": ordered[-1].get("contract_id"),
                "symbol": ordered[-1].get("symbol"),
            }
        )
    return output


def _multi_timeframe_alignment(rows: list[dict[str, Any]], *, source_unit: str, source_number: int, base_trend: dict[str, Any] | None = None) -> dict[str, Any]:
    source_seconds = _interval_seconds(source_unit, source_number)
    candidates = [(unit, number, label) for unit, number, label in _TIMEFRAME_LADDER if _interval_seconds(unit, number) > source_seconds and _interval_seconds(unit, number) % source_seconds == 0][:2]
    reads: list[dict[str, Any]] = []
    base_trend = base_trend or _trend_read(
        [row["close"] for row in rows],
        _true_ranges(rows),
        fast_period=min(9, max(2, len(rows) - 1)),
        slow_period=min(21, len(rows)),
    )
    reads.append({"timeframe": _timeframe_label(source_unit, source_number), "direction": base_trend["direction"],
        "latest_candle_end_timestamp": _candle_end(rows[-1]["timestamp"], source_unit, source_number).isoformat(),
        "contract_id": rows[-1].get("contract_id"), "closed_candle_count": len(rows),
        "fast_period": base_trend["fast_period"], "slow_period": base_trend["slow_period"]})
    for unit, number, label in candidates:
        target_seconds = _interval_seconds(unit, number)
        aggregated = _aggregate(rows, source_seconds, target_seconds)
        if len(aggregated) < MIN_SUFFICIENT_BARS:
            continue
        source_end = _candle_end(rows[-1]["timestamp"], source_unit, source_number)
        aggregate_end = aggregated[-1]["timestamp"] + timedelta(seconds=target_seconds)
        if (source_end - aggregate_end).total_seconds() >= target_seconds:
            # Older complete aggregates do not confirm a current read when the
            # latest expected complete higher-timeframe interval is missing.
            continue
        read = _trend_read(
            [row["close"] for row in aggregated],
            _true_ranges(aggregated),
            fast_period=9,
            slow_period=21,
        )
        reads.append({"timeframe": label, "direction": read["direction"],
            "latest_candle_end_timestamp": (aggregated[-1]["timestamp"] + timedelta(seconds=_interval_seconds(unit, number))).isoformat(),
            "contract_id": aggregated[-1].get("contract_id"), "closed_candle_count": len(aggregated),
            "fast_period": read["fast_period"], "slow_period": read["slow_period"]})
    primary = reads[0]["direction"] if reads else "neutral"
    if len(reads) < 2:
        status = "unavailable"
    elif primary == "neutral":
        status = "neutral" if all(read["direction"] == "neutral" for read in reads[1:]) else "mixed"
    else:
        conflicts = sum(1 for read in reads[1:] if read["direction"] not in {primary, "neutral"})
        status = "mixed" if conflicts else primary if all(read["direction"] == primary for read in reads[1:]) else "neutral"
    aligned = sum(1 for read in reads if primary != "neutral" and read["direction"] == primary)
    conflicting = sum(1 for read in reads if primary != "neutral" and read["direction"] not in {primary, "neutral"})
    return {
        "status": status,
        "aligned_timeframes": aligned,
        "conflicting_timeframes": conflicting,
        "timeframes": reads,
    }


def _support_resistance(rows: list[dict[str, Any]], *, current_price: float) -> tuple[list[float], list[float]]:
    recent = rows[-80:]
    supports: list[float] = []
    resistances: list[float] = []
    window = 2 if len(recent) < 30 else 3
    for index in range(window, len(recent) - window):
        row = recent[index]
        neighbors = recent[index - window : index] + recent[index + 1 : index + 1 + window]
        if row["low"] <= min(item["low"] for item in neighbors) and row["low"] <= current_price:
            supports.append(row["low"])
        if row["high"] >= max(item["high"] for item in neighbors) and row["high"] >= current_price:
            resistances.append(row["high"])
    for lookback in (5, 10, 20, 50):
        subset = recent[-min(lookback, len(recent)):]
        supports.append(min(row["low"] for row in subset))
        resistances.append(max(row["high"] for row in subset))
    return (
        _unique_levels([value for value in supports if value <= current_price], current_price, reverse=True),
        _unique_levels([value for value in resistances if value >= current_price], current_price, reverse=False),
    )


def _unique_levels(values: Iterable[float], current_price: float, *, reverse: bool) -> list[float]:
    output: list[float] = []
    for value in sorted(values, reverse=reverse):
        tolerance = max(abs(current_price) * 0.001, _EPSILON)
        if any(abs(value - existing) <= tolerance for existing in output):
            continue
        rounded = _round(value)
        if rounded is not None:
            output.append(rounded)
        if len(output) >= 5:
            break
    return output


def _market_regime(*, trend_direction: str, trend_strength: int, volatility_state: str, mtf_status: str, volume_state: str) -> str:
    del mtf_status  # Alignment is reported separately from the regime classifier.
    if volatility_state not in {"low", "normal", "elevated", "extreme"}:
        return "unknown"
    if volatility_state == "extreme" or (
        volatility_state == "elevated" and (trend_direction == "neutral" or trend_strength < 45)
    ):
        return "volatile"
    if volatility_state == "low":
        return "quiet"
    if trend_direction != "neutral" and trend_strength >= 45:
        return "trend"
    if trend_direction == "neutral" and volume_state != "elevated":
        return "range"
    return "chop"


def _score_drivers(**values: Any) -> dict[str, list[str]]:
    trend = values["trend"]
    bullish: list[str] = []
    bearish: list[str] = []
    neutral: list[str] = []
    if trend["direction"] == "bullish":
        bullish.append(f"Closed-bar trend is bullish at {trend['strength']}/100 strength.")
    elif trend["direction"] == "bearish":
        bearish.append(f"Closed-bar trend is bearish at {trend['strength']}/100 strength.")
    else:
        neutral.append("Closed-bar trend components do not agree directionally.")
    if values["vwap_location"] == "above":
        bullish.append("Latest closed price is above VWAP of the available session candles.")
    elif values["vwap_location"] == "below":
        bearish.append("Latest closed price is below VWAP of the available session candles.")
    else:
        neutral.append(f"VWAP location is {values['vwap_location']}.")
    if values["mtf"]["status"] == "bullish":
        bullish.append("Available closed-bar timeframes align bullish.")
    elif values["mtf"]["status"] == "bearish":
        bearish.append("Available closed-bar timeframes align bearish.")
    elif values["mtf"]["status"] in {"mixed", "neutral", "unavailable"}:
        neutral.append(f"Multi-timeframe alignment is {values['mtf']['status']}.")
    if values["volume_state"] == "low":
        neutral.append("Relative volume is low, reducing follow-through confidence.")
    if values["regime"] in {"range", "quiet", "chop"}:
        neutral.append(f"Market regime is {values['regime']}.")
    return {"bullish": _dedupe(bullish), "bearish": _dedupe(bearish), "neutral": _dedupe(neutral)}


def _scenario_weights(**values: Any) -> dict[str, int]:
    trend = values["trend"]
    bullish, bearish, sideways = 33.0, 33.0, 34.0
    tilt = min(32.0, trend["strength"] * 0.4)
    if trend["direction"] == "bullish":
        bullish += tilt
        bearish -= tilt * 0.55
        sideways -= tilt * 0.45
    elif trend["direction"] == "bearish":
        bearish += tilt
        bullish -= tilt * 0.55
        sideways -= tilt * 0.45
    else:
        sideways += 6
        bullish -= 3
        bearish -= 3
    if values["regime"] in {"range", "quiet"}:
        sideways += 6
        bullish -= 3
        bearish -= 3
    elif values["regime"] == "volatile":
        sideways -= 5
        bullish += 2.5
        bearish += 2.5
    if values["volume_state"] == "elevated" and trend["direction"] == "bullish":
        bullish += 5
        sideways -= 3
        bearish -= 2
    elif values["volume_state"] == "elevated" and trend["direction"] == "bearish":
        bearish += 5
        sideways -= 3
        bullish -= 2
    elif values["volume_state"] == "low":
        sideways += 3
        bullish -= 1.5
        bearish -= 1.5
    if values["vwap_location"] == "above":
        bullish += 3
        bearish -= 3
    elif values["vwap_location"] == "below":
        bearish += 3
        bullish -= 3
    if values["mtf_status"] == "bullish":
        bullish += 4
        bearish -= 2
        sideways -= 2
    elif values["mtf_status"] == "bearish":
        bearish += 4
        bullish -= 2
        sideways -= 2
    elif values["mtf_status"] == "mixed":
        sideways += 4
        bullish -= 2
        bearish -= 2
    expected_move = values["expected_move"] or 0
    if expected_move > 0 and values["nearest_resistance"] is not None and 0 <= values["nearest_resistance"] - values["current_price"] <= expected_move:
        bullish -= 4
        bearish += 2
        sideways += 2
    if expected_move > 0 and values["nearest_support"] is not None and 0 <= values["current_price"] - values["nearest_support"] <= expected_move:
        bearish -= 4
        bullish += 2
        sideways += 2
    return _normalize_weights({"bullish": bullish, "bearish": bearish, "sideways": sideways})


def _normalize_weights(values: dict[str, float]) -> dict[str, int]:
    keys = ["bullish", "bearish", "sideways"]
    positive = {key: max(0.0, float(values.get(key, 0))) for key in keys}
    total = sum(positive.values())
    if total <= _EPSILON:
        return {"bullish": 33, "bearish": 33, "sideways": 34}
    exact = {key: positive[key] / total * 100 for key in keys}
    output = {key: int(math.floor(exact[key])) for key in keys}
    remainder = 100 - sum(output.values())
    order = sorted(keys, key=lambda key: (exact[key] - output[key], exact[key], key), reverse=True)
    for index in range(remainder):
        output[order[index % len(order)]] += 1
    return output


def _invalidation_level(**values: Any) -> float | None:
    direction = values["trend_direction"]
    if direction == "bullish":
        level = values["nearest_support"]
        if level is None and values["expected_move"] is not None:
            level = values["current_price"] - values["expected_move"]
        return _round(level)
    if direction == "bearish":
        level = values["nearest_resistance"]
        if level is None and values["expected_move"] is not None:
            level = values["current_price"] + values["expected_move"]
        return _round(level)
    return None


def _base_missing_inputs(closed: list[dict[str, Any]]) -> list[str]:
    missing = ["news_context", "macro_context", "cross_market_context", "level_1_quotes", "observed_volume_profile"]
    if len(closed) < MIN_SUFFICIENT_BARS:
        missing.append(f"at_least_{MIN_SUFFICIENT_BARS}_closed_candles")
    return missing


def _data_quality(*, closed_count: int, partial_count: int, is_stale: bool, gap_count: int, missing_inputs: list[str], warnings: list[str]) -> dict[str, Any]:
    confidence = 100
    if closed_count < MIN_FEATURE_BARS:
        confidence -= 70
    elif closed_count < MIN_SUFFICIENT_BARS:
        confidence -= 35
    elif closed_count < 50:
        confidence -= 10
    confidence -= min(30, gap_count * 10)
    confidence -= min(10, partial_count * 2)
    confidence -= 5 * sum(1 for item in missing_inputs if item in {"relative_volume_baseline", "session_vwap", "multi_timeframe_history"})
    if is_stale:
        confidence -= 35
    confidence = max(0, min(100, int(round(confidence))))
    if is_stale:
        status = "stale"
    elif closed_count < MIN_SUFFICIENT_BARS:
        status = "insufficient"
    elif confidence < 80:
        status = "limited"
    else:
        status = "good"
    return {"status": status, "confidence": confidence, "missing_inputs": missing_inputs, "warnings": _dedupe(warnings)}


def _setup_quality_score(
    *,
    data_confidence: int,
    trend_direction: str,
    trend_strength: int,
    regime: str,
    mtf_status: str,
    volume_state: str,
    vwap_location: str,
    has_levels: bool,
) -> int:
    # Setup quality is confluence, not merely data completeness. A complete
    # dataset with weak trend, chop, low volume, and VWAP conflict must not
    # receive a near-perfect setup score.
    score = data_confidence * 0.30 + trend_strength * 0.30
    if trend_direction in {"bullish", "bearish"} and mtf_status == trend_direction:
        score += 15
    elif mtf_status == "neutral":
        score += 7
    elif mtf_status == "mixed":
        score += 2

    if regime == "trend" and trend_direction in {"bullish", "bearish"}:
        score += 15
    elif regime in {"range", "quiet"} and trend_direction == "neutral":
        score += 12
    elif regime in {"range", "quiet"}:
        score += 4
    elif regime == "chop":
        score += 2
    elif regime == "volatile":
        score += 3

    score += {"elevated": 10, "normal": 7, "low": 1}.get(volume_state, 0)
    if (trend_direction == "bullish" and vwap_location == "above") or (
        trend_direction == "bearish" and vwap_location == "below"
    ):
        score += 8
    elif (trend_direction == "bullish" and vwap_location == "below") or (
        trend_direction == "bearish" and vwap_location == "above"
    ):
        score -= 4
    elif trend_direction == "neutral" and vwap_location == "at":
        score += 5

    score += 7 if has_levels else 0
    return max(0, min(100, int(round(score))))


def _execution_risk_score(
    *,
    volatility_state: str,
    volume_state: str,
    regime: str,
    trend_strength: int,
    mtf_status: str,
    is_stale: bool,
    gap_count: int,
) -> int:
    score = {"low": 10, "normal": 20, "elevated": 45, "extreme": 70}.get(volatility_state, 30)
    if volume_state == "low":
        score += 15
    if regime in {"chop", "volatile"}:
        score += 15
    if trend_strength < 25:
        score += 10
    elif trend_strength < 45:
        score += 5
    if mtf_status == "mixed":
        score += 15
    elif mtf_status == "unavailable":
        score += 10
    if is_stale:
        score += 35
    score += min(30, gap_count * 10)
    return max(0, min(100, score))


def _analysis_summary(
    *,
    bias_direction: str,
    trend_strength: int,
    regime: str,
    weights: dict[str, int],
    volume_state: str,
    vwap_location: str,
    nearest_support: float | None,
    nearest_resistance: float | None,
) -> str:
    del weights, volume_state, vwap_location, nearest_support, nearest_resistance
    strength_label = "weak" if trend_strength < 30 else "moderate" if trend_strength < 65 else "strong"
    if bias_direction == "neutral":
        return "Closed candles do not establish a clear directional trend."
    direction = "upward" if bias_direction == "bullish" else "downward"
    context = {"quiet": "Recent candle ranges are contracting.", "chop": "Price action is choppy.", "volatile": "Recent candle ranges are expanding."}.get(regime, "")
    return f"Closed candles show an {direction} trend with {strength_label} movement strength. {context}".strip()


def _explanation(*, trend: dict[str, Any], regime: str, drivers: dict[str, list[str]], provenance: dict[str, Any],
    nearest_support: float | None, nearest_resistance: float | None, volume_state: str, mtf: dict[str, Any],
    vwap_provenance: dict[str, Any]) -> dict[str, Any]:
    direction = trend["direction"]
    component_text = [
        f"{item['label']}: {'upward' if item['direction'] == 'bullish' else 'downward' if item['direction'] == 'bearish' else 'within its flat threshold'}."
        for item in trend["components"]
    ]
    if direction == "neutral":
        supporting = component_text
        # Opposing components establish the mixed interpretation. Independent
        # directional context is what could challenge that interpretation.
        conflicting = [
            f"{'Upward' if side == 'bullish' else 'Downward'} evidence: {text}"
            for side in ("bullish", "bearish") for text in drivers[side] if "VWAP" in text
        ]
        conflicting.extend(
            f"The {frame['timeframe']} trend is {'upward' if frame['direction'] == 'bullish' else 'downward'}, providing directional evidence beyond this timeframe."
            for frame in mtf["timeframes"][1:] if frame["direction"] in {"bullish", "bearish"}
        )
    else:
        supporting = [text for item, text in zip(trend["components"], component_text) if item["direction"] == direction]
        conflicting = [text for item, text in zip(trend["components"], component_text) if item["direction"] not in {direction, "neutral"}]
        opposite = "bearish" if direction == "bullish" else "bullish"
        supporting.extend(text for text in drivers[direction] if "VWAP" in text or "timeframes" in text)
        conflicting.extend(text for text in drivers[opposite] if "VWAP" in text or "timeframes" in text)
    if direction != "neutral" and mtf["status"] == "mixed":
        conflicting.append("The available higher-timeframe trends disagree with the selected timeframe.")
    limitations = []
    if volume_state == "unavailable":
        limitations.append("Latest candle volume or its baseline is unavailable; volume supplies no confirmation.")
    elif volume_state == "low":
        limitations.append("Latest candle volume is below its recent baseline; it supplies little participation evidence.")
    if mtf["status"] == "unavailable":
        limitations.append("There are not enough complete higher-timeframe candles to assess agreement.")
    if not vwap_provenance["complete_session"]:
        limitations.append("VWAP covers only the available candles within the futures session, not a complete session history or the strategy's RTH VWAP.")
    if provenance["is_stale"]:
        limitations.append("The candle snapshot is stale during scheduled trading time; this is a historical read.")
    elif not provenance["market_session_open"]:
        limitations.append("The scheduled market is closed; this describes the last available closed candles.")
    levels = []
    if nearest_resistance is not None:
        levels.append({"direction": "bullish", "price": nearest_resistance, "condition": f"A closed {provenance['timeframe']['label']} candle above {_fmt(nearest_resistance)} would challenge the current resistance; re-evaluate trend components at that close."})
    if nearest_support is not None:
        levels.append({"direction": "bearish", "price": nearest_support, "condition": f"A closed {provenance['timeframe']['label']} candle below {_fmt(nearest_support)} would challenge the current support; re-evaluate trend components at that close."})
    return {
        "headline": _analysis_summary(bias_direction=direction, trend_strength=trend["strength"], regime=regime,
            weights={}, volume_state=volume_state, vwap_location="", nearest_support=None, nearest_resistance=None),
        "supporting_evidence": _dedupe(supporting), "conflicting_evidence": _dedupe(conflicting),
        "limitations": _dedupe(limitations), "change_levels": levels,
        "scope": "Descriptive analysis of closed candles only. These reference levels are not entry signals, orders or strategy stop levels; TopBot's strategy, session and risk checks determine its decision.",
    }


def _score_definitions(provenance: dict[str, Any], trend: dict[str, Any] | None) -> dict[str, Any]:
    window = f"{provenance['closed_candle_count']} supplied closed {provenance['timeframe']['label']} candles; contract {provenance.get('resolved_contract_id') or 'unidentified'}."
    def definition(inputs: list[str], scale: str, reference: str, missing: str, meaning: str) -> dict[str, Any]:
        return {"inputs": inputs, "scale": scale, "reference_window": reference, "missing_data": missing, "interpretation": meaning}
    fast, slow = (trend["fast_period"], trend["slow_period"]) if trend else (None, None)
    return {
        "trend_strength": definition(
            [f"EMA({fast}) minus EMA({slow}) / average true range", "Five-bar change in slow EMA / average true range", "Five-bar price change / (average true range × 5)"],
            "0–100, clipped rounded sum: 30×abs(EMA gap units) + 25×abs(slope units) + 35×abs(price-change units). Weak <30, moderate <65, strong otherwise.",
            f"{window} EMAs are seeded at the first supplied close; normalizer uses up to 14 latest true ranges. Five-bar changes use available history.",
            "Fewer than 10 closed bars: unavailable (legacy strength 0 is a placeholder). EMA periods shorten to available bars; zero range uses max(abs(close)×0.001, 1e-9).",
            "Movement magnitude, not indicator agreement or predictive confidence. Direction requires strength ≥15 and net component sign magnitude ≥2; sign deadbands are 0.05, 0.03 and 0.05 ATR units respectively."),
        "atr_percentile": definition(["Simple mean of 14 true ranges", "Up to 100 rolling ATR(14) values including latest"],
            "0–100 mid-rank: (values below latest + 0.5 × values tied) / observation count × 100.",
            window + " Latest up to 100 rolling ATR observations; not a fixed multi-day or long-term reference.",
            "ATR needs 14 bars; percentile needs at least two rolling ATR values. Missing values remain null.",
            "Historical rank within this input window, not volatility direction or forecast. A high rank can coexist with cooling recent ranges."),
        "recent_range_change": definition(["Mean true range of latest 6 bars", "Mean true range of preceding up to 28 bars"],
            "Ratio <0.7 cooling (legacy low); <1.35 stable (normal); <2 expanding (elevated); otherwise sharply expanding (extreme).",
            window + " Requires 22 bars: 6 recent and at least 16 baseline bars; baseline capped at 28.",
            "Fewer than 22 bars or zero baseline: unavailable, never normal.", "Recent range change, distinct from ATR(14)'s historical percentile. Existing thresholds retained."),
        "relative_volume": definition(["Latest closed candle volume", "Mean volume of preceding up to 20 consecutive supplied bars, including real zeros"],
            "Ratio; low <0.7, elevated >1.5, normal otherwise.", window + " At least 5 preceding bars; not adjusted for time of day.",
            "Latest/baseline missing volume or zero baseline: unavailable. A reported latest zero is measured zero and is never replaced by an older positive observation.",
            "Recent participation comparison only; missing data is not low participation."),
        "data_quality": definition(["Closed-bar count", "Detected gaps", "Excluded partial count", "Freshness", "Availability of relative volume, observed VWAP and higher timeframes"],
            "Legacy 0–100: start100; history -70 below10/-35 below25/-10 below50; gaps -min(30,10×count); partials -min(10,2×count); unavailable volume/VWAP/MTF -5 each; stale -35; clip0–100.",
            window + " Freshness counts scheduled-open time since latest candle end; stale after one timeframe interval plus configured grace.",
            "Below10 bars uses legacy min(35,3×count). News, macro, quotes and cross-market coverage do not enter this candle-only score.",
            "Deprecated display score, retained for API compatibility with data_confidence.score. Does not measure overall context completeness or predictive confidence."),
        "setup_quality": definition(["30% candle-quality score", "30% movement-strength score", "MTF, regime, volume, observed VWAP and nearby-level heuristic bonuses"],
            "Legacy clipped0–100 heuristic; strong≥80, acceptable≥60, limited≥40, weak otherwise. Full bonus table: docs/evaluation-market-analysis.md.",
            window, "Unavailable features earn no associated bonus; below10 bars returns candle-count placeholder.",
            "Deprecated display score; not the strategy setup, permission to enter, validation or probability."),
        "execution_risk": definition(["Recent-range category", "Volume category", "Regime", "Movement strength", "MTF agreement", "Candle freshness and gaps"],
            "Legacy clipped0–100 heuristic; high≥60, moderate≥30, low otherwise. Full additive table: docs/evaluation-market-analysis.md.",
            window, "Unknown recent-range state starts30; absent MTF adds10; below10 bars uses100 minus candle-count placeholder.",
            "Deprecated candle-only display score, not TopBot's actual account/session/risk checks and not a measured slippage probability."),
        "scenario_weights": definition(["Trend, regime, volume, observed VWAP, timeframe agreement and distance to levels"],
            "Legacy integer weights summing100, initialized33/33/34 and deterministically adjusted then normalized. Full adjustments: docs/evaluation-market-analysis.md.",
            window, "Missing inputs add no adjustment; below10 bars returns33/33/34 compatibility placeholders with no evidentiary meaning.",
            "Deprecated unvalidated heuristic weights, not probabilities. bullish_probability/bearish_probability/sideways_probability are compatibility aliases. Bot signal is excluded."),
    }


def _volatility_state(true_ranges: list[float]) -> str:
    ratio = _recent_range_ratio(true_ranges)
    if ratio is None:
        return "unavailable"
    return "low" if ratio < 0.7 else "normal" if ratio < 1.35 else "elevated" if ratio < 2 else "extreme"


def _recent_range_ratio(true_ranges: list[float]) -> float | None:
    if len(true_ranges) < 22:
        return None
    recent = _average(true_ranges[-6:])
    baseline = _average(true_ranges[:-6][-28:])
    if recent is None or baseline is None or baseline <= _EPSILON:
        return None
    return recent / baseline


def _recent_range_state(ratio: float | None) -> str:
    if ratio is None:
        return "unavailable"
    return "cooling" if ratio < 0.7 else "stable" if ratio < 1.35 else "expanding" if ratio < 2 else "sharply_expanding"


def _volume_state(relative_volume: float | None) -> str:
    if relative_volume is None:
        return "unavailable"
    return "low" if relative_volume < 0.7 else "elevated" if relative_volume > 1.5 else "normal"


def _quality_label(score: int) -> str:
    return "strong" if score >= 80 else "acceptable" if score >= 60 else "limited" if score >= 40 else "weak"


def _risk_label(score: int) -> str:
    return "high" if score >= 60 else "moderate" if score >= 30 else "low"


def _level_reasoning(support: float | None, resistance: float | None) -> str:
    return f"Nearest support is {_fmt(support)}; nearest resistance is {_fmt(resistance)}."


def _average(values: Sequence[float]) -> float | None:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return sum(finite) / len(finite) if finite else None


def _percent_change(current: float, previous: float) -> float | None:
    return (current - previous) / previous * 100 if abs(previous) > _EPSILON else None


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    rounded = round(float(value), digits)
    return 0.0 if rounded == 0 else rounded


def _fmt(value: float | None, *, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    formatted = f"{value:.{digits}f}"
    return formatted if digits == 0 else formatted.rstrip("0").rstrip(".")


def _fmt_signed(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}"


def _fmt_ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}x"


def _dedupe(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return output
