import os
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.models import DEFAULT_USER_ID, ProjectXTradeDaySync, ProjectXTradeEvent
from app.services import projectx_trades as projectx_trades_module
from app.services.projectx_trades import (
    _build_sync_windows,
    _iter_time_chunks,
    _should_refresh_yesterday,
    _single_trading_day_request_date,
    ensure_trade_cache_for_request,
    refresh_account_trades,
)
from app.services.projectx_client import ProjectXClientError
from app.services.trading_day import trading_day_bounds_utc


def _dt(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 2, day, hour, minute, tzinfo=timezone.utc)


def _make_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[ProjectXTradeEvent.__table__, ProjectXTradeDaySync.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, SessionLocal()


def _event(account_id: int, timestamp: datetime, source_trade_id: str = "SRC-1") -> dict[str, object]:
    return {
        "account_id": account_id,
        "contract_id": "CON.F.US.MNQ.H26",
        "symbol": "MNQ",
        "side": "BUY",
        "size": 1.0,
        "price": 20500.0,
        "timestamp": timestamp,
        "fees": 1.4,
        "pnl": 45.0,
        "order_id": f"ORD-{source_trade_id}",
        "source_trade_id": source_trade_id,
        "status": "FILLED",
        "raw_payload": {"id": source_trade_id},
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class _RecordingClient:
    def __init__(self, events: list[dict[str, object]]):
        self.events = events
        self.calls: list[tuple[int, datetime, datetime | None, int | None, int | None]] = []

    def fetch_trade_history(self, account_id, start, end=None, *, limit=None, offset=None):
        self.calls.append((account_id, start, end, limit, offset))
        return list(self.events)


class _PagedClient:
    def __init__(self, events: list[dict[str, object]]):
        self.events = events
        self.calls: list[tuple[int, datetime, datetime | None, int | None, int | None]] = []

    def fetch_trade_history(self, account_id, start, end=None, *, limit=None, offset=None):
        self.calls.append((account_id, start, end, limit, offset))
        page_limit = int(limit or len(self.events) or 1)
        page_offset = int(offset or 0)
        return list(self.events[page_offset : page_offset + page_limit])


class _FailingClient:
    def fetch_trade_history(self, account_id, start, end=None, *, limit=None, offset=None):
        raise ProjectXClientError("provider unavailable", status_code=504)


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


def test_build_sync_windows_can_revisit_recent_gap_between_local_events():
    now = _dt(20, 12, 0)
    latest = _dt(19, 14, 10)
    earliest = _dt(1, 8, 0)
    recent_floor = now - timedelta(days=10)

    windows = _build_sync_windows(
        start=None,
        end=now,
        now=now,
        latest_local=latest,
        earliest_local=earliest,
        lookback_days=15,
        recent_backfill_days=10,
    )

    assert windows == [(recent_floor, now)]


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


def test_refresh_account_trades_pages_through_provider_results(monkeypatch):
    monkeypatch.setenv("PROJECTX_DAY_SYNC_LIMIT", "2")
    engine, db = _make_session()
    try:
        account_id = 13032506
        start = _dt(3, 14, 0)
        end = _dt(3, 16, 0)
        client = _PagedClient(
            [
                _event(account_id, start + timedelta(minutes=10), "SRC-PAGE-1"),
                _event(account_id, start + timedelta(minutes=20), "SRC-PAGE-2"),
                _event(account_id, start + timedelta(minutes=30), "SRC-PAGE-3"),
            ]
        )

        result = refresh_account_trades(
            db,
            client,
            user_id=DEFAULT_USER_ID,
            account_id=account_id,
            start=start,
            end=end,
        )

        assert result == {"fetched_count": 3, "inserted_count": 3}
        assert [call[3:] for call in client.calls] == [(2, 0), (2, 2)]
        assert db.query(ProjectXTradeEvent).filter(ProjectXTradeEvent.account_id == account_id).count() == 3
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeDaySync.__table__, ProjectXTradeEvent.__table__])
        engine.dispose()


def test_stale_provider_event_cannot_downgrade_completed_trade():
    engine, db = _make_session()
    try:
        account_id = 13032509
        timestamp = _dt(3, 14, 10)
        completed = _event(account_id, timestamp, "SRC-COMPLETE")
        completed.update(
            {
                "pnl": 125.0,
                "fees": 2.5,
                "status": "FILLED",
                "raw_payload": {"state": "completed"},
            }
        )
        projectx_trades_module.store_trade_events(
            db,
            [completed],
            user_id=DEFAULT_USER_ID,
        )
        db.commit()

        stale = dict(completed)
        stale.update(
            {
                "contract_id": "STALE-CONTRACT",
                "pnl": None,
                "fees": 0.0,
                "status": "PENDING",
                "raw_payload": {"state": "older-page"},
            }
        )
        inserted = projectx_trades_module.store_trade_events(
            db,
            [stale],
            user_id=DEFAULT_USER_ID,
        )
        db.commit()

        stored = db.query(ProjectXTradeEvent).one()
        assert inserted == 0
        assert stored.contract_id == completed["contract_id"]
        assert float(stored.pnl) == 125.0
        assert float(stored.fees) == 2.5
        assert stored.status == "FILLED"
        assert stored.raw_payload == {"state": "completed"}
    finally:
        db.close()
        Base.metadata.drop_all(
            bind=engine,
            tables=[ProjectXTradeDaySync.__table__, ProjectXTradeEvent.__table__],
        )
        engine.dispose()


def test_explicit_provider_correction_can_clear_completed_pnl():
    engine, db = _make_session()
    try:
        account_id = 13032510
        timestamp = _dt(3, 14, 10)
        completed = _event(account_id, timestamp, "SRC-CORRECTION")
        projectx_trades_module.store_trade_events(
            db,
            [completed],
            user_id=DEFAULT_USER_ID,
        )
        db.commit()

        correction = dict(completed)
        correction.update(
            {
                "pnl": None,
                "status": "PENDING",
                "correction": True,
                "raw_payload": {"isCorrection": True},
            }
        )
        projectx_trades_module.store_trade_events(
            db,
            [correction],
            user_id=DEFAULT_USER_ID,
        )
        db.commit()

        stored = db.query(ProjectXTradeEvent).one()
        assert stored.pnl is None
        assert stored.status == "PENDING"
    finally:
        db.close()
        Base.metadata.drop_all(
            bind=engine,
            tables=[ProjectXTradeDaySync.__table__, ProjectXTradeEvent.__table__],
        )
        engine.dispose()


def test_refresh_retries_expected_unique_race_without_losing_or_overreporting_chunk():
    engine, db = _make_session()
    try:
        account_id = 13032507
        start = _dt(3, 14, 0)
        end = _dt(3, 16, 0)
        client = _RecordingClient([_event(account_id, start + timedelta(minutes=10), "SRC-RACE")])
        real_commit = db.commit
        commit_calls = 0

        class Diagnostic:
            constraint_name = "uq_projectx_trade_events_account_source_trade"

        class UniqueRace(Exception):
            diag = Diagnostic()

        def commit_with_one_unique_race():
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                raise IntegrityError("insert", {}, UniqueRace("duplicate"))
            real_commit()

        db.commit = commit_with_one_unique_race

        result = refresh_account_trades(
            db,
            client,
            user_id=DEFAULT_USER_ID,
            account_id=account_id,
            start=start,
            end=end,
        )

        assert result == {"fetched_count": 1, "inserted_count": 1}
        assert commit_calls == 2
        assert db.query(ProjectXTradeEvent).filter(ProjectXTradeEvent.account_id == account_id).count() == 1
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeDaySync.__table__, ProjectXTradeEvent.__table__])
        engine.dispose()


def test_refresh_propagates_non_unique_integrity_failure():
    engine, db = _make_session()
    try:
        account_id = 13032508
        start = _dt(3, 14, 0)
        client = _RecordingClient([_event(account_id, start + timedelta(minutes=10), "SRC-BAD")])

        class Diagnostic:
            constraint_name = "projectx_trade_events_whole_size_check"

        class CheckFailure(Exception):
            diag = Diagnostic()

        db.commit = lambda: (_ for _ in ()).throw(IntegrityError("insert", {}, CheckFailure("bad size")))

        with pytest.raises(IntegrityError):
            refresh_account_trades(
                db,
                client,
                user_id=DEFAULT_USER_ID,
                account_id=account_id,
                start=start,
                end=_dt(3, 16, 0),
            )

        assert db.query(ProjectXTradeEvent).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeDaySync.__table__, ProjectXTradeEvent.__table__])
        engine.dispose()


@pytest.mark.parametrize(
    "constraint_name",
    [
        "projectx_trade_events_user_id_account_id_source_trade_id_key",
        "projectx_trade_events_user_id_account_id_order_id_trade_timesta",
    ],
)
def test_legacy_autogenerated_trade_event_unique_constraints_are_recognized(constraint_name):
    class Diagnostic:
        table_name = "projectx_trade_events"

        def __init__(self, name):
            self.constraint_name = name

    class LegacyUnique(Exception):
        def __init__(self, name):
            super().__init__("duplicate")
            self.diag = Diagnostic(name)

    exc = IntegrityError("insert", {}, LegacyUnique(constraint_name))

    assert projectx_trades_module._is_expected_trade_event_unique_conflict(exc) is True


def test_legacy_trade_constraint_name_on_another_table_is_not_treated_as_duplicate():
    class Diagnostic:
        table_name = "unrelated_events"
        constraint_name = "projectx_trade_events_user_id_account_id_order_id_trade_timesta"

    class UnrelatedFailure(Exception):
        diag = Diagnostic()

    exc = IntegrityError("insert", {}, UnrelatedFailure("unrelated constraint"))

    assert projectx_trades_module._is_expected_trade_event_unique_conflict(exc) is False


def test_single_trading_day_request_date_returns_day_when_start_end_match():
    day = _single_trading_day_request_date(
        start=_dt(3, 14, 0),
        end=_dt(3, 15, 0),
    )

    assert day is not None
    assert day.isoformat() == "2026-02-03"


def test_single_trading_day_request_date_returns_day_for_new_york_trading_day_range():
    day = _single_trading_day_request_date(
        start=datetime(2026, 3, 1, 23, 0, tzinfo=timezone.utc),
        end=datetime(2026, 3, 2, 22, 59, 59, 999000, tzinfo=timezone.utc),
    )

    assert day is not None
    assert day.isoformat() == "2026-03-02"


def test_single_trading_day_request_date_returns_none_for_multi_day_range():
    day = _single_trading_day_request_date(
        start=datetime(2026, 3, 2, 22, 59, tzinfo=timezone.utc),
        end=datetime(2026, 3, 2, 23, 0, tzinfo=timezone.utc),
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


def test_should_refresh_yesterday_when_complete_sync_window_is_missing(monkeypatch):
    monkeypatch.setenv("PROJECTX_YESTERDAY_REFRESH_MINUTES", "180")
    trade_day = date(2026, 2, 19)
    window_start, window_end = trading_day_bounds_utc(trade_day)
    row = ProjectXTradeDaySync(
        account_id=13032451,
        trade_date=trade_day,
        sync_status="complete",
        last_synced_at=_dt(20, 11, 0),
    )

    should_refresh = _should_refresh_yesterday(
        row,
        now_utc=_dt(20, 12, 0),
        window_start=window_start,
        window_end=window_end,
    )

    assert should_refresh is True


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


def test_equal_complete_refresh_advances_stale_marker_and_row_count(monkeypatch):
    monkeypatch.setenv("PROJECTX_YESTERDAY_REFRESH_MINUTES", "30")
    engine, db = _make_session()
    try:
        account_id = 13032452
        trade_day = date(2026, 2, 19)
        window_start, window_end = trading_day_bounds_utc(trade_day)
        db.add(
            ProjectXTradeDaySync(
                user_id=DEFAULT_USER_ID,
                account_id=account_id,
                trade_date=trade_day,
                window_start=window_start,
                window_end=window_end,
                sync_status="complete",
                last_synced_at=_dt(20, 10, 0),
                row_count=99,
                updated_at=_dt(20, 10, 0),
            )
        )
        db.commit()

        projectx_trades_module._upsert_trade_day_sync(
            db,
            user_id=DEFAULT_USER_ID,
            account_id=account_id,
            trade_day=trade_day,
            window_start=window_start,
            window_end=window_end,
            sync_status="complete",
            last_synced_at=_dt(20, 12, 0),
            row_count=3,
        )
        db.commit()

        marker = db.query(ProjectXTradeDaySync).one()
        assert _as_utc(marker.last_synced_at) == _dt(20, 12, 0)
        assert _as_utc(marker.updated_at) == _dt(20, 12, 0)
        assert marker.row_count == 3
        assert _should_refresh_yesterday(
            marker,
            now_utc=_dt(20, 12, 1),
            window_start=window_start,
            window_end=window_end,
        ) is False
    finally:
        db.close()
        Base.metadata.drop_all(
            bind=engine,
            tables=[ProjectXTradeDaySync.__table__, ProjectXTradeEvent.__table__],
        )
        engine.dispose()


def test_ensure_trade_cache_records_trading_day_window_and_reuses_complete_cache(monkeypatch):
    monkeypatch.delenv("PROJECTX_DAY_SYNC_LIMIT", raising=False)
    engine, db = _make_session()
    try:
        account_id = 13032501
        trade_day = date(2026, 3, 2)
        window_start, window_end = trading_day_bounds_utc(trade_day)
        client = _RecordingClient([_event(account_id, window_start + timedelta(minutes=30))])

        ensure_trade_cache_for_request(
            db,
            user_id=DEFAULT_USER_ID,
            account_id=account_id,
            start=window_start,
            end=window_end,
            refresh=False,
            client_factory=lambda: client,
        )

        sync_row = (
            db.query(ProjectXTradeDaySync)
            .filter(ProjectXTradeDaySync.account_id == account_id)
            .filter(ProjectXTradeDaySync.trade_date == trade_day)
            .one()
        )
        assert len(client.calls) == 1
        assert client.calls[0] == (account_id, window_start, window_end, 1000, 0)
        assert sync_row.sync_status == "complete"
        assert _as_utc(sync_row.window_start) == window_start
        assert _as_utc(sync_row.window_end) == window_end
        assert sync_row.row_count == 1

        ensure_trade_cache_for_request(
            db,
            user_id=DEFAULT_USER_ID,
            account_id=account_id,
            start=window_start,
            end=window_end,
            refresh=False,
            client_factory=lambda: client,
        )

        assert len(client.calls) == 1
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeDaySync.__table__, ProjectXTradeEvent.__table__])
        engine.dispose()


def test_ensure_trade_cache_current_day_refresh_uses_requested_window(monkeypatch):
    monkeypatch.delenv("PROJECTX_DAY_SYNC_LIMIT", raising=False)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 3, 2, 15, 5, tzinfo=timezone.utc)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(projectx_trades_module, "datetime", FrozenDateTime)
    engine, db = _make_session()
    try:
        account_id = 13032503
        trade_day = date(2026, 3, 2)
        window_start, _window_end = trading_day_bounds_utc(trade_day)
        request_end = datetime(2026, 3, 2, 15, 0, tzinfo=timezone.utc)
        client = _RecordingClient([_event(account_id, window_start + timedelta(minutes=30))])

        ensure_trade_cache_for_request(
            db,
            user_id=DEFAULT_USER_ID,
            account_id=account_id,
            start=window_start,
            end=request_end,
            refresh=True,
            client_factory=lambda: client,
        )

        sync_row = (
            db.query(ProjectXTradeDaySync)
            .filter(ProjectXTradeDaySync.account_id == account_id)
            .filter(ProjectXTradeDaySync.trade_date == trade_day)
            .one()
        )
        assert len(client.calls) == 1
        assert client.calls[0] == (account_id, window_start, request_end, 1000, 0)
        assert sync_row.sync_status == "partial"
        assert _as_utc(sync_row.window_start) == window_start
        assert _as_utc(sync_row.window_end) == request_end
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeDaySync.__table__, ProjectXTradeEvent.__table__])
        engine.dispose()


def test_ensure_trade_cache_current_day_non_refresh_uses_local_cache(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 3, 2, 15, 5, tzinfo=timezone.utc)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(projectx_trades_module, "datetime", FrozenDateTime)
    engine, db = _make_session()
    try:
        account_id = 13032504
        trade_day = date(2026, 3, 2)
        window_start, _window_end = trading_day_bounds_utc(trade_day)
        request_end = datetime(2026, 3, 2, 15, 0, tzinfo=timezone.utc)

        ensure_trade_cache_for_request(
            db,
            user_id=DEFAULT_USER_ID,
            account_id=account_id,
            start=window_start,
            end=request_end,
            refresh=False,
            client_factory=lambda: _FailingClient(),
        )

        sync_row = (
            db.query(ProjectXTradeDaySync)
            .filter(ProjectXTradeDaySync.account_id == account_id)
            .filter(ProjectXTradeDaySync.trade_date == trade_day)
            .one_or_none()
        )
        assert sync_row is None
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeDaySync.__table__, ProjectXTradeEvent.__table__])
        engine.dispose()


def test_ensure_trade_cache_current_day_provider_failure_raises_on_refresh(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 3, 2, 15, 5, tzinfo=timezone.utc)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(projectx_trades_module, "datetime", FrozenDateTime)
    engine, db = _make_session()
    try:
        trade_day = date(2026, 3, 2)
        window_start, _window_end = trading_day_bounds_utc(trade_day)
        request_end = datetime(2026, 3, 2, 15, 0, tzinfo=timezone.utc)

        with pytest.raises(ProjectXClientError):
            ensure_trade_cache_for_request(
                db,
                user_id=DEFAULT_USER_ID,
                account_id=13032505,
                start=window_start,
                end=request_end,
                refresh=True,
                client_factory=lambda: _FailingClient(),
            )
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeDaySync.__table__, ProjectXTradeEvent.__table__])
        engine.dispose()


def test_ensure_trade_cache_refetches_complete_rows_without_window_bounds(monkeypatch):
    monkeypatch.delenv("PROJECTX_DAY_SYNC_LIMIT", raising=False)
    engine, db = _make_session()
    try:
        account_id = 13032502
        trade_day = date(2026, 3, 2)
        window_start, window_end = trading_day_bounds_utc(trade_day)
        db.add(
            ProjectXTradeDaySync(
                user_id=DEFAULT_USER_ID,
                account_id=account_id,
                trade_date=trade_day,
                sync_status="complete",
                last_synced_at=_dt(20, 11, 0),
            )
        )
        db.commit()

        client = _RecordingClient([])
        ensure_trade_cache_for_request(
            db,
            user_id=DEFAULT_USER_ID,
            account_id=account_id,
            start=window_start,
            end=window_end,
            refresh=False,
            client_factory=lambda: client,
        )

        sync_row = (
            db.query(ProjectXTradeDaySync)
            .filter(ProjectXTradeDaySync.account_id == account_id)
            .filter(ProjectXTradeDaySync.trade_date == trade_day)
            .one()
        )
        assert len(client.calls) == 1
        assert _as_utc(sync_row.window_start) == window_start
        assert _as_utc(sync_row.window_end) == window_end
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeDaySync.__table__, ProjectXTradeEvent.__table__])
        engine.dispose()


def test_trade_day_unique_race_retries_from_winner_state_without_downgrading_complete(
    tmp_path,
):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'trade-day-race.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[ProjectXTradeEvent.__table__, ProjectXTradeDaySync.__table__],
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    loser = SessionLocal()
    try:
        account_id = 13032550
        trade_day = date(2026, 3, 2)
        window_start, window_end = trading_day_bounds_utc(trade_day)
        event = _event(account_id, window_start + timedelta(minutes=30), "SRC-CONCURRENT")

        class ConcurrentWinnerClient(_RecordingClient):
            def fetch_trade_history(self, *args, **kwargs):
                with SessionLocal() as winner:
                    projectx_trades_module.store_trade_events(
                        winner,
                        [event],
                        user_id=DEFAULT_USER_ID,
                    )
                    projectx_trades_module._upsert_trade_day_sync(
                        winner,
                        user_id=DEFAULT_USER_ID,
                        account_id=account_id,
                        trade_day=trade_day,
                        window_start=window_start,
                        window_end=window_end,
                        sync_status="complete",
                        last_synced_at=_dt(20, 12, 0),
                        row_count=1,
                    )
                    winner.commit()
                return super().fetch_trade_history(*args, **kwargs)

        class Diagnostic:
            table_name = "projectx_trade_events"
            constraint_name = "uq_projectx_trade_events_account_source_trade"

        class UniqueRace(Exception):
            diag = Diagnostic()

        real_flush = loser.flush
        flush_calls = 0

        def flush_with_one_race(*args, **kwargs):
            nonlocal flush_calls
            flush_calls += 1
            if flush_calls == 1:
                raise IntegrityError("insert", {}, UniqueRace("concurrent duplicate"))
            return real_flush(*args, **kwargs)

        loser.flush = flush_with_one_race
        client = ConcurrentWinnerClient([event])

        projectx_trades_module._sync_trade_day_from_provider(
            loser,
            client_factory=lambda: client,
            user_id=DEFAULT_USER_ID,
            account_id=account_id,
            trade_day=trade_day,
            window_start=window_start,
            window_end=window_end,
            allow_complete=False,
        )

        with SessionLocal() as verifier:
            marker = verifier.query(ProjectXTradeDaySync).one()
            assert marker.sync_status == "complete"
            assert marker.row_count == 1
            assert verifier.query(ProjectXTradeEvent).count() == 1
        assert flush_calls == 2
    finally:
        loser.close()
        Base.metadata.drop_all(
            bind=engine,
            tables=[ProjectXTradeDaySync.__table__, ProjectXTradeEvent.__table__],
        )
        engine.dispose()


def test_partial_writer_reloads_complete_marker_before_update(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'trade-marker-lock.db'}")
    Base.metadata.create_all(bind=engine, tables=[ProjectXTradeDaySync.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    loser = SessionLocal()
    try:
        account_id = 13032551
        trade_day = date(2026, 3, 2)
        window_start, window_end = trading_day_bounds_utc(trade_day)
        loser.add(
            ProjectXTradeDaySync(
                user_id=DEFAULT_USER_ID,
                account_id=account_id,
                trade_date=trade_day,
                window_start=window_start,
                window_end=window_end,
                sync_status="partial",
                last_synced_at=_dt(20, 10, 0),
            )
        )
        loser.commit()
        original_get = projectx_trades_module._get_trade_day_sync
        winner_committed = False

        def get_after_winner(db, **kwargs):
            nonlocal winner_committed
            if db is loser and kwargs.get("for_update") and not winner_committed:
                winner_committed = True
                db.rollback()
                with SessionLocal() as winner:
                    marker = winner.query(ProjectXTradeDaySync).one()
                    marker.sync_status = "complete"
                    marker.last_synced_at = _dt(20, 11, 0)
                    winner.commit()
            return original_get(db, **kwargs)

        monkeypatch.setattr(
            projectx_trades_module,
            "_get_trade_day_sync",
            get_after_winner,
        )

        projectx_trades_module._upsert_trade_day_sync(
            loser,
            user_id=DEFAULT_USER_ID,
            account_id=account_id,
            trade_day=trade_day,
            window_start=window_start,
            window_end=window_end,
            sync_status="partial",
            last_synced_at=_dt(20, 12, 0),
            row_count=0,
        )
        loser.commit()

        with SessionLocal() as verifier:
            marker = verifier.query(ProjectXTradeDaySync).one()
            assert marker.sync_status == "complete"
            assert _as_utc(marker.last_synced_at) == _dt(20, 11, 0)
    finally:
        loser.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeDaySync.__table__])
        engine.dispose()


def test_provider_failure_does_not_downgrade_covering_complete_marker(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'trade-provider-failure.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[ProjectXTradeEvent.__table__, ProjectXTradeDaySync.__table__],
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()
    try:
        account_id = 13032552
        trade_day = date(2026, 3, 2)
        window_start, window_end = trading_day_bounds_utc(trade_day)
        db.add(
            ProjectXTradeDaySync(
                user_id=DEFAULT_USER_ID,
                account_id=account_id,
                trade_date=trade_day,
                window_start=window_start,
                window_end=window_end,
                sync_status="complete",
                last_synced_at=_dt(20, 11, 0),
                row_count=1,
            )
        )
        db.commit()

        with pytest.raises(ProjectXClientError):
            projectx_trades_module._sync_trade_day_from_provider(
                db,
                client_factory=lambda: _FailingClient(),
                user_id=DEFAULT_USER_ID,
                account_id=account_id,
                trade_day=trade_day,
                window_start=window_start,
                window_end=window_end,
                allow_complete=True,
            )

        db.expire_all()
        marker = db.query(ProjectXTradeDaySync).one()
        assert marker.sync_status == "complete"
        assert marker.row_count == 1
    finally:
        db.close()
        Base.metadata.drop_all(
            bind=engine,
            tables=[ProjectXTradeDaySync.__table__, ProjectXTradeEvent.__table__],
        )
        engine.dispose()
