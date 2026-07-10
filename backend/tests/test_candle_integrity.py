from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Lock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.models import ProjectXMarketCandle
from app.services.bot_service import ensure_market_candles, store_market_candles
from app.services.candle_integrity import (
    _reset_request_state_for_tests,
    audit_candle_rows,
    normalize_provider_bars,
    retrieve_bars_singleflight,
)
from app.services.projectx_client import ProjectXClientError


USER_ID = "00000000-0000-0000-0000-000000000000"
CONTRACT_ID = "CON.F.US.MNQ.M26"
BASE = datetime(2026, 4, 1, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def reset_request_state():
    _reset_request_state_for_tests()
    yield
    _reset_request_state_for_tests()


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXMarketCandle.__table__])
        engine.dispose()


def _bar(timestamp: datetime, close: float = 100.0, **overrides):
    bar = {
        "timestamp": timestamp,
        "open": close - 0.25,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": 10.0,
        "is_partial": False,
    }
    bar.update(overrides)
    return bar


def _row(timestamp: datetime, close: float = 100.0, **overrides):
    values = {
        "user_id": USER_ID,
        "contract_id": CONTRACT_ID,
        "symbol": "MNQ",
        "live": False,
        "unit": "minute",
        "unit_number": 5,
        "candle_timestamp": timestamp,
        "open_price": close - 0.25,
        "high_price": close + 0.5,
        "low_price": close - 0.5,
        "close_price": close,
        "volume": 10.0,
        "is_partial": False,
    }
    values.update(overrides)
    return ProjectXMarketCandle(**values)


def _audit(rows, *, start=BASE, end=BASE + timedelta(minutes=15), include_partial=False):
    return audit_candle_rows(
        rows,
        start=start,
        end=end,
        unit="minute",
        unit_number=5,
        limit=100,
        include_partial_bar=include_partial,
        symbol="MNQ",
        as_of=datetime(2026, 4, 2, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_timestamp_prefers_closed_valid_bar_independent_of_order(reverse):
    closed = _bar(BASE, close=101.0)
    partial = _bar(BASE, close=999.0, is_partial=True)
    rows = [partial, closed] if reverse else [closed, partial]

    report = _audit(rows, end=BASE + timedelta(minutes=5), include_partial=True)

    assert report.issue_codes == ("duplicate_candle_timestamp",)
    assert len(report.valid_rows) == 1
    assert report.valid_rows[0]["close"] == 101.0


@pytest.mark.parametrize(
    ("overrides", "issue"),
    [
        ({"high": 99.0}, "invalid_candle_high"),
        ({"low": 100.25}, "invalid_candle_low"),
        ({"open": 0.0}, "non_positive_ohlc"),
        ({"close": float("nan")}, "non_finite_close"),
        ({"volume": -1.0}, "negative_candle_volume"),
    ],
)
def test_invalid_ohlcv_is_excluded_and_scheduled_for_repair(overrides, issue):
    bar = _bar(BASE)
    bar.update(overrides)
    report = _audit([bar], end=BASE + timedelta(minutes=5))

    assert issue in report.issue_codes
    assert report.valid_rows == ()
    assert report.repair_ranges


def test_overlap_is_detected_and_only_one_bar_survives():
    report = _audit(
        [_bar(BASE), _bar(BASE + timedelta(minutes=1), close=101)],
        end=BASE + timedelta(minutes=10),
    )

    assert "overlapping_candles" in report.issue_codes
    assert len(report.valid_rows) == 1
    assert report.valid_rows[0]["timestamp"] == BASE


def test_open_session_gap_and_incomplete_tail_are_detected():
    report = _audit(
        [_bar(BASE), _bar(BASE + timedelta(minutes=10))],
        end=BASE + timedelta(minutes=20),
    )

    assert "missing_candle" in report.issue_codes
    assert [(item.start, item.end) for item in report.missing_ranges] == [
        (BASE + timedelta(minutes=5), BASE + timedelta(minutes=10)),
        (BASE + timedelta(minutes=15), BASE + timedelta(minutes=20)),
    ]


def test_large_second_gap_uses_bounded_broad_repair_window():
    end = BASE + timedelta(hours=8)
    report = audit_candle_rows(
        [_bar(BASE), _bar(end)],
        start=BASE,
        end=end + timedelta(seconds=1),
        unit="second",
        unit_number=1,
        limit=50_000,
        include_partial_bar=False,
        symbol="MNQ",
        as_of=datetime(2026, 4, 2, tzinfo=timezone.utc),
    )

    assert "missing_candle" in report.issue_codes
    assert report.repair_ranges
    assert len(report.repair_ranges) <= 8


def test_daily_maintenance_gap_is_not_repaired():
    # 2026-04-01 is EDT: 17:00-18:00 ET is 21:00-22:00 UTC.
    before_close = datetime(2026, 4, 1, 20, 55, tzinfo=timezone.utc)
    reopen = datetime(2026, 4, 1, 22, 0, tzinfo=timezone.utc)
    report = _audit(
        [_bar(before_close), _bar(reopen)],
        start=before_close,
        end=reopen + timedelta(minutes=5),
    )

    assert "missing_candle" not in report.issue_codes


def test_weekend_gap_is_not_repaired():
    friday_close = datetime(2026, 4, 10, 20, 55, tzinfo=timezone.utc)
    sunday_open = datetime(2026, 4, 12, 22, 0, tzinfo=timezone.utc)
    report = _audit(
        [_bar(friday_close), _bar(sunday_open)],
        start=friday_close,
        end=sunday_open + timedelta(minutes=5),
    )

    assert "missing_candle" not in report.issue_codes


def test_holiday_early_close_gap_is_not_repaired():
    # MLK Day 2026 closes at 13:00 ET (18:00 UTC) and reopens at 18:00 ET.
    before_close = datetime(2026, 1, 19, 17, 55, tzinfo=timezone.utc)
    reopen = datetime(2026, 1, 19, 23, 0, tzinfo=timezone.utc)
    report = _audit(
        [_bar(before_close), _bar(reopen)],
        start=before_close,
        end=reopen + timedelta(minutes=5),
    )

    assert "missing_candle" not in report.issue_codes


def test_stale_partial_is_excluded_and_scheduled_for_authoritative_replacement():
    report = _audit(
        [_bar(BASE, is_partial=True)],
        end=BASE + timedelta(minutes=5),
        include_partial=True,
    )

    assert report.valid_rows == ()
    assert report.stale_partial_rows
    assert "stale_partial_candle" in report.issue_codes


def test_current_partial_is_allowed_until_its_bucket_closes():
    report = audit_candle_rows(
        [_bar(BASE, is_partial=True)],
        start=BASE,
        end=BASE + timedelta(minutes=5),
        unit="minute",
        unit_number=5,
        limit=10,
        include_partial_bar=True,
        symbol="MNQ",
        as_of=BASE + timedelta(minutes=2),
    )

    assert report.is_complete
    assert len(report.valid_rows) == 1
    assert len(report.current_partial_rows) == 1


def test_provider_duplicate_normalization_prefers_closed_bar():
    normalized = normalize_provider_bars(
        [_bar(BASE, close=999, is_partial=True), _bar(BASE, close=101)],
        unit="minute",
        unit_number=5,
        request_end=BASE + timedelta(minutes=5),
        include_partial_bar=True,
        as_of=datetime(2026, 4, 2, tzinfo=timezone.utc),
    )

    assert len(normalized) == 1
    assert normalized[0]["close"] == 101
    assert normalized[0]["is_partial"] is False


def test_provider_normalization_rejects_bars_outside_requested_closed_window():
    normalized = normalize_provider_bars(
        [
            _bar(BASE - timedelta(minutes=5)),
            _bar(BASE),
            _bar(BASE + timedelta(minutes=5)),
        ],
        unit="minute",
        unit_number=5,
        request_start=BASE,
        request_end=BASE + timedelta(minutes=5),
        include_partial_bar=False,
        as_of=datetime(2026, 4, 2, tzinfo=timezone.utc),
    )

    assert [row["timestamp"] for row in normalized] == [BASE]


def test_singleflight_deduplicates_concurrent_provider_requests():
    call_count = 0
    guard = Lock()

    def retrieve():
        nonlocal call_count
        with guard:
            call_count += 1
        time.sleep(0.1)
        return [_bar(BASE)]

    def invoke():
        return retrieve_bars_singleflight(key=("same",), retrieve=retrieve)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _index: invoke(), range(4)))

    assert call_count == 1
    assert [result[0]["timestamp"] for result in results] == [BASE] * 4


def test_transient_retries_are_capped_and_nontransient_errors_are_not_retried():
    transient_calls = 0

    def transient():
        nonlocal transient_calls
        transient_calls += 1
        raise ProjectXClientError("timeout", status_code=504)

    with pytest.raises(ProjectXClientError):
        retrieve_bars_singleflight(key=("transient",), retrieve=transient)
    assert transient_calls == 3

    permanent_calls = 0

    def permanent():
        nonlocal permanent_calls
        permanent_calls += 1
        raise ProjectXClientError("bad request", status_code=400)

    with pytest.raises(ProjectXClientError):
        retrieve_bars_singleflight(key=("permanent",), retrieve=permanent)
    assert permanent_calls == 1


def test_closed_database_row_cannot_be_downgraded_by_partial(db_session):
    store_market_candles(
        db_session,
        user_id=USER_ID,
        contract_id=CONTRACT_ID,
        symbol="MNQ",
        live=False,
        unit="minute",
        unit_number=5,
        bars=[_bar(BASE, close=101)],
    )
    db_session.flush()
    store_market_candles(
        db_session,
        user_id=USER_ID,
        contract_id=CONTRACT_ID,
        symbol="MNQ",
        live=False,
        unit="minute",
        unit_number=5,
        bars=[_bar(BASE, close=999, is_partial=True)],
    )
    db_session.flush()

    row = db_session.query(ProjectXMarketCandle).one()
    assert row.is_partial is False
    assert float(row.close_price) == 101.0


def test_stale_partial_is_replaced_in_place_by_closed_provider_bar(db_session):
    db_session.add(_row(BASE, close=100, is_partial=True))
    db_session.flush()

    class Client:
        def retrieve_bars(self, **_kwargs):
            return [_bar(BASE, close=102, is_partial=False)]

    rows = ensure_market_candles(
        db_session,
        user_id=USER_ID,
        client=Client(),
        contract_id=CONTRACT_ID,
        symbol="MNQ",
        live=False,
        start=BASE,
        end=BASE + timedelta(minutes=5),
        unit="minute",
        unit_number=5,
        limit=10,
    )

    assert len(rows) == 1
    assert db_session.query(ProjectXMarketCandle).count() == 1
    assert rows[0].is_partial is False
    assert float(rows[0].close_price) == 102.0


def test_empty_provider_response_never_creates_synthetic_gap_bar(db_session):
    db_session.add_all([_row(BASE), _row(BASE + timedelta(minutes=10), close=102)])
    db_session.flush()

    class Client:
        calls = 0

        def retrieve_bars(self, **_kwargs):
            self.calls += 1
            return []

    client = Client()
    rows = ensure_market_candles(
        db_session,
        user_id=USER_ID,
        client=client,
        contract_id=CONTRACT_ID,
        symbol="MNQ",
        live=False,
        start=BASE,
        end=BASE + timedelta(minutes=15),
        unit="minute",
        unit_number=5,
        limit=10,
    )
    repeated_rows = ensure_market_candles(
        db_session,
        user_id=USER_ID,
        client=client,
        contract_id=CONTRACT_ID,
        symbol="MNQ",
        live=False,
        start=BASE,
        end=BASE + timedelta(minutes=15),
        unit="minute",
        unit_number=5,
        limit=10,
    )

    assert client.calls == 1
    assert [
        row.candle_timestamp.replace(tzinfo=row.candle_timestamp.tzinfo or timezone.utc)
        for row in rows
    ] == [BASE, BASE + timedelta(minutes=10)]
    assert len(repeated_rows) == 2
    assert db_session.query(ProjectXMarketCandle).count() == 2


def test_provider_failure_uses_vetted_cache_after_exact_retry_cap(db_session):
    db_session.add(_row(BASE, close=101))
    db_session.flush()

    class Client:
        calls = 0

        def retrieve_bars(self, **_kwargs):
            self.calls += 1
            raise ProjectXClientError("timeout", status_code=504)

    client = Client()
    rows = ensure_market_candles(
        db_session,
        user_id=USER_ID,
        client=client,
        contract_id=CONTRACT_ID,
        symbol="MNQ",
        live=False,
        start=BASE,
        end=BASE + timedelta(minutes=5),
        unit="minute",
        unit_number=5,
        limit=10,
        force_refresh=True,
    )

    assert client.calls == 3
    assert len(rows) == 1
    assert float(rows[0].close_price) == 101.0


def test_invalid_provider_bar_cannot_overwrite_good_cache(db_session):
    db_session.add(_row(BASE, close=101))
    db_session.flush()

    class Client:
        def retrieve_bars(self, **_kwargs):
            return [_bar(BASE, close=999, high=998)]

    rows = ensure_market_candles(
        db_session,
        user_id=USER_ID,
        client=Client(),
        contract_id=CONTRACT_ID,
        symbol="MNQ",
        live=False,
        start=BASE,
        end=BASE + timedelta(minutes=5),
        unit="minute",
        unit_number=5,
        limit=10,
        force_refresh=True,
    )

    assert len(rows) == 1
    assert float(rows[0].close_price) == 101.0
