"""Public, provenance-aware contracts for the market data workspace."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CoverageWindowOut(BaseModel):
    start: datetime
    end_exclusive: datetime
    expected_open_minutes: int
    observed_open_minutes: int
    missing_open_minutes: int
    note: str


class CandleStreamOut(BaseModel):
    contract_id: str
    symbol: str | None
    root_symbol: str
    live: bool
    unit: str
    unit_number: int
    source: str
    rows: int
    complete_rows: int
    first_timestamp: datetime
    last_timestamp: datetime
    last_fetched_at: datetime
    recent_gap_check: CoverageWindowOut | None = None


class ArchiveSeriesOut(BaseModel):
    symbol: str
    timeframe: str
    rows: int
    first_timestamp: datetime | None
    end_exclusive: datetime | None
    files_present: bool


class ArchiveOut(BaseModel):
    status: Literal["available", "missing", "invalid"]
    source: str = "databento"
    fingerprint: str | None = None
    built_at: datetime | None = None
    series: list[ArchiveSeriesOut] = Field(default_factory=list)
    schemas: dict[str, int] = Field(default_factory=dict)
    note: str


class LocalCaptureOut(BaseModel):
    capture_id: str
    status: Literal["available", "missing", "invalid"]
    source: str = "projectx"
    contract_id: str
    symbol: str
    live: bool
    rows: int
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    matching_database_rows: int = 0
    research_exposure: str = "Previously evaluated; not an untouched holdout."
    note: str


class FeedStatusOut(BaseModel):
    key: str
    label: str
    status: str
    detail: str


class MarketDataInventoryOut(BaseModel):
    generated_at: datetime
    database_rows: int
    streams: list[CandleStreamOut]
    archive: ArchiveOut
    local_capture: LocalCaptureOut
    feeds: list[FeedStatusOut]
    note: str


class MarketContextItemOut(BaseModel):
    symbol: str
    status: Literal["fresh", "stale", "missing"]
    contract_id: str | None = None
    source: str | None = None
    live: bool
    timeframe: str | None = None
    candle_timestamp: datetime | None = None
    available_at: datetime | None = None
    fetched_at: datetime | None = None
    age_seconds: float | None = None
    close: float | None = None
    volume: float | None = None
    previous_close: float | None = None
    change_pct: float | None = None
    change_period_seconds: float | None = None
    observation_date: date | None = None
    observation_kind: str | None = None
    value_unit: str | None = None
    change_bps: float | None = None
    source_url: str | None = None
    data_notice: str | None = None
    data_mode: str | None = None


class MarketContextOut(BaseModel):
    as_of: datetime
    generated_at: datetime
    items: list[MarketContextItemOut]
    missing_symbols: list[str]
    note: str


class CandleImportTimeframeOut(BaseModel):
    timeframe: str
    available_rows: int
    inserted_rows: int
    unchanged_rows: int
    conflicting_rows: int


class CandleImportOut(BaseModel):
    capture_id: str
    verified_rows: int
    inserted_rows: int
    unchanged_rows: int
    conflicting_rows: int
    timeframes: list[CandleImportTimeframeOut] = Field(default_factory=list)
    note: str


class MarketRefreshIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbols: list[Literal["MNQ", "MES", "NQ", "ES"]] = Field(default_factory=lambda: ["MNQ", "MES", "NQ", "ES"], min_length=1, max_length=4)
    days: int = Field(default=3, ge=1, le=10)
    live: bool = False


class MarketRefreshItemOut(BaseModel):
    symbol: str
    status: Literal["updated", "unavailable", "failed"]
    contract_id: str | None = None
    received_rows: int = 0
    inserted_rows: int = 0
    unchanged_rows: int = 0
    conflicting_rows: int = 0
    detail: str


class MarketRefreshOut(BaseModel):
    started_at: datetime
    finished_at: datetime
    live: bool
    items: list[MarketRefreshItemOut]


class PublicMarketRefreshIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbols: list[Literal["US10Y", "VIX"]] = Field(default_factory=lambda: ["US10Y", "VIX"], min_length=1, max_length=2)
    days: int = Field(default=365, ge=1, le=365)


class PublicMarketSourceOut(BaseModel):
    symbol: str
    source: str
    label: str
    status: Literal["ready", "stored", "disabled"]
    enabled: bool
    stored_rows: int = 0
    latest_observation_date: date | None = None
    last_collected_at: datetime | None = None
    source_url: str
    data_notice: str


class PublicMarketStatusOut(BaseModel):
    generated_at: datetime
    sources: list[PublicMarketSourceOut]


class PublicMarketRefreshItemOut(BaseModel):
    symbol: str
    source: str
    status: Literal["updated", "unavailable", "failed", "disabled"]
    received_rows: int = 0
    inserted_rows: int = 0
    unchanged_rows: int = 0
    conflicting_rows: int = 0
    detail: str
    data_notice: str


class PublicMarketRefreshOut(BaseModel):
    started_at: datetime
    finished_at: datetime
    items: list[PublicMarketRefreshItemOut]
