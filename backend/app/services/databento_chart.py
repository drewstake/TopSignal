"""Bounded, read-only chart windows over the existing filesystem replay cache."""

from datetime import datetime, timedelta, timezone

import numpy as np

from .databento_cache import DatabentoCacheMissingError, get_default_databento_cache


TIMEFRAMES = {"1m": ("minute", 1), "5m": ("minute", 5), "4h": ("hour", 4), "1d": ("day", 1)}


def _overview_candles(rows, limit: int) -> tuple[list[dict], int]:
    """Cover every input row with OHLCV groups; never decimate away extrema.

    Work directly on mmap arrays rather than materializing the full history.
    Split at contract changes when the response budget permits it.
    """
    total = len(rows)
    if not total:
        return [], 1
    codes = rows.raw_symbol_code_values
    transitions = np.flatnonzero(codes[1:] != codes[:-1]) + 1
    preserve_contracts = len(transitions) < limit
    budget = limit - len(transitions) if preserve_contracts else limit
    step = 1 if total <= limit else max(1, (total + budget - 1) // budget)
    starts = np.arange(0, total, step)
    if preserve_contracts:
        starts = np.unique(np.concatenate((starts, transitions)))
    ends = np.concatenate((starts[1:], [total]))
    highs = np.maximum.reduceat(rows.high_nano_values, starts)
    lows = np.minimum.reduceat(rows.low_nano_values, starts)
    volumes = np.add.reduceat(rows.volume_values, starts)
    sampled_codes, sample_indices = np.unique(codes[starts], return_index=True)
    contract_names = {
        int(code): rows[int(starts[index])].source_raw_symbol
        for code, index in zip(sampled_codes, sample_indices)
    }
    candles = []
    for index, (left, right) in enumerate(zip(starts, ends)):
        contract = contract_names[int(codes[left])]
        if not preserve_contracts and np.any(codes[left:right] != codes[left]):
            contract = "Multiple contracts"
        candles.append({"time": int(rows.start_ns[left] // 1_000_000_000),
            "end_time": int(rows.close_ns[right - 1] // 1_000_000_000),
            "open": int(rows.open_nano_values[left]) / 1_000_000_000,
            "high": int(highs[index]) / 1_000_000_000,
            "low": int(lows[index]) / 1_000_000_000,
            "close": int(rows.close_nano_values[right - 1]) / 1_000_000_000,
            "volume": int(volumes[index]), "contract": contract,
            "count": int(right - left)})
    return candles, int(np.max(ends - starts))


def chart_candles(*, user_id: str, timeframe: str, start: datetime | None,
                  end: datetime | None, limit: int, overview: bool = False) -> dict:
    store = get_default_databento_cache()
    unit, number = TIMEFRAMES[timeframe]
    bounds = store.series_bounds("MNQ", unit, number)
    if bounds is None:
        raise DatabentoCacheMissingError("MNQ history is not installed")
    first, last = bounds
    for value in (start, end):
        if value is not None and (value.tzinfo is None or not 2010 <= value.year <= 2100):
            raise ValueError("Use timezone-aware dates between 2010 and 2100.")
    end = end.astimezone(timezone.utc) if end else last
    start = start.astimezone(timezone.utc) if start else first if overview else max(first, end - timedelta(days=7))
    if start >= end:
        raise ValueError("Start must be before end.")
    rows = store.open_candles(user_id=user_id, contract_id="MNQ", root_symbol="MNQ",
        unit=unit, unit_number=number, start=start, end=end, closed_by=min(end, last))
    total = len(rows)
    aggregate_size = 1
    if overview:
        candles, aggregate_size = _overview_candles(rows, limit)
    else:
        # Legacy tail windows remain bounded. Overview requests cover ALL rows.
        candles = [{"time": int(row.candle_timestamp.timestamp()),
            "end_time": int(row.nominal_close_time.timestamp()), "count": 1, "open": row.open_price,
            "high": row.high_price, "low": row.low_price, "close": row.close_price,
            "volume": row.volume, "contract": row.source_raw_symbol}
            for row in rows[max(0, total - limit):]]
    return {"symbol": "MNQ", "source": "Databento", "timeframe": timeframe,
        "coverage_start": first, "coverage_end": last, "start": start, "end": end,
        "total": total, "truncated": not overview and total > limit,
        "aggregate_size": aggregate_size, "candles": candles}
