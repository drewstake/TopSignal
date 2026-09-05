from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping


TimeframeSource = Literal["configured", "fixed", "derived"]

BACKTEST_SUPPORTED_STRATEGY_IDENTIFIERS = frozenset(
    {
        "topbot_adaptive",
        "sma_cross",
        "ema_trend_pullback",
        "pullback_trap_reversal",
        "bollinger_mean_reversion",
        "bollinger_rsi_reversal",
        "vwap_atr_mean_reversion",
        "orb_fibonacci_pullback",
    }
)


@dataclass(frozen=True)
class TimeframeRequirement:
    """A candle series required to evaluate a strategy."""

    role: str
    source: TimeframeSource
    unit: str | None = None
    unit_number: int | None = None
    aligned_to: str | None = None
    derivation: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class HistoryRequirement:
    """Conservative history metadata plus the evaluator's exact hard floor."""

    role: str
    minimum_bars: int | None
    hard_minimum_bars: int | None = None
    minimum_sessions: int | None = None
    notes: str | None = None


@lru_cache(maxsize=1)
def _bot_service_module() -> Any:
    # Deliberately lazy: bot_service can import this registry without creating a
    # cycle, and evaluators continue to use bot_service's runtime-final helpers.
    return import_module(".bot_service", package=__package__)


@dataclass(frozen=True)
class LazyParameterNormalizer:
    identifier: str

    def __call__(self, params: Any = None) -> dict[str, Any]:
        service = _bot_service_module()
        return service._normalize_strategy_params(self.identifier, params)


@dataclass(frozen=True)
class LazyConfigurationValidator:
    identifier: str

    def __call__(
        self,
        *,
        timeframe_unit: str,
        timeframe_unit_number: int,
        fast_period: int,
        slow_period: int,
    ) -> None:
        service = _bot_service_module()
        effective_fast, effective_slow = service._normalized_strategy_period_values(
            self.identifier,
            fast_period=fast_period,
            slow_period=slow_period,
        )
        service._validate_strategy_configuration(
            strategy_type=self.identifier,
            timeframe_unit=timeframe_unit,
            timeframe_unit_number=timeframe_unit_number,
            fast_period=effective_fast,
            slow_period=effective_slow,
        )


@dataclass(frozen=True)
class LazyEvaluator:
    callable_name: str

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        evaluator = getattr(_bot_service_module(), self.callable_name)
        return evaluator(*args, **kwargs)


@dataclass(frozen=True)
class MinimumHistoryResolver:
    identifier: str

    def __call__(
        self,
        *,
        strategy_params: Any = None,
        fast_period: int = 9,
        slow_period: int = 21,
        timeframe_unit: str = "minute",
        timeframe_unit_number: int = 5,
    ) -> tuple[HistoryRequirement, ...]:
        params = LazyParameterNormalizer(self.identifier)(strategy_params)
        service = _bot_service_module()
        effective_fast, effective_slow = service._normalized_strategy_period_values(
            self.identifier,
            fast_period=fast_period,
            slow_period=slow_period,
        )
        return _resolve_minimum_history(
            self.identifier,
            params=params,
            fast_period=effective_fast,
            slow_period=effective_slow,
            timeframe_unit=timeframe_unit,
            timeframe_unit_number=timeframe_unit_number,
        )


@dataclass(frozen=True)
class StrategyDefinition:
    identifier: str
    parameter_normalizer: Callable[[Any], dict[str, Any]]
    configuration_validator: Callable[..., None]
    required_timeframes: tuple[TimeframeRequirement, ...]
    minimum_history: Callable[..., tuple[HistoryRequirement, ...]]
    evaluator: Callable[..., Any]
    auxiliary_data_requirements: tuple[str, ...]
    backtesting_supported: bool = False


def _configured(
    role: str = "signal",
    *,
    aligned_to: str | None = None,
    notes: str | None = None,
) -> TimeframeRequirement:
    return TimeframeRequirement(role=role, source="configured", aligned_to=aligned_to, notes=notes)


def _fixed(
    role: str,
    unit: str,
    unit_number: int,
    *,
    aligned_to: str | None = None,
    notes: str | None = None,
) -> TimeframeRequirement:
    return TimeframeRequirement(
        role=role,
        source="fixed",
        unit=unit,
        unit_number=unit_number,
        aligned_to=aligned_to,
        notes=notes,
    )


def _derived(
    role: str,
    *,
    aligned_to: str,
    derivation: str,
    notes: str | None = None,
) -> TimeframeRequirement:
    return TimeframeRequirement(
        role=role,
        source="derived",
        aligned_to=aligned_to,
        derivation=derivation,
        notes=notes,
    )


def _definition(
    identifier: str,
    evaluator_name: str,
    required_timeframes: tuple[TimeframeRequirement, ...],
    auxiliary_data_requirements: tuple[str, ...] = (),
) -> StrategyDefinition:
    return StrategyDefinition(
        identifier=identifier,
        parameter_normalizer=LazyParameterNormalizer(identifier),
        configuration_validator=LazyConfigurationValidator(identifier),
        required_timeframes=required_timeframes,
        minimum_history=MinimumHistoryResolver(identifier),
        evaluator=LazyEvaluator(evaluator_name),
        auxiliary_data_requirements=auxiliary_data_requirements,
        backtesting_supported=identifier in BACKTEST_SUPPORTED_STRATEGY_IDENTIFIERS,
    )


_DEFINITIONS = (
    _definition(
        "topbot_adaptive",
        "evaluate_topbot_adaptive",
        (_fixed("signal", "minute", 5, notes="MNQ EMA/VWAP trend pullback."),),
        ("regular_session_vwap",),
    ),
    _definition("sma_cross", "evaluate_sma_cross", (_configured(),)),
    _definition(
        "support_resistance",
        "evaluate_support_resistance_levels",
        (_fixed("higher", "hour", 4), _fixed("signal", "hour", 1)),
        ("higher_timeframe_levels",),
    ),
    _definition(
        "liquidity_sweep_retest",
        "evaluate_liquidity_sweep_retest",
        (_fixed("higher", "hour", 4), _fixed("signal", "hour", 1)),
        ("higher_timeframe_bias", "liquidity_levels"),
    ),
    _definition(
        "donchian_breakout",
        "evaluate_donchian_breakout",
        (_configured(),),
        ("open_position_state", "latest_entry_plan", "base_order_size"),
    ),
    _definition(
        "opening_rvol_breakout",
        "evaluate_opening_rvol_breakout",
        (_fixed("signal", "minute", 5, notes="Regular-session opening candle."),),
        ("historical_session_opening_volume", "configured_session_start"),
    ),
    _definition(
        "bollinger_rsi_reversal",
        "evaluate_bollinger_rsi_reversal",
        (_configured(),),
        ("session_vwap",),
    ),
    _definition(
        "bollinger_mean_reversion",
        "evaluate_bollinger_mean_reversion",
        (_configured(),),
        ("session_vwap", "news_blackout_windows"),
    ),
    _definition(
        "macd_support_resistance",
        "evaluate_macd_support_resistance",
        (_fixed("higher", "hour", 4), _fixed("signal", "hour", 1)),
        ("higher_timeframe_levels",),
    ),
    _definition(
        "delayed_orb_confirmation",
        "evaluate_delayed_orb_confirmation",
        (_fixed("signal", "minute", 1, notes="Current configured trading session only."),),
        ("configured_session_start", "session_loss_history"),
    ),
    _definition(
        "orb_fibonacci_pullback",
        "evaluate_orb_fibonacci_pullback",
        (
            _configured(
                notes="Minute candles; the opening-range duration must be divisible by the configured bucket."
            ),
        ),
        ("configured_session_window",),
    ),
    _definition(
        "supertrend_pivot",
        "evaluate_supertrend_pivot_points",
        (_configured("signal"), _fixed("daily", "day", 1)),
        ("previous_daily_ohlc",),
    ),
    _definition("ema_trend_pullback", "evaluate_ema_trend_pullback", (_configured(),)),
    _definition(
        "ema_scalping",
        "evaluate_ema_scalping",
        (_configured(notes="Configuration is restricted to 3-minute or 5-minute candles."),),
    ),
    _definition(
        "vwap_atr_mean_reversion",
        "evaluate_vwap_atr_mean_reversion",
        (_configured(),),
        ("same_session_vwap",),
    ),
    _definition(
        "vwap_gap_retrace",
        "evaluate_vwap_gap_retrace",
        (
            _fixed("prior_session", "minute", 1, notes="Prior regular-session close."),
            _fixed("signal", "minute", 1, notes="Current regular-session entry window."),
        ),
        ("prior_regular_session_close", "regular_session_vwap"),
    ),
    _definition(
        "fisher_transform_mean_reversion",
        "evaluate_fisher_transform_mean_reversion",
        (_configured(),),
        ("same_session_vwap",),
    ),
    _definition(
        "atr_adjusted_relative_strength",
        "evaluate_atr_adjusted_relative_strength",
        (_configured("signal"), _configured("benchmark", aligned_to="signal")),
        ("benchmark_contract", "benchmark_candles", "same_session_vwap"),
    ),
    _definition(
        "relative_strength_spy",
        "evaluate_relative_strength_vs_spy",
        (
            _fixed("signal", "minute", 5),
            _fixed("benchmark", "minute", 5, aligned_to="signal"),
        ),
        ("benchmark_contract", "benchmark_candles", "same_session_vwap", "support_resistance_levels"),
    ),
    _definition("pullback_trap_reversal", "evaluate_pullback_trap_reversal", (_configured(),)),
    _definition(
        "fvg_sweep_mss",
        "evaluate_fvg_sweep_mss",
        (
            _configured("fvg"),
            _derived(
                "structure",
                aligned_to="fvg",
                derivation="_derive_lower_timeframe",
                notes="Derived from the configured FVG timeframe.",
            ),
        ),
        ("derived_structure_candles",),
    ),
)


def _build_registry(definitions: tuple[StrategyDefinition, ...]) -> Mapping[str, StrategyDefinition]:
    registry: dict[str, StrategyDefinition] = {}
    for definition in definitions:
        if definition.identifier in registry:
            raise RuntimeError(f"duplicate strategy registration: {definition.identifier}")
        if not definition.required_timeframes:
            raise RuntimeError(f"strategy has no timeframe requirements: {definition.identifier}")
        registry[definition.identifier] = definition
    return MappingProxyType(registry)


STRATEGY_REGISTRY = _build_registry(_DEFINITIONS)
SUPPORTED_STRATEGY_IDENTIFIERS = frozenset(STRATEGY_REGISTRY)


def get_strategy_definition(identifier: Any) -> StrategyDefinition:
    normalized = str(identifier or "sma_cross").strip()
    try:
        return STRATEGY_REGISTRY[normalized]
    except KeyError as exc:
        raise ValueError("unsupported bot strategy type") from exc


def normalize_strategy_parameters(identifier: Any, params: Any = None) -> dict[str, Any]:
    return get_strategy_definition(identifier).parameter_normalizer(params)


def validate_strategy_configuration(
    identifier: Any,
    *,
    timeframe_unit: str,
    timeframe_unit_number: int,
    fast_period: int,
    slow_period: int,
) -> None:
    get_strategy_definition(identifier).configuration_validator(
        timeframe_unit=timeframe_unit,
        timeframe_unit_number=timeframe_unit_number,
        fast_period=fast_period,
        slow_period=slow_period,
    )


def dispatch_strategy_evaluator(identifier: Any, /, *args: Any, **kwargs: Any) -> Any:
    """Dispatch an already-acquired candle bundle to the registered evaluator."""

    return get_strategy_definition(identifier).evaluator(*args, **kwargs)


def dispatch_strategy(identifier: Any, /, *args: Any, **kwargs: Any) -> Any:
    """Compatibility entrypoint for incrementally replacing legacy dispatch."""

    return dispatch_strategy_evaluator(identifier, *args, **kwargs)


def _history(
    role: str,
    minimum_bars: int | None,
    *,
    hard: int | None = None,
    sessions: int | None = None,
    notes: str | None = None,
) -> HistoryRequirement:
    return HistoryRequirement(
        role=role,
        minimum_bars=minimum_bars,
        hard_minimum_bars=minimum_bars if hard is None else hard,
        minimum_sessions=sessions,
        notes=notes,
    )


def _resolve_minimum_history(
    identifier: str,
    *,
    params: Mapping[str, Any],
    fast_period: int,
    slow_period: int,
    timeframe_unit: str,
    timeframe_unit_number: int,
) -> tuple[HistoryRequirement, ...]:
    if identifier == "topbot_adaptive":
        from .topbot_strategy import HISTORY_BARS
        return (_history("signal", HISTORY_BARS, notes="One 5-minute MNQ stream; fixed EMA warmup and regular-session VWAP."),)

    if identifier == "sma_cross":
        hard = slow_period + 1
        return (_history("signal", max(25, hard), hard=hard),)

    if identifier == "ema_scalping":
        hard = slow_period + 1
        return (_history("signal", max(25, hard), hard=hard),)

    if identifier == "ema_trend_pullback":
        hard = max(
            slow_period,
            int(params["rsi_period"]) + 1,
            int(params["volume_average_period"]) + 1,
            int(params["swing_lookback_bars"]),
        )
        return (_history("signal", max(25, hard), hard=hard),)

    if identifier == "donchian_breakout":
        hard = max(
            int(params["entry_period"]) + 1,
            int(params["exit_period"]) + 1,
            int(params["atr_period"]) + 1,
        )
        return (_history("signal", max(25, hard), hard=hard),)

    if identifier == "bollinger_rsi_reversal":
        hard = max(
            int(params["bollinger_period"]) + 1,
            int(params["rsi_period"]) + 2,
            int(params["adx_period"]) * 2,
            int(params["swing_stop_lookback_bars"]),
        )
        return (_history("signal", max(25, hard), hard=hard),)

    if identifier == "bollinger_mean_reversion":
        hard = max(int(params["bollinger_period"]) + 1, int(params["atr_period"]))
        return (_history("signal", max(25, hard), hard=hard),)

    if identifier == "vwap_atr_mean_reversion":
        hard = max(
            int(params["atr_period"]),
            int(params["rsi_period"]) + 1,
            int(params["adx_period"]) * 2,
            int(params["vwap_slope_bars"]) + 1,
            int(params["local_extreme_lookback"]),
        )
        return (
            _history(
                "signal",
                max(25, hard),
                hard=hard,
                notes=f"At least {int(params['vwap_slope_bars']) + 1} bars must be in the same session.",
            ),
        )

    if identifier == "fisher_transform_mean_reversion":
        hard = max(
            int(params["fisher_length"]) + 2,
            fast_period + 1,
            slow_period + int(params["ema_slope_lookback_bars"]),
            int(params["swing_stop_lookback_bars"]),
        )
        return (_history("signal", max(25, hard), hard=hard),)

    if identifier == "pullback_trap_reversal":
        pullback = int(params["pullback_lookback_bars"])
        hard = max(
            slow_period + int(params["trend_confirmation_bars"]),
            int(params["volume_baseline_bars"]) + pullback + 1,
            int(params["prior_swing_window"]) + pullback + 1,
        )
        return (_history("signal", max(25, hard), hard=hard),)

    if identifier == "atr_adjusted_relative_strength":
        move = int(params["move_lookback_bars"])
        atr = int(params["atr_period"])
        primary_hard = max(
            move + 1,
            atr + 1,
            int(params["relative_volume_period"]) + 1,
            int(params["ema_period"]),
            int(params["stop_structure_window"]),
        )
        benchmark_hard = max(move + 1, atr + 1)
        return (
            _history("signal", max(25, primary_hard), hard=primary_hard),
            _history("benchmark", max(25, benchmark_hard), hard=benchmark_hard, notes="Timestamp-aligned to signal."),
        )

    if identifier == "relative_strength_spy":
        hard = max(
            int(params["comparison_bars"]) + 1,
            int(params["pullback_lookback_bars"]) + 1,
            int(params["relative_volume_period"]) + 1,
            int(params["ema_period"]),
            25,
        )
        conservative = max(
            50,
            hard,
            int(params["major_level_lookback_bars"]),
            int(params["swing_window"]) + 2,
        )
        return (
            _history("signal", conservative, hard=hard),
            _history("benchmark", conservative, hard=hard, notes="Timestamp-aligned to signal."),
        )

    if identifier == "support_resistance":
        hard = int(params["swing_window"])
        conservative = max(hard, int(params["bars_per_timeframe"]))
        return (
            _history("higher", conservative, hard=hard),
            _history("signal", conservative, hard=hard),
        )

    if identifier == "liquidity_sweep_retest":
        bars = int(params["bars_per_timeframe"])
        higher_hard = max(int(params["swing_window"]), slow_period)
        signal_hard = max(
            int(params["swing_window"]),
            int(params["reclaim_within_bars"]) + int(params["retest_within_bars"]) + 2,
        )
        return (
            _history("higher", max(bars, higher_hard), hard=higher_hard),
            _history("signal", max(bars, signal_hard), hard=signal_hard),
        )

    if identifier == "macd_support_resistance":
        bars = int(params["bars_per_timeframe"])
        higher_hard = int(params["swing_window"])
        moving_average_floor = (
            int(params["trailing_ma_period"])
            if str(params["trailing_stop_mode"]) == "moving_average"
            else 0
        )
        signal_hard = max(
            int(params["swing_window"]),
            slow_period + int(params["signal_period"]),
            int(params["atr_period"]) + 1,
            moving_average_floor,
        )
        return (
            _history("higher", max(bars, higher_hard), hard=higher_hard),
            _history("signal", max(bars, signal_hard), hard=signal_hard),
        )

    if identifier == "fvg_sweep_mss":
        structure_hard = max(5, int(params["swing_window"]) + 2)
        structure_conservative = max(25, structure_hard, int(params["volume_lookback_bars"]) + 1)
        return (
            _history("fvg", 25, hard=3),
            _history("structure", structure_conservative, hard=structure_hard),
        )

    if identifier == "opening_rvol_breakout":
        sessions = int(params["relative_volume_lookback_days"]) + 1
        return (
            _history(
                "signal",
                int(params["atr_period"]),
                hard=int(params["atr_period"]),
                sessions=sessions,
                notes=(
                    f"Requires {int(params['relative_volume_lookback_days'])} prior regular-session "
                    "opening candles plus the current opening candle."
                ),
            ),
        )

    if identifier == "delayed_orb_confirmation":
        hard = int(params["opening_range_minutes"]) + int(params["confirmation_minutes"])
        return (
            _history(
                "signal",
                hard + 5,
                hard=hard,
                sessions=1,
                notes="The acquisition path keeps a five-bar session cushion.",
            ),
        )

    if identifier == "orb_fibonacci_pullback":
        bucket = max(1, int(timeframe_unit_number))
        opening_bars = math.ceil(int(params["opening_range_minutes"]) / bucket)
        hard = opening_bars + 2
        conservative = opening_bars + int(params["swing_lookback_bars"]) + 10
        unit_note = "Requires a minute timeframe aligned to the opening range."
        if str(timeframe_unit).strip().lower() != "minute":
            unit_note += " The supplied timeframe is not compatible."
        return (_history("signal", conservative, hard=hard, sessions=1, notes=unit_note),)

    if identifier == "vwap_gap_retrace":
        current_session_bars = int(params["wait_end_minutes"]) + 1
        return (
            _history("prior_session", 1, hard=1, sessions=1, notes="Prior regular-session close."),
            _history(
                "signal",
                current_session_bars,
                hard=max(2, int(params["wait_start_minutes"]) + 1),
                sessions=1,
                notes="Current regular-session one-minute bars through the configured entry window.",
            ),
        )

    if identifier == "supertrend_pivot":
        signal_hard = max(
            int(params["supertrend_period"]) + 2,
            int(params["chop_lookback_bars"]) + 1,
            5,
        )
        signal_conservative = max(
            signal_hard,
            int(params["supertrend_period"]) + int(params["chop_lookback_bars"]) + 10,
        )
        return (
            _history("signal", signal_conservative, hard=signal_hard),
            _history("daily", int(params["daily_bars"]), hard=1),
        )

    raise ValueError(f"minimum-history metadata is missing for strategy: {identifier}")


__all__ = [
    "HistoryRequirement",
    "BACKTEST_SUPPORTED_STRATEGY_IDENTIFIERS",
    "STRATEGY_REGISTRY",
    "SUPPORTED_STRATEGY_IDENTIFIERS",
    "StrategyDefinition",
    "TimeframeRequirement",
    "dispatch_strategy",
    "dispatch_strategy_evaluator",
    "get_strategy_definition",
    "normalize_strategy_parameters",
    "validate_strategy_configuration",
]
