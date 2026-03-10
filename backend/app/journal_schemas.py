from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JournalMood(str, Enum):
    FOCUSED = "Focused"
    NEUTRAL = "Neutral"
    FRUSTRATED = "Frustrated"
    CONFIDENT = "Confident"


class JournalEntryOut(BaseModel):
    id: int
    account_id: int
    entry_date: date
    title: str
    mood: JournalMood
    tags: list[str]
    body: str
    version: int
    stats_source: str | None = None
    stats_json: dict[str, Any] | None = None
    stats_pulled_at: datetime | None = None
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class JournalEntryCreateOut(JournalEntryOut):
    already_existed: bool = False


class JournalEntrySaveOut(BaseModel):
    id: int
    account_id: int
    entry_date: date
    title: str
    mood: JournalMood
    tags: list[str]
    version: int
    is_archived: bool
    updated_at: datetime


class JournalEntryCreateIn(BaseModel):
    entry_date: date
    title: str
    mood: JournalMood = JournalMood.NEUTRAL
    tags: list[str] = Field(default_factory=list)
    body: str = ""


class JournalEntryUpdateIn(BaseModel):
    version: int = Field(ge=1)
    entry_date: date | None = None
    title: str | None = None
    mood: JournalMood | None = None
    tags: list[str] | None = None
    body: str | None = None
    is_archived: bool | None = None


class JournalEntryListOut(BaseModel):
    items: list[JournalEntryOut]
    total: int


class JournalDaysOut(BaseModel):
    days: list[date]


class JournalImageOut(BaseModel):
    id: int
    journal_entry_id: int
    account_id: int
    entry_date: date
    filename: str
    mime_type: str
    byte_size: int
    width: int | None = None
    height: int | None = None
    created_at: datetime
    url: str


class PullTradeStatsIn(BaseModel):
    trade_ids: list[int] | None = None
    entry_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None


class JournalMergeConflictStrategy(str, Enum):
    SKIP = "skip"
    OVERWRITE = "overwrite"


class JournalMergeIn(BaseModel):
    from_account_id: int = Field(ge=1)
    to_account_id: int = Field(ge=1)
    on_conflict: JournalMergeConflictStrategy = JournalMergeConflictStrategy.SKIP
    include_images: bool = True


class JournalMergeOut(BaseModel):
    from_account_id: int
    to_account_id: int
    transferred_count: int
    skipped_count: int
    overwritten_count: int
    image_count: int
