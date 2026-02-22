import json
import os
from datetime import date

import pytest
from fastapi import Response
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.db import Base
from app.journal_schemas import JournalEntryCreateIn, JournalEntryUpdateIn, JournalMood
from app.main import (
    create_projectx_account_journal_entry,
    delete_projectx_account_journal_entry,
    update_projectx_account_journal_entry,
)
from app.models import JournalEntry, JournalEntryImage
from app.services.journal import create_journal_entry_image


@pytest.fixture()
def db_session(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNAL_IMAGE_STORAGE_DIR", str(tmp_path / "journal-images"))

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[JournalEntry.__table__, JournalEntryImage.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    session.execute(text("PRAGMA foreign_keys=ON"))
    session.commit()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=[JournalEntryImage.__table__, JournalEntry.__table__])
        engine.dispose()


def test_create_same_account_and_day_returns_existing_entry(db_session):
    first = create_projectx_account_journal_entry(
        account_id=13011,
        payload=JournalEntryCreateIn(
            entry_date=date(2026, 2, 21),
            title="Initial title",
            mood=JournalMood.NEUTRAL,
            tags=[],
            body="Body",
        ),
        response=Response(),
        db=db_session,
    )

    second = create_projectx_account_journal_entry(
        account_id=13011,
        payload=JournalEntryCreateIn(
            entry_date=date(2026, 2, 21),
            title="Ignored title",
            mood=JournalMood.FOCUSED,
            tags=["nq"],
            body="Should return original",
        ),
        response=Response(),
        db=db_session,
    )

    assert first["already_existed"] is False
    assert second["already_existed"] is True
    assert second["id"] == first["id"]
    assert second["title"] == "Initial title"


def test_patch_with_stale_version_returns_409_conflict_payload(db_session):
    created = create_projectx_account_journal_entry(
        account_id=13012,
        payload=JournalEntryCreateIn(
            entry_date=date(2026, 2, 21),
            title="Versioned entry",
            mood=JournalMood.NEUTRAL,
            tags=[],
            body="Body",
        ),
        response=Response(),
        db=db_session,
    )

    updated = update_projectx_account_journal_entry(
        account_id=13012,
        entry_id=created["id"],
        payload=JournalEntryUpdateIn(version=created["version"], body="Updated once"),
        db=db_session,
    )

    conflict = update_projectx_account_journal_entry(
        account_id=13012,
        entry_id=created["id"],
        payload=JournalEntryUpdateIn(version=created["version"], body="Stale update"),
        db=db_session,
    )

    assert updated["version"] == created["version"] + 1
    assert isinstance(conflict, JSONResponse)
    assert conflict.status_code == 409

    payload = json.loads(conflict.body.decode("utf-8"))
    assert payload["detail"] == "version_conflict"
    assert payload["server"]["id"] == created["id"]
    assert payload["server"]["version"] == updated["version"]


def test_delete_entry_cascades_journal_images(db_session):
    created = create_projectx_account_journal_entry(
        account_id=13013,
        payload=JournalEntryCreateIn(
            entry_date=date(2026, 2, 22),
            title="With image",
            mood=JournalMood.FOCUSED,
            tags=[],
            body="Body",
        ),
        response=Response(),
        db=db_session,
    )

    create_journal_entry_image(
        db_session,
        account_id=13013,
        entry_id=created["id"],
        file_bytes=b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
        mime_type="image/png",
    )

    image_count_before = db_session.query(JournalEntryImage).count()
    assert image_count_before == 1

    delete_projectx_account_journal_entry(account_id=13013, entry_id=created["id"], db=db_session)

    assert db_session.query(JournalEntry).count() == 0
    assert db_session.query(JournalEntryImage).count() == 0


def test_image_upload_validation_rejects_invalid_mime_and_large_files(db_session):
    created = create_projectx_account_journal_entry(
        account_id=13014,
        payload=JournalEntryCreateIn(
            entry_date=date(2026, 2, 22),
            title="Validation",
            mood=JournalMood.NEUTRAL,
            tags=[],
            body="Body",
        ),
        response=Response(),
        db=db_session,
    )

    with pytest.raises(ValueError, match="unsupported image type"):
        create_journal_entry_image(
            db_session,
            account_id=13014,
            entry_id=created["id"],
            file_bytes=b"not-an-image",
            mime_type="text/plain",
        )

    with pytest.raises(ValueError, match="10MB"):
        create_journal_entry_image(
            db_session,
            account_id=13014,
            entry_id=created["id"],
            file_bytes=b"0" * (10 * 1024 * 1024 + 1),
            mime_type="image/png",
        )
