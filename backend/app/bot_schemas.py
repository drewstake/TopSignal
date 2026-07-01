from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .trade_plan_schemas import TradeEvaluationResultOut


TimeframeUnit = Literal["second", "minute", "hour", "day", "week", "month"]
BotExecutionMode = Literal["dry_run", "live"]
BotRunStatus = Literal["running", "stopped", "blocked", "error"]
BotAction = Literal["BUY", "SELL", "HOLD", "NONE", "STOP", "RISK_REJECT"]
BotMarketTrend = Literal["bullish", "bearish", "neutral"]
BotVolatilityState = Literal["low", "normal", "elevated", "extreme"]
BotVolumeState = Literal["low", "normal", "elevated"]
BotStrategyType = Literal[
    "topbot_adaptive",
    "sma_cross",
    "support_resistance",
    "donchian_breakout",
    "liquidity_sweep_retest",
    "opening_rvol_breakout",
    "bollinger_rsi_reversal",
    "macd_support_resistance",
    "delayed_orb_confirmation",
    "ema_trend_pullback",
    "ema_scalping",
    "fvg_sweep_mss",
    "orb_fibonacci_pullback",
    "pullback_trap_reversal",
    "supertrend_pivot",
    "bollinger_mean_reversion",
    "fisher_transform_mean_reversion",
    "vwap_atr_mean_reversion",
    "atr_adjusted_relative_strength",
    "relative_strength_spy",
    "vwap_gap_retrace",
]


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
    strategy_type: BotStrategyType = "sma_cross"
    strategy_params: dict[str, Any] = Field(default_factory=dict)
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
    strategy_type: BotStrategyType | None = None
    strategy_params: dict[str, Any] | None = None
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
    raw_state: dict[str, Any] | None = None


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
    raw_payload: dict[str, Any] | None = None
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
    continuous: bool = True
    poll_interval_seconds: int | None = Field(default=None, ge=15, le=3600)
    stop_at_session_end: bool = True


class BotBacktestIn(BaseModel):
    start: datetime | None = None
    end: datetime | None = None
    limit: int = Field(default=100000, ge=100, le=200000)


class BotBacktestTradeOut(BaseModel):
    id: int
    side: Literal["BUY", "SELL"]
    quantity: float
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    exit_reason: str
    gross_pnl: float
    net_pnl: float
    fees: float
    points: float
    signal_reason: str
    duration_minutes: float = 0.0
    bars_held: int = 0
    max_favorable_points: float = 0.0
    max_adverse_points: float = 0.0


class BotBacktestOut(BaseModel):
    bot_config_id: int
    bot_name: str
    strategy_type: BotStrategyType
    contract_id: str
    symbol: str | None = None
    start: datetime
    end: datetime
    generated_at: datetime
    candles_processed: int
    signals_evaluated: int
    point_value: float
    assumptions: dict[str, Any]
    summary: dict[str, Any]
    analysis: dict[str, Any] = Field(default_factory=dict)
    daily_pnl: list[dict[str, Any]]
    trades: list[BotBacktestTradeOut]


class BotBacktestJobOut(BaseModel):
    job_id: str
    bot_config_id: int
    status: Literal["queued", "running", "completed", "failed"]
    progress: float = Field(ge=0, le=100)
    stage: str
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    error: str | None = None
    result: BotBacktestOut | None = None


class BotMarketAnalysisOut(BaseModel):
    current_price: float | None = None
    previous_close: float | None = None
    price_change: float | None = None
    price_change_percent: float | None = None
    trend: BotMarketTrend
    trend_strength: int = Field(ge=0, le=100)
    volatility_state: BotVolatilityState
    volume_state: BotVolumeState
    support_levels: list[float]
    resistance_levels: list[float]
    nearest_support: float | None = None
    nearest_resistance: float | None = None
    bullish_probability: int = Field(ge=0, le=100)
    bearish_probability: int = Field(ge=0, le=100)
    sideways_probability: int = Field(ge=0, le=100)
    expected_move: float | None = None
    expected_move_percent: float | None = None
    invalidation_level: float | None = None
    summary: str
    reasoning: list[str]
    risk_notes: list[str]
    indicators: dict[str, Any] | None = None
    candle_timestamp: str | None = None
    generated_at: str | None = None
    trade_evaluation: TradeEvaluationResultOut | None = None


class BotEvaluationOut(BaseModel):
    config: BotConfigOut
    run: BotRunOut | None = None
    decision: BotDecisionOut
    order_attempt: BotOrderAttemptOut | None = None
    risk_events: list[BotRiskEventOut]
    analysis: BotMarketAnalysisOut
    candles: list[ProjectXMarketCandleOut]


class BotActivityOut(BaseModel):
    config: BotConfigOut
    runs: list[BotRunOut]
    decisions: list[BotDecisionOut]
    order_attempts: list[BotOrderAttemptOut]
    risk_events: list[BotRiskEventOut]
