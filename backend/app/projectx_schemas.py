from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ProjectXAccountState = Literal["ACTIVE", "LOCKED_OUT", "HIDDEN", "MISSING"]
ProjectXTradeDataSource = Literal["projectx", "csv_import"]
ProjectXProviderSyncStatus = Literal[
    "provider_fresh",
    "cache_fresh",
    "cache_stale",
    "cached_fallback",
    "not_applicable",
]


class ProjectXAccountOut(BaseModel):
    id: int
    name: str
    provider_name: str
    custom_display_name: str | None = None
    balance: float | None = None
    status: ProjectXAccountState
    account_state: ProjectXAccountState
    is_main: bool
    is_archived: bool = False
    can_trade: bool | None = None
    is_visible: bool | None = None
    provider_simulated: bool | None = None
    provider_classification_observed_at: datetime | None = None
    last_trade_at: datetime | None = None
    last_seen_at: datetime | None = None
    provider_data_stale: bool = False
    provider_data_stale_at: datetime | None = None
    provider_sync_status: ProjectXProviderSyncStatus
    provider_sync_error_code: str | None = None
    provider_sync_error_message: str | None = None
    provider_last_successful_refresh_at: datetime | None = None
    trade_data_source: ProjectXTradeDataSource


class ProjectXAccountAutomationClassificationOut(BaseModel):
    account_id: int
    provider_simulated: bool
    provider_classification_observed_at: datetime
    source: Literal["projectx_user_hub"]


class TopstepLiveAccountCreateIn(BaseModel):
    name: str
    starting_balance: float | None = Field(
        default=None,
        gt=0,
        le=1_000_000_000,
    )


class ProjectXAccountTradeDataSourceIn(BaseModel):
    trade_data_source: ProjectXTradeDataSource


class ProjectXAccountMainOut(BaseModel):
    account_id: int
    is_main: bool


class ProjectXAccountArchiveIn(BaseModel):
    replacement_account_id: int | None = Field(
        default=None,
        gt=0,
        le=9_223_372_036_854_775_807,
    )


class ProjectXAccountArchiveOut(BaseModel):
    account_id: int
    is_archived: bool
    is_main: bool
    replacement_main_account_id: int | None = None


class ProjectXAccountRenameIn(BaseModel):
    display_name: str


class ProjectXAccountRenameOut(BaseModel):
    account_id: int
    name: str
    provider_name: str
    custom_display_name: str | None = None


class ProjectXAccountLastTradeOut(BaseModel):
    account_id: int
    last_trade_at: datetime | None = None
    source: str


class ProjectXTradeOut(BaseModel):
    id: int
    account_id: int
    contract_id: str
    symbol: str
    side: str
    size: float
    price: float
    timestamp: datetime
    entry_time: datetime | None = None
    exit_time: datetime
    duration_minutes: float | None = None
    entry_price: float | None = None
    exit_price: float
    fees: float
    non_commission_fees: float = 0.0
    commissions: float = 0.0
    pnl: float | None = None
    order_id: str
    source_trade_id: str | None = None


class ProjectXSizingBenchmarkOut(BaseModel):
    benchmarkMode: Literal["fixed_average_size"]
    benchmarkSizeUsed: float
    benchmarkGrossPnl: float
    benchmarkNetPnl: float
    benchmarkDiff: float
    benchmarkRatio: float | None = None
    benchmarkLabel: Literal[
        "Far Below Benchmark",
        "Below Benchmark",
        "In Line With Benchmark",
        "Above Benchmark",
        "Far Above Benchmark",
    ]


class ProjectXTradeSummaryOut(BaseModel):
    realized_pnl: float
    gross_pnl: float
    fees: float
    net_pnl: float
    win_rate: float
    win_count: int
    loss_count: int
    breakeven_count: int
    profit_factor: float
    avg_win: float
    avg_loss: float
    avg_win_duration_minutes: float
    avg_loss_duration_minutes: float
    expectancy_per_trade: float
    tail_risk_5pct: float
    max_drawdown: float
    average_drawdown: float
    risk_drawdown_score: float
    max_drawdown_length_hours: float
    recovery_time_hours: float
    average_recovery_length_hours: float
    trade_count: int
    half_turn_count: int
    execution_count: int
    day_win_rate: float
    green_days: int
    red_days: int
    flat_days: int
    avg_trades_per_day: float
    active_days: int
    efficiency_per_hour: float
    profit_per_day: float
    averagePositionSize: float
    medianPositionSize: float
    tradeCountUsedForSizingStats: int
    avgPointGain: float | None = None
    avgPointLoss: float | None = None
    pointsBasisUsed: str
    sizingBenchmark: ProjectXSizingBenchmarkOut


class ProjectXPointPayoffOut(BaseModel):
    avgPointGain: float | None = None
    avgPointLoss: float | None = None


class ProjectXTradeSummaryWithPointBasesOut(BaseModel):
    summary: ProjectXTradeSummaryOut
    point_payoff_by_basis: dict[str, ProjectXPointPayoffOut]


class ProjectXTradeRefreshOut(BaseModel):
    fetched_count: int
    inserted_count: int


class ProjectXPnlCalendarDayOut(BaseModel):
    date: date
    trade_count: int
    gross_pnl: float
    fees: float
    non_commission_fees: float = 0.0
    commissions: float = 0.0
    net_pnl: float
    win_count: int = 0
    loss_count: int = 0
    breakeven_count: int = 0


class TopstepTradeImportSummaryOut(BaseModel):
    gross_pnl: float
    fees: float
    commissions: float
    net_pnl: float
    wins: int
    losses: int
    breakeven: int


class TopstepTradeImportConflictDifferenceOut(BaseModel):
    field: str
    stored: Any = None
    incoming: Any = None


class TopstepTradeImportConflictOut(BaseModel):
    identity_kind: Literal["source_trade_id", "order_exit"]
    identity_value: str
    reason: Literal[
        "repeated_id_mismatch",
        "stored_trade_mismatch",
        "ambiguous_stored_identity",
    ]
    stored_event_id: int | None = None
    stored_event_ids: list[int] | None = None
    stored_row_number: int | None = None
    differences: list[TopstepTradeImportConflictDifferenceOut]


class TopstepTradeImportPreviewRowOut(BaseModel):
    row_number: int
    source_trade_id: str
    contract_name: str
    symbol: str
    entered_at: datetime
    exited_at: datetime
    entry_price: float
    exit_price: float
    fees: float
    commissions: float
    gross_pnl: float
    net_pnl: float
    size: float
    direction: Literal["Long", "Short"]
    trade_day: date
    duration: str | None = None
    status: Literal["new", "duplicate", "conflict"]
    conflict: TopstepTradeImportConflictOut | None = None


class TopstepTradeImportPreviewOut(BaseModel):
    preview_token: str
    expires_at: datetime
    source_file_name: str
    file_sha256: str
    total_rows: int
    new_rows: int
    duplicate_rows: int
    conflict_rows: int
    summary: TopstepTradeImportSummaryOut
    trades: list[TopstepTradeImportPreviewRowOut]


class TopstepTradeImportConfirmOut(BaseModel):
    import_id: int
    source_file_name: str
    imported_at: datetime
    total_rows: int
    inserted_rows: int
    duplicate_rows: int


class TopstepTradeImportStatusIn(BaseModel):
    preview_token: str = Field(min_length=32, max_length=200)


class TopstepTradeImportStatusOut(BaseModel):
    status: Literal["pending", "confirming", "committed", "expired", "stale", "conflict", "failed"]
    confirmation_retryable: bool
    outcome_code: str | None = None
    source_file_name: str
    created_at: datetime
    expires_at: datetime
    confirmed_at: datetime | None = None
    total_rows: int
    new_rows: int
    duplicate_rows: int
    conflict_rows: int
    result: TopstepTradeImportConfirmOut | None = None


class ProjectXCredentialsUpsertIn(BaseModel):
    username: str
    api_key: str


class ProjectXCredentialsStatusOut(BaseModel):
    configured: bool
    decryptable: bool
    status: Literal["not_configured", "ready", "unavailable"]
    error_code: str | None = None


class AuthMeOut(BaseModel):
    user_id: str
    email: str | None = None
