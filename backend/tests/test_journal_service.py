import os
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.journal_schemas import JournalMood
from app.models import JournalEntry, ProjectXTradeEvent
from app.services.journal import (
    _compute_trade_stats_snapshot,
    create_journal_entry,
    list_journal_entries,
    normalize_tags,
    normalize_title,
    pull_journal_entry_trade_stats,
    update_journal_entry,
    validate_date_range,
)


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[JournalEntry.__table__, ProjectXTradeEvent.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeEvent.__table__, JournalEntry.__table__])
        engine.dispose()


def test_normalize_tags_trims_lowercases_and_deduplicates():
    tags = normalize_tags([" NQ ", "discipline", "nq", "", "Discipline", "plan"])

    assert tags == ["nq", "discipline", "plan"]


def test_normalize_title_rejects_blank_values():
    with pytest.raises(ValueError, match="title must not be empty"):
        normalize_title("   ")


def test_validate_date_range_rejects_inverted_ranges():
    with pytest.raises(ValueError, match="start_date must be before or equal to end_date"):
        validate_date_range(start_date=date(2026, 2, 12), end_date=date(2026, 2, 11))


def test_list_journal_entries_filters_by_date_mood_search_and_archive(db_session):
    create_journal_entry(
        db_session,
        account_id=101,
        entry_date=date(2026, 2, 10),
        title="NQ execution was clean",
        mood=JournalMood.FOCUSED,
        tags=["nq", "discipline"],
        body="Waited for confirmation and followed the plan.",
    )
    archived, _ = create_journal_entry(
        db_session,
        account_id=101,
        entry_date=date(2026, 2, 9),
        title="Breakdown day",
        mood=JournalMood.FRUSTRATED,
        tags=["risk"],
        body="Overtraded after initial loss.",
    )
    update_journal_entry(
        db_session,
        account_id=101,
        entry_id=int(archived.id),
        version=1,
        is_archived=True,
    )
    create_journal_entry(
        db_session,
        account_id=102,
        entry_date=date(2026, 2, 10),
        title="Different account",
        mood=JournalMood.FOCUSED,
        tags=["nq"],
        body="Should not appear for account 101.",
    )

    rows, total = list_journal_entries(
        db_session,
        account_id=101,
        start_date=date(2026, 2, 10),
        end_date=date(2026, 2, 10),
        mood=JournalMood.FOCUSED,
        text_query="discipline",
        include_archived=False,
        limit=50,
        offset=0,
    )

    assert total == 1
    assert len(rows) == 1
    assert rows[0].title == "NQ execution was clean"

    archived_rows, archived_total = list_journal_entries(
        db_session,
        account_id=101,
        include_archived=True,
        limit=50,
        offset=0,
    )
    assert archived_total == 2
    assert len(archived_rows) == 2


def test_update_journal_entry_enforces_account_scoping(db_session):
    row, _ = create_journal_entry(
        db_session,
        account_id=555,
        entry_date=date(2026, 2, 20),
        title="Scoped entry",
        mood=JournalMood.NEUTRAL,
        tags=[],
        body="Body",
    )

    with pytest.raises(LookupError, match="journal entry not found"):
        update_journal_entry(
            db_session,
            account_id=777,
            entry_id=int(row.id),
            version=1,
            title="Should fail",
        )


def test_compute_trade_stats_snapshot_uses_net_values_for_outcome_stats():
    rows = [
        SimpleNamespace(pnl=100.0, fees=1.0),
        SimpleNamespace(pnl=50.0, fees=30.0),
    ]

    snapshot = _compute_trade_stats_snapshot(rows)

    assert snapshot["trade_count"] == 2
    assert snapshot["gross"] == 150.0
    assert snapshot["total_fees"] == 62.0
    assert snapshot["net"] == 88.0
    assert snapshot["net_realized_pnl"] == 88.0
    assert snapshot["win_rate"] == 50.0
    assert snapshot["avg_win"] == 98.0
    assert snapshot["avg_loss"] == -10.0
    assert snapshot["largest_win"] == 98.0
    assert snapshot["largest_loss"] == -10.0


def test_pull_journal_entry_trade_stats_uses_date_range_when_provided(db_session):
    user_id = "test-user"
    entry, _ = create_journal_entry(
        db_session,
        user_id=user_id,
        account_id=999,
        entry_date=date(2026, 2, 27),
        title="Range test",
        mood=JournalMood.NEUTRAL,
        tags=[],
        body="",
    )

    rows = [
        ProjectXTradeEvent(
            user_id=user_id,
            account_id=999,
            contract_id="MNQH6",
            symbol="MNQ",
            side="BUY",
            size=1,
            price=20000,
            trade_timestamp=datetime(2026, 2, 27, 14, 0, tzinfo=timezone.utc),
            fees=1.02,
            pnl=225.0,
            order_id="o-1",
            source_trade_id="s-1",
            raw_payload={},
        ),
        ProjectXTradeEvent(
            user_id=user_id,
            account_id=999,
            contract_id="MNQH6",
            symbol="MNQ",
            side="SELL",
            size=1,
            price=20050,
            trade_timestamp=datetime(2026, 2, 27, 14, 5, tzinfo=timezone.utc),
            fees=1.02,
            pnl=185.0,
            order_id="o-2",
            source_trade_id="s-2",
            raw_payload={},
        ),
        ProjectXTradeEvent(
            user_id=user_id,
            account_id=999,
            contract_id="MNQH6",
            symbol="MNQ",
            side="BUY",
            size=1,
            price=20100,
            trade_timestamp=datetime(2026, 2, 26, 15, 0, tzinfo=timezone.utc),
            fees=1.0,
            pnl=350.0,
            order_id="o-3",
            source_trade_id="s-3",
            raw_payload={},
        ),
        ProjectXTradeEvent(
            user_id=user_id,
            account_id=999,
            contract_id="MNQH6",
            symbol="MNQ",
            side="BUY",
            size=1,
            price=20150,
            trade_timestamp=datetime(2026, 2, 28, 16, 0, tzinfo=timezone.utc),
            fees=1.0,
            pnl=360.0,
            order_id="o-4",
            source_trade_id="s-4",
            raw_payload={},
        ),
        ProjectXTradeEvent(
            user_id=user_id,
            account_id=999,
            contract_id="MNQH6",
            symbol="MNQ",
            side="SELL",
            size=1,
            price=20200,
            trade_timestamp=datetime(2026, 2, 28, 17, 0, tzinfo=timezone.utc),
            fees=1.0,
            pnl=377.8,
            order_id="o-5",
            source_trade_id="s-5",
            raw_payload={},
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()

    pull_journal_entry_trade_stats(
        db_session,
        user_id=user_id,
        account_id=999,
        entry_id=int(entry.id),
        entry_date=date(2026, 2, 27),
    )
    db_session.refresh(entry)
    assert entry.stats_json is not None
    assert entry.stats_json["trade_count"] == 2
    assert entry.stats_json["net_realized_pnl"] == 405.92

    pull_journal_entry_trade_stats(
        db_session,
        user_id=user_id,
        account_id=999,
        entry_id=int(entry.id),
        start_date=date(2026, 2, 26),
        end_date=date(2026, 2, 28),
    )
    db_session.refresh(entry)
    assert entry.stats_json is not None
    assert entry.stats_json["trade_count"] == 5
    assert entry.stats_json["net_realized_pnl"] == 1487.72
    assert entry.stats_json["win_rate"] == 100.0
    assert entry.stats_json["avg_win"] == 297.54
    assert entry.stats_json["avg_loss"] == 0.0


def test_pull_journal_entry_trade_stats_entry_date_uses_new_york_day_boundary(db_session):
    user_id = "test-user-ny-boundary"
    entry, _ = create_journal_entry(
        db_session,
        user_id=user_id,
        account_id=1001,
        entry_date=date(2026, 2, 27),
        title="NY day boundary",
        mood=JournalMood.NEUTRAL,
        tags=[],
        body="",
    )

    rows = [
        # 2026-02-27 23:40 UTC -> 2026-02-27 18:40 ET
        ProjectXTradeEvent(
            user_id=user_id,
            account_id=1001,
            contract_id="MNQH6",
            symbol="MNQ",
            side="BUY",
            size=1,
            price=20000,
            trade_timestamp=datetime(2026, 2, 27, 23, 40, tzinfo=timezone.utc),
            fees=1.0,
            pnl=100.0,
            order_id="ny-1",
            source_trade_id="ny-s-1",
            raw_payload={},
        ),
        # 2026-02-28 00:20 UTC -> 2026-02-27 19:20 ET (same ET day)
        ProjectXTradeEvent(
            user_id=user_id,
            account_id=1001,
            contract_id="MNQH6",
            symbol="MNQ",
            side="SELL",
            size=1,
            price=20010,
            trade_timestamp=datetime(2026, 2, 28, 0, 20, tzinfo=timezone.utc),
            fees=1.0,
            pnl=200.0,
            order_id="ny-2",
            source_trade_id="ny-s-2",
            raw_payload={},
        ),
        # 2026-02-28 05:10 UTC -> 2026-02-28 00:10 ET (next ET day, excluded)
        ProjectXTradeEvent(
            user_id=user_id,
            account_id=1001,
            contract_id="MNQH6",
            symbol="MNQ",
            side="BUY",
            size=1,
            price=20020,
            trade_timestamp=datetime(2026, 2, 28, 5, 10, tzinfo=timezone.utc),
            fees=1.0,
            pnl=300.0,
            order_id="ny-3",
            source_trade_id="ny-s-3",
            raw_payload={},
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()

    pull_journal_entry_trade_stats(
        db_session,
        user_id=user_id,
        account_id=1001,
        entry_id=int(entry.id),
        entry_date=date(2026, 2, 27),
    )
    db_session.refresh(entry)
    assert entry.stats_json is not None
    assert entry.stats_json["trade_count"] == 2
    assert entry.stats_json["net_realized_pnl"] == 296.0


def test_pull_journal_entry_trade_stats_invokes_sync_callback_with_effective_bounds(db_session):
    user_id = "test-user-sync-callback"
    entry, _ = create_journal_entry(
        db_session,
        user_id=user_id,
        account_id=1002,
        entry_date=date(2026, 2, 27),
        title="Sync callback",
        mood=JournalMood.NEUTRAL,
        tags=[],
        body="",
    )

    calls: list[tuple[datetime | None, datetime | None]] = []

    pull_journal_entry_trade_stats(
        db_session,
        user_id=user_id,
        account_id=1002,
        entry_id=int(entry.id),
        before_query_sync=lambda start, end: calls.append((start, end)),
    )

    assert len(calls) == 1
    assert calls[0][0] == datetime(2026, 2, 27, 5, 0, tzinfo=timezone.utc)
    assert calls[0][1] == datetime(2026, 2, 28, 4, 59, 59, 999999, tzinfo=timezone.utc)
