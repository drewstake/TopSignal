from __future__ import annotations

import logging
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import Text, cast, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only

from ..auth import get_authenticated_user_id
from ..journal_schemas import JournalMergeConflictStrategy, JournalMood
from ..models import JournalEntry, JournalEntryImage, ProjectXTradeEvent
from .journal_storage import delete_journal_image, load_journal_image, local_journal_image_path, save_journal_image

_MAX_TITLE_LENGTH = 160
_MAX_BODY_LENGTH = 20_000
_MAX_TAG_COUNT = 20
_MAX_TAG_LENGTH = 32
_MAX_JOURNAL_IMAGE_BYTES = 10 * 1024 * 1024
_ALLOWED_JOURNAL_IMAGE_MIME_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
}
_TRADING_TZ = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)


class VersionConflictError(Exception):
    def __init__(self, server_row: JournalEntry):
        super().__init__("version_conflict")
        self.server_row = server_row


def validate_date_range(*, start_date: date | None, end_date: date | None) -> None:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start_date must be before or equal to end_date")


def _resolve_user_id(user_id: str | None) -> str:
    if user_id:
        return user_id
    return get_authenticated_user_id()


def list_journal_entries(
    db: Session,
    *,
    user_id: str | None = None,
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
    resolved_user_id = _resolve_user_id(user_id)

    query = (
        db.query(JournalEntry)
        .filter(JournalEntry.user_id == resolved_user_id)
        .filter(JournalEntry.account_id == account_id)
    )
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


def list_journal_days(
    db: Session,
    *,
    user_id: str | None = None,
    account_id: int,
    start_date: date,
    end_date: date,
    include_archived: bool = False,
) -> list[date]:
    validate_date_range(start_date=start_date, end_date=end_date)
    resolved_user_id = _resolve_user_id(user_id)

    query = (
        db.query(JournalEntry.entry_date)
        .filter(JournalEntry.user_id == resolved_user_id)
        .filter(JournalEntry.account_id == account_id)
        .filter(JournalEntry.entry_date >= start_date)
        .filter(JournalEntry.entry_date <= end_date)
    )
    if not include_archived:
        query = query.filter(JournalEntry.is_archived.is_(False))

    rows = query.distinct().order_by(JournalEntry.entry_date.asc()).all()
    return [row.entry_date for row in rows]


def create_journal_entry(
    db: Session,
    *,
    user_id: str | None = None,
    account_id: int,
    entry_date: date,
    title: str,
    mood: JournalMood | str,
    tags: list[str],
    body: str,
) -> tuple[JournalEntry, bool]:
    resolved_user_id = _resolve_user_id(user_id)
    existing = _get_entry_for_account_and_date(
        db,
        user_id=resolved_user_id,
        account_id=account_id,
        entry_date=entry_date,
    )
    if existing is not None:
        return existing, True

    now = _utcnow()
    row = JournalEntry(
        user_id=resolved_user_id,
        account_id=account_id,
        entry_date=entry_date,
        title=normalize_title(title),
        mood=_normalize_mood(mood),
        tags=normalize_tags(tags),
        body=normalize_body(body),
        version=1,
        is_archived=False,
        updated_at=now,
    )
    db.add(row)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _get_entry_for_account_and_date(
            db,
            user_id=resolved_user_id,
            account_id=account_id,
            entry_date=entry_date,
        )
        if existing is not None:
            return existing, True
        raise

    db.refresh(row)
    return row, False


def merge_journal_entries(
    db: Session,
    *,
    user_id: str | None = None,
    from_account_id: int,
    to_account_id: int,
    on_conflict: JournalMergeConflictStrategy | str = JournalMergeConflictStrategy.SKIP,
    include_images: bool = True,
) -> dict[str, int]:
    resolved_user_id = _resolve_user_id(user_id)
    if from_account_id == to_account_id:
        raise ValueError("from_account_id and to_account_id must be different")

    normalized_conflict = _normalize_merge_conflict_strategy(on_conflict)
    source_rows = (
        db.query(JournalEntry)
        .filter(JournalEntry.user_id == resolved_user_id)
        .filter(JournalEntry.account_id == from_account_id)
        .order_by(JournalEntry.entry_date.asc(), JournalEntry.id.asc())
        .all()
    )

    summary = {
        "from_account_id": int(from_account_id),
        "to_account_id": int(to_account_id),
        "transferred_count": 0,
        "skipped_count": 0,
        "overwritten_count": 0,
        "image_count": 0,
    }
    if not source_rows:
        return summary

    source_dates = [row.entry_date for row in source_rows]
    existing_dest_rows = (
        db.query(JournalEntry)
        .filter(JournalEntry.user_id == resolved_user_id)
        .filter(JournalEntry.account_id == to_account_id)
        .filter(JournalEntry.entry_date.in_(source_dates))
        .all()
    )
    dest_by_date = {row.entry_date: row for row in existing_dest_rows}

    source_images_by_entry_id: dict[int, list[JournalEntryImage]] = {}
    if include_images:
        source_entry_ids = [int(row.id) for row in source_rows]
        source_image_rows = (
            db.query(JournalEntryImage)
            .filter(JournalEntryImage.user_id == resolved_user_id)
            .filter(JournalEntryImage.journal_entry_id.in_(source_entry_ids))
            .order_by(JournalEntryImage.created_at.asc(), JournalEntryImage.id.asc())
            .all()
        )
        for image_row in source_image_rows:
            source_images_by_entry_id.setdefault(int(image_row.journal_entry_id), []).append(image_row)

    created_filenames: list[str] = []
    filenames_to_delete_after_commit: list[str] = []
    try:
        for source_row in source_rows:
            source_entry_id = int(source_row.id)
            existing_dest_row = dest_by_date.get(source_row.entry_date)
            source_images = source_images_by_entry_id.get(source_entry_id, [])

            if existing_dest_row is None:
                next_row = JournalEntry(
                    user_id=resolved_user_id,
                    account_id=to_account_id,
                    entry_date=source_row.entry_date,
                    title=source_row.title,
                    mood=source_row.mood,
                    tags=_copy_tags(source_row.tags),
                    body=source_row.body,
                    version=max(int(source_row.version or 1), 1),
                    # Trade snapshots are account-specific and must be recomputed after a merge.
                    stats_source=None,
                    stats_json=None,
                    stats_pulled_at=None,
                    is_archived=bool(source_row.is_archived),
                    created_at=source_row.created_at,
                    updated_at=source_row.updated_at,
                )
                db.add(next_row)
                db.flush()
                dest_by_date[source_row.entry_date] = next_row
                summary["transferred_count"] += 1
                if include_images:
                    summary["image_count"] += _copy_journal_entry_images(
                        db,
                        user_id=resolved_user_id,
                        account_id=to_account_id,
                        destination_entry=next_row,
                        source_images=source_images,
                        created_filenames=created_filenames,
                    )
                continue

            if normalized_conflict == JournalMergeConflictStrategy.SKIP.value:
                summary["skipped_count"] += 1
                continue

            _overwrite_journal_entry(destination=existing_dest_row, source=source_row)
            summary["transferred_count"] += 1
            summary["overwritten_count"] += 1

            if include_images:
                for existing_image in _list_images_for_entry(
                    db,
                    user_id=resolved_user_id,
                    account_id=to_account_id,
                    entry_id=int(existing_dest_row.id),
                ):
                    filenames_to_delete_after_commit.append(existing_image.filename)
                    db.delete(existing_image)
                db.flush()
                summary["image_count"] += _copy_journal_entry_images(
                    db,
                    user_id=resolved_user_id,
                    account_id=to_account_id,
                    destination_entry=existing_dest_row,
                    source_images=source_images,
                    created_filenames=created_filenames,
                )

        db.commit()
    except Exception:
        db.rollback()
        for filename in created_filenames:
            _delete_journal_image_quietly(filename)
        raise

    for filename in filenames_to_delete_after_commit:
        _delete_journal_image_quietly(filename)

    return summary


def update_journal_entry(
    db: Session,
    *,
    user_id: str | None = None,
    account_id: int,
    entry_id: int,
    version: int,
    entry_date: date | None = None,
    title: str | None = None,
    mood: JournalMood | str | None = None,
    tags: list[str] | None = None,
    body: str | None = None,
    is_archived: bool | None = None,
) -> JournalEntry:
    resolved_user_id = _resolve_user_id(user_id)
    row = _get_entry_for_account(
        db,
        user_id=resolved_user_id,
        account_id=account_id,
        entry_id=entry_id,
    )
    if row is None:
        raise LookupError("journal entry not found")

    normalized_updates: dict[str, Any] = {}
    if entry_date is not None:
        if entry_date != row.entry_date:
            existing = _get_entry_for_account_and_date(
                db,
                user_id=resolved_user_id,
                account_id=account_id,
                entry_date=entry_date,
            )
            if existing is not None and int(existing.id) != int(row.id):
                raise ValueError("journal entry already exists for this account and date")
        normalized_updates["entry_date"] = entry_date
    if title is not None:
        normalized_updates["title"] = normalize_title(title)
    if mood is not None:
        normalized_updates["mood"] = _normalize_mood(mood)
    if tags is not None:
        normalized_updates["tags"] = normalize_tags(tags)
    if body is not None:
        normalized_updates["body"] = normalize_body(body)
    if is_archived is not None:
        normalized_updates["is_archived"] = bool(is_archived)

    expected_version = int(version)
    current_version = int(row.version or 1)
    if expected_version != current_version:
        if _journal_entry_matches_updates(row, normalized_updates):
            return row
        raise VersionConflictError(row)

    has_changes = False
    for field_name, next_value in normalized_updates.items():
        if _journal_entry_field_value(row, field_name) == next_value:
            continue
        setattr(row, field_name, next_value)
        has_changes = True

    if not has_changes:
        return row

    row.version = current_version + 1
    row.updated_at = _utcnow()
    db.add(row)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("journal entry already exists for this account and date") from exc

    db.refresh(row)
    return row


def archive_journal_entry(
    db: Session,
    *,
    user_id: str | None = None,
    account_id: int,
    entry_id: int,
    version: int,
) -> JournalEntry:
    return update_journal_entry(
        db,
        user_id=user_id,
        account_id=account_id,
        entry_id=entry_id,
        version=version,
        is_archived=True,
    )


def unarchive_journal_entry(
    db: Session,
    *,
    user_id: str | None = None,
    account_id: int,
    entry_id: int,
    version: int,
) -> JournalEntry:
    return update_journal_entry(
        db,
        user_id=user_id,
        account_id=account_id,
        entry_id=entry_id,
        version=version,
        is_archived=False,
    )


def delete_journal_entry(
    db: Session,
    *,
    user_id: str | None = None,
    account_id: int,
    entry_id: int,
) -> None:
    resolved_user_id = _resolve_user_id(user_id)
    row = _get_entry_for_account(
        db,
        user_id=resolved_user_id,
        account_id=account_id,
        entry_id=entry_id,
    )
    if row is None:
        raise LookupError("journal entry not found")

    image_filenames = [
        image_row.filename
        for image_row in db.query(JournalEntryImage.filename)
        .filter(JournalEntryImage.user_id == resolved_user_id)
        .filter(JournalEntryImage.journal_entry_id == entry_id)
        .all()
    ]

    db.delete(row)
    db.commit()

    for filename in image_filenames:
        delete_journal_image(object_key=filename)


def create_journal_entry_image(
    db: Session,
    *,
    user_id: str | None = None,
    account_id: int,
    entry_id: int,
    file_bytes: bytes,
    mime_type: str | None,
) -> JournalEntryImage:
    resolved_user_id = _resolve_user_id(user_id)
    entry = _get_entry_for_account(
        db,
        user_id=resolved_user_id,
        account_id=account_id,
        entry_id=entry_id,
    )
    if entry is None:
        raise LookupError("journal entry not found")

    byte_size = len(file_bytes)
    if byte_size <= 0:
        raise ValueError("image file must not be empty")
    if byte_size > _MAX_JOURNAL_IMAGE_BYTES:
        raise ValueError("image file exceeds 10MB limit")

    normalized_mime = _normalize_image_mime_type(mime_type)
    filename = _build_journal_image_filename(
        user_id=resolved_user_id,
        account_id=account_id,
        entry_id=entry_id,
        mime_type=normalized_mime,
    )
    save_journal_image(object_key=filename, file_bytes=file_bytes, mime_type=normalized_mime)

    row = JournalEntryImage(
        user_id=resolved_user_id,
        journal_entry_id=int(entry.id),
        account_id=account_id,
        entry_date=entry.entry_date,
        filename=filename,
        mime_type=normalized_mime,
        byte_size=byte_size,
        width=None,
        height=None,
    )
    db.add(row)

    try:
        db.commit()
    except Exception:
        db.rollback()
        delete_journal_image(object_key=filename)
        raise

    db.refresh(row)
    return row


def list_journal_entry_images(
    db: Session,
    *,
    user_id: str | None = None,
    account_id: int,
    entry_id: int,
) -> list[JournalEntryImage]:
    resolved_user_id = _resolve_user_id(user_id)
    entry = _get_entry_for_account(
        db,
        user_id=resolved_user_id,
        account_id=account_id,
        entry_id=entry_id,
    )
    if entry is None:
        raise LookupError("journal entry not found")

    return (
        db.query(JournalEntryImage)
        .filter(JournalEntryImage.user_id == resolved_user_id)
        .filter(JournalEntryImage.account_id == account_id)
        .filter(JournalEntryImage.journal_entry_id == entry_id)
        .order_by(JournalEntryImage.created_at.asc(), JournalEntryImage.id.asc())
        .all()
    )


def get_journal_entry_image(
    db: Session,
    *,
    user_id: str | None = None,
    image_id: int,
    account_id: int | None = None,
) -> JournalEntryImage:
    resolved_user_id = _resolve_user_id(user_id)
    query = (
        db.query(JournalEntryImage)
        .filter(JournalEntryImage.user_id == resolved_user_id)
        .filter(JournalEntryImage.id == image_id)
    )
    if account_id is not None:
        query = query.filter(JournalEntryImage.account_id == account_id)

    row = query.one_or_none()
    if row is None:
        raise LookupError("journal image not found")
    return row


def delete_journal_entry_image(
    db: Session,
    *,
    user_id: str | None = None,
    account_id: int,
    entry_id: int,
    image_id: int,
) -> None:
    filename = delete_journal_entry_image_record(
        db,
        user_id=user_id,
        account_id=account_id,
        entry_id=entry_id,
        image_id=image_id,
    )
    delete_journal_image(object_key=filename)


def delete_journal_entry_image_record(
    db: Session,
    *,
    user_id: str | None = None,
    account_id: int,
    entry_id: int,
    image_id: int,
) -> str:
    resolved_user_id = _resolve_user_id(user_id)
    entry = _get_entry_for_account(
        db,
        user_id=resolved_user_id,
        account_id=account_id,
        entry_id=entry_id,
    )
    if entry is None:
        raise LookupError("journal entry not found")

    row = (
        db.query(JournalEntryImage)
        .filter(JournalEntryImage.user_id == resolved_user_id)
        .filter(JournalEntryImage.id == image_id)
        .filter(JournalEntryImage.account_id == account_id)
        .filter(JournalEntryImage.journal_entry_id == entry_id)
        .one_or_none()
    )
    if row is None:
        raise LookupError("journal image not found")

    filename = row.filename
    db.delete(row)
    db.commit()
    return filename


def pull_journal_entry_trade_stats(
    db: Session,
    *,
    user_id: str | None = None,
    account_id: int,
    entry_id: int,
    trade_ids: list[int] | None = None,
    entry_date: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    before_query_sync: Callable[[datetime | None, datetime | None], None] | None = None,
) -> JournalEntry:
    resolved_user_id = _resolve_user_id(user_id)
    row = _get_entry_for_account(
        db,
        user_id=resolved_user_id,
        account_id=account_id,
        entry_id=entry_id,
    )
    if row is None:
        raise LookupError("journal entry not found")

    base_trade_query = (
        db.query(ProjectXTradeEvent)
        .options(
            load_only(
                ProjectXTradeEvent.id,
                ProjectXTradeEvent.contract_id,
                ProjectXTradeEvent.symbol,
                ProjectXTradeEvent.side,
                ProjectXTradeEvent.size,
                ProjectXTradeEvent.trade_timestamp,
                ProjectXTradeEvent.fees,
                ProjectXTradeEvent.pnl,
            )
        )
        .filter(ProjectXTradeEvent.user_id == resolved_user_id)
        .filter(ProjectXTradeEvent.account_id == account_id)
        .filter(_non_voided_trade_event_expr())
    )
    closed_query = base_trade_query.filter(ProjectXTradeEvent.pnl.isnot(None))
    window_start: datetime | None = None
    window_end: datetime | None = None

    normalized_trade_ids = _normalize_trade_ids(trade_ids)
    if normalized_trade_ids:
        closed_query = closed_query.filter(ProjectXTradeEvent.id.in_(normalized_trade_ids))
    elif start_date is not None or end_date is not None:
        validate_date_range(start_date=start_date, end_date=end_date)
        if start_date is not None:
            window_start, _ = _trading_day_bounds(start_date)
            closed_query = closed_query.filter(ProjectXTradeEvent.trade_timestamp >= window_start)
        if end_date is not None:
            _, window_end = _trading_day_bounds(end_date)
            closed_query = closed_query.filter(ProjectXTradeEvent.trade_timestamp <= window_end)
        if before_query_sync is not None:
            before_query_sync(window_start, window_end)
    else:
        effective_date = entry_date or row.entry_date
        window_start, window_end = _trading_day_bounds(effective_date)
        if before_query_sync is not None:
            before_query_sync(window_start, window_end)
        closed_query = closed_query.filter(ProjectXTradeEvent.trade_timestamp >= window_start)
        closed_query = closed_query.filter(ProjectXTradeEvent.trade_timestamp <= window_end)

    closed_rows = (
        closed_query.order_by(ProjectXTradeEvent.trade_timestamp.asc(), ProjectXTradeEvent.id.asc()).all()
    )

    if normalized_trade_ids and closed_rows:
        window_start = min(_as_utc(trade.trade_timestamp) for trade in closed_rows)
        window_end = max(_as_utc(trade.trade_timestamp) for trade in closed_rows)

    largest_position_size = 0.0
    if window_end is not None:
        window_event_query = base_trade_query.filter(ProjectXTradeEvent.trade_timestamp <= window_end)
        if window_start is not None:
            window_event_query = window_event_query.filter(ProjectXTradeEvent.trade_timestamp >= window_start)
        window_events = (
            window_event_query.order_by(ProjectXTradeEvent.trade_timestamp.asc(), ProjectXTradeEvent.id.asc()).all()
        )
        contract_ids = sorted({str(trade.contract_id) for trade in window_events if trade.contract_id})
        if contract_ids:
            context_rows = (
                base_trade_query.filter(ProjectXTradeEvent.contract_id.in_(contract_ids))
                .filter(ProjectXTradeEvent.trade_timestamp <= window_end)
                .order_by(ProjectXTradeEvent.trade_timestamp.asc(), ProjectXTradeEvent.id.asc())
                .all()
            )
            largest_position_size = _compute_largest_position_size_from_events(
                context_rows,
                window_start=window_start,
                window_end=window_end,
            )

    snapshot = _compute_trade_stats_snapshot(closed_rows, largest_position_size=largest_position_size)

    now = _utcnow()
    row.stats_source = "trade_snapshot"
    row.stats_json = snapshot
    row.stats_pulled_at = now
    row.version = int(row.version or 1) + 1
    row.updated_at = now

    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def serialize_journal_entry(row: JournalEntry) -> dict[str, Any]:
    raw_tags = row.tags if isinstance(row.tags, list) else []
    tags = [str(tag) for tag in raw_tags]
    stats_json: dict[str, Any] | None = None
    if isinstance(row.stats_json, dict):
        stats_json = row.stats_json
    return {
        "id": int(row.id),
        "account_id": int(row.account_id),
        "entry_date": row.entry_date,
        "title": row.title,
        "mood": row.mood,
        "tags": tags,
        "body": row.body,
        "version": int(row.version or 1),
        "stats_source": row.stats_source,
        "stats_json": stats_json,
        "stats_pulled_at": row.stats_pulled_at,
        "is_archived": bool(row.is_archived),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def serialize_journal_entry_save(row: JournalEntry) -> dict[str, Any]:
    raw_tags = row.tags if isinstance(row.tags, list) else []
    tags = [str(tag) for tag in raw_tags]
    return {
        "id": int(row.id),
        "account_id": int(row.account_id),
        "entry_date": row.entry_date,
        "title": row.title,
        "mood": row.mood,
        "tags": tags,
        "version": int(row.version or 1),
        "is_archived": bool(row.is_archived),
        "updated_at": row.updated_at,
    }


def serialize_journal_entry_image(row: JournalEntryImage, *, url: str) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "journal_entry_id": int(row.journal_entry_id),
        "account_id": int(row.account_id),
        "entry_date": row.entry_date,
        "filename": row.filename,
        "mime_type": row.mime_type,
        "byte_size": int(row.byte_size),
        "width": int(row.width) if row.width is not None else None,
        "height": int(row.height) if row.height is not None else None,
        "created_at": row.created_at,
        "url": url,
    }


def get_journal_image_file_path(filename: str) -> Path:
    return local_journal_image_path(filename)


def get_journal_image_bytes(filename: str) -> bytes:
    return load_journal_image(object_key=filename)


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


def _normalize_merge_conflict_strategy(value: JournalMergeConflictStrategy | str) -> str:
    if isinstance(value, JournalMergeConflictStrategy):
        return value.value

    normalized = str(value).strip().lower()
    if normalized in {
        JournalMergeConflictStrategy.SKIP.value,
        JournalMergeConflictStrategy.OVERWRITE.value,
    }:
        return normalized
    raise ValueError("on_conflict is invalid")


def _journal_entry_field_value(row: JournalEntry, field_name: str) -> Any:
    value = getattr(row, field_name)
    if field_name == "tags":
        raw_tags = value if isinstance(value, list) else []
        return [str(tag) for tag in raw_tags]
    return value


def _journal_entry_matches_updates(row: JournalEntry, updates: dict[str, Any]) -> bool:
    return all(_journal_entry_field_value(row, field_name) == next_value for field_name, next_value in updates.items())


def _normalize_mood(value: JournalMood | str) -> str:
    if isinstance(value, JournalMood):
        return value.value

    raw = str(value).strip()
    for mood in JournalMood:
        if raw.lower() == mood.value.lower():
            return mood.value
    raise ValueError("mood is invalid")


def _normalize_trade_ids(trade_ids: Iterable[int] | None) -> list[int]:
    if trade_ids is None:
        return []
    normalized = sorted({int(trade_id) for trade_id in trade_ids if int(trade_id) > 0})
    return normalized


def _get_entry_for_account(
    db: Session,
    *,
    user_id: str,
    account_id: int,
    entry_id: int,
) -> JournalEntry | None:
    return (
        db.query(JournalEntry)
        .filter(JournalEntry.user_id == user_id)
        .filter(JournalEntry.account_id == account_id)
        .filter(JournalEntry.id == entry_id)
        .one_or_none()
    )


def _get_entry_for_account_and_date(
    db: Session,
    *,
    user_id: str,
    account_id: int,
    entry_date: date,
) -> JournalEntry | None:
    return (
        db.query(JournalEntry)
        .filter(JournalEntry.user_id == user_id)
        .filter(JournalEntry.account_id == account_id)
        .filter(JournalEntry.entry_date == entry_date)
        .one_or_none()
    )


def _normalize_image_mime_type(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if not normalized:
        raise ValueError("image mime type is required")
    if normalized == "image/jpg":
        normalized = "image/jpeg"
    if normalized not in _ALLOWED_JOURNAL_IMAGE_MIME_TYPES:
        raise ValueError("unsupported image type")
    return normalized


def _build_journal_image_filename(*, user_id: str, account_id: int, entry_id: int, mime_type: str) -> str:
    extension = _ALLOWED_JOURNAL_IMAGE_MIME_TYPES[mime_type]
    return f"{user_id}/{account_id}/{entry_id}/{uuid4().hex}.{extension}"


def _copy_tags(value: Any) -> list[str]:
    raw_tags = value if isinstance(value, list) else []
    return [str(tag) for tag in raw_tags]


def _copy_stats_json(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return deepcopy(value)


def _overwrite_journal_entry(*, destination: JournalEntry, source: JournalEntry) -> None:
    destination.title = source.title
    destination.mood = source.mood
    destination.tags = _copy_tags(source.tags)
    destination.body = source.body
    destination.stats_source = None
    destination.stats_json = None
    destination.stats_pulled_at = None
    destination.is_archived = bool(source.is_archived)
    destination.version = max(int(destination.version or 1), int(source.version or 1)) + 1
    destination.updated_at = _utcnow()


def _copy_journal_entry_images(
    db: Session,
    *,
    user_id: str,
    account_id: int,
    destination_entry: JournalEntry,
    source_images: list[JournalEntryImage],
    created_filenames: list[str],
) -> int:
    copied_count = 0
    destination_entry_id = int(destination_entry.id)
    for source_image in source_images:
        file_bytes = load_journal_image(object_key=source_image.filename)
        normalized_mime = _normalize_image_mime_type(source_image.mime_type)
        filename = _build_journal_image_filename(
            user_id=user_id,
            account_id=account_id,
            entry_id=destination_entry_id,
            mime_type=normalized_mime,
        )
        save_journal_image(object_key=filename, file_bytes=file_bytes, mime_type=normalized_mime)
        created_filenames.append(filename)

        db.add(
            JournalEntryImage(
                user_id=user_id,
                journal_entry_id=destination_entry_id,
                account_id=account_id,
                entry_date=destination_entry.entry_date,
                filename=filename,
                mime_type=normalized_mime,
                byte_size=int(source_image.byte_size),
                width=source_image.width,
                height=source_image.height,
                created_at=source_image.created_at,
            )
        )
        copied_count += 1

    if copied_count > 0:
        db.flush()
    return copied_count


def _list_images_for_entry(
    db: Session,
    *,
    user_id: str,
    account_id: int,
    entry_id: int,
) -> list[JournalEntryImage]:
    return (
        db.query(JournalEntryImage)
        .filter(JournalEntryImage.user_id == user_id)
        .filter(JournalEntryImage.account_id == account_id)
        .filter(JournalEntryImage.journal_entry_id == entry_id)
        .order_by(JournalEntryImage.created_at.asc(), JournalEntryImage.id.asc())
        .all()
    )


def _delete_journal_image_quietly(object_key: str) -> None:
    try:
        delete_journal_image(object_key=object_key)
    except Exception:
        logger.exception("journal_image_delete_failed", extra={"object_key": object_key})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _trading_day_bounds(value: date) -> tuple[datetime, datetime]:
    # Keep date boundaries aligned with dashboard/trading views (America/New_York).
    start_local = datetime.combine(value, time.min, tzinfo=_TRADING_TZ)
    end_local = start_local + timedelta(days=1) - timedelta(microseconds=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _effective_trade_fee(*, pnl: float | None, fees: float | None) -> float:
    if pnl is None:
        return 0.0
    raw_fees = float(fees) if fees is not None else 0.0
    return raw_fees * 2


def _compute_trade_stats_snapshot(
    rows: Iterable[Any],
    *,
    largest_position_size: float | None = None,
) -> dict[str, Any]:
    pnls: list[float] = []
    fees: list[float] = []
    net_values: list[float] = []
    position_sizes: list[float] = []
    for row in rows:
        pnl_value = float(row.pnl) if row.pnl is not None else None
        if pnl_value is None:
            continue
        pnls.append(pnl_value)
        fee_value = _effective_trade_fee(pnl=pnl_value, fees=float(row.fees) if row.fees is not None else 0.0)
        fees.append(fee_value)
        net_values.append(pnl_value - fee_value)
        size_value = abs(float(row.size)) if getattr(row, "size", None) is not None else 0.0
        position_sizes.append(size_value)

    trade_count = len(net_values)
    gross = sum(pnls)
    total_fees = sum(fees)
    net = sum(net_values)

    wins = [value for value in net_values if value > 0]
    losses = [value for value in net_values if value < 0]

    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    win_rate = ((len(wins) / trade_count) * 100.0) if trade_count > 0 else 0.0

    return {
        "trade_count": trade_count,
        "total_pnl": _round(gross),
        "total_fees": _round(total_fees),
        "win_rate": _round(win_rate, 2),
        "avg_win": _round(avg_win),
        "avg_loss": _round(avg_loss),
        "largest_win": _round(max(wins) if wins else 0.0),
        "largest_loss": _round(min(losses) if losses else 0.0),
        "largest_position_size": _round(
            largest_position_size if largest_position_size is not None else max(position_sizes) if position_sizes else 0.0,
            4,
        ),
        "gross": _round(gross),
        "net": _round(net),
        "net_realized_pnl": _round(net),
    }


def _compute_largest_position_size_from_events(
    rows: Iterable[ProjectXTradeEvent],
    *,
    window_start: datetime | None,
    window_end: datetime | None,
) -> float:
    epsilon = 1e-9
    positions_by_contract: dict[str, float] = {}
    largest = 0.0
    window_started = window_start is None

    for row in rows:
        trade_ts = _as_utc(row.trade_timestamp)
        if window_end is not None and trade_ts > window_end:
            break

        if not window_started and window_start is not None and trade_ts >= window_start:
            largest = max(largest, _max_abs_position(positions_by_contract))
            window_started = True

        qty = abs(float(row.size)) if row.size is not None else 0.0
        side_sign = _trade_side_sign(row.side)
        if qty <= epsilon or side_sign == 0:
            continue

        contract_key = str(row.contract_id or row.symbol or "__UNKNOWN__")
        next_position = positions_by_contract.get(contract_key, 0.0) + (side_sign * qty)
        if abs(next_position) <= epsilon:
            positions_by_contract.pop(contract_key, None)
        else:
            positions_by_contract[contract_key] = next_position

        if window_started:
            largest = max(largest, abs(next_position))

    return _round(largest, 4)


def _max_abs_position(positions_by_contract: dict[str, float]) -> float:
    if not positions_by_contract:
        return 0.0
    return max(abs(position) for position in positions_by_contract.values())


def _trade_side_sign(side: str | None) -> int:
    normalized = (side or "").strip().upper()
    if normalized == "BUY":
        return 1
    if normalized == "SELL":
        return -1
    return 0


def _round(value: float, digits: int = 2) -> float:
    return round(float(value), digits)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _non_voided_trade_event_expr():
    voided_text = func.lower(func.coalesce(ProjectXTradeEvent.raw_payload.op("->>")("voided"), "false"))
    return voided_text != "true"
