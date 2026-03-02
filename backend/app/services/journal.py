from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import Text, cast, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import get_authenticated_user_id
from ..journal_schemas import JournalMood
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

    expected_version = int(version)
    current_version = int(row.version or 1)
    if expected_version != current_version:
        raise VersionConflictError(row)

    if entry_date is not None and entry_date != row.entry_date:
        existing = _get_entry_for_account_and_date(
            db,
            user_id=resolved_user_id,
            account_id=account_id,
            entry_date=entry_date,
        )
        if existing is not None and int(existing.id) != int(row.id):
            raise ValueError("journal entry already exists for this account and date")
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
    extension = _ALLOWED_JOURNAL_IMAGE_MIME_TYPES[normalized_mime]
    filename = f"{resolved_user_id}/{account_id}/{entry_id}/{uuid4().hex}.{extension}"
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
    delete_journal_image(object_key=filename)


def pull_journal_entry_trade_stats(
    db: Session,
    *,
    user_id: str | None = None,
    account_id: int,
    entry_id: int,
    trade_ids: list[int] | None = None,
    entry_date: date | None = None,
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

    query = (
        db.query(ProjectXTradeEvent.pnl, ProjectXTradeEvent.fees)
        .filter(ProjectXTradeEvent.user_id == resolved_user_id)
        .filter(ProjectXTradeEvent.account_id == account_id)
        .filter(_non_voided_trade_event_expr())
        .filter(ProjectXTradeEvent.pnl.isnot(None))
    )

    normalized_trade_ids = _normalize_trade_ids(trade_ids)
    if normalized_trade_ids:
        query = query.filter(ProjectXTradeEvent.id.in_(normalized_trade_ids))
    else:
        effective_date = entry_date or row.entry_date
        day_start, day_end = _utc_day_bounds(effective_date)
        query = query.filter(ProjectXTradeEvent.trade_timestamp >= day_start)
        query = query.filter(ProjectXTradeEvent.trade_timestamp <= day_end)

    trade_rows = query.all()
    snapshot = _compute_trade_stats_snapshot(trade_rows)

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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utc_day_bounds(value: date) -> tuple[datetime, datetime]:
    start = datetime.combine(value, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1) - timedelta(microseconds=1)
    return start, end


def _effective_trade_fee(*, pnl: float | None, fees: float | None) -> float:
    if pnl is None:
        return 0.0
    raw_fees = float(fees) if fees is not None else 0.0
    return raw_fees * 2


def _compute_trade_stats_snapshot(rows: Iterable[Any]) -> dict[str, Any]:
    pnls: list[float] = []
    fees: list[float] = []
    for row in rows:
        pnl_value = float(row.pnl) if row.pnl is not None else None
        if pnl_value is None:
            continue
        pnls.append(pnl_value)
        fees.append(_effective_trade_fee(pnl=pnl_value, fees=float(row.fees) if row.fees is not None else 0.0))

    trade_count = len(pnls)
    gross = sum(pnls)
    total_fees = sum(fees)
    net = gross - total_fees

    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]

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
        "gross": _round(gross),
        "net": _round(net),
    }


def _round(value: float, digits: int = 2) -> float:
    return round(float(value), digits)


def _non_voided_trade_event_expr():
    voided_text = func.lower(func.coalesce(ProjectXTradeEvent.raw_payload.op("->>")("voided"), "false"))
    return voided_text != "true"
