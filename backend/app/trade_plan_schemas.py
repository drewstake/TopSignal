from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


TradeDirection = Literal["long", "short"]
TrendDirection = Literal["bullish", "bearish", "neutral", "unknown"]
TimeOfDay = Literal["premarket", "open", "ny_am", "lunch", "power_hour", "close", "overnight"]
MarketRegime = Literal["trend", "range", "chop", "breakout", "reversal", "unknown"]
NewsRisk = Literal["low", "medium", "high", "unknown"]
TradeDecision = Literal["take", "wait", "avoid"]
TradeConfidence = Literal["low", "medium", "high"]
TradeGrade = Literal["A", "B", "C", "D", "F"]


class TradePlanIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    symbol: str = Field(min_length=1, max_length=80)
    direction: TradeDirection
    entry_price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    quantity: float = Field(default=1, gt=0)
    timestamp: datetime
    account_balance: float | None = Field(default=None, gt=0)
    current_day_pnl: float | None = None
    max_daily_loss: float | None = Field(default=None, ge=0)
    trailing_drawdown: float | None = Field(default=None, ge=0)
    tick_size: float | None = Field(default=None, gt=0)
    tick_value: float | None = Field(default=None, gt=0)
    point_value: float | None = Field(default=None, gt=0)


class MarketContextIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    current_price: float = Field(gt=0)
    high_of_day: float | None = Field(default=None, gt=0)
    low_of_day: float | None = Field(default=None, gt=0)
    previous_day_high: float | None = Field(default=None, gt=0)
    previous_day_low: float | None = Field(default=None, gt=0)
    previous_close: float | None = Field(default=None, gt=0)
    open_price: float | None = Field(default=None, gt=0)
    vwap: float | None = Field(default=None, gt=0)
    anchored_vwap: float | None = Field(default=None, gt=0)
    volume_profile_poc: float | None = Field(default=None, gt=0)
    value_area_high: float | None = Field(default=None, gt=0)
    value_area_low: float | None = Field(default=None, gt=0)
    ema21_5m: float | None = Field(default=None, gt=0)
    ema21_15m: float | None = Field(default=None, gt=0)
    ema21_1h: float | None = Field(default=None, gt=0)
    ema21_4h: float | None = Field(default=None, gt=0)
    ma200_5m: float | None = Field(default=None, gt=0)
    ma200_15m: float | None = Field(default=None, gt=0)
    ma200_1h: float | None = Field(default=None, gt=0)
    ma200_4h: float | None = Field(default=None, gt=0)
    trend5m: TrendDirection = "unknown"
    trend15m: TrendDirection = "unknown"
    trend1h: TrendDirection = "unknown"
    trend4h: TrendDirection = "unknown"
    atr1m: float | None = Field(default=None, gt=0)
    atr5m: float | None = Field(default=None, gt=0)
    atr15m: float | None = Field(default=None, gt=0)
    atr1h: float | None = Field(default=None, gt=0)
    average_daily_range: float | None = Field(default=None, gt=0)
    current_day_range: float | None = Field(default=None, ge=0)
    current_volume: float | None = Field(default=None, ge=0)
    average_volume_at_time: float | None = Field(default=None, ge=0)
    relative_volume: float | None = Field(default=None, ge=0)
    cumulative_delta: float | None = None
    time_of_day: TimeOfDay = "overnight"
    market_regime: MarketRegime = "unknown"
    news_risk: NewsRisk = "unknown"
    es_trend: TrendDirection | None = None
    nq_trend: TrendDirection | None = None
    vix_trend: TrendDirection | None = None
    ten_year_yield_trend: TrendDirection | None = None
    nvda_trend: TrendDirection | None = None
    smh_trend: TrendDirection | None = None


class TradePlanEvaluationIn(BaseModel):
    trade_plan: TradePlanIn
    market_context: MarketContextIn


class TradePlanFeaturesOut(BaseModel):
    geometry_valid: bool
    geometry_issues: list[str]
    tick_size: float | None
    tick_value: float | None
    point_value: float | None
    normalized_entry_price: float
    normalized_stop_loss: float
    normalized_take_profit: float
    prices_normalized_to_tick: bool
    risk_points: float
    reward_points: float
    risk_ticks: float | None
    reward_ticks: float | None
    risk_reward_ratio: float | None
    r_multiple: float | None
    breakeven_win_rate: float | None
    estimated_dollar_risk: float | None
    estimated_dollar_reward: float | None
    is_long: bool
    is_short: bool
    price_above_vwap: bool | None
    price_below_vwap: bool | None
    entry_distance_from_vwap_points: float | None
    entry_distance_from_vwap_atr: float | None
    vwap_supports_direction: bool | None
    distance_from_high_of_day: float | None
    distance_from_low_of_day: float | None
    distance_from_previous_day_high: float | None
    distance_from_previous_day_low: float | None
    entry_near_high_of_day: bool
    entry_near_low_of_day: bool
    take_profit_blocked_by_high_of_day: bool
    take_profit_blocked_by_low_of_day: bool
    stop_atr_multiple: float | None
    target_atr_multiple: float | None
    is_stop_too_tight: bool
    is_stop_too_wide: bool
    is_target_realistic: bool
    trend_alignment_score: int = Field(ge=0, le=100)
    aligned_timeframes: int
    conflicting_timeframes: int
    higher_timeframe_conflict: bool
    stop_behind_structure: bool
    entry_chasing: bool
    has_room_to_target: bool
    bad_location: bool
    max_loss_risk_percent: float | None
    account_risk_percent: float | None
    projected_day_pnl: float | None
    daily_loss_remaining_before_trade: float | None
    daily_loss_remaining_after_trade: float | None
    daily_loss_danger: bool | None
    drawdown_risk_percent: float | None
    drawdown_danger: bool | None
    should_reduce_size: bool | None


class TradeScorePenaltyOut(BaseModel):
    code: str
    category: str | None = None
    points_deducted: int = Field(ge=0)
    reason: str


class TradeScoreCapOut(BaseModel):
    code: str
    maximum: int = Field(ge=0, le=100)
    reason: str
    score_before: int = Field(ge=0, le=100)
    score_after: int = Field(ge=0, le=100)
    applied: bool


class TradeEvaluationDimensionOut(BaseModel):
    awarded_points: int = Field(ge=0)
    maximum_points: int = Field(gt=0)
    score_percent: int = Field(ge=0, le=100)


class TradeEvaluationResultOut(BaseModel):
    scoring_model_version: str
    total_score: int = Field(ge=0, le=100)
    score: int = Field(ge=0, le=100)
    score_before_caps: int = Field(ge=0, le=100)
    grade: TradeGrade
    decision: TradeDecision
    confidence: TradeConfidence
    summary: str
    reasons: list[str]
    warnings: list[str]
    positives: list[str]
    suggested_adjustments: list[str]
    features: TradePlanFeaturesOut
    category_scores: dict[str, int]
    category_maximums: dict[str, int]
    category_awarded_points: dict[str, int]
    penalties: list[TradeScorePenaltyOut]
    caps: list[TradeScoreCapOut]
    missing_inputs: list[str]
    top_positive_drivers: list[str]
    top_negative_drivers: list[str]
    evaluation_dimensions: dict[str, TradeEvaluationDimensionOut]
    data_confidence: TradeConfidence
    data_confidence_score: int = Field(ge=0, le=100)
