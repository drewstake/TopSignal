from __future__ import annotations

import hashlib
import json
import math
import os
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Callable, Iterable, Mapping

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import BotBacktest, BotConfig, ProjectXMarketCandle
from . import bot_service as bot_service_module
from .bot_candle_acquisition import (
    _SourceConfigView,
    _build_topbot_source_result,
    _generic_evaluator_arguments,
)
from .bot_service import (
    SignalResult,
    evaluate_bollinger_mean_reversion,
    evaluate_bollinger_rsi_reversal,
    evaluate_ema_trend_pullback,
    evaluate_orb_fibonacci_pullback,
    evaluate_pullback_trap_reversal,
    evaluate_sma_cross,
    evaluate_vwap_atr_mean_reversion,
    _session_window_utc_for_reference,
)
from .instruments import load_instrument_specs, normalize_symbol_key
from .projectx_client import ProjectXClient
from .bot_strategy_registry import (
    BACKTEST_SUPPORTED_STRATEGY_IDENTIFIERS,
    get_strategy_definition,
)
from .trading_day import (
    TRADING_TZ,
    futures_session_is_open,
    trading_day_bounds_utc,
    trading_day_date,
)


BACKTEST_ENGINE_VERSION = "1.3.0"
# Retained as a compatibility constant for callers/tests that display the old
# limit. It is deliberately not used to truncate or reject replay history.
MAX_BACKTEST_BARS = 20_000
MAX_PROVIDER_FETCH_BARS = 20_000
MAX_BACKTEST_PROVIDER_REQUESTS = 40
CANDLE_QUERY_CHUNK_SIZE = 8_192
_DEFAULT_BACKTEST_MEMORY_BUDGET_BYTES = 1_536 * 1024 * 1024
try:
    BACKTEST_MEMORY_BUDGET_BYTES = int(
        os.getenv(
            "TOPSIGNAL_BACKTEST_MEMORY_BUDGET_BYTES",
            str(_DEFAULT_BACKTEST_MEMORY_BUDGET_BYTES),
        )
    )
except ValueError:
    BACKTEST_MEMORY_BUDGET_BYTES = _DEFAULT_BACKTEST_MEMORY_BUDGET_BYTES

# Calibrated with tracemalloc and the projected-row benchmark. These estimates
# intentionally include Python container overhead and the two per-bar result
# series, not only raw OHLCV payload bytes.
ESTIMATED_REPLAY_CANDLE_BYTES = 640
ESTIMATED_EXECUTION_RESULT_BYTES = 704
MIN_EXECUTION_BARS = 2

# These strategies have exact replay adapters for their required stored streams
# and exit semantics that match the current live/dry-run bracket payloads.
# Remaining strategies are rejected instead of being approximated.
SUPPORTED_BACKTEST_STRATEGIES = BACKTEST_SUPPORTED_STRATEGY_IDENTIFIERS

UNSUPPORTED_BACKTEST_STRATEGY_REASONS: dict[str, str] = {
    "support_resistance": "requires synchronized closed 4-hour and 1-hour candle streams",
    "liquidity_sweep_retest": "requires synchronized closed 4-hour and 1-hour candle streams",
    "macd_support_resistance": "requires synchronized higher timeframes and exact trailing-stop replay",
    "opening_rvol_breakout": "requires its fixed 5-minute multi-session dataset",
    "delayed_orb_confirmation": "requires its fixed 1-minute stream and per-session loss-stop state",
    "supertrend_pivot": "requires synchronized signal-timeframe and closed daily candles",
    "fvg_sweep_mss": "requires synchronized FVG and lower-timeframe structure streams",
    "atr_adjusted_relative_strength": "requires an exactly aligned benchmark candle stream",
    "relative_strength_spy": "requires an exactly aligned MES benchmark candle stream",
    "vwap_gap_retrace": "requires fixed 1-minute data and alternative exit-path replay",
    "donchian_breakout": "requires stateful channel, trailing-stop, sizing, and entry-plan replay",
    "ema_scalping": "requires exact strong-opposite-candle exit replay",
    "fisher_transform_mean_reversion": "requires exact Fisher-neutral exit replay",
}

_BRACKET_REQUIRED_STRATEGIES = SUPPORTED_BACKTEST_STRATEGIES - {"sma_cross"}
_TOPBOT_STRATEGY = "topbot_adaptive"
_TOPBOT_SHARED_CONFIGURED_STRATEGIES = {
    "sma_cross",
    "ema_scalping",
    "ema_trend_pullback",
    "bollinger_rsi_reversal",
    "bollinger_mean_reversion",
    "vwap_atr_mean_reversion",
    "fisher_transform_mean_reversion",
    "pullback_trap_reversal",
}
_TOPBOT_LEVEL_STRATEGIES = {
    "support_resistance",
    "liquidity_sweep_retest",
    "macd_support_resistance",
}
_TRADING_DAY_VWAP_STRATEGIES = {
    "vwap_atr_mean_reversion",
    "bollinger_mean_reversion",
    "bollinger_rsi_reversal",
}
_TOPBOT_SESSION_VWAP_STRATEGIES = _TRADING_DAY_VWAP_STRATEGIES | {
    "fisher_transform_mean_reversion"
}
_UNIT_SECONDS = {
    "second": 1,
    "minute": 60,
    "hour": 60 * 60,
    "day": 24 * 60 * 60,
    "week": 7 * 24 * 60 * 60,
}


class BacktestError(ValueError):
    """Base error for deterministic, user-correctable backtest failures."""


class UnsupportedBacktestStrategyError(BacktestError):
    pass


class InsufficientBacktestDataError(BacktestError):
    pass


class MalformedBacktestDataError(BacktestError):
    pass


class BacktestConfigurationError(BacktestError):
    pass


@dataclass(frozen=True)
class BacktestSettings:
    start: datetime
    end: datetime
    starting_balance: float
    commission_per_contract: float
    slippage_ticks: float
    tick_size: float
    tick_value: float
    force_close_at_end: bool = True


@dataclass(frozen=True)
class _ResolvedBacktestWindow:
    """One captured request window used by preparation, replay, and persistence."""

    requested_start: datetime
    requested_end: datetime
    start: datetime
    end: datetime
    full_history: bool


@dataclass
class _ProviderRequestBudget:
    limit: int
    used: int = 0

    def claim(self) -> None:
        if self.used >= max(1, int(self.limit)):
            raise BacktestConfigurationError(
                "backtest_market_data_request_limit_exceeded: complete history could not be "
                f"prepared within {self.limit} provider requests; no partial backtest was saved"
            )
        self.used += 1


@dataclass(frozen=True)
class _TopBotReplayStreamSpec:
    key: str
    unit: str
    unit_number: int
    warmup_bars: int
    contract_id: str | None = None
    symbol: str | None = None
    source_strategy: str | None = None


@dataclass(frozen=True)
class _PreparedReplayStream:
    candles: list[ProjectXMarketCandle]
    start_times: list[datetime]
    close_times: list[datetime]


class _ClosedCandleList(list[ProjectXMarketCandle]):
    """Internal proof that a replay window is already closed and ordered."""

    _topsignal_sorted_closed = True


@dataclass(slots=True)
class _ProjectedCandle:
    """Lightweight query result with the candle attribute contract evaluators use."""

    user_id: Any
    contract_id: str
    symbol: str | None
    live: bool
    unit: str
    unit_number: int
    candle_timestamp: datetime
    open_price: Any
    high_price: Any
    low_price: Any
    close_price: Any
    volume: Any
    is_partial: bool
    raw_payload: Any
    fetched_at: datetime | None


@dataclass(frozen=True)
class _PendingSignal:
    action: str
    signal_timestamp: datetime
    signal_price: float | None
    reason: str
    payload: dict[str, Any]


@dataclass
class _OpenTrade:
    side: str
    quantity: float
    signal_timestamp: datetime
    entry_timestamp: datetime
    entry_price: float
    entry_commission: float
    stop_loss: float | None
    take_profit: float | None
    mae: float = 0.0
    mfe: float = 0.0
    bars_held: int = 0


SignalEvaluator = Callable[[list[ProjectXMarketCandle]], SignalResult]


class BacktestEngine:
    """Closed-bar, event-driven simulator with no live-order dependencies."""

    def __init__(
        self,
        *,
        config: BotConfig,
        candles: list[ProjectXMarketCandle],
        settings: BacktestSettings,
        signal_evaluator: SignalEvaluator | None = None,
        replay_streams: Mapping[str, list[ProjectXMarketCandle]] | None = None,
    ) -> None:
        self.config = config
        self.settings = _validate_settings(settings)
        self.strategy_type = str(config.strategy_type)
        _require_supported_strategy(self.strategy_type)
        _validate_replay_configuration(config)
        uses_real_evaluator = signal_evaluator is None
        self._uses_real_evaluator = uses_real_evaluator
        self.signal_evaluator = signal_evaluator or self._evaluate_real_strategy

        self.all_candles, excluded_partial = _validate_and_sort_candles(candles, config=config)
        self.all_start_times = [
            _as_utc(candle.candle_timestamp) for candle in self.all_candles
        ]
        self.all_close_times = [
            _candle_close_time(candle) for candle in self.all_candles
        ]
        self._sma_close_values = (
            [float(candle.close_price) for candle in self.all_candles]
            if uses_real_evaluator
            and self.strategy_type == "sma_cross"
            and evaluate_sma_cross is bot_service_module.evaluate_sma_cross
            else None
        )
        self.topbot_streams: dict[str, _PreparedReplayStream] = {}
        self.topbot_unavailable_sources: dict[str, tuple[str, ...]] = {}
        self._topbot_params: dict[str, Any] = {}
        self._topbot_source_params: dict[str, dict[str, Any]] = {}
        self._topbot_source_keys: dict[str, tuple[str, ...]] = {}
        self._topbot_source_configs: dict[str, _SourceConfigView] = {}
        self._topbot_cursor_state: dict[str, tuple[datetime, int]] = {}
        self._topbot_history_event: datetime | None = None
        self._topbot_history_cache: dict[
            tuple[str, int, datetime | None], list[ProjectXMarketCandle]
        ] = {}
        self._current_event_timestamp: datetime | None = None
        self._current_event_in_session = False
        self._configured_session_start = _parse_time(str(config.trading_start_time))
        self._configured_session_end = _parse_time(str(config.trading_end_time))
        auxiliary_excluded_partial = 0
        deferred_execution_bars = 0
        if uses_real_evaluator and self.strategy_type == _TOPBOT_STRATEGY:
            (
                self.topbot_streams,
                auxiliary_excluded_partial,
            ) = _prepare_topbot_replay_streams(
                config,
                primary_candles=self.all_candles,
                replay_streams=replay_streams,
            )
            self._prepare_topbot_runtime()

        execution_start = bisect_left(self.all_start_times, self.settings.start)
        execution_end = bisect_right(self.all_close_times, self.settings.end)
        self.execution_candles = self.all_candles[execution_start:execution_end]
        self.execution_start_times = self.all_start_times[execution_start:execution_end]
        self.execution_close_times = self.all_close_times[execution_start:execution_end]
        if len(self.execution_candles) < MIN_EXECUTION_BARS:
            raise InsufficientBacktestDataError(
                "insufficient_backtest_data: at least 2 closed execution bars are required "
                f"inside the requested range; found {len(self.execution_candles)}"
            )
        if uses_real_evaluator and self.strategy_type != "orb_fibonacci_pullback":
            hard_minimum = _strategy_history_bars(config, hard_minimum=True)
            first_event = self.execution_close_times[0]
            closed_by_first_event = bisect_right(self.all_close_times, first_event)
            if closed_by_first_event < hard_minimum:
                deferred_execution_bars = hard_minimum - closed_by_first_event
                self.execution_candles = self.execution_candles[deferred_execution_bars:]
                self.execution_start_times = self.execution_start_times[
                    deferred_execution_bars:
                ]
                self.execution_close_times = self.execution_close_times[
                    deferred_execution_bars:
                ]
                if len(self.execution_candles) < MIN_EXECUTION_BARS:
                    available_closed_bars = bisect_right(
                        self.all_close_times,
                        self.settings.end,
                    )
                    raise InsufficientBacktestDataError(
                        "insufficient_backtest_data: insufficient_strategy_warmup: "
                        f"{self.strategy_type} requires at least {hard_minimum} closed bars; "
                        f"only {available_closed_bars} were available"
                    )
        self.execution_in_session = (
            [
                self._event_in_configured_session(timestamp)
                for timestamp in self.execution_start_times
            ]
            if self.strategy_type == _TOPBOT_STRATEGY
            else [True] * len(self.execution_candles)
        )
        if uses_real_evaluator and self.strategy_type == _TOPBOT_STRATEGY:
            in_session_indexes = [
                index
                for index, inside in enumerate(self.execution_in_session)
                if inside
            ]
            first_coverage_index = in_session_indexes[0] if in_session_indexes else 0
            last_coverage_index = (
                in_session_indexes[-1]
                if in_session_indexes
                else len(self.execution_candles) - 1
            )
            self.topbot_unavailable_sources = _topbot_unavailable_sources(
                config,
                streams=self.topbot_streams,
                first_event=self.execution_close_times[first_coverage_index],
                last_event=self.execution_close_times[last_coverage_index],
            )
        self.evaluator_history_limit = max(
            int(config.lookback_bars),
            _strategy_history_bars(config, hard_minimum=False),
        )
        self.max_evaluator_input_bars = _max_evaluator_input_bars(
            config,
            rolling_limit=self.evaluator_history_limit,
        )
        replay_row_count = len(self.all_candles)
        if self.topbot_streams:
            primary_stream_key = _topbot_asset_stream_key(
                str(config.timeframe_unit),
                int(config.timeframe_unit_number),
            )
            replay_row_count += sum(
                len(stream.candles)
                for key, stream in self.topbot_streams.items()
                if key != primary_stream_key
            )
        _enforce_backtest_resource_budget(
            replay_rows=replay_row_count,
            execution_rows=len(self.execution_candles),
        )

        self.warnings: list[str] = []
        if excluded_partial:
            self.warnings.append(f"Excluded {excluded_partial} partial candle(s); only closed bars were replayed.")
        if auxiliary_excluded_partial:
            self.warnings.append(
                f"Excluded {auxiliary_excluded_partial} partial auxiliary candle(s); only closed bars were replayed."
            )
        if deferred_execution_bars:
            self.warnings.append(
                f"Deferred replay by {deferred_execution_bars} candle(s) so the strategy had "
                "its required closed-bar warmup before the first evaluation."
            )
        if self.topbot_unavailable_sources:
            excluded = "; ".join(
                f"{source} ({', '.join(keys)})"
                for source, keys in sorted(self.topbot_unavailable_sources.items())
            )
            self.warnings.append(
                "TopBot excluded source(s) whose required stored replay data was unavailable: "
                + excluded
                + ". Vote thresholds were unchanged."
            )
        self._add_data_quality_warnings()

        self.cash = float(self.settings.starting_balance)
        self.position: _OpenTrade | None = None
        self.pending: _PendingSignal | None = None
        self.trades: list[dict[str, Any]] = []
        self.equity_curve: list[dict[str, Any]] = [
            {
                "timestamp": self.settings.start.isoformat(),
                "equity": _clean(self.settings.starting_balance),
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
            }
        ]
        self.exposed_bar_count = 0
        self._current_bar_exposed = False
        self.daily_entry_counts: dict[Any, int] = defaultdict(int)
        self.daily_net_activity: dict[Any, float] = defaultdict(float)
        self.last_loss_at: datetime | None = None
        self.block_counts: dict[str, int] = defaultdict(int)
        self.unfilled_final_signals = 0
        self._emitted_topbot_signal_identities: set[tuple[str, datetime]] = set()
        self._topbot_source_cache: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
        self.topbot_source_failure_counts: dict[tuple[str, str], int] = defaultdict(int)

    def run(self) -> dict[str, Any]:
        closed_history: list[ProjectXMarketCandle] = (
            _ClosedCandleList() if self._uses_real_evaluator else []
        )
        history_cursor = 0
        timeline = zip(
            self.execution_candles,
            self.execution_start_times,
            self.execution_close_times,
            self.execution_in_session,
        )
        for index, (candle, candle_start, event_time, inside_session) in enumerate(
            timeline
        ):
            self._current_bar_exposed = False
            self._current_event_timestamp = candle_start
            self._current_event_in_session = inside_session

            counted_position = self.position
            if counted_position is not None:
                self._current_bar_exposed = True
                counted_position.bars_held += 1
                self._process_open_gap(candle)

            if self.pending is not None:
                pending = self.pending
                self.pending = None
                self._fill_pending_signal(pending, candle=candle)

            if self.position is not None:
                self._current_bar_exposed = True
                if self.position is not counted_position:
                    self.position.bars_held += 1
                self._process_intrabar_bracket(candle)

            while (
                history_cursor < len(self.all_candles)
                and self.all_close_times[history_cursor] <= event_time
            ):
                closed_history.append(self.all_candles[history_cursor])
                history_cursor += 1
            if self.strategy_type == _TOPBOT_STRATEGY and not inside_session:
                self._record_equity(event_time=event_time, mark_price=float(candle.close_price))
                continue
            if self._sma_close_values is not None:
                signal = self._evaluate_prepared_sma(history_cursor)
            else:
                signal = self.signal_evaluator(self._evaluator_input(closed_history))
            if self.strategy_type == _TOPBOT_STRATEGY:
                self._record_topbot_source_failures(signal)
            if signal.action in {"BUY", "SELL"}:
                source_signal_timestamp = (
                    _as_utc(signal.candle_timestamp)
                    if self.strategy_type == _TOPBOT_STRATEGY and signal.candle_timestamp is not None
                    else candle_start
                )
                if self.strategy_type == _TOPBOT_STRATEGY:
                    signal_identity = (str(signal.action), source_signal_timestamp)
                    if signal_identity in self._emitted_topbot_signal_identities:
                        self._record_equity(event_time=event_time, mark_price=float(candle.close_price))
                        continue
                    self._emitted_topbot_signal_identities.add(signal_identity)
                self.pending = _PendingSignal(
                    action=signal.action,
                    signal_timestamp=candle_start,
                    signal_price=float(signal.price) if signal.price is not None else None,
                    reason=signal.reason,
                    payload=dict(signal.raw_payload) if isinstance(signal.raw_payload, dict) else {},
                )

            self._record_equity(event_time=event_time, mark_price=float(candle.close_price))

        if self.pending is not None:
            self.unfilled_final_signals += 1
            self.pending = None

        if self.position is not None and self.settings.force_close_at_end:
            final_candle = self.execution_candles[-1]
            final_time = self.execution_close_times[-1]
            self._update_excursion(float(final_candle.close_price), float(final_candle.close_price))
            self._close_position(
                raw_exit_price=float(final_candle.close_price),
                exit_timestamp=final_time,
                exit_reason="forced_end_of_test",
            )
            self._replace_last_equity(event_time=final_time)
        elif self.position is not None:
            self.warnings.append(
                "A position remained open because force_close_at_end was false; closed-trade metrics exclude it."
            )

        self._append_run_warnings()
        drawdown_series = _build_drawdown_series(self.equity_curve)
        metrics = _build_metrics(
            self.trades,
            equity_curve=self.equity_curve,
            drawdown_series=drawdown_series,
            exposure_percent=(
                self.exposed_bar_count / len(self.execution_candles) * 100.0
            ),
        )
        return {
            "range": {
                "start": self.execution_start_times[0].isoformat(),
                "end": self.execution_close_times[-1].isoformat(),
                "bar_count": len(self.execution_candles),
                "contract_id": str(self.execution_candles[0].contract_id),
                "symbol": self.execution_candles[0].symbol or self.config.symbol,
                "timeframe_unit": str(self.execution_candles[0].unit),
                "timeframe_unit_number": int(self.execution_candles[0].unit_number),
            },
            "config_snapshot": _config_snapshot(self.config),
            "assumptions": _assumptions_snapshot(self.config, self.settings),
            "metrics": metrics,
            "equity_curve": self.equity_curve,
            "drawdown_series": drawdown_series,
            "daily_results": _period_results(self.trades, monthly=False),
            "monthly_results": _period_results(self.trades, monthly=True),
            "trades": self.trades,
            "warnings": self.warnings,
        }

    def _prepare_topbot_runtime(self) -> None:
        self._topbot_params = bot_service_module._normalize_strategy_params(
            _TOPBOT_STRATEGY,
            self.config.strategy_params,
        )
        source_overrides = self._topbot_params.get("source_strategy_params") or {}
        self._topbot_source_static_errors: dict[str, str] = {}
        self._topbot_generic_args: dict[str, dict[str, Any]] = {}
        self._topbot_primary_limits: dict[str, int] = {}
        for source_strategy in self._topbot_params["source_strategies"]:
            source_params = bot_service_module._normalize_strategy_params(
                source_strategy,
                source_overrides.get(source_strategy, {}),
            )
            self._topbot_source_params[source_strategy] = source_params
            self._topbot_source_keys[source_strategy] = tuple(
                _topbot_source_stream_keys(
                    self.config,
                    source_strategy,
                    source_params=source_params,
                )
            )
            fast_period, slow_period = (
                bot_service_module._normalized_strategy_period_values(
                    source_strategy,
                    fast_period=int(self.config.fast_period),
                    slow_period=int(self.config.slow_period),
                )
            )
            source_config = _SourceConfigView(
                self.config,
                strategy_type=source_strategy,
                strategy_params=source_params,
                fast_period=fast_period,
                slow_period=slow_period,
            )
            self._topbot_source_configs[source_strategy] = source_config
            self._topbot_primary_limits[source_strategy] = (
                _topbot_primary_source_history_limit(
                    self.config,
                    source_strategy,
                )
            )
            if source_strategy in _TOPBOT_SHARED_CONFIGURED_STRATEGIES:
                self._topbot_generic_args[source_strategy] = (
                    _generic_evaluator_arguments(
                        source_strategy,
                        config=source_config,
                        strategy_params=source_params,
                    )
                )
            try:
                bot_service_module._validate_strategy_configuration(
                    strategy_type=source_strategy,
                    timeframe_unit=str(source_config.timeframe_unit),
                    timeframe_unit_number=int(source_config.timeframe_unit_number),
                    fast_period=fast_period,
                    slow_period=slow_period,
                )
            except Exception as exc:
                self._topbot_source_static_errors[source_strategy] = str(exc)

    def _event_in_configured_session(self, event_timestamp: datetime) -> bool:
        timestamp = _as_utc(event_timestamp)
        if self._current_event_timestamp == timestamp:
            return self._current_event_in_session
        if not futures_session_is_open(timestamp):
            return False
        local_time = timestamp.astimezone(TRADING_TZ).time().replace(tzinfo=None)
        start = self._configured_session_start
        end = self._configured_session_end
        if start <= end:
            return start <= local_time <= end
        return local_time >= start or local_time <= end

    def _evaluate_prepared_sma(self, closed_count: int) -> SignalResult:
        closes = self._sma_close_values
        assert closes is not None
        fast_period = int(self.config.fast_period)
        slow_period = int(self.config.slow_period)
        visible_count = min(closed_count, self.evaluator_history_limit)
        latest_index = closed_count - 1
        latest = self.all_candles[latest_index]
        if visible_count < slow_period + 1:
            return SignalResult(
                action="HOLD",
                reason=(
                    f"Need at least {slow_period + 1} closed candles; "
                    f"found {visible_count}."
                ),
                candle_timestamp=self.all_start_times[latest_index],
                price=float(latest.close_price),
                raw_payload={
                    "fast_period": fast_period,
                    "slow_period": slow_period,
                    "closed_count": visible_count,
                },
            )

        previous_fast = bot_service_module._average(
            closes[closed_count - fast_period - 1 : closed_count - 1]
        )
        previous_slow = bot_service_module._average(
            closes[closed_count - slow_period - 1 : closed_count - 1]
        )
        current_fast = bot_service_module._average(
            closes[closed_count - fast_period : closed_count]
        )
        current_slow = bot_service_module._average(
            closes[closed_count - slow_period : closed_count]
        )
        action = "HOLD"
        if previous_fast <= previous_slow and current_fast > current_slow:
            action = "BUY"
        elif previous_fast >= previous_slow and current_fast < current_slow:
            action = "SELL"
        reason = (
            "No SMA crossover on the latest closed candle."
            if action == "HOLD"
            else f"{fast_period}/{slow_period} SMA crossover generated {action}."
        )
        return SignalResult(
            action=action,
            reason=reason,
            candle_timestamp=self.all_start_times[latest_index],
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

    def _record_topbot_source_failures(self, signal: SignalResult) -> None:
        payload = signal.raw_payload if isinstance(signal.raw_payload, dict) else {}
        ensemble = payload.get("ensemble") if isinstance(payload.get("ensemble"), dict) else {}
        failures = ensemble.get("failures") if isinstance(ensemble.get("failures"), list) else []
        for failure in failures:
            if not isinstance(failure, dict):
                continue
            source = str(failure.get("strategy_type") or "unknown")
            error = str(failure.get("error") or "evaluation failed")[:300]
            self.topbot_source_failure_counts[(source, error)] += 1

    def _evaluator_input(
        self,
        closed_history: list[ProjectXMarketCandle],
    ) -> list[ProjectXMarketCandle]:
        if not closed_history:
            return _ClosedCandleList()
        latest_timestamp = _as_utc(closed_history[-1].candle_timestamp)
        rolling_start = max(0, len(closed_history) - self.evaluator_history_limit)

        if self.strategy_type == "orb_fibonacci_pullback":
            session_start, session_end = _session_window_utc_for_reference(
                latest_timestamp,
                start_text=str(self.config.trading_start_time),
                end_text=str(self.config.trading_end_time),
            )
            if latest_timestamp < session_start:
                return _ClosedCandleList()
            if latest_timestamp > session_end:
                return _ClosedCandleList()
            session_start_index = _first_index_at_or_after(closed_history, session_start)
            session_rows = _closed_candle_slice(closed_history, session_start_index)
            _require_complete_session_prefix(
                session_rows,
                expected_start=session_start,
                strategy_type=self.strategy_type,
                enforce=True,
                expected_interval_seconds=None,
            )
            _require_complete_orb_opening_range(
                session_rows,
                config=self.config,
                session_start=session_start,
                latest_timestamp=latest_timestamp,
            )
            return session_rows

        if self.strategy_type in _TRADING_DAY_VWAP_STRATEGIES:
            session_start, _session_end = trading_day_bounds_utc(
                trading_day_date(latest_timestamp)
            )
            session_start_index = _first_index_at_or_after(closed_history, session_start)
            session_rows = _closed_candle_slice(closed_history, session_start_index)
            _require_complete_session_prefix(
                session_rows,
                expected_start=session_start,
                strategy_type=self.strategy_type,
                enforce=_is_intraday_timeframe(self.config),
                expected_interval_seconds=_timeframe_seconds(
                    str(self.config.timeframe_unit),
                    int(self.config.timeframe_unit_number),
                ),
            )
            return _closed_candle_slice(
                closed_history,
                min(rolling_start, session_start_index),
            )

        return _closed_candle_slice(closed_history, rolling_start)

    def _evaluate_real_strategy(self, candles: list[ProjectXMarketCandle]) -> SignalResult:
        params = self.config.strategy_params
        if self.strategy_type == _TOPBOT_STRATEGY:
            return self._evaluate_topbot_adaptive(candles)
        if self.strategy_type == "sma_cross":
            return evaluate_sma_cross(
                candles,
                fast_period=int(self.config.fast_period),
                slow_period=int(self.config.slow_period),
            )
        if self.strategy_type == "ema_trend_pullback":
            return evaluate_ema_trend_pullback(
                candles,
                fast_period=int(self.config.fast_period),
                slow_period=int(self.config.slow_period),
                strategy_params=params,
            )
        if self.strategy_type == "pullback_trap_reversal":
            return evaluate_pullback_trap_reversal(
                candles,
                fast_period=int(self.config.fast_period),
                slow_period=int(self.config.slow_period),
                strategy_params=params,
            )
        if self.strategy_type == "bollinger_mean_reversion":
            return evaluate_bollinger_mean_reversion(candles, strategy_params=params)
        if self.strategy_type == "bollinger_rsi_reversal":
            return evaluate_bollinger_rsi_reversal(candles, strategy_params=params)
        if self.strategy_type == "vwap_atr_mean_reversion":
            return evaluate_vwap_atr_mean_reversion(candles, strategy_params=params)
        if self.strategy_type == "orb_fibonacci_pullback":
            return evaluate_orb_fibonacci_pullback(
                candles,
                timeframe_unit=str(self.config.timeframe_unit),
                timeframe_unit_number=int(self.config.timeframe_unit_number),
                strategy_params=params,
                session_start_time=str(self.config.trading_start_time),
                session_end_time=str(self.config.trading_end_time),
            )
        raise UnsupportedBacktestStrategyError(
            f"strategy_not_supported_for_backtesting:{self.strategy_type}"
        )

    def _evaluate_topbot_adaptive(
        self,
        primary_candles: list[ProjectXMarketCandle],
    ) -> SignalResult:
        if not primary_candles:
            return bot_service_module.evaluate_topbot_adaptive(
                [],
                strategy_params=self.config.strategy_params,
            )
        event_time = _candle_close_time(primary_candles[-1])
        topbot_params = self._topbot_params
        source_results: list[dict[str, Any]] = []
        event_timestamp = _as_utc(primary_candles[-1].candle_timestamp)

        for source_strategy in topbot_params["source_strategies"]:
            if source_strategy in self.topbot_unavailable_sources:
                continue
            source_params = self._topbot_source_params[source_strategy]
            signature = self._topbot_source_cache_signature(
                source_strategy,
                source_params=source_params,
                event_time=event_time,
                event_timestamp=event_timestamp,
            )
            cached = self._topbot_source_cache.get(source_strategy)
            if cached is not None and cached[0] == signature:
                source_results.append(cached[1])
                continue
            try:
                result = self._evaluate_topbot_source(
                    source_strategy,
                    source_params=source_params,
                    event_time=event_time,
                    event_timestamp=event_timestamp,
                )
            except Exception as exc:
                result = {
                    "strategy_type": source_strategy,
                    "action": "ERROR",
                    "reason": "Source evaluation failed during synchronized replay.",
                    "error": bot_service_module.sanitize_error(exc, max_length=300),
                    "score": None,
                    "reward_risk": None,
                    "eligible": False,
                }
            self._topbot_source_cache[source_strategy] = (signature, result)
            source_results.append(result)

        return bot_service_module.evaluate_topbot_adaptive(
            source_results,
            strategy_params=topbot_params,
        )

    def _topbot_source_cache_signature(
        self,
        source_strategy: str,
        *,
        source_params: dict[str, Any],
        event_time: datetime,
        event_timestamp: datetime,
    ) -> tuple[Any, ...]:
        signature: list[Any] = []
        for key in self._topbot_source_keys[source_strategy]:
            closed_count = self._topbot_closed_count(key, event_time)
            signature.append((key, closed_count))
        if source_strategy == "donchian_breakout":
            if self.position is None:
                signature.append(("position", "flat"))
            else:
                signature.append(
                    (
                        "position",
                        self.position.side,
                        self.position.quantity,
                        self.position.entry_timestamp,
                        self.position.entry_price,
                        self.position.stop_loss,
                        self.position.take_profit,
                    )
                )
        if source_strategy == "delayed_orb_confirmation":
            # A one-minute stream must advance through every primary event.
            # Including the event prevents a stale final minute from being
            # cached and reused across the rest of a session or a later day.
            signature.append(("primary_event", _as_utc(event_timestamp)))
        return tuple(signature)

    def _evaluate_topbot_source(
        self,
        source_strategy: str,
        *,
        source_params: dict[str, Any],
        event_time: datetime,
        event_timestamp: datetime,
    ) -> dict[str, Any]:
        static_error = self._topbot_source_static_errors.get(source_strategy)
        if static_error is not None:
            raise ValueError(static_error)
        source_config = self._topbot_source_configs[source_strategy]
        fast_period = int(source_config.fast_period)
        slow_period = int(source_config.slow_period)

        primary_key = _topbot_asset_stream_key(
            str(self.config.timeframe_unit),
            int(self.config.timeframe_unit_number),
        )
        primary_limit = self._topbot_primary_limits[source_strategy]

        if source_strategy in _TOPBOT_SHARED_CONFIGURED_STRATEGIES:
            source_candles = self._topbot_stream_history(
                primary_key,
                event_time=event_time,
                limit=primary_limit,
            )
            if source_strategy in _TOPBOT_SESSION_VWAP_STRATEGIES and source_candles:
                trading_day_start, _trading_day_end = trading_day_bounds_utc(
                    trading_day_date(_as_utc(source_candles[-1].candle_timestamp))
                )
                session_index = _first_index_at_or_after(source_candles, trading_day_start)
                _require_complete_session_prefix(
                    source_candles[session_index:],
                    expected_start=trading_day_start,
                    strategy_type=f"topbot:{source_strategy}",
                    enforce=_is_intraday_timeframe(self.config),
                    expected_interval_seconds=_timeframe_seconds(
                        str(self.config.timeframe_unit),
                        int(self.config.timeframe_unit_number),
                    ),
                )
            signal = bot_service_module.dispatch_strategy_evaluator(
                source_strategy,
                source_candles,
                **self._topbot_generic_args[source_strategy],
            )
        elif source_strategy == "donchian_breakout":
            source_candles = self._topbot_stream_history(
                primary_key,
                event_time=event_time,
                limit=primary_limit,
            )
            if self.position is None:
                position_state = bot_service_module.OpenPositionState(
                    net_qty=0.0,
                    avg_entry_price=None,
                    opened_at=None,
                )
                latest_entry_plan = None
            else:
                position_state = bot_service_module.OpenPositionState(
                    net_qty=(
                        self.position.quantity
                        if self.position.side == "long"
                        else -self.position.quantity
                    ),
                    avg_entry_price=self.position.entry_price,
                    opened_at=self.position.entry_timestamp,
                )
                latest_entry_plan = {
                    "stop_loss": self.position.stop_loss,
                    "take_profit": self.position.take_profit,
                }
            signal = bot_service_module.dispatch_strategy_evaluator(
                source_strategy,
                source_candles,
                strategy_params=source_params,
                position_state=position_state,
                latest_entry_plan=latest_entry_plan,
                base_order_size=float(self.config.order_size),
            )
        elif source_strategy == "delayed_orb_confirmation":
            session_start, session_end = _session_window_utc_for_reference(
                event_timestamp,
                start_text=str(self.config.trading_start_time),
                end_text=str(self.config.trading_end_time),
            )
            if not self._event_in_configured_session(event_timestamp):
                source_candles = []
                signal = _topbot_outside_session_signal(
                    source_strategy,
                    event_timestamp=event_timestamp,
                )
            else:
                source_candles = self._topbot_stream_history(
                    _topbot_asset_stream_key("minute", 1),
                    event_time=event_time,
                    limit=_topbot_configured_session_capacity(
                        self.config,
                        unit="minute",
                        unit_number=1,
                    ),
                    not_before=session_start,
                )
                _require_complete_session_prefix(
                    source_candles,
                    expected_start=session_start,
                    strategy_type="topbot:delayed_orb_confirmation",
                    enforce=True,
                    expected_interval_seconds=60,
                )
                _require_stream_current_through_event(
                    source_candles,
                    event_time=event_time,
                    interval_seconds=60,
                    strategy_type="topbot:delayed_orb_confirmation",
                )
                signal = bot_service_module.dispatch_strategy_evaluator(
                    source_strategy,
                    candles=source_candles,
                    strategy_params=source_params,
                    session_start_time=str(self.config.trading_start_time),
                )
        elif source_strategy == "orb_fibonacci_pullback":
            session_start, session_end = _session_window_utc_for_reference(
                event_timestamp,
                start_text=str(self.config.trading_start_time),
                end_text=str(self.config.trading_end_time),
            )
            if not self._event_in_configured_session(event_timestamp):
                source_candles = []
                signal = _topbot_outside_session_signal(
                    source_strategy,
                    event_timestamp=event_timestamp,
                )
            else:
                source_candles = self._topbot_stream_history(
                    primary_key,
                    event_time=event_time,
                    limit=_topbot_configured_session_capacity(
                        self.config,
                        unit=str(self.config.timeframe_unit),
                        unit_number=int(self.config.timeframe_unit_number),
                    ),
                    not_before=session_start,
                )
                _require_complete_session_prefix(
                    source_candles,
                    expected_start=session_start,
                    strategy_type="topbot:orb_fibonacci_pullback",
                    enforce=True,
                    expected_interval_seconds=_timeframe_seconds(
                        str(self.config.timeframe_unit),
                        int(self.config.timeframe_unit_number),
                    ),
                )
                _require_complete_orb_opening_range(
                    source_candles,
                    config=source_config,
                    session_start=session_start,
                    latest_timestamp=_as_utc(source_candles[-1].candle_timestamp),
                )
                signal = bot_service_module.dispatch_strategy_evaluator(
                    source_strategy,
                    source_candles,
                    timeframe_unit=str(self.config.timeframe_unit),
                    timeframe_unit_number=int(self.config.timeframe_unit_number),
                    strategy_params=source_params,
                    session_start_time=str(self.config.trading_start_time),
                    session_end_time=str(self.config.trading_end_time),
                )
        elif source_strategy == "opening_rvol_breakout":
            lookback_days = int(source_params["relative_volume_lookback_days"])
            calendar_lookback_days = max(lookback_days + 14, 21)
            limit = max(
                int(self.config.lookback_bars),
                (calendar_lookback_days + 1) * ((24 * 60) // 5),
                int(source_params["atr_period"]) * 20,
                500,
            )
            source_candles = self._topbot_stream_history(
                _topbot_asset_stream_key("minute", 5),
                event_time=event_time,
                limit=limit,
            )
            signal = bot_service_module.dispatch_strategy_evaluator(
                source_strategy,
                source_candles,
                strategy_params=source_params,
                session_start_time=str(self.config.trading_start_time),
            )
        elif source_strategy == "vwap_gap_retrace":
            source_candles = self._topbot_stream_history(
                _topbot_asset_stream_key("minute", 1),
                event_time=event_time,
                limit=int(source_params["bars_to_fetch"]),
            )
            signal = bot_service_module.dispatch_strategy_evaluator(
                source_strategy,
                source_candles,
                strategy_params=source_params,
            )
        elif source_strategy in _TOPBOT_LEVEL_STRATEGIES:
            bars_per_timeframe = int(source_params["bars_per_timeframe"])
            higher_candles = self._topbot_stream_history(
                _topbot_asset_stream_key("hour", 4),
                event_time=event_time,
                limit=bars_per_timeframe,
            )
            source_candles = self._topbot_stream_history(
                _topbot_asset_stream_key("hour", 1),
                event_time=event_time,
                limit=bars_per_timeframe,
            )
            evaluator_args: dict[str, Any] = {
                "higher_timeframe_candles": higher_candles,
                "lower_timeframe_candles": source_candles,
                "strategy_params": source_params,
            }
            if source_strategy != "support_resistance":
                evaluator_args.update(fast_period=fast_period, slow_period=slow_period)
            signal = bot_service_module.dispatch_strategy_evaluator(
                source_strategy,
                **evaluator_args,
            )
        elif source_strategy == "supertrend_pivot":
            signal_limit = max(
                int(self.config.lookback_bars),
                int(source_params["supertrend_period"])
                + int(source_params["chop_lookback_bars"])
                + 10,
            )
            source_candles = self._topbot_stream_history(
                primary_key,
                event_time=event_time,
                limit=signal_limit,
            )
            daily_candles = self._topbot_stream_history(
                _topbot_asset_stream_key("day", 1),
                event_time=event_time,
                limit=int(source_params["daily_bars"]),
            )
            signal = bot_service_module.dispatch_strategy_evaluator(
                source_strategy,
                signal_timeframe_candles=source_candles,
                daily_candles=daily_candles,
                strategy_params=source_params,
            )
        elif source_strategy == "fvg_sweep_mss":
            fvg_limit = max(25, int(self.config.lookback_bars))
            structure_unit, structure_unit_number = bot_service_module._derive_lower_timeframe(
                base_unit=str(self.config.timeframe_unit),
                base_unit_number=int(self.config.timeframe_unit_number),
            )
            base_seconds = _timeframe_seconds(
                str(self.config.timeframe_unit),
                int(self.config.timeframe_unit_number),
            ) or 1
            structure_seconds = _timeframe_seconds(
                structure_unit,
                structure_unit_number,
            ) or 1
            structure_ratio = max(1, int(round(base_seconds / structure_seconds)))
            structure_limit = min(5000, max(fvg_limit * structure_ratio, fvg_limit + 25))
            fvg_candles = self._topbot_stream_history(
                primary_key,
                event_time=event_time,
                limit=fvg_limit,
            )
            source_candles = self._topbot_stream_history(
                _topbot_asset_stream_key(structure_unit, structure_unit_number),
                event_time=event_time,
                limit=structure_limit,
            )
            signal = bot_service_module.dispatch_strategy_evaluator(
                source_strategy,
                fvg_candles=fvg_candles,
                structure_candles=source_candles,
                strategy_params=source_params,
            )
        elif source_strategy == "atr_adjusted_relative_strength":
            source_candles = self._topbot_stream_history(
                primary_key,
                event_time=event_time,
                limit=primary_limit,
            )
            benchmark_candles = self._topbot_stream_history(
                _topbot_benchmark_stream_key(source_strategy),
                event_time=event_time,
                limit=max(25, int(self.config.lookback_bars)),
            )
            signal = bot_service_module.dispatch_strategy_evaluator(
                source_strategy,
                source_candles,
                benchmark_candles=benchmark_candles,
                strategy_params=source_params,
                session_start_time=str(self.config.trading_start_time),
            )
        elif source_strategy == "relative_strength_spy":
            limit = _relative_strength_spy_history_limit(self.config, source_params)
            source_candles = self._topbot_stream_history(
                _topbot_asset_stream_key("minute", 5),
                event_time=event_time,
                limit=limit,
            )
            benchmark_candles = self._topbot_stream_history(
                _topbot_benchmark_stream_key(source_strategy),
                event_time=event_time,
                limit=limit,
            )
            signal = bot_service_module.dispatch_strategy_evaluator(
                source_strategy,
                asset_candles=source_candles,
                benchmark_candles=benchmark_candles,
                strategy_params=source_params,
            )
        else:
            raise ValueError(f"topbot_replay_adapter_missing:{source_strategy}")

        return _build_topbot_source_result(
            bot_service_module,
            strategy_type=source_strategy,
            config=source_config,
            candles=source_candles,
            signal=signal,
        )

    def _topbot_stream_history(
        self,
        key: str,
        *,
        event_time: datetime,
        limit: int,
        not_before: datetime | None = None,
    ) -> list[ProjectXMarketCandle]:
        stream = self.topbot_streams.get(key)
        if stream is None or not stream.candles:
            raise ValueError(f"missing_stored_replay_stream:{key}")
        event_utc = _as_utc(event_time)
        if self._topbot_history_event != event_utc:
            self._topbot_history_event = event_utc
            self._topbot_history_cache.clear()
        normalized_not_before = _as_utc(not_before) if not_before is not None else None
        cache_key = (key, max(1, int(limit)), normalized_not_before)
        cached = self._topbot_history_cache.get(cache_key)
        if cached is not None:
            return cached

        end_index = self._topbot_closed_count(key, event_utc)
        start_index = max(0, end_index - max(1, int(limit)))
        if normalized_not_before is not None:
            start_index = max(
                start_index,
                bisect_left(stream.start_times, normalized_not_before),
            )
        history = _closed_candle_slice(stream.candles, start_index, end_index)
        self._topbot_history_cache[cache_key] = history
        return history

    def _topbot_closed_count(self, key: str, event_time: datetime) -> int:
        stream = self.topbot_streams.get(key)
        if stream is None:
            return 0
        event_utc = _as_utc(event_time)
        previous_event, count = self._topbot_cursor_state.get(
            key,
            (datetime.min.replace(tzinfo=timezone.utc), 0),
        )
        if event_utc < previous_event:
            count = bisect_right(stream.close_times, event_utc)
        elif event_utc > previous_event:
            while count < len(stream.close_times) and stream.close_times[count] <= event_utc:
                count += 1
        self._topbot_cursor_state[key] = (event_utc, count)
        return count

    def _fill_pending_signal(
        self,
        pending: _PendingSignal,
        *,
        candle: ProjectXMarketCandle,
    ) -> None:
        fill_time = _as_utc(candle.candle_timestamp)
        if not _signal_fill_is_in_same_session(
            pending.signal_timestamp,
            fill_time,
            start_text=str(self.config.trading_start_time),
            end_text=str(self.config.trading_end_time),
        ):
            self.block_counts["stale_session_signal"] += 1
            return
        desired_side = "long" if pending.action == "BUY" else "short"
        signal_category = str(pending.payload.get("signal_category") or "entry")
        is_exit_only = signal_category == "exit"

        if self.position is not None and self.position.side != desired_side:
            self._current_bar_exposed = True
            self._update_excursion(float(candle.open_price), float(candle.open_price))
            exit_reason = str(pending.payload.get("exit_reason") or "position_reversal")
            self._close_position(
                raw_exit_price=float(candle.open_price),
                exit_timestamp=fill_time,
                exit_reason=exit_reason,
            )

        if is_exit_only or self.position is not None:
            return

        if not self._can_enter(fill_time):
            return

        planned_stop, planned_target = _extract_bracket(pending)
        if self.strategy_type in _BRACKET_REQUIRED_STRATEGIES and (
            planned_stop is None or planned_target is None
        ):
            self.block_counts["invalid_signal_plan"] += 1
            return
        if not _bracket_is_valid(
            action=pending.action,
            signal_price=pending.signal_price,
            stop_loss=planned_stop,
            take_profit=planned_target,
        ):
            self.block_counts["invalid_signal_plan"] += 1
            return

        quantity = _entry_quantity(self.config, pending.payload)
        if quantity <= 0 or abs(quantity - round(quantity)) > 1e-9:
            self.block_counts["invalid_quantity"] += 1
            return
        if quantity > float(self.config.max_contracts):
            self.block_counts["max_contracts"] += 1
            return
        if quantity > float(self.config.max_open_position):
            self.block_counts["max_open_position"] += 1
            return

        entry_price = self._slipped_price(float(candle.open_price), action=pending.action)
        stop_loss, take_profit = _anchor_bracket_to_fill(
            action=pending.action,
            signal_price=pending.signal_price,
            entry_price=entry_price,
            planned_stop=planned_stop,
            planned_target=planned_target,
            tick_size=self.settings.tick_size,
        )
        entry_commission = self.settings.commission_per_contract * quantity
        self.cash -= entry_commission
        session_day = trading_day_date(fill_time)
        self.daily_net_activity[session_day] -= entry_commission
        self.daily_entry_counts[session_day] += 1
        self.position = _OpenTrade(
            side=desired_side,
            quantity=quantity,
            signal_timestamp=pending.signal_timestamp,
            entry_timestamp=fill_time,
            entry_price=entry_price,
            entry_commission=entry_commission,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        self._current_bar_exposed = True

    def _can_enter(self, timestamp: datetime) -> bool:
        if not _contract_is_allowed(self.config):
            self.block_counts["contract_not_allowed"] += 1
            return False
        if not _inside_session(
            timestamp,
            start_text=str(self.config.trading_start_time),
            end_text=str(self.config.trading_end_time),
        ):
            self.block_counts["outside_session"] += 1
            return False

        session_day = trading_day_date(timestamp)
        if self.daily_entry_counts[session_day] >= int(self.config.max_trades_per_day):
            self.block_counts["max_trades_per_day"] += 1
            return False
        if self.daily_net_activity[session_day] <= -float(self.config.max_daily_loss):
            self.block_counts["max_daily_loss"] += 1
            return False
        if self.last_loss_at is not None:
            elapsed = (timestamp - self.last_loss_at).total_seconds()
            if elapsed < int(self.config.cooldown_seconds):
                self.block_counts["cooldown_after_loss"] += 1
                return False
        return True

    def _process_open_gap(self, candle: ProjectXMarketCandle) -> None:
        position = self.position
        if position is None:
            return
        raw_open = float(candle.open_price)
        stop = position.stop_loss
        target = position.take_profit
        exit_price: float | None = None
        exit_reason: str | None = None
        if position.side == "long":
            if stop is not None and raw_open <= stop:
                exit_price, exit_reason = raw_open, "stop_loss_gap"
            elif target is not None and raw_open >= target:
                exit_price, exit_reason = target, "take_profit"
        else:
            if stop is not None and raw_open >= stop:
                exit_price, exit_reason = raw_open, "stop_loss_gap"
            elif target is not None and raw_open <= target:
                exit_price, exit_reason = target, "take_profit"
        if exit_price is None:
            return
        self._update_excursion(exit_price, exit_price)
        self._close_position(
            raw_exit_price=exit_price,
            exit_timestamp=_as_utc(candle.candle_timestamp),
            exit_reason=str(exit_reason),
        )

    def _process_intrabar_bracket(self, candle: ProjectXMarketCandle) -> None:
        position = self.position
        if position is None:
            return

        raw_open = float(candle.open_price)
        raw_high = float(candle.high_price)
        raw_low = float(candle.low_price)
        stop = position.stop_loss
        target = position.take_profit
        if position.side == "long":
            stop_touched = stop is not None and raw_low <= stop
            target_touched = target is not None and raw_high >= target
        else:
            stop_touched = stop is not None and raw_high >= stop
            target_touched = target is not None and raw_low <= target
        both_touched = bool(stop_touched and target_touched)
        exit_price = stop if stop_touched else target if target_touched else None
        exit_reason = "stop_loss" if stop_touched else "take_profit" if target_touched else None

        if exit_price is None:
            self._update_excursion(raw_low, raw_high)
            return

        # Intrabar path is unknowable from OHLC. Stop-first is the documented
        # conservative rule; excursion is bounded consistently with that rule.
        if exit_reason == "stop_loss":
            if position.side == "long":
                self._update_excursion(exit_price, raw_open)
            else:
                self._update_excursion(raw_open, exit_price)
        elif position.side == "long":
            self._update_excursion(raw_low, exit_price)
        else:
            self._update_excursion(exit_price, raw_high)
        self._close_position(
            raw_exit_price=exit_price,
            exit_timestamp=_as_utc(candle.candle_timestamp),
            exit_reason="stop_loss_same_bar_conservative" if both_touched else str(exit_reason),
        )

    def _update_excursion(self, raw_low: float, raw_high: float) -> None:
        position = self.position
        if position is None:
            return
        if position.side == "long":
            favorable = _price_pnl(
                side="long",
                entry=position.entry_price,
                exit=raw_high,
                quantity=position.quantity,
                tick_size=self.settings.tick_size,
                tick_value=self.settings.tick_value,
            )
            adverse = _price_pnl(
                side="long",
                entry=position.entry_price,
                exit=raw_low,
                quantity=position.quantity,
                tick_size=self.settings.tick_size,
                tick_value=self.settings.tick_value,
            )
        else:
            favorable = _price_pnl(
                side="short",
                entry=position.entry_price,
                exit=raw_low,
                quantity=position.quantity,
                tick_size=self.settings.tick_size,
                tick_value=self.settings.tick_value,
            )
            adverse = _price_pnl(
                side="short",
                entry=position.entry_price,
                exit=raw_high,
                quantity=position.quantity,
                tick_size=self.settings.tick_size,
                tick_value=self.settings.tick_value,
            )
        position.mfe = max(position.mfe, favorable, 0.0)
        position.mae = max(position.mae, -adverse, 0.0)

    def _close_position(
        self,
        *,
        raw_exit_price: float,
        exit_timestamp: datetime,
        exit_reason: str,
    ) -> None:
        position = self.position
        if position is None:
            return
        exit_action = "SELL" if position.side == "long" else "BUY"
        exit_price = self._slipped_price(raw_exit_price, action=exit_action)
        gross_pnl = _price_pnl(
            side=position.side,
            entry=position.entry_price,
            exit=exit_price,
            quantity=position.quantity,
            tick_size=self.settings.tick_size,
            tick_value=self.settings.tick_value,
        )
        exit_commission = self.settings.commission_per_contract * position.quantity
        total_commission = position.entry_commission + exit_commission
        net_pnl = gross_pnl - total_commission
        if net_pnl < 0:
            self.last_loss_at = _as_utc(exit_timestamp)
        self.cash += gross_pnl - exit_commission
        self.daily_net_activity[trading_day_date(exit_timestamp)] += gross_pnl - exit_commission
        self.trades.append(
            {
                "id": len(self.trades) + 1,
                "side": position.side,
                "quantity": _clean(position.quantity),
                "signal_timestamp": position.signal_timestamp.isoformat(),
                "entry_timestamp": position.entry_timestamp.isoformat(),
                "entry_price": _clean(position.entry_price),
                "exit_timestamp": _as_utc(exit_timestamp).isoformat(),
                "exit_price": _clean(exit_price),
                "exit_reason": exit_reason,
                "gross_pnl": _clean(gross_pnl),
                "commission": _clean(total_commission),
                "net_pnl": _clean(net_pnl),
                "mae": _clean(position.mae),
                "mfe": _clean(position.mfe),
                "bars_held": position.bars_held,
            }
        )
        self.position = None

    def _slipped_price(self, raw_price: float, *, action: str) -> float:
        raw = Decimal(str(raw_price))
        slip = Decimal(str(self.settings.slippage_ticks)) * Decimal(
            str(self.settings.tick_size)
        )
        return float(raw + slip if action == "BUY" else raw - slip)

    def _record_equity(self, *, event_time: datetime, mark_price: float) -> None:
        if self._current_bar_exposed:
            self.exposed_bar_count += 1
        unrealized = 0.0
        if self.position is not None:
            unrealized = _price_pnl(
                side=self.position.side,
                entry=self.position.entry_price,
                exit=mark_price,
                quantity=self.position.quantity,
                tick_size=self.settings.tick_size,
                tick_value=self.settings.tick_value,
            )
        realized = self.cash - self.settings.starting_balance
        self.equity_curve.append(
            {
                "timestamp": _as_utc(event_time).isoformat(),
                "equity": _clean(self.cash + unrealized),
                "realized_pnl": _clean(realized),
                "unrealized_pnl": _clean(unrealized),
            }
        )

    def _replace_last_equity(self, *, event_time: datetime) -> None:
        point = {
            "timestamp": _as_utc(event_time).isoformat(),
            "equity": _clean(self.cash),
            "realized_pnl": _clean(self.cash - self.settings.starting_balance),
            "unrealized_pnl": 0.0,
        }
        if self.equity_curve:
            self.equity_curve[-1] = point
        else:
            self.equity_curve.append(point)

    def _add_data_quality_warnings(self) -> None:
        warmup_count = min(
            bisect_left(self.all_start_times, self.settings.start),
            bisect_right(self.all_close_times, self.execution_close_times[0]),
        )
        requested_warmup = max(
            int(self.config.lookback_bars),
            _strategy_history_bars(self.config, hard_minimum=False),
        )
        if self.strategy_type == _TOPBOT_STRATEGY:
            primary_key = _topbot_asset_stream_key(
                str(self.config.timeframe_unit),
                int(self.config.timeframe_unit_number),
            )
            requested_warmup = _topbot_stream_specs(self.config)[primary_key].warmup_bars
        if warmup_count < requested_warmup:
            self.warnings.append(
                f"Only {warmup_count} of {requested_warmup} configured warmup bars were available before the test range."
            )
        if len(self.execution_candles) < 100:
            self.warnings.append(
                f"Small bar sample ({len(self.execution_candles)} bars); performance estimates may be unstable."
            )
        zero_volume = sum(1 for row in self.execution_candles if float(row.volume or 0) == 0)
        if zero_volume:
            self.warnings.append(f"{zero_volume} execution candle(s) have zero volume.")
        expected_seconds = _timeframe_seconds(
            str(self.config.timeframe_unit), int(self.config.timeframe_unit_number)
        )
        if expected_seconds is not None:
            actual_start = self.execution_start_times[0]
            actual_end = self.execution_close_times[-1]
            if _has_open_futures_interval(
                self.settings.start,
                actual_start,
                interval_seconds=expected_seconds,
            ):
                self.warnings.append(
                    "Stored candles began after the requested start; replay coverage begins at "
                    f"{actual_start.isoformat()}."
                )
            if _has_open_futures_interval(
                actual_end,
                self.settings.end,
                interval_seconds=expected_seconds,
            ):
                self.warnings.append(
                    "Stored candles ended before the requested end; replay coverage ends at "
                    f"{actual_end.isoformat()}. The configured futures delivery may have expired or rolled."
                )
            gaps = _count_futures_session_gaps(
                self.execution_candles,
                interval_seconds=expected_seconds,
            )
            if gaps:
                self.warnings.append(
                    f"Detected {gaps} candle gap(s); the engine used the next available bar without interpolation."
                )
        if self.strategy_type == _TOPBOT_STRATEGY:
            primary_key = _topbot_asset_stream_key(
                str(self.config.timeframe_unit),
                int(self.config.timeframe_unit_number),
            )
            first_event = self.execution_close_times[0]
            for key, spec in sorted(_topbot_stream_specs(self.config).items()):
                if key == primary_key:
                    continue
                stream = self.topbot_streams.get(key)
                if stream is None or not stream.candles:
                    continue
                auxiliary_warmup = sum(
                    1
                    for row in stream.candles
                    if _as_utc(row.candle_timestamp) < self.settings.start
                    and _candle_close_time(row) <= first_event
                )
                if auxiliary_warmup < spec.warmup_bars:
                    self.warnings.append(
                        f"TopBot replay stream {key} has {auxiliary_warmup} of "
                        f"{spec.warmup_bars} configured warmup bars."
                    )
                expected = _timeframe_seconds(spec.unit, spec.unit_number)
                if expected is None:
                    continue
                execution_rows = [
                    row
                    for row in stream.candles
                    if _as_utc(row.candle_timestamp) >= self.settings.start
                    and _candle_close_time(row) <= self.settings.end
                ]
                gaps = _count_futures_session_gaps(
                    execution_rows,
                    interval_seconds=expected,
                )
                if gaps:
                    self.warnings.append(
                        f"TopBot replay stream {key} has {gaps} candle gap(s); no bars were interpolated."
                    )

    def _append_run_warnings(self) -> None:
        if self.unfilled_final_signals:
            self.warnings.append(
                "The final-bar signal was not filled because no next bar existed in the test range."
            )
        for (source, error), count in sorted(self.topbot_source_failure_counts.items()):
            self.warnings.append(
                f"TopBot source {source} failed on {count} replay event(s): {error}"
            )
        for code in sorted(self.block_counts):
            count = self.block_counts[code]
            label = code.replace("_", " ")
            self.warnings.append(f"Blocked {count} pending signal(s) due to {label}.")
        if not self.trades:
            self.warnings.append("No closed trades were produced in the requested range.")
        elif len(self.trades) < 30:
            self.warnings.append(
                f"Small trade sample ({len(self.trades)} trades); win rate and profit factor may be unreliable."
            )
        if self.trades and not any(float(trade["net_pnl"]) < 0 for trade in self.trades):
            self.warnings.append(
                "Profit factor and payoff ratio are undefined because the sample has no net losing trades."
            )
        elif self.trades and not any(float(trade["net_pnl"]) > 0 for trade in self.trades):
            self.warnings.append(
                "Payoff ratio is undefined because the sample has no net winning trades."
            )


def _topbot_asset_stream_key(unit: str, unit_number: int) -> str:
    return f"asset:{str(unit).strip().lower()}:{int(unit_number)}"


def _topbot_benchmark_stream_key(source_strategy: str) -> str:
    return f"benchmark:{source_strategy}"


def _topbot_primary_source_history_limit(config: BotConfig, source_strategy: str) -> int:
    limit = max(300, int(config.lookback_bars), 25)
    if source_strategy not in _TOPBOT_SESSION_VWAP_STRATEGIES:
        return limit
    timeframe_seconds = _timeframe_seconds(
        str(config.timeframe_unit),
        int(config.timeframe_unit_number),
    )
    if timeframe_seconds is None or timeframe_seconds <= 0:
        return limit
    session_capacity = math.ceil((25 * 60 * 60) / timeframe_seconds) + 2
    return max(limit, session_capacity)


def _topbot_configured_session_capacity(
    config: BotConfig,
    *,
    unit: str,
    unit_number: int,
) -> int:
    timeframe_seconds = _timeframe_seconds(unit, unit_number)
    if timeframe_seconds is None or timeframe_seconds <= 0:
        return max(1, int(config.lookback_bars))
    start = _parse_time(str(config.trading_start_time))
    end = _parse_time(str(config.trading_end_time))
    start_seconds = start.hour * 3600 + start.minute * 60 + start.second
    end_seconds = end.hour * 3600 + end.minute * 60 + end.second
    duration = end_seconds - start_seconds
    if duration < 0:
        duration += 24 * 60 * 60
    return max(1, math.ceil((duration + 60 * 60) / timeframe_seconds) + 2)


def _relative_strength_spy_history_limit(
    config: BotConfig,
    source_params: Mapping[str, Any],
) -> int:
    return max(
        int(config.lookback_bars),
        int(source_params["comparison_bars"]) + 1,
        int(source_params["pullback_lookback_bars"]) + 1,
        int(source_params["relative_volume_period"]) + 1,
        int(source_params["major_level_lookback_bars"]),
        50,
    )


def _matching_benchmark_contract(
    config: BotConfig,
    source_params: Mapping[str, Any],
) -> tuple[str | None, str]:
    explicit_contract = source_params.get("benchmark_contract_id")
    benchmark_symbol = str(source_params.get("benchmark_symbol") or "MES").strip().upper()
    benchmark_root = benchmark_symbol.rsplit(".", 1)[-1]
    canonical_symbol = f"F.US.{benchmark_root}"
    if explicit_contract:
        return str(explicit_contract), canonical_symbol

    parts = str(config.contract_id).strip().split(".")
    if len(parts) == 5 and parts[:3] == ["CON", "F", "US"]:
        return ".".join([*parts[:3], benchmark_root, parts[4]]), canonical_symbol
    return None, canonical_symbol


def _topbot_stream_specs(config: BotConfig) -> dict[str, _TopBotReplayStreamSpec]:
    topbot_params = bot_service_module._normalize_strategy_params(
        _TOPBOT_STRATEGY,
        config.strategy_params,
    )
    source_overrides = topbot_params.get("source_strategy_params") or {}
    specs: dict[str, _TopBotReplayStreamSpec] = {}

    def add_asset(unit: str, unit_number: int, warmup_bars: int) -> None:
        key = _topbot_asset_stream_key(unit, unit_number)
        existing = specs.get(key)
        specs[key] = _TopBotReplayStreamSpec(
            key=key,
            unit=str(unit),
            unit_number=int(unit_number),
            warmup_bars=max(
                int(warmup_bars),
                existing.warmup_bars if existing is not None else 0,
            ),
            contract_id=str(config.contract_id),
            symbol=config.symbol,
        )

    primary_unit = str(config.timeframe_unit)
    primary_unit_number = int(config.timeframe_unit_number)
    add_asset(primary_unit, primary_unit_number, max(300, int(config.lookback_bars), 25))

    for source_strategy in topbot_params["source_strategies"]:
        source_params = bot_service_module._normalize_strategy_params(
            source_strategy,
            source_overrides.get(source_strategy, {}),
        )
        if source_strategy in _TOPBOT_SHARED_CONFIGURED_STRATEGIES or source_strategy in {
            "donchian_breakout",
            "orb_fibonacci_pullback",
            "supertrend_pivot",
            "atr_adjusted_relative_strength",
        }:
            add_asset(
                primary_unit,
                primary_unit_number,
                _topbot_primary_source_history_limit(config, source_strategy),
            )

        if source_strategy in _TOPBOT_LEVEL_STRATEGIES:
            bars = int(source_params["bars_per_timeframe"])
            add_asset("hour", 4, bars)
            add_asset("hour", 1, bars)
        elif source_strategy == "orb_fibonacci_pullback":
            add_asset(
                primary_unit,
                primary_unit_number,
                _topbot_configured_session_capacity(
                    config,
                    unit=primary_unit,
                    unit_number=primary_unit_number,
                ),
            )
        elif source_strategy == "opening_rvol_breakout":
            lookback_days = int(source_params["relative_volume_lookback_days"])
            calendar_lookback_days = max(lookback_days + 14, 21)
            opening_limit = max(
                int(config.lookback_bars),
                (calendar_lookback_days + 1) * ((24 * 60) // 5),
                int(source_params["atr_period"]) * 20,
                500,
            )
            add_asset("minute", 5, opening_limit)
        elif source_strategy == "delayed_orb_confirmation":
            add_asset(
                "minute",
                1,
                _topbot_configured_session_capacity(
                    config,
                    unit="minute",
                    unit_number=1,
                ),
            )
        elif source_strategy == "vwap_gap_retrace":
            add_asset("minute", 1, int(source_params["bars_to_fetch"]))
        elif source_strategy == "supertrend_pivot":
            add_asset("day", 1, int(source_params["daily_bars"]))
        elif source_strategy == "fvg_sweep_mss":
            fvg_limit = max(25, int(config.lookback_bars))
            add_asset(primary_unit, primary_unit_number, fvg_limit)
            structure_unit, structure_unit_number = bot_service_module._derive_lower_timeframe(
                base_unit=primary_unit,
                base_unit_number=primary_unit_number,
            )
            base_seconds = _timeframe_seconds(primary_unit, primary_unit_number) or 1
            structure_seconds = _timeframe_seconds(structure_unit, structure_unit_number) or 1
            ratio = max(1, int(round(base_seconds / structure_seconds)))
            add_asset(
                structure_unit,
                structure_unit_number,
                min(5000, max(fvg_limit * ratio, fvg_limit + 25)),
            )
        elif source_strategy == "atr_adjusted_relative_strength":
            benchmark_contract_id, benchmark_symbol = _matching_benchmark_contract(
                config,
                source_params,
            )
            key = _topbot_benchmark_stream_key(source_strategy)
            specs[key] = _TopBotReplayStreamSpec(
                key=key,
                unit=primary_unit,
                unit_number=primary_unit_number,
                warmup_bars=max(25, int(config.lookback_bars)),
                contract_id=benchmark_contract_id,
                symbol=benchmark_symbol,
                source_strategy=source_strategy,
            )
        elif source_strategy == "relative_strength_spy":
            limit = _relative_strength_spy_history_limit(config, source_params)
            add_asset("minute", 5, limit)
            benchmark_contract_id, benchmark_symbol = _matching_benchmark_contract(
                config,
                source_params,
            )
            key = _topbot_benchmark_stream_key(source_strategy)
            specs[key] = _TopBotReplayStreamSpec(
                key=key,
                unit="minute",
                unit_number=5,
                warmup_bars=limit,
                contract_id=benchmark_contract_id,
                symbol=benchmark_symbol,
                source_strategy=source_strategy,
            )

    return specs


def _topbot_source_stream_keys(
    config: BotConfig,
    source_strategy: str,
    *,
    source_params: Mapping[str, Any],
) -> tuple[str, ...]:
    primary = _topbot_asset_stream_key(
        str(config.timeframe_unit),
        int(config.timeframe_unit_number),
    )
    if source_strategy in _TOPBOT_SHARED_CONFIGURED_STRATEGIES or source_strategy in {
        "donchian_breakout",
        "orb_fibonacci_pullback",
    }:
        return (primary,)
    if source_strategy in _TOPBOT_LEVEL_STRATEGIES:
        return (
            _topbot_asset_stream_key("hour", 4),
            _topbot_asset_stream_key("hour", 1),
        )
    if source_strategy == "opening_rvol_breakout":
        return (_topbot_asset_stream_key("minute", 5),)
    if source_strategy in {"delayed_orb_confirmation", "vwap_gap_retrace"}:
        return (_topbot_asset_stream_key("minute", 1),)
    if source_strategy == "supertrend_pivot":
        return (primary, _topbot_asset_stream_key("day", 1))
    if source_strategy == "fvg_sweep_mss":
        unit, unit_number = bot_service_module._derive_lower_timeframe(
            base_unit=str(config.timeframe_unit),
            base_unit_number=int(config.timeframe_unit_number),
        )
        return (primary, _topbot_asset_stream_key(unit, unit_number))
    if source_strategy == "atr_adjusted_relative_strength":
        return (primary, _topbot_benchmark_stream_key(source_strategy))
    if source_strategy == "relative_strength_spy":
        return (
            _topbot_asset_stream_key("minute", 5),
            _topbot_benchmark_stream_key(source_strategy),
        )
    return (primary,)


def _prepare_topbot_replay_streams(
    config: BotConfig,
    *,
    primary_candles: list[ProjectXMarketCandle],
    replay_streams: Mapping[str, list[ProjectXMarketCandle]] | None,
) -> tuple[dict[str, _PreparedReplayStream], int]:
    provided = dict(replay_streams or {})
    primary_key = _topbot_asset_stream_key(
        str(config.timeframe_unit),
        int(config.timeframe_unit_number),
    )
    provided[primary_key] = primary_candles
    prepared: dict[str, _PreparedReplayStream] = {}
    excluded_partial = 0

    for key, spec in _topbot_stream_specs(config).items():
        rows, excluded = _validate_topbot_replay_stream(
            provided.get(key, []),
            spec=spec,
            config=config,
        )
        excluded_partial += excluded
        prepared[key] = _PreparedReplayStream(
            candles=rows,
            start_times=[_as_utc(row.candle_timestamp) for row in rows],
            close_times=[_candle_close_time(row) for row in rows],
        )
    return prepared, excluded_partial


def _validate_topbot_replay_stream(
    candles: list[ProjectXMarketCandle],
    *,
    spec: _TopBotReplayStreamSpec,
    config: BotConfig,
) -> tuple[list[ProjectXMarketCandle], int]:
    if getattr(candles, "_topsignal_sorted_closed", False):
        closed = _ClosedCandleList(candles)
        excluded_partial = 0
    else:
        closed = _ClosedCandleList(
            row for row in candles if not _cached_candle_is_effectively_partial(row)
        )
        excluded_partial = len(candles) - len(closed)
        closed.sort(key=lambda row: _as_utc(row.candle_timestamp))
    seen: set[datetime] = set()
    previous_close_time: datetime | None = None
    contract_ids: set[str] = set()

    for row in closed:
        timestamp = _as_utc(row.candle_timestamp)
        if timestamp in seen:
            raise MalformedBacktestDataError(
                f"duplicate_topbot_candle_timestamp:{spec.key}:{timestamp.isoformat()}"
            )
        seen.add(timestamp)
        if bool(row.live):
            raise MalformedBacktestDataError(
                f"live_candle_not_allowed:{spec.key}:{timestamp.isoformat()}"
            )
        if config.user_id is not None and str(row.user_id) != str(config.user_id):
            raise MalformedBacktestDataError(
                f"mixed_user_candles:{spec.key}:{timestamp.isoformat()}"
            )
        if str(row.unit) != spec.unit or int(row.unit_number) != spec.unit_number:
            raise MalformedBacktestDataError(f"mixed_timeframe_candles:{spec.key}")
        contract_ids.add(str(row.contract_id))
        if spec.contract_id is not None and str(row.contract_id) != spec.contract_id:
            raise MalformedBacktestDataError(
                f"mixed_contract_candles:{spec.key}:{timestamp.isoformat()}"
            )
        if spec.contract_id is None and spec.symbol is not None:
            if str(row.contract_id) != spec.symbol and str(row.symbol or "") != spec.symbol:
                raise MalformedBacktestDataError(
                    f"mixed_benchmark_candles:{spec.key}:{timestamp.isoformat()}"
                )
        if previous_close_time is not None and timestamp < previous_close_time:
            raise MalformedBacktestDataError(
                f"overlapping_candles:{spec.key}:{timestamp.isoformat()}"
            )
        previous_close_time = _candle_close_time(row)
        values = {
            "open": float(row.open_price),
            "high": float(row.high_price),
            "low": float(row.low_price),
            "close": float(row.close_price),
            "volume": float(row.volume or 0),
        }
        if not all(math.isfinite(value) for value in values.values()):
            raise MalformedBacktestDataError(
                f"non_finite_candle_value:{spec.key}:{timestamp.isoformat()}"
            )
        if min(values["open"], values["high"], values["low"], values["close"]) <= 0:
            raise MalformedBacktestDataError(
                f"non_positive_candle_price:{spec.key}:{timestamp.isoformat()}"
            )
        if values["high"] < max(values["open"], values["close"], values["low"]):
            raise MalformedBacktestDataError(
                f"invalid_candle_high:{spec.key}:{timestamp.isoformat()}"
            )
        if values["low"] > min(values["open"], values["close"], values["high"]):
            raise MalformedBacktestDataError(
                f"invalid_candle_low:{spec.key}:{timestamp.isoformat()}"
            )
        if values["volume"] < 0:
            raise MalformedBacktestDataError(
                f"negative_candle_volume:{spec.key}:{timestamp.isoformat()}"
            )

    if spec.contract_id is None and len(contract_ids) > 1:
        raise BacktestConfigurationError(
            f"ambiguous_benchmark_replay_stream:{spec.key}: configure benchmark_contract_id"
        )
    return closed, excluded_partial


def _topbot_unavailable_sources(
    config: BotConfig,
    *,
    streams: Mapping[str, _PreparedReplayStream],
    first_event: datetime,
    last_event: datetime,
) -> dict[str, tuple[str, ...]]:
    params = bot_service_module._normalize_strategy_params(
        _TOPBOT_STRATEGY,
        config.strategy_params,
    )
    overrides = params.get("source_strategy_params") or {}
    specs = _topbot_stream_specs(config)
    unavailable: dict[str, tuple[str, ...]] = {}
    for source_strategy in params["source_strategies"]:
        source_params = bot_service_module._normalize_strategy_params(
            source_strategy,
            overrides.get(source_strategy, {}),
        )
        missing = tuple(
            key
            for key in _topbot_source_stream_keys(
                config,
                source_strategy,
                source_params=source_params,
            )
            if not _prepared_stream_covers_replay_window(
                streams.get(key),
                spec=specs.get(key),
                first_event=first_event,
                last_event=last_event,
            )
        )
        if missing:
            unavailable[source_strategy] = missing
    return unavailable


def _prepared_stream_covers_replay_window(
    stream: _PreparedReplayStream | None,
    *,
    spec: _TopBotReplayStreamSpec | None,
    first_event: datetime,
    last_event: datetime,
) -> bool:
    if stream is None or not stream.candles or spec is None:
        return False
    interval_seconds = _timeframe_seconds(spec.unit, spec.unit_number)
    if interval_seconds is None or interval_seconds <= 0:
        return False
    for event_time in (first_event, last_event):
        event_utc = _as_utc(event_time)
        closed_count = bisect_right(stream.close_times, event_utc)
        if closed_count <= 0:
            return False
        latest_close = stream.close_times[closed_count - 1]
        if _has_open_futures_interval(
            latest_close,
            event_utc,
            interval_seconds=interval_seconds,
        ):
            return False
    return True


def _estimate_topbot_evaluator_operations(config: BotConfig, *, execution_bars: int) -> int:
    params = bot_service_module._normalize_strategy_params(
        _TOPBOT_STRATEGY,
        config.strategy_params,
    )
    overrides = params.get("source_strategy_params") or {}
    per_event = 0
    for source in params["source_strategies"]:
        source_params = bot_service_module._normalize_strategy_params(
            source,
            overrides.get(source, {}),
        )
        if source in _TOPBOT_SHARED_CONFIGURED_STRATEGIES or source == "donchian_breakout":
            per_event += _topbot_primary_source_history_limit(config, source)
        elif source in _TOPBOT_LEVEL_STRATEGIES:
            per_event += int(source_params["bars_per_timeframe"]) * 2
        elif source == "opening_rvol_breakout":
            lookback_days = int(source_params["relative_volume_lookback_days"])
            calendar_days = max(lookback_days + 14, 21)
            per_event += max(
                int(config.lookback_bars),
                (calendar_days + 1) * ((24 * 60) // 5),
                int(source_params["atr_period"]) * 20,
                500,
            )
        elif source == "delayed_orb_confirmation":
            per_event += 1500
        elif source == "orb_fibonacci_pullback":
            timeframe_seconds = _timeframe_seconds(
                str(config.timeframe_unit),
                int(config.timeframe_unit_number),
            ) or 60
            per_event += max(1, math.ceil((25 * 60 * 60) / timeframe_seconds))
        elif source == "vwap_gap_retrace":
            per_event += int(source_params["bars_to_fetch"])
        elif source == "supertrend_pivot":
            per_event += max(
                int(config.lookback_bars),
                int(source_params["supertrend_period"])
                + int(source_params["chop_lookback_bars"])
                + 10,
            ) + int(source_params["daily_bars"])
        elif source == "fvg_sweep_mss":
            fvg_limit = max(25, int(config.lookback_bars))
            unit, unit_number = bot_service_module._derive_lower_timeframe(
                base_unit=str(config.timeframe_unit),
                base_unit_number=int(config.timeframe_unit_number),
            )
            base_seconds = _timeframe_seconds(
                str(config.timeframe_unit),
                int(config.timeframe_unit_number),
            ) or 1
            structure_seconds = _timeframe_seconds(unit, unit_number) or 1
            ratio = max(1, int(round(base_seconds / structure_seconds)))
            per_event += fvg_limit + min(5000, max(fvg_limit * ratio, fvg_limit + 25))
        elif source == "atr_adjusted_relative_strength":
            per_event += _topbot_primary_source_history_limit(
                config,
                source,
            ) + max(25, int(config.lookback_bars))
        elif source == "relative_strength_spy":
            per_event += _relative_strength_spy_history_limit(config, source_params) * 2
        else:
            per_event += _topbot_primary_source_history_limit(config, source)
    return execution_bars * max(1, per_event)


def _load_topbot_replay_streams(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    start: datetime,
    end: datetime,
    primary_rows: list[ProjectXMarketCandle],
) -> dict[str, list[ProjectXMarketCandle]]:
    primary_key = _topbot_asset_stream_key(
        str(config.timeframe_unit),
        int(config.timeframe_unit_number),
    )
    streams: dict[str, list[ProjectXMarketCandle]] = {primary_key: primary_rows}
    specs = _topbot_stream_specs(config)
    total_rows = len(primary_rows)

    def stream_identity(spec: _TopBotReplayStreamSpec) -> tuple[str, str, str, int]:
        if spec.contract_id is not None:
            return ("contract", spec.contract_id, spec.unit, spec.unit_number)
        return ("symbol", str(spec.symbol or ""), spec.unit, spec.unit_number)

    max_warmup_by_identity: dict[tuple[str, str, str, int], int] = {}
    for key, spec in specs.items():
        if key == primary_key:
            continue
        identity = stream_identity(spec)
        max_warmup_by_identity[identity] = max(
            max_warmup_by_identity.get(identity, 0),
            int(spec.warmup_bars),
        )

    rows_by_identity: dict[
        tuple[str, str, str, int], list[ProjectXMarketCandle]
    ] = {stream_identity(specs[primary_key]): primary_rows}

    for key, spec in specs.items():
        if key == primary_key:
            continue
        identity = stream_identity(spec)
        cached_rows = rows_by_identity.get(identity)
        if cached_rows is not None:
            streams[key] = cached_rows
            continue
        query = (
            _projected_candle_query(db)
            .filter(ProjectXMarketCandle.user_id == user_id)
            .filter(ProjectXMarketCandle.live.is_(False))
            .filter(ProjectXMarketCandle.unit == spec.unit)
            .filter(ProjectXMarketCandle.unit_number == spec.unit_number)
            .filter(ProjectXMarketCandle.is_partial.is_(False))
        )
        if spec.contract_id is not None:
            query = query.filter(ProjectXMarketCandle.contract_id == spec.contract_id)
        elif spec.symbol is not None:
            query = query.filter(
                or_(
                    ProjectXMarketCandle.contract_id == spec.symbol,
                    ProjectXMarketCandle.symbol == spec.symbol,
                )
            )

        execution_query = (
            query.filter(ProjectXMarketCandle.candle_timestamp >= start)
            .filter(ProjectXMarketCandle.candle_timestamp <= end)
            .order_by(ProjectXMarketCandle.candle_timestamp.asc())
        )
        execution_rows = _collect_projected_candles(execution_query)
        execution_rows = [row for row in execution_rows if _candle_close_time(row) <= end]
        warmup_query = (
            query.filter(ProjectXMarketCandle.candle_timestamp < start)
            .order_by(ProjectXMarketCandle.candle_timestamp.desc())
            # One candle can start before the requested range while closing
            # after the first replay event. Overfetch it so the requested
            # number of genuinely closed warmup bars remains available.
            .limit(max(1, max_warmup_by_identity[identity] + 1))
        )
        warmup_rows = _collect_projected_candles(warmup_query)
        warmup_rows.reverse()
        rows = [*warmup_rows, *execution_rows]
        rows_by_identity[identity] = rows
        streams[key] = rows
        total_rows += len(rows)
        _enforce_backtest_resource_budget(
            replay_rows=total_rows,
            execution_rows=len(primary_rows),
        )

    return streams


def prepare_bot_backtest_data(
    db: Session,
    *,
    user_id: str,
    bot_config_id: int,
    payload: Any,
    client: ProjectXClient,
    now: datetime | None = None,
    include_primary: bool = True,
    request_budget: _ProviderRequestBudget | None = None,
) -> int:
    """Populate TopBot's deterministic replay cache without running a replay."""

    config = (
        db.query(BotConfig)
        .filter(BotConfig.user_id == user_id)
        .filter(BotConfig.id == bot_config_id)
        .one_or_none()
    )
    if config is None:
        raise LookupError("bot_config_not_found")
    if str(config.strategy_type) != _TOPBOT_STRATEGY:
        return 0
    _validate_replay_configuration(config)

    start = _as_utc(payload.start)
    end = _as_utc(payload.end)
    if end <= start:
        raise BacktestConfigurationError("backtest end must be after start")

    captured_now = _as_utc(now or datetime.now(timezone.utc))
    budget = request_budget or _ProviderRequestBudget(MAX_BACKTEST_PROVIDER_REQUESTS)
    fetch_end = min(end, captured_now)
    primary_identity = (
        str(config.contract_id),
        str(config.timeframe_unit),
        int(config.timeframe_unit_number),
    )
    requests: dict[tuple[str, str, int], tuple[str | None, datetime, int]] = {}
    for spec in _topbot_stream_specs(config).values():
        interval_seconds = _timeframe_seconds(spec.unit, spec.unit_number)
        if interval_seconds is None or interval_seconds <= 0:
            raise BacktestConfigurationError(
                f"unsupported_topbot_replay_timeframe:{spec.unit}:{spec.unit_number}"
            )
        contract_id = spec.contract_id or spec.symbol
        if not contract_id:
            raise BacktestConfigurationError(f"topbot_replay_contract_missing:{spec.key}")
        identity = (str(contract_id), str(spec.unit), int(spec.unit_number))
        if identity == primary_identity and not include_primary:
            continue
        fetch_start = start - timedelta(
            seconds=interval_seconds * max(25, int(spec.warmup_bars)) * 3
        )
        existing = requests.get(identity)
        if existing is None or fetch_start < existing[1]:
            requests[identity] = (spec.symbol, fetch_start, int(spec.warmup_bars))
        elif int(spec.warmup_bars) > existing[2]:
            requests[identity] = (existing[0], existing[1], int(spec.warmup_bars))

    primary_execution_rows = (
        db.query(ProjectXMarketCandle)
        .filter(ProjectXMarketCandle.user_id == user_id)
        .filter(ProjectXMarketCandle.contract_id == str(config.contract_id))
        .filter(ProjectXMarketCandle.live.is_(False))
        .filter(ProjectXMarketCandle.unit == str(config.timeframe_unit))
        .filter(ProjectXMarketCandle.unit_number == int(config.timeframe_unit_number))
        .filter(ProjectXMarketCandle.is_partial.is_(False))
        .filter(ProjectXMarketCandle.candle_timestamp >= start)
        .filter(ProjectXMarketCandle.candle_timestamp <= fetch_end)
        .order_by(ProjectXMarketCandle.candle_timestamp.asc())
        .all()
    )
    primary_execution_rows = [
        row for row in primary_execution_rows if _candle_close_time(row) <= fetch_end
    ]
    first_event = (
        _candle_close_time(primary_execution_rows[0])
        if primary_execution_rows
        else start
    )
    last_event = (
        _candle_close_time(primary_execution_rows[-1])
        if primary_execution_rows
        else fetch_end
    )

    prepared_count = 0
    for (contract_id, unit, unit_number), (symbol, fetch_start, warmup_bars) in requests.items():
        interval_seconds = _timeframe_seconds(unit, unit_number)
        assert interval_seconds is not None
        identity = (contract_id, unit, unit_number)
        if (
            identity == primary_identity
            and _cached_primary_stream_covers(
                db,
                user_id=user_id,
                contract_id=contract_id,
                unit=unit,
                unit_number=unit_number,
                fetch_start=fetch_start,
                requested_start=start,
                requested_end=fetch_end,
                warmup_bars=warmup_bars,
                execution_rows=primary_execution_rows,
            )
        ):
            continue
        if identity != primary_identity and primary_execution_rows and _cached_replay_stream_covers(
            db,
            user_id=user_id,
            contract_id=contract_id,
            unit=unit,
            unit_number=unit_number,
            fetch_start=fetch_start,
            requested_start=start,
            first_event=first_event,
            last_event=last_event,
            warmup_bars=warmup_bars,
        ):
            continue
        cursor = fetch_start
        chunk_span = timedelta(seconds=interval_seconds * (MAX_PROVIDER_FETCH_BARS - 1))
        while cursor < fetch_end:
            chunk_end = min(fetch_end, cursor + chunk_span)
            budget.claim()
            bot_service_module.fetch_and_store_market_candles(
                db,
                user_id=user_id,
                client=client,
                contract_id=contract_id,
                symbol=symbol,
                live=False,
                start=cursor,
                end=chunk_end,
                unit=unit,
                unit_number=unit_number,
                limit=MAX_PROVIDER_FETCH_BARS,
                include_partial_bar=False,
                prefer_current_contract=False,
                preserve_cached_history=True,
                authoritative_refresh=True,
            )
            if chunk_end >= fetch_end:
                break
            cursor = chunk_end
        prepared_count += 1

        if identity == primary_identity:
            primary_execution_rows = (
                db.query(ProjectXMarketCandle)
                .filter(ProjectXMarketCandle.user_id == user_id)
                .filter(ProjectXMarketCandle.contract_id == str(config.contract_id))
                .filter(ProjectXMarketCandle.live.is_(False))
                .filter(ProjectXMarketCandle.unit == str(config.timeframe_unit))
                .filter(ProjectXMarketCandle.unit_number == int(config.timeframe_unit_number))
                .filter(ProjectXMarketCandle.is_partial.is_(False))
                .filter(ProjectXMarketCandle.candle_timestamp >= start)
                .filter(ProjectXMarketCandle.candle_timestamp <= fetch_end)
                .order_by(ProjectXMarketCandle.candle_timestamp.asc())
                .all()
            )
            primary_execution_rows = [
                row for row in primary_execution_rows if _candle_close_time(row) <= fetch_end
            ]
            if primary_execution_rows:
                first_event = _candle_close_time(primary_execution_rows[0])
                last_event = _candle_close_time(primary_execution_rows[-1])

    return prepared_count


def _cached_primary_stream_covers(
    db: Session,
    *,
    user_id: str,
    contract_id: str,
    unit: str,
    unit_number: int,
    fetch_start: datetime,
    requested_start: datetime,
    requested_end: datetime,
    warmup_bars: int,
    execution_rows: list[ProjectXMarketCandle],
) -> bool:
    interval_seconds = _timeframe_seconds(unit, unit_number)
    if interval_seconds is None or len(execution_rows) < MIN_EXECUTION_BARS:
        return False
    if any(_cached_candle_is_effectively_partial(row) for row in execution_rows):
        return False
    if _candle_stream_has_overlaps(execution_rows):
        return False
    if _count_futures_session_gaps(
        execution_rows,
        interval_seconds=interval_seconds,
    ):
        return False

    first_start = _as_utc(execution_rows[0].candle_timestamp)
    last_close = _candle_close_time(execution_rows[-1])
    if _has_missing_boundary_slot(
        requested_start,
        first_start,
        interval_seconds=interval_seconds,
    ):
        return False
    if _has_missing_boundary_slot(
        last_close,
        requested_end,
        interval_seconds=interval_seconds,
    ):
        return False

    return _cached_replay_stream_covers(
        db,
        user_id=user_id,
        contract_id=contract_id,
        unit=unit,
        unit_number=unit_number,
        fetch_start=fetch_start,
        requested_start=requested_start,
        first_event=_candle_close_time(execution_rows[0]),
        last_event=last_close,
        warmup_bars=warmup_bars,
    )


def _cached_replay_stream_covers(
    db: Session,
    *,
    user_id: str,
    contract_id: str,
    unit: str,
    unit_number: int,
    fetch_start: datetime,
    requested_start: datetime,
    first_event: datetime,
    last_event: datetime,
    warmup_bars: int,
) -> bool:
    interval_seconds = _timeframe_seconds(unit, unit_number)
    if interval_seconds is None:
        return False
    fetch_start_utc = _as_utc(fetch_start)
    requested_start_utc = _as_utc(requested_start)
    first_event_utc = _as_utc(first_event)
    last_event_utc = _as_utc(last_event)
    query = (
        _projected_candle_query(db)
        .filter(ProjectXMarketCandle.user_id == user_id)
        .filter(ProjectXMarketCandle.contract_id == contract_id)
        .filter(ProjectXMarketCandle.live.is_(False))
        .filter(ProjectXMarketCandle.unit == unit)
        .filter(ProjectXMarketCandle.unit_number == unit_number)
        .filter(ProjectXMarketCandle.is_partial.is_(False))
        .filter(ProjectXMarketCandle.candle_timestamp >= fetch_start_utc)
        .filter(ProjectXMarketCandle.candle_timestamp <= last_event_utc)
        .order_by(ProjectXMarketCandle.candle_timestamp.asc())
    )
    rows = _collect_projected_candles(query)
    if any(_cached_candle_was_fetched_before_nominal_close(row) for row in rows):
        return False
    if _candle_stream_has_overlaps(rows):
        return False
    if _count_futures_session_gaps(rows, interval_seconds=interval_seconds):
        return False
    close_times = [_candle_close_time(row) for row in rows]
    start_times = [_as_utc(row.candle_timestamp) for row in rows]
    first_end = bisect_right(close_times, first_event_utc)
    last_end = bisect_right(close_times, last_event_utc)
    closed_by_first = rows[:first_end]
    warmup_count = min(first_end, bisect_left(start_times, requested_start_utc))
    if warmup_count < max(1, int(warmup_bars)):
        return False

    for event, candidates in (
        (first_event_utc, closed_by_first),
        (last_event_utc, rows[:last_end]),
    ):
        if not candidates:
            return False
        latest_close = _candle_close_time(candidates[-1])
        if _has_open_futures_interval(
            latest_close,
            event,
            interval_seconds=interval_seconds,
        ):
            return False
    return True


def run_backtest(
    *,
    config: BotConfig,
    candles: list[ProjectXMarketCandle],
    start: datetime,
    end: datetime,
    starting_balance: float,
    commission_per_contract: float,
    slippage_ticks: float,
    tick_size: float,
    tick_value: float,
    force_close_at_end: bool = True,
    signal_evaluator: SignalEvaluator | None = None,
    replay_streams: Mapping[str, list[ProjectXMarketCandle]] | None = None,
) -> dict[str, Any]:
    """Run a pure replay. This function cannot create or route an order."""

    return BacktestEngine(
        config=config,
        candles=candles,
        settings=BacktestSettings(
            start=start,
            end=end,
            starting_balance=starting_balance,
            commission_per_contract=commission_per_contract,
            slippage_ticks=slippage_ticks,
            tick_size=tick_size,
            tick_value=tick_value,
            force_close_at_end=force_close_at_end,
        ),
        signal_evaluator=signal_evaluator,
        replay_streams=replay_streams,
    ).run()


def _projected_candle_query(db: Session):
    """Select only replay fields, avoiding ORM identity-map materialization."""

    return db.query(
        ProjectXMarketCandle.user_id,
        ProjectXMarketCandle.contract_id,
        ProjectXMarketCandle.symbol,
        ProjectXMarketCandle.live,
        ProjectXMarketCandle.unit,
        ProjectXMarketCandle.unit_number,
        ProjectXMarketCandle.candle_timestamp,
        ProjectXMarketCandle.open_price,
        ProjectXMarketCandle.high_price,
        ProjectXMarketCandle.low_price,
        ProjectXMarketCandle.close_price,
        ProjectXMarketCandle.volume,
        ProjectXMarketCandle.is_partial,
        ProjectXMarketCandle.raw_payload,
        ProjectXMarketCandle.fetched_at,
    )


def _collect_projected_candles(query: Any) -> list[ProjectXMarketCandle]:
    rows: list[ProjectXMarketCandle] = []
    for values in query.yield_per(CANDLE_QUERY_CHUNK_SIZE):
        rows.append(_ProjectedCandle(*values))
        if len(rows) % CANDLE_QUERY_CHUNK_SIZE == 0:
            _enforce_backtest_resource_budget(
                replay_rows=len(rows),
                execution_rows=0,
            )
    return rows


def _load_primary_closed_candles(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    closed_by: datetime,
) -> list[ProjectXMarketCandle]:
    """Load every eligible primary bar without rolling across futures deliveries."""

    cutoff = _as_utc(closed_by)
    query = (
        _projected_candle_query(db)
        .filter(ProjectXMarketCandle.user_id == user_id)
        .filter(ProjectXMarketCandle.contract_id == str(config.contract_id))
        .filter(ProjectXMarketCandle.live.is_(False))
        .filter(ProjectXMarketCandle.unit == str(config.timeframe_unit))
        .filter(ProjectXMarketCandle.unit_number == int(config.timeframe_unit_number))
        .filter(ProjectXMarketCandle.is_partial.is_(False))
        .filter(ProjectXMarketCandle.candle_timestamp <= cutoff)
        .order_by(ProjectXMarketCandle.candle_timestamp.asc())
    )
    rows = _collect_projected_candles(query)
    return _ClosedCandleList(
        row
        for row in rows
        if not _cached_candle_is_effectively_partial(row)
        and _candle_close_time(row) <= cutoff
    )


def _requested_backtest_bounds(payload: Any) -> tuple[datetime, datetime] | None:
    raw_start = getattr(payload, "start", None)
    raw_end = getattr(payload, "end", None)
    if (raw_start is None) != (raw_end is None):
        raise BacktestConfigurationError(
            "backtest start and end must be provided together"
        )
    if raw_start is None:
        return None
    start = _as_utc(raw_start)
    end = _as_utc(raw_end)
    if end <= start:
        raise BacktestConfigurationError("backtest end must be after start")
    return start, end


def _resolve_backtest_window(
    rows: list[ProjectXMarketCandle],
    *,
    payload: Any,
    now: datetime,
) -> _ResolvedBacktestWindow:
    """Resolve absent dates to complete exact-contract coverage at one instant."""

    captured_now = _as_utc(now)
    requested = _requested_backtest_bounds(payload)
    if requested is None:
        eligible = rows
        full_history = True
    else:
        requested_start, requested_end = requested
        effective_end = min(requested_end, captured_now)
        eligible = [
            row
            for row in rows
            if _as_utc(row.candle_timestamp) >= requested_start
            and _candle_close_time(row) <= effective_end
        ]
        full_history = False

    if len(eligible) < MIN_EXECUTION_BARS:
        mode = "full configured-contract history" if requested is None else "requested range"
        raise InsufficientBacktestDataError(
            "insufficient_backtest_data: at least 2 fully closed execution bars are required "
            f"for the {mode}; found {len(eligible)}"
        )

    if requested is None:
        start = _as_utc(eligible[0].candle_timestamp)
        end = _candle_close_time(eligible[-1])
        requested_start = start
        requested_end = end
    else:
        requested_start, requested_end = requested
        start = requested_start
        end = min(requested_end, captured_now)

    return _ResolvedBacktestWindow(
        requested_start=requested_start,
        requested_end=requested_end,
        start=start,
        end=end,
        full_history=full_history,
    )


_FULL_HISTORY_DISCOVERY_START = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MAX_PROVIDER_EMPTY_SPAN = timedelta(days=14)


def _fetch_exact_primary_chunk(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    client: ProjectXClient,
    request_budget: _ProviderRequestBudget,
    start: datetime,
    end: datetime,
) -> list[ProjectXMarketCandle]:
    """Fetch one exact-delivery chunk and propagate provider failures."""

    if end <= start:
        return []
    unit = str(config.timeframe_unit)
    provider_unit = bot_service_module._PROJECTX_UNIT_BY_NAME.get(unit)
    if provider_unit is None:
        raise BacktestConfigurationError(f"unsupported_candle_unit:{unit}")
    request_budget.claim()
    bars = client.retrieve_bars(
        contract_id=str(config.contract_id),
        live=False,
        start=_as_utc(start),
        end=_as_utc(end),
        unit=provider_unit,
        unit_number=int(config.timeframe_unit_number),
        limit=MAX_PROVIDER_FETCH_BARS,
        include_partial_bar=False,
    )
    return bot_service_module.store_market_candles(
        db,
        user_id=user_id,
        contract_id=str(config.contract_id),
        symbol=config.symbol,
        live=False,
        unit=unit,
        unit_number=int(config.timeframe_unit_number),
        bars=bars,
    )


def _prepare_topbot_primary_full_history(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    client: ProjectXClient,
    request_budget: _ProviderRequestBudget,
    now: datetime,
) -> None:
    """Discover and persist the provider's complete configured-delivery history."""

    captured_now = _as_utc(now)
    interval_seconds = _timeframe_seconds(
        str(config.timeframe_unit),
        int(config.timeframe_unit_number),
    )
    if interval_seconds is None or interval_seconds <= 0:
        raise BacktestConfigurationError(
            "unsupported_topbot_replay_timeframe:"
            f"{config.timeframe_unit}:{config.timeframe_unit_number}"
        )
    chunk_span = timedelta(seconds=interval_seconds * (MAX_PROVIDER_FETCH_BARS - 1))

    seed = _fetch_exact_primary_chunk(
        db,
        user_id=user_id,
        config=config,
        client=client,
        request_budget=request_budget,
        start=_FULL_HISTORY_DISCOVERY_START,
        end=captured_now,
    )
    if not seed:
        raise InsufficientBacktestDataError(
            "insufficient_backtest_data: provider returned no closed history for configured "
            f"contract {config.contract_id}"
        )

    rows = _load_primary_closed_candles(
        db,
        user_id=user_id,
        config=config,
        closed_by=captured_now,
    )
    if not rows:
        raise InsufficientBacktestDataError(
            "insufficient_backtest_data: provider returned no valid closed history for "
            f"configured contract {config.contract_id}"
        )

    earliest = _as_utc(rows[0].candle_timestamp)
    backward_cursor = earliest
    empty_span = timedelta(0)
    while backward_cursor > _FULL_HISTORY_DISCOVERY_START:
        chunk_start = max(_FULL_HISTORY_DISCOVERY_START, backward_cursor - chunk_span)
        fetched = _fetch_exact_primary_chunk(
            db,
            user_id=user_id,
            config=config,
            client=client,
            request_budget=request_budget,
            start=chunk_start,
            end=backward_cursor,
        )
        older = [
            row for row in fetched if _as_utc(row.candle_timestamp) < earliest
        ]
        if older:
            earliest = min(_as_utc(row.candle_timestamp) for row in older)
            backward_cursor = earliest
            empty_span = timedelta(0)
            continue
        empty_span += backward_cursor - chunk_start
        backward_cursor = chunk_start
        if empty_span >= _MAX_PROVIDER_EMPTY_SPAN:
            break

    rows = _load_primary_closed_candles(
        db,
        user_id=user_id,
        config=config,
        closed_by=captured_now,
    )
    latest_timestamp = _as_utc(rows[-1].candle_timestamp)
    forward_cursor = _candle_close_time(rows[-1])
    empty_span = timedelta(0)
    while forward_cursor < captured_now:
        chunk_end = min(captured_now, forward_cursor + chunk_span)
        fetched = _fetch_exact_primary_chunk(
            db,
            user_id=user_id,
            config=config,
            client=client,
            request_budget=request_budget,
            start=forward_cursor,
            end=chunk_end,
        )
        newer = [
            row for row in fetched if _as_utc(row.candle_timestamp) > latest_timestamp
        ]
        if newer:
            latest_row = max(newer, key=lambda row: _as_utc(row.candle_timestamp))
            latest_timestamp = _as_utc(latest_row.candle_timestamp)
            forward_cursor = _candle_close_time(latest_row)
            empty_span = timedelta(0)
            continue
        empty_span += chunk_end - forward_cursor
        forward_cursor = chunk_end
        if empty_span >= _MAX_PROVIDER_EMPTY_SPAN:
            break


def create_bot_backtest(
    db: Session,
    *,
    user_id: str,
    bot_config_id: int,
    payload: Any,
    client: ProjectXClient | None = None,
    now: datetime | None = None,
) -> BotBacktest:
    config = (
        db.query(BotConfig)
        .filter(BotConfig.user_id == user_id)
        .filter(BotConfig.id == bot_config_id)
        .one_or_none()
    )
    if config is None:
        raise LookupError("bot_config_not_found")
    _require_supported_strategy(str(config.strategy_type))
    _validate_replay_configuration(config)

    specs = load_instrument_specs(db)
    symbol_key = normalize_symbol_key(config.symbol) or normalize_symbol_key(config.contract_id)
    spec = specs.get(symbol_key or "")
    if spec is None:
        raise BacktestConfigurationError(
            f"instrument_metadata_missing:{symbol_key or config.contract_id}"
        )

    captured_now = _as_utc(now or datetime.now(timezone.utc))
    requested_bounds = _requested_backtest_bounds(payload)
    is_topbot = str(config.strategy_type) == _TOPBOT_STRATEGY
    request_budget = _ProviderRequestBudget(MAX_BACKTEST_PROVIDER_REQUESTS) if is_topbot else None
    if is_topbot:
        if client is None:
            raise BacktestConfigurationError("topbot_backtest_market_data_client_required")
        if requested_bounds is None:
            _prepare_topbot_primary_full_history(
                db,
                user_id=user_id,
                config=config,
                client=client,
                request_budget=request_budget,
                now=captured_now,
            )
        else:
            prepare_bot_backtest_data(
                db,
                user_id=user_id,
                bot_config_id=bot_config_id,
                payload=payload,
                client=client,
                now=captured_now,
                request_budget=request_budget,
            )

    primary_rows = _load_primary_closed_candles(
        db,
        user_id=user_id,
        config=config,
        closed_by=captured_now,
    )
    window = _resolve_backtest_window(primary_rows, payload=payload, now=captured_now)

    if is_topbot and requested_bounds is None:
        prepare_bot_backtest_data(
            db,
            user_id=user_id,
            bot_config_id=bot_config_id,
            payload=window,
            client=client,
            now=captured_now,
            include_primary=False,
            request_budget=request_budget,
        )
        primary_rows = _load_primary_closed_candles(
            db,
            user_id=user_id,
            config=config,
            closed_by=captured_now,
        )
        window = _resolve_backtest_window(primary_rows, payload=payload, now=captured_now)

    primary_start_times = [
        _as_utc(row.candle_timestamp) for row in primary_rows
    ]
    primary_close_times = [_candle_close_time(row) for row in primary_rows]
    execution_start_index = bisect_left(primary_start_times, window.start)
    execution_end_index = bisect_right(primary_close_times, window.end)
    execution_rows = _closed_candle_slice(
        primary_rows,
        execution_start_index,
        execution_end_index,
    )

    rolling_warmup_limit = max(
        int(config.lookback_bars),
        _strategy_history_bars(config, hard_minimum=False),
    )
    if is_topbot:
        primary_key = _topbot_asset_stream_key(
            str(config.timeframe_unit),
            int(config.timeframe_unit_number),
        )
        primary_spec = _topbot_stream_specs(config)[primary_key]
        rolling_warmup_limit = max(
            rolling_warmup_limit,
            primary_spec.warmup_bars,
        )
    warmup_limit = _max_evaluator_input_bars(
        config,
        rolling_limit=rolling_warmup_limit,
    )
    warmup_start_index = max(0, execution_start_index - warmup_limit)
    warmup_rows = _closed_candle_slice(
        primary_rows,
        warmup_start_index,
        execution_start_index,
    )
    replay_rows = _ClosedCandleList(warmup_rows)
    replay_rows.extend(execution_rows)
    replay_streams: dict[str, list[ProjectXMarketCandle]] | None = None
    if is_topbot:
        replay_streams = _load_topbot_replay_streams(
            db,
            user_id=user_id,
            config=config,
            start=window.start,
            end=window.end,
            primary_rows=replay_rows,
        )

    result = run_backtest(
        config=config,
        candles=replay_rows,
        start=window.start,
        end=window.end,
        starting_balance=float(payload.starting_balance),
        commission_per_contract=float(payload.commission_per_contract),
        slippage_ticks=float(payload.slippage_ticks),
        tick_size=spec.tick_size,
        tick_value=spec.tick_value,
        force_close_at_end=bool(payload.force_close_at_end),
        replay_streams=replay_streams,
    )
    if is_topbot and requested_bounds is None:
        result["warnings"].append(
            "Full-history preparation scanned the exact configured delivery backward and forward; "
            "because ProjectX supplies no end-of-history marker, the older boundary is inferred only "
            f"after at least {int(_MAX_PROVIDER_EMPTY_SPAN.total_seconds() // 86_400)} consecutive "
            "empty calendar days. Provider limits fail explicitly instead of saving a partial replay."
        )
    input_fingerprint = (
        candle_stream_input_fingerprint(replay_streams)
        if replay_streams is not None
        else candle_input_fingerprint(replay_rows)
    )
    assumptions = result["assumptions"]
    snapshot = result["config_snapshot"]
    row = BotBacktest(
        user_id=user_id,
        bot_config_id=int(config.id),
        account_id=int(config.account_id),
        engine_version=BACKTEST_ENGINE_VERSION,
        strategy_type=str(config.strategy_type),
        contract_id=str(config.contract_id),
        symbol=config.symbol,
        timeframe_unit=str(config.timeframe_unit),
        timeframe_unit_number=int(config.timeframe_unit_number),
        requested_start=window.requested_start,
        requested_end=window.requested_end,
        actual_start=_as_utc(datetime.fromisoformat(str(result["range"]["start"]))),
        actual_end=_as_utc(datetime.fromisoformat(str(result["range"]["end"]))),
        starting_balance=float(payload.starting_balance),
        commission_per_contract=float(payload.commission_per_contract),
        slippage_ticks=float(payload.slippage_ticks),
        tick_size=spec.tick_size,
        tick_value=spec.tick_value,
        bar_count=int(result["range"]["bar_count"]),
        input_fingerprint=input_fingerprint,
        config_snapshot=snapshot,
        assumptions_snapshot=assumptions,
        result_snapshot=result,
    )
    db.add(row)
    db.flush()
    return row


def serialize_bot_backtest(row: BotBacktest) -> dict[str, Any]:
    payload = dict(row.result_snapshot or {})
    payload.update(
        {
            "id": int(row.id),
            "bot_config_id": int(row.bot_config_id) if row.bot_config_id is not None else None,
            "engine_version": row.engine_version,
            "input_fingerprint": row.input_fingerprint,
            "created_at": _as_utc(row.created_at).isoformat(),
        }
    )
    return payload


def candle_input_fingerprint(candles: list[ProjectXMarketCandle]) -> str:
    ordered = (
        candles
        if getattr(candles, "_topsignal_sorted_closed", False)
        else sorted(candles, key=lambda candle: _as_utc(candle.candle_timestamp))
    )
    return _incremental_candle_fingerprint(
        {
            "contract_id": str(row.contract_id),
            "live": bool(row.live),
            "unit": str(row.unit),
            "unit_number": int(row.unit_number),
            "timestamp": _as_utc(row.candle_timestamp).isoformat(),
            "open": str(row.open_price),
            "high": str(row.high_price),
            "low": str(row.low_price),
            "close": str(row.close_price),
            "volume": str(row.volume),
            "is_partial": bool(row.is_partial),
        }
        for row in ordered
        if not _cached_candle_is_effectively_partial(row)
    )


def candle_stream_input_fingerprint(
    streams: Mapping[str, list[ProjectXMarketCandle]],
) -> str:
    def canonical_rows() -> Iterable[dict[str, Any]]:
        for key in sorted(streams):
            candles = streams[key]
            ordered = (
                candles
                if getattr(candles, "_topsignal_sorted_closed", False)
                else sorted(
                    candles,
                    key=lambda candle: _as_utc(candle.candle_timestamp),
                )
            )
            for row in ordered:
                if _cached_candle_is_effectively_partial(row):
                    continue
                yield {
                    "stream": key,
                    "contract_id": str(row.contract_id),
                    "live": bool(row.live),
                    "unit": str(row.unit),
                    "unit_number": int(row.unit_number),
                    "timestamp": _as_utc(row.candle_timestamp).isoformat(),
                    "open": str(row.open_price),
                    "high": str(row.high_price),
                    "low": str(row.low_price),
                    "close": str(row.close_price),
                    "volume": str(row.volume),
                    "is_partial": bool(row.is_partial),
                }

    return _incremental_candle_fingerprint(canonical_rows())


def _incremental_candle_fingerprint(rows: Iterable[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    digest.update(b"[")
    first = True
    for row in rows:
        if first:
            first = False
        else:
            digest.update(b",")
        digest.update(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
    digest.update(b"]")
    return digest.hexdigest()


def _require_supported_strategy(strategy_type: str) -> None:
    if strategy_type in SUPPORTED_BACKTEST_STRATEGIES:
        return
    reason = UNSUPPORTED_BACKTEST_STRATEGY_REASONS.get(
        strategy_type, "no exact historical replay adapter is implemented"
    )
    raise UnsupportedBacktestStrategyError(
        f"strategy_not_supported_for_backtesting:{strategy_type}: {reason}"
    )


def _validate_replay_configuration(config: BotConfig) -> None:
    try:
        get_strategy_definition(str(config.strategy_type)).configuration_validator(
            timeframe_unit=str(config.timeframe_unit),
            timeframe_unit_number=int(config.timeframe_unit_number),
            fast_period=int(config.fast_period),
            slow_period=int(config.slow_period),
        )
    except ValueError as exc:
        raise BacktestConfigurationError(
            f"invalid_backtest_strategy_configuration:{exc}"
        ) from exc
    if str(config.strategy_type) == "orb_fibonacci_pullback":
        if str(config.timeframe_unit) != "minute":
            raise BacktestConfigurationError(
                "invalid_backtest_strategy_configuration: ORB Fibonacci Pullback requires minute candles"
            )
        params = get_strategy_definition(
            "orb_fibonacci_pullback"
        ).parameter_normalizer(config.strategy_params)
        opening_minutes = int(params["opening_range_minutes"])
        bucket = int(config.timeframe_unit_number)
        if opening_minutes % bucket != 0:
            raise BacktestConfigurationError(
                "invalid_backtest_strategy_configuration: opening range must align to the minute bucket"
            )


def _contract_is_allowed(config: BotConfig) -> bool:
    raw_values = config.allowed_contracts if isinstance(config.allowed_contracts, list) else []
    allowed = {str(value).strip() for value in raw_values if str(value).strip()}
    if not allowed:
        return True
    candidates = {
        str(value).strip()
        for value in (config.contract_id, config.symbol)
        if value is not None and str(value).strip()
    }
    return bool(allowed.intersection(candidates))


def _closed_candle_slice(
    candles: list[ProjectXMarketCandle],
    start: int,
    end: int | None = None,
) -> list[ProjectXMarketCandle]:
    sliced = candles[start:end]
    if getattr(candles, "_topsignal_sorted_closed", False):
        return _ClosedCandleList(sliced)
    return sliced


def _enforce_backtest_resource_budget(
    *,
    replay_rows: int,
    execution_rows: int,
) -> None:
    budget = max(1, int(BACKTEST_MEMORY_BUDGET_BYTES))
    estimated = (
        max(0, int(replay_rows)) * ESTIMATED_REPLAY_CANDLE_BYTES
        + max(0, int(execution_rows)) * ESTIMATED_EXECUTION_RESULT_BYTES
    )
    if estimated <= budget:
        return
    estimated_mib = math.ceil(estimated / (1024 * 1024))
    budget_mib = max(1, budget // (1024 * 1024))
    raise BacktestConfigurationError(
        "backtest_resource_budget_exceeded: the complete resolved history requires "
        f"an estimated {estimated_mib:,} MiB, above the configured {budget_mib:,} MiB "
        "working-set budget; no partial result was saved"
    )


def _validate_settings(settings: BacktestSettings) -> BacktestSettings:
    normalized = BacktestSettings(
        start=_as_utc(settings.start),
        end=_as_utc(settings.end),
        starting_balance=float(settings.starting_balance),
        commission_per_contract=float(settings.commission_per_contract),
        slippage_ticks=float(settings.slippage_ticks),
        tick_size=float(settings.tick_size),
        tick_value=float(settings.tick_value),
        force_close_at_end=bool(settings.force_close_at_end),
    )
    if normalized.end <= normalized.start:
        raise BacktestConfigurationError("backtest end must be after start")
    for name in ["starting_balance", "tick_size", "tick_value"]:
        value = getattr(normalized, name)
        if not math.isfinite(value) or value <= 0:
            raise BacktestConfigurationError(f"{name} must be a finite positive number")
    for name in ["commission_per_contract", "slippage_ticks"]:
        value = getattr(normalized, name)
        if not math.isfinite(value) or value < 0:
            raise BacktestConfigurationError(f"{name} must be a finite non-negative number")
    return normalized


def _validate_and_sort_candles(
    candles: list[ProjectXMarketCandle],
    *,
    config: BotConfig,
) -> tuple[list[ProjectXMarketCandle], int]:
    if getattr(candles, "_topsignal_sorted_closed", False):
        closed = _ClosedCandleList(candles)
        excluded_partial = 0
    else:
        closed = _ClosedCandleList(
            row for row in candles if not _cached_candle_is_effectively_partial(row)
        )
        excluded_partial = len(candles) - len(closed)
        closed.sort(key=lambda row: _as_utc(row.candle_timestamp))
    seen: set[datetime] = set()
    previous_close_time: datetime | None = None
    for row in closed:
        timestamp = _as_utc(row.candle_timestamp)
        if timestamp in seen:
            raise MalformedBacktestDataError(
                f"duplicate_candle_timestamp:{timestamp.isoformat()}"
            )
        seen.add(timestamp)
        if str(row.contract_id) != str(config.contract_id):
            raise MalformedBacktestDataError(
                f"mixed_contract_candles:{timestamp.isoformat()}"
            )
        if bool(row.live):
            raise MalformedBacktestDataError(
                f"live_candle_not_allowed:{timestamp.isoformat()}"
            )
        if config.user_id is not None and str(row.user_id) != str(config.user_id):
            raise MalformedBacktestDataError(
                f"mixed_user_candles:{timestamp.isoformat()}"
            )
        if str(row.unit) != str(config.timeframe_unit) or int(row.unit_number) != int(
            config.timeframe_unit_number
        ):
            raise MalformedBacktestDataError(
                "mixed_timeframe_candles: every replay candle must match the bot timeframe"
            )
        if previous_close_time is not None and timestamp < previous_close_time:
            raise MalformedBacktestDataError(
                f"overlapping_candles:{timestamp.isoformat()}"
            )
        previous_close_time = _candle_close_time(row)
        values = {
            "open": float(row.open_price),
            "high": float(row.high_price),
            "low": float(row.low_price),
            "close": float(row.close_price),
            "volume": float(row.volume or 0),
        }
        if not all(math.isfinite(value) for value in values.values()):
            raise MalformedBacktestDataError(
                f"non_finite_candle_value:{timestamp.isoformat()}"
            )
        if min(values["open"], values["high"], values["low"], values["close"]) <= 0:
            raise MalformedBacktestDataError(
                f"non_positive_candle_price:{timestamp.isoformat()}"
            )
        if values["high"] < max(values["open"], values["close"], values["low"]):
            raise MalformedBacktestDataError(f"invalid_candle_high:{timestamp.isoformat()}")
        if values["low"] > min(values["open"], values["close"], values["high"]):
            raise MalformedBacktestDataError(f"invalid_candle_low:{timestamp.isoformat()}")
        if values["volume"] < 0:
            raise MalformedBacktestDataError(f"negative_candle_volume:{timestamp.isoformat()}")
    return closed, excluded_partial


def _cached_candle_is_effectively_partial(candle: ProjectXMarketCandle) -> bool:
    return bool(candle.is_partial) or _cached_candle_was_fetched_before_nominal_close(candle)


def _cached_candle_was_fetched_before_nominal_close(
    candle: ProjectXMarketCandle,
) -> bool:
    if str(candle.unit) not in {"second", "minute", "hour"}:
        return False
    fetched_at = candle.fetched_at
    if fetched_at is None:
        return False
    raw_payload = candle.raw_payload
    if isinstance(raw_payload, dict) and any(
        key in raw_payload for key in ("isPartial", "is_partial", "partial")
    ):
        return False
    return _as_utc(fetched_at) < _candle_close_time(candle)


def _candle_stream_has_overlaps(candles: list[ProjectXMarketCandle]) -> bool:
    previous_close_time: datetime | None = None
    for row in sorted(candles, key=lambda candle: _as_utc(candle.candle_timestamp)):
        timestamp = _as_utc(row.candle_timestamp)
        if previous_close_time is not None and timestamp < previous_close_time:
            return True
        previous_close_time = _candle_close_time(row)
    return False


def _candle_close_time(candle: ProjectXMarketCandle) -> datetime:
    timestamp = _as_utc(candle.candle_timestamp)
    unit = str(candle.unit)
    unit_number = int(candle.unit_number)
    if unit == "month":
        return _add_months(timestamp, unit_number)
    seconds = _UNIT_SECONDS.get(unit)
    if seconds is None:
        raise MalformedBacktestDataError(f"unsupported_candle_unit:{unit}")
    return timestamp + timedelta(seconds=seconds * unit_number)


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=value.tzinfo)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=value.tzinfo)
    month_start = datetime(year, month, 1, tzinfo=value.tzinfo)
    last_day = (next_month - month_start).days
    return value.replace(year=year, month=month, day=min(value.day, last_day))


def _extract_bracket(pending: _PendingSignal) -> tuple[float | None, float | None]:
    stop = _finite_optional_float(pending.payload.get("stop_loss"))
    target = _finite_optional_float(
        pending.payload.get("take_profit")
        if pending.payload.get("take_profit") is not None
        else pending.payload.get("final_take_profit")
    )
    return stop, target


def _anchor_bracket_to_fill(
    *,
    action: str,
    signal_price: float | None,
    entry_price: float,
    planned_stop: float | None,
    planned_target: float | None,
    tick_size: float,
) -> tuple[float | None, float | None]:
    """Mirror live ProjectX bracket distances, then anchor them to the fill.

    Live routing converts evaluator levels to whole-tick distances from the
    signal price. ProjectX attaches those distances to the eventual market
    fill, so a gap does not leave stale absolute stop/target prices behind.
    """

    if signal_price is None:
        return None, None
    signal = Decimal(str(signal_price))
    entry = Decimal(str(entry_price))
    tick = Decimal(str(tick_size))

    def distance_ticks(level: float | None) -> int | None:
        if level is None:
            return None
        distance = abs(Decimal(str(level)) - signal) / tick
        return max(1, int(distance.to_integral_value(rounding=ROUND_HALF_EVEN)))

    stop_ticks = distance_ticks(planned_stop)
    target_ticks = distance_ticks(planned_target)
    direction = Decimal("1") if action == "BUY" else Decimal("-1")
    stop = entry - direction * stop_ticks * tick if stop_ticks is not None else None
    target = entry + direction * target_ticks * tick if target_ticks is not None else None
    return (
        float(stop) if stop is not None else None,
        float(target) if target is not None else None,
    )


def _bracket_is_valid(
    *,
    action: str,
    signal_price: float | None,
    stop_loss: float | None,
    take_profit: float | None,
) -> bool:
    if signal_price is None:
        return stop_loss is None and take_profit is None
    if stop_loss is not None:
        if action == "BUY" and stop_loss >= signal_price:
            return False
        if action == "SELL" and stop_loss <= signal_price:
            return False
    if take_profit is not None:
        if action == "BUY" and take_profit <= signal_price:
            return False
        if action == "SELL" and take_profit >= signal_price:
            return False
    return stop_loss is None or take_profit is None or stop_loss != take_profit


def _strategy_history_bars(config: BotConfig, *, hard_minimum: bool) -> int:
    definition = get_strategy_definition(str(config.strategy_type))
    requirements = definition.minimum_history(
        strategy_params=config.strategy_params,
        fast_period=int(config.fast_period),
        slow_period=int(config.slow_period),
        timeframe_unit=str(config.timeframe_unit),
        timeframe_unit_number=int(config.timeframe_unit_number),
    )
    signal_requirement = next(
        (requirement for requirement in requirements if requirement.role == "signal"),
        requirements[0],
    )
    value = (
        signal_requirement.hard_minimum_bars
        if hard_minimum
        else signal_requirement.minimum_bars
    )
    return max(1, int(value or 1))


def _entry_quantity(config: BotConfig, payload: dict[str, Any]) -> float:
    target = _finite_optional_float(payload.get("target_position_qty"))
    return abs(target) if target is not None and target != 0 else float(config.order_size)


def _inside_session(timestamp: datetime, *, start_text: str, end_text: str) -> bool:
    local_time = _as_utc(timestamp).astimezone(TRADING_TZ).time().replace(tzinfo=None)
    start = _parse_time(start_text)
    end = _parse_time(end_text)
    if start <= end:
        return start <= local_time <= end
    return local_time >= start or local_time <= end


def _signal_fill_is_in_same_session(
    signal_timestamp: datetime,
    fill_timestamp: datetime,
    *,
    start_text: str,
    end_text: str,
) -> bool:
    session_start, session_end = _session_window_utc_for_reference(
        _as_utc(signal_timestamp),
        start_text=start_text,
        end_text=end_text,
    )
    signal = _as_utc(signal_timestamp)
    fill = _as_utc(fill_timestamp)
    return session_start <= signal <= session_end and session_start <= fill <= session_end


def _parse_time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise BacktestConfigurationError("session times must use HH:MM format") from exc


def _price_pnl(
    *,
    side: str,
    entry: float,
    exit: float,
    quantity: float,
    tick_size: float,
    tick_value: float,
) -> float:
    direction = Decimal("1") if side == "long" else Decimal("-1")
    value = (
        (Decimal(str(exit)) - Decimal(str(entry)))
        / Decimal(str(tick_size))
        * Decimal(str(tick_value))
        * Decimal(str(quantity))
        * direction
    )
    return float(value)


def _build_metrics(
    trades: list[dict[str, Any]],
    *,
    equity_curve: list[dict[str, Any]],
    drawdown_series: list[dict[str, Any]],
    exposure_percent: float,
) -> dict[str, Any]:
    overall = _trade_breakdown(trades)
    consecutive_wins = 0
    consecutive_losses = 0
    max_wins = 0
    max_losses = 0
    for trade in trades:
        net = float(trade["net_pnl"])
        if net > 0:
            consecutive_wins += 1
            consecutive_losses = 0
        elif net < 0:
            consecutive_losses += 1
            consecutive_wins = 0
        else:
            consecutive_wins = 0
            consecutive_losses = 0
        max_wins = max(max_wins, consecutive_wins)
        max_losses = max(max_losses, consecutive_losses)

    max_drawdown_dollars = max(
        (float(point["drawdown_dollars"]) for point in drawdown_series), default=0.0
    )
    max_drawdown_percent = max(
        (float(point["drawdown_percent"]) for point in drawdown_series), default=0.0
    )
    total_commission = sum(float(trade["commission"]) for trade in trades)
    return {
        "gross_pnl": overall["gross_pnl"],
        "net_pnl": overall["net_pnl"],
        "total_commission": _clean(total_commission),
        "trade_count": overall["trade_count"],
        "winning_trades": overall["winning_trades"],
        "losing_trades": overall["losing_trades"],
        "win_rate": overall["win_rate"],
        "profit_factor": overall["profit_factor"],
        "expectancy": overall["expectancy"],
        "average_win": overall["average_win"],
        "average_loss": overall["average_loss"],
        "payoff_ratio": overall["payoff_ratio"],
        "max_drawdown_dollars": _clean(max_drawdown_dollars),
        "max_drawdown_percent": _clean(max_drawdown_percent),
        "average_mae": _clean(
            sum(float(trade["mae"]) for trade in trades) / len(trades) if trades else 0.0
        ),
        "average_mfe": _clean(
            sum(float(trade["mfe"]) for trade in trades) / len(trades) if trades else 0.0
        ),
        "max_consecutive_wins": max_wins,
        "max_consecutive_losses": max_losses,
        "exposure_percent": _clean(exposure_percent),
        "long": _trade_breakdown([trade for trade in trades if trade["side"] == "long"]),
        "short": _trade_breakdown([trade for trade in trades if trade["side"] == "short"]),
    }


def _trade_breakdown(trades: list[dict[str, Any]]) -> dict[str, Any]:
    winners = [float(trade["net_pnl"]) for trade in trades if float(trade["net_pnl"]) > 0]
    losers = [float(trade["net_pnl"]) for trade in trades if float(trade["net_pnl"]) < 0]
    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    average_win = sum(winners) / len(winners) if winners else 0.0
    average_loss = sum(losers) / len(losers) if losers else 0.0
    return {
        "trade_count": len(trades),
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "win_rate": _clean(len(winners) / len(trades) * 100.0 if trades else 0.0),
        "gross_pnl": _clean(sum(float(trade["gross_pnl"]) for trade in trades)),
        "net_pnl": _clean(sum(float(trade["net_pnl"]) for trade in trades)),
        "profit_factor": _clean(gross_profit / gross_loss) if gross_loss > 0 else None,
        "expectancy": _clean(
            sum(float(trade["net_pnl"]) for trade in trades) / len(trades) if trades else 0.0
        ),
        "average_win": _clean(average_win),
        "average_loss": _clean(average_loss),
        "payoff_ratio": (
            _clean(average_win / abs(average_loss)) if winners and average_loss < 0 else None
        ),
    }


def _build_drawdown_series(equity_curve: list[dict[str, Any]]) -> list[dict[str, Any]]:
    peak: float | None = None
    output: list[dict[str, Any]] = []
    for point in equity_curve:
        equity = float(point["equity"])
        peak = equity if peak is None else max(peak, equity)
        drawdown = max(0.0, peak - equity)
        percent = drawdown / peak * 100.0 if peak > 0 else 0.0
        output.append(
            {
                "timestamp": point["timestamp"],
                "equity": _clean(equity),
                "drawdown_dollars": _clean(drawdown),
                "drawdown_percent": _clean(percent),
            }
        )
    return output


def _period_results(trades: list[dict[str, Any]], *, monthly: bool) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        timestamp = datetime.fromisoformat(str(trade["exit_timestamp"]))
        day = trading_day_date(timestamp)
        key = day.strftime("%Y-%m") if monthly else day.isoformat()
        grouped[key].append(trade)
    output: list[dict[str, Any]] = []
    for period in sorted(grouped):
        rows = grouped[period]
        output.append(
            {
                "period": period,
                "gross_pnl": _clean(sum(float(row["gross_pnl"]) for row in rows)),
                "net_pnl": _clean(sum(float(row["net_pnl"]) for row in rows)),
                "commission": _clean(sum(float(row["commission"]) for row in rows)),
                "trade_count": len(rows),
                "wins": sum(1 for row in rows if float(row["net_pnl"]) > 0),
                "losses": sum(1 for row in rows if float(row["net_pnl"]) < 0),
            }
        )
    return output


def _config_snapshot(config: BotConfig) -> dict[str, Any]:
    return {
        "id": int(config.id) if config.id is not None else None,
        "account_id": int(config.account_id),
        "name": str(config.name),
        "provider": str(config.provider),
        "enabled": bool(config.enabled),
        "execution_mode_at_run": str(config.execution_mode),
        "strategy_type": str(config.strategy_type),
        "strategy_params": _json_safe(config.strategy_params or {}),
        "contract_id": str(config.contract_id),
        "symbol": config.symbol,
        "timeframe_unit": str(config.timeframe_unit),
        "timeframe_unit_number": int(config.timeframe_unit_number),
        "lookback_bars": int(config.lookback_bars),
        "fast_period": int(config.fast_period),
        "slow_period": int(config.slow_period),
        "order_size": float(config.order_size),
        "max_contracts": float(config.max_contracts),
        "max_daily_loss": float(config.max_daily_loss),
        "max_trades_per_day": int(config.max_trades_per_day),
        "max_open_position": float(config.max_open_position),
        "allowed_contracts": _json_safe(config.allowed_contracts or []),
        "trading_start_time": str(config.trading_start_time),
        "trading_end_time": str(config.trading_end_time),
        "cooldown_seconds": int(config.cooldown_seconds),
        "max_data_staleness_seconds": int(config.max_data_staleness_seconds),
        "allow_market_depth": bool(config.allow_market_depth),
        "created_at": (
            _as_utc(config.created_at).isoformat() if config.created_at is not None else None
        ),
        "updated_at": (
            _as_utc(config.updated_at).isoformat() if config.updated_at is not None else None
        ),
    }


def _assumptions_snapshot(config: BotConfig, settings: BacktestSettings) -> dict[str, Any]:
    is_topbot = str(config.strategy_type) == _TOPBOT_STRATEGY
    return {
        "fill_model": "next_bar_open_market",
        "signal_timing": "strategy_evaluated_after_bar_close_using_only_then-closed_bars",
        "strategy_replay": (
            "synchronized_configured_source_ensemble" if is_topbot else "single_strategy"
        ),
        "source_synchronization": (
            "each_source_receives_only_its_bars_closed_by_the_primary_event"
            if is_topbot
            else "not_applicable"
        ),
        "synchronized_stream_count": len(_topbot_stream_specs(config)) if is_topbot else 1,
        "event_order": "resting_gap_brackets_then_pending_open_fill_then_intrabar_brackets_then_close_signal",
        "same_bar_exit_rule": "stop_first_when_stop_and_target_are_both_touched",
        "bracket_rule": "evaluator_levels_become_whole_tick_distances_anchored_to_actual_entry_fill",
        "gap_rule": "stops_fill_at_adverse_gap_open; targets receive no favorable price improvement",
        "final_position_handling": (
            "forced_close_at_last_bar_close" if settings.force_close_at_end else "left_open"
        ),
        "position_rule": (
            "signals_target_direction; same-side_duplicates_do_not_pyramid; opposites_reverse; "
            "oversized_entries_are_blocked_not_capped"
        ),
        "session_rule": (
            "TopBot signals are evaluated only during the configured session while resting "
            "brackets are processed on every execution candle; entries obey bot session "
            "after-loss cooldown, daily-trade, and daily-loss limits; "
            "counters_reset_at_requested_start_and_loss_uses_isolated_simulated_realized_net_pnl"
        ),
        "commission_rule": "commission_per_contract_is_charged_per_side",
        "slippage_rule": "configured_ticks_are_applied_adversely_to_every_entry_and_exit_fill",
        "pnl_rule": "price_delta_divided_by_tick_size_times_tick_value_times_quantity",
        "metric_basis": "profit_factor_expectancy_average_win_and_average_loss_use_net_trade_pnl",
        "market_data": (
            "preparation_may_fetch_exact_configured_contract; replay_uses_persisted_"
            "user_scoped_non_live_closed_candles_only"
        ),
        "live_order_routing": "disabled_by_architecture",
        "timezone": str(getattr(TRADING_TZ, "key", "America/New_York")),
        "commission_per_contract": _clean(settings.commission_per_contract),
        "slippage_ticks": _clean(settings.slippage_ticks),
        "tick_size": _clean(settings.tick_size),
        "tick_value": _clean(settings.tick_value),
        "engine_version": BACKTEST_ENGINE_VERSION,
        "configured_execution_mode_was_ignored": str(config.execution_mode),
    }


def _timeframe_seconds(unit: str, unit_number: int) -> int | None:
    seconds = _UNIT_SECONDS.get(unit)
    return seconds * unit_number if seconds is not None else None


def _count_futures_session_gaps(
    candles: list[ProjectXMarketCandle],
    *,
    interval_seconds: int,
) -> int:
    gaps = 0
    symbol = next(
        (
            str(value)
            for row in candles
            for value in (getattr(row, "symbol", None), getattr(row, "contract_id", None))
            if value is not None and str(value).strip()
        ),
        None,
    )
    for previous, current in zip(candles, candles[1:]):
        previous_timestamp = _as_utc(previous.candle_timestamp)
        current_timestamp = _as_utc(current.candle_timestamp)
        elapsed = int((current_timestamp - previous_timestamp).total_seconds())
        missing_slots = max(0, int(round(elapsed / interval_seconds)) - 1)
        if missing_slots and _contains_open_futures_timestamp(
            previous_timestamp + timedelta(seconds=interval_seconds),
            current_timestamp,
            step_seconds=interval_seconds,
            symbol=symbol,
        ):
            gaps += 1
    return gaps


def _has_open_futures_interval(
    start: datetime,
    end: datetime,
    *,
    interval_seconds: int,
) -> bool:
    cursor = _as_utc(start)
    end_utc = _as_utc(end)
    if end_utc - cursor <= timedelta(seconds=interval_seconds * 1.5):
        return False
    return _contains_open_futures_timestamp(
        cursor + timedelta(seconds=interval_seconds),
        end_utc,
        step_seconds=interval_seconds,
    )


def _has_missing_boundary_slot(
    start: datetime,
    end: datetime,
    *,
    interval_seconds: int,
) -> bool:
    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    if end_utc - start_utc < timedelta(seconds=interval_seconds):
        return False
    return _contains_open_futures_timestamp(
        start_utc,
        end_utc,
        step_seconds=interval_seconds,
    )


def _contains_open_futures_timestamp(
    start: datetime,
    end: datetime,
    *,
    step_seconds: int,
    symbol: str | None = None,
) -> bool:
    cursor = _as_utc(start)
    end_utc = _as_utc(end)
    # Session boundaries are minute-aligned, so sub-minute streams need not
    # scan every second across a weekend closure.
    step = timedelta(seconds=max(60, int(step_seconds)))
    while cursor < end_utc:
        if futures_session_is_open(cursor, symbol=symbol):
            return True
        cursor += step
    return False


def _max_evaluator_input_bars(config: BotConfig, *, rolling_limit: int) -> int:
    timeframe_seconds = _timeframe_seconds(
        str(config.timeframe_unit), int(config.timeframe_unit_number)
    )
    if timeframe_seconds is None or timeframe_seconds <= 0:
        return rolling_limit
    if str(config.strategy_type) in _TRADING_DAY_VWAP_STRATEGIES:
        # A New York trading day spans 25 real hours across the fall DST change.
        session_capacity = math.ceil((25 * 60 * 60) / timeframe_seconds) + 2
        return max(rolling_limit, session_capacity)
    if str(config.strategy_type) == "orb_fibonacci_pullback":
        start = _parse_time(str(config.trading_start_time))
        end = _parse_time(str(config.trading_end_time))
        start_seconds = start.hour * 3600 + start.minute * 60 + start.second
        end_seconds = end.hour * 3600 + end.minute * 60 + end.second
        duration = end_seconds - start_seconds
        if duration < 0:
            duration += 24 * 60 * 60
        # Overnight configured windows can also cross a fall DST transition.
        session_capacity = math.ceil((duration + 60 * 60) / timeframe_seconds) + 2
        return max(1, session_capacity)
    return rolling_limit


def _first_index_at_or_after(
    candles: list[ProjectXMarketCandle],
    timestamp: datetime,
) -> int:
    target = _as_utc(timestamp)
    low = 0
    high = len(candles)
    while low < high:
        middle = (low + high) // 2
        if _as_utc(candles[middle].candle_timestamp) < target:
            low = middle + 1
        else:
            high = middle
    return low


def _topbot_outside_session_signal(
    strategy_type: str,
    *,
    event_timestamp: datetime,
) -> SignalResult:
    return SignalResult(
        action="HOLD",
        reason="Outside the configured trading session.",
        candle_timestamp=_as_utc(event_timestamp),
        price=None,
        raw_payload={
            "strategy_type": strategy_type,
            "outside_configured_session": True,
        },
    )


def _event_timestamp_is_in_configured_session(
    config: BotConfig,
    event_timestamp: datetime,
) -> bool:
    timestamp = _as_utc(event_timestamp)
    if not futures_session_is_open(timestamp):
        return False
    session_start, session_end = _session_window_utc_for_reference(
        timestamp,
        start_text=str(config.trading_start_time),
        end_text=str(config.trading_end_time),
    )
    return session_start <= timestamp <= session_end


def _require_stream_current_through_event(
    candles: list[ProjectXMarketCandle],
    *,
    event_time: datetime,
    interval_seconds: int,
    strategy_type: str,
) -> None:
    if not candles:
        return
    expected_latest_start = _as_utc(event_time) - timedelta(seconds=interval_seconds)
    actual_latest_start = _as_utc(candles[-1].candle_timestamp)
    if actual_latest_start != expected_latest_start:
        raise InsufficientBacktestDataError(
            "insufficient_backtest_data: incomplete_session_history: "
            f"{strategy_type} is missing the closed candle at "
            f"{expected_latest_start.isoformat()}"
        )


def _require_complete_session_prefix(
    candles: list[ProjectXMarketCandle],
    *,
    expected_start: datetime,
    strategy_type: str,
    enforce: bool,
    expected_interval_seconds: int | None,
) -> None:
    if not enforce:
        return
    if not candles or _as_utc(candles[0].candle_timestamp) != _as_utc(expected_start):
        raise InsufficientBacktestDataError(
            "insufficient_backtest_data: incomplete_session_history: "
            f"{strategy_type} requires the session-opening candle at "
            f"{_as_utc(expected_start).isoformat()}"
        )
    if expected_interval_seconds is None:
        return
    for previous, current in zip(candles, candles[1:]):
        delta = (
            _as_utc(current.candle_timestamp) - _as_utc(previous.candle_timestamp)
        ).total_seconds()
        if delta != expected_interval_seconds:
            raise InsufficientBacktestDataError(
                "insufficient_backtest_data: incomplete_session_history: "
                f"{strategy_type} has a missing session candle after "
                f"{_as_utc(previous.candle_timestamp).isoformat()}"
            )


def _require_complete_orb_opening_range(
    candles: list[ProjectXMarketCandle],
    *,
    config: BotConfig,
    session_start: datetime,
    latest_timestamp: datetime,
) -> None:
    params = get_strategy_definition("orb_fibonacci_pullback").parameter_normalizer(
        config.strategy_params
    )
    opening_minutes = int(params["opening_range_minutes"])
    range_end = _as_utc(session_start) + timedelta(minutes=opening_minutes)
    if _as_utc(latest_timestamp) < range_end:
        return
    timeframe_seconds = _timeframe_seconds(
        str(config.timeframe_unit), int(config.timeframe_unit_number)
    )
    if timeframe_seconds is None or timeframe_seconds <= 0:
        raise MalformedBacktestDataError("orb_fibonacci_pullback requires an intraday timeframe")
    expected_bars = math.ceil(opening_minutes * 60 / timeframe_seconds)
    if len(candles) < expected_bars:
        raise InsufficientBacktestDataError(
            "insufficient_backtest_data: incomplete_session_history: "
            "orb_fibonacci_pullback opening range is incomplete"
        )
    for index in range(expected_bars):
        expected = _as_utc(session_start) + timedelta(seconds=timeframe_seconds * index)
        actual = _as_utc(candles[index].candle_timestamp)
        if actual != expected:
            raise InsufficientBacktestDataError(
                "insufficient_backtest_data: incomplete_session_history: "
                f"orb_fibonacci_pullback is missing opening-range candle {expected.isoformat()}"
            )


def _is_intraday_timeframe(config: BotConfig) -> bool:
    return str(config.timeframe_unit) in {"second", "minute", "hour"}


def _finite_optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def _clean(value: float) -> float:
    rounded = round(float(value), 10)
    return 0.0 if rounded == 0 else rounded


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
