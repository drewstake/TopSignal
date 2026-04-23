import os
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.journal_schemas import JournalMood
from app.models import JournalEntry, JournalEntryImage, ProjectXTradeEvent
from app.services.journal import (
    _compute_trade_stats_snapshot,
    create_journal_entry,
    create_journal_entry_image,
    delete_journal_entry,
    list_journal_entries,
    list_journal_entry_images,
    merge_journal_entries,
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
    Base.metadata.create_all(
        bind=engine,
        tables=[JournalEntry.__table__, JournalEntryImage.__table__, ProjectXTradeEvent.__table__],
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(
            bind=engine,
            tables=[ProjectXTradeEvent.__table__, JournalEntryImage.__table__, JournalEntry.__table__],
        )
        engine.dispose()


@pytest.fixture()
def journal_storage_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("JOURNAL_IMAGE_STORAGE_BACKEND", "local")
    monkeypatch.setenv("JOURNAL_IMAGE_STORAGE_DIR", str(tmp_path))
    return tmp_path


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


def test_update_journal_entry_keeps_version_when_no_fields_change(db_session):
    row, _ = create_journal_entry(
        db_session,
        account_id=555,
        entry_date=date(2026, 2, 20),
        title="Scoped entry",
        mood=JournalMood.NEUTRAL,
        tags=["plan"],
        body="Body",
    )

    updated = update_journal_entry(
        db_session,
        account_id=555,
        entry_id=int(row.id),
        version=1,
        title="Scoped entry",
        mood=JournalMood.NEUTRAL,
        tags=["plan"],
        body="Body",
    )

    assert int(updated.version) == 1


def test_update_journal_entry_accepts_stale_duplicate_payload_when_server_already_matches(db_session):
    row, _ = create_journal_entry(
        db_session,
        account_id=555,
        entry_date=date(2026, 2, 20),
        title="Scoped entry",
        mood=JournalMood.NEUTRAL,
        tags=["plan"],
        body="Body",
    )

    first_update = update_journal_entry(
        db_session,
        account_id=555,
        entry_id=int(row.id),
        version=1,
        title="Updated title",
        tags=["review", "nq"],
        body="Updated body",
    )

    duplicate_update = update_journal_entry(
        db_session,
        account_id=555,
        entry_id=int(row.id),
        version=1,
        title="Updated title",
        tags=["review", "nq"],
        body="Updated body",
    )

    assert int(first_update.version) == 2
    assert int(duplicate_update.version) == 2
    assert duplicate_update.title == "Updated title"
    assert duplicate_update.body == "Updated body"


def test_merge_journal_entries_copies_missing_days_into_destination_without_touching_source(db_session):
    user_id = "merge-success-user"
    create_journal_entry(
        db_session,
        user_id=user_id,
        account_id=4101,
        entry_date=date(2026, 2, 1),
        title="Day one",
        mood=JournalMood.FOCUSED,
        tags=["discipline"],
        body="First source entry",
    )
    source_second, _ = create_journal_entry(
        db_session,
        user_id=user_id,
        account_id=4101,
        entry_date=date(2026, 2, 2),
        title="Day two",
        mood=JournalMood.CONFIDENT,
        tags=["review"],
        body="Second source entry",
    )
    source_second.stats_source = "trade_snapshot"
    source_second.stats_json = {"trade_count": 3, "net": 175.5}
    source_second.stats_pulled_at = datetime(2026, 2, 2, 18, 15, tzinfo=timezone.utc)
    db_session.add(source_second)

    destination_existing, _ = create_journal_entry(
        db_session,
        user_id=user_id,
        account_id=4202,
        entry_date=date(2026, 2, 4),
        title="Destination keeps this",
        mood=JournalMood.NEUTRAL,
        tags=["existing"],
        body="Already on the new account",
    )
    db_session.commit()

    summary = merge_journal_entries(
        db_session,
        user_id=user_id,
        from_account_id=4101,
        to_account_id=4202,
        on_conflict="skip",
        include_images=False,
    )

    source_rows, source_total = list_journal_entries(db_session, user_id=user_id, account_id=4101, limit=10, offset=0)
    destination_rows, destination_total = list_journal_entries(
        db_session,
        user_id=user_id,
        account_id=4202,
        limit=10,
        offset=0,
    )

    assert summary == {
        "from_account_id": 4101,
        "to_account_id": 4202,
        "transferred_count": 2,
        "skipped_count": 0,
        "overwritten_count": 0,
        "image_count": 0,
    }
    assert source_total == 2
    assert destination_total == 3
    assert [row.entry_date for row in source_rows] == [date(2026, 2, 2), date(2026, 2, 1)]
    assert destination_existing.id in {row.id for row in destination_rows}
    merged_by_date = {row.entry_date: row for row in destination_rows}
    assert merged_by_date[date(2026, 2, 1)].title == "Day one"
    assert merged_by_date[date(2026, 2, 2)].stats_source == "trade_snapshot"
    assert merged_by_date[date(2026, 2, 2)].stats_json == {"trade_count": 3, "net": 175.5}
    assert merged_by_date[date(2026, 2, 2)].stats_pulled_at == datetime(2026, 2, 2, 18, 15)


def test_merge_journal_entries_skip_conflict_preserves_destination_entry(db_session, journal_storage_dir: Path):
    user_id = "merge-skip-user"
    source_entry, _ = create_journal_entry(
        db_session,
        user_id=user_id,
        account_id=4301,
        entry_date=date(2026, 2, 6),
        title="Old account day",
        mood=JournalMood.FRUSTRATED,
        tags=["old"],
        body="Should be skipped",
    )
    destination_entry, _ = create_journal_entry(
        db_session,
        user_id=user_id,
        account_id=4302,
        entry_date=date(2026, 2, 6),
        title="New account day",
        mood=JournalMood.FOCUSED,
        tags=["keep"],
        body="Keep the destination entry",
    )
    create_journal_entry_image(
        db_session,
        user_id=user_id,
        account_id=4301,
        entry_id=int(source_entry.id),
        file_bytes=b"source-image",
        mime_type="image/png",
    )
    destination_image = create_journal_entry_image(
        db_session,
        user_id=user_id,
        account_id=4302,
        entry_id=int(destination_entry.id),
        file_bytes=b"destination-image",
        mime_type="image/png",
    )

    summary = merge_journal_entries(
        db_session,
        user_id=user_id,
        from_account_id=4301,
        to_account_id=4302,
        on_conflict="skip",
        include_images=True,
    )

    db_session.refresh(destination_entry)
    destination_images = list_journal_entry_images(
        db_session,
        user_id=user_id,
        account_id=4302,
        entry_id=int(destination_entry.id),
    )

    assert summary == {
        "from_account_id": 4301,
        "to_account_id": 4302,
        "transferred_count": 0,
        "skipped_count": 1,
        "overwritten_count": 0,
        "image_count": 0,
    }
    assert destination_entry.title == "New account day"
    assert destination_entry.body == "Keep the destination entry"
    assert [image.id for image in destination_images] == [int(destination_image.id)]
    assert (journal_storage_dir / destination_image.filename).exists()


def test_merge_journal_entries_overwrite_conflict_replaces_entry_and_copies_images(
    db_session,
    journal_storage_dir: Path,
):
    user_id = "merge-overwrite-user"
    source_entry, _ = create_journal_entry(
        db_session,
        user_id=user_id,
        account_id=4401,
        entry_date=date(2026, 2, 8),
        title="Recovered review",
        mood=JournalMood.CONFIDENT,
        tags=["merge", "review"],
        body="Use the old account review instead.",
    )
    source_entry.version = 5
    source_entry.stats_source = "trade_snapshot"
    source_entry.stats_json = {"trade_count": 2, "net": 412.0}
    source_entry.stats_pulled_at = datetime(2026, 2, 8, 19, 5, tzinfo=timezone.utc)
    source_entry.is_archived = True
    db_session.add(source_entry)
    db_session.commit()

    source_image_one = create_journal_entry_image(
        db_session,
        user_id=user_id,
        account_id=4401,
        entry_id=int(source_entry.id),
        file_bytes=b"source-image-one",
        mime_type="image/png",
    )
    source_image_two = create_journal_entry_image(
        db_session,
        user_id=user_id,
        account_id=4401,
        entry_id=int(source_entry.id),
        file_bytes=b"source-image-two",
        mime_type="image/jpeg",
    )

    destination_entry, _ = create_journal_entry(
        db_session,
        user_id=user_id,
        account_id=4402,
        entry_date=date(2026, 2, 8),
        title="Keep no more",
        mood=JournalMood.NEUTRAL,
        tags=["stale"],
        body="This should be replaced",
    )
    destination_entry.version = 2
    db_session.add(destination_entry)
    db_session.commit()

    destination_old_image = create_journal_entry_image(
        db_session,
        user_id=user_id,
        account_id=4402,
        entry_id=int(destination_entry.id),
        file_bytes=b"destination-old-image",
        mime_type="image/png",
    )
    old_image_path = journal_storage_dir / destination_old_image.filename

    summary = merge_journal_entries(
        db_session,
        user_id=user_id,
        from_account_id=4401,
        to_account_id=4402,
        on_conflict="overwrite",
        include_images=True,
    )

    db_session.refresh(destination_entry)
    destination_images = list_journal_entry_images(
        db_session,
        user_id=user_id,
        account_id=4402,
        entry_id=int(destination_entry.id),
    )

    assert summary == {
        "from_account_id": 4401,
        "to_account_id": 4402,
        "transferred_count": 1,
        "skipped_count": 0,
        "overwritten_count": 1,
        "image_count": 2,
    }
    assert destination_entry.title == "Recovered review"
    assert destination_entry.mood == JournalMood.CONFIDENT.value
    assert destination_entry.tags == ["merge", "review"]
    assert destination_entry.body == "Use the old account review instead."
    assert destination_entry.stats_source == "trade_snapshot"
    assert destination_entry.stats_json == {"trade_count": 2, "net": 412.0}
    assert destination_entry.stats_pulled_at == datetime(2026, 2, 8, 19, 5)
    assert destination_entry.is_archived is True
    assert int(destination_entry.version) == 6
    assert len(destination_images) == 2
    assert sorted(image.mime_type for image in destination_images) == ["image/jpeg", "image/png"]
    assert all(image.filename != destination_old_image.filename for image in destination_images)
    assert all((journal_storage_dir / image.filename).exists() for image in destination_images)
    assert not old_image_path.exists()
    assert (journal_storage_dir / source_image_one.filename).exists()
    assert (journal_storage_dir / source_image_two.filename).exists()


def test_compute_trade_stats_snapshot_counts_broker_fees_once():
    rows = [
        SimpleNamespace(pnl=100.0, fees=1.0, size=3.0),
        SimpleNamespace(pnl=50.0, fees=30.0, size=5.0),
    ]

    snapshot = _compute_trade_stats_snapshot(rows)

    assert snapshot["snapshot_version"] == 2
    assert snapshot["trade_count"] == 2
    assert snapshot["gross"] == 150.0
    assert snapshot["total_fees"] == 31.0
    assert snapshot["net"] == 119.0
    assert snapshot["net_realized_pnl"] == 119.0
    assert snapshot["win_rate"] == 100.0
    assert snapshot["avg_win"] == 59.5
    assert snapshot["avg_loss"] == 0.0
    assert snapshot["largest_win"] == 99.0
    assert snapshot["largest_loss"] == 0.0
    assert snapshot["largest_position_size"] == 5.0


def test_delete_journal_entry_does_not_fail_when_image_cleanup_fails(
    db_session,
    journal_storage_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    user_id = "delete-journal-cleanup"
    entry, _ = create_journal_entry(
        db_session,
        user_id=user_id,
        account_id=5501,
        entry_date=date(2026, 2, 18),
        title="Delete me",
        mood=JournalMood.NEUTRAL,
        tags=[],
        body="Body",
    )
    image = create_journal_entry_image(
        db_session,
        user_id=user_id,
        account_id=5501,
        entry_id=int(entry.id),
        file_bytes=b"delete-image",
        mime_type="image/png",
    )

    monkeypatch.setattr(
        "app.services.journal.delete_journal_image",
        lambda *, object_key: (_ for _ in ()).throw(RuntimeError(f"storage unavailable: {object_key}")),
    )

    delete_journal_entry(
        db_session,
        user_id=user_id,
        account_id=5501,
        entry_id=int(entry.id),
    )

    assert (
        db_session.query(JournalEntry)
        .filter(JournalEntry.user_id == user_id)
        .filter(JournalEntry.account_id == 5501)
        .count()
    ) == 0
    assert (
        db_session.query(JournalEntryImage)
        .filter(JournalEntryImage.user_id == user_id)
        .filter(JournalEntryImage.id == int(image.id))
        .count()
    ) == 0
    assert (journal_storage_dir / image.filename).exists()


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
    assert entry.stats_json["net_realized_pnl"] == 407.96

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
    assert entry.stats_json["net_realized_pnl"] == 1492.76
    assert entry.stats_json["win_rate"] == 100.0
    assert entry.stats_json["avg_win"] == 298.55
    assert entry.stats_json["avg_loss"] == 0.0


def test_pull_journal_entry_trade_stats_uses_combined_position_size_from_execution_history(db_session):
    user_id = "test-user-position-size"
    entry, _ = create_journal_entry(
        db_session,
        user_id=user_id,
        account_id=1000,
        entry_date=date(2026, 3, 5),
        title="Combined size test",
        mood=JournalMood.NEUTRAL,
        tags=[],
        body="",
    )

    rows = [
        ProjectXTradeEvent(
            user_id=user_id,
            account_id=1000,
            contract_id="MNQH6",
            symbol="MNQ",
            side="SELL",
            size=15,
            price=25068,
            trade_timestamp=datetime(2026, 3, 5, 14, 44, 0, tzinfo=timezone.utc),
            fees=0,
            pnl=None,
            order_id="open-1",
            source_trade_id="open-s-1",
            raw_payload={},
        ),
        ProjectXTradeEvent(
            user_id=user_id,
            account_id=1000,
            contract_id="MNQH6",
            symbol="MNQ",
            side="SELL",
            size=15,
            price=25067.5,
            trade_timestamp=datetime(2026, 3, 5, 14, 44, 1, tzinfo=timezone.utc),
            fees=0,
            pnl=None,
            order_id="open-2",
            source_trade_id="open-s-2",
            raw_payload={},
        ),
        ProjectXTradeEvent(
            user_id=user_id,
            account_id=1000,
            contract_id="MNQH6",
            symbol="MNQ",
            side="SELL",
            size=15,
            price=25067.75,
            trade_timestamp=datetime(2026, 3, 5, 14, 44, 2, tzinfo=timezone.utc),
            fees=0,
            pnl=None,
            order_id="open-3",
            source_trade_id="open-s-3",
            raw_payload={},
        ),
        ProjectXTradeEvent(
            user_id=user_id,
            account_id=1000,
            contract_id="MNQH6",
            symbol="MNQ",
            side="BUY",
            size=15,
            price=25038.25,
            trade_timestamp=datetime(2026, 3, 5, 14, 45, 0, tzinfo=timezone.utc),
            fees=1,
            pnl=892.5,
            order_id="close-1",
            source_trade_id="close-s-1",
            raw_payload={},
        ),
        ProjectXTradeEvent(
            user_id=user_id,
            account_id=1000,
            contract_id="MNQH6",
            symbol="MNQ",
            side="BUY",
            size=15,
            price=25038.25,
            trade_timestamp=datetime(2026, 3, 5, 14, 45, 1, tzinfo=timezone.utc),
            fees=1,
            pnl=877.5,
            order_id="close-2",
            source_trade_id="close-s-2",
            raw_payload={},
        ),
        ProjectXTradeEvent(
            user_id=user_id,
            account_id=1000,
            contract_id="MNQH6",
            symbol="MNQ",
            side="BUY",
            size=15,
            price=25038.25,
            trade_timestamp=datetime(2026, 3, 5, 14, 45, 2, tzinfo=timezone.utc),
            fees=1,
            pnl=885.0,
            order_id="close-3",
            source_trade_id="close-s-3",
            raw_payload={},
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()

    pull_journal_entry_trade_stats(
        db_session,
        user_id=user_id,
        account_id=1000,
        entry_id=int(entry.id),
        entry_date=date(2026, 3, 5),
    )
    db_session.refresh(entry)

    assert entry.stats_json is not None
    assert entry.stats_json["largest_position_size"] == 45.0


def test_pull_journal_entry_trade_stats_entry_date_uses_trading_day_rollover_boundary(db_session):
    user_id = "test-user-trading-day-boundary"
    entry, _ = create_journal_entry(
        db_session,
        user_id=user_id,
        account_id=1001,
        entry_date=date(2026, 2, 27),
        title="Trading day boundary",
        mood=JournalMood.NEUTRAL,
        tags=[],
        body="",
    )

    rows = [
        # 2026-02-27 21:40 UTC -> 2026-02-27 16:40 ET, still in the 2026-02-27 trading day.
        ProjectXTradeEvent(
            user_id=user_id,
            account_id=1001,
            contract_id="MNQH6",
            symbol="MNQ",
            side="BUY",
            size=1,
            price=20000,
            trade_timestamp=datetime(2026, 2, 27, 21, 40, tzinfo=timezone.utc),
            fees=1.0,
            pnl=100.0,
            order_id="ny-1",
            source_trade_id="ny-s-1",
            raw_payload={},
        ),
        # 2026-02-27 22:20 UTC -> 2026-02-27 17:20 ET, still in the 2026-02-27 trading day.
        ProjectXTradeEvent(
            user_id=user_id,
            account_id=1001,
            contract_id="MNQH6",
            symbol="MNQ",
            side="SELL",
            size=1,
            price=20010,
            trade_timestamp=datetime(2026, 2, 27, 22, 20, tzinfo=timezone.utc),
            fees=1.0,
            pnl=200.0,
            order_id="ny-2",
            source_trade_id="ny-s-2",
            raw_payload={},
        ),
        # 2026-02-27 23:10 UTC -> 2026-02-27 18:10 ET, rolled into the 2026-02-28 trading day.
        ProjectXTradeEvent(
            user_id=user_id,
            account_id=1001,
            contract_id="MNQH6",
            symbol="MNQ",
            side="BUY",
            size=1,
            price=20020,
            trade_timestamp=datetime(2026, 2, 27, 23, 10, tzinfo=timezone.utc),
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
    assert entry.stats_json["net_realized_pnl"] == 298.0


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
    assert calls[0][0] == datetime(2026, 2, 26, 23, 0, tzinfo=timezone.utc)
    assert calls[0][1] == datetime(2026, 2, 27, 22, 59, 59, 999999, tzinfo=timezone.utc)
