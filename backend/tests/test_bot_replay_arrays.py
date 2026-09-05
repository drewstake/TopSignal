"""Array replay must preserve scalar decisions, boundaries and arithmetic."""

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.services import bot_backtesting as replay
from app.services import bot_service as service


class ArrayRows:
    _topsignal_sorted_closed = True
    _topsignal_verified_replay = True
    _topsignal_mmap_backed = True

    def __init__(self, rows):
        self.rows = rows
        for field in ("open", "high", "low", "close"):
            setattr(self, f"{field}_nano_values", np.asarray(
                [int(getattr(row, f"{field}_price") * 1_000_000_000) for row in rows], dtype=np.int64,
            ))
        self.volume_values = np.asarray([row.volume for row in rows], dtype=np.uint64)
        self.start_ns = np.asarray([
            replay._datetime_to_epoch_ns(row.candle_timestamp) for row in rows
        ], dtype=np.int64)
        self.instrument_id_values = np.asarray([row.source_instrument_id for row in rows])
        self.raw_symbol_code_values = np.asarray([row.source_raw_symbol for row in rows])

    def __len__(self):
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def __getitem__(self, index):
        return ArrayRows(self.rows[index]) if isinstance(index, slice) else self.rows[index]


def candles(count=180, flat=False):
    rng = np.random.default_rng(2519)
    output = []
    price = 50.0
    for index in range(count):
        opening = price
        price += 0 if flat else int(rng.integers(-30, 31)) / 4
        output.append(SimpleNamespace(
            candle_timestamp=datetime(2026, 6, 15, 13, 30, tzinfo=timezone.utc) + timedelta(minutes=5 * index),
            open_price=opening, close_price=price,
            high_price=max(opening, price) + (0 if flat else 1.25),
            low_price=min(opening, price) - (0 if flat else 1.25),
            volume=0 if index % 7 == 0 else int(rng.integers(1, 500)),
            source_instrument_id=1, source_raw_symbol="MNQM6",
        ))
    return output


def test_gap_report_matches_arrays_and_excludes_weekend_and_equity_halt():
    times = [
        datetime(2026, 7, 10, 20, 10, tzinfo=timezone.utc),  # 16:10 ET, halt next
        datetime(2026, 7, 10, 20, 30, tzinfo=timezone.utc),
        datetime(2026, 7, 10, 20, 55, tzinfo=timezone.utc),  # four absent off-hours bars
        datetime(2026, 7, 12, 22, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 12, 22, 5, tzinfo=timezone.utc),
    ]
    rows = candles(len(times))
    for row, timestamp in zip(rows, times):
        row.candle_timestamp = timestamp
        row.symbol = "MNQ"
    kwargs = dict(interval_seconds=300, in_entry_session=lambda _: False)
    scalar = replay._summarize_futures_session_gaps(rows, **kwargs)
    assert scalar == replay._summarize_futures_session_gaps(ArrayRows(rows), **kwargs)
    assert scalar["gap_count"] == replay._count_futures_session_gaps(rows, interval_seconds=300) == 1
    assert scalar["missing_bar_count"] == 4
    assert scalar["in_session_gap_count"] == 0


def test_gap_report_counts_slots_across_entry_open_and_bounds_examples():
    rows = candles(60)
    for index, row in enumerate(rows):
        row.candle_timestamp = datetime(2026, 7, 6, 13, 20, tzinfo=timezone.utc) + timedelta(minutes=15 * index)
        row.symbol = "MNQ"
    result = replay._summarize_futures_session_gaps(
        rows, interval_seconds=300,
        in_entry_session=lambda timestamp: datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc)
        <= timestamp < datetime(2026, 7, 6, 13, 35, tzinfo=timezone.utc),
    )
    assert result["in_session_gap_count"] == 1
    assert result["in_session_missing_bar_count"] == 1
    assert len(result["largest_gaps"]) == 20
    assert sum(year["gap_count"] for year in result["by_year"]) == result["gap_count"]
    assert result["gap_count"] > 20


@pytest.mark.parametrize("period", [1, 14, 200])
@pytest.mark.parametrize("flat", [False, True])
def test_indicator_arrays_exactly_match_scalar_windows(period, flat):
    rows = candles(flat=flat)
    for left, right in [(0, 0), (0, 1), (0, 180), (15, 90), (110, 180)]:
        scalar = rows[left:right]
        arrays = ArrayRows(scalar)
        assert service._candle_close_values(arrays) == service._candle_close_values(scalar)
        assert service._atr_series(arrays, period=period) == service._atr_series(scalar, period=period)
        assert service._adx_series(arrays, period=period) == service._adx_series(scalar, period=period)


@pytest.mark.parametrize("lookback,multiplier", [(1, 1), (20, 1.5), (200, 2)])
def test_gap_invalidation_reuses_volume_without_changing_first_match(lookback, multiplier):
    rows = candles()
    arrays = ArrayRows(rows)
    gaps = service._detect_fair_value_gaps(rows)
    assert gaps
    for gap in gaps:
        kwargs = dict(gap=gap, volume_lookback_bars=lookback, strong_volume_multiplier=multiplier)
        assert service._find_fvg_invalidation_candle(candles=arrays, **kwargs) == service._find_fvg_invalidation_candle(candles=rows, **kwargs)


@pytest.mark.parametrize("field", ["source_instrument_id", "source_raw_symbol"])
def test_delivery_array_boundary_matches_scalar_and_never_sees_future_roll(field):
    rows = candles(10)
    for row in rows[4:7]:
        setattr(row, field, 2 if field == "source_instrument_id" else "MNQU6")
    arrays = ArrayRows(rows)
    for end in range(1, len(rows) + 1):
        for start in range(end):
            kwargs = dict(start_index=start, end_index=end)
            assert replay._contiguous_delivery_start(arrays, **kwargs) == replay._contiguous_delivery_start(rows, **kwargs)


@pytest.mark.parametrize("missing", [None, 0, 4, 8])
def test_session_array_validation_preserves_first_missing_candle_error(missing):
    rows = candles(10)
    expected_start = rows[0].candle_timestamp
    if missing is not None:
        del rows[missing]
    kwargs = dict(expected_start=expected_start, strategy_type="topbot", enforce=True, expected_interval_seconds=300)
    errors = []
    for sequence in [rows, ArrayRows(rows)]:
        try:
            replay._require_complete_session_prefix(sequence, **kwargs)
            errors.append(None)
        except replay.InsufficientBacktestDataError as exc:
            errors.append(str(exc))
    assert errors[0] == errors[1]
    assert (errors[0] is not None) == (missing is not None)
