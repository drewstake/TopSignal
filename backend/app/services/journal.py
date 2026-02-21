from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Text, cast, func, or_
from sqlalchemy.orm import Session

from ..journal_schemas import JournalMood
from ..models import JournalEntry

_MAX_TITLE_LENGTH = 160
_MAX_BODY_LENGTH = 20_000
_MAX_TAG_COUNT = 20
_MAX_TAG_LENGTH = 32


def validate_date_range(*, start_date: date | None, end_date: date | None) -> None:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start_date must be before or equal to end_date")


def list_journal_entries(
    db: Session,
    *,
    account_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
    mood: JournalMood | str | None = None,
    text_query: str | None = None,
    include_archived: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[JournalEntry], int]:
    validate_date_range(start_date=start_date, end_date=end_date)

    query = db.query(JournalEntry).filter(JournalEntry.account_id == account_id)
    if not include_archived:
        query = query.filter(JournalEntry.is_archived.is_(False))
    if start_date is not None:
        query = query.filter(JournalEntry.entry_date >= start_date)
    if end_date is not None:
        query = query.filter(JournalEntry.entry_date <= end_date)
    if mood is not None:
        query = query.filter(JournalEntry.mood == _normalize_mood(mood))

    normalized_search = _normalize_search_query(text_query)
    if normalized_search:
        tags_text = func.lower(cast(JournalEntry.tags, Text))
        query = query.filter(
            or_(
                func.lower(JournalEntry.title).contains(normalized_search),
                func.lower(JournalEntry.body).contains(normalized_search),
                tags_text.contains(normalized_search),
            )
        )

    total = query.count()
    rows = (
        query.order_by(
            JournalEntry.entry_date.desc(),
            JournalEntry.updated_at.desc(),
            JournalEntry.id.desc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    return rows, total


def create_journal_entry(
    db: Session,
    *,
    account_id: int,
    entry_date: date,
    title: str,
    mood: JournalMood | str,
    tags: list[str],
    body: str,
) -> JournalEntry:
    now = _utcnow()
    row = JournalEntry(
        account_id=account_id,
        entry_date=entry_date,
        title=normalize_title(title),
        mood=_normalize_mood(mood),
        tags=normalize_tags(tags),
        body=normalize_body(body),
        is_archived=False,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_journal_entry(
    db: Session,
    *,
    account_id: int,
    entry_id: int,
    entry_date: date | None = None,
    title: str | None = None,
    mood: JournalMood | str | None = None,
    tags: list[str] | None = None,
    body: str | None = None,
    is_archived: bool | None = None,
) -> JournalEntry:
    row = _get_entry_for_account(db, account_id=account_id, entry_id=entry_id)
    if row is None:
        raise LookupError("journal entry not found")

    if entry_date is not None:
        row.entry_date = entry_date
    if title is not None:
        row.title = normalize_title(title)
    if mood is not None:
        row.mood = _normalize_mood(mood)
    if tags is not None:
        row.tags = normalize_tags(tags)
    if body is not None:
        row.body = normalize_body(body)
    if is_archived is not None:
        row.is_archived = bool(is_archived)

    row.updated_at = _utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def archive_journal_entry(db: Session, *, account_id: int, entry_id: int) -> JournalEntry:
    return update_journal_entry(
        db,
        account_id=account_id,
        entry_id=entry_id,
        is_archived=True,
    )


def unarchive_journal_entry(db: Session, *, account_id: int, entry_id: int) -> JournalEntry:
    return update_journal_entry(
        db,
        account_id=account_id,
        entry_id=entry_id,
        is_archived=False,
    )


def serialize_journal_entry(row: JournalEntry) -> dict[str, Any]:
    raw_tags = row.tags if isinstance(row.tags, list) else []
    tags = [str(tag) for tag in raw_tags]
    return {
        "id": int(row.id),
        "account_id": int(row.account_id),
        "entry_date": row.entry_date,
        "title": row.title,
        "mood": row.mood,
        "tags": tags,
        "body": row.body,
        "is_archived": bool(row.is_archived),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def normalize_title(value: str) -> str:
    title = str(value).strip()
    if not title:
        raise ValueError("title must not be empty")
    if len(title) > _MAX_TITLE_LENGTH:
        raise ValueError(f"title must be {_MAX_TITLE_LENGTH} characters or fewer")
    return title


def normalize_body(value: str) -> str:
    body = str(value)
    if len(body) > _MAX_BODY_LENGTH:
        raise ValueError(f"body must be {_MAX_BODY_LENGTH} characters or fewer")
    return body


def normalize_tags(tags: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    for raw_tag in tags:
        tag = str(raw_tag).strip().lower()
        if not tag:
            continue
        if len(tag) > _MAX_TAG_LENGTH:
            raise ValueError(f"each tag must be {_MAX_TAG_LENGTH} characters or fewer")
        if tag in seen:
            continue
        normalized.append(tag)
        seen.add(tag)
        if len(normalized) > _MAX_TAG_COUNT:
            raise ValueError(f"a maximum of {_MAX_TAG_COUNT} tags is allowed")

    return normalized


def _normalize_search_query(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized if normalized else None


def _normalize_mood(value: JournalMood | str) -> str:
    if isinstance(value, JournalMood):
        return value.value

    raw = str(value).strip()
    for mood in JournalMood:
        if raw.lower() == mood.value.lower():
            return mood.value
    raise ValueError("mood is invalid")


def _get_entry_for_account(db: Session, *, account_id: int, entry_id: int) -> JournalEntry | None:
    return (
        db.query(JournalEntry)
        .filter(JournalEntry.account_id == account_id)
        .filter(JournalEntry.id == entry_id)
        .one_or_none()
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
