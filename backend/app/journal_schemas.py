from __future__ import annotations

from datetime import date, datetime
from enum import Enum

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
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class JournalEntryCreateIn(BaseModel):
    entry_date: date
    title: str
    mood: JournalMood = JournalMood.NEUTRAL
    tags: list[str] = Field(default_factory=list)
    body: str = ""


class JournalEntryUpdateIn(BaseModel):
    entry_date: date | None = None
    title: str | None = None
    mood: JournalMood | None = None
    tags: list[str] | None = None
    body: str | None = None
    is_archived: bool | None = None


class JournalEntryListOut(BaseModel):
    items: list[JournalEntryOut]
    total: int
