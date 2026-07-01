from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Protocol, Sequence


class CandleLike(Protocol):
    candle_timestamp: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float


@dataclass(frozen=True)
class BollingerBandPoint:
    middle: float | None
    upper: float | None
    lower: float | None


@dataclass(frozen=True)
class FairValueGap:
    side: str
    lower_price: float
    upper_price: float
    timestamp: datetime
    source_index: int
    gap_size: float
    gap_percent: float | None
    mitigated: bool = False
    mitigation_index: int | None = None
    mitigation_timestamp: datetime | None = None
    mitigation_level: float | None = None


@dataclass(frozen=True)
class OrderBlock:
    side: str
    bottom_price: float
    top_price: float
    timestamp: datetime
    source_index: int
    volume: float | None
    strength: float
    mitigated: bool = False
    mitigation_index: int | None = None
    mitigation_timestamp: datetime | None = None
    mitigation_level: float | None = None


@dataclass(frozen=True)
class CandlestickPattern:
    name: str
    direction: str
    strength: float
    timestamp: datetime
    source_index: int


@dataclass(frozen=True)
class WaddahAttarPoint:
    explosion: float | None
    trend: int
    dead_zone: float | None
    bullish: bool
    bearish: bool
    explosion_above_dead_zone: bool


def ema_series(values: Sequence[float], period: int) -> list[float | None]:
    normalized_period = max(1, int(period))
    if len(values) < normalized_period:
        return [None] * len(values)

    current = _average([float(value) for value in values[:normalized_period]])
    multiplier = 2.0 / (normalized_period + 1)
    output: list[float | None] = [None] * (normalized_period - 1)
    output.append(current)
    for value in values[normalized_period:]:
        current = ((float(value) - current) * multiplier) + current
        output.append(current)
    return output


def rsi_series(closes: Sequence[float], period: int) -> list[float | None]:
    normalized_period = max(1, int(period))
    output: list[float | None] = [None] * len(closes)
    if len(closes) < normalized_period + 1:
        return output

    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, len(closes)):
        change = float(closes[index]) - float(closes[index - 1])
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    average_gain = _average(gains[:normalized_period])
    average_loss = _average(losses[:normalized_period])
    output[normalized_period] = _wilder_rsi(average_gain, average_loss)
    for index in range(normalized_period, len(gains)):
        average_gain = ((average_gain * (normalized_period - 1)) + gains[index]) / normalized_period
        average_loss = ((average_loss * (normalized_period - 1)) + losses[index]) / normalized_period
        output[index + 1] = _wilder_rsi(average_gain, average_loss)

    return output


def atr_series(candles: Sequence[CandleLike], period: int) -> list[float | None]:
    normalized_period = max(1, int(period))
    output: list[float | None] = [None] * len(candles)
    if len(candles) < normalized_period:
        return output

    true_ranges = true_range_series(candles)
    current = _average(true_ranges[:normalized_period])
    output[normalized_period - 1] = current
    for index in range(normalized_period, len(candles)):
        current = ((current * (normalized_period - 1)) + true_ranges[index]) / normalized_period
        output[index] = current
    return output


def true_range_series(candles: Sequence[CandleLike]) -> list[float]:
    true_ranges: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        high = _price(candle, "high_price")
        low = _price(candle, "low_price")
        if previous_close is None:
            true_range = high - low
        else:
            true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        true_ranges.append(true_range)
        previous_close = _price(candle, "close_price")
    return true_ranges


def bollinger_bands(
    closes: Sequence[float],
    *,
    period: int,
    stddev_multiplier: float,
) -> list[BollingerBandPoint]:
    normalized_period = max(1, int(period))
    output = [BollingerBandPoint(middle=None, upper=None, lower=None) for _ in closes]
    if len(closes) < normalized_period:
        return output

    for index in range(normalized_period - 1, len(closes)):
        window = [float(value) for value in closes[index - normalized_period + 1 : index + 1]]
        average = _average(window)
        variance = sum((value - average) ** 2 for value in window) / normalized_period
        deviation = math.sqrt(variance)
        output[index] = BollingerBandPoint(
            middle=average,
            upper=average + deviation * float(stddev_multiplier),
            lower=average - deviation * float(stddev_multiplier),
        )
    return output


def session_vwap_values(candles: Sequence[CandleLike]) -> list[float | None]:
    values: list[float | None] = []
    current_session: str | None = None
    cumulative_volume = 0.0
    cumulative_price_volume = 0.0
    current_vwap: float | None = None

    for candle in candles:
        session = _timestamp(candle).date().isoformat()
        if session != current_session:
            current_session = session
            cumulative_volume = 0.0
            cumulative_price_volume = 0.0
            current_vwap = None

        volume = _price(candle, "volume")
        if volume > 0:
            typical_price = (
                _price(candle, "high_price") + _price(candle, "low_price") + _price(candle, "close_price")
            ) / 3.0
            cumulative_volume += volume
            cumulative_price_volume += typical_price * volume
            current_vwap = cumulative_price_volume / cumulative_volume
        values.append(current_vwap)

    return values


def detect_fair_value_gaps(
    candles: Sequence[CandleLike],
    *,
    min_gap_size: float = 0.0,
    min_gap_percent: float = 0.0,
    check_mitigation: bool = False,
    mitigation_threshold: float = 0.5,
) -> list[FairValueGap]:
    gaps: list[FairValueGap] = []
    threshold = min(max(float(mitigation_threshold), 0.0), 1.0)
    for index in range(2, len(candles)):
        left = candles[index - 2]
        right = candles[index]
        left_high = _price(left, "high_price")
        left_low = _price(left, "low_price")
        right_high = _price(right, "high_price")
        right_low = _price(right, "low_price")

        if right_low > left_high:
            gap_size = right_low - left_high
            gap_percent = _percent_of(gap_size, left_high)
            if gap_size >= min_gap_size and (gap_percent or 0.0) >= min_gap_percent:
                gaps.append(
                    _with_fvg_mitigation(
                        candles,
                        FairValueGap(
                            side="bullish",
                            lower_price=left_high,
                            upper_price=right_low,
                            timestamp=_timestamp(right),
                            source_index=index,
                            gap_size=gap_size,
                            gap_percent=gap_percent,
                        ),
                        threshold=threshold,
                    )
                    if check_mitigation
                    else FairValueGap(
                        side="bullish",
                        lower_price=left_high,
                        upper_price=right_low,
                        timestamp=_timestamp(right),
                        source_index=index,
                        gap_size=gap_size,
                        gap_percent=gap_percent,
                    )
                )

        if right_high < left_low:
            gap_size = left_low - right_high
            gap_percent = _percent_of(gap_size, left_low)
            if gap_size >= min_gap_size and (gap_percent or 0.0) >= min_gap_percent:
                gaps.append(
                    _with_fvg_mitigation(
                        candles,
                        FairValueGap(
                            side="bearish",
                            lower_price=right_high,
                            upper_price=left_low,
                            timestamp=_timestamp(right),
                            source_index=index,
                            gap_size=gap_size,
                            gap_percent=gap_percent,
                        ),
                        threshold=threshold,
                    )
                    if check_mitigation
                    else FairValueGap(
                        side="bearish",
                        lower_price=right_high,
                        upper_price=left_low,
                        timestamp=_timestamp(right),
                        source_index=index,
                        gap_size=gap_size,
                        gap_percent=gap_percent,
                    )
                )

    return gaps


def detect_order_blocks(
    candles: Sequence[CandleLike],
    *,
    min_volume_percentile: float = 50.0,
    lookback_periods: int = 3,
    check_mitigation: bool = False,
    mitigation_threshold: float = 0.5,
    use_wicks: bool = True,
) -> list[OrderBlock]:
    lookback = max(1, int(lookback_periods))
    if len(candles) < lookback + 1:
        return []

    volume_percentiles = _volume_percentiles(candles)
    threshold = min(max(float(mitigation_threshold), 0.0), 1.0)
    blocks_by_index: dict[tuple[int, str], OrderBlock] = {}

    for index in range(lookback, len(candles)):
        current = candles[index]
        for offset in range(1, lookback + 1):
            source_index = index - offset
            source = candles[source_index]
            if volume_percentiles[source_index] < float(min_volume_percentile):
                continue

            if (
                _price(source, "close_price") < _price(source, "open_price")
                and _price(current, "high_price") > _price(source, "high_price")
            ):
                blocks_by_index[(source_index, "bullish")] = _make_order_block(
                    candles,
                    source_index=source_index,
                    side="bullish",
                    volume_percentile=volume_percentiles[source_index],
                    use_wicks=use_wicks,
                    check_mitigation=check_mitigation,
                    threshold=threshold,
                )
                break

        for offset in range(1, lookback + 1):
            source_index = index - offset
            source = candles[source_index]
            if volume_percentiles[source_index] < float(min_volume_percentile):
                continue

            if (
                _price(source, "close_price") > _price(source, "open_price")
                and _price(current, "low_price") < _price(source, "low_price")
            ):
                blocks_by_index[(source_index, "bearish")] = _make_order_block(
                    candles,
                    source_index=source_index,
                    side="bearish",
                    volume_percentile=volume_percentiles[source_index],
                    use_wicks=use_wicks,
                    check_mitigation=check_mitigation,
                    threshold=threshold,
                )
                break

    return sorted(blocks_by_index.values(), key=lambda block: block.source_index)


def detect_candlestick_patterns(
    candles: Sequence[CandleLike],
    *,
    min_strength: float = 50.0,
) -> list[CandlestickPattern]:
    patterns: list[CandlestickPattern] = []
    for index, candle in enumerate(candles):
        timestamp = _timestamp(candle)
        metrics = _candle_shape(candle)
        if metrics["range"] <= 0:
            continue

        doji_strength = max(0.0, min(100.0, 100.0 - (metrics["body"] / metrics["range"] * 100.0)))
        if doji_strength >= min_strength:
            patterns.append(CandlestickPattern("doji", "neutral", doji_strength, timestamp, index))

        hammer_strength = _hammer_strength(metrics)
        if hammer_strength >= min_strength:
            patterns.append(CandlestickPattern("hammer", "bullish", hammer_strength, timestamp, index))

        shooting_star_strength = _shooting_star_strength(metrics)
        if abs(shooting_star_strength) >= min_strength:
            patterns.append(
                CandlestickPattern("shooting_star", "bearish", shooting_star_strength, timestamp, index)
            )

        if index > 0:
            previous = candles[index - 1]
            if (
                _price(previous, "close_price") < _price(previous, "open_price")
                and _price(candle, "close_price") > _price(candle, "open_price")
                and _price(candle, "open_price") < _price(previous, "close_price")
                and _price(candle, "close_price") > _price(previous, "open_price")
            ):
                patterns.append(CandlestickPattern("bullish_engulfing", "bullish", 100.0, timestamp, index))
            if (
                _price(previous, "close_price") > _price(previous, "open_price")
                and _price(candle, "close_price") < _price(candle, "open_price")
                and _price(candle, "open_price") > _price(previous, "close_price")
                and _price(candle, "close_price") < _price(previous, "open_price")
            ):
                patterns.append(CandlestickPattern("bearish_engulfing", "bearish", -100.0, timestamp, index))

    return patterns


def waddah_attar_explosion(
    candles: Sequence[CandleLike],
    *,
    fast_period: int = 20,
    slow_period: int = 40,
    bb_period: int = 20,
    bb_mult: float = 2.0,
    sensitivity: float = 150.0,
    dead_zone_period: int = 100,
    dead_zone_mult: float = 3.6,
) -> list[WaddahAttarPoint]:
    if not candles:
        return []

    closes = [_price(candle, "close_price") for candle in candles]
    fast = ema_series(closes, max(1, int(fast_period)))
    slow = ema_series(closes, max(1, int(slow_period)))
    bands = bollinger_bands(closes, period=max(1, int(bb_period)), stddev_multiplier=float(bb_mult))
    atr = atr_series(candles, max(1, int(dead_zone_period)))

    output: list[WaddahAttarPoint] = []
    for fast_value, slow_value, band, atr_value in zip(fast, slow, bands, atr):
        if (
            fast_value is None
            or slow_value is None
            or band.upper is None
            or band.lower is None
            or atr_value is None
        ):
            output.append(
                WaddahAttarPoint(
                    explosion=None,
                    trend=0,
                    dead_zone=None,
                    bullish=False,
                    bearish=False,
                    explosion_above_dead_zone=False,
                )
            )
            continue

        macd_line = fast_value - slow_value
        explosion = (band.upper - band.lower) * abs(macd_line) * float(sensitivity) / max(1, int(bb_period))
        trend = 1 if macd_line > 0 else -1 if macd_line < 0 else 0
        dead_zone = atr_value * float(dead_zone_mult)
        above = explosion > dead_zone
        output.append(
            WaddahAttarPoint(
                explosion=explosion,
                trend=trend,
                dead_zone=dead_zone,
                bullish=above and trend == 1,
                bearish=above and trend == -1,
                explosion_above_dead_zone=above,
            )
        )
    return output


def build_projectx_style_indicator_snapshot(candles: Sequence[CandleLike]) -> dict[str, Any]:
    if not candles:
        return {
            "rsi": None,
            "atr": None,
            "vwap": None,
            "bollinger": None,
            "fair_value_gaps": {"active_count": 0, "latest": None},
            "order_blocks": {"active_count": 0, "latest": None},
            "candlestick_patterns": [],
            "waddah_attar": None,
        }

    closes = [_price(candle, "close_price") for candle in candles]
    atr_period = min(14, len(candles))
    rsi_period = min(14, max(1, len(closes) - 1))
    bollinger_period = min(20, len(closes))
    dead_zone_period = min(100, max(14, len(candles) // 2))

    atr_values = atr_series(candles, atr_period)
    rsi_values = rsi_series(closes, rsi_period)
    vwap_values = session_vwap_values(candles)
    band_values = bollinger_bands(closes, period=bollinger_period, stddev_multiplier=2.0)
    fvg_values = detect_fair_value_gaps(candles, check_mitigation=True)
    order_blocks = detect_order_blocks(candles, min_volume_percentile=60, check_mitigation=True)
    patterns = detect_candlestick_patterns(candles, min_strength=60)
    wae_slow_period = min(40, max(4, len(candles) // 2))
    wae_fast_period = min(20, max(2, wae_slow_period // 2))
    wae_values = waddah_attar_explosion(
        candles,
        fast_period=wae_fast_period,
        slow_period=wae_slow_period,
        dead_zone_period=dead_zone_period,
    )

    active_fvg = [gap for gap in fvg_values if not gap.mitigated]
    active_blocks = [block for block in order_blocks if not block.mitigated]
    latest_patterns = [pattern for pattern in patterns if pattern.source_index == len(candles) - 1]
    latest_wae = wae_values[-1] if wae_values else None
    latest_band = band_values[-1] if band_values else None

    return {
        "rsi": _round(_last_defined(rsi_values)),
        "atr": _round(_last_defined(atr_values)),
        "vwap": _round(_last_defined(vwap_values)),
        "bollinger": _serialize_bollinger(latest_band),
        "fair_value_gaps": {
            "detected_count": len(fvg_values),
            "active_count": len(active_fvg),
            "latest": _serialize_fair_value_gap(active_fvg[-1] if active_fvg else fvg_values[-1] if fvg_values else None),
        },
        "order_blocks": {
            "detected_count": len(order_blocks),
            "active_count": len(active_blocks),
            "latest": _serialize_order_block(
                active_blocks[-1] if active_blocks else order_blocks[-1] if order_blocks else None
            ),
        },
        "candlestick_patterns": [_serialize_pattern(pattern) for pattern in latest_patterns],
        "waddah_attar": _serialize_waddah_attar(latest_wae),
    }


def _make_order_block(
    candles: Sequence[CandleLike],
    *,
    source_index: int,
    side: str,
    volume_percentile: float,
    use_wicks: bool,
    check_mitigation: bool,
    threshold: float,
) -> OrderBlock:
    candle = candles[source_index]
    if use_wicks:
        top = _price(candle, "high_price")
        bottom = _price(candle, "low_price")
    else:
        top = max(_price(candle, "open_price"), _price(candle, "close_price"))
        bottom = min(_price(candle, "open_price"), _price(candle, "close_price"))

    open_price = _price(candle, "open_price")
    price_move_percent = abs(_price(candle, "close_price") - open_price) / abs(open_price) * 100 if open_price else 0.0
    strength = (price_move_percent + (volume_percentile / 100.0)) / 2.0
    block = OrderBlock(
        side=side,
        bottom_price=bottom,
        top_price=top,
        timestamp=_timestamp(candle),
        source_index=source_index,
        volume=_price(candle, "volume"),
        strength=strength,
    )
    if not check_mitigation:
        return block
    return _with_order_block_mitigation(candles, block, threshold=threshold)


def _with_fvg_mitigation(
    candles: Sequence[CandleLike],
    gap: FairValueGap,
    *,
    threshold: float,
) -> FairValueGap:
    gap_size = gap.upper_price - gap.lower_price
    if gap_size <= 0:
        return gap

    if gap.side == "bullish":
        mitigation_level = gap.lower_price + gap_size * threshold
        for index in range(gap.source_index + 1, len(candles)):
            if _price(candles[index], "low_price") <= mitigation_level:
                return replace(
                    gap,
                    mitigated=True,
                    mitigation_index=index,
                    mitigation_timestamp=_timestamp(candles[index]),
                    mitigation_level=mitigation_level,
                )
    else:
        mitigation_level = gap.upper_price - gap_size * threshold
        for index in range(gap.source_index + 1, len(candles)):
            if _price(candles[index], "high_price") >= mitigation_level:
                return replace(
                    gap,
                    mitigated=True,
                    mitigation_index=index,
                    mitigation_timestamp=_timestamp(candles[index]),
                    mitigation_level=mitigation_level,
                )
    return gap


def _with_order_block_mitigation(
    candles: Sequence[CandleLike],
    block: OrderBlock,
    *,
    threshold: float,
) -> OrderBlock:
    block_size = block.top_price - block.bottom_price
    if block_size <= 0:
        return block

    if block.side == "bullish":
        mitigation_level = block.bottom_price + block_size * threshold
        for index in range(block.source_index + 1, len(candles)):
            if _price(candles[index], "low_price") <= mitigation_level:
                return replace(
                    block,
                    mitigated=True,
                    mitigation_index=index,
                    mitigation_timestamp=_timestamp(candles[index]),
                    mitigation_level=mitigation_level,
                )
    else:
        mitigation_level = block.top_price - block_size * threshold
        for index in range(block.source_index + 1, len(candles)):
            if _price(candles[index], "high_price") >= mitigation_level:
                return replace(
                    block,
                    mitigated=True,
                    mitigation_index=index,
                    mitigation_timestamp=_timestamp(candles[index]),
                    mitigation_level=mitigation_level,
                )
    return block


def _candle_shape(candle: CandleLike) -> dict[str, float]:
    open_price = _price(candle, "open_price")
    high_price = _price(candle, "high_price")
    low_price = _price(candle, "low_price")
    close_price = _price(candle, "close_price")
    body = abs(close_price - open_price)
    candle_range = high_price - low_price
    return {
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "body": body,
        "range": candle_range,
        "upper_shadow": high_price - max(open_price, close_price),
        "lower_shadow": min(open_price, close_price) - low_price,
    }


def _hammer_strength(metrics: dict[str, float]) -> float:
    body = metrics["body"]
    candle_range = metrics["range"]
    if body <= 0 or candle_range <= 0:
        return 0.0
    lower_shadow = metrics["lower_shadow"]
    upper_shadow = metrics["upper_shadow"]
    if lower_shadow < 2 * body or upper_shadow > body * 0.3:
        return 0.0
    if min(metrics["open"], metrics["close"]) <= metrics["low"] + lower_shadow * 0.6:
        return 0.0
    return min(
        100.0,
        (lower_shadow / body * 20.0)
        + (100.0 - min(50.0, upper_shadow / body * 100.0))
        + (100.0 - min(50.0, body / candle_range * 100.0)),
    ) / 3.0


def _shooting_star_strength(metrics: dict[str, float]) -> float:
    body = metrics["body"]
    candle_range = metrics["range"]
    if body <= 0 or candle_range <= 0:
        return 0.0
    upper_shadow = metrics["upper_shadow"]
    lower_shadow = metrics["lower_shadow"]
    if upper_shadow < 2 * body or lower_shadow > body * 0.3:
        return 0.0
    if max(metrics["open"], metrics["close"]) >= metrics["high"] - upper_shadow * 0.6:
        return 0.0
    return -min(
        100.0,
        (upper_shadow / body * 20.0)
        + (100.0 - min(50.0, lower_shadow / body * 100.0))
        + (100.0 - min(50.0, body / candle_range * 100.0)),
    ) / 3.0


def _volume_percentiles(candles: Sequence[CandleLike]) -> list[float]:
    volumes = [_price(candle, "volume") for candle in candles]
    if not volumes:
        return []
    sorted_volumes = sorted(volumes)
    return [((sorted_volumes.index(volume) + 1) / len(volumes)) * 100.0 for volume in volumes]


def _serialize_fair_value_gap(gap: FairValueGap | None) -> dict[str, Any] | None:
    if gap is None:
        return None
    return {
        "side": gap.side,
        "lower_price": _round(gap.lower_price),
        "upper_price": _round(gap.upper_price),
        "timestamp": gap.timestamp.isoformat(),
        "source_index": gap.source_index,
        "gap_size": _round(gap.gap_size),
        "gap_percent": _round(gap.gap_percent),
        "mitigated": gap.mitigated,
        "mitigation_index": gap.mitigation_index,
        "mitigation_timestamp": gap.mitigation_timestamp.isoformat() if gap.mitigation_timestamp else None,
        "mitigation_level": _round(gap.mitigation_level),
    }


def _serialize_order_block(block: OrderBlock | None) -> dict[str, Any] | None:
    if block is None:
        return None
    return {
        "side": block.side,
        "bottom_price": _round(block.bottom_price),
        "top_price": _round(block.top_price),
        "timestamp": block.timestamp.isoformat(),
        "source_index": block.source_index,
        "volume": _round(block.volume),
        "strength": _round(block.strength),
        "mitigated": block.mitigated,
        "mitigation_index": block.mitigation_index,
        "mitigation_timestamp": block.mitigation_timestamp.isoformat() if block.mitigation_timestamp else None,
        "mitigation_level": _round(block.mitigation_level),
    }


def _serialize_pattern(pattern: CandlestickPattern) -> dict[str, Any]:
    return {
        "name": pattern.name,
        "direction": pattern.direction,
        "strength": _round(pattern.strength),
        "timestamp": pattern.timestamp.isoformat(),
        "source_index": pattern.source_index,
    }


def _serialize_bollinger(point: BollingerBandPoint | None) -> dict[str, float | None] | None:
    if point is None:
        return None
    return {
        "middle": _round(point.middle),
        "upper": _round(point.upper),
        "lower": _round(point.lower),
    }


def _serialize_waddah_attar(point: WaddahAttarPoint | None) -> dict[str, Any] | None:
    if point is None:
        return None
    return {
        "explosion": _round(point.explosion),
        "trend": point.trend,
        "dead_zone": _round(point.dead_zone),
        "bullish": point.bullish,
        "bearish": point.bearish,
        "explosion_above_dead_zone": point.explosion_above_dead_zone,
    }


def _last_defined(values: Sequence[float | None]) -> float | None:
    for value in reversed(values):
        if value is not None and math.isfinite(float(value)):
            return float(value)
    return None


def _average(values: Sequence[float]) -> float:
    cleaned = [float(value) for value in values]
    return sum(cleaned) / len(cleaned) if cleaned else 0.0


def _wilder_rsi(average_gain: float, average_loss: float) -> float:
    if average_loss <= 0:
        return 100.0 if average_gain > 0 else 50.0
    relative_strength = average_gain / average_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def _timestamp(candle: CandleLike) -> datetime:
    value = getattr(candle, "candle_timestamp")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    raise TypeError("candle_timestamp must be a datetime")


def _price(candle: CandleLike, field: str) -> float:
    value = getattr(candle, field)
    return float(value or 0.0)


def _percent_of(value: float, reference: float) -> float | None:
    if abs(reference) <= 1e-9:
        return None
    return abs(value) / abs(reference) * 100.0


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    if not math.isfinite(float(value)):
        return None
    return round(float(value), digits)
