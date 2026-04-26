from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


TimeframeUnit = Literal["second", "minute", "hour", "day", "week", "month"]
BotExecutionMode = Literal["dry_run", "live"]
BotRunStatus = Literal["running", "stopped", "blocked", "error"]
BotAction = Literal["BUY", "SELL", "HOLD", "NONE", "STOP"]


class ProjectXContractOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    tick_size: float | None = None
    tick_value: float | None = None
    active_contract: bool | None = None
    symbol_id: str | None = None


class ProjectXMarketCandleOut(BaseModel):
    id: int | None = None
    contract_id: str
    symbol: str | None = None
    live: bool
    unit: TimeframeUnit
    unit_number: int
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_partial: bool = False
    fetched_at: datetime | None = None


class BotConfigBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    account_id: int = Field(gt=0)
    contract_id: str = Field(min_length=1, max_length=120)
    symbol: str | None = Field(default=None, max_length=40)
    execution_mode: BotExecutionMode = "dry_run"
    strategy_type: Literal["sma_cross"] = "sma_cross"
    timeframe_unit: TimeframeUnit = "minute"
    timeframe_unit_number: int = Field(default=5, gt=0, le=1440)
    lookback_bars: int = Field(default=200, ge=25, le=20000)
    fast_period: int = Field(default=9, gt=0, le=500)
    slow_period: int = Field(default=21, gt=1, le=1000)
    order_size: float = Field(default=1, gt=0)
    max_contracts: float = Field(default=1, gt=0)
    max_daily_loss: float = Field(default=250, ge=0)
    max_trades_per_day: int = Field(default=3, ge=0)
    max_open_position: float = Field(default=1, gt=0)
    allowed_contracts: list[str] = Field(default_factory=list)
    trading_start_time: str = "09:30"
    trading_end_time: str = "15:45"
    cooldown_seconds: int = Field(default=300, ge=0)
    max_data_staleness_seconds: int = Field(default=600, gt=0)
    allow_market_depth: bool = False


class BotConfigCreateIn(BotConfigBase):
    enabled: bool = False


class BotConfigUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    account_id: int | None = Field(default=None, gt=0)
    contract_id: str | None = Field(default=None, min_length=1, max_length=120)
    symbol: str | None = Field(default=None, max_length=40)
    enabled: bool | None = None
    execution_mode: BotExecutionMode | None = None
    timeframe_unit: TimeframeUnit | None = None
    timeframe_unit_number: int | None = Field(default=None, gt=0, le=1440)
    lookback_bars: int | None = Field(default=None, ge=25, le=20000)
    fast_period: int | None = Field(default=None, gt=0, le=500)
    slow_period: int | None = Field(default=None, gt=1, le=1000)
    order_size: float | None = Field(default=None, gt=0)
    max_contracts: float | None = Field(default=None, gt=0)
    max_daily_loss: float | None = Field(default=None, ge=0)
    max_trades_per_day: int | None = Field(default=None, ge=0)
    max_open_position: float | None = Field(default=None, gt=0)
    allowed_contracts: list[str] | None = None
    trading_start_time: str | None = None
    trading_end_time: str | None = None
    cooldown_seconds: int | None = Field(default=None, ge=0)
    max_data_staleness_seconds: int | None = Field(default=None, gt=0)
    allow_market_depth: bool | None = None


class BotConfigOut(BotConfigBase):
    id: int
    enabled: bool
    provider: str
    created_at: datetime
    updated_at: datetime


class BotConfigListOut(BaseModel):
    items: list[BotConfigOut]
    total: int


class BotRunOut(BaseModel):
    id: int
    bot_config_id: int
    account_id: int
    status: BotRunStatus
    dry_run: bool
    started_at: datetime
    stopped_at: datetime | None = None
    stop_reason: str | None = None
    last_heartbeat_at: datetime | None = None


class BotDecisionOut(BaseModel):
    id: int
    bot_config_id: int
    bot_run_id: int | None = None
    account_id: int
    contract_id: str
    symbol: str | None = None
    decision_type: str
    action: BotAction
    reason: str
    candle_timestamp: datetime | None = None
    price: float | None = None
    quantity: float | None = None
    created_at: datetime


class BotOrderAttemptOut(BaseModel):
    id: int
    bot_config_id: int
    bot_run_id: int | None = None
    bot_decision_id: int | None = None
    account_id: int
    contract_id: str
    side: Literal["BUY", "SELL"]
    order_type: str
    size: float
    status: str
    provider_order_id: str | None = None
    rejection_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class BotRiskEventOut(BaseModel):
    id: int
    bot_config_id: int
    bot_run_id: int | None = None
    account_id: int
    severity: str
    code: str
    message: str
    created_at: datetime


class BotStartIn(BaseModel):
    dry_run: bool | None = None
    confirm_live_order_routing: bool = False


class BotEvaluationOut(BaseModel):
    config: BotConfigOut
    run: BotRunOut | None = None
    decision: BotDecisionOut
    order_attempt: BotOrderAttemptOut | None = None
    risk_events: list[BotRiskEventOut]
    candles: list[ProjectXMarketCandleOut]


class BotActivityOut(BaseModel):
    config: BotConfigOut
    runs: list[BotRunOut]
    decisions: list[BotDecisionOut]
    order_attempts: list[BotOrderAttemptOut]
    risk_events: list[BotRiskEventOut]

