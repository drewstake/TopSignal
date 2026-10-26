from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import numpy as np
from fastapi import FastAPI

from app.market_data_routes import market_data_user_id, router
from app.services import databento_chart as chart
from app.services.databento_cache import DatabentoCacheMissingError, DatabentoCacheStaleError
from test_market_data_workspace import request


STAMP = datetime(2026, 7, 10, tzinfo=timezone.utc)


@pytest.fixture
def app():
    instance = FastAPI()
    instance.include_router(router)
    instance.dependency_overrides[market_data_user_id] = lambda: "chart-reader"
    return instance


def test_large_history_is_sliced_before_materialization(monkeypatch, app):
    visited = []
    calls = []

    class LazyRows:
        def __len__(self):
            return 2_500_000

        def __getitem__(self, value):
            assert isinstance(value, slice)
            assert value.start == 2_499_997
            visited.append(value)
            return [SimpleNamespace(candle_timestamp=STAMP + timedelta(minutes=i),
                nominal_close_time=STAMP + timedelta(minutes=i + 1), open_price=20000, high_price=20001, low_price=19999, close_price=20000.25,
                volume=123, source_raw_symbol="MNQU6") for i in range(3)]

    def open_candles(**kwargs):
        calls.append(kwargs)
        return LazyRows()

    monkeypatch.setattr(chart, "get_default_databento_cache", lambda: SimpleNamespace(
        series_bounds=lambda symbol, unit, number: (STAMP - timedelta(days=2000), STAMP + timedelta(days=1)),
        open_candles=open_candles))
    result = request(app, "GET", "/api/market-data/databento/candles", params={"limit": 3})
    assert result.status_code == 200
    body = result.json()
    assert body["total"] == 2_500_000 and body["truncated"] is True
    assert len(body["candles"]) == 3 and len(visited) == 1
    assert body["candles"][0]["contract"] == "MNQU6"
    assert body["candles"][0]["time"] == int(STAMP.timestamp())
    assert calls[0]["user_id"] == "chart-reader"
    assert (calls[0]["unit"], calls[0]["unit_number"]) == ("minute", 5)


@pytest.mark.parametrize("params", [{"limit": 10001}, {"limit": 0}, {"timeframe": "1s"}])
def test_rejects_unbounded_or_unsupported_requests(app, params):
    assert request(app, "GET", "/api/market-data/databento/candles", params=params).status_code == 422


@pytest.mark.parametrize("start,end", [
    ("2026-07-10T00:00:00", "2026-07-11T00:00:00Z"),
    ("2026-07-11T00:00:00Z", "2026-07-10T00:00:00Z"),
    ("0001-01-01T00:00:00Z", "2026-07-10T00:00:00Z"),
])
def test_invalid_date_ranges_never_open_series(monkeypatch, app, start, end):
    monkeypatch.setattr(chart, "get_default_databento_cache", lambda: SimpleNamespace(
        series_bounds=lambda symbol, unit, number: (STAMP - timedelta(days=7), STAMP)))
    assert request(app, "GET", "/api/market-data/databento/candles", params={"start": start, "end": end}).status_code == 422


def test_empty_window_and_history_end_clipping(monkeypatch, app):
    calls = []
    def open_candles(**kwargs):
        calls.append(kwargs)
        return []
    monkeypatch.setattr(chart, "get_default_databento_cache", lambda: SimpleNamespace(
        series_bounds=lambda symbol, unit, number: (STAMP - timedelta(days=7), STAMP), open_candles=open_candles))
    result = request(app, "GET", "/api/market-data/databento/candles", params={
        "start": "2026-07-11T00:00:00Z", "end": "2026-07-12T00:00:00Z"})
    assert result.status_code == 200
    assert result.json()["candles"] == []
    assert result.json()["truncated"] is False
    assert calls[0]["closed_by"] == STAMP


@pytest.mark.parametrize("error,status", [(DatabentoCacheMissingError, 404), (DatabentoCacheStaleError, 409)])
def test_cache_errors_do_not_disclose_filesystem_paths(monkeypatch, app, error, status):
    def fail():
        raise error("private/path/to/archive")
    monkeypatch.setattr(chart, "get_default_databento_cache", fail)
    result = request(app, "GET", "/api/market-data/databento/candles")
    assert result.status_code == status
    assert "private" not in str(result.json())


def test_authentication_precedes_cache_access(monkeypatch):
    import app.market_data_routes as routes
    instance = FastAPI()
    instance.include_router(router)
    monkeypatch.setattr(routes, "auth_required", lambda: True)
    monkeypatch.setattr(routes, "get_authenticated_user", lambda: None)
    result = request(instance, "GET", "/api/market-data/databento/candles")
    assert result.status_code == 401


class ArrayRows:
    def __init__(self):
        self.start_ns = (int(STAMP.timestamp()) + np.arange(10) * 60) * 1_000_000_000
        self.close_ns = self.start_ns + 60_000_000_000
        self.open_nano_values = np.arange(100, 110) * 1_000_000_000
        self.high_nano_values = self.open_nano_values + 5_000_000_000
        self.low_nano_values = self.open_nano_values - 5_000_000_000
        self.close_nano_values = self.open_nano_values + 1_000_000_000
        self.volume_values = np.arange(1, 11, dtype=np.uint64)
        self.raw_symbol_code_values = np.array([1] * 5 + [2] * 5)

    def __len__(self):
        return 10

    def __getitem__(self, index):
        return SimpleNamespace(source_raw_symbol="MNQU6" if index < 5 else "MNQZ6")


@pytest.mark.parametrize("limit", [1, 2, 3, 4, 10, 2000])
def test_overview_preserves_every_candle_and_ohlcv(limit):
    rows = ArrayRows()
    candles, resolution = chart._overview_candles(rows, limit)
    assert len(candles) <= limit
    assert sum(row["count"] for row in candles) == len(rows)
    assert sum(row["volume"] for row in candles) == 55
    assert candles[0]["open"] == 100 and candles[-1]["close"] == 110
    assert min(row["low"] for row in candles) == 95
    assert max(row["high"] for row in candles) == 114
    assert candles[0]["time"] == int(STAMP.timestamp())
    assert candles[-1]["end_time"] == int(STAMP.timestamp()) + 600
    assert resolution == max(row["count"] for row in candles)
    if limit >= 2:
        assert {row["contract"] for row in candles} == {"MNQU6", "MNQZ6"}
    else:
        assert candles[0]["contract"] == "Multiple contracts"
    if limit >= 10:
        assert resolution == 1
        assert [row["close"] for row in candles] == list(range(101, 111))


def test_overview_defaults_to_entire_history(monkeypatch, app):
    first = STAMP - timedelta(days=2000)
    calls = []
    def open_candles(**kwargs):
        calls.append(kwargs)
        return ArrayRows()
    monkeypatch.setattr(chart, "get_default_databento_cache", lambda: SimpleNamespace(
        series_bounds=lambda symbol, unit, number: (first, STAMP + timedelta(days=1)), open_candles=open_candles))
    result = request(app, "GET", "/api/market-data/databento/candles", params={"overview": "true", "limit": 3})
    assert result.status_code == 200
    assert calls[0]["start"] == first
    assert result.json()["truncated"] is False
    assert sum(row["count"] for row in result.json()["candles"]) == 10


def test_chart_uses_completed_timeframe_bounds_during_maintenance(monkeypatch, app):
    calls = []
    def bounds(symbol, unit, number):
        assert (symbol, unit, number) == ("MNQ", "day", 1)
        return STAMP - timedelta(days=1), STAMP + timedelta(hours=22)
    def open_candles(**kwargs):
        calls.append(kwargs)
        return []
    monkeypatch.setattr(chart, "get_default_databento_cache", lambda: SimpleNamespace(
        series_bounds=bounds, open_candles=open_candles))
    result = request(app, "GET", "/api/market-data/databento/candles", params={"timeframe": "1d", "overview": True})
    assert result.status_code == 200
    assert calls[0]["closed_by"] == STAMP + timedelta(hours=22)
    assert calls[0]["end"] == STAMP + timedelta(hours=22)
