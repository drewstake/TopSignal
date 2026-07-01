from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.services.technical_indicators import (
    build_projectx_style_indicator_snapshot,
    detect_candlestick_patterns,
    detect_fair_value_gaps,
    detect_order_blocks,
    waddah_attar_explosion,
)


@dataclass(frozen=True)
class Candle:
    candle_timestamp: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float = 100.0


def _candle(index: int, close: float, **overrides) -> Candle:
    values = {
        "candle_timestamp": datetime(2026, 4, 1, 9, 30, tzinfo=timezone.utc) + timedelta(minutes=index * 5),
        "open_price": close,
        "high_price": close + 0.5,
        "low_price": close - 0.5,
        "close_price": close,
        "volume": 100.0,
    }
    values.update(overrides)
    return Candle(**values)


def test_detect_fair_value_gap_tracks_mitigation_metadata():
    candles = [
        _candle(0, 99, high_price=100, low_price=98),
        _candle(1, 101, high_price=102, low_price=100),
        _candle(2, 103, high_price=104, low_price=102),
        _candle(3, 101, high_price=103, low_price=100.8),
    ]

    gaps = detect_fair_value_gaps(candles, check_mitigation=True, mitigation_threshold=0.5)

    assert len(gaps) == 1
    assert gaps[0].side == "bullish"
    assert gaps[0].lower_price == 100
    assert gaps[0].upper_price == 102
    assert gaps[0].gap_size == 2
    assert gaps[0].mitigated is True
    assert gaps[0].mitigation_index == 3
    assert gaps[0].mitigation_level == 101


def test_detect_order_blocks_uses_volume_percentile_and_break_logic():
    candles = [
        _candle(0, 98, open_price=100, high_price=101, low_price=97, volume=500),
        _candle(1, 102, open_price=98, high_price=102, low_price=98, volume=50),
        _candle(2, 103, open_price=102, high_price=104, low_price=101, volume=60),
    ]

    blocks = detect_order_blocks(candles, min_volume_percentile=60, lookback_periods=2)

    assert len(blocks) == 1
    assert blocks[0].side == "bullish"
    assert blocks[0].bottom_price == 97
    assert blocks[0].top_price == 101
    assert blocks[0].source_index == 0
    assert blocks[0].strength > 1


def test_detect_candlestick_patterns_identifies_latest_projectx_patterns():
    candles = [
        _candle(0, 100, open_price=101, close_price=99, high_price=101.2, low_price=98.8),
        _candle(1, 102, open_price=98.5, close_price=102.2, high_price=102.4, low_price=98.2),
        _candle(2, 100.02, open_price=100, close_price=100.02, high_price=101, low_price=99),
    ]

    patterns = detect_candlestick_patterns(candles, min_strength=60)
    names = {pattern.name for pattern in patterns}

    assert "bullish_engulfing" in names
    assert "doji" in names


def test_waddah_attar_and_snapshot_return_projectx_style_indicator_state():
    candles = [
        _candle(
            index,
            100 + index * 0.35,
            open_price=99.8 + index * 0.35,
            high_price=100.8 + index * 0.35,
            low_price=99.4 + index * 0.35,
            volume=100 + index,
        )
        for index in range(80)
    ]

    wae = waddah_attar_explosion(candles, fast_period=10, slow_period=20, dead_zone_period=20)
    snapshot = build_projectx_style_indicator_snapshot(candles)

    assert wae[-1].explosion is not None
    assert wae[-1].dead_zone is not None
    assert set(snapshot) >= {
        "rsi",
        "atr",
        "vwap",
        "bollinger",
        "fair_value_gaps",
        "order_blocks",
        "candlestick_patterns",
        "waddah_attar",
    }
    assert snapshot["waddah_attar"] is not None
    assert "active_count" in snapshot["fair_value_gaps"]
