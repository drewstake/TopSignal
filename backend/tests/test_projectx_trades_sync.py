from datetime import datetime, timedelta, timezone

import pytest

from app.services.projectx_trades import _build_sync_windows, _iter_time_chunks


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

