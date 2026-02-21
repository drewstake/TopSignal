from datetime import datetime, timedelta, timezone

import pytest

from app.models import ProjectXTradeDaySync
from app.services.projectx_trades import (
    _build_sync_windows,
    _iter_time_chunks,
    _should_refresh_yesterday,
    _single_day_request_utc_date,
)


def _dt(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 2, day, hour, minute, tzinfo=timezone.utc)


def test_build_sync_windows_uses_explicit_start_and_end():
    windows = _build_sync_windows(
        start=_dt(3, 9, 30),
        end=_dt(5, 16, 0),
        now=_dt(20, 0, 0),
        latest_local=None,
        earliest_local=None,
        lookback_days=365,
    )

    assert windows == [(_dt(3, 9, 30), _dt(5, 16, 0))]


def test_build_sync_windows_uses_lookback_floor_for_first_sync():
    now = _dt(20, 12, 0)
    windows = _build_sync_windows(
        start=None,
        end=now,
        now=now,
        latest_local=None,
        earliest_local=None,
        lookback_days=30,
    )

    assert windows == [(now - timedelta(days=30), now)]


def test_build_sync_windows_incremental_only_when_history_floor_is_already_covered():
    now = _dt(20, 12, 0)
    latest = _dt(19, 14, 10)
    earliest = _dt(1, 8, 0)

    windows = _build_sync_windows(
        start=None,
        end=now,
        now=now,
        latest_local=latest,
        earliest_local=earliest,
        lookback_days=10,
    )

    assert windows == [(latest - timedelta(minutes=5), now)]


def test_build_sync_windows_backfills_when_local_history_is_partial():
    now = _dt(20, 12, 0)
    latest = _dt(19, 14, 10)
    earliest = _dt(18, 1, 0)
    history_floor = now - timedelta(days=30)

    windows = _build_sync_windows(
        start=None,
        end=now,
        now=now,
        latest_local=latest,
        earliest_local=earliest,
        lookback_days=30,
    )

    assert windows == [
        (history_floor, earliest),
        (latest - timedelta(minutes=5), now),
    ]


def test_build_sync_windows_rejects_invalid_explicit_range():
    with pytest.raises(ValueError, match="start must be before end"):
        _build_sync_windows(
            start=_dt(20, 12, 0),
            end=_dt(20, 11, 59),
            now=_dt(20, 12, 0),
            latest_local=None,
            earliest_local=None,
            lookback_days=30,
        )


def test_iter_time_chunks_splits_into_contiguous_windows():
    start = _dt(1, 0, 0)
    end = _dt(3, 6, 0)

    chunks = _iter_time_chunks(start, end, chunk_days=1)

    assert len(chunks) == 3
    assert chunks[0] == (start, _dt(2, 0, 0))
    assert chunks[1][0] == _dt(2, 0, 0) + timedelta(microseconds=1)
    assert chunks[1][1] == _dt(3, 0, 0) + timedelta(microseconds=1)
    assert chunks[2][0] == _dt(3, 0, 0) + timedelta(microseconds=2)
    assert chunks[2][1] == end


def test_single_day_request_utc_date_returns_day_when_start_end_match():
    day = _single_day_request_utc_date(
        start=_dt(3, 0, 0),
        end=_dt(3, 23, 59),
    )

    assert day is not None
    assert day.isoformat() == "2026-02-03"


def test_single_day_request_utc_date_returns_none_for_multi_day_range():
    day = _single_day_request_utc_date(
        start=_dt(3, 23, 59),
        end=_dt(4, 0, 0),
    )

    assert day is None


def test_should_refresh_yesterday_when_sync_missing():
    should_refresh = _should_refresh_yesterday(None, now_utc=_dt(20, 12, 0))

    assert should_refresh is True


def test_should_refresh_yesterday_when_sync_is_partial():
    row = ProjectXTradeDaySync(
        account_id=13032451,
        trade_date=_dt(19, 0, 0).date(),
        sync_status="partial",
        last_synced_at=_dt(20, 11, 0),
    )

    should_refresh = _should_refresh_yesterday(row, now_utc=_dt(20, 12, 0))

    assert should_refresh is True


def test_should_refresh_yesterday_when_sync_is_fresh_and_complete(monkeypatch):
    monkeypatch.setenv("PROJECTX_YESTERDAY_REFRESH_MINUTES", "180")
    row = ProjectXTradeDaySync(
        account_id=13032451,
        trade_date=_dt(19, 0, 0).date(),
        sync_status="complete",
        last_synced_at=_dt(20, 11, 0),
    )

    should_refresh = _should_refresh_yesterday(row, now_utc=_dt(20, 12, 0))

    assert should_refresh is False


def test_should_refresh_yesterday_when_sync_is_stale(monkeypatch):
    monkeypatch.setenv("PROJECTX_YESTERDAY_REFRESH_MINUTES", "30")
    row = ProjectXTradeDaySync(
        account_id=13032451,
        trade_date=_dt(19, 0, 0).date(),
        sync_status="complete",
        last_synced_at=_dt(20, 11, 0),
    )

    should_refresh = _should_refresh_yesterday(row, now_utc=_dt(20, 12, 0))

    assert should_refresh is True
