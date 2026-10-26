"""Read-only OHLCV cross-check and timing report for the local backtesting chart.

Run from the repository root with backend/.venv/Scripts/python and this path.
No database, broker, network access, or writes are used. Output is bounded JSON.
The reusable task prompt is docs/backtesting-audit.prompt.txt.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.databento_cache import get_default_databento_cache  # noqa: E402
from app.services.databento_chart import TIMEFRAMES, chart_candles  # noqa: E402


def main() -> None:
    store = get_default_databento_cache()
    report = {}
    for timeframe, (unit, number) in TIMEFRAMES.items():
        bounds = store.series_bounds("MNQ", unit, number)
        assert bounds is not None, f"Missing {timeframe} cache"
        first, last = bounds
        rows = store.open_candles(user_id="chart-audit", contract_id="MNQ", root_symbol="MNQ",
                                  unit=unit, unit_number=number, start=first, end=last, closed_by=last)
        assert np.all(np.diff(rows.start_ns) > 0), f"Unsorted/duplicate {timeframe} timestamps"
        samples = np.unique(np.concatenate((np.linspace(0, len(rows) - 1, 20, dtype=int),
                                           np.arange(max(0, len(rows) - 20), len(rows)))))
        for index in samples:
            bar = rows[int(index)]
            minutes = store.open_candles(user_id="chart-audit", contract_id="MNQ", root_symbol="MNQ",
                unit="minute", unit_number=1, start=bar.candle_timestamp, end=bar.nominal_close_time,
                closed_by=bar.nominal_close_time)
            assert len(minutes), (timeframe, bar.candle_timestamp)
            assert np.all(minutes.raw_symbol_code_values == minutes.raw_symbol_code_values[0]), "Mixed deliveries"
            expected = (int(minutes.open_nano_values[0]), int(np.max(minutes.high_nano_values)),
                        int(np.min(minutes.low_nano_values)), int(minutes.close_nano_values[-1]),
                        int(np.sum(minutes.volume_values)))
            actual = (round(bar.open_price * 1e9), round(bar.high_price * 1e9),
                      round(bar.low_price * 1e9), round(bar.close_price * 1e9), bar.volume)
            assert actual == expected, (timeframe, str(bar.candle_timestamp), actual, expected)
        timings = []
        for _ in range(8):
            started = time.perf_counter()
            result = chart_candles(user_id="chart-audit", timeframe=timeframe,
                                  start=None, end=None, limit=1000, overview=True)
            timings.append((time.perf_counter() - started) * 1000)
        assert result["total"] == len(rows)
        assert sum(bar["count"] for bar in result["candles"]) == len(rows)
        assert result["candles"][-1]["end_time"] == int(last.timestamp())
        report[timeframe] = {
            "rows": len(rows), "ohlcv_samples_verified": len(samples),
            "first_bar_utc": first.isoformat(), "last_close_utc": last.isoformat(),
            "overview_bars": len(result["candles"]),
            "warm_median_ms": round(statistics.median(timings[1:]), 2),
            "json_bytes": len(json.dumps(result, default=str).encode()),
        }
    print(json.dumps({"ok": True, "timeframes": report}, indent=2))


if __name__ == "__main__":
    main()
