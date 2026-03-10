import os
from datetime import date

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.auth import DEFAULT_USER_ID
from app.db import Base
from app.journal_schemas import JournalEntryCreateIn, JournalEntryUpdateIn, JournalMergeIn, JournalMood
from app.main import (
    create_projectx_account_journal_entry,
    merge_projectx_account_journal_entries,
    list_projectx_account_journal_entries,
    update_projectx_account_journal_entry,
)
from app.models import Account, JournalEntry, JournalEntryImage


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[Account.__table__, JournalEntry.__table__, JournalEntryImage.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=[JournalEntryImage.__table__, JournalEntry.__table__, Account.__table__])
        engine.dispose()


def add_projectx_account(db_session, account_id: int, *, user_id: str = DEFAULT_USER_ID) -> None:
    db_session.add(
        Account(
            user_id=user_id,
            provider="projectx",
            external_id=str(account_id),
            name=f"Account {account_id}",
            account_state="ACTIVE",
        )
    )
    db_session.commit()


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
        response=Response(),
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
        response=Response(),
        db=db_session,
    )

    updated = update_projectx_account_journal_entry(
        account_id=13002,
        entry_id=created["id"],
        payload=JournalEntryUpdateIn(
            version=created["version"],
            title="Updated title",
            body="Updated body.",
            tags=["review", "nq", "review"],
        ),
        db=db_session,
    )

    assert updated["title"] == "Updated title"
    assert updated["tags"] == ["review", "nq"]
    assert updated["version"] == created["version"] + 1
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
        response=Response(),
        db=db_session,
    )

    update_projectx_account_journal_entry(
        account_id=13003,
        entry_id=created["id"],
        payload=JournalEntryUpdateIn(version=created["version"], is_archived=True),
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
        JournalEntryUpdateIn(version=1, tags="not-a-list")


def test_merge_route_merges_entries_for_owned_accounts(db_session):
    add_projectx_account(db_session, 13005)
    add_projectx_account(db_session, 13006)

    create_projectx_account_journal_entry(
        account_id=13005,
        payload=JournalEntryCreateIn(
            entry_date=date(2026, 2, 18),
            title="Old account day one",
            mood=JournalMood.FOCUSED,
            tags=["merge"],
            body="Copy this into the new account.",
        ),
        response=Response(),
        db=db_session,
    )
    create_projectx_account_journal_entry(
        account_id=13006,
        payload=JournalEntryCreateIn(
            entry_date=date(2026, 2, 20),
            title="Keep this current entry",
            mood=JournalMood.NEUTRAL,
            tags=["current"],
            body="Already on the new account.",
        ),
        response=Response(),
        db=db_session,
    )

    summary = merge_projectx_account_journal_entries(
        payload=JournalMergeIn(
            from_account_id=13005,
            to_account_id=13006,
            on_conflict="skip",
            include_images=False,
        ),
        db=db_session,
    )
    destination_payload = list_projectx_account_journal_entries(
        account_id=13006,
        q=None,
        limit=50,
        offset=0,
        db=db_session,
    )

    assert summary == {
        "from_account_id": 13005,
        "to_account_id": 13006,
        "transferred_count": 1,
        "skipped_count": 0,
        "overwritten_count": 0,
        "image_count": 0,
    }
    assert destination_payload["total"] == 2
    assert {item["title"] for item in destination_payload["items"]} == {
        "Old account day one",
        "Keep this current entry",
    }


def test_merge_route_rejects_same_account(db_session):
    add_projectx_account(db_session, 13007)

    with pytest.raises(HTTPException) as exc_info:
        merge_projectx_account_journal_entries(
            payload=JournalMergeIn(
                from_account_id=13007,
                to_account_id=13007,
                on_conflict="skip",
                include_images=True,
            ),
            db=db_session,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "from_account_id and to_account_id must be different"


def test_merge_route_rejects_missing_source_account(db_session):
    add_projectx_account(db_session, 13008)

    with pytest.raises(HTTPException) as exc_info:
        merge_projectx_account_journal_entries(
            payload=JournalMergeIn(
                from_account_id=99999,
                to_account_id=13008,
                on_conflict="skip",
                include_images=False,
            ),
            db=db_session,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Source account not found."


def test_merge_route_rejects_destination_account_owned_by_another_user(db_session):
    add_projectx_account(db_session, 13009)
    add_projectx_account(db_session, 13010, user_id="other-user")

    with pytest.raises(HTTPException) as exc_info:
        merge_projectx_account_journal_entries(
            payload=JournalMergeIn(
                from_account_id=13009,
                to_account_id=13010,
                on_conflict="overwrite",
                include_images=False,
            ),
            db=db_session,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Destination account not found."
