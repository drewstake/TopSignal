from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import func, or_
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from ..models import (
    Account,
    BotConfig,
    BotDecision,
    BotOrderAttempt,
    BotRiskEvent,
    BotRun,
    PositionLifecycle,
    ProjectXMarketCandle,
    ProjectXTradeEvent,
)
from .instruments import load_instrument_specs, normalize_symbol_key
from .projectx_accounts import ACCOUNT_STATE_ACTIVE, get_projectx_account_row
from .projectx_client import ProjectXClient
from .trade_plan_evaluator import TradePlan, TradePlanEvaluator, build_market_context_from_ohlcv
from .trading_day import TRADING_TZ, trading_day_bounds_utc, trading_day_date

_PROJECTX_UNIT_BY_NAME = {
    "second": 1,
    "minute": 2,
    "hour": 3,
    "day": 4,
    "week": 5,
    "month": 6,
}
_UNIT_SECONDS_BY_NAME = {
    "second": 1,
    "minute": 60,
    "hour": 60 * 60,
    "day": 24 * 60 * 60,
    "week": 7 * 24 * 60 * 60,
    "month": 31 * 24 * 60 * 60,
}
_MARKET_CANDLE_TAIL_REVALIDATION_BARS = 3
_MARKET_CANDLE_TAIL_REVALIDATION_TTL = timedelta(seconds=15)
_ORDER_TYPE_MARKET = 2
_SIDE_BY_ACTION = {"BUY": 0, "SELL": 1}
_LIVE_ACCOUNT_PATTERN = re.compile(r"\b(LIVE|LFA|BROKERAGE|FUNDED\s+LIVE)\b", re.IGNORECASE)
_STRATEGY_SMA_CROSS = "sma_cross"
_STRATEGY_SUPPORT_RESISTANCE = "support_resistance"
_STRATEGY_LIQUIDITY_SWEEP_RETEST = "liquidity_sweep_retest"
_STRATEGY_DONCHIAN_BREAKOUT = "donchian_breakout"
_STRATEGY_OPENING_RVOL_BREAKOUT = "opening_rvol_breakout"
_STRATEGY_BOLLINGER_RSI_REVERSAL = "bollinger_rsi_reversal"
_STRATEGY_BOLLINGER_MEAN_REVERSION = "bollinger_mean_reversion"
_STRATEGY_MACD_SUPPORT_RESISTANCE = "macd_support_resistance"
_STRATEGY_DELAYED_ORB_CONFIRMATION = "delayed_orb_confirmation"
_STRATEGY_ORB_FIBONACCI_PULLBACK = "orb_fibonacci_pullback"
_STRATEGY_SUPERTREND_PIVOT = "supertrend_pivot"
_STRATEGY_EMA_TREND_PULLBACK = "ema_trend_pullback"
_STRATEGY_EMA_SCALPING = "ema_scalping"
_STRATEGY_VWAP_ATR_MEAN_REVERSION = "vwap_atr_mean_reversion"
_STRATEGY_VWAP_GAP_RETRACE = "vwap_gap_retrace"
_STRATEGY_FISHER_MEAN_REVERSION = "fisher_transform_mean_reversion"
_STRATEGY_ATR_ADJUSTED_RELATIVE_STRENGTH = "atr_adjusted_relative_strength"
_STRATEGY_RELATIVE_STRENGTH_SPY = "relative_strength_spy"
_STRATEGY_PULLBACK_TRAP_REVERSAL = "pullback_trap_reversal"
_STRATEGY_FVG_SWEEP_MSS = "fvg_sweep_mss"
_SUPPORTED_STRATEGY_TYPES = {
    _STRATEGY_SMA_CROSS,
    _STRATEGY_SUPPORT_RESISTANCE,
    _STRATEGY_LIQUIDITY_SWEEP_RETEST,
    _STRATEGY_DONCHIAN_BREAKOUT,
    _STRATEGY_OPENING_RVOL_BREAKOUT,
    _STRATEGY_BOLLINGER_RSI_REVERSAL,
    _STRATEGY_BOLLINGER_MEAN_REVERSION,
    _STRATEGY_MACD_SUPPORT_RESISTANCE,
    _STRATEGY_DELAYED_ORB_CONFIRMATION,
    _STRATEGY_ORB_FIBONACCI_PULLBACK,
    _STRATEGY_SUPERTREND_PIVOT,
    _STRATEGY_EMA_TREND_PULLBACK,
    _STRATEGY_EMA_SCALPING,
    _STRATEGY_VWAP_ATR_MEAN_REVERSION,
    _STRATEGY_VWAP_GAP_RETRACE,
    _STRATEGY_FISHER_MEAN_REVERSION,
    _STRATEGY_ATR_ADJUSTED_RELATIVE_STRENGTH,
    _STRATEGY_RELATIVE_STRENGTH_SPY,
    _STRATEGY_PULLBACK_TRAP_REVERSAL,
    _STRATEGY_FVG_SWEEP_MSS,
}
_LEVEL_STRATEGY_TYPES = {
    _STRATEGY_SUPPORT_RESISTANCE,
    _STRATEGY_LIQUIDITY_SWEEP_RETEST,
    _STRATEGY_MACD_SUPPORT_RESISTANCE,
}
_SUPPORT_RESISTANCE_DEFAULTS = {
    "bars_per_timeframe": 100,
    "swing_window": 5,
    "level_tolerance_percent": 0.25,
    "stop_beyond_level_percent": 1.0,
    "take_profit_r_multiple": 2.0,
}
_LIQUIDITY_SWEEP_TARGET_MODE_2R = "2r"
_LIQUIDITY_SWEEP_TARGET_MODE_3R = "3r"
_LIQUIDITY_SWEEP_TARGET_MODE_NEXT_POOL = "next_liquidity"
_LIQUIDITY_SWEEP_TARGET_MODES = {
    _LIQUIDITY_SWEEP_TARGET_MODE_2R,
    _LIQUIDITY_SWEEP_TARGET_MODE_3R,
    _LIQUIDITY_SWEEP_TARGET_MODE_NEXT_POOL,
}
_LIQUIDITY_SWEEP_RETEST_DEFAULTS = {
    "bars_per_timeframe": 100,
    "swing_window": 5,
    "level_tolerance_percent": 0.25,
    "reclaim_within_bars": 2,
    "retest_within_bars": 3,
    "stop_beyond_sweep_percent": 0.05,
    "take_profit_mode": _LIQUIDITY_SWEEP_TARGET_MODE_2R,
}
_DONCHIAN_BREAKOUT_DEFAULTS = {
    "entry_period": 20,
    "exit_period": 10,
    "atr_period": 14,
    "atr_stop_multiple": 2.0,
    "take_profit_r_multiple": 2.0,
    "atr_trail_multiple": 2.0,
    "atr_size_reference_percent": 1.5,
    "min_size_scale": 0.5,
}
_EMA_SCALPING_ALLOWED_MINUTE_BUCKETS = {3, 5}
_EMA_SCALPING_REWARD_MULTIPLE = 2.0
_EMA_SCALPING_MIN_GAP_PERCENT = 0.01
_EMA_SCALPING_MIN_SLOPE_PERCENT = 0.005
_SUPERTREND_PIVOT_DEFAULTS = {
    "daily_bars": 10,
    "supertrend_period": 10,
    "supertrend_multiplier": 3.0,
    "pivot_tolerance_percent": 0.05,
    "stop_beyond_level_percent": 0.05,
    "take_profit_r_multiple": 2.0,
    "chop_lookback_bars": 12,
    "chop_max_flips": 3,
    "chop_max_range_percent": 0.5,
}
_EMA_TREND_PULLBACK_DEFAULTS = {
    "rsi_period": 14,
    "volume_average_period": 20,
    "swing_lookback_bars": 5,
    "long_rsi_min": 40.0,
    "long_rsi_max": 55.0,
    "short_rsi_min": 45.0,
    "short_rsi_max": 60.0,
    "partial_take_profit_r_multiple": 1.0,
    "final_take_profit_r_multiple": 2.0,
}
_EMA_TREND_PULLBACK_FAST_PERIOD = 20
_EMA_TREND_PULLBACK_SLOW_PERIOD = 50
_OPENING_RVOL_BREAKOUT_DEFAULTS = {
    "relative_volume_lookback_days": 20,
    "min_relative_volume": 2.0,
    "min_opening_volume": 500.0,
    "min_body_to_range_ratio": 0.5,
    "atr_period": 14,
    "atr_stop_multiple": 1.0,
    "take_profit_r_multiple": 2.0,
}
_TRAILING_STOP_MODE_ATR = "atr"
_TRAILING_STOP_MODE_SWING = "swing"
_TRAILING_STOP_MODE_MOVING_AVERAGE = "moving_average"
_MACD_SUPPORT_RESISTANCE_TRAILING_STOP_MODES = {
    _TRAILING_STOP_MODE_ATR,
    _TRAILING_STOP_MODE_SWING,
    _TRAILING_STOP_MODE_MOVING_AVERAGE,
}
_MACD_SUPPORT_RESISTANCE_DEFAULTS = {
    "bars_per_timeframe": 100,
    "swing_window": 5,
    "level_tolerance_percent": 0.25,
    "signal_period": 9,
    "atr_period": 14,
    "initial_stop_atr_multiplier": 1.5,
    "trailing_stop_mode": _TRAILING_STOP_MODE_ATR,
    "trailing_atr_multiplier": 2.0,
    "trailing_ma_period": 21,
}
_ORB_STOP_MODE_INSIDE_RANGE = "inside_range"
_ORB_STOP_MODE_OPPOSITE_SIDE = "opposite_side"
_ORB_TARGET_MODE_2R = "2r"
_ORB_TARGET_MODE_3R = "3r"
_ORB_TARGET_MODE_MEASURED_MOVE = "measured_move"
_ORB_TARGET_MODE_DAY_EXTREME = "day_extreme"
_DELAYED_ORB_CONFIRMATION_STOP_MODES = {
    _ORB_STOP_MODE_INSIDE_RANGE,
    _ORB_STOP_MODE_OPPOSITE_SIDE,
}
_DELAYED_ORB_CONFIRMATION_TARGET_MODES = {
    _ORB_TARGET_MODE_2R,
    _ORB_TARGET_MODE_3R,
    _ORB_TARGET_MODE_MEASURED_MOVE,
}
_DELAYED_ORB_CONFIRMATION_DEFAULTS = {
    "opening_range_minutes": 15,
    "confirmation_minutes": 5,
    "stop_mode": _ORB_STOP_MODE_INSIDE_RANGE,
    "target_mode": _ORB_TARGET_MODE_2R,
    "stop_after_losses_per_session": 0,
}
_ORB_FIBONACCI_PULLBACK_TAKE_PROFIT_MODES = {
    _ORB_TARGET_MODE_2R,
    _ORB_TARGET_MODE_3R,
    _ORB_TARGET_MODE_DAY_EXTREME,
}
_ORB_FIBONACCI_PULLBACK_DEFAULTS = {
    "opening_range_minutes": 15,
    "swing_lookback_bars": 5,
    "take_profit_mode": _ORB_TARGET_MODE_2R,
}
_VWAP_ATR_MEAN_REVERSION_TAKE_PROFIT_MODES = {"vwap", "half_vwap_distance", "r_multiple"}
_VWAP_ATR_MEAN_REVERSION_DEFAULTS = {
    "atr_period": 14,
    "rsi_period": 14,
    "adx_period": 14,
    "stretch_atr_multiple": 1.0,
    "rsi_oversold": 30.0,
    "rsi_overbought": 70.0,
    "adx_max": 20.0,
    "vwap_slope_bars": 5,
    "flat_vwap_threshold_bps": 8.0,
    "local_extreme_lookback": 5,
    "stop_buffer_atr": 0.1,
    "take_profit_mode": "vwap",
    "take_profit_r_multiple": 1.5,
}
_BOLLINGER_RSI_REVERSAL_TAKE_PROFIT_MODES = {"middle_band", "vwap", "two_r"}
_BOLLINGER_RSI_REVERSAL_DEFAULTS = {
    "rsi_period": 14,
    "rsi_oversold": 30.0,
    "rsi_overbought": 70.0,
    "bollinger_period": 20,
    "bollinger_stddev": 2.0,
    "adx_period": 14,
    "adx_max": 25.0,
    "swing_stop_lookback_bars": 5,
    "stop_buffer_percent": 0.1,
    "take_profit_mode": "middle_band",
    "take_profit_r_multiple": 2.0,
}
_BOLLINGER_MEAN_REVERSION_TAKE_PROFIT_MODES = {"middle_band", "vwap", "fixed_r"}
_BOLLINGER_MEAN_REVERSION_DEFAULTS = {
    "bollinger_period": 120,
    "bollinger_stddev": 4.5,
    "atr_period": 14,
    "atr_stop_buffer": 0.5,
    "take_profit_mode": "middle_band",
    "take_profit_r_multiple": 1.5,
    "news_blackout_windows": ["08:25-08:35", "09:55-10:05", "13:55-14:05"],
}
_FISHER_MEAN_REVERSION_DEFAULTS = {
    "fisher_length": 10,
    "fisher_extreme_threshold": 1.5,
    "price_stretch_percent": 0.2,
    "ema_slope_lookback_bars": 5,
    "ema_slope_max_percent": 0.6,
    "swing_stop_lookback_bars": 5,
    "take_profit_r_multiple": 2.0,
}
_VWAP_GAP_RETRACE_DEFAULTS = {
    "min_gap_percent": 2.0,
    "wait_start_minutes": 5,
    "wait_end_minutes": 15,
    "min_volume_ratio": 1.0,
    "stop_beyond_vwap_percent": 0.1,
    "touch_tolerance_percent": 0.1,
    "bars_to_fetch": 2000,
}
_REGULAR_SESSION_OPEN = time(hour=9, minute=30)
_REGULAR_SESSION_CLOSE = time(hour=16, minute=0)
_FVG_TARGET_MODE_2R = "2r"
_FVG_TARGET_MODE_3R = "3r"
_FVG_TARGET_MODE_NEXT_LIQUIDITY = "next_liquidity"
_FVG_SWEEP_MSS_TARGET_MODES = {
    _FVG_TARGET_MODE_2R,
    _FVG_TARGET_MODE_3R,
    _FVG_TARGET_MODE_NEXT_LIQUIDITY,
}
_FVG_SWEEP_MSS_DEFAULTS = {
    "swing_window": 5,
    "volume_lookback_bars": 20,
    "strong_volume_multiplier": 1.5,
    "stop_buffer_percent": 0.05,
    "target_mode": _FVG_TARGET_MODE_NEXT_LIQUIDITY,
}
_ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS = {
    "benchmark_symbol": "SPY",
    "move_lookback_bars": 3,
    "atr_period": 14,
    "relative_volume_period": 20,
    "relative_volume_cap": 3.0,
    "long_score_threshold": 1.5,
    "short_score_threshold": -1.5,
    "ema_period": 9,
    "stop_structure_window": 5,
    "stop_atr_multiple": 0.25,
    "take_profit_r_multiple": 2.0,
}
_RELATIVE_STRENGTH_SPY_DEFAULTS = {
    "benchmark_symbol": "SPY",
    "comparison_bars": 12,
    "pullback_lookback_bars": 3,
    "relative_volume_period": 20,
    "minimum_relative_volume": 2.0,
    "minimum_relative_strength_percent": 0.25,
    "minimum_benchmark_move_percent": 0.1,
    "ema_period": 9,
    "swing_window": 5,
    "major_level_lookback_bars": 40,
    "entry_level_tolerance_percent": 0.4,
    "stop_buffer_percent": 0.1,
    "take_profit_r_multiple": 2.0,
}
_PULLBACK_TRAP_REVERSAL_DEFAULTS = {
    "pullback_lookback_bars": 4,
    "micro_level_window": 3,
    "volume_baseline_bars": 20,
    "volume_spike_multiple": 1.5,
    "wick_to_body_ratio_min": 1.5,
    "stop_buffer_percent": 0.1,
    "take_profit_r_multiple": 2.0,
    "trend_confirmation_bars": 3,
    "min_countertrend_bars": 2,
    "pullback_range_multiplier": 1.25,
    "prior_swing_window": 10,
}


@dataclass(frozen=True)
class SignalResult:
    action: str
    reason: str
    candle_timestamp: datetime | None
    price: float | None
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class RiskBlock:
    code: str
    message: str
    severity: str = "warning"


@dataclass(frozen=True)
class SupportResistanceLevel:
    side: str
    price: float
    timestamp: datetime
    timeframe: str
    source_index: int
    score: float


@dataclass(frozen=True)
class PivotLevel:
    name: str
    kind: str
    price: float


@dataclass(frozen=True)
class SupertrendState:
    timestamp: datetime
    value: float
    direction: str
    upper_band: float
    lower_band: float


@dataclass(frozen=True)
class FairValueGapZone:
    side: str
    lower_price: float
    upper_price: float
    timestamp: datetime
    source_index: int


@dataclass(frozen=True)
class LevelStrategyContext:
    higher_closed: list[ProjectXMarketCandle]
    lower_closed: list[ProjectXMarketCandle]
    latest: ProjectXMarketCandle | None
    latest_timestamp: datetime | None
    latest_price: float | None
    raw_levels: list[SupportResistanceLevel]
    supports: list[SupportResistanceLevel]
    resistances: list[SupportResistanceLevel]
    support_touch: tuple[SupportResistanceLevel, float] | None
    resistance_touch: tuple[SupportResistanceLevel, float] | None
    nearest_touch: tuple[SupportResistanceLevel, float] | None
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class LiquiditySweepRetestSetup:
    action: str
    trigger_level: SupportResistanceLevel
    sweep_candle: ProjectXMarketCandle
    reclaim_candle: ProjectXMarketCandle
    retest_candle: ProjectXMarketCandle
    stop_loss: float
    take_profit: float
    risk: float
    reward_r: float
    target_mode: str
    target_source: str
    target_level: SupportResistanceLevel | None = None


@dataclass(frozen=True)
class OpenPositionLot:
    qty: float
    timestamp: datetime
    price: float


@dataclass(frozen=True)
class OpenPositionState:
    net_qty: float
    avg_entry_price: float | None
    opened_at: datetime | None

    @property
    def side(self) -> str:
        if self.net_qty > 0:
            return "long"
        if self.net_qty < 0:
            return "short"
        return "flat"

    @property
    def abs_qty(self) -> float:
        return abs(self.net_qty)


@dataclass(frozen=True)
class EvaluationResult:
    config: BotConfig
    run: BotRun | None
    decision: BotDecision
    order_attempt: BotOrderAttempt | None
    risk_events: list[BotRiskEvent]
    analysis: dict[str, Any]
    candles: list[ProjectXMarketCandle]


def list_bot_configs(
    db: Session,
    *,
    user_id: str,
    account_id: int | None = None,
) -> list[BotConfig]:
    query = db.query(BotConfig).filter(BotConfig.user_id == user_id)
    if account_id is not None:
        query = query.filter(BotConfig.account_id == account_id)
    return query.order_by(BotConfig.created_at.desc(), BotConfig.id.desc()).all()


def get_bot_config(db: Session, *, user_id: str, bot_config_id: int) -> BotConfig | None:
    return (
        db.query(BotConfig)
        .filter(BotConfig.user_id == user_id)
        .filter(BotConfig.id == bot_config_id)
        .one_or_none()
    )


def create_bot_config(db: Session, *, user_id: str, payload: Any) -> BotConfig:
    _require_owned_account(db, user_id=user_id, account_id=payload.account_id)
    name = _validate_unique_bot_name(db, user_id=user_id, name=payload.name)
    strategy_type = _validate_strategy_type(payload.strategy_type)
    strategy_params = _normalize_strategy_params(strategy_type, payload.strategy_params)
    fast_period, slow_period = _normalized_strategy_period_values(
        strategy_type,
        fast_period=int(payload.fast_period),
        slow_period=int(payload.slow_period),
    )
    _validate_strategy_configuration(
        strategy_type=strategy_type,
        timeframe_unit=str(payload.timeframe_unit),
        timeframe_unit_number=int(payload.timeframe_unit_number),
        fast_period=fast_period,
        slow_period=slow_period,
    )
    _validate_session_time(payload.trading_start_time)
    _validate_session_time(payload.trading_end_time)

    row = BotConfig(
        user_id=user_id,
        account_id=payload.account_id,
        name=name,
        enabled=bool(payload.enabled),
        execution_mode=payload.execution_mode,
        strategy_type=strategy_type,
        strategy_params=strategy_params,
        contract_id=payload.contract_id.strip(),
        symbol=_normalized_optional_text(payload.symbol),
        timeframe_unit=payload.timeframe_unit,
        timeframe_unit_number=payload.timeframe_unit_number,
        lookback_bars=payload.lookback_bars,
        fast_period=fast_period,
        slow_period=slow_period,
        order_size=payload.order_size,
        max_contracts=payload.max_contracts,
        max_daily_loss=payload.max_daily_loss,
        max_trades_per_day=payload.max_trades_per_day,
        max_open_position=payload.max_open_position,
        allowed_contracts=_normalize_allowed_contracts(payload.allowed_contracts),
        trading_start_time=payload.trading_start_time,
        trading_end_time=payload.trading_end_time,
        cooldown_seconds=payload.cooldown_seconds,
        max_data_staleness_seconds=payload.max_data_staleness_seconds,
        allow_market_depth=payload.allow_market_depth,
    )
    db.add(row)
    db.flush()
    return row


def update_bot_config(db: Session, *, user_id: str, bot_config_id: int, payload: Any) -> BotConfig:
    row = get_bot_config(db, user_id=user_id, bot_config_id=bot_config_id)
    if row is None:
        raise LookupError("bot_config_not_found")

    update_data = payload.model_dump(exclude_unset=True)
    if "account_id" in update_data:
        _require_owned_account(db, user_id=user_id, account_id=int(update_data["account_id"]))
    if "name" in update_data and update_data["name"] is not None:
        update_data["name"] = _validate_unique_bot_name(
            db,
            user_id=user_id,
            name=update_data["name"],
            exclude_bot_config_id=bot_config_id,
        )
    if "contract_id" in update_data and update_data["contract_id"] is not None:
        update_data["contract_id"] = str(update_data["contract_id"]).strip()
    if "symbol" in update_data:
        update_data["symbol"] = _normalized_optional_text(update_data["symbol"])
    if "strategy_type" in update_data and update_data["strategy_type"] is not None:
        update_data["strategy_type"] = _validate_strategy_type(update_data["strategy_type"])
    if "allowed_contracts" in update_data and update_data["allowed_contracts"] is not None:
        update_data["allowed_contracts"] = _normalize_allowed_contracts(update_data["allowed_contracts"])
    if "trading_start_time" in update_data and update_data["trading_start_time"] is not None:
        _validate_session_time(update_data["trading_start_time"])
    if "trading_end_time" in update_data and update_data["trading_end_time"] is not None:
        _validate_session_time(update_data["trading_end_time"])

    for key, value in update_data.items():
        setattr(row, key, value)

    row.fast_period, row.slow_period = _normalized_strategy_period_values(
        str(row.strategy_type),
        fast_period=int(row.fast_period),
        slow_period=int(row.slow_period),
    )
    _validate_strategy_configuration(
        strategy_type=str(row.strategy_type),
        timeframe_unit=str(row.timeframe_unit),
        timeframe_unit_number=int(row.timeframe_unit_number),
        fast_period=int(row.fast_period),
        slow_period=int(row.slow_period),
    )
    if "strategy_type" in update_data or "strategy_params" in update_data:
        row.strategy_params = _normalize_strategy_params(str(row.strategy_type), row.strategy_params)
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row


def delete_bot_config(db: Session, *, user_id: str, bot_config_id: int) -> None:
    row = get_bot_config(db, user_id=user_id, bot_config_id=bot_config_id)
    if row is None:
        raise LookupError("bot_config_not_found")

    filters = {"user_id": user_id, "bot_config_id": bot_config_id}
    db.query(BotOrderAttempt).filter_by(**filters).delete(synchronize_session=False)
    db.query(BotRiskEvent).filter_by(**filters).delete(synchronize_session=False)
    db.query(BotDecision).filter_by(**filters).delete(synchronize_session=False)
    db.query(BotRun).filter_by(**filters).delete(synchronize_session=False)
    db.delete(row)
    db.flush()


def start_bot_run(
    db: Session,
    *,
    user_id: str,
    bot_config_id: int,
    client: ProjectXClient,
    dry_run: bool | None = None,
    confirm_live_order_routing: bool = False,
) -> EvaluationResult:
    config = _require_bot_config(db, user_id=user_id, bot_config_id=bot_config_id, lock_for_update=True)
    account = _require_owned_account(db, user_id=user_id, account_id=int(config.account_id))
    now = datetime.now(timezone.utc)
    config.enabled = True
    config.updated_at = now
    effective_dry_run = bool(dry_run) if dry_run is not None else config.execution_mode != "live"
    _stop_running_bot_runs(
        db,
        user_id=user_id,
        bot_config_id=bot_config_id,
        reason="superseded_by_manual_start",
        now=now,
    )
    run = BotRun(
        user_id=user_id,
        bot_config_id=int(config.id),
        account_id=int(config.account_id),
        status="running",
        dry_run=effective_dry_run,
        started_at=now,
        last_heartbeat_at=now,
        raw_state={"source": "manual_start"},
    )
    db.add(run)
    db.flush()
    return evaluate_bot_config(
        db,
        user_id=user_id,
        config=config,
        account=account,
        client=client,
        run=run,
        dry_run=effective_dry_run,
        confirm_live_order_routing=confirm_live_order_routing,
    )


def evaluate_bot_config(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    account: Account | None,
    client: ProjectXClient,
    run: BotRun | None = None,
    dry_run: bool | None = None,
    confirm_live_order_routing: bool = False,
) -> EvaluationResult:
    resolved_account = account or _require_owned_account(db, user_id=user_id, account_id=int(config.account_id))
    effective_dry_run = bool(dry_run) if dry_run is not None else config.execution_mode != "live"
    candles, signal = fetch_candles_and_evaluate_strategy(db, user_id=user_id, config=config, client=client)
    analysis = build_bot_market_analysis(candles=candles, config=config, signal=signal)
    trade_evaluation = build_signal_trade_evaluation(candles=candles, config=config, signal=signal, analysis=analysis)
    if trade_evaluation is not None:
        analysis["trade_evaluation"] = trade_evaluation
    signal_order_size = _signal_order_size(config=config, signal=signal)
    current_position_qty = _signal_current_position_qty(signal)
    target_position_qty = _signal_target_position_qty(signal)
    latest_candle = candles[-1] if candles else None
    execution_contract_id = _execution_contract_id(config, latest_candle)
    execution_symbol = _execution_symbol(config, latest_candle)
    decision = BotDecision(
        user_id=user_id,
        bot_config_id=int(config.id),
        bot_run_id=int(run.id) if run is not None and run.id is not None else None,
        account_id=int(config.account_id),
        contract_id=execution_contract_id,
        symbol=execution_symbol,
        decision_type="signal",
        action=signal.action,
        reason=signal.reason,
        candle_timestamp=signal.candle_timestamp,
        price=signal.price,
        quantity=signal_order_size if signal.action in {"BUY", "SELL"} else None,
        raw_payload=signal.raw_payload,
    )
    db.add(decision)
    db.flush()

    risk_events: list[BotRiskEvent] = []
    order_attempt: BotOrderAttempt | None = None
    if signal.action in {"BUY", "SELL"}:
        blocks = evaluate_risk_gates(
            db,
            user_id=user_id,
            config=config,
            account=resolved_account,
            latest_candle=latest_candle,
            contract_id=execution_contract_id,
            symbol=execution_symbol,
            action=signal.action,
            requested_order_size=signal_order_size,
            current_position_qty=current_position_qty,
            target_position_qty=target_position_qty,
            dry_run=effective_dry_run,
            confirm_live_order_routing=confirm_live_order_routing,
        )
        if blocks:
            risk_events = [
                _create_risk_event(db, user_id=user_id, config=config, run=run, block=block)
                for block in blocks
            ]
            db.add(
                BotDecision(
                    user_id=user_id,
                    bot_config_id=int(config.id),
                    bot_run_id=int(run.id) if run is not None and run.id is not None else None,
                    account_id=int(config.account_id),
                    contract_id=execution_contract_id,
                    symbol=execution_symbol,
                    decision_type="risk_reject",
                    action=signal.action,
                    reason="; ".join(block.message for block in blocks),
                    candle_timestamp=signal.candle_timestamp,
                    price=signal.price,
                    quantity=signal_order_size,
                    raw_payload={"risk_blocks": [block.__dict__ for block in blocks]},
                )
            )
            if run is not None:
                run.status = "blocked"
                run.stopped_at = datetime.now(timezone.utc)
                run.stop_reason = "risk_gate_blocked_order"
        else:
            order_attempt = _create_order_attempt(
                db,
                user_id=user_id,
                config=config,
                run=run,
                decision=decision,
                contract_id=execution_contract_id,
                action=signal.action,
                order_size=signal_order_size,
            )
            db.flush()
            if effective_dry_run:
                order_attempt.status = "dry_run"
                order_attempt.raw_response = {"dry_run": True, "message": "Order not sent to ProjectX."}
            else:
                # Persist the audit row before any possible external order submission.
                db.commit()
                _submit_order_attempt(client=client, order_attempt=order_attempt)
            db.flush()

    if run is not None and run.status == "running":
        run.last_heartbeat_at = datetime.now(timezone.utc)
    db.flush()
    return EvaluationResult(
        config=config,
        run=run,
        decision=decision,
        order_attempt=order_attempt,
        risk_events=risk_events,
        analysis=analysis,
        candles=candles,
    )


def stop_latest_bot_run(
    db: Session,
    *,
    user_id: str,
    bot_config_id: int,
    reason: str = "manual_stop",
) -> BotRun:
    config = _require_bot_config(db, user_id=user_id, bot_config_id=bot_config_id, lock_for_update=True)
    config.enabled = False
    config.updated_at = datetime.now(timezone.utc)
    run = (
        db.query(BotRun)
        .filter(BotRun.user_id == user_id)
        .filter(BotRun.bot_config_id == bot_config_id)
        .filter(BotRun.status == "running")
        .order_by(BotRun.started_at.desc(), BotRun.id.desc())
        .first()
    )
    if run is None:
        run = BotRun(
            user_id=user_id,
            bot_config_id=bot_config_id,
            account_id=int(config.account_id),
            status="stopped",
            dry_run=True,
            started_at=datetime.now(timezone.utc),
            stopped_at=datetime.now(timezone.utc),
            stop_reason=reason,
        )
        db.add(run)
        db.flush()
    else:
        run.status = "stopped"
        run.stopped_at = datetime.now(timezone.utc)
        run.stop_reason = reason

    db.add(
        BotDecision(
            user_id=user_id,
            bot_config_id=bot_config_id,
            bot_run_id=int(run.id) if run.id is not None else None,
            account_id=int(config.account_id),
            contract_id=str(config.contract_id),
            symbol=config.symbol,
            decision_type="lifecycle",
            action="STOP",
            reason=reason,
        )
    )
    db.flush()
    return run


def get_bot_activity(
    db: Session,
    *,
    user_id: str,
    bot_config_id: int,
    limit: int = 50,
) -> dict[str, Any]:
    config = _require_bot_config(db, user_id=user_id, bot_config_id=bot_config_id)
    bounded_limit = max(1, min(int(limit), 200))
    runs = (
        db.query(BotRun)
        .filter(BotRun.user_id == user_id)
        .filter(BotRun.bot_config_id == bot_config_id)
        .order_by(BotRun.started_at.desc(), BotRun.id.desc())
        .limit(10)
        .all()
    )
    decisions = (
        db.query(BotDecision)
        .filter(BotDecision.user_id == user_id)
        .filter(BotDecision.bot_config_id == bot_config_id)
        .order_by(BotDecision.created_at.desc(), BotDecision.id.desc())
        .limit(bounded_limit)
        .all()
    )
    attempts = (
        db.query(BotOrderAttempt)
        .filter(BotOrderAttempt.user_id == user_id)
        .filter(BotOrderAttempt.bot_config_id == bot_config_id)
        .order_by(BotOrderAttempt.created_at.desc(), BotOrderAttempt.id.desc())
        .limit(bounded_limit)
        .all()
    )
    risk_events = (
        db.query(BotRiskEvent)
        .filter(BotRiskEvent.user_id == user_id)
        .filter(BotRiskEvent.bot_config_id == bot_config_id)
        .order_by(BotRiskEvent.created_at.desc(), BotRiskEvent.id.desc())
        .limit(bounded_limit)
        .all()
    )
    return {
        "config": config,
        "runs": runs,
        "decisions": decisions,
        "order_attempts": attempts,
        "risk_events": risk_events,
    }


def fetch_candles_and_evaluate_strategy(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    client: ProjectXClient,
) -> tuple[list[ProjectXMarketCandle], SignalResult]:
    strategy_type = _validate_strategy_type(str(config.strategy_type))
    if strategy_type == _STRATEGY_DELAYED_ORB_CONFIRMATION:
        strategy_params = _normalize_strategy_params(strategy_type, config.strategy_params)
        candle_sets = fetch_and_store_delayed_orb_candles(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_params=strategy_params,
        )
        signal = evaluate_delayed_orb_confirmation(
            candles=candle_sets.get("1m", []),
            strategy_params=strategy_params,
            session_start_time=str(config.trading_start_time),
        )
        return candle_sets.get("1m", []), signal

    if strategy_type == _STRATEGY_ORB_FIBONACCI_PULLBACK:
        strategy_params = _normalize_strategy_params(strategy_type, config.strategy_params)
        candles = fetch_and_store_orb_fibonacci_candles(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_params=strategy_params,
        )
        signal = evaluate_orb_fibonacci_pullback(
            candles,
            timeframe_unit=str(config.timeframe_unit),
            timeframe_unit_number=int(config.timeframe_unit_number),
            strategy_params=strategy_params,
            session_start_time=str(config.trading_start_time),
            session_end_time=str(config.trading_end_time),
        )
        return candles, signal

    if strategy_type == _STRATEGY_OPENING_RVOL_BREAKOUT:
        strategy_params = _normalize_strategy_params(strategy_type, config.strategy_params)
        candles = fetch_and_store_opening_rvol_breakout_candles(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_params=strategy_params,
        )
        signal = evaluate_opening_rvol_breakout(
            candles,
            strategy_params=strategy_params,
            session_start_time=str(config.trading_start_time),
        )
        return candles, signal

    if strategy_type == _STRATEGY_VWAP_GAP_RETRACE:
        strategy_params = _normalize_strategy_params(strategy_type, config.strategy_params)
        candles = fetch_and_store_vwap_gap_retrace_candles(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_params=strategy_params,
        )
        signal = evaluate_vwap_gap_retrace(candles, strategy_params=strategy_params)
        return candles, signal

    if strategy_type in _LEVEL_STRATEGY_TYPES:
        strategy_params = _normalize_strategy_params(strategy_type, config.strategy_params)
        candle_sets = fetch_and_store_support_resistance_candles(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_type=strategy_type,
            strategy_params=strategy_params,
        )
        candles_1h = candle_sets.get("1H", [])
        if strategy_type == _STRATEGY_LIQUIDITY_SWEEP_RETEST:
            signal = evaluate_liquidity_sweep_retest(
                higher_timeframe_candles=candle_sets.get("4H", []),
                lower_timeframe_candles=candles_1h,
                fast_period=int(config.fast_period),
                slow_period=int(config.slow_period),
                strategy_params=strategy_params,
            )
        elif strategy_type == _STRATEGY_MACD_SUPPORT_RESISTANCE:
            signal = evaluate_macd_support_resistance(
                higher_timeframe_candles=candle_sets.get("4H", []),
                lower_timeframe_candles=candles_1h,
                fast_period=int(config.fast_period),
                slow_period=int(config.slow_period),
                strategy_params=strategy_params,
            )
        else:
            signal = evaluate_support_resistance_levels(
                higher_timeframe_candles=candle_sets.get("4H", []),
                lower_timeframe_candles=candles_1h,
                strategy_params=strategy_params,
            )
        return candles_1h, signal

    if strategy_type == _STRATEGY_SUPERTREND_PIVOT:
        strategy_params = _normalize_strategy_params(strategy_type, config.strategy_params)
        candle_sets = fetch_and_store_supertrend_pivot_candles(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_params=strategy_params,
        )
        signal_candles = candle_sets.get("signal", [])
        signal = evaluate_supertrend_pivot_points(
            signal_timeframe_candles=signal_candles,
            daily_candles=candle_sets.get("1D", []),
            strategy_params=strategy_params,
        )
        return signal_candles, signal

    if strategy_type == _STRATEGY_FVG_SWEEP_MSS:
        strategy_params = _normalize_strategy_params(strategy_type, config.strategy_params)
        candle_sets = fetch_and_store_fvg_sweep_mss_candles(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_params=strategy_params,
        )
        structure_candles = candle_sets.get("structure", [])
        signal = evaluate_fvg_sweep_mss(
            fvg_candles=candle_sets.get("fvg", []),
            structure_candles=structure_candles,
            strategy_params=strategy_params,
        )
        return structure_candles, signal

    if strategy_type == _STRATEGY_ATR_ADJUSTED_RELATIVE_STRENGTH:
        strategy_params = _normalize_strategy_params(strategy_type, config.strategy_params)
        candles = fetch_and_store_candles(db, user_id=user_id, config=config, client=client)
        benchmark_candles = fetch_and_store_relative_strength_benchmark_candles(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_params=strategy_params,
        )
        signal = evaluate_atr_adjusted_relative_strength(
            candles,
            benchmark_candles=benchmark_candles,
            strategy_params=strategy_params,
            session_start_time=str(config.trading_start_time),
        )
        return candles, signal

    if strategy_type == _STRATEGY_RELATIVE_STRENGTH_SPY:
        strategy_params = _normalize_strategy_params(strategy_type, config.strategy_params)
        candle_sets = fetch_and_store_relative_strength_spy_candles(
            db,
            user_id=user_id,
            config=config,
            client=client,
            strategy_params=strategy_params,
        )
        asset_candles = candle_sets.get("5m", [])
        signal = evaluate_relative_strength_vs_spy(
            asset_candles=asset_candles,
            benchmark_candles=candle_sets.get("SPY", []),
            strategy_params=strategy_params,
        )
        return asset_candles, signal

    minimum_lookback_bars: int | None = None
    if strategy_type == _STRATEGY_BOLLINGER_MEAN_REVERSION:
        strategy_params = _normalize_strategy_params(strategy_type, config.strategy_params)
        minimum_lookback_bars = max(
            int(strategy_params["bollinger_period"]) + 1,
            int(strategy_params["atr_period"]),
            25,
        )

    candles = fetch_and_store_candles(
        db,
        user_id=user_id,
        config=config,
        client=client,
        minimum_lookback_bars=minimum_lookback_bars,
    )
    if strategy_type == _STRATEGY_DONCHIAN_BREAKOUT:
        latest_candle = candles[-1] if candles else None
        contract_id = _execution_contract_id(config, latest_candle)
        symbol = _execution_symbol(config, latest_candle)
        position_state = load_open_position_state(
            db,
            user_id=user_id,
            account_id=int(config.account_id),
            contract_id=contract_id,
            symbol=symbol,
        )
        signal = evaluate_donchian_breakout(
            candles,
            strategy_params=config.strategy_params,
            position_state=position_state,
            latest_entry_plan=load_latest_bot_entry_plan(
                db,
                user_id=user_id,
                bot_config_id=int(config.id),
                position_state=position_state,
            ),
            base_order_size=float(config.order_size),
        )
    elif strategy_type == _STRATEGY_BOLLINGER_MEAN_REVERSION:
        signal = evaluate_bollinger_mean_reversion(candles, strategy_params=config.strategy_params)
    elif strategy_type == _STRATEGY_BOLLINGER_RSI_REVERSAL:
        signal = evaluate_bollinger_rsi_reversal(candles, strategy_params=config.strategy_params)
    elif strategy_type == _STRATEGY_EMA_SCALPING:
        signal = evaluate_ema_scalping(candles, fast_period=int(config.fast_period), slow_period=int(config.slow_period))
    elif strategy_type == _STRATEGY_EMA_TREND_PULLBACK:
        signal = evaluate_ema_trend_pullback(
            candles,
            fast_period=int(config.fast_period),
            slow_period=int(config.slow_period),
            strategy_params=config.strategy_params,
        )
    elif strategy_type == _STRATEGY_VWAP_ATR_MEAN_REVERSION:
        signal = evaluate_vwap_atr_mean_reversion(candles, strategy_params=config.strategy_params)
    elif strategy_type == _STRATEGY_FISHER_MEAN_REVERSION:
        signal = evaluate_fisher_transform_mean_reversion(
            candles,
            fast_period=int(config.fast_period),
            slow_period=int(config.slow_period),
            strategy_params=config.strategy_params,
        )
    elif strategy_type == _STRATEGY_PULLBACK_TRAP_REVERSAL:
        signal = evaluate_pullback_trap_reversal(
            candles,
            fast_period=int(config.fast_period),
            slow_period=int(config.slow_period),
            strategy_params=config.strategy_params,
        )
    else:
        signal = evaluate_sma_cross(candles, fast_period=int(config.fast_period), slow_period=int(config.slow_period))
    return candles, signal


def fetch_and_store_candles(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    client: ProjectXClient,
    minimum_lookback_bars: int | None = None,
) -> list[ProjectXMarketCandle]:
    now = datetime.now(timezone.utc)
    target_bars = max(25, int(config.lookback_bars), int(minimum_lookback_bars or 0))
    unit_seconds = _UNIT_SECONDS_BY_NAME[str(config.timeframe_unit)]
    lookback_seconds = unit_seconds * int(config.timeframe_unit_number) * target_bars * 3
    start = now - timedelta(seconds=max(lookback_seconds, unit_seconds * int(config.timeframe_unit_number) * 25))
    contract_id, symbol = resolve_market_contract(
        client,
        contract_id=str(config.contract_id),
        symbol=config.symbol,
        live=False,
    )
    bars = client.retrieve_bars(
        contract_id=contract_id,
        live=False,
        start=start,
        end=now,
        unit=_PROJECTX_UNIT_BY_NAME[str(config.timeframe_unit)],
        unit_number=int(config.timeframe_unit_number),
        limit=target_bars,
        include_partial_bar=False,
    )
    return store_market_candles(
        db,
        user_id=user_id,
        contract_id=contract_id,
        symbol=symbol,
        live=False,
        unit=str(config.timeframe_unit),
        unit_number=int(config.timeframe_unit_number),
        bars=bars,
    )


def fetch_and_store_vwap_gap_retrace_candles(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    client: ProjectXClient,
    strategy_params: dict[str, Any] | None = None,
) -> list[ProjectXMarketCandle]:
    params = _normalize_strategy_params(_STRATEGY_VWAP_GAP_RETRACE, strategy_params)
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=5)
    return fetch_and_store_market_candles(
        db,
        user_id=user_id,
        client=client,
        contract_id=str(config.contract_id),
        symbol=config.symbol,
        live=False,
        start=start,
        end=now,
        unit="minute",
        unit_number=1,
        limit=int(params["bars_to_fetch"]),
        include_partial_bar=False,
    )


def fetch_and_store_fvg_sweep_mss_candles(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    client: ProjectXClient,
    strategy_params: dict[str, Any] | None = None,
) -> dict[str, list[ProjectXMarketCandle]]:
    _normalize_strategy_params(_STRATEGY_FVG_SWEEP_MSS, strategy_params)
    now = datetime.now(timezone.utc)
    base_unit = str(config.timeframe_unit)
    base_unit_number = int(config.timeframe_unit_number)
    base_seconds = _UNIT_SECONDS_BY_NAME[base_unit] * base_unit_number
    structure_unit, structure_unit_number = _derive_lower_timeframe(base_unit=base_unit, base_unit_number=base_unit_number)
    structure_seconds = _UNIT_SECONDS_BY_NAME[structure_unit] * structure_unit_number
    structure_ratio = max(1, int(round(base_seconds / structure_seconds)))
    fvg_limit = max(25, int(config.lookback_bars))
    structure_limit = min(5000, max(fvg_limit * structure_ratio, fvg_limit + 25))
    contract_id, symbol = resolve_market_contract(
        client,
        contract_id=str(config.contract_id),
        symbol=config.symbol,
        live=False,
    )

    candle_sets: dict[str, list[ProjectXMarketCandle]] = {}
    for key, unit, unit_number, limit in (
        ("fvg", base_unit, base_unit_number, fvg_limit),
        ("structure", structure_unit, structure_unit_number, structure_limit),
    ):
        unit_seconds = _UNIT_SECONDS_BY_NAME[unit]
        lookback_seconds = unit_seconds * unit_number * limit * 3
        start = now - timedelta(seconds=max(lookback_seconds, unit_seconds * unit_number * 25))
        bars = client.retrieve_bars(
            contract_id=contract_id,
            live=False,
            start=start,
            end=now,
            unit=_PROJECTX_UNIT_BY_NAME[unit],
            unit_number=unit_number,
            limit=limit,
            include_partial_bar=False,
        )
        candle_sets[key] = store_market_candles(
            db,
            user_id=user_id,
            contract_id=contract_id,
            symbol=symbol,
            live=False,
            unit=unit,
            unit_number=unit_number,
            bars=bars,
        )

    return candle_sets


def fetch_and_store_opening_rvol_breakout_candles(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    client: ProjectXClient,
    strategy_params: dict[str, Any] | None = None,
) -> list[ProjectXMarketCandle]:
    params = _normalize_strategy_params(_STRATEGY_OPENING_RVOL_BREAKOUT, strategy_params)
    lookback_days = int(params["relative_volume_lookback_days"])
    atr_period = int(params["atr_period"])
    now = datetime.now(timezone.utc)
    calendar_lookback_days = max(lookback_days + 14, 21)
    five_minute_bars_per_day = (24 * 60) // 5
    limit = min(
        20_000,
        max(
            int(config.lookback_bars),
            (calendar_lookback_days + 1) * five_minute_bars_per_day,
            atr_period * 20,
            500,
        ),
    )
    start = now - timedelta(days=calendar_lookback_days)
    return fetch_and_store_market_candles(
        db,
        user_id=user_id,
        client=client,
        contract_id=str(config.contract_id),
        symbol=config.symbol,
        live=False,
        start=start,
        end=now,
        unit="minute",
        unit_number=5,
        limit=limit,
        include_partial_bar=False,
    )


def fetch_and_store_relative_strength_benchmark_candles(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    client: ProjectXClient,
    strategy_params: dict[str, Any] | None = None,
) -> list[ProjectXMarketCandle]:
    params = _normalize_strategy_params(_STRATEGY_ATR_ADJUSTED_RELATIVE_STRENGTH, strategy_params)
    benchmark_contract_id = _normalized_optional_text(params.get("benchmark_contract_id"))
    benchmark_symbol = _normalized_optional_text(params.get("benchmark_symbol")) or str(
        _ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS["benchmark_symbol"]
    )
    benchmark_identifier = benchmark_contract_id or benchmark_symbol
    now = datetime.now(timezone.utc)
    unit_seconds = _UNIT_SECONDS_BY_NAME[str(config.timeframe_unit)]
    lookback_seconds = unit_seconds * int(config.timeframe_unit_number) * int(config.lookback_bars) * 3
    start = now - timedelta(seconds=max(lookback_seconds, unit_seconds * int(config.timeframe_unit_number) * 25))
    return fetch_and_store_market_candles(
        db,
        user_id=user_id,
        client=client,
        contract_id=benchmark_identifier,
        symbol=benchmark_symbol,
        live=False,
        start=start,
        end=now,
        unit=str(config.timeframe_unit),
        unit_number=int(config.timeframe_unit_number),
        limit=int(config.lookback_bars),
        include_partial_bar=False,
    )


def fetch_and_store_relative_strength_spy_candles(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    client: ProjectXClient,
    strategy_params: dict[str, Any] | None = None,
) -> dict[str, list[ProjectXMarketCandle]]:
    params = _normalize_strategy_params(_STRATEGY_RELATIVE_STRENGTH_SPY, strategy_params)
    comparison_bars = int(params["comparison_bars"])
    pullback_lookback_bars = int(params["pullback_lookback_bars"])
    relative_volume_period = int(params["relative_volume_period"])
    major_level_lookback_bars = int(params["major_level_lookback_bars"])
    limit = min(
        20_000,
        max(
            int(config.lookback_bars),
            comparison_bars + 1,
            pullback_lookback_bars + 1,
            relative_volume_period + 1,
            major_level_lookback_bars,
            50,
        ),
    )
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=limit * 5 * 3)
    benchmark_contract_id = _normalized_optional_text(params.get("benchmark_contract_id"))
    benchmark_symbol = _normalized_optional_text(params.get("benchmark_symbol")) or str(
        _RELATIVE_STRENGTH_SPY_DEFAULTS["benchmark_symbol"]
    )
    benchmark_identifier = benchmark_contract_id or benchmark_symbol
    return {
        "5m": fetch_and_store_market_candles(
            db,
            user_id=user_id,
            client=client,
            contract_id=str(config.contract_id),
            symbol=config.symbol,
            live=False,
            start=start,
            end=now,
            unit="minute",
            unit_number=5,
            limit=limit,
            include_partial_bar=False,
        ),
        "SPY": fetch_and_store_market_candles(
            db,
            user_id=user_id,
            client=client,
            contract_id=benchmark_identifier,
            symbol=benchmark_symbol,
            live=False,
            start=start,
            end=now,
            unit="minute",
            unit_number=5,
            limit=limit,
            include_partial_bar=False,
        ),
    }


def fetch_and_store_support_resistance_candles(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    client: ProjectXClient,
    strategy_type: str = _STRATEGY_SUPPORT_RESISTANCE,
    strategy_params: dict[str, Any] | None = None,
) -> dict[str, list[ProjectXMarketCandle]]:
    params = _normalize_strategy_params(strategy_type, strategy_params)
    bars_per_timeframe = int(params["bars_per_timeframe"])
    now = datetime.now(timezone.utc)
    contract_id, symbol = resolve_market_contract(
        client,
        contract_id=str(config.contract_id),
        symbol=config.symbol,
        live=False,
    )

    candle_sets: dict[str, list[ProjectXMarketCandle]] = {}
    for label, unit, unit_number in (("4H", "hour", 4), ("1H", "hour", 1)):
        unit_seconds = _UNIT_SECONDS_BY_NAME[unit]
        lookback_seconds = unit_seconds * unit_number * bars_per_timeframe * 3
        start = now - timedelta(seconds=lookback_seconds)
        bars = client.retrieve_bars(
            contract_id=contract_id,
            live=False,
            start=start,
            end=now,
            unit=_PROJECTX_UNIT_BY_NAME[unit],
            unit_number=unit_number,
            limit=bars_per_timeframe,
            include_partial_bar=False,
        )
        candle_sets[label] = store_market_candles(
            db,
            user_id=user_id,
            contract_id=contract_id,
            symbol=symbol,
            live=False,
            unit=unit,
            unit_number=unit_number,
            bars=bars,
        )

    return candle_sets


def fetch_and_store_supertrend_pivot_candles(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    client: ProjectXClient,
    strategy_params: dict[str, Any] | None = None,
) -> dict[str, list[ProjectXMarketCandle]]:
    params = _normalize_strategy_params(_STRATEGY_SUPERTREND_PIVOT, strategy_params)
    lookback_bars = max(int(config.lookback_bars), int(params["supertrend_period"]) + int(params["chop_lookback_bars"]) + 10)
    daily_bars = int(params["daily_bars"])
    now = datetime.now(timezone.utc)
    contract_id, symbol = resolve_market_contract(
        client,
        contract_id=str(config.contract_id),
        symbol=config.symbol,
        live=False,
    )

    signal_unit = str(config.timeframe_unit)
    signal_unit_number = int(config.timeframe_unit_number)
    signal_unit_seconds = _UNIT_SECONDS_BY_NAME[signal_unit]
    signal_lookback_seconds = signal_unit_seconds * signal_unit_number * lookback_bars * 3
    signal_start = now - timedelta(seconds=signal_lookback_seconds)
    signal_bars = client.retrieve_bars(
        contract_id=contract_id,
        live=False,
        start=signal_start,
        end=now,
        unit=_PROJECTX_UNIT_BY_NAME[signal_unit],
        unit_number=signal_unit_number,
        limit=lookback_bars,
        include_partial_bar=False,
    )

    daily_lookback_seconds = _UNIT_SECONDS_BY_NAME["day"] * daily_bars * 3
    daily_start = now - timedelta(seconds=daily_lookback_seconds)
    daily_bars_payload = client.retrieve_bars(
        contract_id=contract_id,
        live=False,
        start=daily_start,
        end=now,
        unit=_PROJECTX_UNIT_BY_NAME["day"],
        unit_number=1,
        limit=daily_bars,
        include_partial_bar=False,
    )

    return {
        "signal": store_market_candles(
            db,
            user_id=user_id,
            contract_id=contract_id,
            symbol=symbol,
            live=False,
            unit=signal_unit,
            unit_number=signal_unit_number,
            bars=signal_bars,
        ),
        "1D": store_market_candles(
            db,
            user_id=user_id,
            contract_id=contract_id,
            symbol=symbol,
            live=False,
            unit="day",
            unit_number=1,
            bars=daily_bars_payload,
        ),
    }


def fetch_and_store_delayed_orb_candles(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    client: ProjectXClient,
    strategy_params: dict[str, Any] | None = None,
) -> dict[str, list[ProjectXMarketCandle]]:
    params = _normalize_strategy_params(_STRATEGY_DELAYED_ORB_CONFIRMATION, strategy_params)
    now = datetime.now(timezone.utc)
    session_start = _session_start_utc_for_reference(now, str(config.trading_start_time))
    opening_range_minutes = int(params["opening_range_minutes"])
    confirmation_minutes = int(params["confirmation_minutes"])
    contract_id, symbol = resolve_market_contract(
        client,
        contract_id=str(config.contract_id),
        symbol=config.symbol,
        live=False,
    )
    intraday_bars: list[dict[str, Any]] = []
    if session_start <= now:
        minimum_required_bars = opening_range_minutes + confirmation_minutes + 5
        minutes_since_session_start = max(0, int((now - session_start).total_seconds() // 60) + 1)
        limit = max(minimum_required_bars, minutes_since_session_start + 5)
        intraday_bars = client.retrieve_bars(
            contract_id=contract_id,
            live=False,
            start=session_start,
            end=now,
            unit=_PROJECTX_UNIT_BY_NAME["minute"],
            unit_number=1,
            limit=limit,
            include_partial_bar=False,
        )
    return {
        "1m": store_market_candles(
            db,
            user_id=user_id,
            contract_id=contract_id,
            symbol=symbol,
            live=False,
            unit="minute",
            unit_number=1,
            bars=intraday_bars,
        ),
        "1D": [],
    }


def fetch_and_store_orb_fibonacci_candles(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    client: ProjectXClient,
    strategy_params: dict[str, Any] | None = None,
) -> list[ProjectXMarketCandle]:
    params = _normalize_strategy_params(_STRATEGY_ORB_FIBONACCI_PULLBACK, strategy_params)
    unit = str(config.timeframe_unit).strip().lower()
    unit_number = max(1, int(config.timeframe_unit_number))
    if unit != "minute":
        return fetch_and_store_candles(db, user_id=user_id, config=config, client=client)

    now = datetime.now(timezone.utc)
    session_start = _session_start_utc_for_reference(now, str(config.trading_start_time))
    if session_start > now:
        return []
    opening_range_bars = math.ceil(int(params["opening_range_minutes"]) / unit_number)
    minimum_required_bars = opening_range_bars + int(params["swing_lookback_bars"]) + 10
    minutes_since_session_start = max(0, int((now - session_start).total_seconds() // 60) + 1)
    bars_since_session_start = math.ceil(minutes_since_session_start / unit_number)
    limit = max(int(config.lookback_bars), minimum_required_bars, bars_since_session_start + 5)
    contract_id, symbol = resolve_market_contract(
        client,
        contract_id=str(config.contract_id),
        symbol=config.symbol,
        live=False,
    )
    bars = client.retrieve_bars(
        contract_id=contract_id,
        live=False,
        start=session_start,
        end=now,
        unit=_PROJECTX_UNIT_BY_NAME["minute"],
        unit_number=unit_number,
        limit=limit,
        include_partial_bar=False,
    )
    return store_market_candles(
        db,
        user_id=user_id,
        contract_id=contract_id,
        symbol=symbol,
        live=False,
        unit="minute",
        unit_number=unit_number,
        bars=bars,
    )


def fetch_and_store_market_candles(
    db: Session,
    *,
    user_id: str,
    client: ProjectXClient,
    contract_id: str,
    symbol: str | None,
    live: bool,
    start: datetime,
    end: datetime,
    unit: str,
    unit_number: int,
    limit: int,
    include_partial_bar: bool = False,
) -> list[ProjectXMarketCandle]:
    normalized_unit = str(unit).strip().lower()
    if normalized_unit not in _PROJECTX_UNIT_BY_NAME:
        raise ValueError("unsupported candle unit")
    if start > end:
        raise ValueError("start must be before end")
    resolved_contract_id, resolved_symbol = resolve_market_contract(
        client,
        contract_id=contract_id,
        symbol=symbol,
        live=live,
    )
    bars = client.retrieve_bars(
        contract_id=resolved_contract_id,
        live=live,
        start=start,
        end=end,
        unit=_PROJECTX_UNIT_BY_NAME[normalized_unit],
        unit_number=unit_number,
        limit=limit,
        include_partial_bar=include_partial_bar,
    )
    return store_market_candles(
        db,
        user_id=user_id,
        contract_id=resolved_contract_id,
        symbol=resolved_symbol,
        live=live,
        unit=normalized_unit,
        unit_number=unit_number,
        bars=bars,
    )


def list_market_candles(
    db: Session,
    *,
    user_id: str,
    contract_id: str,
    live: bool,
    start: datetime,
    end: datetime,
    unit: str,
    unit_number: int,
    limit: int,
    include_partial_bar: bool = False,
) -> list[ProjectXMarketCandle]:
    normalized_unit = str(unit).strip().lower()
    if normalized_unit not in _PROJECTX_UNIT_BY_NAME:
        raise ValueError("unsupported candle unit")
    if start > end:
        raise ValueError("start must be before end")

    query = (
        db.query(ProjectXMarketCandle)
        .filter(ProjectXMarketCandle.user_id == user_id)
        .filter(ProjectXMarketCandle.contract_id == contract_id)
        .filter(ProjectXMarketCandle.live == bool(live))
        .filter(ProjectXMarketCandle.unit == normalized_unit)
        .filter(ProjectXMarketCandle.unit_number == unit_number)
        .filter(ProjectXMarketCandle.candle_timestamp >= _as_utc(start))
        .filter(ProjectXMarketCandle.candle_timestamp <= _as_utc(end))
    )
    if not include_partial_bar:
        query = query.filter(ProjectXMarketCandle.is_partial.is_(False))

    rows = (
        query.order_by(ProjectXMarketCandle.candle_timestamp.desc())
        .limit(max(1, int(limit)))
        .all()
    )
    rows.sort(key=lambda row: _as_utc(row.candle_timestamp))
    return rows


def prune_market_candle_cache_range(
    db: Session,
    *,
    user_id: str,
    contract_id: str,
    live: bool,
    start: datetime,
    end: datetime,
    unit: str,
    unit_number: int,
    keep_timestamps: Iterable[datetime],
) -> int:
    normalized_unit = str(unit).strip().lower()
    if normalized_unit not in _PROJECTX_UNIT_BY_NAME:
        raise ValueError("unsupported candle unit")
    if start > end:
        raise ValueError("start must be before end")

    query = (
        db.query(ProjectXMarketCandle)
        .filter(ProjectXMarketCandle.user_id == user_id)
        .filter(ProjectXMarketCandle.contract_id == contract_id)
        .filter(ProjectXMarketCandle.live == bool(live))
        .filter(ProjectXMarketCandle.unit == normalized_unit)
        .filter(ProjectXMarketCandle.unit_number == unit_number)
        .filter(ProjectXMarketCandle.candle_timestamp >= _as_utc(start))
        .filter(ProjectXMarketCandle.candle_timestamp <= _as_utc(end))
    )

    timestamps_to_keep = [_as_utc(timestamp) for timestamp in keep_timestamps]
    if timestamps_to_keep:
        query = query.filter(ProjectXMarketCandle.candle_timestamp.notin_(timestamps_to_keep))

    deleted = query.delete(synchronize_session=False)
    db.flush()
    return int(deleted or 0)


def market_candle_cache_needs_refresh(
    cached_candles: list[ProjectXMarketCandle],
    *,
    end: datetime,
    unit: str,
    unit_number: int,
    include_partial_bar: bool = False,
) -> bool:
    if not cached_candles:
        return True

    if market_candle_rows_are_stale(
        cached_candles,
        end=end,
        unit=unit,
        unit_number=unit_number,
        include_partial_bar=include_partial_bar,
    ):
        return True

    interval = _market_candle_interval(unit=unit, unit_number=unit_number)
    latest_timestamp = max(_as_utc(row.candle_timestamp) for row in cached_candles)
    end_utc = _as_utc(end)
    if latest_timestamp + interval <= end_utc:
        return _market_candle_tail_revalidation_due(cached_candles)
    return False


def market_candle_rows_are_stale(
    candles: list[ProjectXMarketCandle],
    *,
    end: datetime,
    unit: str,
    unit_number: int,
    include_partial_bar: bool = False,
) -> bool:
    if not candles:
        return True

    interval = _market_candle_interval(unit=unit, unit_number=unit_number)
    latest_timestamp = max(_as_utc(row.candle_timestamp) for row in candles)
    end_utc = _as_utc(end)
    if include_partial_bar:
        return latest_timestamp + interval <= end_utc
    return latest_timestamp + interval + interval <= end_utc


def market_candle_cache_covers_request(
    cached_candles: list[ProjectXMarketCandle],
    *,
    start: datetime,
    unit: str,
    unit_number: int,
    limit: int,
) -> bool:
    if not cached_candles:
        return False

    normalized_limit = max(1, int(limit))
    if len(cached_candles) >= normalized_limit:
        return True

    interval = _market_candle_interval(unit=unit, unit_number=unit_number)
    earliest_timestamp = min(_as_utc(row.candle_timestamp) for row in cached_candles)
    return earliest_timestamp <= _as_utc(start) + interval


def next_market_candle_fetch_start(
    cached_candles: list[ProjectXMarketCandle],
    *,
    start: datetime,
    unit: str,
    unit_number: int,
) -> datetime:
    start_utc = _as_utc(start)
    if not cached_candles:
        return start_utc

    interval = _market_candle_interval(unit=unit, unit_number=unit_number)
    latest_timestamp = max(_as_utc(row.candle_timestamp) for row in cached_candles)
    overlap = interval * _MARKET_CANDLE_TAIL_REVALIDATION_BARS
    return max(start_utc, latest_timestamp - overlap)


def _market_candle_tail_revalidation_due(cached_candles: list[ProjectXMarketCandle]) -> bool:
    latest_timestamp = max(_as_utc(row.candle_timestamp) for row in cached_candles)
    fetched_values = [
        row.fetched_at
        for row in cached_candles
        if _as_utc(row.candle_timestamp) == latest_timestamp and row.fetched_at is not None
    ]
    if not fetched_values:
        return True

    newest_fetch = max(_as_utc(value) for value in fetched_values)
    return newest_fetch + _MARKET_CANDLE_TAIL_REVALIDATION_TTL <= datetime.now(timezone.utc)


def resolve_market_contract(
    client: ProjectXClient,
    *,
    contract_id: str,
    symbol: str | None,
    live: bool,
) -> tuple[str, str | None]:
    normalized_contract_id = str(contract_id).strip()
    normalized_symbol = _normalized_optional_text(symbol)
    if _looks_like_projectx_contract_id(normalized_contract_id):
        return normalized_contract_id, normalized_symbol

    candidates = _unique_text_values([normalized_contract_id, normalized_symbol])
    for candidate in candidates:
        rows = client.search_contracts(search_text=candidate, live=live)
        resolved = _pick_market_contract(rows)
        if resolved is None:
            continue
        resolved_id = _normalized_optional_text(resolved.get("id"))
        if resolved_id is None:
            continue
        resolved_symbol = _normalized_optional_text(resolved.get("symbol_id")) or normalized_symbol
        return resolved_id, resolved_symbol

    return normalized_contract_id, normalized_symbol


def store_market_candles(
    db: Session,
    *,
    user_id: str,
    contract_id: str,
    symbol: str | None,
    live: bool,
    unit: str,
    unit_number: int,
    bars: Iterable[dict[str, Any]],
) -> list[ProjectXMarketCandle]:
    normalized = _dedupe_market_candle_bars(bars)
    if not normalized:
        return []

    timestamps = [bar["timestamp"] for bar in normalized]
    fetched_at = datetime.now(timezone.utc)

    if _session_dialect_name(db) == "postgresql":
        _upsert_market_candle_rows(
            db,
            values=[
                _market_candle_insert_values(
                    user_id=user_id,
                    contract_id=contract_id,
                    symbol=symbol,
                    live=live,
                    unit=unit,
                    unit_number=unit_number,
                    bar=bar,
                    fetched_at=fetched_at,
                )
                for bar in normalized
            ],
        )
        return _query_market_candles_by_timestamps(
            db,
            user_id=user_id,
            contract_id=contract_id,
            live=live,
            unit=unit,
            unit_number=unit_number,
            timestamps=timestamps,
        )

    existing_rows = (
        db.query(ProjectXMarketCandle)
        .filter(ProjectXMarketCandle.user_id == user_id)
        .filter(ProjectXMarketCandle.contract_id == contract_id)
        .filter(ProjectXMarketCandle.live == bool(live))
        .filter(ProjectXMarketCandle.unit == unit)
        .filter(ProjectXMarketCandle.unit_number == unit_number)
        .filter(ProjectXMarketCandle.candle_timestamp >= min(timestamps))
        .filter(ProjectXMarketCandle.candle_timestamp <= max(timestamps))
        .all()
    )
    existing_by_timestamp = {_as_utc(row.candle_timestamp): row for row in existing_rows}
    output: list[ProjectXMarketCandle] = []
    for bar in normalized:
        timestamp = bar["timestamp"]
        row = existing_by_timestamp.get(timestamp)
        if row is None:
            row = ProjectXMarketCandle(
                user_id=user_id,
                contract_id=contract_id,
                live=bool(live),
                unit=unit,
                unit_number=unit_number,
                candle_timestamp=timestamp,
            )
            db.add(row)
        row.symbol = symbol
        row.open_price = float(bar.get("open") or 0.0)
        row.high_price = float(bar.get("high") or 0.0)
        row.low_price = float(bar.get("low") or 0.0)
        row.close_price = float(bar.get("close") or 0.0)
        row.volume = float(bar.get("volume") or 0.0)
        row.is_partial = bool(bar.get("is_partial") or False)
        row.raw_payload = bar.get("raw_payload")
        row.fetched_at = fetched_at
        output.append(row)

    output.sort(key=lambda row: _as_utc(row.candle_timestamp))
    db.flush()
    return output


def _dedupe_market_candle_bars(bars: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_timestamp: dict[datetime, dict[str, Any]] = {}
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        timestamp = bar.get("timestamp")
        if not isinstance(timestamp, datetime):
            continue
        timestamp_utc = _as_utc(timestamp)
        by_timestamp[timestamp_utc] = {**bar, "timestamp": timestamp_utc}
    return [by_timestamp[timestamp] for timestamp in sorted(by_timestamp)]


def _session_dialect_name(db: Session) -> str:
    bind = db.get_bind()
    return str(bind.dialect.name) if bind is not None else ""


def _market_candle_insert_values(
    *,
    user_id: str,
    contract_id: str,
    symbol: str | None,
    live: bool,
    unit: str,
    unit_number: int,
    bar: dict[str, Any],
    fetched_at: datetime,
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "contract_id": contract_id,
        "symbol": symbol,
        "live": bool(live),
        "unit": unit,
        "unit_number": unit_number,
        "candle_timestamp": bar["timestamp"],
        "open_price": float(bar.get("open") or 0.0),
        "high_price": float(bar.get("high") or 0.0),
        "low_price": float(bar.get("low") or 0.0),
        "close_price": float(bar.get("close") or 0.0),
        "volume": float(bar.get("volume") or 0.0),
        "is_partial": bool(bar.get("is_partial") or False),
        "raw_payload": bar.get("raw_payload"),
        "fetched_at": fetched_at,
    }


def _upsert_market_candle_rows(db: Session, *, values: list[dict[str, Any]]) -> None:
    if not values:
        return

    table = ProjectXMarketCandle.__table__
    insert_stmt = postgresql_insert(table).values(values)
    excluded = insert_stmt.excluded
    db.execute(
        insert_stmt.on_conflict_do_update(
            index_elements=[
                table.c.user_id,
                table.c.contract_id,
                table.c.live,
                table.c.unit,
                table.c.unit_number,
                table.c.candle_timestamp,
            ],
            set_={
                "symbol": excluded.symbol,
                "open_price": excluded.open_price,
                "high_price": excluded.high_price,
                "low_price": excluded.low_price,
                "close_price": excluded.close_price,
                "volume": excluded.volume,
                "is_partial": excluded.is_partial,
                "raw_payload": excluded.raw_payload,
                "fetched_at": excluded.fetched_at,
            },
        )
    )


def _query_market_candles_by_timestamps(
    db: Session,
    *,
    user_id: str,
    contract_id: str,
    live: bool,
    unit: str,
    unit_number: int,
    timestamps: list[datetime],
) -> list[ProjectXMarketCandle]:
    rows = (
        db.query(ProjectXMarketCandle)
        .populate_existing()
        .filter(ProjectXMarketCandle.user_id == user_id)
        .filter(ProjectXMarketCandle.contract_id == contract_id)
        .filter(ProjectXMarketCandle.live == bool(live))
        .filter(ProjectXMarketCandle.unit == unit)
        .filter(ProjectXMarketCandle.unit_number == unit_number)
        .filter(ProjectXMarketCandle.candle_timestamp.in_(timestamps))
        .all()
    )
    rows.sort(key=lambda row: _as_utc(row.candle_timestamp))
    return rows


def evaluate_sma_cross(
    candles: list[ProjectXMarketCandle],
    *,
    fast_period: int,
    slow_period: int,
) -> SignalResult:
    _validate_strategy_periods(fast_period, slow_period)
    closed = [candle for candle in candles if not bool(candle.is_partial)]
    closed.sort(key=lambda candle: _as_utc(candle.candle_timestamp))
    if len(closed) < slow_period + 1:
        return SignalResult(
            action="HOLD",
            reason=f"Need at least {slow_period + 1} closed candles; found {len(closed)}.",
            candle_timestamp=_as_utc(closed[-1].candle_timestamp) if closed else None,
            price=float(closed[-1].close_price) if closed else None,
            raw_payload={"fast_period": fast_period, "slow_period": slow_period, "closed_count": len(closed)},
        )

    closes = [float(candle.close_price) for candle in closed]
    previous_fast = _average(closes[-fast_period - 1 : -1])
    previous_slow = _average(closes[-slow_period - 1 : -1])
    current_fast = _average(closes[-fast_period:])
    current_slow = _average(closes[-slow_period:])
    latest = closed[-1]

    action = "HOLD"
    if previous_fast <= previous_slow and current_fast > current_slow:
        action = "BUY"
    elif previous_fast >= previous_slow and current_fast < current_slow:
        action = "SELL"

    if action == "HOLD":
        reason = "No SMA crossover on the latest closed candle."
    else:
        reason = f"{fast_period}/{slow_period} SMA crossover generated {action}."

    return SignalResult(
        action=action,
        reason=reason,
        candle_timestamp=_as_utc(latest.candle_timestamp),
        price=float(latest.close_price),
        raw_payload={
            "fast_period": fast_period,
            "slow_period": slow_period,
            "previous_fast": previous_fast,
            "previous_slow": previous_slow,
            "current_fast": current_fast,
            "current_slow": current_slow,
        },
    )


def evaluate_ema_scalping(
    candles: list[ProjectXMarketCandle],
    *,
    fast_period: int,
    slow_period: int,
) -> SignalResult:
    _validate_strategy_periods(fast_period, slow_period)
    closed = _closed_candles(candles)
    latest = closed[-1] if closed else None
    latest_timestamp = _as_utc(latest.candle_timestamp) if latest is not None else None
    latest_price = float(latest.close_price) if latest is not None else None
    minimum_required = slow_period + 1
    raw_payload: dict[str, Any] = {
        "strategy_type": _STRATEGY_EMA_SCALPING,
        "fast_period": fast_period,
        "slow_period": slow_period,
        "closed_count": len(closed),
        "exit_on_opposite_candle": True,
    }
    if len(closed) < minimum_required:
        return SignalResult(
            action="HOLD",
            reason=f"Need at least {minimum_required} closed candles; found {len(closed)}.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    assert latest is not None
    closes = [float(candle.close_price) for candle in closed]
    fast_ema = _ema_series(closes, period=fast_period)
    slow_ema = _ema_series(closes, period=slow_period)
    previous_fast = fast_ema[-2]
    previous_slow = slow_ema[-2]
    current_fast = fast_ema[-1]
    current_slow = slow_ema[-1]
    price = float(latest.close_price)
    ema_gap_percent = _absolute_percent_delta(current_fast, current_slow, reference=price)
    fast_slope_percent = _absolute_percent_delta(current_fast, previous_fast, reference=price)
    slow_slope_percent = _absolute_percent_delta(current_slow, previous_slow, reference=price)
    raw_payload.update(
        {
            "fast_ema": current_fast,
            "slow_ema": current_slow,
            "previous_fast_ema": previous_fast,
            "previous_slow_ema": previous_slow,
            "ema_gap_percent": ema_gap_percent,
            "fast_slope_percent": fast_slope_percent,
            "slow_slope_percent": slow_slope_percent,
        }
    )

    if current_fast == current_slow or ema_gap_percent < _EMA_SCALPING_MIN_GAP_PERCENT:
        return SignalResult(
            action="HOLD",
            reason=(
                "9/15 EMA scalping skipped because EMA alignment is too flat "
                f"({_format_percent(ema_gap_percent)}% gap)."
            ),
            candle_timestamp=_as_utc(latest.candle_timestamp),
            price=price,
            raw_payload=raw_payload,
        )

    if max(fast_slope_percent, slow_slope_percent) < _EMA_SCALPING_MIN_SLOPE_PERCENT:
        return SignalResult(
            action="HOLD",
            reason=(
                "9/15 EMA scalping skipped because EMA slope is too flat "
                f"({_format_percent(max(fast_slope_percent, slow_slope_percent))}% slope)."
            ),
            candle_timestamp=_as_utc(latest.candle_timestamp),
            price=price,
            raw_payload=raw_payload,
        )

    pattern = _classify_ema_signal_candle(latest)
    if pattern is None:
        return SignalResult(
            action="HOLD",
            reason="Latest candle did not print a strong body, pin bar, or marubozu setup.",
            candle_timestamp=_as_utc(latest.candle_timestamp),
            price=price,
            raw_payload=raw_payload,
        )

    pattern_name, pattern_label, pattern_bias = pattern
    raw_payload["signal_candle_pattern"] = pattern_name
    bullish_trend = current_fast > current_slow
    if bullish_trend and pattern_bias != "bullish":
        return SignalResult(
            action="HOLD",
            reason="Bullish EMA alignment is present, but the signal candle is not bullish.",
            candle_timestamp=_as_utc(latest.candle_timestamp),
            price=price,
            raw_payload=raw_payload,
        )
    if not bullish_trend and pattern_bias != "bearish":
        return SignalResult(
            action="HOLD",
            reason="Bearish EMA alignment is present, but the signal candle is not bearish.",
            candle_timestamp=_as_utc(latest.candle_timestamp),
            price=price,
            raw_payload=raw_payload,
        )

    if bullish_trend:
        action = "BUY"
        stop_loss = float(latest.low_price)
        risk = price - stop_loss
        take_profit = price + risk * _EMA_SCALPING_REWARD_MULTIPLE
    else:
        action = "SELL"
        stop_loss = float(latest.high_price)
        risk = stop_loss - price
        take_profit = price - risk * _EMA_SCALPING_REWARD_MULTIPLE

    raw_payload.update(
        {
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk": risk,
            "take_profit_r_multiple": _EMA_SCALPING_REWARD_MULTIPLE,
        }
    )
    if risk <= 0:
        return SignalResult(
            action="HOLD",
            reason="Signal candle produced a non-positive risk distance.",
            candle_timestamp=_as_utc(latest.candle_timestamp),
            price=price,
            raw_payload=raw_payload,
        )

    return SignalResult(
        action=action,
        reason=(
            f"{action} on {pattern_label} with {fast_period}/{slow_period} EMA alignment. "
            f"SL {_format_strategy_price(stop_loss)}, TP {_format_strategy_price(take_profit)} "
            f"({_format_percent(_EMA_SCALPING_REWARD_MULTIPLE)}R) or exit on a strong opposite candle."
        ),
        candle_timestamp=_as_utc(latest.candle_timestamp),
        price=price,
        raw_payload=raw_payload,
    )


def evaluate_relative_strength_vs_spy(
    *,
    asset_candles: list[ProjectXMarketCandle],
    benchmark_candles: list[ProjectXMarketCandle],
    strategy_params: dict[str, Any] | None = None,
) -> SignalResult:
    params = _normalize_strategy_params(_STRATEGY_RELATIVE_STRENGTH_SPY, strategy_params)
    comparison_bars = int(params["comparison_bars"])
    pullback_lookback_bars = int(params["pullback_lookback_bars"])
    relative_volume_period = int(params["relative_volume_period"])
    minimum_relative_volume = float(params["minimum_relative_volume"])
    minimum_relative_strength_percent = float(params["minimum_relative_strength_percent"])
    minimum_benchmark_move_percent = float(params["minimum_benchmark_move_percent"])
    ema_period = int(params["ema_period"])
    swing_window = int(params["swing_window"])
    major_level_lookback_bars = int(params["major_level_lookback_bars"])
    entry_level_tolerance_percent = float(params["entry_level_tolerance_percent"])
    stop_buffer_percent = float(params["stop_buffer_percent"])
    take_profit_r_multiple = float(params["take_profit_r_multiple"])

    asset_closed = _closed_candles(asset_candles)
    benchmark_closed = _closed_candles(benchmark_candles)
    aligned_asset, aligned_benchmark = _align_candles_by_timestamp(asset_closed, benchmark_closed)
    latest = aligned_asset[-1] if aligned_asset else None
    latest_timestamp = _as_utc(latest.candle_timestamp) if latest is not None else None
    latest_price = float(latest.close_price) if latest is not None else None
    minimum_required = max(comparison_bars + 1, pullback_lookback_bars + 1, relative_volume_period + 1, ema_period, 25)
    raw_payload: dict[str, Any] = {
        "strategy_type": _STRATEGY_RELATIVE_STRENGTH_SPY,
        "settings": params,
        "closed_counts": {
            "asset": len(asset_closed),
            "benchmark": len(benchmark_closed),
            "aligned": len(aligned_asset),
        },
    }
    if len(aligned_asset) < minimum_required or len(aligned_benchmark) < minimum_required:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Need at least {minimum_required} aligned closed 5-minute candles for the symbol and SPY; "
                f"found {len(aligned_asset)}."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    assert latest is not None
    asset_closes = [float(candle.close_price) for candle in aligned_asset]
    benchmark_closes = [float(candle.close_price) for candle in aligned_benchmark]
    price = float(latest.close_price)
    asset_move_percent = _percent_change(asset_closes[-comparison_bars - 1], asset_closes[-1])
    benchmark_move_percent = _percent_change(benchmark_closes[-comparison_bars - 1], benchmark_closes[-1])
    relative_strength_gap = asset_move_percent - benchmark_move_percent
    asset_recent_move_percent = _percent_change(asset_closes[-pullback_lookback_bars - 1], asset_closes[-1])
    benchmark_recent_move_percent = _percent_change(benchmark_closes[-pullback_lookback_bars - 1], benchmark_closes[-1])
    relative_volume = _relative_volume_ratio(aligned_asset, lookback_bars=relative_volume_period)
    ema_value = _ema_series(asset_closes, period=ema_period)[-1]
    _session_keys, session_vwaps = _session_vwap_values(aligned_asset)
    session_vwap = session_vwaps[-1]
    recent_levels = aligned_asset[-max(major_level_lookback_bars, swing_window + 2) :]
    raw_levels = _detect_support_resistance_levels(recent_levels, timeframe="5m", window_size=swing_window)
    supports = _filter_clustered_levels(
        [level for level in raw_levels if level.side == "support"],
        tolerance_percent=entry_level_tolerance_percent,
    )
    resistances = _filter_clustered_levels(
        [level for level in raw_levels if level.side == "resistance"],
        tolerance_percent=entry_level_tolerance_percent,
    )
    nearest_support = _nearest_directional_level(supports, price=price, side="support")
    nearest_resistance = _nearest_directional_level(resistances, price=price, side="resistance")
    long_entry = _nearest_entry_reference(
        candle=latest,
        price=price,
        side="BUY",
        tolerance_percent=entry_level_tolerance_percent,
        candidates=[
            ("VWAP", session_vwap),
            (f"EMA {ema_period}", ema_value),
            ("support", nearest_support.price if nearest_support is not None else None),
        ],
    )
    short_entry = _nearest_entry_reference(
        candle=latest,
        price=price,
        side="SELL",
        tolerance_percent=entry_level_tolerance_percent,
        candidates=[
            ("VWAP", session_vwap),
            (f"EMA {ema_period}", ema_value),
            ("resistance", nearest_resistance.price if nearest_resistance is not None else None),
        ],
    )

    raw_payload.update(
        {
            "benchmark_symbol": params["benchmark_symbol"],
            "asset_move_percent": asset_move_percent,
            "benchmark_move_percent": benchmark_move_percent,
            "relative_strength_gap_percent": relative_strength_gap,
            "asset_recent_move_percent": asset_recent_move_percent,
            "benchmark_recent_move_percent": benchmark_recent_move_percent,
            "relative_volume": relative_volume,
            "session_vwap": session_vwap,
            "ema": ema_value,
            "support_levels": [_serialize_support_resistance_level(level) for level in supports],
            "resistance_levels": [_serialize_support_resistance_level(level) for level in resistances],
        }
    )

    if relative_volume < minimum_relative_volume:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Relative volume {_format_percent(relative_volume)}x is below the "
                f"{_format_percent(minimum_relative_volume)}x minimum."
            ),
            candle_timestamp=latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    if relative_strength_gap >= minimum_relative_strength_percent:
        if benchmark_recent_move_percent > -minimum_benchmark_move_percent:
            return SignalResult(
                action="HOLD",
                reason=(
                    f"Symbol outperformed SPY over {comparison_bars} candles, but SPY is not in a meaningful pullback "
                    f"({_format_percent(benchmark_recent_move_percent)}% over the last {pullback_lookback_bars} candles)."
                ),
                candle_timestamp=latest_timestamp,
                price=price,
                raw_payload=raw_payload,
            )
        if asset_recent_move_percent <= benchmark_recent_move_percent:
            return SignalResult(
                action="HOLD",
                reason="Symbol outperformed SPY overall, but it did not hold up better during the latest SPY pullback.",
                candle_timestamp=latest_timestamp,
                price=price,
                raw_payload=raw_payload,
            )
        if long_entry is None:
            return SignalResult(
                action="HOLD",
                reason="Long setup is present, but price is not pulling back to VWAP, EMA, or support.",
                candle_timestamp=latest_timestamp,
                price=price,
                raw_payload=raw_payload,
            )

        stop_anchor = _long_stop_anchor(
            latest=latest,
            support=nearest_support,
            session_vwap=session_vwap,
            ema_value=ema_value,
        )
        stop_loss = stop_anchor * (1 - stop_buffer_percent / 100)
        risk = price - stop_loss
        if risk <= 0:
            return SignalResult(
                action="HOLD",
                reason="Long setup was found, but the stop placement did not leave positive risk.",
                candle_timestamp=latest_timestamp,
                price=price,
                raw_payload=raw_payload,
            )
        take_profit_2r = price + risk * take_profit_r_multiple
        major_level = nearest_resistance.price if nearest_resistance is not None and nearest_resistance.price > price else None
        take_profit = min(take_profit_2r, major_level) if major_level is not None else take_profit_2r
        raw_payload.update(
            {
                "trigger_level": {"name": long_entry[0], "price": long_entry[1]},
                "entry_reference": {"name": long_entry[0], "price": long_entry[1]},
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "take_profit_2r": take_profit_2r,
                "major_level_price": major_level,
                "risk": risk,
            }
        )
        return SignalResult(
            action="BUY",
            reason=(
                f"BUY on relative strength vs SPY: {_format_percent(asset_move_percent)}% vs "
                f"{_format_percent(benchmark_move_percent)}% over {comparison_bars} candles, "
                f"RVOL {_format_percent(relative_volume)}x, entry at {long_entry[0]} "
                f"{_format_strategy_price(long_entry[1])}. SL {_format_strategy_price(stop_loss)}, "
                f"TP {_format_strategy_price(take_profit)}."
            ),
            candle_timestamp=latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    if relative_strength_gap <= -minimum_relative_strength_percent:
        if benchmark_recent_move_percent < minimum_benchmark_move_percent:
            return SignalResult(
                action="HOLD",
                reason=(
                    f"Symbol lagged SPY over {comparison_bars} candles, but SPY is not in a meaningful bounce "
                    f"({_format_percent(benchmark_recent_move_percent)}% over the last {pullback_lookback_bars} candles)."
                ),
                candle_timestamp=latest_timestamp,
                price=price,
                raw_payload=raw_payload,
            )
        if asset_recent_move_percent >= benchmark_recent_move_percent:
            return SignalResult(
                action="HOLD",
                reason="Symbol lagged SPY overall, but it bounced too much during the latest SPY rebound.",
                candle_timestamp=latest_timestamp,
                price=price,
                raw_payload=raw_payload,
            )
        if short_entry is None:
            return SignalResult(
                action="HOLD",
                reason="Short setup is present, but price is not bouncing into VWAP, EMA, or resistance.",
                candle_timestamp=latest_timestamp,
                price=price,
                raw_payload=raw_payload,
            )

        stop_anchor = _short_stop_anchor(
            latest=latest,
            resistance=nearest_resistance,
            session_vwap=session_vwap,
            ema_value=ema_value,
        )
        stop_loss = stop_anchor * (1 + stop_buffer_percent / 100)
        risk = stop_loss - price
        if risk <= 0:
            return SignalResult(
                action="HOLD",
                reason="Short setup was found, but the stop placement did not leave positive risk.",
                candle_timestamp=latest_timestamp,
                price=price,
                raw_payload=raw_payload,
            )
        take_profit_2r = price - risk * take_profit_r_multiple
        major_level = nearest_support.price if nearest_support is not None and nearest_support.price < price else None
        take_profit = max(take_profit_2r, major_level) if major_level is not None else take_profit_2r
        raw_payload.update(
            {
                "trigger_level": {"name": short_entry[0], "price": short_entry[1]},
                "entry_reference": {"name": short_entry[0], "price": short_entry[1]},
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "take_profit_2r": take_profit_2r,
                "major_level_price": major_level,
                "risk": risk,
            }
        )
        return SignalResult(
            action="SELL",
            reason=(
                f"SELL on relative weakness vs SPY: {_format_percent(asset_move_percent)}% vs "
                f"{_format_percent(benchmark_move_percent)}% over {comparison_bars} candles, "
                f"RVOL {_format_percent(relative_volume)}x, entry at {short_entry[0]} "
                f"{_format_strategy_price(short_entry[1])}. SL {_format_strategy_price(stop_loss)}, "
                f"TP {_format_strategy_price(take_profit)}."
            ),
            candle_timestamp=latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    return SignalResult(
        action="HOLD",
        reason=(
            f"Relative strength gap {_format_percent(relative_strength_gap)}% is smaller than the "
            f"{_format_percent(minimum_relative_strength_percent)}% threshold."
        ),
        candle_timestamp=latest_timestamp,
        price=price,
        raw_payload=raw_payload,
    )


def evaluate_ema_trend_pullback(
    candles: list[ProjectXMarketCandle],
    *,
    fast_period: int,
    slow_period: int,
    strategy_params: dict[str, Any] | None = None,
) -> SignalResult:
    effective_fast_period, effective_slow_period = _normalized_strategy_period_values(
        _STRATEGY_EMA_TREND_PULLBACK,
        fast_period=fast_period,
        slow_period=slow_period,
    )
    _validate_strategy_periods(effective_fast_period, effective_slow_period)
    params = _normalize_strategy_params(_STRATEGY_EMA_TREND_PULLBACK, strategy_params)
    closed = _closed_candles(candles)
    latest = closed[-1] if closed else None
    latest_timestamp = _as_utc(latest.candle_timestamp) if latest is not None else None
    latest_price = float(latest.close_price) if latest is not None else None
    rsi_period = int(params["rsi_period"])
    volume_average_period = int(params["volume_average_period"])
    swing_lookback_bars = int(params["swing_lookback_bars"])
    long_rsi_min = float(params["long_rsi_min"])
    long_rsi_max = float(params["long_rsi_max"])
    short_rsi_min = float(params["short_rsi_min"])
    short_rsi_max = float(params["short_rsi_max"])
    partial_r_multiple = float(params["partial_take_profit_r_multiple"])
    final_r_multiple = float(params["final_take_profit_r_multiple"])
    minimum_required = max(effective_slow_period, rsi_period + 1, volume_average_period + 1, swing_lookback_bars)
    raw_payload: dict[str, Any] = {
        "strategy_type": _STRATEGY_EMA_TREND_PULLBACK,
        "settings": params,
        "fast_period": effective_fast_period,
        "slow_period": effective_slow_period,
        "closed_count": len(closed),
    }
    if len(closed) < minimum_required:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Need at least {minimum_required} closed candles for the EMA trend pullback setup; "
                f"found {len(closed)}."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    assert latest is not None
    closes = [float(candle.close_price) for candle in closed]
    fast_ema = _ema_series(closes, effective_fast_period)
    slow_ema = _ema_series(closes, effective_slow_period)
    latest_fast_ema = fast_ema[-1]
    latest_slow_ema = slow_ema[-1]
    if any(value is None for value in [latest_fast_ema, latest_slow_ema]):
        return SignalResult(
            action="HOLD",
            reason="EMA series is not fully initialized for the latest closed candle.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    latest_open = float(latest.open_price)
    latest_high = float(latest.high_price)
    latest_low = float(latest.low_price)
    latest_close = float(latest.close_price)
    latest_volume = float(latest.volume or 0.0)
    fast_ema_value = float(latest_fast_ema)
    slow_ema_value = float(latest_slow_ema)
    latest_rsi = _rsi_series(closes, period=rsi_period)[-1]
    recent_window = closed[-swing_lookback_bars:]
    recent_swing_low = min(float(candle.low_price) for candle in recent_window)
    recent_swing_high = max(float(candle.high_price) for candle in recent_window)
    previous_volumes = [float(candle.volume or 0.0) for candle in closed[-volume_average_period - 1 : -1]]
    average_volume = _average(previous_volumes)
    volume_ratio = latest_volume / average_volume if average_volume > 0 else None
    touches_fast_ema = latest_low <= fast_ema_value <= latest_high
    is_bullish_candle = latest_close > latest_open
    is_bearish_candle = latest_close < latest_open
    long_trend = latest_close > fast_ema_value > slow_ema_value
    short_trend = latest_close < fast_ema_value < slow_ema_value
    long_rsi_ok = latest_rsi is not None and long_rsi_min <= latest_rsi <= long_rsi_max
    short_rsi_ok = latest_rsi is not None and short_rsi_min <= latest_rsi <= short_rsi_max
    volume_above_average = latest_volume > average_volume if average_volume > 0 else False

    raw_payload.update(
        {
            "fast_ema": fast_ema_value,
            "slow_ema": slow_ema_value,
            "rsi": latest_rsi,
            "average_volume": average_volume,
            "volume_ratio": volume_ratio,
            "latest_candle": {
                "timestamp": _as_utc(latest.candle_timestamp).isoformat(),
                "open": latest_open,
                "high": latest_high,
                "low": latest_low,
                "close": latest_close,
                "volume": latest_volume,
            },
            "recent_swing_low": recent_swing_low,
            "recent_swing_high": recent_swing_high,
            "conditions": {
                "touches_fast_ema": touches_fast_ema,
                "is_bullish_candle": is_bullish_candle,
                "is_bearish_candle": is_bearish_candle,
                "long_trend": long_trend,
                "short_trend": short_trend,
                "long_rsi_ok": long_rsi_ok,
                "short_rsi_ok": short_rsi_ok,
                "volume_above_average": volume_above_average,
            },
        }
    )

    if latest_rsi is None:
        return SignalResult(
            action="HOLD",
            reason=f"Need at least {rsi_period + 1} closes to calculate RSI.",
            candle_timestamp=latest_timestamp,
            price=latest_close,
            raw_payload=raw_payload,
        )

    if average_volume <= 0:
        return SignalResult(
            action="HOLD",
            reason="Average volume is not positive, so the pullback volume filter cannot be evaluated.",
            candle_timestamp=latest_timestamp,
            price=latest_close,
            raw_payload=raw_payload,
        )

    if long_trend and touches_fast_ema and is_bullish_candle and long_rsi_ok and volume_above_average:
        stop_loss = min(recent_swing_low, slow_ema_value)
        risk = latest_close - stop_loss
        raw_payload.update(
            {
                "direction": "long",
                "entry_price": latest_close,
                "trigger_level": {
                    "kind": "ema_pullback",
                    "period": effective_fast_period,
                    "price": fast_ema_value,
                    "candle_touched": True,
                },
                "stop_reference": {
                    "swing_low": recent_swing_low,
                    "slow_ema": slow_ema_value,
                    "selected_anchor": stop_loss,
                },
            }
        )
        if risk <= 0:
            raw_payload["risk"] = risk
            return SignalResult(
                action="HOLD",
                reason="Long pullback conditions matched, but the stop anchor did not leave positive risk.",
                candle_timestamp=latest_timestamp,
                price=latest_close,
                raw_payload=raw_payload,
            )

        partial_take_profit = latest_close + risk * partial_r_multiple
        final_take_profit = latest_close + risk * final_r_multiple
        raw_payload.update(
            {
                "stop_loss": stop_loss,
                "risk": risk,
                "partial_take_profit": partial_take_profit,
                "take_profit": final_take_profit,
                "final_take_profit": final_take_profit,
            }
        )
        return SignalResult(
            action="BUY",
            reason=(
                f"BUY {effective_fast_period}/{effective_slow_period} EMA trend pullback. "
                f"RSI {latest_rsi:.1f}, volume {volume_ratio:.2f}x average, touch at "
                f"{_format_strategy_price(fast_ema_value)}. SL {_format_strategy_price(stop_loss)}, "
                f"TP1 {_format_strategy_price(partial_take_profit)} ({partial_r_multiple:g}R), "
                f"TP2 {_format_strategy_price(final_take_profit)} ({final_r_multiple:g}R)."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_close,
            raw_payload=raw_payload,
        )

    if short_trend and touches_fast_ema and is_bearish_candle and short_rsi_ok and volume_above_average:
        stop_loss = max(recent_swing_high, slow_ema_value)
        risk = stop_loss - latest_close
        raw_payload.update(
            {
                "direction": "short",
                "entry_price": latest_close,
                "trigger_level": {
                    "kind": "ema_pullback",
                    "period": effective_fast_period,
                    "price": fast_ema_value,
                    "candle_touched": True,
                },
                "stop_reference": {
                    "swing_high": recent_swing_high,
                    "slow_ema": slow_ema_value,
                    "selected_anchor": stop_loss,
                },
            }
        )
        if risk <= 0:
            raw_payload["risk"] = risk
            return SignalResult(
                action="HOLD",
                reason="Short pullback conditions matched, but the stop anchor did not leave positive risk.",
                candle_timestamp=latest_timestamp,
                price=latest_close,
                raw_payload=raw_payload,
            )

        partial_take_profit = latest_close - risk * partial_r_multiple
        final_take_profit = latest_close - risk * final_r_multiple
        raw_payload.update(
            {
                "stop_loss": stop_loss,
                "risk": risk,
                "partial_take_profit": partial_take_profit,
                "take_profit": final_take_profit,
                "final_take_profit": final_take_profit,
            }
        )
        return SignalResult(
            action="SELL",
            reason=(
                f"SELL {effective_fast_period}/{effective_slow_period} EMA trend pullback. "
                f"RSI {latest_rsi:.1f}, volume {volume_ratio:.2f}x average, touch at "
                f"{_format_strategy_price(fast_ema_value)}. SL {_format_strategy_price(stop_loss)}, "
                f"TP1 {_format_strategy_price(partial_take_profit)} ({partial_r_multiple:g}R), "
                f"TP2 {_format_strategy_price(final_take_profit)} ({final_r_multiple:g}R)."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_close,
            raw_payload=raw_payload,
        )

    return SignalResult(
        action="HOLD",
        reason=(
            f"No {effective_fast_period}/{effective_slow_period} EMA trend pullback setup on the latest closed candle. "
            "Trend, EMA touch, RSI range, candle direction, or volume confirmation was not aligned."
        ),
        candle_timestamp=latest_timestamp,
        price=latest_close,
        raw_payload=raw_payload,
    )


def evaluate_fvg_sweep_mss(
    *,
    fvg_candles: list[ProjectXMarketCandle],
    structure_candles: list[ProjectXMarketCandle],
    strategy_params: dict[str, Any] | None = None,
) -> SignalResult:
    params = _normalize_strategy_params(_STRATEGY_FVG_SWEEP_MSS, strategy_params)
    fvg_closed = _closed_candles(fvg_candles)
    structure_closed = _closed_candles(structure_candles)
    latest = structure_closed[-1] if structure_closed else (fvg_closed[-1] if fvg_closed else None)
    latest_timestamp = _as_utc(latest.candle_timestamp) if latest is not None else None
    latest_price = float(latest.close_price) if latest is not None else None
    swing_window = int(params["swing_window"])
    minimum_structure = max(5, swing_window + 2)
    raw_payload: dict[str, Any] = {
        "strategy_type": _STRATEGY_FVG_SWEEP_MSS,
        "settings": params,
        "closed_counts": {
            "fvg": len(fvg_closed),
            "structure": len(structure_closed),
        },
        "timeframes": {
            "fvg": _candles_timeframe_label(fvg_closed),
            "structure": _candles_timeframe_label(structure_closed),
        },
    }

    if len(fvg_closed) < 3 or len(structure_closed) < minimum_structure:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Need at least 3 FVG candles and {minimum_structure} structure candles; "
                f"found {len(fvg_closed)} and {len(structure_closed)}."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    gaps = _detect_fair_value_gaps(fvg_closed)
    raw_payload["fvg_count"] = len(gaps)
    if not gaps:
        return SignalResult(
            action="HOLD",
            reason="No bullish or bearish fair value gaps were detected in the current lookback window.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    swing_levels = _detect_support_resistance_levels(structure_closed, timeframe="LTF", window_size=swing_window)
    supports = [level for level in swing_levels if level.side == "support"]
    resistances = [level for level in swing_levels if level.side == "resistance"]
    raw_payload["swing_levels"] = {
        "supports": [_serialize_support_resistance_level(level) for level in supports],
        "resistances": [_serialize_support_resistance_level(level) for level in resistances],
    }

    first_hold: SignalResult | None = None
    for gap in reversed(gaps):
        candidate, triggered = _evaluate_single_fvg_gap(
            gap=gap,
            fvg_closed=fvg_closed,
            structure_closed=structure_closed,
            supports=supports,
            resistances=resistances,
            params=params,
            base_payload=raw_payload,
        )
        if candidate is None:
            continue
        if triggered:
            return candidate
        if first_hold is None:
            first_hold = candidate

    if first_hold is not None:
        return first_hold

    return SignalResult(
        action="HOLD",
        reason="No active FVG sweep + structure-shift setup is ready on the latest closed candle.",
        candle_timestamp=latest_timestamp,
        price=latest_price,
        raw_payload=raw_payload,
    )


def _evaluate_single_fvg_gap(
    *,
    gap: FairValueGapZone,
    fvg_closed: list[ProjectXMarketCandle],
    structure_closed: list[ProjectXMarketCandle],
    supports: list[SupportResistanceLevel],
    resistances: list[SupportResistanceLevel],
    params: dict[str, Any],
    base_payload: dict[str, Any],
) -> tuple[SignalResult | None, bool]:
    latest = structure_closed[-1] if structure_closed else None
    latest_timestamp = _as_utc(latest.candle_timestamp) if latest is not None else None
    latest_price = float(latest.close_price) if latest is not None else None
    gap_payload = {
        **base_payload,
        "fvg": _serialize_fair_value_gap(gap),
    }
    invalidation_candle = _find_fvg_invalidation_candle(
        candles=fvg_closed,
        gap=gap,
        volume_lookback_bars=int(params["volume_lookback_bars"]),
        strong_volume_multiplier=float(params["strong_volume_multiplier"]),
    )
    if invalidation_candle is not None and latest_timestamp is not None:
        invalidation_timestamp = _as_utc(invalidation_candle.candle_timestamp)
        if invalidation_timestamp <= latest_timestamp:
            gap_payload["invalidation_candle"] = _serialize_strategy_candle(invalidation_candle)
            return (
                SignalResult(
                    action="HOLD",
                    reason=(
                        f"{gap.side.title()} FVG {_format_strategy_price(gap.lower_price)}-"
                        f"{_format_strategy_price(gap.upper_price)} was invalidated by a strong-volume body close through the zone."
                    ),
                    candle_timestamp=latest_timestamp,
                    price=latest_price,
                    raw_payload=gap_payload,
                ),
                False,
            )

    structure_start_index = next(
        (index for index, candle in enumerate(structure_closed) if _as_utc(candle.candle_timestamp) > gap.timestamp),
        None,
    )
    if structure_start_index is None:
        return (
            SignalResult(
                action="HOLD",
                reason=(
                    f"{gap.side.title()} FVG {_format_strategy_price(gap.lower_price)}-"
                    f"{_format_strategy_price(gap.upper_price)} is fresh; waiting for lower-timeframe reaction candles."
                ),
                candle_timestamp=latest_timestamp,
                price=latest_price,
                raw_payload=gap_payload,
            ),
            False,
        )

    recent_swing_max_age = max(6, int(params["swing_window"]) * 4)
    sweep_index: int | None = None
    sweep_level: SupportResistanceLevel | None = None
    structure_level: SupportResistanceLevel | None = None
    touched_gap = False

    for index in range(len(structure_closed) - 1, structure_start_index - 1, -1):
        candle = structure_closed[index]
        if not _candle_intersects_fvg(candle, gap):
            continue
        touched_gap = True
        if gap.side == "bullish":
            level = _latest_prior_swing(
                levels=supports,
                before_index=index,
                max_age=recent_swing_max_age,
            )
            if level is None or float(candle.low_price) >= level.price:
                continue
            if not _has_rejection_wick(candle, side="bullish", gap=gap, reference_price=level.price):
                continue
            opposing = _latest_prior_swing(levels=resistances, before_index=index)
            fallback = _fallback_structure_level(structure_closed, start_index=structure_start_index, end_index=index, side="bullish")
        else:
            level = _latest_prior_swing(
                levels=resistances,
                before_index=index,
                max_age=recent_swing_max_age,
            )
            if level is None or float(candle.high_price) <= level.price:
                continue
            if not _has_rejection_wick(candle, side="bearish", gap=gap, reference_price=level.price):
                continue
            opposing = _latest_prior_swing(levels=supports, before_index=index)
            fallback = _fallback_structure_level(structure_closed, start_index=structure_start_index, end_index=index, side="bearish")
        sweep_index = index
        sweep_level = level
        structure_level = opposing or fallback
        if structure_level is not None:
            break

    if sweep_index is None or sweep_level is None or structure_level is None:
        if touched_gap:
            reason = (
                f"{gap.side.title()} FVG {_format_strategy_price(gap.lower_price)}-{_format_strategy_price(gap.upper_price)} "
                "was tapped, but no sweep-and-rejection candle has confirmed yet."
            )
        else:
            reason = (
                f"Waiting for price to trade back into {gap.side} FVG "
                f"{_format_strategy_price(gap.lower_price)}-{_format_strategy_price(gap.upper_price)}."
            )
        return (
            SignalResult(
                action="HOLD",
                reason=reason,
                candle_timestamp=latest_timestamp,
                price=latest_price,
                raw_payload=gap_payload,
            ),
            False,
        )

    gap_payload.update(
        {
            "sweep_level": _serialize_support_resistance_level(sweep_level),
            "structure_level": _serialize_support_resistance_level(structure_level),
            "sweep_candle": _serialize_strategy_candle(structure_closed[sweep_index]),
        }
    )

    breakout_index = _find_fvg_structure_break_index(
        candles=structure_closed,
        sweep_index=sweep_index,
        structure_level=structure_level,
        side=gap.side,
    )
    if breakout_index is None:
        return (
            SignalResult(
                action="HOLD",
                reason=(
                    f"{gap.side.title()} FVG sweep confirmed. Waiting for a lower-timeframe break "
                    f"{'above' if gap.side == 'bullish' else 'below'} {_format_strategy_price(structure_level.price)}."
                ),
                candle_timestamp=latest_timestamp,
                price=latest_price,
                raw_payload=gap_payload,
            ),
            False,
        )

    gap_payload["breakout_candle"] = _serialize_strategy_candle(structure_closed[breakout_index])
    if breakout_index != len(structure_closed) - 1:
        return (
            SignalResult(
                action="HOLD",
                reason=(
                    f"{gap.side.title()} FVG setup already broke structure at "
                    f"{_format_strategy_price(structure_level.price)} before the latest candle; waiting for a fresh setup."
                ),
                candle_timestamp=latest_timestamp,
                price=latest_price,
                raw_payload=gap_payload,
            ),
            False,
        )

    entry_price = float(structure_closed[breakout_index].close_price)
    stop_buffer_percent = float(params["stop_buffer_percent"])
    if gap.side == "bullish":
        action = "BUY"
        sweep_extreme = float(structure_closed[sweep_index].low_price)
        stop_loss = sweep_extreme * (1 - stop_buffer_percent / 100)
        risk = entry_price - stop_loss
        target_2r = entry_price + risk * 2
        target_3r = entry_price + risk * 3
        next_liquidity = _next_fvg_liquidity_target(levels=resistances, entry_price=entry_price, side="bullish")
    else:
        action = "SELL"
        sweep_extreme = float(structure_closed[sweep_index].high_price)
        stop_loss = sweep_extreme * (1 + stop_buffer_percent / 100)
        risk = stop_loss - entry_price
        target_2r = entry_price - risk * 2
        target_3r = entry_price - risk * 3
        next_liquidity = _next_fvg_liquidity_target(levels=supports, entry_price=entry_price, side="bearish")

    if risk <= 0:
        return (
            SignalResult(
                action="HOLD",
                reason="FVG sweep + structure shift triggered, but the calculated risk was not positive.",
                candle_timestamp=latest_timestamp,
                price=entry_price,
                raw_payload=gap_payload,
            ),
            False,
        )

    selected_target_mode = str(params["target_mode"])
    take_profit = target_2r
    effective_target_mode = _FVG_TARGET_MODE_2R
    if selected_target_mode == _FVG_TARGET_MODE_3R:
        take_profit = target_3r
        effective_target_mode = _FVG_TARGET_MODE_3R
    elif selected_target_mode == _FVG_TARGET_MODE_NEXT_LIQUIDITY and next_liquidity is not None:
        take_profit = next_liquidity
        effective_target_mode = _FVG_TARGET_MODE_NEXT_LIQUIDITY

    gap_payload.update(
        {
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk": risk,
            "selected_target_mode": effective_target_mode,
            "targets": {
                "2r": target_2r,
                "3r": target_3r,
                "next_liquidity": next_liquidity,
            },
        }
    )
    reason = (
        f"{action} after {gap.side} FVG reaction, liquidity sweep, and structure break "
        f"{'above' if gap.side == 'bullish' else 'below'} {_format_strategy_price(structure_level.price)}. "
        f"SL {_format_strategy_price(stop_loss)}, TP {_format_strategy_price(take_profit)} ({effective_target_mode})."
    )
    return (
        SignalResult(
            action=action,
            reason=reason,
            candle_timestamp=latest_timestamp,
            price=entry_price,
            raw_payload=gap_payload,
        ),
        True,
    )


def _detect_fair_value_gaps(candles: list[ProjectXMarketCandle]) -> list[FairValueGapZone]:
    gaps: list[FairValueGapZone] = []
    for index in range(2, len(candles)):
        left = candles[index - 2]
        right = candles[index]
        left_high = float(left.high_price)
        left_low = float(left.low_price)
        right_high = float(right.high_price)
        right_low = float(right.low_price)
        timestamp = _as_utc(right.candle_timestamp)

        if right_low > left_high:
            gaps.append(
                FairValueGapZone(
                    side="bullish",
                    lower_price=left_high,
                    upper_price=right_low,
                    timestamp=timestamp,
                    source_index=index,
                )
            )
        if right_high < left_low:
            gaps.append(
                FairValueGapZone(
                    side="bearish",
                    lower_price=right_high,
                    upper_price=left_low,
                    timestamp=timestamp,
                    source_index=index,
                )
            )
    return gaps


def _find_fvg_invalidation_candle(
    *,
    candles: list[ProjectXMarketCandle],
    gap: FairValueGapZone,
    volume_lookback_bars: int,
    strong_volume_multiplier: float,
) -> ProjectXMarketCandle | None:
    for index in range(gap.source_index + 1, len(candles)):
        candle = candles[index]
        if not _full_body_closes_through_fvg(candle, gap):
            continue
        start = max(0, index - volume_lookback_bars)
        prior_volumes = [float(row.volume) for row in candles[start:index] if float(row.volume) > 0]
        average_volume = _average(prior_volumes) if prior_volumes else 0.0
        if average_volume <= 0:
            continue
        if float(candle.volume) >= average_volume * strong_volume_multiplier:
            return candle
    return None


def _latest_prior_swing(
    *,
    levels: list[SupportResistanceLevel],
    before_index: int,
    max_age: int | None = None,
) -> SupportResistanceLevel | None:
    for level in reversed(levels):
        if level.source_index >= before_index:
            continue
        if max_age is not None and before_index - level.source_index > max_age:
            continue
        return level
    return None


def _fallback_structure_level(
    candles: list[ProjectXMarketCandle],
    *,
    start_index: int,
    end_index: int,
    side: str,
) -> SupportResistanceLevel | None:
    if end_index <= start_index:
        return None
    window = candles[start_index:end_index]
    if not window:
        return None
    if side == "bullish":
        price = max(float(candle.high_price) for candle in window)
        side_name = "resistance"
    else:
        price = min(float(candle.low_price) for candle in window)
        side_name = "support"
    source_index = next(
        index
        for index in range(start_index, end_index)
        if (
            side == "bullish"
            and float(candles[index].high_price) == price
        ) or (
            side == "bearish"
            and float(candles[index].low_price) == price
        )
    )
    return SupportResistanceLevel(
        side=side_name,
        price=price,
        timestamp=_as_utc(candles[source_index].candle_timestamp),
        timeframe="LTF-fallback",
        source_index=source_index,
        score=0.0,
    )


def _find_fvg_structure_break_index(
    *,
    candles: list[ProjectXMarketCandle],
    sweep_index: int,
    structure_level: SupportResistanceLevel,
    side: str,
) -> int | None:
    if sweep_index >= len(candles) - 1:
        return None
    for index in range(max(1, sweep_index + 1), len(candles)):
        previous_close = float(candles[index - 1].close_price)
        current_close = float(candles[index].close_price)
        if side == "bullish":
            if previous_close <= structure_level.price and current_close > structure_level.price:
                return index
        else:
            if previous_close >= structure_level.price and current_close < structure_level.price:
                return index
    return None


def _next_fvg_liquidity_target(
    *,
    levels: list[SupportResistanceLevel],
    entry_price: float,
    side: str,
) -> float | None:
    if side == "bullish":
        candidates = [level.price for level in levels if level.price > entry_price]
        return min(candidates) if candidates else None
    candidates = [level.price for level in levels if level.price < entry_price]
    return max(candidates) if candidates else None


def _candle_intersects_fvg(candle: ProjectXMarketCandle, gap: FairValueGapZone) -> bool:
    return float(candle.high_price) >= gap.lower_price and float(candle.low_price) <= gap.upper_price


def _full_body_closes_through_fvg(candle: ProjectXMarketCandle, gap: FairValueGapZone) -> bool:
    open_price = float(candle.open_price)
    close_price = float(candle.close_price)
    body_low = min(open_price, close_price)
    body_high = max(open_price, close_price)
    if gap.side == "bullish":
        return body_high < gap.lower_price
    return body_low > gap.upper_price


def _has_rejection_wick(
    candle: ProjectXMarketCandle,
    *,
    side: str,
    gap: FairValueGapZone,
    reference_price: float,
) -> bool:
    open_price = float(candle.open_price)
    close_price = float(candle.close_price)
    high_price = float(candle.high_price)
    low_price = float(candle.low_price)
    body = abs(close_price - open_price)
    candle_range = high_price - low_price
    if candle_range <= 0:
        return False
    if side == "bullish":
        lower_wick = min(open_price, close_price) - low_price
        upper_wick = high_price - max(open_price, close_price)
        return (
            close_price > reference_price
            and close_price >= gap.lower_price
            and lower_wick >= max(body, candle_range * 0.35)
            and lower_wick > upper_wick
        )
    upper_wick = high_price - max(open_price, close_price)
    lower_wick = min(open_price, close_price) - low_price
    return (
        close_price < reference_price
        and close_price <= gap.upper_price
        and upper_wick >= max(body, candle_range * 0.35)
        and upper_wick > lower_wick
    )


def _serialize_fair_value_gap(gap: FairValueGapZone) -> dict[str, Any]:
    return {
        "side": gap.side,
        "lower_price": gap.lower_price,
        "upper_price": gap.upper_price,
        "timestamp": gap.timestamp.isoformat(),
        "source_index": gap.source_index,
    }


def _serialize_strategy_candle(candle: ProjectXMarketCandle) -> dict[str, Any]:
    return {
        "timestamp": _as_utc(candle.candle_timestamp).isoformat(),
        "open": float(candle.open_price),
        "high": float(candle.high_price),
        "low": float(candle.low_price),
        "close": float(candle.close_price),
        "volume": float(candle.volume),
    }


def evaluate_pullback_trap_reversal(
    candles: list[ProjectXMarketCandle],
    *,
    fast_period: int,
    slow_period: int,
    strategy_params: dict[str, Any] | None = None,
) -> SignalResult:
    _validate_strategy_periods(fast_period, slow_period)
    params = _normalize_strategy_params(_STRATEGY_PULLBACK_TRAP_REVERSAL, strategy_params)
    closed = _closed_candles(candles)
    latest = closed[-1] if closed else None
    latest_timestamp = _as_utc(latest.candle_timestamp) if latest is not None else None
    latest_price = float(latest.close_price) if latest is not None else None

    pullback_lookback_bars = int(params["pullback_lookback_bars"])
    micro_level_window = int(params["micro_level_window"])
    volume_baseline_bars = int(params["volume_baseline_bars"])
    volume_spike_multiple = float(params["volume_spike_multiple"])
    wick_to_body_ratio_min = float(params["wick_to_body_ratio_min"])
    stop_buffer_percent = float(params["stop_buffer_percent"])
    reward_multiple = float(params["take_profit_r_multiple"])
    trend_confirmation_bars = int(params["trend_confirmation_bars"])
    min_countertrend_bars = int(params["min_countertrend_bars"])
    pullback_range_multiplier = float(params["pullback_range_multiplier"])
    prior_swing_window = int(params["prior_swing_window"])

    minimum_required = max(
        slow_period + trend_confirmation_bars,
        volume_baseline_bars + pullback_lookback_bars + 1,
        prior_swing_window + pullback_lookback_bars + 1,
    )
    if len(closed) < minimum_required:
        return SignalResult(
            action="HOLD",
            reason=f"Need at least {minimum_required} closed candles; found {len(closed)}.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload={
                "strategy_type": _STRATEGY_PULLBACK_TRAP_REVERSAL,
                "settings": params,
                "closed_count": len(closed),
                "fast_period": fast_period,
                "slow_period": slow_period,
            },
        )

    closes = [float(candle.close_price) for candle in closed]
    fast_ema = _ema_series(closes, fast_period)
    slow_ema = _ema_series(closes, slow_period)
    trend_direction = _determine_pullback_trap_trend_direction(
        fast_ema=fast_ema,
        slow_ema=slow_ema,
        closes=closes,
        confirmation_bars=trend_confirmation_bars,
    )
    latest = closed[-1]
    latest_open = float(latest.open_price)
    latest_high = float(latest.high_price)
    latest_low = float(latest.low_price)
    latest_close = float(latest.close_price)
    latest_volume = float(latest.volume or 0.0)
    candle_range = max(latest_high - latest_low, 0.0)
    body = abs(latest_close - latest_open)
    wick_reference = max(body, candle_range * 0.1, 1e-9)
    lower_wick = max(min(latest_open, latest_close) - latest_low, 0.0)
    upper_wick = max(latest_high - max(latest_open, latest_close), 0.0)

    pullback_window = closed[-pullback_lookback_bars - 1 : -1]
    prior_context = closed[-(prior_swing_window + pullback_lookback_bars + 1) : -(pullback_lookback_bars + 1)]
    volume_window = closed[-volume_baseline_bars - 1 : -1]
    baseline_window = closed[-(volume_baseline_bars + pullback_lookback_bars + 1) : -(pullback_lookback_bars + 1)]
    pullback_high = max(float(candle.high_price) for candle in pullback_window)
    pullback_low = min(float(candle.low_price) for candle in pullback_window)
    pullback_span = pullback_high - pullback_low
    average_range = _average([float(candle.high_price) - float(candle.low_price) for candle in baseline_window]) if baseline_window else 0.0
    average_volume = _average([float(candle.volume or 0.0) for candle in volume_window]) if volume_window else 0.0
    volume_multiple = latest_volume / average_volume if average_volume > 0 else 0.0
    micro_window = pullback_window[-micro_level_window:]
    fast_ema_latest = fast_ema[-1]
    slow_ema_latest = slow_ema[-1]

    raw_payload: dict[str, Any] = {
        "strategy_type": _STRATEGY_PULLBACK_TRAP_REVERSAL,
        "settings": params,
        "fast_period": fast_period,
        "slow_period": slow_period,
        "trend_direction": trend_direction,
        "fast_ema": fast_ema_latest,
        "slow_ema": slow_ema_latest,
        "latest_candle": {
            "timestamp": _as_utc(latest.candle_timestamp).isoformat(),
            "open": latest_open,
            "high": latest_high,
            "low": latest_low,
            "close": latest_close,
            "volume": latest_volume,
        },
        "pullback_high": pullback_high,
        "pullback_low": pullback_low,
        "pullback_span": pullback_span,
        "average_range": average_range,
        "average_volume": average_volume,
        "volume_multiple": volume_multiple,
        "lower_wick": lower_wick,
        "upper_wick": upper_wick,
        "body": body,
    }

    if trend_direction == "none":
        return SignalResult(
            action="HOLD",
            reason="Trend not confirmed by EMA alignment and slope.",
            candle_timestamp=_as_utc(latest.candle_timestamp),
            price=latest_close,
            raw_payload=raw_payload,
        )

    if trend_direction == "uptrend":
        countertrend_count = sum(float(candle.close_price) < float(candle.open_price) for candle in pullback_window)
        micro_level = max(max(float(candle.open_price), float(candle.close_price)) for candle in micro_window)
        wick_ratio = lower_wick / wick_reference
        prior_swing_target = max(float(candle.high_price) for candle in prior_context) if prior_context else None
        raw_payload.update(
            {
                "countertrend_bars": countertrend_count,
                "micro_level": micro_level,
                "wick_ratio": wick_ratio,
                "prior_swing_target": prior_swing_target,
                "trail_reference_ema": fast_ema_latest,
            }
        )
        if countertrend_count < min_countertrend_bars:
            reason = f"Need at least {min_countertrend_bars} bearish pullback candles; found {countertrend_count}."
        elif average_range > 0 and pullback_span < average_range * pullback_range_multiplier:
            reason = "Pullback was not sharp enough versus the recent average candle range."
        elif latest_close <= latest_open:
            reason = "Latest candle did not close bullish."
        elif latest_low >= pullback_low:
            reason = "Latest candle did not sweep the recent pullback low."
        elif lower_wick <= upper_wick or wick_ratio < wick_to_body_ratio_min:
            reason = f"Lower wick/body ratio {wick_ratio:.2f} is below the {wick_to_body_ratio_min:.2f} minimum."
        elif average_volume <= 0 or latest_volume < average_volume * volume_spike_multiple:
            reason = f"Volume spike requirement not met; latest volume is {volume_multiple:.2f}x baseline."
        elif latest_close <= micro_level or latest_low >= micro_level:
            reason = "Latest candle did not reclaim the recent micro level after the sweep."
        else:
            stop_loss = latest_low * (1 - stop_buffer_percent / 100)
            risk = latest_close - stop_loss
            take_profit = latest_close + risk * reward_multiple
            raw_payload.update(
                {
                    "stop_loss": stop_loss,
                    "risk": risk,
                    "take_profit": take_profit,
                }
            )
            if risk <= 0:
                reason = "Calculated wick stop did not produce positive risk."
            else:
                return SignalResult(
                    action="BUY",
                    reason=(
                        f"BUY on uptrend pullback trap above {_format_strategy_price(micro_level)} after sweeping "
                        f"{_format_strategy_price(pullback_low)} with {wick_ratio:.2f}x lower wick and "
                        f"{volume_multiple:.2f}x volume. SL {_format_strategy_price(stop_loss)}, "
                        f"TP {_format_strategy_price(take_profit)} ({_format_percent(reward_multiple)}R)."
                    ),
                    candle_timestamp=_as_utc(latest.candle_timestamp),
                    price=latest_close,
                    raw_payload=raw_payload,
                )
    else:
        countertrend_count = sum(float(candle.close_price) > float(candle.open_price) for candle in pullback_window)
        micro_level = min(min(float(candle.open_price), float(candle.close_price)) for candle in micro_window)
        wick_ratio = upper_wick / wick_reference
        prior_swing_target = min(float(candle.low_price) for candle in prior_context) if prior_context else None
        raw_payload.update(
            {
                "countertrend_bars": countertrend_count,
                "micro_level": micro_level,
                "wick_ratio": wick_ratio,
                "prior_swing_target": prior_swing_target,
                "trail_reference_ema": fast_ema_latest,
            }
        )
        if countertrend_count < min_countertrend_bars:
            reason = f"Need at least {min_countertrend_bars} bullish bounce candles; found {countertrend_count}."
        elif average_range > 0 and pullback_span < average_range * pullback_range_multiplier:
            reason = "Bounce was not sharp enough versus the recent average candle range."
        elif latest_close >= latest_open:
            reason = "Latest candle did not close bearish."
        elif latest_high <= pullback_high:
            reason = "Latest candle did not sweep the recent bounce high."
        elif upper_wick <= lower_wick or wick_ratio < wick_to_body_ratio_min:
            reason = f"Upper wick/body ratio {wick_ratio:.2f} is below the {wick_to_body_ratio_min:.2f} minimum."
        elif average_volume <= 0 or latest_volume < average_volume * volume_spike_multiple:
            reason = f"Volume spike requirement not met; latest volume is {volume_multiple:.2f}x baseline."
        elif latest_close >= micro_level or latest_high <= micro_level:
            reason = "Latest candle did not lose the recent micro level after the sweep."
        else:
            stop_loss = latest_high * (1 + stop_buffer_percent / 100)
            risk = stop_loss - latest_close
            take_profit = latest_close - risk * reward_multiple
            raw_payload.update(
                {
                    "stop_loss": stop_loss,
                    "risk": risk,
                    "take_profit": take_profit,
                }
            )
            if risk <= 0:
                reason = "Calculated wick stop did not produce positive risk."
            else:
                return SignalResult(
                    action="SELL",
                    reason=(
                        f"SELL on downtrend pullback trap below {_format_strategy_price(micro_level)} after sweeping "
                        f"{_format_strategy_price(pullback_high)} with {wick_ratio:.2f}x upper wick and "
                        f"{volume_multiple:.2f}x volume. SL {_format_strategy_price(stop_loss)}, "
                        f"TP {_format_strategy_price(take_profit)} ({_format_percent(reward_multiple)}R)."
                    ),
                    candle_timestamp=_as_utc(latest.candle_timestamp),
                    price=latest_close,
                    raw_payload=raw_payload,
                )

    return SignalResult(
        action="HOLD",
        reason=reason,
        candle_timestamp=_as_utc(latest.candle_timestamp),
        price=latest_close,
        raw_payload=raw_payload,
    )


def evaluate_opening_rvol_breakout(
    candles: list[ProjectXMarketCandle],
    *,
    strategy_params: dict[str, Any] | None = None,
    session_start_time: str = "09:30",
) -> SignalResult:
    params = _normalize_strategy_params(_STRATEGY_OPENING_RVOL_BREAKOUT, strategy_params)
    closed = _closed_candles(candles)
    latest = closed[-1] if closed else None
    latest_timestamp = _as_utc(latest.candle_timestamp) if latest is not None else None
    latest_price = float(latest.close_price) if latest is not None else None
    lookback_days = int(params["relative_volume_lookback_days"])
    min_relative_volume = float(params["min_relative_volume"])
    min_opening_volume = float(params["min_opening_volume"])
    min_body_to_range_ratio = float(params["min_body_to_range_ratio"])
    atr_period = int(params["atr_period"])
    atr_stop_multiple = float(params["atr_stop_multiple"])
    reward_multiple = float(params["take_profit_r_multiple"])
    raw_payload: dict[str, Any] = {
        "strategy_type": _STRATEGY_OPENING_RVOL_BREAKOUT,
        "settings": params,
        "session_start_time": session_start_time,
        "closed_count": len(closed),
    }

    if latest is None:
        return SignalResult(
            action="HOLD",
            reason="Need closed 5-minute candles to evaluate opening RVOL breakout.",
            candle_timestamp=None,
            price=None,
            raw_payload=raw_payload,
        )

    raw_payload["latest_candle"] = _serialize_strategy_candle(latest)
    if str(latest.unit) != "minute" or int(latest.unit_number) != 5:
        return SignalResult(
            action="HOLD",
            reason="Opening RVOL breakout expects 5-minute candles.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    if not _is_session_opening_candle(latest, session_start_time=session_start_time):
        return SignalResult(
            action="HOLD",
            reason=(
                f"Latest closed candle is not the {session_start_time} ET opening 5-minute candle "
                "for the configured session."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    opening_candles = _opening_session_candles(closed, session_start_time=session_start_time)
    prior_opening_candles = [
        candle for candle in opening_candles if _as_utc(candle.candle_timestamp) < latest_timestamp
    ]
    baseline_candles = prior_opening_candles[-lookback_days:]
    raw_payload.update(
        {
            "opening_candle": _serialize_strategy_candle(latest),
            "baseline_sample_size": len(baseline_candles),
        }
    )
    if len(baseline_candles) < lookback_days:
        return SignalResult(
            action="HOLD",
            reason=f"Need {lookback_days} prior opening-session candles for RVOL; found {len(baseline_candles)}.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    average_opening_volume = _average([float(candle.volume) for candle in baseline_candles])
    if average_opening_volume <= 0:
        raw_payload["average_opening_volume"] = average_opening_volume
        return SignalResult(
            action="HOLD",
            reason="Historical opening-session volume is not positive, so RVOL cannot be computed.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    open_price = float(latest.open_price)
    high_price = float(latest.high_price)
    low_price = float(latest.low_price)
    close_price = float(latest.close_price)
    opening_volume = float(latest.volume)
    relative_volume = opening_volume / average_opening_volume
    candle_range = high_price - low_price
    candle_body = abs(close_price - open_price)
    body_to_range_ratio = candle_body / candle_range if candle_range > 0 else 0.0
    raw_payload.update(
        {
            "average_opening_volume": average_opening_volume,
            "opening_volume": opening_volume,
            "relative_volume": relative_volume,
            "opening_range": candle_range,
            "body_to_range_ratio": body_to_range_ratio,
        }
    )

    if opening_volume < min_opening_volume:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Opening candle volume {opening_volume:.0f} is below the minimum "
                f"{min_opening_volume:.0f}."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    if relative_volume < min_relative_volume:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Opening candle RVOL is {relative_volume:.2f}x, below the minimum "
                f"{min_relative_volume:.2f}x."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    if candle_range <= 0:
        return SignalResult(
            action="HOLD",
            reason="Opening candle range is not positive.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    if close_price == open_price:
        return SignalResult(
            action="HOLD",
            reason="Opening candle closed flat, so there is no directional breakout bias.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    if body_to_range_ratio < min_body_to_range_ratio:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Opening candle body is {_format_percent(body_to_range_ratio * 100)}% of its range, "
                f"below the required {_format_percent(min_body_to_range_ratio * 100)}%."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    atr_values = _atr_series(closed, period=atr_period)
    latest_atr = atr_values[-1] if atr_values else None
    raw_payload["atr"] = latest_atr
    if latest_atr is None or latest_atr <= 0:
        return SignalResult(
            action="HOLD",
            reason=f"Need a positive {atr_period}-period ATR to size the stop.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    risk = latest_atr * atr_stop_multiple
    if risk <= 0:
        raw_payload["risk"] = risk
        return SignalResult(
            action="HOLD",
            reason="ATR-based stop distance was not positive.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    action = "BUY" if close_price > open_price else "SELL"
    stop_loss = close_price - risk if action == "BUY" else close_price + risk
    take_profit = close_price + (risk * reward_multiple) if action == "BUY" else close_price - (risk * reward_multiple)
    raw_payload.update(
        {
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk": risk,
        }
    )
    candle_color = "green" if action == "BUY" else "red"
    reason = (
        f"{action} on {candle_color} opening 5-minute RVOL breakout. "
        f"RVOL {relative_volume:.2f}x, body {_format_percent(body_to_range_ratio * 100)}% of range. "
        f"SL {_format_strategy_price(stop_loss)}, TP {_format_strategy_price(take_profit)} "
        f"({_format_percent(reward_multiple)}R)."
    )
    return SignalResult(
        action=action,
        reason=reason,
        candle_timestamp=latest_timestamp,
        price=close_price,
        raw_payload=raw_payload,
    )


def evaluate_delayed_orb_confirmation(
    candles: list[ProjectXMarketCandle],
    *,
    strategy_params: dict[str, Any] | None = None,
    session_start_time: str = "09:30",
) -> SignalResult:
    params = _normalize_strategy_params(_STRATEGY_DELAYED_ORB_CONFIRMATION, strategy_params)
    closed = _closed_candles(candles)
    latest = closed[-1] if closed else None
    latest_timestamp = _as_utc(latest.candle_timestamp) if latest is not None else None
    latest_price = float(latest.close_price) if latest is not None else None
    opening_range_minutes = int(params["opening_range_minutes"])
    confirmation_minutes = int(params["confirmation_minutes"])
    stop_mode = str(params["stop_mode"])
    target_mode = str(params["target_mode"])
    raw_payload: dict[str, Any] = {
        "strategy_type": _STRATEGY_DELAYED_ORB_CONFIRMATION,
        "settings": params,
        "session_start_time": session_start_time,
        "closed_count": len(closed),
    }

    if latest is None:
        return SignalResult(
            action="HOLD",
            reason="Need closed 1-minute candles to evaluate delayed ORB confirmation.",
            candle_timestamp=None,
            price=None,
            raw_payload=raw_payload,
        )

    session_start = _session_start_utc_for_reference(latest_timestamp, session_start_time)
    range_end = session_start + timedelta(minutes=opening_range_minutes)
    session_candles = [
        candle
        for candle in closed
        if session_start <= _as_utc(candle.candle_timestamp) < session_start + timedelta(hours=12)
    ]
    opening_range_candles = [
        candle for candle in session_candles if _as_utc(candle.candle_timestamp) < range_end
    ]
    raw_payload.update(
        {
            "opening_range_start": session_start.isoformat(),
            "opening_range_end": range_end.isoformat(),
            "opening_range_candle_count": len(opening_range_candles),
        }
    )
    if len(opening_range_candles) < opening_range_minutes:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Need {opening_range_minutes} opening-range candles starting at {session_start_time}; "
                f"found {len(opening_range_candles)}."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    range_high = max(float(candle.high_price) for candle in opening_range_candles)
    range_low = min(float(candle.low_price) for candle in opening_range_candles)
    range_size = range_high - range_low
    raw_payload.update(
        {
            "opening_range_high": range_high,
            "opening_range_low": range_low,
            "opening_range_size": range_size,
        }
    )
    if range_size <= 0:
        return SignalResult(
            action="HOLD",
            reason="Opening range size is not positive.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    post_range_candles = [
        candle for candle in session_candles if _as_utc(candle.candle_timestamp) >= range_end
    ]
    if len(post_range_candles) < confirmation_minutes:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Opening range is set. Waiting for {confirmation_minutes} full 1-minute candles "
                "to confirm a breakout outside the range."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    long_streak, long_confirmation = _count_trailing_matching_candles(
        post_range_candles,
        predicate=lambda candle: float(candle.low_price) > range_high,
    )
    short_streak, short_confirmation = _count_trailing_matching_candles(
        post_range_candles,
        predicate=lambda candle: float(candle.high_price) < range_low,
    )
    raw_payload["confirmation_state"] = {
        "long_streak_minutes": long_streak,
        "short_streak_minutes": short_streak,
    }

    if long_streak > confirmation_minutes or short_streak > confirmation_minutes:
        return SignalResult(
            action="HOLD",
            reason="A confirmed opening-range breakout already extended beyond the entry window; waiting for a fresh setup.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    if long_streak == confirmation_minutes:
        action = "BUY"
        confirmation_candles = long_confirmation
        stop_loss = range_high if stop_mode == _ORB_STOP_MODE_INSIDE_RANGE else range_low
    elif short_streak == confirmation_minutes:
        action = "SELL"
        confirmation_candles = short_confirmation
        stop_loss = range_low if stop_mode == _ORB_STOP_MODE_INSIDE_RANGE else range_high
    else:
        if float(latest.close_price) > range_high:
            reason = (
                f"Price is above the opening-range high {_format_strategy_price(range_high)}, "
                f"but only {long_streak} full candles have stayed outside; need {confirmation_minutes}."
            )
        elif float(latest.close_price) < range_low:
            reason = (
                f"Price is below the opening-range low {_format_strategy_price(range_low)}, "
                f"but only {short_streak} full candles have stayed outside; need {confirmation_minutes}."
            )
        else:
            reason = (
                f"Price {_format_strategy_price(float(latest.close_price))} is still inside the opening range "
                f"{_format_strategy_price(range_low)}-{_format_strategy_price(range_high)}."
            )
        return SignalResult(
            action="HOLD",
            reason=reason,
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    entry_price = float(latest.close_price)
    risk = entry_price - stop_loss if action == "BUY" else stop_loss - entry_price
    if risk <= 0:
        return SignalResult(
            action="HOLD",
            reason="Delayed ORB confirmation triggered, but the calculated risk was not positive.",
            candle_timestamp=latest_timestamp,
            price=entry_price,
            raw_payload=raw_payload,
        )

    take_profit = _orb_take_profit(
        action=action,
        entry_price=entry_price,
        risk=risk,
        opening_range_size=range_size,
        target_mode=target_mode,
    )
    raw_payload.update(
        {
            "confirmed_breakout": action,
            "confirmation_candles": [candle.candle_timestamp.isoformat() for candle in confirmation_candles],
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk": risk,
        }
    )
    reason = (
        f"{action} after {confirmation_minutes} full minutes outside the {opening_range_minutes}-minute opening range. "
        f"OR {_format_strategy_price(range_low)}-{_format_strategy_price(range_high)}, "
        f"SL {_format_strategy_price(stop_loss)}, TP {_format_strategy_price(take_profit)} ({target_mode})."
    )
    return SignalResult(
        action=action,
        reason=reason,
        candle_timestamp=latest_timestamp,
        price=entry_price,
        raw_payload=raw_payload,
    )


def evaluate_orb_fibonacci_pullback(
    candles: list[ProjectXMarketCandle],
    *,
    timeframe_unit: str,
    timeframe_unit_number: int,
    strategy_params: dict[str, Any] | None = None,
    session_start_time: str = "09:30",
    session_end_time: str = "15:45",
) -> SignalResult:
    params = _normalize_strategy_params(_STRATEGY_ORB_FIBONACCI_PULLBACK, strategy_params)
    closed = _closed_candles(candles)
    latest = closed[-1] if closed else None
    latest_timestamp = _as_utc(latest.candle_timestamp) if latest is not None else None
    latest_price = float(latest.close_price) if latest is not None else None
    opening_range_minutes = int(params["opening_range_minutes"])
    swing_lookback_bars = int(params["swing_lookback_bars"])
    target_mode = str(params["take_profit_mode"])
    normalized_unit = str(timeframe_unit).strip().lower()
    bar_minutes = max(1, int(timeframe_unit_number))
    raw_payload: dict[str, Any] = {
        "strategy_type": _STRATEGY_ORB_FIBONACCI_PULLBACK,
        "settings": params,
        "timeframe_unit": normalized_unit,
        "timeframe_unit_number": bar_minutes,
        "session_start_time": session_start_time,
        "session_end_time": session_end_time,
        "closed_count": len(closed),
    }

    if latest is None:
        return SignalResult(
            action="HOLD",
            reason="Need closed minute candles to evaluate ORB Fibonacci Pullback.",
            candle_timestamp=None,
            price=None,
            raw_payload=raw_payload,
        )

    if normalized_unit != "minute":
        return SignalResult(
            action="HOLD",
            reason="ORB Fibonacci Pullback requires minute candles.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    if opening_range_minutes % bar_minutes != 0:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Opening range {opening_range_minutes} minutes must align to the bot timeframe "
                f"of {bar_minutes} minute candles."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    session_start, session_end = _session_window_utc_for_reference(
        latest_timestamp,
        start_text=session_start_time,
        end_text=session_end_time,
    )
    session_candles = [
        candle
        for candle in closed
        if session_start <= _as_utc(candle.candle_timestamp) <= session_end
    ]
    opening_range_bars = opening_range_minutes // bar_minutes
    raw_payload.update(
        {
            "session_start": session_start.isoformat(),
            "session_end": session_end.isoformat(),
            "session_candle_count": len(session_candles),
            "opening_range_bars": opening_range_bars,
        }
    )
    if len(session_candles) <= opening_range_bars:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Need at least {opening_range_bars + 1} closed session candles to form the "
                f"{opening_range_minutes}-minute opening range and a breakout."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    opening_range_candles = session_candles[:opening_range_bars]
    post_range_candles = session_candles[opening_range_bars:]
    opening_range_high = max(float(candle.high_price) for candle in opening_range_candles)
    opening_range_low = min(float(candle.low_price) for candle in opening_range_candles)
    raw_payload["opening_range"] = {
        "high": opening_range_high,
        "low": opening_range_low,
        "start": _as_utc(opening_range_candles[0].candle_timestamp).isoformat(),
        "end": _as_utc(opening_range_candles[-1].candle_timestamp).isoformat(),
    }
    if opening_range_high <= opening_range_low:
        return SignalResult(
            action="HOLD",
            reason="Opening range size is not positive.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    breakout = _find_orb_breakout(
        post_range_candles,
        opening_range_high=opening_range_high,
        opening_range_low=opening_range_low,
    )
    if breakout is None:
        return SignalResult(
            action="HOLD",
            reason="Opening range is set, but no breakout has formed yet.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    breakout_action, breakout_index = breakout
    if breakout_action == "NONE":
        return SignalResult(
            action="HOLD",
            reason="A candle broke both sides of the opening range, so the ORB bias is ambiguous.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    breakout_leg_candles = post_range_candles[breakout_index:]
    if len(breakout_leg_candles) < 2:
        return SignalResult(
            action="HOLD",
            reason="Breakout bias is active. Waiting for the breakout leg to extend before a pullback entry.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    prior_leg_candles = breakout_leg_candles[:-1]
    latest_candle = breakout_leg_candles[-1]
    if breakout_action == "BUY":
        breakout_reference_price = opening_range_high
        leg_extreme = max(float(candle.high_price) for candle in prior_leg_candles)
        leg_extreme_index = next(
            index
            for index, candle in enumerate(prior_leg_candles)
            if float(candle.high_price) == leg_extreme
        )
        fib_50 = leg_extreme - (leg_extreme - breakout_reference_price) * 0.5
        fib_618 = leg_extreme - (leg_extreme - breakout_reference_price) * 0.618
        fib_786 = leg_extreme - (leg_extreme - breakout_reference_price) * 0.786
        zone_touched = _price_range_overlaps_zone(
            low=float(latest_candle.low_price),
            high=float(latest_candle.high_price),
            zone_low=fib_618,
            zone_high=fib_50,
        )
        confirmed = float(latest_candle.close_price) > float(latest_candle.open_price) and float(latest_candle.close_price) >= fib_50
        recent_swing = min(
            float(candle.low_price)
            for candle in breakout_leg_candles[leg_extreme_index + 1 :][-max(1, swing_lookback_bars) :]
        )
        stop_loss = min(fib_786, recent_swing)
        take_profit_anchor = leg_extreme
    else:
        breakout_reference_price = opening_range_low
        leg_extreme = min(float(candle.low_price) for candle in prior_leg_candles)
        leg_extreme_index = next(
            index
            for index, candle in enumerate(prior_leg_candles)
            if float(candle.low_price) == leg_extreme
        )
        fib_50 = leg_extreme + (breakout_reference_price - leg_extreme) * 0.5
        fib_618 = leg_extreme + (breakout_reference_price - leg_extreme) * 0.618
        fib_786 = leg_extreme + (breakout_reference_price - leg_extreme) * 0.786
        zone_touched = _price_range_overlaps_zone(
            low=float(latest_candle.low_price),
            high=float(latest_candle.high_price),
            zone_low=fib_50,
            zone_high=fib_618,
        )
        confirmed = float(latest_candle.close_price) < float(latest_candle.open_price) and float(latest_candle.close_price) <= fib_50
        recent_swing = max(
            float(candle.high_price)
            for candle in breakout_leg_candles[leg_extreme_index + 1 :][-max(1, swing_lookback_bars) :]
        )
        stop_loss = max(fib_786, recent_swing)
        take_profit_anchor = leg_extreme

    raw_payload.update(
        {
            "breakout": {
                "action": breakout_action,
                "timestamp": _as_utc(breakout_leg_candles[0].candle_timestamp).isoformat(),
                "reference_price": breakout_reference_price,
                "leg_extreme": leg_extreme,
            },
            "fib_levels": {
                "50.0": fib_50,
                "61.8": fib_618,
                "78.6": fib_786,
            },
            "entry_zone": {
                "touched": zone_touched,
                "confirmed": confirmed,
            },
        }
    )
    if not zone_touched:
        return SignalResult(
            action="HOLD",
            reason=(
                f"{breakout_action} ORB bias is active, but the latest candle has not pulled back into the "
                "50%-61.8% Fibonacci retracement zone."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )
    if not confirmed:
        return SignalResult(
            action="HOLD",
            reason=(
                f"{breakout_action} ORB pullback touched the Fibonacci zone, but the latest candle did not provide "
                f"{'bullish' if breakout_action == 'BUY' else 'bearish'} confirmation."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    entry_price = float(latest_candle.close_price)
    risk = entry_price - stop_loss if breakout_action == "BUY" else stop_loss - entry_price
    if risk <= 0:
        raw_payload.update({"stop_loss": stop_loss, "recent_swing": recent_swing})
        return SignalResult(
            action="HOLD",
            reason="ORB Fibonacci pullback touched the zone, but the calculated risk was not positive.",
            candle_timestamp=latest_timestamp,
            price=entry_price,
            raw_payload=raw_payload,
        )

    take_profit = _orb_fibonacci_take_profit(
        action=breakout_action,
        entry_price=entry_price,
        risk=risk,
        target_mode=target_mode,
        day_extreme=take_profit_anchor,
    )
    reward = take_profit - entry_price if breakout_action == "BUY" else entry_price - take_profit
    raw_payload.update(
        {
            "trigger_level": _nearest_orb_fib_trigger_level(
                action=breakout_action,
                latest_candle=latest_candle,
                fib_50=fib_50,
                fib_618=fib_618,
            ),
            "stop_loss": stop_loss,
            "recent_swing": recent_swing,
            "risk": risk,
            "take_profit": take_profit,
            "target_mode": target_mode,
            "reward": reward,
        }
    )
    if reward <= 0:
        return SignalResult(
            action="HOLD",
            reason="The selected ORB Fibonacci target does not produce a favorable reward on the latest candle.",
            candle_timestamp=latest_timestamp,
            price=entry_price,
            raw_payload=raw_payload,
        )

    reason = (
        f"{breakout_action} on an ORB fib pullback to the 50%-61.8% zone. "
        f"OR {_format_strategy_price(opening_range_low)}-{_format_strategy_price(opening_range_high)}, "
        f"fib 50 {_format_strategy_price(fib_50)}, fib 61.8 {_format_strategy_price(fib_618)}, "
        f"SL {_format_strategy_price(stop_loss)}, TP {_format_strategy_price(take_profit)} ({target_mode})."
    )
    return SignalResult(
        action=breakout_action,
        reason=reason,
        candle_timestamp=latest_timestamp,
        price=entry_price,
        raw_payload=raw_payload,
    )


def evaluate_atr_adjusted_relative_strength(
    candles: list[ProjectXMarketCandle],
    *,
    benchmark_candles: list[ProjectXMarketCandle],
    strategy_params: dict[str, Any] | None = None,
    session_start_time: str = "09:30",
) -> SignalResult:
    params = _normalize_strategy_params(_STRATEGY_ATR_ADJUSTED_RELATIVE_STRENGTH, strategy_params)
    closed = _closed_candles(candles)
    benchmark_closed = _closed_candles(benchmark_candles)
    aligned_pairs = _aligned_candle_pairs_by_timestamp(closed, benchmark_closed)
    latest_primary = aligned_pairs[-1][0] if aligned_pairs else (closed[-1] if closed else None)
    latest_benchmark = aligned_pairs[-1][1] if aligned_pairs else (benchmark_closed[-1] if benchmark_closed else None)
    latest_timestamp = _as_utc(latest_primary.candle_timestamp) if latest_primary is not None else None
    latest_price = float(latest_primary.close_price) if latest_primary is not None else None
    benchmark_name = (
        _normalized_optional_text(params.get("benchmark_symbol"))
        or (latest_benchmark.symbol if latest_benchmark is not None else None)
        or (latest_benchmark.contract_id if latest_benchmark is not None else None)
        or str(_ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS["benchmark_symbol"])
    )

    move_lookback_bars = int(params["move_lookback_bars"])
    atr_period = int(params["atr_period"])
    relative_volume_period = int(params["relative_volume_period"])
    relative_volume_cap = float(params["relative_volume_cap"])
    long_score_threshold = float(params["long_score_threshold"])
    short_score_threshold = float(params["short_score_threshold"])
    ema_period = int(params["ema_period"])
    stop_structure_window = int(params["stop_structure_window"])
    stop_atr_multiple = float(params["stop_atr_multiple"])
    reward_multiple = float(params["take_profit_r_multiple"])
    raw_payload: dict[str, Any] = {
        "strategy_type": _STRATEGY_ATR_ADJUSTED_RELATIVE_STRENGTH,
        "settings": params,
        "closed_counts": {
            "primary": len(closed),
            "benchmark": len(benchmark_closed),
            "aligned": len(aligned_pairs),
        },
        "benchmark_symbol": benchmark_name,
        "session_start_time": session_start_time,
    }

    if len(aligned_pairs) < move_lookback_bars + 1:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Need at least {move_lookback_bars + 1} aligned closed candles against {benchmark_name}; "
                f"found {len(aligned_pairs)}."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    latest_primary, latest_benchmark = aligned_pairs[-1]
    latest_timestamp = _as_utc(latest_primary.candle_timestamp)
    price = float(latest_primary.close_price)
    primary_window = [candle for candle in closed if _as_utc(candle.candle_timestamp) <= latest_timestamp]
    benchmark_window = [candle for candle in benchmark_closed if _as_utc(candle.candle_timestamp) <= latest_timestamp]
    minimum_primary_required = max(
        move_lookback_bars + 1,
        atr_period + 1,
        relative_volume_period + 1,
        ema_period,
        stop_structure_window,
    )
    minimum_benchmark_required = max(move_lookback_bars + 1, atr_period + 1)
    if len(primary_window) < minimum_primary_required or len(benchmark_window) < minimum_benchmark_required:
        raw_payload["minimum_required"] = {
            "primary": minimum_primary_required,
            "benchmark": minimum_benchmark_required,
        }
        return SignalResult(
            action="HOLD",
            reason=(
                f"Need at least {minimum_primary_required} primary candles and "
                f"{minimum_benchmark_required} {benchmark_name} candles by the latest aligned close; "
                f"found {len(primary_window)} and {len(benchmark_window)}."
            ),
            candle_timestamp=latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    primary_reference, benchmark_reference = aligned_pairs[-(move_lookback_bars + 1)]
    primary_move_percent = _percent_change(float(primary_reference.close_price), price)
    benchmark_price = float(latest_benchmark.close_price)
    benchmark_move_percent = _percent_change(float(benchmark_reference.close_price), benchmark_price)
    primary_atr = _atr_series(primary_window, period=atr_period)[-1]
    benchmark_atr = _atr_series(benchmark_window, period=atr_period)[-1]
    if primary_atr is None or benchmark_atr is None or primary_atr <= 0 or benchmark_atr <= 0:
        raw_payload["atr_state"] = {
            "primary_atr": primary_atr,
            "benchmark_atr": benchmark_atr,
        }
        return SignalResult(
            action="HOLD",
            reason="Primary or benchmark ATR is unavailable on the latest aligned closed candle.",
            candle_timestamp=latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    primary_atr_percent = (primary_atr / price) * 100 if price else float("inf")
    benchmark_atr_percent = (benchmark_atr / benchmark_price) * 100 if benchmark_price else float("inf")
    if primary_atr_percent <= 0 or benchmark_atr_percent <= 0:
        raw_payload["atr_percent"] = {
            "primary": primary_atr_percent,
            "benchmark": benchmark_atr_percent,
        }
        return SignalResult(
            action="HOLD",
            reason="Primary or benchmark ATR percent is not positive.",
            candle_timestamp=latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    primary_normalized_move = primary_move_percent / primary_atr_percent
    benchmark_normalized_move = benchmark_move_percent / benchmark_atr_percent
    raw_score = primary_normalized_move - benchmark_normalized_move
    relative_volume = _relative_volume_ratio(primary_window, period=relative_volume_period)
    if relative_volume is None:
        return SignalResult(
            action="HOLD",
            reason=f"Need positive average volume across the prior {relative_volume_period} candles.",
            candle_timestamp=latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )
    relative_volume_weight = min(relative_volume_cap, max(0.0, relative_volume))
    final_score = raw_score * relative_volume_weight

    closes = [float(candle.close_price) for candle in primary_window]
    ema_value = _ema_series(closes, period=ema_period)[-1] if closes else None
    session_vwap = _session_vwap_values(primary_window, session_start_time=session_start_time)[1][-1]
    if ema_value is None or session_vwap is None:
        raw_payload["entry_filter_state"] = {
            "ema": ema_value,
            "session_vwap": session_vwap,
        }
        return SignalResult(
            action="HOLD",
            reason="EMA or session VWAP is unavailable on the latest primary candle.",
            candle_timestamp=latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    recent_primary = primary_window[-stop_structure_window:]
    structure_low = min(float(candle.low_price) for candle in recent_primary)
    structure_high = max(float(candle.high_price) for candle in recent_primary)
    long_entry_confirmed = price > ema_value and price > session_vwap
    short_entry_confirmed = price < ema_value and price < session_vwap
    raw_payload.update(
        {
            "latest_aligned_timestamp": latest_timestamp.isoformat(),
            "reference_timestamp": _as_utc(primary_reference.candle_timestamp).isoformat(),
            "benchmark_contract_id": latest_benchmark.contract_id,
            "benchmark_symbol": latest_benchmark.symbol or benchmark_name,
            "primary_move_percent": primary_move_percent,
            "benchmark_move_percent": benchmark_move_percent,
            "primary_atr": primary_atr,
            "benchmark_atr": benchmark_atr,
            "primary_atr_percent": primary_atr_percent,
            "benchmark_atr_percent": benchmark_atr_percent,
            "primary_normalized_move": primary_normalized_move,
            "benchmark_normalized_move": benchmark_normalized_move,
            "raw_score": raw_score,
            "relative_volume": relative_volume,
            "relative_volume_weight": relative_volume_weight,
            "final_score": final_score,
            "ema": ema_value,
            "session_vwap": session_vwap,
            "structure_low": structure_low,
            "structure_high": structure_high,
        }
    )

    if final_score >= long_score_threshold:
        if not long_entry_confirmed:
            return SignalResult(
                action="HOLD",
                reason=(
                    f"Score {_format_percent(final_score)} cleared the long threshold, "
                    "but price is not above both EMA and session VWAP."
                ),
                candle_timestamp=latest_timestamp,
                price=price,
                raw_payload=raw_payload,
            )
        stop_anchor = min(structure_low, ema_value, session_vwap)
        stop_loss = stop_anchor - (primary_atr * stop_atr_multiple)
        risk = price - stop_loss
        if risk <= 0:
            return SignalResult(
                action="HOLD",
                reason="Long setup passed the score filter, but the calculated stop was not below price.",
                candle_timestamp=latest_timestamp,
                price=price,
                raw_payload=raw_payload,
            )
        take_profit = price + risk * reward_multiple
        raw_payload.update(
            {
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "risk": risk,
                "score_threshold_hit": "long",
            }
        )
        return SignalResult(
            action="BUY",
            reason=(
                f"BUY ATR-adjusted relative strength score {_format_percent(final_score)} vs {benchmark_name}. "
                f"Close is above EMA {_format_strategy_price(ema_value)} and VWAP {_format_strategy_price(session_vwap)}. "
                f"SL {_format_strategy_price(stop_loss)}, TP {_format_strategy_price(take_profit)}."
            ),
            candle_timestamp=latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    if final_score <= short_score_threshold:
        if not short_entry_confirmed:
            return SignalResult(
                action="HOLD",
                reason=(
                    f"Score {_format_percent(final_score)} cleared the short threshold, "
                    "but price is not below both EMA and session VWAP."
                ),
                candle_timestamp=latest_timestamp,
                price=price,
                raw_payload=raw_payload,
            )
        stop_anchor = max(structure_high, ema_value, session_vwap)
        stop_loss = stop_anchor + (primary_atr * stop_atr_multiple)
        risk = stop_loss - price
        if risk <= 0:
            return SignalResult(
                action="HOLD",
                reason="Short setup passed the score filter, but the calculated stop was not above price.",
                candle_timestamp=latest_timestamp,
                price=price,
                raw_payload=raw_payload,
            )
        take_profit = price - risk * reward_multiple
        raw_payload.update(
            {
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "risk": risk,
                "score_threshold_hit": "short",
            }
        )
        return SignalResult(
            action="SELL",
            reason=(
                f"SELL ATR-adjusted relative strength score {_format_percent(final_score)} vs {benchmark_name}. "
                f"Close is below EMA {_format_strategy_price(ema_value)} and VWAP {_format_strategy_price(session_vwap)}. "
                f"SL {_format_strategy_price(stop_loss)}, TP {_format_strategy_price(take_profit)}."
            ),
            candle_timestamp=latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    return SignalResult(
        action="HOLD",
        reason=(
            f"ATR-adjusted relative strength score {_format_percent(final_score)} remained between "
            f"{_format_percent(short_score_threshold)} and {_format_percent(long_score_threshold)}."
        ),
        candle_timestamp=latest_timestamp,
        price=price,
        raw_payload=raw_payload,
    )


def evaluate_vwap_atr_mean_reversion(
    candles: list[ProjectXMarketCandle],
    *,
    strategy_params: dict[str, Any] | None = None,
) -> SignalResult:
    params = _normalize_strategy_params(_STRATEGY_VWAP_ATR_MEAN_REVERSION, strategy_params)
    closed = _closed_candles(candles)
    latest = closed[-1] if closed else None
    latest_timestamp = _as_utc(latest.candle_timestamp) if latest is not None else None
    latest_price = float(latest.close_price) if latest is not None else None

    atr_period = int(params["atr_period"])
    rsi_period = int(params["rsi_period"])
    adx_period = int(params["adx_period"])
    stretch_atr_multiple = float(params["stretch_atr_multiple"])
    rsi_oversold = float(params["rsi_oversold"])
    rsi_overbought = float(params["rsi_overbought"])
    adx_max = float(params["adx_max"])
    vwap_slope_bars = int(params["vwap_slope_bars"])
    flat_vwap_threshold_bps = float(params["flat_vwap_threshold_bps"])
    local_extreme_lookback = int(params["local_extreme_lookback"])
    stop_buffer_atr = float(params["stop_buffer_atr"])
    take_profit_mode = str(params["take_profit_mode"])
    take_profit_r_multiple = float(params["take_profit_r_multiple"])

    minimum_required = max(
        atr_period,
        rsi_period + 1,
        adx_period * 2,
        vwap_slope_bars + 1,
        local_extreme_lookback,
    )
    raw_payload: dict[str, Any] = {
        "strategy_type": _STRATEGY_VWAP_ATR_MEAN_REVERSION,
        "settings": params,
        "closed_count": len(closed),
    }
    if len(closed) < minimum_required:
        return SignalResult(
            action="HOLD",
            reason=f"Need at least {minimum_required} closed candles; found {len(closed)}.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    assert latest is not None
    closes = [float(candle.close_price) for candle in closed]
    session_keys, session_vwaps = _session_vwap_values(closed)
    latest_vwap = session_vwaps[-1]
    session_key = session_keys[-1]
    raw_payload["session_key"] = session_key
    if latest_vwap is None:
        return SignalResult(
            action="HOLD",
            reason="Session VWAP is unavailable because the session has no usable volume yet.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    current_session_vwaps = [
        value
        for index, value in enumerate(session_vwaps)
        if session_keys[index] == session_key and value is not None
    ]
    if len(current_session_vwaps) < vwap_slope_bars + 1:
        raw_payload["session_vwap_points"] = len(current_session_vwaps)
        return SignalResult(
            action="HOLD",
            reason=(
                f"Need at least {vwap_slope_bars + 1} same-session VWAP points to measure slope; "
                f"found {len(current_session_vwaps)}."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    atr_values = _atr_series(closed, period=atr_period)
    rsi_values = _rsi_series(closes, period=rsi_period)
    adx_values = _adx_series(closed, period=adx_period)
    latest_atr = atr_values[-1]
    latest_rsi = rsi_values[-1]
    latest_adx = adx_values[-1]
    if latest_atr is None or latest_rsi is None or latest_adx is None or latest_atr <= 0:
        raw_payload["indicator_state"] = {
            "atr_available": latest_atr is not None,
            "rsi_available": latest_rsi is not None,
            "adx_available": latest_adx is not None,
            "atr": latest_atr,
            "rsi": latest_rsi,
            "adx": latest_adx,
        }
        return SignalResult(
            action="HOLD",
            reason="ATR, RSI, or ADX is not available on the latest closed candle.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    price = float(latest.close_price)
    distance_from_vwap = price - latest_vwap
    stretch_multiple = abs(distance_from_vwap) / latest_atr if latest_atr > 0 else float("inf")
    slope_reference_vwap = current_session_vwaps[-1 - vwap_slope_bars]
    vwap_slope_bps = abs(latest_vwap - slope_reference_vwap) / max(abs(slope_reference_vwap), 1e-9) * 10_000
    adx_pass = latest_adx <= adx_max
    vwap_slope_pass = vwap_slope_bps <= flat_vwap_threshold_bps
    range_filter_pass = adx_pass or vwap_slope_pass
    long_setup = distance_from_vwap < -(stretch_atr_multiple * latest_atr) and latest_rsi <= rsi_oversold
    short_setup = distance_from_vwap > stretch_atr_multiple * latest_atr and latest_rsi >= rsi_overbought

    raw_payload.update(
        {
            "session_vwap": latest_vwap,
            "distance_from_vwap": distance_from_vwap,
            "distance_atr_multiple": stretch_multiple,
            "atr": latest_atr,
            "rsi": latest_rsi,
            "adx": latest_adx,
            "vwap_slope_bps": vwap_slope_bps,
            "range_filter": {
                "adx_pass": adx_pass,
                "vwap_slope_pass": vwap_slope_pass,
                "passed": range_filter_pass,
            },
            "setup": {
                "long": long_setup,
                "short": short_setup,
            },
        }
    )

    if not long_setup and not short_setup:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Price stretch is {stretch_multiple:.2f} ATR from session VWAP with RSI {latest_rsi:.1f}; "
                "no oversold or overbought mean-reversion trigger is active."
            ),
            candle_timestamp=latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    if not range_filter_pass:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Setup is stretched from VWAP, but ADX {latest_adx:.1f} is above {adx_max:.1f} and "
                f"VWAP slope {vwap_slope_bps:.1f} bps is above {flat_vwap_threshold_bps:.1f}."
            ),
            candle_timestamp=latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    action = "BUY" if long_setup else "SELL"
    local_window = closed[-local_extreme_lookback:]
    local_low = min(float(candle.low_price) for candle in local_window)
    local_high = max(float(candle.high_price) for candle in local_window)
    stop_buffer = latest_atr * stop_buffer_atr
    if action == "BUY":
        stop_loss = local_low - stop_buffer
        risk = price - stop_loss
    else:
        stop_loss = local_high + stop_buffer
        risk = stop_loss - price

    raw_payload.update(
        {
            "local_low": local_low,
            "local_high": local_high,
            "stop_buffer": stop_buffer,
        }
    )
    if risk <= 0:
        raw_payload["stop_loss"] = stop_loss
        raw_payload["risk"] = risk
        return SignalResult(
            action="HOLD",
            reason="The local-extreme stop would not create positive risk on the latest candle.",
            candle_timestamp=latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    take_profit = _mean_reversion_take_profit(
        action=action,
        price=price,
        session_vwap=latest_vwap,
        risk=risk,
        mode=take_profit_mode,
        r_multiple=take_profit_r_multiple,
    )
    reward = take_profit - price if action == "BUY" else price - take_profit
    if reward <= 0:
        raw_payload.update(
            {
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "risk": risk,
                "reward": reward,
            }
        )
        return SignalResult(
            action="HOLD",
            reason="The selected take-profit mode does not produce a favorable target on the latest candle.",
            candle_timestamp=latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    reward_r = reward / risk if risk > 0 else None
    raw_payload.update(
        {
            "take_profit_mode": take_profit_mode,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk": risk,
            "reward": reward,
            "reward_r": reward_r,
        }
    )
    reason = (
        f"{action} {stretch_multiple:.2f} ATR from session VWAP with RSI {latest_rsi:.1f}. "
        f"ADX {latest_adx:.1f}, VWAP slope {vwap_slope_bps:.1f} bps. "
        f"SL {_format_strategy_price(stop_loss)}, TP {_format_strategy_price(take_profit)}."
    )
    return SignalResult(
        action=action,
        reason=reason,
        candle_timestamp=latest_timestamp,
        price=price,
        raw_payload=raw_payload,
    )


def evaluate_fisher_transform_mean_reversion(
    candles: list[ProjectXMarketCandle],
    *,
    fast_period: int,
    slow_period: int,
    strategy_params: dict[str, Any] | None = None,
) -> SignalResult:
    _validate_strategy_periods(fast_period, slow_period)
    params = _normalize_strategy_params(_STRATEGY_FISHER_MEAN_REVERSION, strategy_params)
    closed = _closed_candles(candles)
    latest = closed[-1] if closed else None
    latest_timestamp = _as_utc(latest.candle_timestamp) if latest is not None else None
    latest_price = float(latest.close_price) if latest is not None else None

    fisher_length = int(params["fisher_length"])
    threshold = float(params["fisher_extreme_threshold"])
    stretch_percent = float(params["price_stretch_percent"])
    slope_lookback_bars = int(params["ema_slope_lookback_bars"])
    slope_max_percent = float(params["ema_slope_max_percent"])
    swing_stop_lookback_bars = int(params["swing_stop_lookback_bars"])
    reward_multiple = float(params["take_profit_r_multiple"])

    minimum_required = max(
        fisher_length + 2,
        int(fast_period) + 1,
        int(slow_period) + slope_lookback_bars,
        swing_stop_lookback_bars,
    )
    raw_payload: dict[str, Any] = {
        "strategy_type": _STRATEGY_FISHER_MEAN_REVERSION,
        "settings": params,
        "closed_count": len(closed),
    }
    if len(closed) < minimum_required:
        return SignalResult(
            action="HOLD",
            reason=f"Need at least {minimum_required} closed candles; found {len(closed)}.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    assert latest is not None
    closes = [float(candle.close_price) for candle in closed]
    session_keys, session_vwaps = _session_vwap_values(closed)
    current_vwap = session_vwaps[-1]
    current_session_key = session_keys[-1]
    current_mean_ema = _ema_series(closes, period=int(fast_period))[-1]
    trend_ema_values = _ema_series(closes, period=int(slow_period))
    current_trend_ema = trend_ema_values[-1]
    prior_trend_ema = trend_ema_values[-(slope_lookback_bars + 1)]
    fisher_values = _fisher_transform_series(closed, length=fisher_length)
    previous_fisher = fisher_values[-2]
    current_fisher = fisher_values[-1]
    trend_slope_percent = _percent_change(prior_trend_ema, current_trend_ema)
    price = float(latest.close_price)

    below_vwap = _mean_stretch_hit(price, mean=current_vwap, threshold_percent=stretch_percent, side="below")
    below_mean_ema = _mean_stretch_hit(price, mean=current_mean_ema, threshold_percent=stretch_percent, side="below")
    above_vwap = _mean_stretch_hit(price, mean=current_vwap, threshold_percent=stretch_percent, side="above")
    above_mean_ema = _mean_stretch_hit(price, mean=current_mean_ema, threshold_percent=stretch_percent, side="above")
    stretched_below = below_vwap or below_mean_ema
    stretched_above = above_vwap or above_mean_ema
    trend_blocked = abs(trend_slope_percent) > slope_max_percent
    cross_up_from_extreme = previous_fisher <= -threshold and current_fisher > previous_fisher and current_fisher < 0
    cross_down_from_extreme = previous_fisher >= threshold and current_fisher < previous_fisher and current_fisher > 0

    raw_payload.update(
        {
            "session_key": current_session_key,
            "session_vwap": current_vwap,
            "mean_ema": current_mean_ema,
            "trend_ema": current_trend_ema,
            "fisher": {
                "previous": previous_fisher,
                "current": current_fisher,
                "extreme_threshold": threshold,
            },
            "stretch": {
                "price": price,
                "stretch_percent": stretch_percent,
                "below_vwap": below_vwap,
                "below_mean_ema": below_mean_ema,
                "above_vwap": above_vwap,
                "above_mean_ema": above_mean_ema,
            },
            "trend_filter": {
                "lookback_bars": slope_lookback_bars,
                "slope_percent": trend_slope_percent,
                "max_slope_percent": slope_max_percent,
                "blocked": trend_blocked,
            },
            "exit_plan": {
                "fisher_neutral": 0.0,
                "session_vwap": current_vwap,
                "mean_ema": current_mean_ema,
                "take_profit_r_multiple": reward_multiple,
            },
        }
    )

    if trend_blocked:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Trend filter blocked entry: {slow_period}-EMA slope "
                f"{_format_percent(trend_slope_percent)}% exceeds {_format_percent(slope_max_percent)}%."
            ),
            candle_timestamp=latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    action = "HOLD"
    mean_label = ""
    if cross_up_from_extreme and stretched_below:
        action = "BUY"
        mean_label = _mean_source_label(vwap_hit=below_vwap, ema_hit=below_mean_ema)
    elif cross_down_from_extreme and stretched_above:
        action = "SELL"
        mean_label = _mean_source_label(vwap_hit=above_vwap, ema_hit=above_mean_ema)

    if action == "HOLD":
        if cross_up_from_extreme or cross_down_from_extreme:
            reason = (
                f"Fisher reversed from an extreme, but price is not stretched by "
                f"{_format_percent(stretch_percent)}% from VWAP or the mean EMA."
            )
        else:
            reason = f"No Fisher reversal from +/-{_format_percent(threshold)} on the latest closed candle."
        return SignalResult(
            action="HOLD",
            reason=reason,
            candle_timestamp=latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    local_window = closed[-swing_stop_lookback_bars:]
    if action == "BUY":
        stop_loss = min(float(candle.low_price) for candle in local_window)
        risk = price - stop_loss
        take_profit = price + risk * reward_multiple
    else:
        stop_loss = max(float(candle.high_price) for candle in local_window)
        risk = stop_loss - price
        take_profit = price - risk * reward_multiple

    raw_payload.update(
        {
            "trigger_level": {
                "type": "fisher_reversal",
                "direction": action,
                "mean_source": mean_label,
                "current_fisher": current_fisher,
                "previous_fisher": previous_fisher,
            },
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk": risk,
        }
    )
    if risk <= 0:
        return SignalResult(
            action="HOLD",
            reason="Fisher reversal matched, but the calculated swing-based risk was not positive.",
            candle_timestamp=latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    reason = (
        f"{action} on Fisher reversal from {_format_percent(previous_fisher)} to {_format_percent(current_fisher)} "
        f"with price stretched beyond {mean_label}. SL {_format_strategy_price(stop_loss)}, "
        f"TP {_format_strategy_price(take_profit)} ({_format_percent(reward_multiple)}R)."
    )
    return SignalResult(
        action=action,
        reason=reason,
        candle_timestamp=latest_timestamp,
        price=price,
        raw_payload=raw_payload,
    )


def evaluate_bollinger_mean_reversion(
    candles: list[ProjectXMarketCandle],
    *,
    strategy_params: dict[str, Any] | None = None,
) -> SignalResult:
    params = _normalize_strategy_params(_STRATEGY_BOLLINGER_MEAN_REVERSION, strategy_params)
    closed = _closed_candles(candles)
    latest = closed[-1] if closed else None
    latest_timestamp = _as_utc(latest.candle_timestamp) if latest is not None else None
    latest_price = float(latest.close_price) if latest is not None else None

    bollinger_period = int(params["bollinger_period"])
    bollinger_stddev = float(params["bollinger_stddev"])
    atr_period = int(params["atr_period"])
    atr_stop_buffer = float(params["atr_stop_buffer"])
    take_profit_mode = str(params["take_profit_mode"])
    take_profit_r_multiple = float(params["take_profit_r_multiple"])
    news_blackout_windows = list(params["news_blackout_windows"])

    minimum_required = max(bollinger_period + 1, atr_period)
    raw_payload: dict[str, Any] = {
        "strategy_type": _STRATEGY_BOLLINGER_MEAN_REVERSION,
        "settings": params,
        "closed_count": len(closed),
    }
    if len(closed) < minimum_required:
        return SignalResult(
            action="HOLD",
            reason=f"Need at least {minimum_required} closed candles; found {len(closed)}.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    assert latest is not None
    closes = [float(candle.close_price) for candle in closed]
    middle_bands, upper_bands, lower_bands = _bollinger_band_values(
        closes,
        period=bollinger_period,
        stddev_multiplier=bollinger_stddev,
    )
    atr_values = _atr_series(closed, period=atr_period)
    _session_keys, session_vwaps = _session_vwap_values(closed)

    previous_index = len(closed) - 2
    current_index = len(closed) - 1
    previous_close = closes[previous_index]
    current_close = closes[current_index]
    previous_upper_band = upper_bands[previous_index]
    previous_lower_band = lower_bands[previous_index]
    current_middle_band = middle_bands[current_index]
    current_upper_band = upper_bands[current_index]
    current_lower_band = lower_bands[current_index]
    current_atr = atr_values[current_index]
    current_vwap = session_vwaps[current_index]

    raw_payload.update(
        {
            "previous_close": previous_close,
            "current_close": current_close,
            "previous_upper_band": previous_upper_band,
            "previous_lower_band": previous_lower_band,
            "middle_band": current_middle_band,
            "upper_band": current_upper_band,
            "lower_band": current_lower_band,
            "atr": current_atr,
            "session_vwap": current_vwap,
            "take_profit_mode": take_profit_mode,
            "news_blackout_windows": news_blackout_windows,
        }
    )

    if (
        previous_upper_band is None
        or previous_lower_band is None
        or current_middle_band is None
        or current_upper_band is None
        or current_lower_band is None
        or current_atr is None
        or current_atr <= 0
    ):
        return SignalResult(
            action="HOLD",
            reason="Bollinger Bands or ATR is not available on the latest closed candles.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    if latest_timestamp is not None and _timestamp_in_time_windows(latest_timestamp, news_blackout_windows):
        return SignalResult(
            action="HOLD",
            reason="Latest close falls inside a configured news blackout window.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    long_setup = previous_close >= previous_lower_band and current_close < current_lower_band
    short_setup = previous_close <= previous_upper_band and current_close > current_upper_band
    if not long_setup and not short_setup:
        return SignalResult(
            action="HOLD",
            reason="Latest close did not freshly break outside the wide Bollinger Bands.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    action = "BUY" if long_setup else "SELL"
    if action == "BUY":
        stop_loss = current_lower_band - (current_atr * atr_stop_buffer)
        risk = current_close - stop_loss
    else:
        stop_loss = current_upper_band + (current_atr * atr_stop_buffer)
        risk = stop_loss - current_close

    raw_payload["setup_direction"] = action
    if risk <= 0:
        raw_payload.update({"stop_loss": stop_loss, "risk": risk})
        return SignalResult(
            action="HOLD",
            reason="Band-plus-ATR stop placement did not leave positive risk on the entry candle.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    take_profit = _bollinger_rsi_take_profit(
        action=action,
        price=current_close,
        middle_band=current_middle_band,
        session_vwap=current_vwap,
        risk=risk,
        mode=take_profit_mode,
        r_multiple=take_profit_r_multiple,
    )
    reward = take_profit - current_close if action == "BUY" else current_close - take_profit if take_profit is not None else None
    raw_payload.update(
        {
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk": risk,
            "reward": reward,
        }
    )
    if take_profit is None or reward is None or reward <= 0:
        return SignalResult(
            action="HOLD",
            reason="The configured exit target does not produce a favorable mean-reversion payoff on this candle.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    raw_payload["reward_r_multiple"] = reward / risk if risk > 0 else None
    target_label = "middle band" if take_profit_mode == "middle_band" else "VWAP" if take_profit_mode == "vwap" else f"{_format_percent(take_profit_r_multiple)}R"
    reason = (
        f"{action} on a fresh {bollinger_period}-bar {bollinger_stddev:.2f} sigma Bollinger break. "
        f"SL {_format_strategy_price(stop_loss)}, TP {_format_strategy_price(take_profit)} via {target_label}."
    )
    return SignalResult(
        action=action,
        reason=reason,
        candle_timestamp=latest_timestamp,
        price=latest_price,
        raw_payload=raw_payload,
    )

def evaluate_vwap_gap_retrace(
    candles: list[ProjectXMarketCandle],
    *,
    strategy_params: dict[str, Any] | None = None,
) -> SignalResult:
    params = _normalize_strategy_params(_STRATEGY_VWAP_GAP_RETRACE, strategy_params)
    closed = _closed_candles(candles)
    latest = closed[-1] if closed else None
    latest_timestamp = _as_utc(latest.candle_timestamp) if latest is not None else None
    latest_price = float(latest.close_price) if latest is not None else None
    raw_payload: dict[str, Any] = {
        "strategy_type": _STRATEGY_VWAP_GAP_RETRACE,
        "settings": params,
        "closed_count": len(closed),
    }
    if not closed:
        return SignalResult(
            action="HOLD",
            reason="No closed candles are available for VWAP gap retrace evaluation.",
            candle_timestamp=None,
            price=None,
            raw_payload=raw_payload,
        )

    current_session_date = _regular_session_date(closed[-1])
    session_candles = [
        candle
        for candle in closed
        if _regular_session_date(candle) == current_session_date and _is_regular_session_candle(candle)
    ]
    raw_payload["session_date"] = current_session_date.isoformat()
    raw_payload["session_bar_count"] = len(session_candles)
    if not session_candles:
        return SignalResult(
            action="HOLD",
            reason="The current regular session has not produced any closed candles yet.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    prior_session_candles = [
        candle for candle in closed if _regular_session_date(candle) < current_session_date and _is_regular_session_candle(candle)
    ]
    if not prior_session_candles:
        return SignalResult(
            action="HOLD",
            reason="A prior regular-session close is required to measure the opening gap.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    previous_close = float(prior_session_candles[-1].close_price)
    session_open = float(session_candles[0].open_price)
    if previous_close <= 0 or session_open <= 0:
        raw_payload.update({"previous_close": previous_close, "session_open": session_open})
        return SignalResult(
            action="HOLD",
            reason="Gap sizing requires positive prior-close and session-open prices.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    min_gap_percent = float(params["min_gap_percent"])
    wait_start_minutes = int(params["wait_start_minutes"])
    wait_end_minutes = max(wait_start_minutes, int(params["wait_end_minutes"]))
    min_volume_ratio = float(params["min_volume_ratio"])
    stop_beyond_vwap_percent = float(params["stop_beyond_vwap_percent"])
    touch_tolerance_percent = float(params["touch_tolerance_percent"])

    gap_percent = (session_open - previous_close) / previous_close * 100
    raw_payload.update(
        {
            "previous_close": previous_close,
            "session_open": session_open,
            "gap_percent": gap_percent,
        }
    )
    if abs(gap_percent) < min_gap_percent:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Opening gap is {_format_percent(gap_percent)}%; "
                f"need at least {_format_percent(min_gap_percent)}%."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    gap_direction = "up" if gap_percent > 0 else "down"
    latest_session_candle = session_candles[-1]
    entry_price = float(latest_session_candle.close_price)
    entry_open = float(latest_session_candle.open_price)
    session_high = max(float(candle.high_price) for candle in session_candles)
    session_low = min(float(candle.low_price) for candle in session_candles)
    minutes_from_open = _regular_session_minutes_from_open(latest_session_candle)
    _session_keys, session_vwaps = _session_vwap_values(session_candles)
    latest_vwap = session_vwaps[-1]
    previous_session_candle = session_candles[-2] if len(session_candles) >= 2 else None
    previous_volume = float(previous_session_candle.volume) if previous_session_candle is not None else None
    latest_volume = float(latest_session_candle.volume)
    volume_ratio = None
    volume_increased = latest_volume > 0
    if previous_volume is not None and previous_volume > 0:
        volume_ratio = latest_volume / previous_volume
        volume_increased = volume_ratio > min_volume_ratio

    recent_vwap_bias = _has_recent_regular_session_vwap_bias(
        session_candles,
        session_vwaps,
        direction=gap_direction,
    )
    raw_payload.update(
        {
            "gap_direction": gap_direction,
            "minutes_from_open": minutes_from_open,
            "session_vwap": latest_vwap,
            "high_of_day": session_high,
            "low_of_day": session_low,
            "previous_volume": previous_volume,
            "latest_volume": latest_volume,
            "volume_ratio": volume_ratio,
            "recent_vwap_bias": recent_vwap_bias,
        }
    )
    if minutes_from_open < wait_start_minutes or minutes_from_open > wait_end_minutes:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Latest closed candle is {minutes_from_open} minutes after the open; "
                f"entries are limited to {wait_start_minutes}-{wait_end_minutes} minutes after the open."
            ),
            candle_timestamp=_as_utc(latest_session_candle.candle_timestamp),
            price=entry_price,
            raw_payload=raw_payload,
        )

    if latest_vwap is None:
        return SignalResult(
            action="HOLD",
            reason="Session VWAP is unavailable because the regular session has no usable volume yet.",
            candle_timestamp=_as_utc(latest_session_candle.candle_timestamp),
            price=entry_price,
            raw_payload=raw_payload,
        )

    tolerance_multiplier = touch_tolerance_percent / 100
    long_rejection = (
        gap_direction == "up"
        and recent_vwap_bias
        and entry_price > entry_open
        and entry_price >= latest_vwap
        and float(latest_session_candle.low_price) <= latest_vwap * (1 + tolerance_multiplier)
    )
    short_rejection = (
        gap_direction == "down"
        and recent_vwap_bias
        and entry_price < entry_open
        and entry_price <= latest_vwap
        and float(latest_session_candle.high_price) >= latest_vwap * (1 - tolerance_multiplier)
    )
    raw_payload["trigger_state"] = {
        "long_rejection": long_rejection,
        "short_rejection": short_rejection,
        "touch_tolerance_percent": touch_tolerance_percent,
    }
    if gap_direction == "up" and not long_rejection:
        return SignalResult(
            action="HOLD",
            reason="Gap-up is present, but the latest candle did not reject session VWAP with a bullish close.",
            candle_timestamp=_as_utc(latest_session_candle.candle_timestamp),
            price=entry_price,
            raw_payload=raw_payload,
        )
    if gap_direction == "down" and not short_rejection:
        return SignalResult(
            action="HOLD",
            reason="Gap-down is present, but the latest candle did not reject session VWAP with a bearish close.",
            candle_timestamp=_as_utc(latest_session_candle.candle_timestamp),
            price=entry_price,
            raw_payload=raw_payload,
        )
    if not volume_increased:
        volume_text = (
            f"{volume_ratio:.2f}x previous-bar volume"
            if volume_ratio is not None
            else "no usable prior-bar volume comparison"
        )
        return SignalResult(
            action="HOLD",
            reason=f"VWAP rejection formed, but volume did not increase enough ({volume_text}).",
            candle_timestamp=_as_utc(latest_session_candle.candle_timestamp),
            price=entry_price,
            raw_payload=raw_payload,
        )

    if gap_direction == "up":
        action = "BUY"
        stop_loss = latest_vwap * (1 - stop_beyond_vwap_percent / 100)
        risk = entry_price - stop_loss
        take_profit = entry_price + risk * 2
        directional_day_target = session_high
        opposite_momentum_label = "first bearish momentum candle"
    else:
        action = "SELL"
        stop_loss = latest_vwap * (1 + stop_beyond_vwap_percent / 100)
        risk = stop_loss - entry_price
        take_profit = entry_price - risk * 2
        directional_day_target = session_low
        opposite_momentum_label = "first bullish momentum candle"

    raw_payload.update(
        {
            "trigger_level": {
                "type": "session_vwap",
                "direction": gap_direction,
                "price": latest_vwap,
            },
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk": risk,
            "targets": {
                "day_extreme": directional_day_target,
                "two_r": take_profit,
                "opposite_momentum_exit": opposite_momentum_label,
            },
        }
    )
    if risk <= 0:
        return SignalResult(
            action="HOLD",
            reason="VWAP rejection matched, but the calculated stop beyond VWAP did not create positive risk.",
            candle_timestamp=_as_utc(latest_session_candle.candle_timestamp),
            price=entry_price,
            raw_payload=raw_payload,
        )

    volume_text = (
        f"{volume_ratio:.2f}x previous-bar volume"
        if volume_ratio is not None
        else "higher volume versus a zero-volume prior bar"
    )
    reason = (
        f"{action} after a {_format_percent(gap_percent)}% gap {gap_direction} and {minutes_from_open}-minute "
        f"VWAP rejection with {volume_text}. "
        f"SL {_format_strategy_price(stop_loss)}, target day extreme {_format_strategy_price(directional_day_target)} "
        f"or {_format_strategy_price(take_profit)} (2R), then exit on the {opposite_momentum_label}."
    )
    return SignalResult(
        action=action,
        reason=reason,
        candle_timestamp=_as_utc(latest_session_candle.candle_timestamp),
        price=entry_price,
        raw_payload=raw_payload,
    )


def evaluate_bollinger_rsi_reversal(
    candles: list[ProjectXMarketCandle],
    *,
    strategy_params: dict[str, Any] | None = None,
) -> SignalResult:
    params = _normalize_strategy_params(_STRATEGY_BOLLINGER_RSI_REVERSAL, strategy_params)
    closed = _closed_candles(candles)
    latest = closed[-1] if closed else None
    latest_timestamp = _as_utc(latest.candle_timestamp) if latest is not None else None
    latest_price = float(latest.close_price) if latest is not None else None

    rsi_period = int(params["rsi_period"])
    rsi_oversold = float(params["rsi_oversold"])
    rsi_overbought = float(params["rsi_overbought"])
    bollinger_period = int(params["bollinger_period"])
    bollinger_stddev = float(params["bollinger_stddev"])
    adx_period = int(params["adx_period"])
    adx_max = float(params["adx_max"])
    swing_stop_lookback_bars = int(params["swing_stop_lookback_bars"])
    stop_buffer_percent = float(params["stop_buffer_percent"])
    take_profit_mode = str(params["take_profit_mode"])
    take_profit_r_multiple = float(params["take_profit_r_multiple"])

    minimum_required = max(bollinger_period + 1, rsi_period + 2, adx_period * 2, swing_stop_lookback_bars)
    raw_payload: dict[str, Any] = {
        "strategy_type": _STRATEGY_BOLLINGER_RSI_REVERSAL,
        "settings": params,
        "closed_count": len(closed),
    }
    if len(closed) < minimum_required:
        return SignalResult(
            action="HOLD",
            reason=f"Need at least {minimum_required} closed candles; found {len(closed)}.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    assert latest is not None
    setup = closed[-2]
    confirmation = closed[-1]
    closes = [float(candle.close_price) for candle in closed]
    rsi_values = _rsi_series(closes, period=rsi_period)
    adx_values = _adx_series(closed, period=adx_period)
    middle_bands, upper_bands, lower_bands = _bollinger_band_values(closes, period=bollinger_period, stddev_multiplier=bollinger_stddev)
    _session_keys, session_vwaps = _session_vwap_values(closed)

    setup_index = len(closed) - 2
    confirmation_index = len(closed) - 1
    setup_rsi = rsi_values[setup_index]
    setup_upper_band = upper_bands[setup_index]
    setup_lower_band = lower_bands[setup_index]
    confirmation_middle_band = middle_bands[confirmation_index]
    confirmation_adx = adx_values[confirmation_index]
    confirmation_vwap = session_vwaps[confirmation_index]
    confirmation_open = float(confirmation.open_price)
    confirmation_close = float(confirmation.close_price)
    setup_close = float(setup.close_price)

    raw_payload.update(
        {
            "setup": {
                "timestamp": _as_utc(setup.candle_timestamp).isoformat(),
                "close": setup_close,
                "rsi": setup_rsi,
                "upper_band": setup_upper_band,
                "lower_band": setup_lower_band,
            },
            "confirmation": {
                "timestamp": _as_utc(confirmation.candle_timestamp).isoformat(),
                "open": confirmation_open,
                "close": confirmation_close,
                "middle_band": confirmation_middle_band,
                "adx": confirmation_adx,
                "vwap": confirmation_vwap,
            },
        }
    )

    if (
        setup_rsi is None
        or setup_upper_band is None
        or setup_lower_band is None
        or confirmation_middle_band is None
        or confirmation_adx is None
    ):
        return SignalResult(
            action="HOLD",
            reason="RSI, Bollinger Bands, or ADX is not available for the setup and confirmation candles.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    confirmation_is_green = confirmation_close > confirmation_open
    confirmation_is_red = confirmation_close < confirmation_open
    long_setup = setup_rsi < rsi_oversold and setup_close < setup_lower_band
    short_setup = setup_rsi > rsi_overbought and setup_close > setup_upper_band
    range_filter_pass = confirmation_adx <= adx_max
    raw_payload["range_filter"] = {
        "adx": confirmation_adx,
        "adx_max": adx_max,
        "passed": range_filter_pass,
    }

    if not long_setup and not short_setup:
        return SignalResult(
            action="HOLD",
            reason="Previous candle did not satisfy the RSI and Bollinger Band reversal setup.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    if not range_filter_pass:
        return SignalResult(
            action="HOLD",
            reason=f"Reversal setup formed, but ADX {confirmation_adx:.1f} is above the {adx_max:.1f} range threshold.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    if long_setup and not confirmation_is_green:
        return SignalResult(
            action="HOLD",
            reason="Long reversal setup formed, but the confirmation candle did not close green.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )
    if short_setup and not confirmation_is_red:
        return SignalResult(
            action="HOLD",
            reason="Short reversal setup formed, but the confirmation candle did not close red.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    action = "BUY" if long_setup else "SELL"
    recent_window = closed[-swing_stop_lookback_bars:]
    swing_low = min(float(candle.low_price) for candle in recent_window)
    swing_high = max(float(candle.high_price) for candle in recent_window)
    if action == "BUY":
        stop_anchor = swing_low
        stop_loss = stop_anchor * (1 - stop_buffer_percent / 100)
        risk = confirmation_close - stop_loss
    else:
        stop_anchor = swing_high
        stop_loss = stop_anchor * (1 + stop_buffer_percent / 100)
        risk = stop_loss - confirmation_close

    raw_payload.update(
        {
            "setup_direction": action,
            "swing_stop": {
                "lookback_bars": swing_stop_lookback_bars,
                "anchor_price": stop_anchor,
                "buffer_percent": stop_buffer_percent,
            },
        }
    )
    if risk <= 0:
        raw_payload.update({"stop_loss": stop_loss, "risk": risk})
        return SignalResult(
            action="HOLD",
            reason="The recent swing stop would not create positive risk on the confirmation candle.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    take_profit = _bollinger_rsi_take_profit(
        action=action,
        price=confirmation_close,
        middle_band=confirmation_middle_band,
        session_vwap=confirmation_vwap,
        risk=risk,
        mode=take_profit_mode,
        r_multiple=take_profit_r_multiple,
    )
    if take_profit is None:
        return SignalResult(
            action="HOLD",
            reason="Selected take-profit mode could not be calculated on the confirmation candle.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    reward = take_profit - confirmation_close if action == "BUY" else confirmation_close - take_profit
    if reward <= 0:
        raw_payload.update(
            {
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "risk": risk,
                "reward": reward,
            }
        )
        return SignalResult(
            action="HOLD",
            reason="The selected take-profit mode does not produce a favorable target on the confirmation candle.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    reward_r = reward / risk if risk > 0 else None
    raw_payload.update(
        {
            "take_profit_mode": take_profit_mode,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk": risk,
            "reward": reward,
            "reward_r": reward_r,
        }
    )
    reason = (
        f"{action} after RSI {setup_rsi:.1f} and a close "
        f"{'below' if action == 'BUY' else 'above'} the {'lower' if action == 'BUY' else 'upper'} band, "
        f"with the next candle closing {'green' if action == 'BUY' else 'red'}. "
        f"ADX {confirmation_adx:.1f}. SL {_format_strategy_price(stop_loss)}, TP {_format_strategy_price(take_profit)}."
    )
    return SignalResult(
        action=action,
        reason=reason,
        candle_timestamp=latest_timestamp,
        price=latest_price,
        raw_payload=raw_payload,
    )


def evaluate_support_resistance_levels(
    *,
    higher_timeframe_candles: list[ProjectXMarketCandle],
    lower_timeframe_candles: list[ProjectXMarketCandle],
    strategy_params: dict[str, Any] | None = None,
) -> SignalResult:
    params = _normalize_strategy_params(_STRATEGY_SUPPORT_RESISTANCE, strategy_params)
    bars_per_timeframe = int(params["bars_per_timeframe"])
    swing_window = int(params["swing_window"])
    tolerance_percent = float(params["level_tolerance_percent"])
    stop_beyond_percent = float(params["stop_beyond_level_percent"])
    reward_multiple = float(params["take_profit_r_multiple"])
    context = _build_level_strategy_context(
        higher_timeframe_candles=higher_timeframe_candles,
        lower_timeframe_candles=lower_timeframe_candles,
        bars_per_timeframe=bars_per_timeframe,
        swing_window=swing_window,
        tolerance_percent=tolerance_percent,
        strategy_type=_STRATEGY_SUPPORT_RESISTANCE,
        params=params,
    )
    minimum_required = swing_window

    if len(context.higher_closed) < minimum_required or len(context.lower_closed) < minimum_required:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Need at least {minimum_required} closed 4H and 1H candles; "
                f"found {len(context.higher_closed)} 4H and {len(context.lower_closed)} 1H."
            ),
            candle_timestamp=context.latest_timestamp,
            price=context.latest_price,
            raw_payload=context.raw_payload,
        )

    assert context.latest is not None
    price = float(context.latest.close_price)
    raw_payload = dict(context.raw_payload)

    if context.nearest_touch is None:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Price {_format_strategy_price(price)} is not within "
                f"{_format_percent(tolerance_percent)}% of a filtered support or resistance level."
            ),
            candle_timestamp=context.latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    level, distance_percent = context.nearest_touch
    is_support = level.side == "support"
    action = "BUY" if is_support else "SELL"
    if is_support:
        stop_loss = level.price * (1 - stop_beyond_percent / 100)
        risk = price - stop_loss
        take_profit = price + risk * reward_multiple
    else:
        stop_loss = level.price * (1 + stop_beyond_percent / 100)
        risk = stop_loss - price
        take_profit = price - risk * reward_multiple

    if risk <= 0:
        raw_payload["trigger_level"] = _serialize_support_resistance_level(level)
        raw_payload["distance_percent"] = distance_percent
        raw_payload["stop_loss"] = stop_loss
        raw_payload["take_profit"] = take_profit
        return SignalResult(
            action="HOLD",
            reason="Support/resistance level was touched, but the calculated risk was not positive.",
            candle_timestamp=context.latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    raw_payload.update(
        {
            "trigger_level": _serialize_support_resistance_level(level),
            "distance_percent": distance_percent,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk": risk,
        }
    )
    side_name = "support" if is_support else "resistance"
    reason = (
        f"{action} within {_format_percent(distance_percent)}% of {level.timeframe} {side_name} "
        f"{_format_strategy_price(level.price)}. SL {_format_strategy_price(stop_loss)}, "
        f"TP {_format_strategy_price(take_profit)} ({_format_percent(reward_multiple)}R)."
    )
    return SignalResult(
        action=action,
        reason=reason,
        candle_timestamp=context.latest_timestamp,
        price=price,
        raw_payload=raw_payload,
    )


def evaluate_liquidity_sweep_retest(
    *,
    higher_timeframe_candles: list[ProjectXMarketCandle],
    lower_timeframe_candles: list[ProjectXMarketCandle],
    fast_period: int,
    slow_period: int,
    strategy_params: dict[str, Any] | None = None,
) -> SignalResult:
    _validate_strategy_periods(fast_period, slow_period)
    params = _normalize_strategy_params(_STRATEGY_LIQUIDITY_SWEEP_RETEST, strategy_params)
    bars_per_timeframe = int(params["bars_per_timeframe"])
    swing_window = int(params["swing_window"])
    tolerance_percent = float(params["level_tolerance_percent"])
    reclaim_within_bars = int(params["reclaim_within_bars"])
    retest_within_bars = int(params["retest_within_bars"])
    stop_beyond_sweep_percent = float(params["stop_beyond_sweep_percent"])
    take_profit_mode = str(params["take_profit_mode"])
    context = _build_level_strategy_context(
        higher_timeframe_candles=higher_timeframe_candles,
        lower_timeframe_candles=lower_timeframe_candles,
        bars_per_timeframe=bars_per_timeframe,
        swing_window=swing_window,
        tolerance_percent=tolerance_percent,
        strategy_type=_STRATEGY_LIQUIDITY_SWEEP_RETEST,
        params=params,
    )
    minimum_higher_required = max(swing_window, slow_period)
    minimum_lower_required = max(swing_window, reclaim_within_bars + retest_within_bars + 2)
    raw_payload = dict(context.raw_payload)
    raw_payload["bias_periods"] = {"fast_period": fast_period, "slow_period": slow_period}

    if len(context.higher_closed) < minimum_higher_required or len(context.lower_closed) < minimum_lower_required:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Need at least {minimum_higher_required} closed 4H candles and "
                f"{minimum_lower_required} closed 1H candles; found {len(context.higher_closed)} 4H "
                f"and {len(context.lower_closed)} 1H."
            ),
            candle_timestamp=context.latest_timestamp,
            price=context.latest_price,
            raw_payload=raw_payload,
        )

    assert context.latest is not None
    bias, bias_metrics = _moving_average_bias(context.higher_closed, fast_period=fast_period, slow_period=slow_period)
    raw_payload["higher_timeframe_bias"] = {"bias": bias, **bias_metrics}
    if bias == "neutral":
        return SignalResult(
            action="HOLD",
            reason=f"4H bias is neutral on the configured {fast_period}/{slow_period} SMA filter.",
            candle_timestamp=context.latest_timestamp,
            price=context.latest_price,
            raw_payload=raw_payload,
        )

    setup = _find_liquidity_sweep_retest_setup(
        action="BUY" if bias == "bullish" else "SELL",
        levels=context.supports if bias == "bullish" else context.resistances,
        opposite_levels=context.resistances if bias == "bullish" else context.supports,
        candles=context.lower_closed,
        tolerance_percent=tolerance_percent,
        reclaim_within_bars=reclaim_within_bars,
        retest_within_bars=retest_within_bars,
        stop_beyond_sweep_percent=stop_beyond_sweep_percent,
        take_profit_mode=take_profit_mode,
    )
    if setup is None:
        side_text = "support" if bias == "bullish" else "resistance"
        direction_text = "bullish" if bias == "bullish" else "bearish"
        return SignalResult(
            action="HOLD",
            reason=(
                f"4H bias is {direction_text}, but no {side_text} sweep reclaimed and retested "
                "on the latest 1H candle."
            ),
            candle_timestamp=context.latest_timestamp,
            price=context.latest_price,
            raw_payload=raw_payload,
        )

    raw_payload.update(
        {
            "trigger_level": _serialize_support_resistance_level(setup.trigger_level),
            "sweep_candle": _serialize_strategy_candle(setup.sweep_candle),
            "reclaim_candle": _serialize_strategy_candle(setup.reclaim_candle),
            "retest_candle": _serialize_strategy_candle(setup.retest_candle),
            "stop_loss": setup.stop_loss,
            "take_profit": setup.take_profit,
            "risk": setup.risk,
            "reward_r": setup.reward_r,
            "take_profit_mode": setup.target_mode,
            "target_source": setup.target_source,
        }
    )
    if setup.target_level is not None:
        raw_payload["target_level"] = _serialize_support_resistance_level(setup.target_level)

    level_side = "low" if setup.action == "BUY" else "high"
    retest_phrase = "holding the retest" if setup.action == "BUY" else "holding the retest from below"
    reason = (
        f"{setup.action} reclaimed {setup.trigger_level.timeframe} liquidity {level_side} "
        f"{_format_strategy_price(setup.trigger_level.price)} after sweeping "
        f"{_format_strategy_price(_strategy_candle_extreme(setup.sweep_candle, setup.action))} and {retest_phrase}. "
        f"4H bias {bias} on {fast_period}/{slow_period} SMA. "
        f"SL {_format_strategy_price(setup.stop_loss)}, TP {_format_strategy_price(setup.take_profit)} "
        f"({_liquidity_target_label(setup)})."
    )
    return SignalResult(
        action=setup.action,
        reason=reason,
        candle_timestamp=context.latest_timestamp,
        price=float(context.latest.close_price),
        raw_payload=raw_payload,
    )


def evaluate_macd_support_resistance(
    *,
    higher_timeframe_candles: list[ProjectXMarketCandle],
    lower_timeframe_candles: list[ProjectXMarketCandle],
    fast_period: int,
    slow_period: int,
    strategy_params: dict[str, Any] | None = None,
) -> SignalResult:
    _validate_strategy_periods(fast_period, slow_period)
    params = _normalize_strategy_params(_STRATEGY_MACD_SUPPORT_RESISTANCE, strategy_params)
    bars_per_timeframe = int(params["bars_per_timeframe"])
    swing_window = int(params["swing_window"])
    tolerance_percent = float(params["level_tolerance_percent"])
    signal_period = int(params["signal_period"])
    atr_period = int(params["atr_period"])
    initial_stop_atr_multiplier = float(params["initial_stop_atr_multiplier"])
    trailing_stop_mode = str(params["trailing_stop_mode"])
    trailing_atr_multiplier = float(params["trailing_atr_multiplier"])
    trailing_ma_period = int(params["trailing_ma_period"])
    context = _build_level_strategy_context(
        higher_timeframe_candles=higher_timeframe_candles,
        lower_timeframe_candles=lower_timeframe_candles,
        bars_per_timeframe=bars_per_timeframe,
        swing_window=swing_window,
        tolerance_percent=tolerance_percent,
        strategy_type=_STRATEGY_MACD_SUPPORT_RESISTANCE,
        params=params,
    )
    required_lower_candles = max(
        swing_window,
        slow_period + signal_period,
        atr_period + 1,
        trailing_ma_period if trailing_stop_mode == _TRAILING_STOP_MODE_MOVING_AVERAGE else 0,
    )
    raw_payload = dict(context.raw_payload)
    raw_payload["requirements"] = {
        "minimum_4h": swing_window,
        "minimum_1h": required_lower_candles,
        "fast_period": fast_period,
        "slow_period": slow_period,
        "signal_period": signal_period,
        "atr_period": atr_period,
    }

    if len(context.higher_closed) < swing_window or len(context.lower_closed) < required_lower_candles:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Need at least {swing_window} closed 4H candles and {required_lower_candles} closed 1H candles; "
                f"found {len(context.higher_closed)} 4H and {len(context.lower_closed)} 1H."
            ),
            candle_timestamp=context.latest_timestamp,
            price=context.latest_price,
            raw_payload=raw_payload,
        )

    if context.latest is None:
        return SignalResult(
            action="HOLD",
            reason="Need closed 1H candles to evaluate MACD near support and resistance.",
            candle_timestamp=None,
            price=None,
            raw_payload=raw_payload,
        )

    closes = [float(candle.close_price) for candle in context.lower_closed]
    macd_state = _macd_state(closes, fast_period=fast_period, slow_period=slow_period, signal_period=signal_period)
    atr_values = _atr_series(context.lower_closed, period=atr_period)
    latest_atr = atr_values[-1] if atr_values else None
    raw_payload["indicator_state"] = {
        "macd": macd_state,
        "atr": latest_atr,
    }
    if macd_state is None or latest_atr is None or latest_atr <= 0:
        return SignalResult(
            action="HOLD",
            reason="MACD or ATR is not available on the latest closed candle.",
            candle_timestamp=context.latest_timestamp,
            price=context.latest_price,
            raw_payload=raw_payload,
        )

    price = float(context.latest.close_price)
    if context.nearest_touch is None:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Price {_format_strategy_price(price)} is not within "
                f"{_format_percent(tolerance_percent)}% of a filtered support or resistance level."
            ),
            candle_timestamp=context.latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    level, distance_percent = context.nearest_touch
    is_support = level.side == "support"
    bullish_signal = bool(macd_state["bullish_signal_cross"])
    bullish_zero = bool(macd_state["bullish_zero_cross"])
    bearish_signal = bool(macd_state["bearish_signal_cross"])
    bearish_zero = bool(macd_state["bearish_zero_cross"])
    bullish_trigger = bullish_signal or bullish_zero
    bearish_trigger = bearish_signal or bearish_zero
    if is_support and not bullish_trigger:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Price is near {level.timeframe} support {_format_strategy_price(level.price)}, "
                "but MACD did not cross above the signal or zero line."
            ),
            candle_timestamp=context.latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )
    if not is_support and not bearish_trigger:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Price is near {level.timeframe} resistance {_format_strategy_price(level.price)}, "
                "but MACD did not cross below the signal or zero line."
            ),
            candle_timestamp=context.latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    action = "BUY" if is_support else "SELL"
    stop_distance = latest_atr * initial_stop_atr_multiplier
    stop_loss = price - stop_distance if action == "BUY" else price + stop_distance
    risk = stop_distance
    if risk <= 0:
        raw_payload["trigger_level"] = _serialize_support_resistance_level(level)
        raw_payload["distance_percent"] = distance_percent
        raw_payload["stop_loss"] = stop_loss
        raw_payload["atr"] = latest_atr
        return SignalResult(
            action="HOLD",
            reason="MACD trigger fired near a level, but the ATR-based stop was not valid.",
            candle_timestamp=context.latest_timestamp,
            price=price,
            raw_payload=raw_payload,
        )

    trigger_description = _macd_trigger_description(
        signal_cross=bullish_signal if action == "BUY" else bearish_signal,
        zero_cross=bullish_zero if action == "BUY" else bearish_zero,
    )
    trailing_stop = _build_macd_support_resistance_trailing_stop(
        action=action,
        price=price,
        level=level,
        atr=latest_atr,
        atr_period=atr_period,
        trailing_stop_mode=trailing_stop_mode,
        trailing_atr_multiplier=trailing_atr_multiplier,
        trailing_ma_period=trailing_ma_period,
        closes=closes,
        swing_window=swing_window,
    )
    raw_payload.update(
        {
            "trigger_level": _serialize_support_resistance_level(level),
            "distance_percent": distance_percent,
            "stop_loss": stop_loss,
            "risk": risk,
            "atr": latest_atr,
            "entry_trigger": trigger_description,
            "trailing_stop": trailing_stop,
        }
    )
    side_name = "support" if is_support else "resistance"
    reason = (
        f"{action} on MACD {trigger_description} within {_format_percent(distance_percent)}% of "
        f"{level.timeframe} {side_name} {_format_strategy_price(level.price)}. "
        f"Initial SL {_format_strategy_price(stop_loss)} ({initial_stop_atr_multiplier:.2f} ATR), "
        f"trailing via {_format_trailing_stop_mode(trailing_stop_mode)}."
    )
    return SignalResult(
        action=action,
        reason=reason,
        candle_timestamp=context.latest_timestamp,
        price=price,
        raw_payload=raw_payload,
    )


def _build_level_strategy_context(
    *,
    higher_timeframe_candles: list[ProjectXMarketCandle],
    lower_timeframe_candles: list[ProjectXMarketCandle],
    bars_per_timeframe: int,
    swing_window: int,
    tolerance_percent: float,
    strategy_type: str,
    params: dict[str, Any],
) -> LevelStrategyContext:
    higher_closed = _closed_candles(higher_timeframe_candles)[-bars_per_timeframe:]
    lower_closed = _closed_candles(lower_timeframe_candles)[-bars_per_timeframe:]
    latest = lower_closed[-1] if lower_closed else None
    latest_timestamp = _as_utc(latest.candle_timestamp) if latest is not None else None
    latest_price = float(latest.close_price) if latest is not None else None
    raw_levels = [
        *_detect_support_resistance_levels(higher_closed, timeframe="4H", window_size=swing_window),
        *_detect_support_resistance_levels(lower_closed, timeframe="1H", window_size=swing_window),
    ]
    supports = _filter_clustered_levels(
        [level for level in raw_levels if level.side == "support"],
        tolerance_percent=tolerance_percent,
    )
    resistances = _filter_clustered_levels(
        [level for level in raw_levels if level.side == "resistance"],
        tolerance_percent=tolerance_percent,
    )
    support_touch = None
    resistance_touch = None
    nearest_touch = None
    if latest_price is not None:
        support_touch = _nearest_level_touch(
            levels=supports,
            price=latest_price,
            side="support",
            tolerance_percent=tolerance_percent,
        )
        resistance_touch = _nearest_level_touch(
            levels=resistances,
            price=latest_price,
            side="resistance",
            tolerance_percent=tolerance_percent,
        )
        nearest_touch = _choose_nearest_touch(support_touch, resistance_touch)

    return LevelStrategyContext(
        higher_closed=higher_closed,
        lower_closed=lower_closed,
        latest=latest,
        latest_timestamp=latest_timestamp,
        latest_price=latest_price,
        raw_levels=raw_levels,
        supports=supports,
        resistances=resistances,
        support_touch=support_touch,
        resistance_touch=resistance_touch,
        nearest_touch=nearest_touch,
        raw_payload={
            "strategy_type": strategy_type,
            "settings": params,
            "closed_counts": {"4H": len(higher_closed), "1H": len(lower_closed)},
            "raw_level_count": len(raw_levels),
            "support_levels": [_serialize_support_resistance_level(level) for level in supports],
            "resistance_levels": [_serialize_support_resistance_level(level) for level in resistances],
        },
    )


def _moving_average_bias(
    candles: list[ProjectXMarketCandle],
    *,
    fast_period: int,
    slow_period: int,
) -> tuple[str, dict[str, float]]:
    closes = [float(candle.close_price) for candle in candles]
    current_fast = _average(closes[-fast_period:])
    current_slow = _average(closes[-slow_period:])
    latest_close = closes[-1]
    if current_fast > current_slow and latest_close >= current_slow:
        bias = "bullish"
    elif current_fast < current_slow and latest_close <= current_slow:
        bias = "bearish"
    else:
        bias = "neutral"
    return bias, {
        "latest_close": latest_close,
        "current_fast": current_fast,
        "current_slow": current_slow,
    }


def _find_liquidity_sweep_retest_setup(
    *,
    action: str,
    levels: list[SupportResistanceLevel],
    opposite_levels: list[SupportResistanceLevel],
    candles: list[ProjectXMarketCandle],
    tolerance_percent: float,
    reclaim_within_bars: int,
    retest_within_bars: int,
    stop_beyond_sweep_percent: float,
    take_profit_mode: str,
) -> LiquiditySweepRetestSetup | None:
    if len(candles) < 2:
        return None

    latest_index = len(candles) - 1
    latest = candles[latest_index]
    latest_timestamp = _as_utc(latest.candle_timestamp)
    entry_price = float(latest.close_price)
    candidates: list[LiquiditySweepRetestSetup] = []

    for level in levels:
        if level.timestamp >= latest_timestamp:
            continue
        if not _liquidity_retest_holds(
            candle=latest,
            action=action,
            level_price=level.price,
            tolerance_percent=tolerance_percent,
        ):
            continue

        reclaim_start_index = max(0, latest_index - retest_within_bars)
        for reclaim_index in range(latest_index - 1, reclaim_start_index - 1, -1):
            reclaim_candle = candles[reclaim_index]
            if _as_utc(reclaim_candle.candle_timestamp) <= level.timestamp:
                continue
            if action == "BUY" and float(reclaim_candle.close_price) < level.price:
                continue
            if action == "SELL" and float(reclaim_candle.close_price) > level.price:
                continue

            sweep_start_index = max(0, reclaim_index - reclaim_within_bars)
            sweep_candidates = [
                candles[index]
                for index in range(sweep_start_index, reclaim_index + 1)
                if _as_utc(candles[index].candle_timestamp) > level.timestamp
                and _is_liquidity_sweep_candle(candles[index], action=action, level_price=level.price)
            ]
            if not sweep_candidates:
                continue

            sweep_candle = (
                min(sweep_candidates, key=lambda candle: float(candle.low_price))
                if action == "BUY"
                else max(sweep_candidates, key=lambda candle: float(candle.high_price))
            )
            sweep_extreme = _strategy_candle_extreme(sweep_candle, action)
            latest_extreme = _strategy_candle_extreme(latest, action)
            if action == "BUY" and latest_extreme < sweep_extreme:
                continue
            if action == "SELL" and latest_extreme > sweep_extreme:
                continue

            stop_loss = (
                sweep_extreme * (1 - stop_beyond_sweep_percent / 100)
                if action == "BUY"
                else sweep_extreme * (1 + stop_beyond_sweep_percent / 100)
            )
            risk = entry_price - stop_loss if action == "BUY" else stop_loss - entry_price
            if risk <= 0:
                continue

            take_profit, target_level, target_source = _resolve_liquidity_sweep_take_profit(
                action=action,
                entry_price=entry_price,
                risk=risk,
                take_profit_mode=take_profit_mode,
                opposite_levels=opposite_levels,
            )
            if take_profit is None:
                continue

            reward = take_profit - entry_price if action == "BUY" else entry_price - take_profit
            if reward <= 0:
                continue

            candidates.append(
                LiquiditySweepRetestSetup(
                    action=action,
                    trigger_level=level,
                    sweep_candle=sweep_candle,
                    reclaim_candle=reclaim_candle,
                    retest_candle=latest,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    risk=risk,
                    reward_r=reward / risk,
                    target_mode=take_profit_mode,
                    target_source=target_source,
                    target_level=target_level,
                )
            )
            break

    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            item.trigger_level.score,
            item.reward_r,
            _as_utc(item.reclaim_candle.candle_timestamp).timestamp(),
        ),
    )


def _liquidity_retest_holds(
    *,
    candle: ProjectXMarketCandle,
    action: str,
    level_price: float,
    tolerance_percent: float,
) -> bool:
    tolerance_multiplier = tolerance_percent / 100
    if action == "BUY":
        return float(candle.low_price) <= level_price * (1 + tolerance_multiplier) and float(candle.close_price) >= level_price
    return float(candle.high_price) >= level_price * (1 - tolerance_multiplier) and float(candle.close_price) <= level_price


def _is_liquidity_sweep_candle(
    candle: ProjectXMarketCandle,
    *,
    action: str,
    level_price: float,
) -> bool:
    if action == "BUY":
        return float(candle.low_price) < level_price
    return float(candle.high_price) > level_price


def _resolve_liquidity_sweep_take_profit(
    *,
    action: str,
    entry_price: float,
    risk: float,
    take_profit_mode: str,
    opposite_levels: list[SupportResistanceLevel],
) -> tuple[float | None, SupportResistanceLevel | None, str]:
    if take_profit_mode == _LIQUIDITY_SWEEP_TARGET_MODE_NEXT_POOL:
        if action == "BUY":
            candidates = [level for level in opposite_levels if level.price > entry_price]
            if not candidates:
                return None, None, "next_liquidity_unavailable"
            target_level = min(candidates, key=lambda level: (level.price, -level.score))
        else:
            candidates = [level for level in opposite_levels if level.price < entry_price]
            if not candidates:
                return None, None, "next_liquidity_unavailable"
            target_level = max(candidates, key=lambda level: (level.price, level.score))
        return target_level.price, target_level, f"next_{target_level.timeframe.lower()}_{target_level.side}"

    reward_multiple = 3.0 if take_profit_mode == _LIQUIDITY_SWEEP_TARGET_MODE_3R else 2.0
    take_profit = entry_price + risk * reward_multiple if action == "BUY" else entry_price - risk * reward_multiple
    return take_profit, None, f"{int(reward_multiple)}r"


def _liquidity_target_label(setup: LiquiditySweepRetestSetup) -> str:
    if setup.target_level is None:
        return "3R" if setup.target_mode == _LIQUIDITY_SWEEP_TARGET_MODE_3R else "2R"
    side_name = "support" if setup.target_level.side == "support" else "resistance"
    return f"next {setup.target_level.timeframe} {side_name}"


def _strategy_candle_extreme(candle: ProjectXMarketCandle, action: str) -> float:
    return float(candle.low_price) if action == "BUY" else float(candle.high_price)


def _serialize_strategy_candle(candle: ProjectXMarketCandle) -> dict[str, Any]:
    return {
        "timestamp": _as_utc(candle.candle_timestamp).isoformat(),
        "open": float(candle.open_price),
        "high": float(candle.high_price),
        "low": float(candle.low_price),
        "close": float(candle.close_price),
        "volume": float(candle.volume),
    }


def _macd_trigger_description(*, signal_cross: bool, zero_cross: bool) -> str:
    if signal_cross and zero_cross:
        return "signal-line and zero-line crossover"
    if signal_cross:
        return "signal-line crossover"
    return "zero-line crossover"


def _build_macd_support_resistance_trailing_stop(
    *,
    action: str,
    price: float,
    level: SupportResistanceLevel,
    atr: float,
    atr_period: int,
    trailing_stop_mode: str,
    trailing_atr_multiplier: float,
    trailing_ma_period: int,
    closes: list[float],
    swing_window: int,
) -> dict[str, Any]:
    if trailing_stop_mode == _TRAILING_STOP_MODE_SWING:
        return {
            "mode": trailing_stop_mode,
            "swing_window": swing_window,
            "reference_level": _serialize_support_resistance_level(level),
            "activation_price": price,
        }
    if trailing_stop_mode == _TRAILING_STOP_MODE_MOVING_AVERAGE:
        ema_values = _ema_series(closes, period=trailing_ma_period)
        reference_value = next((value for value in reversed(ema_values) if value is not None), None)
        return {
            "mode": trailing_stop_mode,
            "ma_type": "ema",
            "ma_period": trailing_ma_period,
            "reference_price": reference_value,
            "activation_price": price,
        }
    return {
        "mode": _TRAILING_STOP_MODE_ATR,
        "atr_period": atr_period,
        "atr_multiplier": trailing_atr_multiplier,
        "trail_offset": atr * trailing_atr_multiplier,
        "activation_price": price,
        "direction": action,
    }


def _format_trailing_stop_mode(value: str) -> str:
    if value == _TRAILING_STOP_MODE_MOVING_AVERAGE:
        return "moving average"
    return value


def _count_trailing_matching_candles(
    candles: list[ProjectXMarketCandle],
    *,
    predicate: Any,
) -> tuple[int, list[ProjectXMarketCandle]]:
    matched: list[ProjectXMarketCandle] = []
    for candle in reversed(candles):
        if not predicate(candle):
            break
        matched.append(candle)
    matched.reverse()
    return len(matched), matched


def _orb_take_profit(
    *,
    action: str,
    entry_price: float,
    risk: float,
    opening_range_size: float,
    target_mode: str,
) -> float:
    if target_mode == _ORB_TARGET_MODE_3R:
        return entry_price + risk * 3 if action == "BUY" else entry_price - risk * 3
    if target_mode == _ORB_TARGET_MODE_MEASURED_MOVE:
        return entry_price + opening_range_size if action == "BUY" else entry_price - opening_range_size
    return entry_price + risk * 2 if action == "BUY" else entry_price - risk * 2


def _orb_fibonacci_take_profit(
    *,
    action: str,
    entry_price: float,
    risk: float,
    target_mode: str,
    day_extreme: float,
) -> float:
    if target_mode == _ORB_TARGET_MODE_3R:
        return entry_price + risk * 3 if action == "BUY" else entry_price - risk * 3
    if target_mode == _ORB_TARGET_MODE_DAY_EXTREME:
        return day_extreme
    return entry_price + risk * 2 if action == "BUY" else entry_price - risk * 2


def _find_orb_breakout(
    candles: list[ProjectXMarketCandle],
    *,
    opening_range_high: float,
    opening_range_low: float,
) -> tuple[str, int] | None:
    for index, candle in enumerate(candles):
        high = float(candle.high_price)
        low = float(candle.low_price)
        close = float(candle.close_price)
        broke_above = high > opening_range_high and close >= opening_range_high
        broke_below = low < opening_range_low and close <= opening_range_low
        if broke_above and broke_below:
            return ("NONE", index)
        if broke_above:
            return ("BUY", index)
        if broke_below:
            return ("SELL", index)
    return None


def _price_range_overlaps_zone(*, low: float, high: float, zone_low: float, zone_high: float) -> bool:
    lower_bound = min(zone_low, zone_high)
    upper_bound = max(zone_low, zone_high)
    return high >= lower_bound and low <= upper_bound


def _nearest_orb_fib_trigger_level(
    *,
    action: str,
    latest_candle: ProjectXMarketCandle,
    fib_50: float,
    fib_618: float,
) -> dict[str, Any]:
    reference_price = float(latest_candle.low_price) if action == "BUY" else float(latest_candle.high_price)
    distances = {
        "fib_50": abs(reference_price - fib_50),
        "fib_61_8": abs(reference_price - fib_618),
    }
    if distances["fib_50"] <= distances["fib_61_8"]:
        return {"name": "fib_50", "price": fib_50}
    return {"name": "fib_61_8", "price": fib_618}


def _session_start_utc_for_reference(reference: datetime, session_start_time: str) -> datetime:
    session_start = _parse_session_time(session_start_time)
    local_reference = _as_utc(reference).astimezone(TRADING_TZ)
    return datetime.combine(local_reference.date(), session_start, tzinfo=TRADING_TZ).astimezone(timezone.utc)


def _is_session_opening_candle(candle: ProjectXMarketCandle, *, session_start_time: str) -> bool:
    timestamp = _as_utc(candle.candle_timestamp)
    return timestamp == _session_start_utc_for_reference(timestamp, session_start_time)


def _opening_session_candles(
    candles: list[ProjectXMarketCandle],
    *,
    session_start_time: str,
) -> list[ProjectXMarketCandle]:
    output: list[ProjectXMarketCandle] = []
    seen: set[datetime] = set()
    for candle in candles:
        if not _is_session_opening_candle(candle, session_start_time=session_start_time):
            continue
        timestamp = _as_utc(candle.candle_timestamp)
        if timestamp in seen:
            continue
        seen.add(timestamp)
        output.append(candle)
    return output


def _serialize_strategy_candle(candle: ProjectXMarketCandle) -> dict[str, Any]:
    return {
        "timestamp": _as_utc(candle.candle_timestamp).isoformat(),
        "open": float(candle.open_price),
        "high": float(candle.high_price),
        "low": float(candle.low_price),
        "close": float(candle.close_price),
        "volume": float(candle.volume),
    }


def _session_window_utc_for_reference(
    reference: datetime,
    *,
    start_text: str,
    end_text: str,
) -> tuple[datetime, datetime]:
    session_start = _parse_session_time(start_text)
    session_end = _parse_session_time(end_text)
    local_reference = _as_utc(reference).astimezone(TRADING_TZ)
    local_date = local_reference.date()

    if session_start <= session_end:
        start_local = datetime.combine(local_date, session_start, tzinfo=TRADING_TZ)
        end_local = datetime.combine(local_date, session_end, tzinfo=TRADING_TZ)
        return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)

    if local_reference.time() >= session_start:
        start_local = datetime.combine(local_date, session_start, tzinfo=TRADING_TZ)
        end_local = datetime.combine(local_date + timedelta(days=1), session_end, tzinfo=TRADING_TZ)
    else:
        start_local = datetime.combine(local_date - timedelta(days=1), session_start, tzinfo=TRADING_TZ)
        end_local = datetime.combine(local_date, session_end, tzinfo=TRADING_TZ)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _mean_reversion_take_profit(
    *,
    action: str,
    price: float,
    session_vwap: float,
    risk: float,
    mode: str,
    r_multiple: float,
) -> float:
    if mode == "half_vwap_distance":
        return price + (session_vwap - price) * 0.5
    if mode == "r_multiple":
        return price + risk * r_multiple if action == "BUY" else price - risk * r_multiple
    return session_vwap


def _bollinger_rsi_take_profit(
    *,
    action: str,
    price: float,
    middle_band: float | None,
    session_vwap: float | None,
    risk: float,
    mode: str,
    r_multiple: float,
) -> float | None:
    if mode == "vwap":
        return session_vwap
    if mode in {"two_r", "fixed_r"}:
        return price + risk * r_multiple if action == "BUY" else price - risk * r_multiple
    return middle_band


def _session_vwap_values(
    candles: list[ProjectXMarketCandle],
    *,
    session_start_time: str | None = None,
) -> tuple[list[str], list[float | None]]:
    session_keys: list[str] = []
    values: list[float | None] = []
    current_session_key: str | None = None
    cumulative_volume = 0.0
    cumulative_price_volume = 0.0
    current_vwap: float | None = None

    for candle in candles:
        session_key = _session_key_for_candle(candle, session_start_time=session_start_time)
        if session_key != current_session_key:
            current_session_key = session_key
            cumulative_volume = 0.0
            cumulative_price_volume = 0.0
            current_vwap = None

        volume = float(candle.volume or 0)
        if volume > 0:
            typical_price = (float(candle.high_price) + float(candle.low_price) + float(candle.close_price)) / 3
            cumulative_volume += volume
            cumulative_price_volume += typical_price * volume
            current_vwap = cumulative_price_volume / cumulative_volume

        session_keys.append(session_key)
        values.append(current_vwap)

    return session_keys, values


def _regular_session_date(candle: ProjectXMarketCandle) -> Any:
    return _as_utc(candle.candle_timestamp).astimezone(TRADING_TZ).date()


def _is_regular_session_candle(candle: ProjectXMarketCandle) -> bool:
    local_time = _as_utc(candle.candle_timestamp).astimezone(TRADING_TZ).time()
    return _REGULAR_SESSION_OPEN <= local_time <= _REGULAR_SESSION_CLOSE


def _regular_session_minutes_from_open(candle: ProjectXMarketCandle) -> int:
    local_time = _as_utc(candle.candle_timestamp).astimezone(TRADING_TZ).time()
    return (local_time.hour * 60 + local_time.minute) - (_REGULAR_SESSION_OPEN.hour * 60 + _REGULAR_SESSION_OPEN.minute)


def _has_recent_regular_session_vwap_bias(
    candles: list[ProjectXMarketCandle],
    session_vwaps: list[float | None],
    *,
    direction: str,
    lookback: int = 3,
) -> bool:
    if len(candles) < 2 or len(candles) != len(session_vwaps):
        return False

    start_index = max(0, len(candles) - lookback - 1)
    rows = list(zip(candles[start_index:-1], session_vwaps[start_index:-1]))
    if direction == "up":
        return any(vwap is not None and float(candle.close_price) > vwap for candle, vwap in rows)
    return any(vwap is not None and float(candle.close_price) < vwap for candle, vwap in rows)


def _session_key_for_candle(
    candle: ProjectXMarketCandle,
    *,
    session_start_time: str | None = None,
) -> str:
    timestamp = _as_utc(candle.candle_timestamp)
    if not session_start_time:
        return trading_day_date(timestamp).isoformat()

    session_time = _parse_session_time(session_start_time)
    local_timestamp = timestamp.astimezone(TRADING_TZ)
    session_date = local_timestamp.date()
    if local_timestamp.timetz().replace(tzinfo=None) < session_time:
        session_date -= timedelta(days=1)
    return session_date.isoformat()


def _bollinger_band_values(
    closes: list[float],
    *,
    period: int,
    stddev_multiplier: float,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    normalized_period = max(1, int(period))
    middle: list[float | None] = [None] * len(closes)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    if len(closes) < normalized_period:
        return middle, upper, lower

    for index in range(normalized_period - 1, len(closes)):
        window = closes[index - normalized_period + 1 : index + 1]
        average = _average(window)
        variance = sum((value - average) ** 2 for value in window) / normalized_period
        deviation = variance ** 0.5
        middle[index] = average
        upper[index] = average + deviation * stddev_multiplier
        lower[index] = average - deviation * stddev_multiplier

    return middle, upper, lower


def _ema_series(values: list[float], *, period: int) -> list[float]:
    if not values:
        return []
    normalized_period = max(1, int(period))
    alpha = 2 / (normalized_period + 1)
    output = [float(values[0])]
    for value in values[1:]:
        output.append((float(value) * alpha) + (output[-1] * (1 - alpha)))
    return output


def _normalized_strategy_period_values(strategy_type: str, *, fast_period: int, slow_period: int) -> tuple[int, int]:
    if strategy_type == _STRATEGY_EMA_SCALPING:
        return 9, 15
    if strategy_type == _STRATEGY_EMA_TREND_PULLBACK:
        return _EMA_TREND_PULLBACK_FAST_PERIOD, _EMA_TREND_PULLBACK_SLOW_PERIOD
    return int(fast_period), int(slow_period)


def _ema_trend_pullback_stop_from_ema(*, action: str, ema_value: float, stop_buffer_percent: float) -> float:
    multiplier = stop_buffer_percent / 100
    if action == "BUY":
        return ema_value * (1 - multiplier)
    return ema_value * (1 + multiplier)


def _ema_trend_pullback_stop_from_swing(*, action: str, swing_level: float, stop_buffer_percent: float) -> float:
    multiplier = stop_buffer_percent / 100
    if action == "BUY":
        return swing_level * (1 - multiplier)
    return swing_level * (1 + multiplier)


def _select_ema_trend_pullback_stop(
    *,
    action: str,
    stop_reference: str,
    ema_stop: float,
    micro_swing_stop: float,
) -> tuple[str, float]:
    if stop_reference == "ema19":
        return "ema19", ema_stop
    if stop_reference == "micro_swing":
        return "micro_swing", micro_swing_stop
    if action == "BUY":
        return ("ema19", ema_stop) if ema_stop <= micro_swing_stop else ("micro_swing", micro_swing_stop)
    return ("ema19", ema_stop) if ema_stop >= micro_swing_stop else ("micro_swing", micro_swing_stop)


def _select_ema_trend_pullback_target(
    *,
    action: str,
    entry_price: float,
    target_mode: str,
    target_candidates: dict[str, float],
) -> tuple[str | None, float | None]:
    ordered_modes = [target_mode, "recent_swing", "2r", "3r"]
    seen: set[str] = set()
    for mode in ordered_modes:
        if mode in seen:
            continue
        seen.add(mode)
        candidate = target_candidates.get(mode)
        if candidate is None:
            continue
        if action == "BUY" and candidate > entry_price:
            return mode, candidate
        if action == "SELL" and candidate < entry_price:
            return mode, candidate
    return None, None


def _fisher_transform_series(candles: list[ProjectXMarketCandle], *, length: int) -> list[float]:
    normalized_length = max(2, int(length))
    medians = [(float(candle.high_price) + float(candle.low_price)) / 2 for candle in candles]
    values: list[float] = []
    fishers: list[float] = []
    for index, median in enumerate(medians):
        start = max(0, index - normalized_length + 1)
        window = medians[start : index + 1]
        highest = max(window)
        lowest = min(window)
        previous_value = values[-1] if values else 0.0
        if highest == lowest:
            transformed = previous_value
        else:
            normalized = ((median - lowest) / (highest - lowest)) - 0.5
            transformed = 0.66 * normalized + 0.67 * previous_value
        transformed = max(min(transformed, 0.999), -0.999)
        values.append(transformed)
        previous_fisher = fishers[-1] if fishers else 0.0
        fishers.append(0.5 * previous_fisher + 0.5 * math.log((1 + transformed) / (1 - transformed)))
    return fishers


def _percent_change(start: float, end: float) -> float:
    if start == 0:
        return 0.0 if end == 0 else float("inf")
    return ((end - start) / abs(start)) * 100


def _align_candles_by_timestamp(
    asset_candles: list[ProjectXMarketCandle],
    benchmark_candles: list[ProjectXMarketCandle],
) -> tuple[list[ProjectXMarketCandle], list[ProjectXMarketCandle]]:
    asset_by_timestamp = {_as_utc(candle.candle_timestamp): candle for candle in asset_candles}
    benchmark_by_timestamp = {_as_utc(candle.candle_timestamp): candle for candle in benchmark_candles}
    timestamps = sorted(set(asset_by_timestamp).intersection(benchmark_by_timestamp))
    return (
        [asset_by_timestamp[timestamp] for timestamp in timestamps],
        [benchmark_by_timestamp[timestamp] for timestamp in timestamps],
    )


def _relative_volume_ratio(candles: list[ProjectXMarketCandle], *, lookback_bars: int) -> float:
    if len(candles) < lookback_bars + 1:
        return 0.0
    latest_volume = float(candles[-1].volume or 0)
    history = [float(candle.volume or 0) for candle in candles[-lookback_bars - 1 : -1] if float(candle.volume or 0) > 0]
    if not history:
        return 0.0
    baseline = _average(history)
    if baseline <= 0:
        return 0.0
    return latest_volume / baseline


def _nearest_directional_level(
    levels: list[SupportResistanceLevel],
    *,
    price: float,
    side: str,
) -> SupportResistanceLevel | None:
    if side == "support":
        candidates = [level for level in levels if level.price <= price]
    else:
        candidates = [level for level in levels if level.price >= price]
    if not candidates:
        return None
    return min(candidates, key=lambda level: (abs(level.price - price), -level.score))


def _nearest_entry_reference(
    *,
    candle: ProjectXMarketCandle,
    price: float,
    side: str,
    tolerance_percent: float,
    candidates: list[tuple[str, float | None]],
) -> tuple[str, float] | None:
    touches: list[tuple[str, float, float]] = []
    candle_low = float(candle.low_price)
    candle_high = float(candle.high_price)
    for label, candidate_price in candidates:
        if candidate_price is None or candidate_price <= 0:
            continue
        distance_percent = _level_distance_percent(price, candidate_price)
        if distance_percent > tolerance_percent:
            continue
        if side == "BUY":
            if candle_low > candidate_price * (1 + tolerance_percent / 100):
                continue
            if price < candidate_price * (1 - tolerance_percent / 100):
                continue
        else:
            if candle_high < candidate_price * (1 - tolerance_percent / 100):
                continue
            if price > candidate_price * (1 + tolerance_percent / 100):
                continue
        touches.append((label, candidate_price, distance_percent))
    if not touches:
        return None
    label, candidate_price, _distance_percent = min(touches, key=lambda item: item[2])
    return label, candidate_price


def _long_stop_anchor(
    *,
    latest: ProjectXMarketCandle,
    support: SupportResistanceLevel | None,
    session_vwap: float | None,
    ema_value: float | None,
) -> float:
    price = float(latest.close_price)
    candidates = [
        candidate
        for candidate in [
            support.price if support is not None else None,
            session_vwap,
            ema_value,
            float(latest.low_price),
        ]
        if candidate is not None and candidate < price
    ]
    if not candidates:
        return float(latest.low_price)
    return min(candidates)


def _short_stop_anchor(
    *,
    latest: ProjectXMarketCandle,
    resistance: SupportResistanceLevel | None,
    session_vwap: float | None,
    ema_value: float | None,
) -> float:
    price = float(latest.close_price)
    candidates = [
        candidate
        for candidate in [
            resistance.price if resistance is not None else None,
            session_vwap,
            ema_value,
            float(latest.high_price),
        ]
        if candidate is not None and candidate > price
    ]
    if not candidates:
        return float(latest.high_price)
    return max(candidates)


def _mean_stretch_hit(price: float, *, mean: float | None, threshold_percent: float, side: str) -> bool:
    if mean is None:
        return False
    if side == "below":
        return price <= mean * (1 - threshold_percent / 100)
    return price >= mean * (1 + threshold_percent / 100)


def _mean_source_label(*, vwap_hit: bool, ema_hit: bool) -> str:
    if vwap_hit and ema_hit:
        return "VWAP and mean EMA"
    if vwap_hit:
        return "VWAP"
    return "mean EMA"


def evaluate_donchian_breakout(
    candles: list[ProjectXMarketCandle],
    *,
    strategy_params: dict[str, Any] | None = None,
    position_state: OpenPositionState | None = None,
    latest_entry_plan: dict[str, Any] | None = None,
    base_order_size: float = 1.0,
) -> SignalResult:
    params = _normalize_strategy_params(_STRATEGY_DONCHIAN_BREAKOUT, strategy_params)
    closed = _closed_candles(candles)
    latest = closed[-1] if closed else None
    latest_timestamp = _as_utc(latest.candle_timestamp) if latest is not None else None
    latest_price = float(latest.close_price) if latest is not None else None
    entry_period = int(params["entry_period"])
    exit_period = int(params["exit_period"])
    atr_period = int(params["atr_period"])
    atr_stop_multiple = float(params["atr_stop_multiple"])
    reward_multiple = float(params["take_profit_r_multiple"])
    atr_trail_multiple = float(params["atr_trail_multiple"])
    reference_atr_percent = float(params["atr_size_reference_percent"])
    min_size_scale = float(params["min_size_scale"])
    current_position = position_state or OpenPositionState(net_qty=0.0, avg_entry_price=None, opened_at=None)
    minimum_required = max(entry_period + 1, exit_period + 1, atr_period + 1)
    raw_payload: dict[str, Any] = {
        "strategy_type": _STRATEGY_DONCHIAN_BREAKOUT,
        "settings": params,
        "closed_count": len(closed),
        "current_position_qty": current_position.net_qty,
        "position_side": current_position.side,
        "avg_entry_price": current_position.avg_entry_price,
        "opened_at": current_position.opened_at.isoformat() if current_position.opened_at is not None else None,
    }
    if latest_entry_plan:
        raw_payload["latest_entry_plan"] = latest_entry_plan

    if len(closed) < minimum_required:
        return SignalResult(
            action="HOLD",
            reason=f"Need at least {minimum_required} closed candles; found {len(closed)}.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    assert latest is not None
    previous = closed[-2]
    price = float(latest.close_price)
    latest_high = float(latest.high_price)
    latest_low = float(latest.low_price)
    previous_close = float(previous.close_price)
    upper_entry = max(float(candle.high_price) for candle in closed[-entry_period - 1 : -1])
    lower_entry = min(float(candle.low_price) for candle in closed[-entry_period - 1 : -1])
    upper_exit = max(float(candle.high_price) for candle in closed[-exit_period - 1 : -1])
    lower_exit = min(float(candle.low_price) for candle in closed[-exit_period - 1 : -1])
    atr = _atr_series(closed, period=atr_period)[-1]
    raw_payload["channels"] = {
        "entry_high": upper_entry,
        "entry_low": lower_entry,
        "exit_high": upper_exit,
        "exit_low": lower_exit,
    }
    if atr is None or atr <= 0:
        return SignalResult(
            action="HOLD",
            reason="ATR is not available on the latest closed candle for Donchian breakout evaluation.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    atr_percent = (atr / price * 100) if price > 0 else 0.0
    desired_entry_size, size_scale = _scaled_donchian_entry_size(
        base_order_size=base_order_size,
        atr_percent=atr_percent,
        reference_percent=reference_atr_percent,
        min_size_scale=min_size_scale,
    )
    raw_payload.update(
        {
            "atr": atr,
            "atr_percent": atr_percent,
            "desired_entry_size": desired_entry_size,
            "size_scale": size_scale,
            "base_order_size": float(base_order_size),
        }
    )
    long_breakout = price > upper_entry and previous_close <= upper_entry
    short_breakout = price < lower_entry and previous_close >= lower_entry

    def build_entry_payload(*, action: str, signal_category: str) -> dict[str, Any]:
        is_buy = action == "BUY"
        stop_loss = price - (atr * atr_stop_multiple) if is_buy else price + (atr * atr_stop_multiple)
        risk = abs(price - stop_loss)
        take_profit = price + (risk * reward_multiple) if is_buy else price - (risk * reward_multiple)
        effective_order_size = float(desired_entry_size)
        if signal_category == "reversal":
            effective_order_size = float(current_position.abs_qty + desired_entry_size)
        return {
            **raw_payload,
            "signal_category": signal_category,
            "entry_price": price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk": risk,
            "trail_amount": atr * atr_trail_multiple,
            "exit_channel_price": lower_exit if is_buy else upper_exit,
            "effective_order_size": effective_order_size,
            "target_position_qty": float(desired_entry_size if is_buy else -desired_entry_size),
            "current_position_qty": float(current_position.net_qty),
        }

    def build_exit_payload(*, action: str, exit_reason: str, trigger_price: float, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            **raw_payload,
            "signal_category": "exit",
            "exit_reason": exit_reason,
            "trigger_price": trigger_price,
            "effective_order_size": float(current_position.abs_qty),
            "target_position_qty": 0.0,
            "current_position_qty": float(current_position.net_qty),
        }
        if extra:
            payload.update(extra)
        return payload

    if current_position.side == "flat":
        if long_breakout:
            entry_payload = build_entry_payload(action="BUY", signal_category="entry")
            size_text = "" if size_scale >= 0.999 else f" Size scaled to {desired_entry_size} ({size_scale:.2f}x) because ATR is elevated."
            return SignalResult(
                action="BUY",
                reason=(
                    f"BUY on {entry_period}-bar Donchian breakout above {_format_strategy_price(upper_entry)}. "
                    f"ATR {_format_strategy_price(atr)} sets SL {_format_strategy_price(entry_payload['stop_loss'])}, "
                    f"TP {_format_strategy_price(entry_payload['take_profit'])}, exit channel {_format_strategy_price(lower_exit)}."
                    f"{size_text}"
                ),
                candle_timestamp=_as_utc(latest.candle_timestamp),
                price=price,
                raw_payload=entry_payload,
            )
        if short_breakout:
            entry_payload = build_entry_payload(action="SELL", signal_category="entry")
            size_text = "" if size_scale >= 0.999 else f" Size scaled to {desired_entry_size} ({size_scale:.2f}x) because ATR is elevated."
            return SignalResult(
                action="SELL",
                reason=(
                    f"SELL on {entry_period}-bar Donchian breakout below {_format_strategy_price(lower_entry)}. "
                    f"ATR {_format_strategy_price(atr)} sets SL {_format_strategy_price(entry_payload['stop_loss'])}, "
                    f"TP {_format_strategy_price(entry_payload['take_profit'])}, exit channel {_format_strategy_price(upper_exit)}."
                    f"{size_text}"
                ),
                candle_timestamp=_as_utc(latest.candle_timestamp),
                price=price,
                raw_payload=entry_payload,
            )
        return SignalResult(
            action="HOLD",
            reason=(
                f"No Donchian breakout: close {_format_strategy_price(price)} remains inside the "
                f"{entry_period}-bar channel {_format_strategy_price(lower_entry)}-{_format_strategy_price(upper_entry)}."
            ),
            candle_timestamp=_as_utc(latest.candle_timestamp),
            price=price,
            raw_payload=raw_payload,
        )

    entry_context = _candles_since_timestamp(closed, current_position.opened_at)
    highest_since_entry = max(float(candle.high_price) for candle in entry_context) if entry_context else latest_high
    lowest_since_entry = min(float(candle.low_price) for candle in entry_context) if entry_context else latest_low
    plan_stop_loss = _optional_float(latest_entry_plan.get("stop_loss")) if isinstance(latest_entry_plan, dict) else None
    plan_take_profit = _optional_float(latest_entry_plan.get("take_profit")) if isinstance(latest_entry_plan, dict) else None

    if current_position.side == "long":
        long_stop_loss = (
            plan_stop_loss
            if plan_stop_loss is not None
            else (current_position.avg_entry_price or price) - (atr * atr_stop_multiple)
        )
        long_trail_stop = highest_since_entry - (atr * atr_trail_multiple)
        if short_breakout:
            if current_position.abs_qty > desired_entry_size:
                return SignalResult(
                    action="SELL",
                    reason=f"SELL exit: price breached the {exit_period}-bar lower exit channel at {_format_strategy_price(lower_exit)}.",
                    candle_timestamp=_as_utc(latest.candle_timestamp),
                    price=price,
                    raw_payload=build_exit_payload(
                        action="SELL",
                        exit_reason="opposite_exit_channel",
                        trigger_price=lower_exit,
                        extra={"exit_channel_price": lower_exit},
                    ),
                )
            entry_payload = build_entry_payload(action="SELL", signal_category="reversal")
            return SignalResult(
                action="SELL",
                reason=(
                    f"SELL reversal on {entry_period}-bar downside breakout below {_format_strategy_price(lower_entry)}; "
                    f"the current long exits and flips short with SL {_format_strategy_price(entry_payload['stop_loss'])}."
                ),
                candle_timestamp=_as_utc(latest.candle_timestamp),
                price=price,
                raw_payload=entry_payload,
            )
        if latest_low <= long_stop_loss:
            return SignalResult(
                action="SELL",
                reason=f"SELL exit: ATR stop {_format_strategy_price(long_stop_loss)} was breached.",
                candle_timestamp=_as_utc(latest.candle_timestamp),
                price=price,
                raw_payload=build_exit_payload(
                    action="SELL",
                    exit_reason="atr_stop",
                    trigger_price=long_stop_loss,
                    extra={"stop_loss": long_stop_loss},
                ),
            )
        if plan_take_profit is not None and latest_high >= plan_take_profit:
            return SignalResult(
                action="SELL",
                reason=f"SELL exit: fixed-R target {_format_strategy_price(plan_take_profit)} was hit.",
                candle_timestamp=_as_utc(latest.candle_timestamp),
                price=price,
                raw_payload=build_exit_payload(
                    action="SELL",
                    exit_reason="fixed_r_target",
                    trigger_price=plan_take_profit,
                    extra={"take_profit": plan_take_profit},
                ),
            )
        if latest_low <= long_trail_stop:
            return SignalResult(
                action="SELL",
                reason=f"SELL exit: ATR trailing stop {_format_strategy_price(long_trail_stop)} was breached.",
                candle_timestamp=_as_utc(latest.candle_timestamp),
                price=price,
                raw_payload=build_exit_payload(
                    action="SELL",
                    exit_reason="atr_trail",
                    trigger_price=long_trail_stop,
                    extra={"trail_stop": long_trail_stop},
                ),
            )
        if latest_low <= lower_exit:
            return SignalResult(
                action="SELL",
                reason=f"SELL exit: price breached the {exit_period}-bar lower exit channel at {_format_strategy_price(lower_exit)}.",
                candle_timestamp=_as_utc(latest.candle_timestamp),
                price=price,
                raw_payload=build_exit_payload(
                    action="SELL",
                    exit_reason="opposite_exit_channel",
                    trigger_price=lower_exit,
                    extra={"exit_channel_price": lower_exit},
                ),
            )
        return SignalResult(
            action="HOLD",
            reason=(
                f"Long remains open. Exit channel {_format_strategy_price(lower_exit)}, "
                f"ATR stop {_format_strategy_price(long_stop_loss)}, trail {_format_strategy_price(long_trail_stop)}."
            ),
            candle_timestamp=_as_utc(latest.candle_timestamp),
            price=price,
            raw_payload=raw_payload,
        )

    short_stop_loss = (
        plan_stop_loss
        if plan_stop_loss is not None
        else (current_position.avg_entry_price or price) + (atr * atr_stop_multiple)
    )
    short_trail_stop = lowest_since_entry + (atr * atr_trail_multiple)
    if long_breakout:
        if current_position.abs_qty > desired_entry_size:
            return SignalResult(
                action="BUY",
                reason=f"BUY exit: price breached the {exit_period}-bar upper exit channel at {_format_strategy_price(upper_exit)}.",
                candle_timestamp=_as_utc(latest.candle_timestamp),
                price=price,
                raw_payload=build_exit_payload(
                    action="BUY",
                    exit_reason="opposite_exit_channel",
                    trigger_price=upper_exit,
                    extra={"exit_channel_price": upper_exit},
                ),
            )
        entry_payload = build_entry_payload(action="BUY", signal_category="reversal")
        return SignalResult(
            action="BUY",
            reason=(
                f"BUY reversal on {entry_period}-bar upside breakout above {_format_strategy_price(upper_entry)}; "
                f"the current short exits and flips long with SL {_format_strategy_price(entry_payload['stop_loss'])}."
            ),
            candle_timestamp=_as_utc(latest.candle_timestamp),
            price=price,
            raw_payload=entry_payload,
        )
    if latest_high >= short_stop_loss:
        return SignalResult(
            action="BUY",
            reason=f"BUY exit: ATR stop {_format_strategy_price(short_stop_loss)} was breached.",
            candle_timestamp=_as_utc(latest.candle_timestamp),
            price=price,
            raw_payload=build_exit_payload(
                action="BUY",
                exit_reason="atr_stop",
                trigger_price=short_stop_loss,
                extra={"stop_loss": short_stop_loss},
            ),
        )
    if plan_take_profit is not None and latest_low <= plan_take_profit:
        return SignalResult(
            action="BUY",
            reason=f"BUY exit: fixed-R target {_format_strategy_price(plan_take_profit)} was hit.",
            candle_timestamp=_as_utc(latest.candle_timestamp),
            price=price,
            raw_payload=build_exit_payload(
                action="BUY",
                exit_reason="fixed_r_target",
                trigger_price=plan_take_profit,
                extra={"take_profit": plan_take_profit},
            ),
        )
    if latest_high >= short_trail_stop:
        return SignalResult(
            action="BUY",
            reason=f"BUY exit: ATR trailing stop {_format_strategy_price(short_trail_stop)} was breached.",
            candle_timestamp=_as_utc(latest.candle_timestamp),
            price=price,
            raw_payload=build_exit_payload(
                action="BUY",
                exit_reason="atr_trail",
                trigger_price=short_trail_stop,
                extra={"trail_stop": short_trail_stop},
            ),
        )
    if latest_high >= upper_exit:
        return SignalResult(
            action="BUY",
            reason=f"BUY exit: price breached the {exit_period}-bar upper exit channel at {_format_strategy_price(upper_exit)}.",
            candle_timestamp=_as_utc(latest.candle_timestamp),
            price=price,
            raw_payload=build_exit_payload(
                action="BUY",
                exit_reason="opposite_exit_channel",
                trigger_price=upper_exit,
                extra={"exit_channel_price": upper_exit},
            ),
        )
    return SignalResult(
        action="HOLD",
        reason=(
            f"Short remains open. Exit channel {_format_strategy_price(upper_exit)}, "
            f"ATR stop {_format_strategy_price(short_stop_loss)}, trail {_format_strategy_price(short_trail_stop)}."
        ),
        candle_timestamp=_as_utc(latest.candle_timestamp),
        price=price,
        raw_payload=raw_payload,
    )


def evaluate_supertrend_pivot_points(
    *,
    signal_timeframe_candles: list[ProjectXMarketCandle],
    daily_candles: list[ProjectXMarketCandle],
    strategy_params: dict[str, Any] | None = None,
) -> SignalResult:
    params = _normalize_strategy_params(_STRATEGY_SUPERTREND_PIVOT, strategy_params)
    signal_closed = _closed_candles(signal_timeframe_candles)
    daily_closed = _closed_candles(daily_candles)[-int(params["daily_bars"]) :]
    latest = signal_closed[-1] if signal_closed else None
    previous = signal_closed[-2] if len(signal_closed) >= 2 else None
    latest_timestamp = _as_utc(latest.candle_timestamp) if latest is not None else None
    latest_price = float(latest.close_price) if latest is not None else None
    minimum_signal_candles = max(
        int(params["supertrend_period"]) + 2,
        int(params["chop_lookback_bars"]) + 1,
        5,
    )
    if len(signal_closed) < minimum_signal_candles or len(daily_closed) < 1:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Need at least {minimum_signal_candles} closed signal candles and 1 daily candle; "
                f"found {len(signal_closed)} signal and {len(daily_closed)} daily."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload={
                "strategy_type": _STRATEGY_SUPERTREND_PIVOT,
                "settings": params,
                "closed_counts": {"signal": len(signal_closed), "1D": len(daily_closed)},
            },
        )

    previous_day = daily_closed[-1]
    previous_day_high = float(previous_day.high_price)
    previous_day_low = float(previous_day.low_price)
    previous_day_close = float(previous_day.close_price)
    raw_payload: dict[str, Any] = {
        "strategy_type": _STRATEGY_SUPERTREND_PIVOT,
        "settings": params,
        "closed_counts": {"signal": len(signal_closed), "1D": len(daily_closed)},
        "daily_pivot_source": {
            "timestamp": _as_utc(previous_day.candle_timestamp).isoformat(),
            "high": previous_day_high,
            "low": previous_day_low,
            "close": previous_day_close,
        },
    }
    if previous_day_high <= previous_day_low:
        return SignalResult(
            action="HOLD",
            reason="Previous daily candle range is not usable for pivot calculations.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    pivot_levels = _build_classic_pivot_levels(previous_day)
    raw_payload["pivot_levels"] = [_serialize_pivot_level(level) for level in pivot_levels]
    supertrend_states = _compute_supertrend_states(
        signal_closed,
        period=int(params["supertrend_period"]),
        multiplier=float(params["supertrend_multiplier"]),
    )
    if len(supertrend_states) < 2:
        return SignalResult(
            action="HOLD",
            reason="Not enough signal candles to calculate Supertrend.",
            candle_timestamp=latest_timestamp,
            price=latest_price,
            raw_payload=raw_payload,
        )

    latest_signal = signal_closed[-1]
    latest_close = float(latest_signal.close_price)
    latest_state = supertrend_states[-1]
    raw_payload["supertrend"] = {
        "direction": latest_state.direction,
        "value": latest_state.value,
        "upper_band": latest_state.upper_band,
        "lower_band": latest_state.lower_band,
    }
    chop_metrics = _supertrend_chop_metrics(
        supertrend_states,
        signal_closed,
        lookback_bars=int(params["chop_lookback_bars"]),
        max_flips=int(params["chop_max_flips"]),
        max_range_percent=float(params["chop_max_range_percent"]),
    )
    raw_payload["chop"] = chop_metrics
    if chop_metrics["is_choppy"]:
        return SignalResult(
            action="HOLD",
            reason=(
                f"Skipping trade: Supertrend flipped {chop_metrics['flip_count']} times over the last "
                f"{chop_metrics['lookback_bars']} bars while price stayed within "
                f"{_format_percent(chop_metrics['range_percent'])}%."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_close,
            raw_payload=raw_payload,
        )

    tolerance_percent = float(params["pivot_tolerance_percent"])
    stop_beyond_percent = float(params["stop_beyond_level_percent"])
    reward_multiple = float(params["take_profit_r_multiple"])

    if latest_state.direction == "bullish":
        reclaim = _find_long_pivot_reclaim(
            pivot_levels,
            latest_signal,
            previous,
            tolerance_percent=tolerance_percent,
        )
        if reclaim is None:
            return SignalResult(
                action="HOLD",
                reason="Supertrend is bullish, but price did not reclaim a daily pivot or support level on the latest candle.",
                candle_timestamp=latest_timestamp,
                price=latest_close,
                raw_payload=raw_payload,
            )

        level, distance_percent = reclaim
        stop_loss = level.price * (1 - stop_beyond_percent / 100)
        risk = latest_close - stop_loss
        raw_payload.update(
            {
                "trigger_level": _serialize_pivot_level(level),
                "distance_percent": distance_percent,
                "stop_loss": stop_loss,
            }
        )
        if risk <= 0:
            return SignalResult(
                action="HOLD",
                reason="Bullish reclaim was detected, but the calculated long risk was not positive.",
                candle_timestamp=latest_timestamp,
                price=latest_close,
                raw_payload=raw_payload,
            )

        take_profit, next_level, r_target_price = _resolve_pivot_take_profit(
            pivot_levels,
            action="BUY",
            entry_price=latest_close,
            risk=risk,
            reward_multiple=reward_multiple,
        )
        take_profit_source = _describe_take_profit_source(next_level, selected_price=take_profit, r_target_price=r_target_price)
        raw_payload.update(
            {
                "take_profit": take_profit,
                "risk": risk,
                "r_target_price": r_target_price,
                "take_profit_source": take_profit_source,
                "next_target_level": _serialize_pivot_level(next_level) if next_level is not None else None,
            }
        )
        return SignalResult(
            action="BUY",
            reason=(
                f"BUY with Supertrend bullish after reclaiming daily {level.name} "
                f"{_format_strategy_price(level.price)}. SL {_format_strategy_price(stop_loss)}, "
                f"TP {_format_strategy_price(take_profit)} ({take_profit_source})."
            ),
            candle_timestamp=latest_timestamp,
            price=latest_close,
            raw_payload=raw_payload,
        )

    rejection = _find_short_pivot_rejection(
        pivot_levels,
        latest_signal,
        tolerance_percent=tolerance_percent,
    )
    if rejection is None:
        return SignalResult(
            action="HOLD",
            reason="Supertrend is bearish, but price did not reject a daily pivot or resistance level on the latest candle.",
            candle_timestamp=latest_timestamp,
            price=latest_close,
            raw_payload=raw_payload,
        )

    level, distance_percent = rejection
    stop_loss = level.price * (1 + stop_beyond_percent / 100)
    risk = stop_loss - latest_close
    raw_payload.update(
        {
            "trigger_level": _serialize_pivot_level(level),
            "distance_percent": distance_percent,
            "stop_loss": stop_loss,
        }
    )
    if risk <= 0:
        return SignalResult(
            action="HOLD",
            reason="Bearish rejection was detected, but the calculated short risk was not positive.",
            candle_timestamp=latest_timestamp,
            price=latest_close,
            raw_payload=raw_payload,
        )

    take_profit, next_level, r_target_price = _resolve_pivot_take_profit(
        pivot_levels,
        action="SELL",
        entry_price=latest_close,
        risk=risk,
        reward_multiple=reward_multiple,
    )
    take_profit_source = _describe_take_profit_source(next_level, selected_price=take_profit, r_target_price=r_target_price)
    raw_payload.update(
        {
            "take_profit": take_profit,
            "risk": risk,
            "r_target_price": r_target_price,
            "take_profit_source": take_profit_source,
            "next_target_level": _serialize_pivot_level(next_level) if next_level is not None else None,
        }
    )
    return SignalResult(
        action="SELL",
        reason=(
            f"SELL with Supertrend bearish after rejecting daily {level.name} "
            f"{_format_strategy_price(level.price)}. SL {_format_strategy_price(stop_loss)}, "
            f"TP {_format_strategy_price(take_profit)} ({take_profit_source})."
        ),
        candle_timestamp=latest_timestamp,
        price=latest_close,
        raw_payload=raw_payload,
    )


def _build_classic_pivot_levels(previous_day: ProjectXMarketCandle) -> list[PivotLevel]:
    high = float(previous_day.high_price)
    low = float(previous_day.low_price)
    close = float(previous_day.close_price)
    pivot = (high + low + close) / 3
    range_size = high - low
    return sorted(
        [
            PivotLevel(name="S3", kind="support", price=pivot - 2 * range_size),
            PivotLevel(name="S2", kind="support", price=pivot - range_size),
            PivotLevel(name="S1", kind="support", price=2 * pivot - high),
            PivotLevel(name="P", kind="pivot", price=pivot),
            PivotLevel(name="R1", kind="resistance", price=2 * pivot - low),
            PivotLevel(name="R2", kind="resistance", price=pivot + range_size),
            PivotLevel(name="R3", kind="resistance", price=pivot + 2 * range_size),
        ],
        key=lambda level: level.price,
    )


def _compute_supertrend_states(
    candles: list[ProjectXMarketCandle],
    *,
    period: int,
    multiplier: float,
) -> list[SupertrendState]:
    if len(candles) < period:
        return []

    true_ranges: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        high = float(candle.high_price)
        low = float(candle.low_price)
        close = float(candle.close_price)
        if previous_close is None:
            true_ranges.append(high - low)
        else:
            true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        previous_close = close

    atr_values: list[float | None] = [None] * len(candles)
    atr = _average(true_ranges[:period])
    atr_values[period - 1] = atr
    for index in range(period, len(candles)):
        atr = ((atr * (period - 1)) + true_ranges[index]) / period
        atr_values[index] = atr

    states: list[SupertrendState] = []
    previous_final_upper: float | None = None
    previous_final_lower: float | None = None
    previous_supertrend: float | None = None
    previous_close = None
    for index, candle in enumerate(candles):
        atr_value = atr_values[index]
        if atr_value is None:
            previous_close = float(candle.close_price)
            continue

        high = float(candle.high_price)
        low = float(candle.low_price)
        close = float(candle.close_price)
        hl2 = (high + low) / 2
        basic_upper = hl2 + multiplier * atr_value
        basic_lower = hl2 - multiplier * atr_value

        if previous_final_upper is None or previous_final_lower is None or previous_supertrend is None or previous_close is None:
            final_upper = basic_upper
            final_lower = basic_lower
            supertrend = final_lower if close >= hl2 else final_upper
        else:
            final_upper = basic_upper if basic_upper < previous_final_upper or previous_close > previous_final_upper else previous_final_upper
            final_lower = basic_lower if basic_lower > previous_final_lower or previous_close < previous_final_lower else previous_final_lower
            if previous_supertrend == previous_final_upper:
                supertrend = final_upper if close <= final_upper else final_lower
            else:
                supertrend = final_lower if close >= final_lower else final_upper

        direction = "bullish" if supertrend == final_lower else "bearish"
        states.append(
            SupertrendState(
                timestamp=_as_utc(candle.candle_timestamp),
                value=supertrend,
                direction=direction,
                upper_band=final_upper,
                lower_band=final_lower,
            )
        )
        previous_final_upper = final_upper
        previous_final_lower = final_lower
        previous_supertrend = supertrend
        previous_close = close

    return states


def _supertrend_chop_metrics(
    states: list[SupertrendState],
    candles: list[ProjectXMarketCandle],
    *,
    lookback_bars: int,
    max_flips: int,
    max_range_percent: float,
) -> dict[str, Any]:
    window_size = min(len(states), max(2, int(lookback_bars)))
    window_states = states[-window_size:]
    window_candles = candles[-window_size:]
    flip_count = sum(1 for index in range(1, len(window_states)) if window_states[index].direction != window_states[index - 1].direction)
    high = max(float(candle.high_price) for candle in window_candles)
    low = min(float(candle.low_price) for candle in window_candles)
    reference = float(window_candles[-1].close_price)
    range_percent = ((high - low) / abs(reference) * 100) if abs(reference) > 1e-9 else 0.0
    return {
        "lookback_bars": window_size,
        "flip_count": flip_count,
        "range_percent": range_percent,
        "is_choppy": flip_count >= max_flips and range_percent <= max_range_percent,
    }


def _find_long_pivot_reclaim(
    levels: list[PivotLevel],
    latest: ProjectXMarketCandle,
    previous: ProjectXMarketCandle | None,
    *,
    tolerance_percent: float,
) -> tuple[PivotLevel, float] | None:
    latest_open = float(latest.open_price)
    latest_low = float(latest.low_price)
    latest_close = float(latest.close_price)
    if latest_close <= latest_open:
        return None

    previous_close = float(previous.close_price) if previous is not None else None
    candidates: list[tuple[PivotLevel, float]] = []
    for level in levels:
        if level.kind not in {"pivot", "support"}:
            continue
        if latest_close <= level.price:
            continue
        if latest_low > level.price * (1 + tolerance_percent / 100):
            continue
        if previous_close is not None and previous_close > level.price and latest_open > level.price:
            continue
        distance_percent = _level_distance_percent(latest_close, level.price)
        candidates.append((level, distance_percent))

    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[1], -item[0].price))


def _find_short_pivot_rejection(
    levels: list[PivotLevel],
    latest: ProjectXMarketCandle,
    *,
    tolerance_percent: float,
) -> tuple[PivotLevel, float] | None:
    latest_open = float(latest.open_price)
    latest_high = float(latest.high_price)
    latest_close = float(latest.close_price)
    if latest_close >= latest_open:
        return None

    candidates: list[tuple[PivotLevel, float]] = []
    for level in levels:
        if level.kind not in {"pivot", "resistance"}:
            continue
        if latest_close >= level.price:
            continue
        if latest_high < level.price * (1 - tolerance_percent / 100):
            continue
        distance_percent = _level_distance_percent(level.price, latest_close)
        candidates.append((level, distance_percent))

    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[1], item[0].price))


def _resolve_pivot_take_profit(
    levels: list[PivotLevel],
    *,
    action: str,
    entry_price: float,
    risk: float,
    reward_multiple: float,
) -> tuple[float, PivotLevel | None, float]:
    r_target_price = entry_price + risk * reward_multiple if action == "BUY" else entry_price - risk * reward_multiple
    if action == "BUY":
        candidates = [level for level in levels if level.price > entry_price]
        next_level = min(candidates, key=lambda level: level.price) if candidates else None
        take_profit = min(next_level.price, r_target_price) if next_level is not None else r_target_price
        return take_profit, next_level, r_target_price

    candidates = [level for level in levels if level.price < entry_price]
    next_level = max(candidates, key=lambda level: level.price) if candidates else None
    take_profit = max(next_level.price, r_target_price) if next_level is not None else r_target_price
    return take_profit, next_level, r_target_price


def _describe_take_profit_source(
    next_level: PivotLevel | None,
    *,
    selected_price: float,
    r_target_price: float,
) -> str:
    if next_level is not None and abs(selected_price - next_level.price) <= 1e-9:
        return f"next daily {next_level.name}"
    if abs(selected_price - r_target_price) <= 1e-9:
        return "2R"
    return "next pivot / 2R"


def _serialize_pivot_level(level: PivotLevel) -> dict[str, Any]:
    return {
        "name": level.name,
        "kind": level.kind,
        "price": level.price,
    }


def _closed_candles(candles: list[ProjectXMarketCandle]) -> list[ProjectXMarketCandle]:
    closed = [candle for candle in candles if not bool(candle.is_partial)]
    closed.sort(key=lambda candle: _as_utc(candle.candle_timestamp))
    return closed


def _aligned_candle_pairs_by_timestamp(
    left: list[ProjectXMarketCandle],
    right: list[ProjectXMarketCandle],
) -> list[tuple[ProjectXMarketCandle, ProjectXMarketCandle]]:
    right_by_timestamp = {_as_utc(candle.candle_timestamp): candle for candle in right}
    pairs: list[tuple[ProjectXMarketCandle, ProjectXMarketCandle]] = []
    for candle in left:
        paired = right_by_timestamp.get(_as_utc(candle.candle_timestamp))
        if paired is not None:
            pairs.append((candle, paired))
    return pairs


def _ema_series(values: list[float], *, period: int) -> list[float | None]:
    normalized_period = max(1, int(period))
    output: list[float | None] = [None] * len(values)
    if len(values) < normalized_period:
        return output

    multiplier = 2.0 / (normalized_period + 1)
    ema = _average(values[:normalized_period])
    output[normalized_period - 1] = ema
    for index in range(normalized_period, len(values)):
        ema = (values[index] - ema) * multiplier + ema
        output[index] = ema
    return output


def _macd_state(
    closes: list[float],
    *,
    fast_period: int,
    slow_period: int,
    signal_period: int,
) -> dict[str, Any] | None:
    fast_ema = _ema_series(closes, period=fast_period)
    slow_ema = _ema_series(closes, period=slow_period)
    macd_line: list[float | None] = []
    for fast_value, slow_value in zip(fast_ema, slow_ema):
        if fast_value is None or slow_value is None:
            macd_line.append(None)
        else:
            macd_line.append(fast_value - slow_value)

    compact_macd = [value for value in macd_line if value is not None]
    aligned_signal: list[float | None] = [None] * len(macd_line)
    if compact_macd:
        compact_signal = _ema_series(compact_macd, period=signal_period)
        compact_index = 0
        for index, value in enumerate(macd_line):
            if value is None:
                continue
            aligned_signal[index] = compact_signal[compact_index]
            compact_index += 1

    aligned_pairs = [
        (macd_value, signal_value)
        for macd_value, signal_value in zip(macd_line, aligned_signal)
        if macd_value is not None and signal_value is not None
    ]
    if len(aligned_pairs) < 2:
        return None

    previous_macd, previous_signal = aligned_pairs[-2]
    current_macd, current_signal = aligned_pairs[-1]
    bullish_signal_cross = previous_macd <= previous_signal and current_macd > current_signal
    bearish_signal_cross = previous_macd >= previous_signal and current_macd < current_signal
    bullish_zero_cross = previous_macd <= 0 < current_macd
    bearish_zero_cross = previous_macd >= 0 > current_macd
    return {
        "previous_macd": previous_macd,
        "previous_signal": previous_signal,
        "current_macd": current_macd,
        "current_signal": current_signal,
        "bullish_signal_cross": bullish_signal_cross,
        "bearish_signal_cross": bearish_signal_cross,
        "bullish_zero_cross": bullish_zero_cross,
        "bearish_zero_cross": bearish_zero_cross,
    }


def _atr_series(candles: list[ProjectXMarketCandle], *, period: int) -> list[float | None]:
    normalized_period = max(1, int(period))
    output: list[float | None] = [None] * len(candles)
    if len(candles) < normalized_period:
        return output

    true_ranges: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        high = float(candle.high_price)
        low = float(candle.low_price)
        if previous_close is None:
            true_range = high - low
        else:
            true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        true_ranges.append(true_range)
        previous_close = float(candle.close_price)

    atr = _average(true_ranges[:normalized_period])
    output[normalized_period - 1] = atr
    for index in range(normalized_period, len(candles)):
        atr = ((atr * (normalized_period - 1)) + true_ranges[index]) / normalized_period
        output[index] = atr
    return output


def _relative_volume_ratio(
    candles: list[ProjectXMarketCandle],
    *,
    period: int | None = None,
    lookback_bars: int | None = None,
) -> float | None:
    fallback_zero = lookback_bars is not None and period is None
    normalized_period = max(1, int(period if period is not None else lookback_bars if lookback_bars is not None else 1))
    if len(candles) < normalized_period + 1:
        return 0.0 if fallback_zero else None

    latest_volume = float(candles[-1].volume or 0)
    baseline_volumes = [float(candle.volume or 0) for candle in candles[-(normalized_period + 1) : -1]]
    average_baseline_volume = _average(baseline_volumes)
    if average_baseline_volume <= 0:
        return 0.0 if fallback_zero else None
    return latest_volume / average_baseline_volume


def _rsi_series(closes: list[float], *, period: int) -> list[float | None]:
    normalized_period = max(1, int(period))
    output: list[float | None] = [None] * len(closes)
    if len(closes) < normalized_period + 1:
        return output

    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, len(closes)):
        change = closes[index] - closes[index - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    average_gain = _average(gains[:normalized_period])
    average_loss = _average(losses[:normalized_period])
    output[normalized_period] = _wilder_rsi(average_gain, average_loss)
    for index in range(normalized_period, len(gains)):
        average_gain = ((average_gain * (normalized_period - 1)) + gains[index]) / normalized_period
        average_loss = ((average_loss * (normalized_period - 1)) + losses[index]) / normalized_period
        output[index + 1] = _wilder_rsi(average_gain, average_loss)

    return output


def _adx_series(candles: list[ProjectXMarketCandle], *, period: int) -> list[float | None]:
    normalized_period = max(1, int(period))
    output: list[float | None] = [None] * len(candles)
    if len(candles) < normalized_period * 2:
        return output

    true_ranges = [0.0] * len(candles)
    plus_dm = [0.0] * len(candles)
    minus_dm = [0.0] * len(candles)
    for index in range(1, len(candles)):
        current_high = float(candles[index].high_price)
        current_low = float(candles[index].low_price)
        previous_high = float(candles[index - 1].high_price)
        previous_low = float(candles[index - 1].low_price)
        previous_close = float(candles[index - 1].close_price)

        up_move = current_high - previous_high
        down_move = previous_low - current_low
        plus_dm[index] = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm[index] = down_move if down_move > up_move and down_move > 0 else 0.0
        true_ranges[index] = max(
            current_high - current_low,
            abs(current_high - previous_close),
            abs(current_low - previous_close),
        )

    smoothed_tr = sum(true_ranges[1 : normalized_period + 1])
    smoothed_plus_dm = sum(plus_dm[1 : normalized_period + 1])
    smoothed_minus_dm = sum(minus_dm[1 : normalized_period + 1])
    dx_values: list[float | None] = [None] * len(candles)
    dx_values[normalized_period] = _directional_movement_dx(
        smoothed_tr,
        smoothed_plus_dm,
        smoothed_minus_dm,
    )

    for index in range(normalized_period + 1, len(candles)):
        smoothed_tr = smoothed_tr - (smoothed_tr / normalized_period) + true_ranges[index]
        smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / normalized_period) + plus_dm[index]
        smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / normalized_period) + minus_dm[index]
        dx_values[index] = _directional_movement_dx(
            smoothed_tr,
            smoothed_plus_dm,
            smoothed_minus_dm,
        )

    first_adx_index = normalized_period * 2 - 1
    first_window = [value for value in dx_values[normalized_period : first_adx_index + 1] if value is not None]
    if len(first_window) < normalized_period:
        return output

    adx = _average(first_window)
    output[first_adx_index] = adx
    for index in range(first_adx_index + 1, len(candles)):
        dx = dx_values[index]
        if dx is None:
            continue
        adx = ((adx * (normalized_period - 1)) + dx) / normalized_period
        output[index] = adx

    return output


def _wilder_rsi(average_gain: float, average_loss: float) -> float:
    if average_loss <= 0:
        return 100.0 if average_gain > 0 else 50.0
    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def _directional_movement_dx(smoothed_tr: float, smoothed_plus_dm: float, smoothed_minus_dm: float) -> float:
    if smoothed_tr <= 0:
        return 0.0
    plus_di = 100 * smoothed_plus_dm / smoothed_tr
    minus_di = 100 * smoothed_minus_dm / smoothed_tr
    denominator = plus_di + minus_di
    if denominator <= 0:
        return 0.0
    return abs(plus_di - minus_di) / denominator * 100


def _detect_support_resistance_levels(
    candles: list[ProjectXMarketCandle],
    *,
    timeframe: str,
    window_size: int,
) -> list[SupportResistanceLevel]:
    span = max(3, int(window_size))
    if span % 2 == 0:
        span += 1
    radius = span // 2
    if len(candles) < span:
        return []

    levels: list[SupportResistanceLevel] = []
    timeframe_weight = 2.0 if timeframe == "4H" else 1.0
    last_index = len(candles) - 1
    for index in range(radius, len(candles) - radius):
        center = candles[index]
        window = candles[index - radius : index + radius + 1]
        highs = [float(candle.high_price) for candle in window]
        lows = [float(candle.low_price) for candle in window]
        center_high = float(center.high_price)
        center_low = float(center.low_price)
        recency_score = index / last_index if last_index > 0 else 0
        score = timeframe_weight + recency_score
        timestamp = _as_utc(center.candle_timestamp)

        if center_high == max(highs) and highs.count(center_high) == 1:
            levels.append(
                SupportResistanceLevel(
                    side="resistance",
                    price=center_high,
                    timestamp=timestamp,
                    timeframe=timeframe,
                    source_index=index,
                    score=score,
                )
            )
        if center_low == min(lows) and lows.count(center_low) == 1:
            levels.append(
                SupportResistanceLevel(
                    side="support",
                    price=center_low,
                    timestamp=timestamp,
                    timeframe=timeframe,
                    source_index=index,
                    score=score,
                )
            )

    return levels


def _filter_clustered_levels(
    levels: list[SupportResistanceLevel],
    *,
    tolerance_percent: float,
) -> list[SupportResistanceLevel]:
    kept: list[SupportResistanceLevel] = []
    for level in sorted(levels, key=lambda item: (-item.score, -item.timestamp.timestamp())):
        if any(_level_distance_percent(level.price, existing.price) <= tolerance_percent for existing in kept):
            continue
        kept.append(level)
    return sorted(kept, key=lambda item: item.price)


def _nearest_level_touch(
    *,
    levels: list[SupportResistanceLevel],
    price: float,
    side: str,
    tolerance_percent: float,
) -> tuple[SupportResistanceLevel, float] | None:
    touches: list[tuple[SupportResistanceLevel, float]] = []
    for level in levels:
        if side == "support" and price < level.price:
            continue
        if side == "resistance" and price > level.price:
            continue
        distance_percent = _level_distance_percent(price, level.price)
        if distance_percent <= tolerance_percent:
            touches.append((level, distance_percent))
    if not touches:
        return None
    return min(touches, key=lambda item: (item[1], -item[0].score))


def _choose_nearest_touch(
    support_touch: tuple[SupportResistanceLevel, float] | None,
    resistance_touch: tuple[SupportResistanceLevel, float] | None,
) -> tuple[SupportResistanceLevel, float] | None:
    if support_touch is None:
        return resistance_touch
    if resistance_touch is None:
        return support_touch
    return min([support_touch, resistance_touch], key=lambda item: (item[1], -item[0].score))


def _level_distance_percent(left: float, right: float) -> float:
    denominator = (abs(left) + abs(right)) / 2
    if denominator <= 0:
        return 0.0 if left == right else float("inf")
    return abs(left - right) / denominator * 100


def _serialize_support_resistance_level(level: SupportResistanceLevel) -> dict[str, Any]:
    return {
        "side": level.side,
        "price": level.price,
        "timestamp": level.timestamp.isoformat(),
        "timeframe": level.timeframe,
        "source_index": level.source_index,
        "score": level.score,
    }


def _format_strategy_price(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _format_percent(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _candles_timeframe_label(candles: list[ProjectXMarketCandle]) -> str | None:
    if not candles:
        return None
    first = candles[0]
    return _format_timeframe_label(str(first.unit), int(first.unit_number))


def _format_timeframe_label(unit: str, unit_number: int) -> str:
    normalized_unit = str(unit).strip().lower()
    normalized_unit_number = max(1, int(unit_number))
    unit_suffix = {
        "second": "s",
        "minute": "m",
        "hour": "H",
        "day": "D",
        "week": "W",
        "month": "M",
    }
    suffix = unit_suffix.get(normalized_unit, normalized_unit[:1].upper())
    return f"{normalized_unit_number}{suffix}"


def _derive_lower_timeframe(*, base_unit: str, base_unit_number: int) -> tuple[str, int]:
    normalized_unit = str(base_unit).strip().lower()
    normalized_unit_number = max(1, int(base_unit_number))
    total_seconds = _UNIT_SECONDS_BY_NAME[normalized_unit] * normalized_unit_number
    for divisor in (4, 3, 5, 2):
        if total_seconds % divisor != 0:
            continue
        candidate_seconds = total_seconds // divisor
        candidate = _timeframe_from_seconds(
            candidate_seconds,
            allow_seconds=normalized_unit == "second",
        )
        if candidate is not None:
            return candidate
    return normalized_unit, normalized_unit_number


def _timeframe_from_seconds(seconds: int, *, allow_seconds: bool) -> tuple[str, int] | None:
    normalized_seconds = max(1, int(seconds))
    for unit in ("month", "week", "day", "hour", "minute"):
        unit_seconds = _UNIT_SECONDS_BY_NAME[unit]
        if normalized_seconds % unit_seconds == 0:
            return unit, normalized_seconds // unit_seconds
    if allow_seconds:
        return "second", normalized_seconds
    return None


def _ema_series(values: list[float], period: int) -> list[float | None]:
    normalized_period = max(1, int(period))
    if len(values) < normalized_period:
        return [None] * len(values)

    seed = _average(values[:normalized_period])
    multiplier = 2 / (normalized_period + 1)
    output: list[float | None] = [None] * (normalized_period - 1)
    output.append(seed)
    current = seed
    for value in values[normalized_period:]:
        current = ((value - current) * multiplier) + current
        output.append(current)
    return output


def _absolute_percent_delta(left: float, right: float, *, reference: float) -> float:
    denominator = abs(reference)
    if denominator <= 1e-9:
        return 0.0
    return abs(left - right) / denominator * 100


def _classify_ema_signal_candle(candle: ProjectXMarketCandle) -> tuple[str, str, str] | None:
    open_price = float(candle.open_price)
    high_price = float(candle.high_price)
    low_price = float(candle.low_price)
    close_price = float(candle.close_price)
    candle_range = high_price - low_price
    if candle_range <= 0:
        return None

    body = abs(close_price - open_price)
    upper_wick = high_price - max(open_price, close_price)
    lower_wick = min(open_price, close_price) - low_price
    body_percent = body / candle_range
    upper_wick_percent = upper_wick / candle_range
    lower_wick_percent = lower_wick / candle_range
    close_position = (close_price - low_price) / candle_range
    is_bullish = close_price > open_price
    is_bearish = close_price < open_price

    if is_bullish and body_percent >= 0.8 and upper_wick_percent <= 0.1 and lower_wick_percent <= 0.1:
        return ("bullish_marubozu", "bullish marubozu", "bullish")
    if is_bearish and body_percent >= 0.8 and upper_wick_percent <= 0.1 and lower_wick_percent <= 0.1:
        return ("bearish_marubozu", "bearish marubozu", "bearish")
    if (
        is_bullish
        and body_percent <= 0.4
        and lower_wick_percent >= 0.4
        and lower_wick >= body * 1.5
        and upper_wick <= max(body, candle_range * 0.1)
        and close_position >= 0.65
    ):
        return ("bullish_pin_bar", "bullish pin bar", "bullish")
    if (
        is_bearish
        and body_percent <= 0.4
        and upper_wick_percent >= 0.4
        and upper_wick >= body * 1.5
        and lower_wick <= max(body, candle_range * 0.1)
        and close_position <= 0.35
    ):
        return ("bearish_pin_bar", "bearish pin bar", "bearish")
    if is_bullish and body_percent >= 0.6:
        return ("strong_bullish_candle", "strong bullish candle", "bullish")
    if is_bearish and body_percent >= 0.6:
        return ("strong_bearish_candle", "strong bearish candle", "bearish")
    return None


def _determine_pullback_trap_trend_direction(
    *,
    fast_ema: list[float | None],
    slow_ema: list[float | None],
    closes: list[float],
    confirmation_bars: int,
) -> str:
    span = max(2, int(confirmation_bars))
    if len(closes) < span or len(fast_ema) < span or len(slow_ema) < span:
        return "none"

    recent_fast = fast_ema[-span:]
    recent_slow = slow_ema[-span:]
    if any(value is None for value in [*recent_fast, *recent_slow]):
        return "none"

    recent_fast_values = [float(value) for value in recent_fast if value is not None]
    recent_slow_values = [float(value) for value in recent_slow if value is not None]
    latest_close = closes[-1]

    is_uptrend = (
        all(fast > slow for fast, slow in zip(recent_fast_values, recent_slow_values))
        and _series_is_strictly_rising(recent_fast_values)
        and _series_is_strictly_rising(recent_slow_values)
        and latest_close >= recent_slow_values[-1]
    )
    is_downtrend = (
        all(fast < slow for fast, slow in zip(recent_fast_values, recent_slow_values))
        and _series_is_strictly_falling(recent_fast_values)
        and _series_is_strictly_falling(recent_slow_values)
        and latest_close <= recent_slow_values[-1]
    )
    if is_uptrend and not is_downtrend:
        return "uptrend"
    if is_downtrend and not is_uptrend:
        return "downtrend"
    return "none"


def _series_is_strictly_rising(values: list[float]) -> bool:
    return len(values) >= 2 and all(current > previous for previous, current in zip(values, values[1:]))


def _series_is_strictly_falling(values: list[float]) -> bool:
    return len(values) >= 2 and all(current < previous for previous, current in zip(values, values[1:]))


def evaluate_risk_gates(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    account: Account,
    latest_candle: ProjectXMarketCandle | None,
    contract_id: str,
    symbol: str | None,
    action: str,
    requested_order_size: float | None = None,
    current_position_qty: float = 0.0,
    target_position_qty: float | None = None,
    dry_run: bool,
    confirm_live_order_routing: bool,
) -> list[RiskBlock]:
    blocks: list[RiskBlock] = []
    if not bool(config.enabled):
        blocks.append(RiskBlock(code="bot_disabled", message="Bot is disabled.", severity="critical"))
    if account.account_state != ACCOUNT_STATE_ACTIVE:
        blocks.append(
            RiskBlock(
                code="account_not_active",
                message=f"Account state is {account.account_state}; only ACTIVE accounts can execute bot orders.",
                severity="critical",
            )
        )
    if not dry_run and account.can_trade is False:
        blocks.append(RiskBlock(code="account_cannot_trade", message="Provider marks this account as not tradable.", severity="critical"))
    if not dry_run and _looks_like_live_funded_account(account):
        blocks.append(
            RiskBlock(
                code="live_funded_api_blocked",
                message="Live Funded Account naming detected; ProjectX API automation is blocked for this account type.",
                severity="critical",
            )
        )
    if not dry_run and not confirm_live_order_routing:
        blocks.append(
            RiskBlock(
                code="live_order_confirmation_missing",
                message="Live order routing requires explicit confirmation for this request.",
                severity="critical",
            )
        )
    if not _is_contract_allowed(config, contract_id=contract_id, symbol=symbol):
        blocks.append(RiskBlock(code="contract_not_allowed", message="Contract is outside this bot's allowed contract list."))
    order_size = float(requested_order_size if requested_order_size is not None else config.order_size)
    signed_change = order_size if action == "BUY" else -order_size
    resulting_position_qty = float(target_position_qty) if target_position_qty is not None else float(current_position_qty) + signed_change
    if order_size <= 0:
        blocks.append(RiskBlock(code="invalid_order_size", message="Computed order size must be positive."))
    if abs(order_size - round(order_size)) > 1e-9:
        blocks.append(RiskBlock(code="fractional_contract_size", message="ProjectX futures order size must be a whole number."))
    if abs(resulting_position_qty) > float(config.max_contracts):
        blocks.append(RiskBlock(code="max_contracts", message="Resulting position exceeds max contracts."))
    if abs(resulting_position_qty) > float(config.max_open_position):
        blocks.append(RiskBlock(code="max_open_position", message="Resulting position exceeds max open position setting."))
    if _todays_bot_trade_count(db, user_id=user_id, config=config) >= int(config.max_trades_per_day):
        blocks.append(RiskBlock(code="max_trades_per_day", message="Daily bot trade limit has been reached."))
    daily_pnl = _todays_account_net_pnl(db, user_id=user_id, account_id=int(config.account_id))
    if daily_pnl <= -float(config.max_daily_loss):
        blocks.append(RiskBlock(code="max_daily_loss", message="Account has reached the configured daily loss limit.", severity="critical"))
    delayed_orb_loss_block = _delayed_orb_session_loss_block(db, user_id=user_id, config=config)
    if delayed_orb_loss_block is not None:
        blocks.append(delayed_orb_loss_block)
    if latest_candle is None:
        blocks.append(RiskBlock(code="missing_market_data", message="No closed candle data is available.", severity="critical"))
    else:
        staleness = (datetime.now(timezone.utc) - _as_utc(latest_candle.candle_timestamp)).total_seconds()
        if staleness > int(config.max_data_staleness_seconds):
            blocks.append(RiskBlock(code="stale_market_data", message="Latest candle is stale.", severity="critical"))
    if not _is_inside_trading_session(str(config.trading_start_time), str(config.trading_end_time)):
        blocks.append(RiskBlock(code="outside_session", message="Current time is outside the bot trading session."))
    cooldown_block = _cooldown_block(db, user_id=user_id, config=config)
    if cooldown_block is not None:
        blocks.append(cooldown_block)
    if action not in {"BUY", "SELL"}:
        blocks.append(RiskBlock(code="unsupported_action", message="Only BUY and SELL actions can create order attempts."))
    return blocks


def serialize_bot_config(row: BotConfig) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "name": row.name,
        "account_id": int(row.account_id),
        "provider": row.provider,
        "enabled": bool(row.enabled),
        "execution_mode": row.execution_mode,
        "strategy_type": row.strategy_type,
        "strategy_params": _normalize_strategy_params(row.strategy_type, row.strategy_params),
        "contract_id": row.contract_id,
        "symbol": row.symbol,
        "timeframe_unit": row.timeframe_unit,
        "timeframe_unit_number": int(row.timeframe_unit_number),
        "lookback_bars": int(row.lookback_bars),
        "fast_period": int(row.fast_period),
        "slow_period": int(row.slow_period),
        "order_size": float(row.order_size),
        "max_contracts": float(row.max_contracts),
        "max_daily_loss": float(row.max_daily_loss),
        "max_trades_per_day": int(row.max_trades_per_day),
        "max_open_position": float(row.max_open_position),
        "allowed_contracts": _normalize_allowed_contracts(row.allowed_contracts),
        "trading_start_time": row.trading_start_time,
        "trading_end_time": row.trading_end_time,
        "cooldown_seconds": int(row.cooldown_seconds),
        "max_data_staleness_seconds": int(row.max_data_staleness_seconds),
        "allow_market_depth": bool(row.allow_market_depth),
        "created_at": _as_utc(row.created_at),
        "updated_at": _as_utc(row.updated_at),
    }


def serialize_bot_run(row: BotRun) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "bot_config_id": int(row.bot_config_id),
        "account_id": int(row.account_id),
        "status": row.status,
        "dry_run": bool(row.dry_run),
        "started_at": _as_utc(row.started_at),
        "stopped_at": _as_utc(row.stopped_at) if row.stopped_at is not None else None,
        "stop_reason": row.stop_reason,
        "last_heartbeat_at": _as_utc(row.last_heartbeat_at) if row.last_heartbeat_at is not None else None,
    }


def serialize_bot_decision(row: BotDecision) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "bot_config_id": int(row.bot_config_id),
        "bot_run_id": int(row.bot_run_id) if row.bot_run_id is not None else None,
        "account_id": int(row.account_id),
        "contract_id": row.contract_id,
        "symbol": row.symbol,
        "decision_type": row.decision_type,
        "action": row.action,
        "reason": row.reason,
        "candle_timestamp": _as_utc(row.candle_timestamp) if row.candle_timestamp is not None else None,
        "price": float(row.price) if row.price is not None else None,
        "quantity": float(row.quantity) if row.quantity is not None else None,
        "created_at": _as_utc(row.created_at),
    }


def serialize_bot_order_attempt(row: BotOrderAttempt) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "bot_config_id": int(row.bot_config_id),
        "bot_run_id": int(row.bot_run_id) if row.bot_run_id is not None else None,
        "bot_decision_id": int(row.bot_decision_id) if row.bot_decision_id is not None else None,
        "account_id": int(row.account_id),
        "contract_id": row.contract_id,
        "side": row.side,
        "order_type": row.order_type,
        "size": float(row.size),
        "status": row.status,
        "provider_order_id": row.provider_order_id,
        "rejection_reason": row.rejection_reason,
        "created_at": _as_utc(row.created_at),
        "updated_at": _as_utc(row.updated_at),
    }


def serialize_bot_risk_event(row: BotRiskEvent) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "bot_config_id": int(row.bot_config_id),
        "bot_run_id": int(row.bot_run_id) if row.bot_run_id is not None else None,
        "account_id": int(row.account_id),
        "severity": row.severity,
        "code": row.code,
        "message": row.message,
        "created_at": _as_utc(row.created_at),
    }


def serialize_market_candle(row: ProjectXMarketCandle) -> dict[str, Any]:
    return {
        "id": int(row.id) if row.id is not None else None,
        "contract_id": row.contract_id,
        "symbol": row.symbol,
        "live": bool(row.live),
        "unit": row.unit,
        "unit_number": int(row.unit_number),
        "timestamp": _as_utc(row.candle_timestamp),
        "open": float(row.open_price),
        "high": float(row.high_price),
        "low": float(row.low_price),
        "close": float(row.close_price),
        "volume": float(row.volume),
        "is_partial": bool(row.is_partial),
        "fetched_at": _as_utc(row.fetched_at) if row.fetched_at is not None else None,
    }


def serialize_evaluation(result: EvaluationResult) -> dict[str, Any]:
    return {
        "config": serialize_bot_config(result.config),
        "run": serialize_bot_run(result.run) if result.run is not None else None,
        "decision": serialize_bot_decision(result.decision),
        "order_attempt": serialize_bot_order_attempt(result.order_attempt) if result.order_attempt is not None else None,
        "risk_events": [serialize_bot_risk_event(row) for row in result.risk_events],
        "analysis": result.analysis,
        "candles": [serialize_market_candle(row) for row in result.candles[-50:]],
    }


def build_signal_trade_evaluation(
    *,
    candles: list[ProjectXMarketCandle],
    config: BotConfig,
    signal: SignalResult,
    analysis: dict[str, Any],
) -> dict[str, Any] | None:
    if signal.action not in {"BUY", "SELL"} or not isinstance(signal.raw_payload, dict):
        return None

    payload = signal.raw_payload
    entry_price = _optional_float(payload.get("entry_price")) or _optional_float(signal.price)
    stop_loss = _optional_float(payload.get("stop_loss"))
    take_profit = (
        _optional_float(payload.get("take_profit"))
        or _optional_float(payload.get("final_take_profit"))
        or _optional_float(payload.get("partial_take_profit"))
    )
    if entry_price is None or stop_loss is None or take_profit is None:
        return None

    quantity = (
        _optional_float(payload.get("effective_order_size"))
        or _optional_float(payload.get("order_size"))
        or float(config.order_size)
    )
    timestamp = signal.candle_timestamp
    if timestamp is None and candles:
        timestamp = candles[-1].candle_timestamp
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)

    market_context = build_market_context_from_ohlcv(
        [
            {
                "timestamp": candle.candle_timestamp,
                "open": float(candle.open_price),
                "high": float(candle.high_price),
                "low": float(candle.low_price),
                "close": float(candle.close_price),
                "volume": float(candle.volume or 0),
            }
            for candle in _closed_candles(candles) or candles
        ],
        current_price=float(signal.price) if signal.price is not None else None,
        timestamp=timestamp,
        market_regime=_infer_trade_plan_market_regime(config=config, signal=signal, analysis=analysis),
        news_risk="low",
    )
    if market_context is None:
        return None

    plan = TradePlan(
        symbol=str(config.symbol or config.contract_id),
        direction="long" if signal.action == "BUY" else "short",
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        quantity=quantity,
        timestamp=timestamp,
        max_daily_loss=float(config.max_daily_loss),
    )
    return TradePlanEvaluator().evaluate(plan, market_context).to_payload()


def _infer_trade_plan_market_regime(
    *,
    config: BotConfig,
    signal: SignalResult,
    analysis: dict[str, Any],
) -> str:
    if isinstance(signal.raw_payload, dict):
        raw_regime = str(signal.raw_payload.get("market_regime") or "").strip().lower()
        if raw_regime in {"trend", "range", "chop", "breakout", "reversal", "unknown"}:
            return raw_regime
        chop_state = signal.raw_payload.get("chop")
        if isinstance(chop_state, dict) and bool(chop_state.get("is_choppy")):
            return "chop"

    strategy_type = str(config.strategy_type)
    if strategy_type in {
        _STRATEGY_DONCHIAN_BREAKOUT,
        _STRATEGY_OPENING_RVOL_BREAKOUT,
        _STRATEGY_DELAYED_ORB_CONFIRMATION,
        _STRATEGY_ORB_FIBONACCI_PULLBACK,
        _STRATEGY_VWAP_GAP_RETRACE,
    }:
        return "breakout"
    if strategy_type in {
        _STRATEGY_BOLLINGER_MEAN_REVERSION,
        _STRATEGY_BOLLINGER_RSI_REVERSAL,
        _STRATEGY_FISHER_MEAN_REVERSION,
        _STRATEGY_VWAP_ATR_MEAN_REVERSION,
    }:
        return "reversal"
    if strategy_type in {
        _STRATEGY_EMA_SCALPING,
        _STRATEGY_EMA_TREND_PULLBACK,
        _STRATEGY_ATR_ADJUSTED_RELATIVE_STRENGTH,
        _STRATEGY_RELATIVE_STRENGTH_SPY,
        _STRATEGY_PULLBACK_TRAP_REVERSAL,
    }:
        return "trend"

    trend = str(analysis.get("trend") or "neutral")
    trend_strength = int(analysis.get("trend_strength") or 0)
    if trend in {"bullish", "bearish"} and trend_strength >= 65:
        return "trend"
    return "unknown"


def build_bot_market_analysis(
    *,
    candles: list[ProjectXMarketCandle],
    config: BotConfig,
    signal: SignalResult,
) -> dict[str, Any]:
    closed_candles = _closed_candles(candles)
    analysis_candles = closed_candles or sorted(candles, key=lambda candle: _as_utc(candle.candle_timestamp))
    risk_notes = [
        "Heuristic probabilities are not financial advice and are not guaranteed predictions.",
    ]
    if any(bool(candle.is_partial) for candle in candles):
        risk_notes.append("Partial candles are excluded from the indicator read when closed candles are available.")
    if not closed_candles and candles:
        risk_notes.append("No closed candles were available, so the analysis used the available candle rows.")

    if len(analysis_candles) < 10:
        return _neutral_market_analysis_payload(
            analysis_candles,
            risk_notes=[
                *risk_notes,
                f"Only {len(analysis_candles)} candle(s) were available; at least 10 are needed for a reliable heuristic read.",
            ],
        )

    latest = analysis_candles[-1]
    previous = analysis_candles[-2]
    current_price = float(latest.close_price)
    previous_close = float(previous.close_price)
    price_change = current_price - previous_close
    price_change_percent = _analysis_percent_change(current_price, previous_close)
    closes = [float(candle.close_price) for candle in analysis_candles]

    true_ranges = _analysis_true_ranges(analysis_candles)
    atr_period = min(14, len(analysis_candles))
    atr_values = _atr_series(analysis_candles, period=atr_period)
    latest_atr = _last_defined_float(atr_values)
    if latest_atr is None:
        latest_atr = _average(true_ranges[-min(len(true_ranges), atr_period) :])
    atr_reference = max(latest_atr, _average(true_ranges), abs(current_price) * 0.001, 1e-9)
    volatility_ratio = _analysis_volatility_ratio(true_ranges, latest_atr)
    volatility_state = _classify_analysis_volatility(volatility_ratio)

    volume_ratio = _analysis_volume_ratio(analysis_candles)
    volume_state = _classify_analysis_volume(volume_ratio)

    requested_fast = int(getattr(config, "fast_period", 9) or 9)
    requested_slow = int(getattr(config, "slow_period", 21) or 21)
    fast_period = min(max(2, requested_fast), max(2, len(closes) - 1))
    slow_period = min(max(fast_period + 1, requested_slow), len(closes))
    fast_ema = _ema_series(closes, fast_period)
    slow_ema = _ema_series(closes, slow_period)
    latest_fast = _last_defined_float(fast_ema) or current_price
    latest_slow = _last_defined_float(slow_ema) or current_price
    slow_prior = _prior_defined_float(slow_ema, lookback=5) or latest_slow
    ema_gap = latest_fast - latest_slow
    ema_slope = latest_slow - slow_prior
    recent_lookback = min(5, len(closes) - 1)
    recent_delta = current_price - closes[-(recent_lookback + 1)]
    trend, trend_strength = _classify_analysis_trend(
        ema_gap=ema_gap,
        ema_slope=ema_slope,
        recent_delta=recent_delta,
        atr_reference=atr_reference,
        recent_lookback=recent_lookback,
    )

    support_levels, resistance_levels = _analysis_support_resistance_levels(
        analysis_candles,
        current_price=current_price,
    )
    nearest_support = support_levels[0] if support_levels else None
    nearest_resistance = resistance_levels[0] if resistance_levels else None

    probabilities = _analysis_probability_scores(
        trend=trend,
        trend_strength=trend_strength,
        volatility_state=volatility_state,
        volume_state=volume_state,
        signal_action=signal.action,
        current_price=current_price,
        expected_move=latest_atr,
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
    )
    invalidation_level = _analysis_invalidation_level(
        trend=trend,
        signal_action=signal.action,
        current_price=current_price,
        expected_move=latest_atr,
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
    )

    reasoning = [
        (
            f"Latest close is {_format_strategy_price(current_price)} versus previous close "
            f"{_format_strategy_price(previous_close)} ({_format_percent(price_change_percent or 0.0)}%)."
        ),
        (
            f"Fast EMA({fast_period}) is {_format_strategy_price(latest_fast)} and slow EMA({slow_period}) "
            f"is {_format_strategy_price(latest_slow)}, with slow EMA slope {_format_strategy_price(ema_slope)}."
        ),
        (
            f"ATR({atr_period}) is {_format_strategy_price(latest_atr)} and recent range ratio is "
            f"{_format_analysis_ratio(volatility_ratio)}, classifying volatility as {volatility_state}."
        ),
        (
            f"Latest volume is {float(latest.volume):.0f} versus recent average ratio "
            f"{_format_analysis_ratio(volume_ratio)}, classifying volume as {volume_state}."
        ),
        _analysis_level_reasoning(nearest_support=nearest_support, nearest_resistance=nearest_resistance),
        _analysis_signal_reasoning(signal.action),
    ]

    if volatility_state in {"elevated", "extreme"}:
        risk_notes.append(f"Volatility is {volatility_state}; expected move and level invalidation can be less stable.")
    if volume_state == "low":
        risk_notes.append("Low relative volume can make candle signals less reliable.")
    if nearest_support is None or nearest_resistance is None:
        risk_notes.append("One or more nearby support/resistance levels could not be detected from recent candles.")
    risk_notes.append("The read uses recent ProjectX candle data only and does not include news, macro, or order-book context.")

    summary = _analysis_summary(
        trend=trend,
        trend_strength=trend_strength,
        probabilities=probabilities,
    )
    expected_move_percent = (
        (latest_atr / abs(current_price)) * 100 if latest_atr is not None and abs(current_price) > 1e-9 else None
    )
    return {
        "current_price": _round_analysis_float(current_price),
        "previous_close": _round_analysis_float(previous_close),
        "price_change": _round_analysis_float(price_change),
        "price_change_percent": _round_analysis_float(price_change_percent),
        "trend": trend,
        "trend_strength": trend_strength,
        "volatility_state": volatility_state,
        "volume_state": volume_state,
        "support_levels": support_levels,
        "resistance_levels": resistance_levels,
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        "bullish_probability": probabilities["bullish"],
        "bearish_probability": probabilities["bearish"],
        "sideways_probability": probabilities["sideways"],
        "expected_move": _round_analysis_float(latest_atr),
        "expected_move_percent": _round_analysis_float(expected_move_percent),
        "invalidation_level": invalidation_level,
        "summary": summary,
        "reasoning": reasoning,
        "risk_notes": risk_notes,
        "candle_timestamp": _as_utc(latest.candle_timestamp).isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _neutral_market_analysis_payload(
    candles: list[ProjectXMarketCandle],
    *,
    risk_notes: list[str],
) -> dict[str, Any]:
    current_price: float | None = None
    previous_close: float | None = None
    price_change: float | None = None
    price_change_percent: float | None = None
    if candles:
        current_price = float(candles[-1].close_price)
    if len(candles) >= 2:
        previous_close = float(candles[-2].close_price)
    if current_price is not None and previous_close is not None:
        price_change = current_price - previous_close
        price_change_percent = _analysis_percent_change(current_price, previous_close)

    return {
        "current_price": _round_analysis_float(current_price),
        "previous_close": _round_analysis_float(previous_close),
        "price_change": _round_analysis_float(price_change),
        "price_change_percent": _round_analysis_float(price_change_percent),
        "trend": "neutral",
        "trend_strength": 0,
        "volatility_state": "normal",
        "volume_state": "normal",
        "support_levels": [],
        "resistance_levels": [],
        "nearest_support": None,
        "nearest_resistance": None,
        "bullish_probability": 33,
        "bearish_probability": 33,
        "sideways_probability": 34,
        "expected_move": None,
        "expected_move_percent": None,
        "invalidation_level": None,
        "summary": "Insufficient candle history for a directional read; heuristic probabilities are held neutral.",
        "reasoning": [
            "Not enough candle history is available to calculate trend, volatility, volume, and swing levels together.",
        ],
        "risk_notes": risk_notes,
        "candle_timestamp": _as_utc(candles[-1].candle_timestamp).isoformat() if candles else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _analysis_percent_change(current_price: float, previous_close: float) -> float | None:
    if abs(previous_close) <= 1e-9:
        return None
    return (current_price - previous_close) / previous_close * 100


def _analysis_true_ranges(candles: list[ProjectXMarketCandle]) -> list[float]:
    true_ranges: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        high_price = float(candle.high_price)
        low_price = float(candle.low_price)
        if previous_close is None:
            true_range = high_price - low_price
        else:
            true_range = max(high_price - low_price, abs(high_price - previous_close), abs(low_price - previous_close))
        true_ranges.append(max(0.0, true_range))
        previous_close = float(candle.close_price)
    return true_ranges or [0.0]


def _analysis_volatility_ratio(true_ranges: list[float], latest_atr: float) -> float | None:
    if len(true_ranges) < 2:
        return None
    baseline = true_ranges[-21:-1] if len(true_ranges) > 20 else true_ranges[:-1]
    average_baseline = _average(baseline) if baseline else 0.0
    if average_baseline <= 1e-9:
        return None
    return latest_atr / average_baseline


def _classify_analysis_volatility(volatility_ratio: float | None) -> str:
    if volatility_ratio is None:
        return "normal"
    if volatility_ratio < 0.7:
        return "low"
    if volatility_ratio < 1.35:
        return "normal"
    if volatility_ratio < 2.0:
        return "elevated"
    return "extreme"


def _analysis_volume_ratio(candles: list[ProjectXMarketCandle]) -> float | None:
    if len(candles) < 2:
        return None
    baseline_count = min(20, len(candles) - 1)
    baseline = [float(candle.volume or 0) for candle in candles[-(baseline_count + 1) : -1]]
    average_baseline = _average(baseline) if baseline else 0.0
    if average_baseline <= 1e-9:
        return None
    return float(candles[-1].volume or 0) / average_baseline


def _classify_analysis_volume(volume_ratio: float | None) -> str:
    if volume_ratio is None:
        return "normal"
    if volume_ratio < 0.7:
        return "low"
    if volume_ratio > 1.5:
        return "elevated"
    return "normal"


def _classify_analysis_trend(
    *,
    ema_gap: float,
    ema_slope: float,
    recent_delta: float,
    atr_reference: float,
    recent_lookback: int,
) -> tuple[str, int]:
    gap_units = ema_gap / atr_reference
    slope_units = ema_slope / atr_reference
    recent_units = recent_delta / (atr_reference * max(1, recent_lookback))
    component_signs = [
        _analysis_component_sign(gap_units, threshold=0.05),
        _analysis_component_sign(slope_units, threshold=0.03),
        _analysis_component_sign(recent_units, threshold=0.05),
    ]
    direction_score = sum(component_signs)
    strength = int(
        round(
            min(
                100.0,
                (abs(gap_units) * 30.0)
                + (abs(slope_units) * 25.0)
                + (abs(recent_units) * 35.0),
            )
        )
    )
    if strength < 15 or abs(direction_score) < 2:
        return "neutral", strength
    if direction_score > 0:
        return "bullish", strength
    return "bearish", strength


def _analysis_component_sign(value: float, *, threshold: float) -> int:
    if value > threshold:
        return 1
    if value < -threshold:
        return -1
    return 0


def _analysis_support_resistance_levels(
    candles: list[ProjectXMarketCandle],
    *,
    current_price: float,
) -> tuple[list[float], list[float]]:
    recent_candles = candles[-min(80, len(candles)) :]
    window_size = 5 if len(recent_candles) >= 15 else 3
    timeframe = _candles_timeframe_label(recent_candles) or "analysis"
    detected_levels = _filter_clustered_levels(
        _detect_support_resistance_levels(recent_candles, timeframe=timeframe, window_size=window_size),
        tolerance_percent=0.15,
    )
    support_candidates = [
        level.price for level in detected_levels if level.side == "support" and level.price <= current_price
    ]
    resistance_candidates = [
        level.price for level in detected_levels if level.side == "resistance" and level.price >= current_price
    ]
    support_candidates.extend(_analysis_fallback_levels(recent_candles, side="support", current_price=current_price))
    resistance_candidates.extend(
        _analysis_fallback_levels(recent_candles, side="resistance", current_price=current_price)
    )
    supports = _analysis_unique_level_prices(support_candidates, current_price=current_price, side="support")
    resistances = _analysis_unique_level_prices(
        resistance_candidates,
        current_price=current_price,
        side="resistance",
    )
    return supports, resistances


def _analysis_fallback_levels(
    candles: list[ProjectXMarketCandle],
    *,
    side: str,
    current_price: float,
) -> list[float]:
    candidates: list[float] = []
    for lookback in (5, 10, 20, 50):
        subset = candles[-min(lookback, len(candles)) :]
        if not subset:
            continue
        if side == "support":
            price = min(float(candle.low_price) for candle in subset)
            if price <= current_price:
                candidates.append(price)
        else:
            price = max(float(candle.high_price) for candle in subset)
            if price >= current_price:
                candidates.append(price)
    return candidates


def _analysis_unique_level_prices(
    values: list[float],
    *,
    current_price: float,
    side: str,
) -> list[float]:
    ordered_values = sorted(
        [value for value in values if math.isfinite(value)],
        reverse=side == "support",
    )
    output: list[float] = []
    for value in ordered_values:
        if side == "support" and value > current_price:
            continue
        if side == "resistance" and value < current_price:
            continue
        if any(_level_distance_percent(value, existing) <= 0.1 for existing in output):
            continue
        rounded = _round_analysis_float(value)
        if rounded is not None:
            output.append(rounded)
        if len(output) >= 5:
            break
    return output


def _analysis_probability_scores(
    *,
    trend: str,
    trend_strength: int,
    volatility_state: str,
    volume_state: str,
    signal_action: str,
    current_price: float,
    expected_move: float,
    nearest_support: float | None,
    nearest_resistance: float | None,
) -> dict[str, int]:
    bullish = 33.0
    bearish = 33.0
    sideways = 34.0
    trend_bias = min(32.0, max(0, trend_strength) * 0.4)
    if trend == "bullish":
        bullish += trend_bias
        bearish -= trend_bias * 0.55
        sideways -= trend_bias * 0.45
    elif trend == "bearish":
        bearish += trend_bias
        bullish -= trend_bias * 0.55
        sideways -= trend_bias * 0.45
    else:
        sideways += 6.0
        bullish -= 3.0
        bearish -= 3.0

    if volatility_state == "low":
        sideways += 4.0
        bullish -= 2.0
        bearish -= 2.0
    elif volatility_state == "elevated":
        sideways -= 4.0
        if trend == "bullish":
            bullish += 3.0
            bearish += 1.0
        elif trend == "bearish":
            bearish += 3.0
            bullish += 1.0
        else:
            bullish += 2.0
            bearish += 2.0
    elif volatility_state == "extreme":
        sideways -= 8.0
        bullish += 4.0
        bearish += 4.0

    if volume_state == "elevated" and trend == "bullish":
        bullish += 5.0
        bearish -= 2.0
        sideways -= 3.0
    elif volume_state == "elevated" and trend == "bearish":
        bearish += 5.0
        bullish -= 2.0
        sideways -= 3.0
    elif volume_state == "low":
        sideways += 3.0
        if trend == "bullish":
            bullish -= 2.0
            bearish -= 1.0
        elif trend == "bearish":
            bearish -= 2.0
            bullish -= 1.0
        else:
            bullish -= 1.5
            bearish -= 1.5

    if signal_action == "BUY":
        bullish += 4.0
        bearish -= 2.0
        sideways -= 2.0
    elif signal_action == "SELL":
        bearish += 4.0
        bullish -= 2.0
        sideways -= 2.0
    elif signal_action in {"HOLD", "NONE"}:
        sideways += 2.0
        bullish -= 1.0
        bearish -= 1.0

    if expected_move > 0 and nearest_resistance is not None:
        resistance_distance = nearest_resistance - current_price
        if 0 <= resistance_distance <= expected_move:
            bullish -= 4.0
            bearish += 2.0
            sideways += 2.0
    if expected_move > 0 and nearest_support is not None:
        support_distance = current_price - nearest_support
        if 0 <= support_distance <= expected_move:
            bearish -= 4.0
            bullish += 2.0
            sideways += 2.0

    normalized = _normalize_analysis_probabilities(
        {
            "bullish": bullish,
            "bearish": bearish,
            "sideways": sideways,
        }
    )
    return normalized


def _normalize_analysis_probabilities(scores: dict[str, float]) -> dict[str, int]:
    ordered_keys = ["bullish", "bearish", "sideways"]
    positive_scores = {key: max(0.0, float(scores.get(key, 0.0))) for key in ordered_keys}
    total = sum(positive_scores.values())
    if total <= 1e-9:
        return {"bullish": 33, "bearish": 33, "sideways": 34}

    scaled = {key: positive_scores[key] / total * 100.0 for key in ordered_keys}
    output = {key: int(math.floor(scaled[key])) for key in ordered_keys}
    remainder = 100 - sum(output.values())
    by_fraction = sorted(ordered_keys, key=lambda key: (scaled[key] - output[key], scaled[key]), reverse=True)
    for index in range(remainder):
        output[by_fraction[index % len(by_fraction)]] += 1
    return output


def _analysis_invalidation_level(
    *,
    trend: str,
    signal_action: str,
    current_price: float,
    expected_move: float,
    nearest_support: float | None,
    nearest_resistance: float | None,
) -> float | None:
    effective_direction = trend
    if signal_action == "BUY":
        effective_direction = "bullish"
    elif signal_action == "SELL":
        effective_direction = "bearish"

    if effective_direction == "bullish":
        level = nearest_support if nearest_support is not None else current_price - expected_move
        return _round_analysis_float(level)
    if effective_direction == "bearish":
        level = nearest_resistance if nearest_resistance is not None else current_price + expected_move
        return _round_analysis_float(level)
    return None


def _analysis_summary(
    *,
    trend: str,
    trend_strength: int,
    probabilities: dict[str, int],
) -> str:
    if trend == "neutral":
        return (
            "Heuristic read is neutral, with sideways probability highest at "
            f"{probabilities['sideways']}%. This is not financial advice."
        )
    return (
        f"Heuristic read leans {trend} with {trend_strength}/100 trend strength and "
        f"{probabilities[trend]}% {trend} probability. This is not financial advice."
    )


def _analysis_level_reasoning(*, nearest_support: float | None, nearest_resistance: float | None) -> str:
    support_text = _format_strategy_price(nearest_support) if nearest_support is not None else "none detected"
    resistance_text = _format_strategy_price(nearest_resistance) if nearest_resistance is not None else "none detected"
    return f"Nearest support is {support_text}; nearest resistance is {resistance_text}."


def _analysis_signal_reasoning(signal_action: str) -> str:
    if signal_action in {"BUY", "SELL"}:
        return f"Bot signal action is {signal_action}; probabilities use it as context, not as a guaranteed prediction."
    return f"Bot signal action is {signal_action}; probabilities stay conservative without an active directional order signal."


def _format_analysis_ratio(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}x"


def _last_defined_float(values: list[float | None]) -> float | None:
    for value in reversed(values):
        if value is not None and math.isfinite(value):
            return float(value)
    return None


def _prior_defined_float(values: list[float | None], *, lookback: int) -> float | None:
    defined = [(index, float(value)) for index, value in enumerate(values) if value is not None and math.isfinite(value)]
    if not defined:
        return None
    latest_index = defined[-1][0]
    target_index = latest_index - max(1, int(lookback))
    prior_candidates = [value for index, value in defined if index <= target_index]
    return prior_candidates[-1] if prior_candidates else defined[0][1]


def _round_analysis_float(value: float | None, digits: int = 4) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    rounded = round(float(value), digits)
    return 0.0 if rounded == 0 else rounded


def _require_bot_config(
    db: Session,
    *,
    user_id: str,
    bot_config_id: int,
    lock_for_update: bool = False,
) -> BotConfig:
    query = (
        db.query(BotConfig)
        .filter(BotConfig.user_id == user_id)
        .filter(BotConfig.id == bot_config_id)
    )
    if lock_for_update and _session_dialect_name(db) == "postgresql":
        query = query.with_for_update()
    row = query.one_or_none()
    if row is None:
        raise LookupError("bot_config_not_found")
    return row


def _require_owned_account(db: Session, *, user_id: str, account_id: int) -> Account:
    account = get_projectx_account_row(db, account_id, user_id=user_id)
    if account is None:
        raise LookupError("account_not_found")
    return account


def _create_risk_event(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    run: BotRun | None,
    block: RiskBlock,
) -> BotRiskEvent:
    row = BotRiskEvent(
        user_id=user_id,
        bot_config_id=int(config.id),
        bot_run_id=int(run.id) if run is not None and run.id is not None else None,
        account_id=int(config.account_id),
        severity=block.severity,
        code=block.code,
        message=block.message,
        raw_payload=block.__dict__,
    )
    db.add(row)
    return row


def _create_order_attempt(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    run: BotRun | None,
    decision: BotDecision,
    contract_id: str,
    action: str,
    order_size: float,
) -> BotOrderAttempt:
    request_payload = {
        "accountId": int(config.account_id),
        "contractId": contract_id,
        "type": _ORDER_TYPE_MARKET,
        "side": _SIDE_BY_ACTION[action],
        "size": int(round(float(order_size))),
        "customTag": f"topsignal-bot-{int(config.id)}-{int(decision.id)}",
    }
    if isinstance(decision.raw_payload, dict):
        strategy_order_plan = {
            key: decision.raw_payload[key]
            for key in [
                "strategy_type",
                "signal_category",
                "signal_candle_pattern",
                "trigger_level",
                "entry_price",
                "stop_loss",
                "partial_take_profit",
                "take_profit",
                "take_profit_mode",
                "final_take_profit",
                "middle_band",
                "upper_band",
                "lower_band",
                "session_vwap",
                "risk",
                "reward_r_multiple",
                "trailing_stop",
                "trail_stop",
                "trail_amount",
                "exit_channel_price",
                "atr",
                "atr_percent",
                "current_position_qty",
                "target_position_qty",
                "effective_order_size",
                "exit_on_opposite_candle",
                "exit_deadline",
            ]
            if key in decision.raw_payload
        }
        if strategy_order_plan:
            request_payload["strategyOrderPlan"] = strategy_order_plan
        request_payload.update(
            _strategy_bracket_payloads(
                db,
                contract_id=contract_id,
                symbol=decision.symbol or config.symbol,
                action=action,
                entry_price=float(decision.price) if decision.price is not None else None,
                decision_payload=decision.raw_payload,
            )
        )
    row = BotOrderAttempt(
        user_id=user_id,
        bot_config_id=int(config.id),
        bot_run_id=int(run.id) if run is not None and run.id is not None else None,
        bot_decision_id=int(decision.id),
        account_id=int(config.account_id),
        contract_id=contract_id,
        side=action,
        order_type="market",
        size=float(order_size),
        status="pending",
        raw_request=request_payload,
    )
    db.add(row)
    return row


def _submit_order_attempt(*, client: ProjectXClient, order_attempt: BotOrderAttempt) -> None:
    request_payload = order_attempt.raw_request or {}
    try:
        response = client.place_order(
            account_id=int(request_payload["accountId"]),
            contract_id=str(request_payload["contractId"]),
            order_type=int(request_payload["type"]),
            side=int(request_payload["side"]),
            size=int(request_payload["size"]),
            custom_tag=str(request_payload.get("customTag")) if request_payload.get("customTag") else None,
            stop_loss_bracket=request_payload.get("stopLossBracket"),
            take_profit_bracket=request_payload.get("takeProfitBracket"),
        )
        order_attempt.status = "submitted"
        order_attempt.provider_order_id = response.get("order_id")
        order_attempt.raw_response = response.get("raw_payload")
    except Exception as exc:
        order_attempt.status = "error"
        order_attempt.rejection_reason = str(exc)
        order_attempt.raw_response = {"error": str(exc)}


def _strategy_bracket_payloads(
    db: Session,
    *,
    contract_id: str,
    symbol: str | None,
    action: str,
    entry_price: float | None,
    decision_payload: dict[str, Any],
) -> dict[str, Any]:
    if decision_payload.get("signal_category") == "exit":
        return {}
    stop_loss = decision_payload.get("stop_loss")
    take_profit = decision_payload.get("take_profit")
    if entry_price is None or not isinstance(stop_loss, (int, float)):
        return {}

    tick_size = _instrument_tick_size(db, symbol=symbol, contract_id=contract_id)
    if tick_size is None or tick_size <= 0:
        return {}

    stop_ticks = max(1, int(round(abs(float(entry_price) - float(stop_loss)) / tick_size)))
    payload: dict[str, Any] = {"stopLossBracket": {"ticks": stop_ticks}}
    if isinstance(take_profit, (int, float)):
        take_profit_ticks = max(1, int(round(abs(float(take_profit) - float(entry_price)) / tick_size)))
        payload["takeProfitBracket"] = {"ticks": take_profit_ticks}
    return payload


def _signal_order_size(*, config: BotConfig, signal: SignalResult) -> float:
    if isinstance(signal.raw_payload, dict):
        value = _optional_float(signal.raw_payload.get("effective_order_size"))
        if value is not None and value > 0:
            return value
    return float(config.order_size)


def _signal_current_position_qty(signal: SignalResult) -> float:
    if isinstance(signal.raw_payload, dict):
        value = _optional_float(signal.raw_payload.get("current_position_qty"))
        if value is not None:
            return value
    return 0.0


def _signal_target_position_qty(signal: SignalResult) -> float | None:
    if isinstance(signal.raw_payload, dict):
        return _optional_float(signal.raw_payload.get("target_position_qty"))
    return None


def load_open_position_state(
    db: Session,
    *,
    user_id: str,
    account_id: int,
    contract_id: str,
    symbol: str | None,
) -> OpenPositionState:
    rows = (
        db.query(ProjectXTradeEvent)
        .filter(ProjectXTradeEvent.user_id == user_id)
        .filter(ProjectXTradeEvent.account_id == account_id)
        .filter(ProjectXTradeEvent.contract_id == contract_id)
        .order_by(ProjectXTradeEvent.trade_timestamp.asc(), ProjectXTradeEvent.id.asc())
        .all()
    )
    if not rows and symbol:
        rows = (
            db.query(ProjectXTradeEvent)
            .filter(ProjectXTradeEvent.user_id == user_id)
            .filter(ProjectXTradeEvent.account_id == account_id)
            .filter(
                or_(
                    ProjectXTradeEvent.contract_id == contract_id,
                    ProjectXTradeEvent.symbol == symbol,
                )
            )
            .order_by(ProjectXTradeEvent.trade_timestamp.asc(), ProjectXTradeEvent.id.asc())
            .all()
        )
    return _open_position_state_from_trade_rows(rows)


def _open_position_state_from_trade_rows(rows: list[ProjectXTradeEvent]) -> OpenPositionState:
    epsilon = 1e-9
    lots: list[OpenPositionLot] = []

    for row in rows:
        qty = abs(float(row.size) if row.size is not None else 0.0)
        sign = _trade_side_sign(row.side)
        if qty <= epsilon or sign == 0:
            continue

        remaining = sign * qty
        trade_ts = _as_utc(row.trade_timestamp)
        trade_price = float(row.price) if row.price is not None else 0.0

        while abs(remaining) > epsilon and lots and _sign(remaining) != _sign(lots[0].qty):
            lot = lots[0]
            close_qty = min(abs(remaining), abs(lot.qty))
            next_qty = lot.qty - (_sign(lot.qty) * close_qty)
            remaining -= _sign(remaining) * close_qty
            if abs(next_qty) <= epsilon:
                lots.pop(0)
            else:
                lots[0] = OpenPositionLot(qty=next_qty, timestamp=lot.timestamp, price=lot.price)

        if abs(remaining) > epsilon:
            lots.append(OpenPositionLot(qty=remaining, timestamp=trade_ts, price=trade_price))

    if not lots:
        return OpenPositionState(net_qty=0.0, avg_entry_price=None, opened_at=None)

    net_qty = sum(lot.qty for lot in lots)
    if abs(net_qty) <= epsilon:
        return OpenPositionState(net_qty=0.0, avg_entry_price=None, opened_at=None)

    weighted_qty = sum(abs(lot.qty) for lot in lots)
    avg_entry_price = (
        sum(abs(lot.qty) * lot.price for lot in lots) / weighted_qty if weighted_qty > epsilon else None
    )
    opened_at = min(lot.timestamp for lot in lots)
    return OpenPositionState(
        net_qty=net_qty,
        avg_entry_price=avg_entry_price,
        opened_at=opened_at,
    )


def load_latest_bot_entry_plan(
    db: Session,
    *,
    user_id: str,
    bot_config_id: int,
    position_state: OpenPositionState,
) -> dict[str, Any] | None:
    if position_state.side == "flat":
        return None

    opening_side = "BUY" if position_state.side == "long" else "SELL"
    rows = (
        db.query(BotOrderAttempt)
        .filter(BotOrderAttempt.user_id == user_id)
        .filter(BotOrderAttempt.bot_config_id == bot_config_id)
        .filter(BotOrderAttempt.side == opening_side)
        .filter(BotOrderAttempt.status.in_(["dry_run", "submitted"]))
        .order_by(BotOrderAttempt.created_at.desc(), BotOrderAttempt.id.desc())
        .limit(25)
        .all()
    )
    for row in rows:
        if position_state.opened_at is not None and row.created_at is not None:
            if _as_utc(row.created_at) < position_state.opened_at - timedelta(minutes=15):
                continue
        if not isinstance(row.raw_request, dict):
            continue
        plan = row.raw_request.get("strategyOrderPlan")
        if isinstance(plan, dict):
            return plan
    return None


def _instrument_tick_size(db: Session, *, symbol: str | None, contract_id: str | None) -> float | None:
    specs = load_instrument_specs(db)
    candidate_keys = _unique_text_values(
        [
            normalize_symbol_key(symbol),
            normalize_symbol_key(contract_id),
            symbol,
            contract_id,
        ]
    )
    for candidate in candidate_keys:
        spec = specs.get(candidate)
        if spec is not None and float(spec.tick_size) > 0:
            return float(spec.tick_size)
    return None


def _stop_running_bot_runs(
    db: Session,
    *,
    user_id: str,
    bot_config_id: int,
    reason: str,
    now: datetime,
) -> None:
    rows = (
        db.query(BotRun)
        .filter(BotRun.user_id == user_id)
        .filter(BotRun.bot_config_id == bot_config_id)
        .filter(BotRun.status == "running")
        .order_by(BotRun.started_at.desc(), BotRun.id.desc())
        .all()
    )
    for row in rows:
        row.status = "stopped"
        row.stopped_at = now
        row.stop_reason = reason


def _execution_contract_id(config: BotConfig, latest_candle: ProjectXMarketCandle | None) -> str:
    if latest_candle is not None:
        contract_id = _normalized_optional_text(latest_candle.contract_id)
        if contract_id is not None:
            return contract_id
    return str(config.contract_id).strip()


def _execution_symbol(config: BotConfig, latest_candle: ProjectXMarketCandle | None) -> str | None:
    if latest_candle is not None:
        symbol = _normalized_optional_text(latest_candle.symbol)
        if symbol is not None:
            return symbol
    return _normalized_optional_text(config.symbol)


def _is_contract_allowed(config: BotConfig, *, contract_id: str, symbol: str | None) -> bool:
    values = _normalize_allowed_contracts(config.allowed_contracts)
    if not values:
        return True

    allowed = set(values)
    candidates = _unique_text_values([contract_id, symbol, config.contract_id, config.symbol])
    return any(candidate in allowed for candidate in candidates)


def _todays_bot_trade_count(db: Session, *, user_id: str, config: BotConfig) -> int:
    start, end = trading_day_bounds_utc(trading_day_date(datetime.now(timezone.utc)))
    count = (
        db.query(func.count(BotOrderAttempt.id))
        .filter(BotOrderAttempt.user_id == user_id)
        .filter(BotOrderAttempt.bot_config_id == int(config.id))
        .filter(BotOrderAttempt.created_at >= start)
        .filter(BotOrderAttempt.created_at <= end)
        .filter(BotOrderAttempt.status.in_(["dry_run", "submitted"]))
        .scalar()
    )
    return int(count or 0)


def _todays_account_net_pnl(db: Session, *, user_id: str, account_id: int) -> float:
    start, end = trading_day_bounds_utc(trading_day_date(datetime.now(timezone.utc)))
    total = (
        db.query(func.coalesce(func.sum(ProjectXTradeEvent.pnl - func.coalesce(ProjectXTradeEvent.fees, 0)), 0))
        .filter(ProjectXTradeEvent.user_id == user_id)
        .filter(ProjectXTradeEvent.account_id == account_id)
        .filter(ProjectXTradeEvent.trade_timestamp >= start)
        .filter(ProjectXTradeEvent.trade_timestamp <= end)
        .filter(ProjectXTradeEvent.pnl.isnot(None))
        .scalar()
    )
    return float(total or 0.0)


def _delayed_orb_session_loss_block(db: Session, *, user_id: str, config: BotConfig) -> RiskBlock | None:
    if str(config.strategy_type) != _STRATEGY_DELAYED_ORB_CONFIRMATION:
        return None

    params = _normalize_strategy_params(config.strategy_type, config.strategy_params)
    stop_after_losses = int(params.get("stop_after_losses_per_session") or 0)
    if stop_after_losses <= 0:
        return None

    session_start, session_end = _session_window_utc_for_reference(
        datetime.now(timezone.utc),
        start_text=str(config.trading_start_time),
        end_text=str(config.trading_end_time),
    )
    contract_candidates = _unique_text_values([config.contract_id])
    symbol_candidates = _unique_text_values([config.symbol, normalize_symbol_key(config.symbol), normalize_symbol_key(config.contract_id)])
    filters = []
    if contract_candidates:
        filters.append(PositionLifecycle.contract_id.in_(contract_candidates))
    if symbol_candidates:
        filters.append(PositionLifecycle.symbol.in_(symbol_candidates))
    if not filters:
        return None

    losses = (
        db.query(func.count(PositionLifecycle.id))
        .filter(PositionLifecycle.user_id == user_id)
        .filter(PositionLifecycle.account_id == int(config.account_id))
        .filter(PositionLifecycle.closed_at >= session_start)
        .filter(PositionLifecycle.closed_at <= session_end)
        .filter(PositionLifecycle.realized_pnl_usd < 0)
        .filter(or_(*filters))
        .scalar()
    )
    if int(losses or 0) < stop_after_losses:
        return None

    return RiskBlock(
        code="session_loss_limit_reached",
        message="This ORB session already has a losing trade, so the bot is done for the day.",
    )


def _cooldown_block(db: Session, *, user_id: str, config: BotConfig) -> RiskBlock | None:
    cooldown_seconds = int(config.cooldown_seconds)
    if cooldown_seconds <= 0:
        return None
    threshold = datetime.now(timezone.utc) - timedelta(seconds=cooldown_seconds)
    recent_attempt = (
        db.query(BotOrderAttempt)
        .filter(BotOrderAttempt.user_id == user_id)
        .filter(BotOrderAttempt.bot_config_id == int(config.id))
        .filter(BotOrderAttempt.created_at >= threshold)
        .filter(BotOrderAttempt.status.in_(["blocked", "rejected", "error"]))
        .order_by(BotOrderAttempt.created_at.desc())
        .first()
    )
    if recent_attempt is not None:
        return RiskBlock(code="cooldown_after_rejection", message="Cooldown is active after a rejected or failed order.")

    recent_loss = (
        db.query(ProjectXTradeEvent)
        .filter(ProjectXTradeEvent.user_id == user_id)
        .filter(ProjectXTradeEvent.account_id == int(config.account_id))
        .filter(ProjectXTradeEvent.contract_id == str(config.contract_id))
        .filter(ProjectXTradeEvent.trade_timestamp >= threshold)
        .filter(ProjectXTradeEvent.pnl < 0)
        .order_by(ProjectXTradeEvent.trade_timestamp.desc())
        .first()
    )
    if recent_loss is not None:
        return RiskBlock(code="cooldown_after_loss", message="Cooldown is active after a losing trade.")
    return None


def _looks_like_live_funded_account(account: Account) -> bool:
    text = " ".join(
        value
        for value in [account.name, account.display_name, account.external_id]
        if isinstance(value, str)
    )
    return bool(_LIVE_ACCOUNT_PATTERN.search(text))


def _is_inside_trading_session(start_text: str, end_text: str) -> bool:
    start = _parse_session_time(start_text)
    end = _parse_session_time(end_text)
    current = datetime.now(TRADING_TZ).time().replace(second=0, microsecond=0)
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def _parse_session_time(value: str) -> time:
    _validate_session_time(value)
    hour_text, minute_text = value.split(":", 1)
    return time(hour=int(hour_text), minute=int(minute_text))


def _validate_session_time(value: str) -> None:
    if not re.fullmatch(r"\d{2}:\d{2}", str(value)):
        raise ValueError("session times must use HH:MM format")
    hour_text, minute_text = str(value).split(":", 1)
    hour = int(hour_text)
    minute = int(minute_text)
    if hour > 23 or minute > 59:
        raise ValueError("session times must use HH:MM format")


def _normalize_time_window_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})\s*", value)
    if not match:
        return None
    start_text, end_text = match.groups()
    _validate_session_time(start_text)
    _validate_session_time(end_text)
    return f"{start_text}-{end_text}"


def _normalize_time_window_values(value: Any, *, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    candidates = value
    if isinstance(value, str):
        candidates = [item for item in value.split(",")]
    if not isinstance(candidates, list):
        return list(default)

    output: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        normalized = _normalize_time_window_text(item)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        output.append(normalized)
    return output


def _timestamp_in_time_windows(timestamp: datetime, windows: list[str]) -> bool:
    if not windows:
        return False
    local_time = _as_utc(timestamp).astimezone(TRADING_TZ).time().replace(second=0, microsecond=0)
    for window in windows:
        start_text, end_text = window.split("-", 1)
        start = _parse_session_time(start_text)
        end = _parse_session_time(end_text)
        if start <= end:
            if start <= local_time <= end:
                return True
        elif local_time >= start or local_time <= end:
            return True
    return False


def _validate_strategy_periods(fast_period: int, slow_period: int) -> None:
    if int(fast_period) <= 0:
        raise ValueError("fast_period must be positive")
    if int(slow_period) <= int(fast_period):
        raise ValueError("slow_period must be greater than fast_period")


def _validate_strategy_configuration(
    *,
    strategy_type: str,
    timeframe_unit: str,
    timeframe_unit_number: int,
    fast_period: int,
    slow_period: int,
) -> None:
    _validate_strategy_periods(fast_period, slow_period)
    if strategy_type != _STRATEGY_EMA_SCALPING:
        return
    if str(timeframe_unit) != "minute" or int(timeframe_unit_number) not in _EMA_SCALPING_ALLOWED_MINUTE_BUCKETS:
        raise ValueError("9/15 EMA scalping requires a 3-minute or 5-minute timeframe.")


def _validate_strategy_type(value: Any) -> str:
    strategy_type = str(value or _STRATEGY_SMA_CROSS).strip()
    if strategy_type not in _SUPPORTED_STRATEGY_TYPES:
        raise ValueError("unsupported bot strategy type")
    return strategy_type


def _normalize_strategy_params(strategy_type: Any, params: Any) -> dict[str, Any]:
    normalized_strategy_type = _validate_strategy_type(strategy_type)
    if normalized_strategy_type in {_STRATEGY_SMA_CROSS, _STRATEGY_EMA_SCALPING}:
        return {}

    raw_params = params if isinstance(params, dict) else {}
    if normalized_strategy_type == _STRATEGY_EMA_TREND_PULLBACK:
        long_rsi_min = _bounded_float_param(
            raw_params,
            "long_rsi_min",
            float(_EMA_TREND_PULLBACK_DEFAULTS["long_rsi_min"]),
            minimum=0,
            maximum=100,
        )
        long_rsi_max = _bounded_float_param(
            raw_params,
            "long_rsi_max",
            float(_EMA_TREND_PULLBACK_DEFAULTS["long_rsi_max"]),
            minimum=0,
            maximum=100,
        )
        short_rsi_min = _bounded_float_param(
            raw_params,
            "short_rsi_min",
            float(_EMA_TREND_PULLBACK_DEFAULTS["short_rsi_min"]),
            minimum=0,
            maximum=100,
        )
        short_rsi_max = _bounded_float_param(
            raw_params,
            "short_rsi_max",
            float(_EMA_TREND_PULLBACK_DEFAULTS["short_rsi_max"]),
            minimum=0,
            maximum=100,
        )
        partial_take_profit_r_multiple = _bounded_float_param(
            raw_params,
            "partial_take_profit_r_multiple",
            float(_EMA_TREND_PULLBACK_DEFAULTS["partial_take_profit_r_multiple"]),
            minimum=0.1,
            maximum=20,
        )
        final_take_profit_r_multiple = _bounded_float_param(
            raw_params,
            "final_take_profit_r_multiple",
            float(_EMA_TREND_PULLBACK_DEFAULTS["final_take_profit_r_multiple"]),
            minimum=0.1,
            maximum=20,
        )
        if long_rsi_max <= long_rsi_min:
            long_rsi_min = float(_EMA_TREND_PULLBACK_DEFAULTS["long_rsi_min"])
            long_rsi_max = float(_EMA_TREND_PULLBACK_DEFAULTS["long_rsi_max"])
        if short_rsi_max <= short_rsi_min:
            short_rsi_min = float(_EMA_TREND_PULLBACK_DEFAULTS["short_rsi_min"])
            short_rsi_max = float(_EMA_TREND_PULLBACK_DEFAULTS["short_rsi_max"])
        if final_take_profit_r_multiple <= partial_take_profit_r_multiple:
            partial_take_profit_r_multiple = float(_EMA_TREND_PULLBACK_DEFAULTS["partial_take_profit_r_multiple"])
            final_take_profit_r_multiple = float(_EMA_TREND_PULLBACK_DEFAULTS["final_take_profit_r_multiple"])
        return {
            "rsi_period": _bounded_int_param(
                raw_params,
                "rsi_period",
                int(_EMA_TREND_PULLBACK_DEFAULTS["rsi_period"]),
                minimum=2,
                maximum=200,
            ),
            "volume_average_period": _bounded_int_param(
                raw_params,
                "volume_average_period",
                int(_EMA_TREND_PULLBACK_DEFAULTS["volume_average_period"]),
                minimum=2,
                maximum=200,
            ),
            "swing_lookback_bars": _bounded_int_param(
                raw_params,
                "swing_lookback_bars",
                int(_EMA_TREND_PULLBACK_DEFAULTS["swing_lookback_bars"]),
                minimum=2,
                maximum=100,
            ),
            "long_rsi_min": long_rsi_min,
            "long_rsi_max": long_rsi_max,
            "short_rsi_min": short_rsi_min,
            "short_rsi_max": short_rsi_max,
            "partial_take_profit_r_multiple": partial_take_profit_r_multiple,
            "final_take_profit_r_multiple": final_take_profit_r_multiple,
        }

    if normalized_strategy_type == _STRATEGY_DONCHIAN_BREAKOUT:
        entry_period = _bounded_int_param(
            raw_params,
            "entry_period",
            int(_DONCHIAN_BREAKOUT_DEFAULTS["entry_period"]),
            minimum=2,
            maximum=500,
        )
        exit_period_default = max(2, entry_period // 2)
        return {
            "entry_period": entry_period,
            "exit_period": _bounded_int_param(
                raw_params,
                "exit_period",
                int(raw_params.get("exit_period", exit_period_default) or exit_period_default),
                minimum=2,
                maximum=max(2, entry_period),
            ),
            "atr_period": _bounded_int_param(
                raw_params,
                "atr_period",
                int(_DONCHIAN_BREAKOUT_DEFAULTS["atr_period"]),
                minimum=2,
                maximum=200,
            ),
            "atr_stop_multiple": _bounded_float_param(
                raw_params,
                "atr_stop_multiple",
                float(_DONCHIAN_BREAKOUT_DEFAULTS["atr_stop_multiple"]),
                minimum=0.1,
                maximum=20,
            ),
            "take_profit_r_multiple": _bounded_float_param(
                raw_params,
                "take_profit_r_multiple",
                float(_DONCHIAN_BREAKOUT_DEFAULTS["take_profit_r_multiple"]),
                minimum=0.1,
                maximum=20,
            ),
            "atr_trail_multiple": _bounded_float_param(
                raw_params,
                "atr_trail_multiple",
                float(_DONCHIAN_BREAKOUT_DEFAULTS["atr_trail_multiple"]),
                minimum=0.1,
                maximum=20,
            ),
            "atr_size_reference_percent": _bounded_float_param(
                raw_params,
                "atr_size_reference_percent",
                float(_DONCHIAN_BREAKOUT_DEFAULTS["atr_size_reference_percent"]),
                minimum=0.05,
                maximum=25,
            ),
            "min_size_scale": _bounded_float_param(
                raw_params,
                "min_size_scale",
                float(_DONCHIAN_BREAKOUT_DEFAULTS["min_size_scale"]),
                minimum=0.1,
                maximum=1.0,
            ),
        }

    if normalized_strategy_type == _STRATEGY_ATR_ADJUSTED_RELATIVE_STRENGTH:
        benchmark_symbol = _normalized_optional_text(raw_params.get("benchmark_symbol")) or str(
            _ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS["benchmark_symbol"]
        )
        benchmark_contract_id = _normalized_optional_text(raw_params.get("benchmark_contract_id"))
        return {
            "benchmark_symbol": benchmark_symbol,
            "benchmark_contract_id": benchmark_contract_id,
            "move_lookback_bars": _bounded_int_param(
                raw_params,
                "move_lookback_bars",
                int(_ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS["move_lookback_bars"]),
                minimum=1,
                maximum=100,
            ),
            "atr_period": _bounded_int_param(
                raw_params,
                "atr_period",
                int(_ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS["atr_period"]),
                minimum=2,
                maximum=200,
            ),
            "relative_volume_period": _bounded_int_param(
                raw_params,
                "relative_volume_period",
                int(_ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS["relative_volume_period"]),
                minimum=2,
                maximum=200,
            ),
            "relative_volume_cap": _bounded_float_param(
                raw_params,
                "relative_volume_cap",
                float(_ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS["relative_volume_cap"]),
                minimum=0.25,
                maximum=20,
            ),
            "long_score_threshold": _bounded_float_param(
                raw_params,
                "long_score_threshold",
                float(_ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS["long_score_threshold"]),
                minimum=0.1,
                maximum=100,
            ),
            "short_score_threshold": _bounded_float_param(
                raw_params,
                "short_score_threshold",
                float(_ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS["short_score_threshold"]),
                minimum=-100,
                maximum=-0.1,
            ),
            "ema_period": _bounded_int_param(
                raw_params,
                "ema_period",
                int(_ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS["ema_period"]),
                minimum=2,
                maximum=200,
            ),
            "stop_structure_window": _bounded_int_param(
                raw_params,
                "stop_structure_window",
                int(_ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS["stop_structure_window"]),
                minimum=2,
                maximum=100,
            ),
            "stop_atr_multiple": _bounded_float_param(
                raw_params,
                "stop_atr_multiple",
                float(_ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS["stop_atr_multiple"]),
                minimum=0,
                maximum=10,
            ),
            "take_profit_r_multiple": _bounded_float_param(
                raw_params,
                "take_profit_r_multiple",
                float(_ATR_ADJUSTED_RELATIVE_STRENGTH_DEFAULTS["take_profit_r_multiple"]),
                minimum=0.1,
                maximum=20,
            ),
        }

    if normalized_strategy_type == _STRATEGY_RELATIVE_STRENGTH_SPY:
        benchmark_symbol = _normalized_optional_text(raw_params.get("benchmark_symbol")) or str(
            _RELATIVE_STRENGTH_SPY_DEFAULTS["benchmark_symbol"]
        )
        benchmark_contract_id = _normalized_optional_text(raw_params.get("benchmark_contract_id"))
        swing_window = _bounded_int_param(
            raw_params,
            "swing_window",
            int(_RELATIVE_STRENGTH_SPY_DEFAULTS["swing_window"]),
            minimum=3,
            maximum=21,
        )
        if swing_window % 2 == 0:
            swing_window += 1
        return {
            "benchmark_symbol": benchmark_symbol,
            "benchmark_contract_id": benchmark_contract_id,
            "comparison_bars": _bounded_int_param(
                raw_params,
                "comparison_bars",
                int(_RELATIVE_STRENGTH_SPY_DEFAULTS["comparison_bars"]),
                minimum=4,
                maximum=100,
            ),
            "pullback_lookback_bars": _bounded_int_param(
                raw_params,
                "pullback_lookback_bars",
                int(_RELATIVE_STRENGTH_SPY_DEFAULTS["pullback_lookback_bars"]),
                minimum=2,
                maximum=20,
            ),
            "relative_volume_period": _bounded_int_param(
                raw_params,
                "relative_volume_period",
                int(_RELATIVE_STRENGTH_SPY_DEFAULTS["relative_volume_period"]),
                minimum=2,
                maximum=200,
            ),
            "minimum_relative_volume": _bounded_float_param(
                raw_params,
                "minimum_relative_volume",
                float(_RELATIVE_STRENGTH_SPY_DEFAULTS["minimum_relative_volume"]),
                minimum=0.5,
                maximum=20,
            ),
            "minimum_relative_strength_percent": _bounded_float_param(
                raw_params,
                "minimum_relative_strength_percent",
                float(_RELATIVE_STRENGTH_SPY_DEFAULTS["minimum_relative_strength_percent"]),
                minimum=0.01,
                maximum=20,
            ),
            "minimum_benchmark_move_percent": _bounded_float_param(
                raw_params,
                "minimum_benchmark_move_percent",
                float(_RELATIVE_STRENGTH_SPY_DEFAULTS["minimum_benchmark_move_percent"]),
                minimum=0.01,
                maximum=10,
            ),
            "ema_period": _bounded_int_param(
                raw_params,
                "ema_period",
                int(_RELATIVE_STRENGTH_SPY_DEFAULTS["ema_period"]),
                minimum=2,
                maximum=100,
            ),
            "swing_window": swing_window,
            "major_level_lookback_bars": _bounded_int_param(
                raw_params,
                "major_level_lookback_bars",
                int(_RELATIVE_STRENGTH_SPY_DEFAULTS["major_level_lookback_bars"]),
                minimum=10,
                maximum=300,
            ),
            "entry_level_tolerance_percent": _bounded_float_param(
                raw_params,
                "entry_level_tolerance_percent",
                float(_RELATIVE_STRENGTH_SPY_DEFAULTS["entry_level_tolerance_percent"]),
                minimum=0.05,
                maximum=10,
            ),
            "stop_buffer_percent": _bounded_float_param(
                raw_params,
                "stop_buffer_percent",
                float(_RELATIVE_STRENGTH_SPY_DEFAULTS["stop_buffer_percent"]),
                minimum=0.01,
                maximum=10,
            ),
            "take_profit_r_multiple": _bounded_float_param(
                raw_params,
                "take_profit_r_multiple",
                float(_RELATIVE_STRENGTH_SPY_DEFAULTS["take_profit_r_multiple"]),
                minimum=0.1,
                maximum=20,
            ),
        }

    if normalized_strategy_type == _STRATEGY_SUPPORT_RESISTANCE:
        bars_per_timeframe = _bounded_int_param(raw_params, "bars_per_timeframe", 100, minimum=25, maximum=500)
        swing_window = _bounded_int_param(raw_params, "swing_window", 5, minimum=3, maximum=51)
        if swing_window % 2 == 0:
            swing_window += 1
        return {
            "bars_per_timeframe": bars_per_timeframe,
            "swing_window": swing_window,
            "level_tolerance_percent": _bounded_float_param(
                raw_params,
                "level_tolerance_percent",
                float(_SUPPORT_RESISTANCE_DEFAULTS["level_tolerance_percent"]),
                minimum=0.01,
                maximum=10,
            ),
            "stop_beyond_level_percent": _bounded_float_param(
                raw_params,
                "stop_beyond_level_percent",
                float(_SUPPORT_RESISTANCE_DEFAULTS["stop_beyond_level_percent"]),
                minimum=0.01,
                maximum=20,
            ),
            "take_profit_r_multiple": _bounded_float_param(
                raw_params,
                "take_profit_r_multiple",
                float(_SUPPORT_RESISTANCE_DEFAULTS["take_profit_r_multiple"]),
                minimum=0.1,
                maximum=20,
            ),
        }

    if normalized_strategy_type == _STRATEGY_FVG_SWEEP_MSS:
        swing_window = _bounded_int_param(
            raw_params,
            "swing_window",
            int(_FVG_SWEEP_MSS_DEFAULTS["swing_window"]),
            minimum=3,
            maximum=21,
        )
        if swing_window % 2 == 0:
            swing_window += 1
        target_mode = str(raw_params.get("target_mode", _FVG_SWEEP_MSS_DEFAULTS["target_mode"])).strip().lower()
        if target_mode not in _FVG_SWEEP_MSS_TARGET_MODES:
            target_mode = str(_FVG_SWEEP_MSS_DEFAULTS["target_mode"])
        return {
            "swing_window": swing_window,
            "volume_lookback_bars": _bounded_int_param(
                raw_params,
                "volume_lookback_bars",
                int(_FVG_SWEEP_MSS_DEFAULTS["volume_lookback_bars"]),
                minimum=5,
                maximum=200,
            ),
            "strong_volume_multiplier": _bounded_float_param(
                raw_params,
                "strong_volume_multiplier",
                float(_FVG_SWEEP_MSS_DEFAULTS["strong_volume_multiplier"]),
                minimum=1,
                maximum=10,
            ),
            "stop_buffer_percent": _bounded_float_param(
                raw_params,
                "stop_buffer_percent",
                float(_FVG_SWEEP_MSS_DEFAULTS["stop_buffer_percent"]),
                minimum=0,
                maximum=5,
            ),
            "target_mode": target_mode,
        }

    if normalized_strategy_type == _STRATEGY_LIQUIDITY_SWEEP_RETEST:
        bars_per_timeframe = _bounded_int_param(
            raw_params,
            "bars_per_timeframe",
            int(_LIQUIDITY_SWEEP_RETEST_DEFAULTS["bars_per_timeframe"]),
            minimum=25,
            maximum=500,
        )
        swing_window = _bounded_int_param(
            raw_params,
            "swing_window",
            int(_LIQUIDITY_SWEEP_RETEST_DEFAULTS["swing_window"]),
            minimum=3,
            maximum=51,
        )
        if swing_window % 2 == 0:
            swing_window += 1
        take_profit_mode = str(
            raw_params.get("take_profit_mode", _LIQUIDITY_SWEEP_RETEST_DEFAULTS["take_profit_mode"])
        ).strip()
        if take_profit_mode not in _LIQUIDITY_SWEEP_TARGET_MODES:
            take_profit_mode = str(_LIQUIDITY_SWEEP_RETEST_DEFAULTS["take_profit_mode"])
        return {
            "bars_per_timeframe": bars_per_timeframe,
            "swing_window": swing_window,
            "level_tolerance_percent": _bounded_float_param(
                raw_params,
                "level_tolerance_percent",
                float(_LIQUIDITY_SWEEP_RETEST_DEFAULTS["level_tolerance_percent"]),
                minimum=0.01,
                maximum=10,
            ),
            "reclaim_within_bars": _bounded_int_param(
                raw_params,
                "reclaim_within_bars",
                int(_LIQUIDITY_SWEEP_RETEST_DEFAULTS["reclaim_within_bars"]),
                minimum=1,
                maximum=10,
            ),
            "retest_within_bars": _bounded_int_param(
                raw_params,
                "retest_within_bars",
                int(_LIQUIDITY_SWEEP_RETEST_DEFAULTS["retest_within_bars"]),
                minimum=1,
                maximum=10,
            ),
            "stop_beyond_sweep_percent": _bounded_float_param(
                raw_params,
                "stop_beyond_sweep_percent",
                float(_LIQUIDITY_SWEEP_RETEST_DEFAULTS["stop_beyond_sweep_percent"]),
                minimum=0.001,
                maximum=5,
            ),
            "take_profit_mode": take_profit_mode,
        }

    if normalized_strategy_type == _STRATEGY_OPENING_RVOL_BREAKOUT:
        return {
            "relative_volume_lookback_days": _bounded_int_param(
                raw_params,
                "relative_volume_lookback_days",
                int(_OPENING_RVOL_BREAKOUT_DEFAULTS["relative_volume_lookback_days"]),
                minimum=3,
                maximum=60,
            ),
            "min_relative_volume": _bounded_float_param(
                raw_params,
                "min_relative_volume",
                float(_OPENING_RVOL_BREAKOUT_DEFAULTS["min_relative_volume"]),
                minimum=0.1,
                maximum=20,
            ),
            "min_opening_volume": _bounded_float_param(
                raw_params,
                "min_opening_volume",
                float(_OPENING_RVOL_BREAKOUT_DEFAULTS["min_opening_volume"]),
                minimum=0,
                maximum=10_000_000_000,
            ),
            "min_body_to_range_ratio": _bounded_float_param(
                raw_params,
                "min_body_to_range_ratio",
                float(_OPENING_RVOL_BREAKOUT_DEFAULTS["min_body_to_range_ratio"]),
                minimum=0.05,
                maximum=1,
            ),
            "atr_period": _bounded_int_param(
                raw_params,
                "atr_period",
                int(_OPENING_RVOL_BREAKOUT_DEFAULTS["atr_period"]),
                minimum=2,
                maximum=200,
            ),
            "atr_stop_multiple": _bounded_float_param(
                raw_params,
                "atr_stop_multiple",
                float(_OPENING_RVOL_BREAKOUT_DEFAULTS["atr_stop_multiple"]),
                minimum=0.1,
                maximum=10,
            ),
            "take_profit_r_multiple": _bounded_float_param(
                raw_params,
                "take_profit_r_multiple",
                float(_OPENING_RVOL_BREAKOUT_DEFAULTS["take_profit_r_multiple"]),
                minimum=0.1,
                maximum=20,
            ),
        }

    if normalized_strategy_type == _STRATEGY_BOLLINGER_MEAN_REVERSION:
        take_profit_mode = str(
            raw_params.get("take_profit_mode", _BOLLINGER_MEAN_REVERSION_DEFAULTS["take_profit_mode"])
        ).strip()
        if take_profit_mode not in _BOLLINGER_MEAN_REVERSION_TAKE_PROFIT_MODES:
            take_profit_mode = str(_BOLLINGER_MEAN_REVERSION_DEFAULTS["take_profit_mode"])
        return {
            "bollinger_period": _bounded_int_param(
                raw_params,
                "bollinger_period",
                int(_BOLLINGER_MEAN_REVERSION_DEFAULTS["bollinger_period"]),
                minimum=60,
                maximum=180,
            ),
            "bollinger_stddev": _bounded_float_param(
                raw_params,
                "bollinger_stddev",
                float(_BOLLINGER_MEAN_REVERSION_DEFAULTS["bollinger_stddev"]),
                minimum=4.0,
                maximum=5.0,
            ),
            "atr_period": _bounded_int_param(
                raw_params,
                "atr_period",
                int(_BOLLINGER_MEAN_REVERSION_DEFAULTS["atr_period"]),
                minimum=2,
                maximum=200,
            ),
            "atr_stop_buffer": _bounded_float_param(
                raw_params,
                "atr_stop_buffer",
                float(_BOLLINGER_MEAN_REVERSION_DEFAULTS["atr_stop_buffer"]),
                minimum=0.05,
                maximum=10.0,
            ),
            "take_profit_mode": take_profit_mode,
            "take_profit_r_multiple": _bounded_float_param(
                raw_params,
                "take_profit_r_multiple",
                float(_BOLLINGER_MEAN_REVERSION_DEFAULTS["take_profit_r_multiple"]),
                minimum=0.1,
                maximum=20.0,
            ),
            "news_blackout_windows": _normalize_time_window_values(
                raw_params.get("news_blackout_windows"),
                default=list(_BOLLINGER_MEAN_REVERSION_DEFAULTS["news_blackout_windows"]),
            ),
        }

    if normalized_strategy_type == _STRATEGY_MACD_SUPPORT_RESISTANCE:
        bars_per_timeframe = _bounded_int_param(
            raw_params,
            "bars_per_timeframe",
            int(_MACD_SUPPORT_RESISTANCE_DEFAULTS["bars_per_timeframe"]),
            minimum=25,
            maximum=500,
        )
        swing_window = _bounded_int_param(
            raw_params,
            "swing_window",
            int(_MACD_SUPPORT_RESISTANCE_DEFAULTS["swing_window"]),
            minimum=3,
            maximum=51,
        )
        if swing_window % 2 == 0:
            swing_window += 1
        trailing_stop_mode = str(
            raw_params.get("trailing_stop_mode", _MACD_SUPPORT_RESISTANCE_DEFAULTS["trailing_stop_mode"])
        ).strip()
        if trailing_stop_mode not in _MACD_SUPPORT_RESISTANCE_TRAILING_STOP_MODES:
            trailing_stop_mode = str(_MACD_SUPPORT_RESISTANCE_DEFAULTS["trailing_stop_mode"])
        return {
            "bars_per_timeframe": bars_per_timeframe,
            "swing_window": swing_window,
            "level_tolerance_percent": _bounded_float_param(
                raw_params,
                "level_tolerance_percent",
                float(_MACD_SUPPORT_RESISTANCE_DEFAULTS["level_tolerance_percent"]),
                minimum=0.01,
                maximum=10,
            ),
            "signal_period": _bounded_int_param(
                raw_params,
                "signal_period",
                int(_MACD_SUPPORT_RESISTANCE_DEFAULTS["signal_period"]),
                minimum=2,
                maximum=200,
            ),
            "atr_period": _bounded_int_param(
                raw_params,
                "atr_period",
                int(_MACD_SUPPORT_RESISTANCE_DEFAULTS["atr_period"]),
                minimum=2,
                maximum=200,
            ),
            "initial_stop_atr_multiplier": _bounded_float_param(
                raw_params,
                "initial_stop_atr_multiplier",
                float(_MACD_SUPPORT_RESISTANCE_DEFAULTS["initial_stop_atr_multiplier"]),
                minimum=0.1,
                maximum=20,
            ),
            "trailing_stop_mode": trailing_stop_mode,
            "trailing_atr_multiplier": _bounded_float_param(
                raw_params,
                "trailing_atr_multiplier",
                float(_MACD_SUPPORT_RESISTANCE_DEFAULTS["trailing_atr_multiplier"]),
                minimum=0.1,
                maximum=20,
            ),
            "trailing_ma_period": _bounded_int_param(
                raw_params,
                "trailing_ma_period",
                int(_MACD_SUPPORT_RESISTANCE_DEFAULTS["trailing_ma_period"]),
                minimum=2,
                maximum=500,
            ),
        }

    if normalized_strategy_type == _STRATEGY_BOLLINGER_RSI_REVERSAL:
        take_profit_mode = str(
            raw_params.get("take_profit_mode", _BOLLINGER_RSI_REVERSAL_DEFAULTS["take_profit_mode"])
        ).strip().lower()
        if take_profit_mode not in _BOLLINGER_RSI_REVERSAL_TAKE_PROFIT_MODES:
            take_profit_mode = str(_BOLLINGER_RSI_REVERSAL_DEFAULTS["take_profit_mode"])
        rsi_oversold = _bounded_float_param(
            raw_params,
            "rsi_oversold",
            float(_BOLLINGER_RSI_REVERSAL_DEFAULTS["rsi_oversold"]),
            minimum=1,
            maximum=49,
        )
        rsi_overbought = _bounded_float_param(
            raw_params,
            "rsi_overbought",
            float(_BOLLINGER_RSI_REVERSAL_DEFAULTS["rsi_overbought"]),
            minimum=51,
            maximum=99,
        )
        if rsi_overbought <= rsi_oversold:
            rsi_oversold = float(_BOLLINGER_RSI_REVERSAL_DEFAULTS["rsi_oversold"])
            rsi_overbought = float(_BOLLINGER_RSI_REVERSAL_DEFAULTS["rsi_overbought"])
        return {
            "rsi_period": _bounded_int_param(
                raw_params,
                "rsi_period",
                int(_BOLLINGER_RSI_REVERSAL_DEFAULTS["rsi_period"]),
                minimum=2,
                maximum=200,
            ),
            "rsi_oversold": rsi_oversold,
            "rsi_overbought": rsi_overbought,
            "bollinger_period": _bounded_int_param(
                raw_params,
                "bollinger_period",
                int(_BOLLINGER_RSI_REVERSAL_DEFAULTS["bollinger_period"]),
                minimum=5,
                maximum=200,
            ),
            "bollinger_stddev": _bounded_float_param(
                raw_params,
                "bollinger_stddev",
                float(_BOLLINGER_RSI_REVERSAL_DEFAULTS["bollinger_stddev"]),
                minimum=0.5,
                maximum=5,
            ),
            "adx_period": _bounded_int_param(
                raw_params,
                "adx_period",
                int(_BOLLINGER_RSI_REVERSAL_DEFAULTS["adx_period"]),
                minimum=2,
                maximum=200,
            ),
            "adx_max": _bounded_float_param(
                raw_params,
                "adx_max",
                float(_BOLLINGER_RSI_REVERSAL_DEFAULTS["adx_max"]),
                minimum=1,
                maximum=100,
            ),
            "swing_stop_lookback_bars": _bounded_int_param(
                raw_params,
                "swing_stop_lookback_bars",
                int(_BOLLINGER_RSI_REVERSAL_DEFAULTS["swing_stop_lookback_bars"]),
                minimum=2,
                maximum=100,
            ),
            "stop_buffer_percent": _bounded_float_param(
                raw_params,
                "stop_buffer_percent",
                float(_BOLLINGER_RSI_REVERSAL_DEFAULTS["stop_buffer_percent"]),
                minimum=0,
                maximum=5,
            ),
            "take_profit_mode": take_profit_mode,
            "take_profit_r_multiple": _bounded_float_param(
                raw_params,
                "take_profit_r_multiple",
                float(_BOLLINGER_RSI_REVERSAL_DEFAULTS["take_profit_r_multiple"]),
                minimum=0.25,
                maximum=20,
            ),
        }

    if normalized_strategy_type == _STRATEGY_PULLBACK_TRAP_REVERSAL:
        pullback_lookback_bars = _bounded_int_param(
            raw_params,
            "pullback_lookback_bars",
            int(_PULLBACK_TRAP_REVERSAL_DEFAULTS["pullback_lookback_bars"]),
            minimum=2,
            maximum=20,
        )
        micro_level_window = _bounded_int_param(
            raw_params,
            "micro_level_window",
            int(_PULLBACK_TRAP_REVERSAL_DEFAULTS["micro_level_window"]),
            minimum=1,
            maximum=pullback_lookback_bars,
        )
        min_countertrend_bars = _bounded_int_param(
            raw_params,
            "min_countertrend_bars",
            int(_PULLBACK_TRAP_REVERSAL_DEFAULTS["min_countertrend_bars"]),
            minimum=1,
            maximum=pullback_lookback_bars,
        )
        return {
            "pullback_lookback_bars": pullback_lookback_bars,
            "micro_level_window": micro_level_window,
            "volume_baseline_bars": _bounded_int_param(
                raw_params,
                "volume_baseline_bars",
                int(_PULLBACK_TRAP_REVERSAL_DEFAULTS["volume_baseline_bars"]),
                minimum=1,
                maximum=100,
            ),
            "volume_spike_multiple": _bounded_float_param(
                raw_params,
                "volume_spike_multiple",
                float(_PULLBACK_TRAP_REVERSAL_DEFAULTS["volume_spike_multiple"]),
                minimum=1,
                maximum=10,
            ),
            "wick_to_body_ratio_min": _bounded_float_param(
                raw_params,
                "wick_to_body_ratio_min",
                float(_PULLBACK_TRAP_REVERSAL_DEFAULTS["wick_to_body_ratio_min"]),
                minimum=0.5,
                maximum=10,
            ),
            "stop_buffer_percent": _bounded_float_param(
                raw_params,
                "stop_buffer_percent",
                float(_PULLBACK_TRAP_REVERSAL_DEFAULTS["stop_buffer_percent"]),
                minimum=0,
                maximum=5,
            ),
            "take_profit_r_multiple": _bounded_float_param(
                raw_params,
                "take_profit_r_multiple",
                float(_PULLBACK_TRAP_REVERSAL_DEFAULTS["take_profit_r_multiple"]),
                minimum=0.25,
                maximum=20,
            ),
            "trend_confirmation_bars": _bounded_int_param(
                raw_params,
                "trend_confirmation_bars",
                int(_PULLBACK_TRAP_REVERSAL_DEFAULTS["trend_confirmation_bars"]),
                minimum=2,
                maximum=10,
            ),
            "min_countertrend_bars": min_countertrend_bars,
            "pullback_range_multiplier": _bounded_float_param(
                raw_params,
                "pullback_range_multiplier",
                float(_PULLBACK_TRAP_REVERSAL_DEFAULTS["pullback_range_multiplier"]),
                minimum=0.5,
                maximum=5,
            ),
            "prior_swing_window": _bounded_int_param(
                raw_params,
                "prior_swing_window",
                int(_PULLBACK_TRAP_REVERSAL_DEFAULTS["prior_swing_window"]),
                minimum=3,
                maximum=50,
            ),
        }

    if normalized_strategy_type == _STRATEGY_SUPERTREND_PIVOT:
        return {
            "daily_bars": _bounded_int_param(
                raw_params,
                "daily_bars",
                int(_SUPERTREND_PIVOT_DEFAULTS["daily_bars"]),
                minimum=2,
                maximum=30,
            ),
            "supertrend_period": _bounded_int_param(
                raw_params,
                "supertrend_period",
                int(_SUPERTREND_PIVOT_DEFAULTS["supertrend_period"]),
                minimum=2,
                maximum=200,
            ),
            "supertrend_multiplier": _bounded_float_param(
                raw_params,
                "supertrend_multiplier",
                float(_SUPERTREND_PIVOT_DEFAULTS["supertrend_multiplier"]),
                minimum=0.1,
                maximum=20,
            ),
            "pivot_tolerance_percent": _bounded_float_param(
                raw_params,
                "pivot_tolerance_percent",
                float(_SUPERTREND_PIVOT_DEFAULTS["pivot_tolerance_percent"]),
                minimum=0.001,
                maximum=5,
            ),
            "stop_beyond_level_percent": _bounded_float_param(
                raw_params,
                "stop_beyond_level_percent",
                float(_SUPERTREND_PIVOT_DEFAULTS["stop_beyond_level_percent"]),
                minimum=0.001,
                maximum=5,
            ),
            "take_profit_r_multiple": _bounded_float_param(
                raw_params,
                "take_profit_r_multiple",
                float(_SUPERTREND_PIVOT_DEFAULTS["take_profit_r_multiple"]),
                minimum=0.1,
                maximum=10,
            ),
            "chop_lookback_bars": _bounded_int_param(
                raw_params,
                "chop_lookback_bars",
                int(_SUPERTREND_PIVOT_DEFAULTS["chop_lookback_bars"]),
                minimum=2,
                maximum=100,
            ),
            "chop_max_flips": _bounded_int_param(
                raw_params,
                "chop_max_flips",
                int(_SUPERTREND_PIVOT_DEFAULTS["chop_max_flips"]),
                minimum=1,
                maximum=20,
            ),
            "chop_max_range_percent": _bounded_float_param(
                raw_params,
                "chop_max_range_percent",
                float(_SUPERTREND_PIVOT_DEFAULTS["chop_max_range_percent"]),
                minimum=0.01,
                maximum=20,
            ),
        }

    if normalized_strategy_type == _STRATEGY_DELAYED_ORB_CONFIRMATION:
        stop_mode = str(
            raw_params.get("stop_mode", _DELAYED_ORB_CONFIRMATION_DEFAULTS["stop_mode"])
        ).strip()
        if stop_mode not in {_ORB_STOP_MODE_INSIDE_RANGE, _ORB_STOP_MODE_OPPOSITE_SIDE}:
            stop_mode = str(_DELAYED_ORB_CONFIRMATION_DEFAULTS["stop_mode"])
        target_mode = str(
            raw_params.get("target_mode", _DELAYED_ORB_CONFIRMATION_DEFAULTS["target_mode"])
        ).strip().lower()
        if target_mode not in {_ORB_TARGET_MODE_2R, _ORB_TARGET_MODE_3R, _ORB_TARGET_MODE_MEASURED_MOVE}:
            target_mode = str(_DELAYED_ORB_CONFIRMATION_DEFAULTS["target_mode"])
        return {
            "opening_range_minutes": _bounded_int_param(
                raw_params,
                "opening_range_minutes",
                int(_DELAYED_ORB_CONFIRMATION_DEFAULTS["opening_range_minutes"]),
                minimum=5,
                maximum=60,
            ),
            "confirmation_minutes": _bounded_int_param(
                raw_params,
                "confirmation_minutes",
                int(_DELAYED_ORB_CONFIRMATION_DEFAULTS["confirmation_minutes"]),
                minimum=4,
                maximum=6,
            ),
            "stop_mode": stop_mode,
            "target_mode": target_mode,
            "stop_after_losses_per_session": _bounded_int_param(
                raw_params,
                "stop_after_losses_per_session",
                int(_DELAYED_ORB_CONFIRMATION_DEFAULTS["stop_after_losses_per_session"]),
                minimum=0,
                maximum=10,
            ),
        }

    if normalized_strategy_type == _STRATEGY_ORB_FIBONACCI_PULLBACK:
        take_profit_mode = str(
            raw_params.get("take_profit_mode", _ORB_FIBONACCI_PULLBACK_DEFAULTS["take_profit_mode"])
        ).strip().lower()
        if take_profit_mode not in _ORB_FIBONACCI_PULLBACK_TAKE_PROFIT_MODES:
            take_profit_mode = str(_ORB_FIBONACCI_PULLBACK_DEFAULTS["take_profit_mode"])
        return {
            "opening_range_minutes": _bounded_int_param(
                raw_params,
                "opening_range_minutes",
                int(_ORB_FIBONACCI_PULLBACK_DEFAULTS["opening_range_minutes"]),
                minimum=15,
                maximum=30,
            ),
            "swing_lookback_bars": _bounded_int_param(
                raw_params,
                "swing_lookback_bars",
                int(_ORB_FIBONACCI_PULLBACK_DEFAULTS["swing_lookback_bars"]),
                minimum=2,
                maximum=20,
            ),
            "take_profit_mode": take_profit_mode,
        }

    if normalized_strategy_type == _STRATEGY_FISHER_MEAN_REVERSION:
        return {
            "fisher_length": _bounded_int_param(
                raw_params,
                "fisher_length",
                int(_FISHER_MEAN_REVERSION_DEFAULTS["fisher_length"]),
                minimum=2,
                maximum=200,
            ),
            "fisher_extreme_threshold": _bounded_float_param(
                raw_params,
                "fisher_extreme_threshold",
                float(_FISHER_MEAN_REVERSION_DEFAULTS["fisher_extreme_threshold"]),
                minimum=0.1,
                maximum=10,
            ),
            "price_stretch_percent": _bounded_float_param(
                raw_params,
                "price_stretch_percent",
                float(_FISHER_MEAN_REVERSION_DEFAULTS["price_stretch_percent"]),
                minimum=0.01,
                maximum=20,
            ),
            "ema_slope_lookback_bars": _bounded_int_param(
                raw_params,
                "ema_slope_lookback_bars",
                int(_FISHER_MEAN_REVERSION_DEFAULTS["ema_slope_lookback_bars"]),
                minimum=1,
                maximum=100,
            ),
            "ema_slope_max_percent": _bounded_float_param(
                raw_params,
                "ema_slope_max_percent",
                float(_FISHER_MEAN_REVERSION_DEFAULTS["ema_slope_max_percent"]),
                minimum=0.01,
                maximum=20,
            ),
            "swing_stop_lookback_bars": _bounded_int_param(
                raw_params,
                "swing_stop_lookback_bars",
                int(_FISHER_MEAN_REVERSION_DEFAULTS["swing_stop_lookback_bars"]),
                minimum=2,
                maximum=100,
            ),
            "take_profit_r_multiple": _bounded_float_param(
                raw_params,
                "take_profit_r_multiple",
                float(_FISHER_MEAN_REVERSION_DEFAULTS["take_profit_r_multiple"]),
                minimum=0.1,
                maximum=20,
            ),
        }

    if normalized_strategy_type == _STRATEGY_VWAP_GAP_RETRACE:
        wait_start_minutes = _bounded_int_param(
            raw_params,
            "wait_start_minutes",
            int(_VWAP_GAP_RETRACE_DEFAULTS["wait_start_minutes"]),
            minimum=1,
            maximum=30,
        )
        wait_end_minutes = _bounded_int_param(
            raw_params,
            "wait_end_minutes",
            int(_VWAP_GAP_RETRACE_DEFAULTS["wait_end_minutes"]),
            minimum=wait_start_minutes,
            maximum=60,
        )
        return {
            "min_gap_percent": _bounded_float_param(
                raw_params,
                "min_gap_percent",
                float(_VWAP_GAP_RETRACE_DEFAULTS["min_gap_percent"]),
                minimum=0.1,
                maximum=20,
            ),
            "wait_start_minutes": wait_start_minutes,
            "wait_end_minutes": wait_end_minutes,
            "min_volume_ratio": _bounded_float_param(
                raw_params,
                "min_volume_ratio",
                float(_VWAP_GAP_RETRACE_DEFAULTS["min_volume_ratio"]),
                minimum=0,
                maximum=20,
            ),
            "stop_beyond_vwap_percent": _bounded_float_param(
                raw_params,
                "stop_beyond_vwap_percent",
                float(_VWAP_GAP_RETRACE_DEFAULTS["stop_beyond_vwap_percent"]),
                minimum=0.01,
                maximum=5,
            ),
            "touch_tolerance_percent": _bounded_float_param(
                raw_params,
                "touch_tolerance_percent",
                float(_VWAP_GAP_RETRACE_DEFAULTS["touch_tolerance_percent"]),
                minimum=0,
                maximum=2,
            ),
            "bars_to_fetch": _bounded_int_param(
                raw_params,
                "bars_to_fetch",
                int(_VWAP_GAP_RETRACE_DEFAULTS["bars_to_fetch"]),
                minimum=200,
                maximum=5000,
            ),
        }

    take_profit_mode = str(
        raw_params.get("take_profit_mode", _VWAP_ATR_MEAN_REVERSION_DEFAULTS["take_profit_mode"])
    ).strip()
    if take_profit_mode not in _VWAP_ATR_MEAN_REVERSION_TAKE_PROFIT_MODES:
        take_profit_mode = str(_VWAP_ATR_MEAN_REVERSION_DEFAULTS["take_profit_mode"])
    rsi_oversold = _bounded_float_param(
        raw_params,
        "rsi_oversold",
        float(_VWAP_ATR_MEAN_REVERSION_DEFAULTS["rsi_oversold"]),
        minimum=1,
        maximum=49,
    )
    rsi_overbought = _bounded_float_param(
        raw_params,
        "rsi_overbought",
        float(_VWAP_ATR_MEAN_REVERSION_DEFAULTS["rsi_overbought"]),
        minimum=51,
        maximum=99,
    )
    if rsi_overbought <= rsi_oversold:
        rsi_oversold = float(_VWAP_ATR_MEAN_REVERSION_DEFAULTS["rsi_oversold"])
        rsi_overbought = float(_VWAP_ATR_MEAN_REVERSION_DEFAULTS["rsi_overbought"])

    return {
        "atr_period": _bounded_int_param(
            raw_params,
            "atr_period",
            int(_VWAP_ATR_MEAN_REVERSION_DEFAULTS["atr_period"]),
            minimum=2,
            maximum=200,
        ),
        "rsi_period": _bounded_int_param(
            raw_params,
            "rsi_period",
            int(_VWAP_ATR_MEAN_REVERSION_DEFAULTS["rsi_period"]),
            minimum=2,
            maximum=200,
        ),
        "adx_period": _bounded_int_param(
            raw_params,
            "adx_period",
            int(_VWAP_ATR_MEAN_REVERSION_DEFAULTS["adx_period"]),
            minimum=2,
            maximum=200,
        ),
        "stretch_atr_multiple": _bounded_float_param(
            raw_params,
            "stretch_atr_multiple",
            float(_VWAP_ATR_MEAN_REVERSION_DEFAULTS["stretch_atr_multiple"]),
            minimum=0.1,
            maximum=10,
        ),
        "rsi_oversold": rsi_oversold,
        "rsi_overbought": rsi_overbought,
        "adx_max": _bounded_float_param(
            raw_params,
            "adx_max",
            float(_VWAP_ATR_MEAN_REVERSION_DEFAULTS["adx_max"]),
            minimum=1,
            maximum=100,
        ),
        "vwap_slope_bars": _bounded_int_param(
            raw_params,
            "vwap_slope_bars",
            int(_VWAP_ATR_MEAN_REVERSION_DEFAULTS["vwap_slope_bars"]),
            minimum=1,
            maximum=100,
        ),
        "flat_vwap_threshold_bps": _bounded_float_param(
            raw_params,
            "flat_vwap_threshold_bps",
            float(_VWAP_ATR_MEAN_REVERSION_DEFAULTS["flat_vwap_threshold_bps"]),
            minimum=0.1,
            maximum=500,
        ),
        "local_extreme_lookback": _bounded_int_param(
            raw_params,
            "local_extreme_lookback",
            int(_VWAP_ATR_MEAN_REVERSION_DEFAULTS["local_extreme_lookback"]),
            minimum=2,
            maximum=100,
        ),
        "stop_buffer_atr": _bounded_float_param(
            raw_params,
            "stop_buffer_atr",
            float(_VWAP_ATR_MEAN_REVERSION_DEFAULTS["stop_buffer_atr"]),
            minimum=0,
            maximum=5,
        ),
        "take_profit_mode": take_profit_mode,
        "take_profit_r_multiple": _bounded_float_param(
            raw_params,
            "take_profit_r_multiple",
            float(_VWAP_ATR_MEAN_REVERSION_DEFAULTS["take_profit_r_multiple"]),
            minimum=0.1,
            maximum=20,
        ),
    }


def _bounded_int_param(
    params: dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(params.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _bounded_float_param(
    params: dict[str, Any],
    key: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(params.get(key, default))
    except (TypeError, ValueError):
        value = default
    if not value == value:
        value = default
    return max(minimum, min(maximum, value))


def _validate_unique_bot_name(
    db: Session,
    *,
    user_id: str,
    name: Any,
    exclude_bot_config_id: int | None = None,
) -> str:
    normalized_name = str(name).strip()
    if not normalized_name:
        raise ValueError("Bot name is required.")

    query = (
        db.query(BotConfig.id)
        .filter(BotConfig.user_id == user_id)
        .filter(func.lower(func.trim(BotConfig.name)) == normalized_name.lower())
    )
    if exclude_bot_config_id is not None:
        query = query.filter(BotConfig.id != int(exclude_bot_config_id))
    if query.first() is not None:
        raise ValueError("A bot with this name already exists.")
    return normalized_name


def _looks_like_projectx_contract_id(value: str) -> bool:
    return value.upper().startswith("CON.")


def _pick_market_contract(rows: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    rows_list = [row for row in rows if isinstance(row, dict)]
    for row in rows_list:
        if bool(row.get("active_contract")):
            return row
    return rows_list[0] if rows_list else None


def _unique_text_values(values: Iterable[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalized_optional_text(value)
        if text is None:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def _normalize_allowed_contracts(values: Any) -> list[str]:
    if not values:
        return []
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _average(values: list[float]) -> float:
    return sum(values) / len(values)


def _scaled_donchian_entry_size(
    *,
    base_order_size: float,
    atr_percent: float,
    reference_percent: float,
    min_size_scale: float,
) -> tuple[int, float]:
    normalized_base = max(1, int(round(float(base_order_size))))
    if atr_percent <= 0 or atr_percent <= reference_percent:
        return normalized_base, 1.0

    scale = max(min_size_scale, min(1.0, reference_percent / atr_percent))
    return max(1, min(normalized_base, int(normalized_base * scale))), scale


def _candles_since_timestamp(
    candles: list[ProjectXMarketCandle],
    timestamp: datetime | None,
) -> list[ProjectXMarketCandle]:
    if timestamp is None:
        return candles
    threshold = _as_utc(timestamp)
    rows = [candle for candle in candles if _as_utc(candle.candle_timestamp) >= threshold]
    return rows or candles


def _optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _trade_side_sign(side: str | None) -> int:
    normalized = (side or "").strip().upper()
    if normalized == "BUY":
        return 1
    if normalized == "SELL":
        return -1
    return 0


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _normalized_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _market_candle_interval(*, unit: str, unit_number: int) -> timedelta:
    normalized_unit = str(unit).strip().lower()
    if normalized_unit not in _UNIT_SECONDS_BY_NAME:
        raise ValueError("unsupported candle unit")
    return timedelta(seconds=_UNIT_SECONDS_BY_NAME[normalized_unit] * max(1, int(unit_number)))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
