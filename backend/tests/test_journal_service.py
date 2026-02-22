import os
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.journal_schemas import JournalMood
from app.models import JournalEntry
from app.services.journal import (
    create_journal_entry,
    list_journal_entries,
    normalize_tags,
    normalize_title,
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
    Base.metadata.create_all(bind=engine, tables=[JournalEntry.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=[JournalEntry.__table__])
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
