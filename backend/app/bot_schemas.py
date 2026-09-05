from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from .trade_plan_schemas import TradeEvaluationResultOut
from .trading_costs import MNQ_FEES_PER_CONTRACT_PER_SIDE


TimeframeUnit = Literal["second", "minute", "hour", "day", "week", "month"]
BotBacktestInstrument = Literal["MNQ", "MES", "NQ", "ES"]
MAX_BOT_CONTRACT_QUANTITY = 10_000
BotExecutionMode = Literal["dry_run", "live"]
BotRunStatus = Literal["running", "stopped", "blocked", "error"]
BotEvaluationStatus = Literal[
    "evaluated",
    "held",
    "risk_blocked",
    "duplicate_skipped",
    "dry_run_attempt",
    "submitted",
    "error",
]
BotAction = Literal["BUY", "SELL", "HOLD", "NONE", "STOP"]
BotMarketTrend = Literal["bullish", "bearish", "neutral"]
BotVolatilityState = Literal["low", "normal", "elevated", "extreme"]
BotVolumeState = Literal["low", "normal", "elevated"]
BotMarketRegime = Literal["trend", "range", "chop", "volatile", "quiet", "unknown"]
BotDataQualityStatus = Literal["good", "limited", "insufficient", "stale"]
BotVwapLocation = Literal["above", "below", "at", "unavailable"]
BotMtfAlignment = Literal["bullish", "bearish", "mixed", "neutral", "unavailable"]
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
    order_size: float = Field(default=1, gt=0, le=MAX_BOT_CONTRACT_QUANTITY, allow_inf_nan=False)
    max_contracts: float = Field(default=1, gt=0, le=MAX_BOT_CONTRACT_QUANTITY, allow_inf_nan=False)
    max_daily_loss: float = Field(default=250, ge=0, allow_inf_nan=False)
    max_trades_per_day: int = Field(default=3, ge=0)
    max_open_position: float = Field(default=1, gt=0, le=MAX_BOT_CONTRACT_QUANTITY, allow_inf_nan=False)
    allowed_contracts: list[str] = Field(default_factory=list)
    trading_start_time: str = "09:30"
    trading_end_time: str = "15:45"
    cooldown_seconds: int = Field(default=300, ge=0)
    max_data_staleness_seconds: int = Field(default=600, gt=0)
    allow_market_depth: bool = False

    @field_validator("order_size", "max_contracts", "max_open_position")
    @classmethod
    def validate_whole_contract_quantity(cls, value: float) -> float:
        if abs(value - round(value)) > 1e-9:
            raise ValueError("ProjectX futures contract quantities must be whole numbers")
        return value


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
    order_size: float | None = Field(
        default=None,
        gt=0,
        le=MAX_BOT_CONTRACT_QUANTITY,
        allow_inf_nan=False,
    )
    max_contracts: float | None = Field(
        default=None,
        gt=0,
        le=MAX_BOT_CONTRACT_QUANTITY,
        allow_inf_nan=False,
    )
    max_daily_loss: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_trades_per_day: int | None = Field(default=None, ge=0)
    max_open_position: float | None = Field(
        default=None,
        gt=0,
        le=MAX_BOT_CONTRACT_QUANTITY,
        allow_inf_nan=False,
    )
    allowed_contracts: list[str] | None = None
    trading_start_time: str | None = None
    trading_end_time: str | None = None
    cooldown_seconds: int | None = Field(default=None, ge=0)
    max_data_staleness_seconds: int | None = Field(default=None, gt=0)
    allow_market_depth: bool | None = None

    @field_validator("order_size", "max_contracts", "max_open_position")
    @classmethod
    def validate_whole_contract_quantity(cls, value: float | None) -> float | None:
        if value is not None and abs(value - round(value)) > 1e-9:
            raise ValueError("ProjectX futures contract quantities must be whole numbers")
        return value


class BotConfigOut(BotConfigBase):
    id: int
    enabled: bool
    provider: str
    created_at: datetime
    updated_at: datetime


class BotConfigListOut(BaseModel):
    items: list[BotConfigOut]
    total: int
    warnings: list[str] = Field(default_factory=list)


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
    last_error: str | None = None
    last_evaluated_at: datetime | None = None


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
    correlation_id: str | None = None
    idempotency_key: str | None = None
    created_at: datetime


class BotOrderAttemptOut(BaseModel):
    id: int
    bot_config_id: int | None = None
    bot_run_id: int | None = None
    bot_decision_id: int | None = None
    account_id: int
    contract_id: str
    side: Literal["BUY", "SELL"]
    order_type: str
    size: float
    status: str
    execution_mode: BotExecutionMode = "dry_run"
    correlation_id: str | None = None
    idempotency_key: str | None = None
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


class BotTradeLevelsOut(BaseModel):
    """Authoritative price levels emitted by an actionable strategy signal."""

    entry: float | None = None
    stop: float | None = None
    target: float | None = None


class TopBotStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: StrictBool = True
    confirm_live_order_routing: StrictBool = False

    @model_validator(mode="after")
    def require_live_confirmation(self):
        if not self.dry_run and not self.confirm_live_order_routing:
            raise ValueError("Live Run requires explicit order-routing confirmation")
        return self


class BotStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: StrictBool | None = None
    confirm_live_order_routing: StrictBool = False
    continuous: bool = False
    poll_interval_seconds: int | None = Field(default=None, ge=1, le=300)
    stop_at_session_end: bool = False


class BotEmergencyFlattenIn(BaseModel):
    """Explicitly authorize an account-wide broker flatten operation."""

    model_config = ConfigDict(extra="forbid")

    confirm_broker_flatten: Literal[True]

    @field_validator("confirm_broker_flatten", mode="before")
    @classmethod
    def require_json_boolean_true(cls, value):
        # Pydantic treats integer 1 as equal to Literal[True] unless rejected
        # before coercion.  A broker kill switch requires the literal JSON
        # boolean, not a truthy substitute.
        if value is not True:
            raise ValueError("confirm_broker_flatten must be literal true")
        return value


class BotEmergencyFlattenRiskBlockOut(BaseModel):
    code: str
    message: str
    severity: str


class BotEmergencyFlattenOut(BaseModel):
    run: BotRunOut
    confirmed_flat: bool
    status: Literal["confirmed_account_flat", "unconfirmed"]
    risk_block: BotEmergencyFlattenRiskBlockOut | None = None
    audit: dict[str, Any]


class AccountEmergencyFlattenOut(BaseModel):
    """Account-level result that remains valid without any bot configuration."""

    account_id: int
    audit_id: int
    confirmed_flat: bool
    status: Literal["confirmed_account_flat", "unconfirmed"]
    risk_block: BotEmergencyFlattenRiskBlockOut | None = None
    audit: dict[str, Any]
    disabled_bot_config_ids: list[int]
    stopped_bot_run_ids: list[int]


class BotScenarioWeightsOut(BaseModel):
    bullish: int = Field(ge=0, le=100)
    bearish: int = Field(ge=0, le=100)
    sideways: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_total(self):
        if self.bullish + self.bearish + self.sideways != 100:
            raise ValueError("scenario weights must total 100")
        return self


class BotAnalysisTimeframeOut(BaseModel):
    unit: TimeframeUnit
    unit_number: int = Field(gt=0)
    label: str


class BotAnalysisGapOut(BaseModel):
    after_timestamp: str
    before_timestamp: str
    missing_bars: int = Field(gt=0)


class BotAnalysisProvenanceOut(BaseModel):
    closed_candle_count: int = Field(ge=0)
    partial_candle_count: int = Field(ge=0)
    latest_candle_timestamp: str | None = None
    data_age_seconds: int | None = Field(default=None, ge=0)
    is_stale: bool
    stale_after_seconds: int = Field(gt=0)
    timeframe: BotAnalysisTimeframeOut
    detected_gaps: list[BotAnalysisGapOut]
    gap_count: int = Field(ge=0)
    configured_contract_id: str | None = None
    resolved_contract_id: str | None = None
    resolved_symbol: str | None = None
    contract_rollover: bool = False
    minimum_feature_bars: int = Field(default=10, gt=0)
    minimum_sufficient_bars: int = Field(default=25, gt=0)

    @model_validator(mode="after")
    def validate_gap_count(self):
        if self.gap_count != len(self.detected_gaps):
            raise ValueError("gap_count must match detected_gaps")
        return self


class BotAnalysisDataQualityOut(BaseModel):
    status: BotDataQualityStatus
    confidence: int = Field(ge=0, le=100)
    missing_inputs: list[str]
    warnings: list[str]


class BotAnalysisTrendFeatureOut(BaseModel):
    direction: BotMarketTrend
    strength: int = Field(ge=0, le=100)
    fast_ema: float | None = None
    slow_ema: float | None = None
    slow_ema_slope: float | None = None


class BotAnalysisVolatilityFeatureOut(BaseModel):
    atr: float | None = None
    atr_percent: float | None = None
    percentile: float | None = Field(default=None, ge=0, le=100)
    state: BotVolatilityState


class BotAnalysisVolumeFeatureOut(BaseModel):
    relative_volume: float | None = Field(default=None, ge=0)
    state: BotVolumeState


class BotAnalysisVwapFeatureOut(BaseModel):
    value: float | None = None
    location: BotVwapLocation


class BotAnalysisTimeframeTrendOut(BaseModel):
    timeframe: str
    direction: BotMarketTrend


class BotAnalysisMtfFeatureOut(BaseModel):
    status: BotMtfAlignment
    aligned_timeframes: int = Field(ge=0)
    conflicting_timeframes: int = Field(ge=0)
    timeframes: list[BotAnalysisTimeframeTrendOut]


class BotAnalysisNearbyLevelsOut(BaseModel):
    support: float | None = None
    resistance: float | None = None


class BotAnalysisFeaturesOut(BaseModel):
    trend: BotAnalysisTrendFeatureOut
    volatility: BotAnalysisVolatilityFeatureOut
    volume: BotAnalysisVolumeFeatureOut
    vwap: BotAnalysisVwapFeatureOut
    multi_timeframe_alignment: BotAnalysisMtfFeatureOut
    nearby_levels: BotAnalysisNearbyLevelsOut


class BotAnalysisScoreDriversOut(BaseModel):
    bullish: list[str]
    bearish: list[str]
    neutral: list[str]


class BotAnalysisDimensionOut(BaseModel):
    score: int = Field(ge=0, le=100)
    label: str
    drivers: list[str]


class BotAnalysisMarketBiasOut(BaseModel):
    direction: BotMarketTrend
    strength: int = Field(ge=0, le=100)
    drivers: list[str]


class BotAnalysisExecutionRiskOut(BaseModel):
    risk_score: int = Field(ge=0, le=100)
    label: str
    drivers: list[str]


class BotMarketAnalysisOut(BaseModel):
    analysis_version: Literal["market_analysis_v2"]
    probability_method: Literal["heuristic_scenario_weight"]
    scenario_weights: BotScenarioWeightsOut
    provenance: BotAnalysisProvenanceOut
    data_quality: BotAnalysisDataQualityOut
    market_regime: BotMarketRegime
    features: BotAnalysisFeaturesOut
    score_drivers: BotAnalysisScoreDriversOut
    setup_quality: BotAnalysisDimensionOut
    market_bias: BotAnalysisMarketBiasOut
    execution_risk: BotAnalysisExecutionRiskOut
    data_confidence: BotAnalysisDimensionOut
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
    candle_timestamp: str | None = None
    generated_at: str | None = None
    trade_evaluation: TradeEvaluationResultOut | None = None

    @model_validator(mode="after")
    def validate_legacy_weight_aliases(self):
        aliases = (
            self.bullish_probability,
            self.bearish_probability,
            self.sideways_probability,
        )
        canonical = (
            self.scenario_weights.bullish,
            self.scenario_weights.bearish,
            self.scenario_weights.sideways,
        )
        if aliases != canonical:
            raise ValueError("legacy probability aliases must match scenario_weights")
        return self


class BotEvaluationOut(BaseModel):
    status: BotEvaluationStatus = "evaluated"
    correlation_id: str | None = None
    idempotency_key: str | None = None
    duplicate_of_order_attempt_id: int | None = None
    config: BotConfigOut
    run: BotRunOut | None = None
    decision: BotDecisionOut
    order_attempt: BotOrderAttemptOut | None = None
    risk_events: list[BotRiskEventOut]
    trade_levels: BotTradeLevelsOut | None = None
    analysis: BotMarketAnalysisOut
    candles: list[ProjectXMarketCandleOut]


class BotActivityOut(BaseModel):
    config: BotConfigOut
    runs: list[BotRunOut]
    decisions: list[BotDecisionOut]
    order_attempts: list[BotOrderAttemptOut]
    risk_events: list[BotRiskEventOut]


class BotBacktestIn(BaseModel):
    start: datetime | None = None
    end: datetime | None = None
    strategy_type: BotStrategyType | None = None
    instrument: BotBacktestInstrument | None = None
    starting_balance: float = Field(default=50_000, gt=0, le=1_000_000_000)
    commission_per_contract: float = Field(
        default=MNQ_FEES_PER_CONTRACT_PER_SIDE, ge=0, le=10_000,
        description="Total transaction fees per contract per side, charged on entry and exit; excludes slippage. Defaults to TopstepX MNQ.",
    )
    slippage_ticks: float = Field(default=0, ge=0, le=1_000)
    force_close_at_end: bool = True

    @model_validator(mode="after")
    def validate_optional_range(self):
        if (self.start is None) != (self.end is None):
            raise ValueError("backtest start and end must be provided together")
        if self.start is not None and self.end is not None:
            start = (
                self.start.replace(tzinfo=timezone.utc)
                if self.start.tzinfo is None
                else self.start.astimezone(timezone.utc)
            )
            end = (
                self.end.replace(tzinfo=timezone.utc)
                if self.end.tzinfo is None
                else self.end.astimezone(timezone.utc)
            )
            if end <= start:
                raise ValueError("backtest end must be after start")
        return self


class BotBacktestRangeOut(BaseModel):
    contract_id: str
    symbol: str | None = None
    timeframe_unit: TimeframeUnit
    timeframe_unit_number: int = Field(gt=0)
    start: datetime
    end: datetime
    bar_count: int = Field(ge=1)


class BotBacktestAssumptionsOut(BaseModel):
    fill_model: str
    signal_timing: str
    strategy_replay: str = "single_strategy"
    source_synchronization: str = "not_recorded"
    synchronized_stream_count: int = Field(default=1, ge=1)
    event_order: str
    same_bar_exit_rule: str
    bracket_rule: str
    gap_rule: str
    roll_gap_rule: str = "not_recorded"
    final_position_handling: str
    position_rule: str
    session_rule: str
    commission_rule: str
    slippage_rule: str
    pnl_rule: str
    metric_basis: str
    market_data: str
    live_order_routing: str
    timezone: str
    commission_per_contract: float
    slippage_ticks: float
    tick_size: float
    tick_value: float
    engine_version: str
    configured_execution_mode_was_ignored: str


class BotBacktestBreakdownOut(BaseModel):
    trade_count: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    gross_pnl: float
    net_pnl: float
    profit_factor: float | None = None
    expectancy: float
    average_win: float
    average_loss: float
    payoff_ratio: float | None = None


class BotBacktestMetricsOut(BotBacktestBreakdownOut):
    total_commission: float
    max_drawdown_dollars: float
    max_drawdown_percent: float
    average_mae: float
    average_mfe: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    exposure_percent: float
    long: BotBacktestBreakdownOut
    short: BotBacktestBreakdownOut


class BotBacktestEvaluationWindowOut(BaseModel):
    start: datetime
    end: datetime
    bar_count: int = Field(ge=1)
    metrics: BotBacktestBreakdownOut


class BotBacktestEvaluationSplitOut(BaseModel):
    method: Literal["chronological_80_20_fixed_parameters"]
    label: str
    validation_status: Literal["diagnostic_only"]
    split_timestamp: datetime
    in_sample: BotBacktestEvaluationWindowOut
    holdout: BotBacktestEvaluationWindowOut
    notes: list[str]


class BotBacktestEquityPointOut(BaseModel):
    timestamp: datetime
    equity: float
    realized_pnl: float
    unrealized_pnl: float


class BotBacktestDrawdownPointOut(BaseModel):
    timestamp: datetime
    equity: float
    drawdown_dollars: float
    drawdown_percent: float


class BotBacktestPeriodOut(BaseModel):
    period: str
    gross_pnl: float
    net_pnl: float
    commission: float
    trade_count: int
    wins: int
    losses: int


class BotBacktestTradeOut(BaseModel):
    id: int
    side: Literal["long", "short"]
    quantity: float
    signal_timestamp: datetime
    entry_timestamp: datetime
    entry_price: float
    exit_timestamp: datetime
    exit_price: float
    exit_reason: str
    gross_pnl: float
    commission: float
    net_pnl: float
    mae: float
    mfe: float
    bars_held: int


class BotBacktestGapYearOut(BaseModel):
    year: int
    gap_count: int
    missing_bar_count: int
    in_session_gap_count: int


class BotBacktestGapExampleOut(BaseModel):
    start: datetime
    end: datetime
    missing_bar_count: int
    in_session_missing_bar_count: int


class BotBacktestGapsOut(BaseModel):
    gap_count: int
    missing_bar_count: int
    in_session_gap_count: int
    in_session_missing_bar_count: int
    by_year: list[BotBacktestGapYearOut]
    largest_gaps: list[BotBacktestGapExampleOut]


class BotBacktestDataQualityOut(BaseModel):
    available_start: datetime
    first_evaluation: datetime
    warmup_required: int
    warmup_available: int
    gaps: BotBacktestGapsOut | None = None


class BotBacktestOut(BaseModel):
    id: int
    bot_config_id: int | None = None
    engine_version: str
    input_fingerprint: str
    created_at: datetime
    range: BotBacktestRangeOut
    config_snapshot: dict[str, Any]
    assumptions: BotBacktestAssumptionsOut
    metrics: BotBacktestMetricsOut
    evaluation_split: BotBacktestEvaluationSplitOut | None = None
    equity_curve: list[BotBacktestEquityPointOut]
    drawdown_series: list[BotBacktestDrawdownPointOut]
    daily_results: list[BotBacktestPeriodOut]
    monthly_results: list[BotBacktestPeriodOut]
    trades: list[BotBacktestTradeOut]
    warnings: list[str]
    notes: list[str] = Field(default_factory=list)
    data_quality: BotBacktestDataQualityOut | None = None
