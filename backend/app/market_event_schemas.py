from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MarketEventSource = Literal["bls", "federal_reserve", "federal_reserve_calendar", "trading_economics"]


class MarketEventRefreshIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[MarketEventSource] = Field(default_factory=lambda: ["bls", "federal_reserve", "federal_reserve_calendar", "trading_economics"], min_length=1, max_length=4)


class MarketEventOut(BaseModel):
    id: str
    source: MarketEventSource
    source_event_id: str
    kind: Literal["calendar", "news"]
    title: str
    country: str
    importance: Literal["high", "medium", "low", "unknown"]
    scheduled_at: datetime | None
    scheduled_end_at: datetime | None
    scheduled_date: str | None
    time_precision: Literal["exact", "date"]
    published_at: datetime | None
    first_seen_at: datetime
    observed_at: datetime
    available_at: datetime
    state: Literal["scheduled", "released", "awaiting_release", "news", "cancelled"]
    actual: str | None
    forecast: str | None
    previous: str | None
    revised: str | None
    url: str | None
