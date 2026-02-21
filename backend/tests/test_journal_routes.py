import os
from datetime import date

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.journal_schemas import JournalEntryCreateIn, JournalEntryUpdateIn, JournalMood
from app.main import (
    create_projectx_account_journal_entry,
    list_projectx_account_journal_entries,
    update_projectx_account_journal_entry,
)
from app.models import JournalEntry


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


def test_create_then_list_returns_new_journal_entry(db_session):
    created = create_projectx_account_journal_entry(
        account_id=13001,
        payload=JournalEntryCreateIn(
            entry_date=date(2026, 2, 21),
            title="Opening plan",
            mood=JournalMood.FOCUSED,
            tags=["nq", "discipline"],
            body="Wait for the first pullback.",
        ),
        db=db_session,
    )

    payload = list_projectx_account_journal_entries(
        account_id=13001,
        q=None,
        limit=50,
        offset=0,
        db=db_session,
    )

    assert payload["total"] == 1
    assert len(payload["items"]) == 1
    assert payload["items"][0]["id"] == created["id"]
    assert payload["items"][0]["title"] == "Opening plan"


def test_patch_updates_fields_and_updated_at(db_session):
    created = create_projectx_account_journal_entry(
        account_id=13002,
        payload=JournalEntryCreateIn(
            entry_date=date(2026, 2, 21),
            title="Initial title",
            mood=JournalMood.NEUTRAL,
            tags=["plan"],
            body="Initial body.",
        ),
        db=db_session,
    )

    updated = update_projectx_account_journal_entry(
        account_id=13002,
        entry_id=created["id"],
        payload=JournalEntryUpdateIn(
            title="Updated title",
            body="Updated body.",
            tags=["review", "nq", "review"],
        ),
        db=db_session,
    )

    assert updated["title"] == "Updated title"
    assert updated["body"] == "Updated body."
    assert updated["tags"] == ["review", "nq"]
    assert updated["updated_at"] >= created["updated_at"]


def test_soft_archive_hidden_by_default_and_visible_when_requested(db_session):
    created = create_projectx_account_journal_entry(
        account_id=13003,
        payload=JournalEntryCreateIn(
            entry_date=date(2026, 2, 21),
            title="To archive",
            mood=JournalMood.FRUSTRATED,
            tags=["risk"],
            body="One bad day.",
        ),
        db=db_session,
    )

    update_projectx_account_journal_entry(
        account_id=13003,
        entry_id=created["id"],
        payload=JournalEntryUpdateIn(is_archived=True),
        db=db_session,
    )

    default_payload = list_projectx_account_journal_entries(
        account_id=13003,
        q=None,
        limit=50,
        offset=0,
        db=db_session,
    )
    with_archived_payload = list_projectx_account_journal_entries(
        account_id=13003,
        q=None,
        include_archived=True,
        limit=50,
        offset=0,
        db=db_session,
    )

    assert default_payload["total"] == 0
    assert with_archived_payload["total"] == 1
    assert with_archived_payload["items"][0]["is_archived"] is True


def test_invalid_query_range_raises_bad_request(db_session):
    with pytest.raises(HTTPException) as exc_info:
        list_projectx_account_journal_entries(
            account_id=13004,
            start_date=date(2026, 2, 22),
            end_date=date(2026, 2, 21),
            q=None,
            limit=50,
            offset=0,
            db=db_session,
        )

    assert exc_info.value.status_code == 400
    assert "start_date must be before or equal to end_date" in str(exc_info.value.detail)


def test_invalid_payload_shape_raises_validation_error_before_route_call():
    with pytest.raises(ValidationError):
        JournalEntryUpdateIn(tags="not-a-list")
