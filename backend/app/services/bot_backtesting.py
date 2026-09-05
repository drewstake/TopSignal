from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from time import monotonic
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Callable, Iterable, Mapping

import numpy as np
from sqlalchemy import inspect, or_
from sqlalchemy.orm import Session

from ..models import BotBacktest, BotConfig, ProjectXMarketCandle
from . import bot_service as bot_service_module
from .bot_candle_acquisition import (
    _SourceConfigView,
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
from .databento_market_data import (
    ROLL_POLICY_VERSION,
    DatabentoMarketDataError,
    databento_history_bounds,
    load_databento_replay_candles,
)
from .databento_cache import (
    DatabentoCacheError,
    DatabentoCacheMissingError,
    DatabentoCacheStaleError,
    DatabentoReplayStore,
    get_default_databento_cache,
)
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


BACKTEST_ENGINE_VERSION = "5.0.0-topbot-bracket-exits"
LEGACY_PROJECTX_BACKTEST_ENGINE_VERSION = "1.3.0"
# Unit tests for the pre-Databento engine can opt in by monkeypatching this
# process-local constant. It is deliberately not environment-configurable and
# is false in every application runtime.
ALLOW_LEGACY_PROJECTX_BACKTEST_FIXTURES = False
# The retired relational Databento loader is likewise available only to
# explicit SQLite compatibility tests. Merely retaining old market tables must
# never make an application request bypass the canonical local cache.
ALLOW_LEGACY_DATABENTO_SQLITE_FIXTURES = False
BACKTEST_INSTRUMENTS = frozenset({"MNQ", "MES", "NQ", "ES"})
# Retained as a compatibility constant for callers/tests that display the old
# limit. It is deliberately not used to truncate or reject replay history.
MAX_BACKTEST_BARS = 20_000
MAX_PROVIDER_FETCH_BARS = 20_000
MAX_BACKTEST_PROVIDER_REQUESTS = 40
CANDLE_QUERY_CHUNK_SIZE = 8_192
_DEFAULT_BACKTEST_MEMORY_BUDGET_BYTES = 1_536 * 1024 * 1024
# cProfile showed CPU time scaling with evaluator-visible candle visits.  This
# configurable ceiling replaces date/bar caps while rejecting pathological
# repeated-indicator workloads before replay or persistence.
_DEFAULT_BACKTEST_EVALUATOR_WORK_BUDGET = 1_000_000_000
_DEFAULT_BACKTEST_MAX_SERIES_POINTS = 50_000
try:
    BACKTEST_MEMORY_BUDGET_BYTES = int(
        os.getenv(
            "TOPSIGNAL_BACKTEST_MEMORY_BUDGET_BYTES",
            str(_DEFAULT_BACKTEST_MEMORY_BUDGET_BYTES),
        )
    )
except ValueError:
    BACKTEST_MEMORY_BUDGET_BYTES = _DEFAULT_BACKTEST_MEMORY_BUDGET_BYTES
try:
    BACKTEST_EVALUATOR_WORK_BUDGET = int(
        os.getenv(
            "TOPSIGNAL_BACKTEST_EVALUATOR_WORK_BUDGET",
            str(_DEFAULT_BACKTEST_EVALUATOR_WORK_BUDGET),
        )
    )
except ValueError:
    BACKTEST_EVALUATOR_WORK_BUDGET = _DEFAULT_BACKTEST_EVALUATOR_WORK_BUDGET
try:
    BACKTEST_MAX_SERIES_POINTS = int(
        os.getenv(
            "TOPSIGNAL_BACKTEST_MAX_SERIES_POINTS",
            str(_DEFAULT_BACKTEST_MAX_SERIES_POINTS),
        )
    )
except ValueError:
    BACKTEST_MAX_SERIES_POINTS = _DEFAULT_BACKTEST_MAX_SERIES_POINTS

# Calibrated with tracemalloc and the projected-row benchmark. These estimates
# intentionally include Python container overhead and the two per-bar result
# series, not only raw OHLCV payload bytes.
ESTIMATED_REPLAY_CANDLE_BYTES = 640
ESTIMATED_EXECUTION_RESULT_BYTES = 704
# Lazy mmap replays do not retain an ORM/proxy object or timestamp object per
# execution row.  This allowance covers bounded replay bookkeeping and the
# possibility of unusually dense trade output; mapped array storage is charged
# separately from the immutable sequence metadata.
ESTIMATED_LAZY_EXECUTION_BYTES = 64
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
_TRADING_DAY_VWAP_STRATEGIES = {
    "vwap_atr_mean_reversion",
    "bollinger_mean_reversion",
    "bollinger_rsi_reversal",
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


class BacktestSupersededError(BacktestError):
    """Raised when a newer backtest replaces an active run for the same user."""

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
    candles: Sequence[ProjectXMarketCandle]
    start_times: Sequence[datetime]
    close_times: Sequence[datetime]


class _TopBotSourceEvaluationCounts(dict[str, int]):
    """Evaluation counts plus strategy-aware expensive-window counts."""

    def __init__(
        self,
        *args: Any,
        expensive_evaluations: Mapping[str, int] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.expensive_evaluations = dict(expensive_evaluations or {})


class _NanosecondDatetimeSequence(Sequence[datetime]):
    """A zero-copy datetime facade over a sliced mmap nanosecond array."""

    __slots__ = ("_values",)

    def __init__(self, values: Any) -> None:
        self._values = values

    def __len__(self) -> int:
        return len(self._values)

    def __getitem__(self, index: int | slice) -> datetime | "_NanosecondDatetimeSequence":
        if isinstance(index, slice):
            return _NanosecondDatetimeSequence(self._values[index])
        return _datetime_from_epoch_ns(int(self._values[index]))


class _ScaledNanoPriceSequence(Sequence[float]):
    """Expose mmap fixed-point closes with the eager candle float semantics."""

    __slots__ = ("_values",)

    def __init__(self, values: Any) -> None:
        self._values = values

    def __len__(self) -> int:
        return len(self._values)

    def __getitem__(self, index: int | slice) -> float | list[float]:
        if isinstance(index, slice):
            return [float(value) / 1_000_000_000 for value in self._values[index]]
        return float(self._values[index]) / 1_000_000_000


class _SessionMembershipSequence(Sequence[bool]):
    """Compute configured-session membership on demand without a bool list."""

    __slots__ = ("_timestamps", "_predicate")

    def __init__(
        self,
        timestamps: Sequence[datetime],
        predicate: Callable[[datetime], bool],
    ) -> None:
        self._timestamps = timestamps
        self._predicate = predicate

    def __len__(self) -> int:
        return len(self._timestamps)

    def __getitem__(self, index: int | slice) -> bool | list[bool]:
        if isinstance(index, slice):
            return [self._predicate(value) for value in self._timestamps[index]]
        return self._predicate(self._timestamps[index])


class _ConstantBoolSequence(Sequence[bool]):
    __slots__ = ("_length", "_value")

    def __init__(self, length: int, value: bool) -> None:
        self._length = max(0, int(length))
        self._value = bool(value)

    def __len__(self) -> int:
        return self._length

    def __getitem__(self, index: int | slice) -> bool | list[bool]:
        if isinstance(index, slice):
            start, stop, step = index.indices(self._length)
            return [self._value] * len(range(start, stop, step))
        if index < 0:
            index += self._length
        if index < 0 or index >= self._length:
            raise IndexError(index)
        return self._value


class _ClosedCandleList(list[ProjectXMarketCandle]):
    """Internal proof that a replay window is already closed and ordered."""

    _topsignal_sorted_closed = True
    _topsignal_physical_stream: tuple[str, str, str, int] | None = None
    _topsignal_physical_row_count: int | None = None
    _topsignal_input_fingerprint: str | None = None
    _topsignal_series_fingerprint: str | None = None
    _topsignal_slice_start: int | None = None
    _topsignal_slice_end: int | None = None
    _topsignal_verified_replay: bool = False
    _topsignal_user_id: str | None = None
    _topsignal_contract_id: str | None = None
    _topsignal_symbol: str | None = None
    _topsignal_unit: str | None = None
    _topsignal_unit_number: int | None = None


class _PhysicalReplayList(list[ProjectXMarketCandle]):
    """Projected rows that share one physical query but still need validation."""

    _topsignal_physical_stream: tuple[str, str, str, int] | None = None
    _topsignal_physical_row_count: int | None = None


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
    decision_timestamp: datetime
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
BacktestProgressCallback = Callable[[dict[str, Any]], None]
BacktestCancellationCallback = Callable[[], bool]


def _raise_if_backtest_cancelled(
    callback: BacktestCancellationCallback | None,
) -> None:
    if callback is not None and callback():
        raise BacktestSupersededError("backtest_superseded_by_newer_run")


def _backtest_cache_json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return _as_utc(value).isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    raise TypeError(f"unsupported backtest cache value: {type(value).__name__}")


class _BacktestResultLru:
    """Bound deterministic replay outputs so exact warm reruns skip replay CPU."""

    def __init__(self) -> None:
        try:
            self.max_entries = max(
                1, int(os.getenv("TOPSIGNAL_BACKTEST_RESULT_CACHE_MAX_ENTRIES", "8"))
            )
        except ValueError:
            self.max_entries = 8
        try:
            self.max_bytes = max(
                1,
                int(
                    os.getenv(
                        "TOPSIGNAL_BACKTEST_RESULT_CACHE_MAX_BYTES",
                        str(256 * 1024 * 1024),
                    )
                ),
            )
        except ValueError:
            self.max_bytes = 256 * 1024 * 1024
        self._lock = threading.RLock()
        self._entries: dict[str, tuple[dict[str, Any], int]] = {}
        self._order: list[str] = []
        self._bytes = 0

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._order.remove(key)
            self._order.append(key)
            return deepcopy(entry[0])

    def put(self, key: str, result: dict[str, Any]) -> None:
        canonical = json.dumps(
            result,
            allow_nan=False,
            default=_backtest_cache_json_default,
            separators=(",", ":"),
            sort_keys=True,
        )
        # JSON bytes undercount Python dict/list/string overhead; a 3x charge
        # keeps the configured ceiling conservative without walking every node.
        size = len(canonical.encode("utf-8")) * 3
        if size > self.max_bytes:
            return
        stored = deepcopy(result)
        with self._lock:
            prior = self._entries.pop(key, None)
            if prior is not None:
                self._bytes -= prior[1]
                self._order.remove(key)
            self._entries[key] = (stored, size)
            self._order.append(key)
            self._bytes += size
            while self._order and (
                len(self._order) > self.max_entries or self._bytes > self.max_bytes
            ):
                oldest = self._order.pop(0)
                _value, evicted_size = self._entries.pop(oldest)
                self._bytes -= evicted_size


_BACKTEST_RESULT_CACHE = _BacktestResultLru()


def _notify_backtest_progress(
    callback: BacktestProgressCallback | None,
    **progress: Any,
) -> None:
    if callback is None:
        return
    try:
        callback(progress)
    except Exception:
        # Progress reporting is advisory and must never alter replay results.
        return


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
        progress_callback: BacktestProgressCallback | None = None,
        cancellation_callback: BacktestCancellationCallback | None = None,
    ) -> None:
        if str(config.strategy_type) == _TOPBOT_STRATEGY:
            config = _SourceConfigView(config, strategy_type=_TOPBOT_STRATEGY,
                strategy_params=bot_service_module._normalize_strategy_params(_TOPBOT_STRATEGY, config.strategy_params),
                fast_period=int(config.fast_period), slow_period=int(config.slow_period))
        self.config = config
        self.progress_callback = progress_callback
        self.cancellation_callback = cancellation_callback
        _raise_if_backtest_cancelled(self.cancellation_callback)
        self._last_replay_progress_percent = -1
        self._last_replay_progress_at = 0.0
        self.settings = _validate_settings(settings)
        self.strategy_type = str(config.strategy_type)
        _require_supported_strategy(self.strategy_type)
        _validate_replay_configuration(config)
        uses_real_evaluator = signal_evaluator is None
        self._uses_real_evaluator = uses_real_evaluator
        self.signal_evaluator = signal_evaluator or self._evaluate_real_strategy

        self.all_candles, excluded_partial = _validate_and_sort_candles(candles, config=config)
        _raise_if_backtest_cancelled(self.cancellation_callback)
        self.all_start_times = _candle_time_sequence(self.all_candles, close=False)
        self.all_close_times = _candle_time_sequence(self.all_candles, close=True)
        self._sma_close_values = (
            (
                _ScaledNanoPriceSequence(self.all_candles.close_nano_values)
                if _is_mmap_candle_sequence(self.all_candles)
                else [float(candle.close_price) for candle in self.all_candles]
            )
            if uses_real_evaluator
            and self.strategy_type == "sma_cross"
            and evaluate_sma_cross is bot_service_module.evaluate_sma_cross
            else None
        )
        self.topbot_streams: dict[str, _PreparedReplayStream] = {}
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
            _raise_if_backtest_cancelled(self.cancellation_callback)

        execution_start = _search_candle_start(
            self.all_candles,
            self.settings.start,
            side="left",
            fallback=self.all_start_times,
        )
        execution_end = _search_candle_close(
            self.all_candles,
            self.settings.end,
            side="right",
            fallback=self.all_close_times,
        )
        self.execution_candles = self.all_candles[execution_start:execution_end]
        self.execution_start_times = self.all_start_times[execution_start:execution_end]
        self.execution_close_times = self.all_close_times[execution_start:execution_end]
        if len(self.execution_candles) < MIN_EXECUTION_BARS:
            raise InsufficientBacktestDataError(
                "insufficient_backtest_data: at least 2 closed execution bars are required "
                f"inside the requested range; found {len(self.execution_candles)}"
            )
        # Coverage starts at the first stored bar, before any warmup deferral.
        self.available_start = self.execution_start_times[0]
        if uses_real_evaluator and self.strategy_type != "orb_fibonacci_pullback":
            hard_minimum = _strategy_history_bars(config, hard_minimum=True)
            first_event = self.execution_close_times[0]
            closed_by_first_event = _search_candle_close(
                self.all_candles,
                first_event,
                side="right",
                fallback=self.all_close_times,
            )
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
                    available_closed_bars = _search_candle_close(
                        self.all_candles,
                        self.settings.end,
                        side="right",
                        fallback=self.all_close_times,
                    )
                    raise InsufficientBacktestDataError(
                        "insufficient_backtest_data: insufficient_strategy_warmup: "
                        f"{self.strategy_type} requires at least {hard_minimum} closed bars; "
                        f"only {available_closed_bars} were available"
                    )
        self.execution_in_session: Sequence[bool] = (
            np.fromiter(
                (
                    self._event_in_configured_session(event_time)
                    for event_time in self.execution_start_times
                ),
                dtype=np.bool_,
                count=len(self.execution_start_times),
            )
            if self.strategy_type == _TOPBOT_STRATEGY
            else _ConstantBoolSequence(len(self.execution_candles), True)
        )
        self.evaluator_history_limit = max(
            int(config.lookback_bars),
            _strategy_history_bars(config, hard_minimum=False),
        )
        self.max_evaluator_input_bars = _max_evaluator_input_bars(
            config,
            rolling_limit=self.evaluator_history_limit,
        )
        if self._sma_close_values is not None:
            estimated_evaluator_work = len(self.execution_candles) * (
                2 * int(config.fast_period) + 2 * int(config.slow_period)
            )
        else:
            estimated_evaluator_work = len(self.execution_candles) * min(
                len(self.all_candles),
                self.max_evaluator_input_bars,
            )
        _enforce_backtest_work_budget(estimated_evaluator_work)
        _raise_if_backtest_cancelled(self.cancellation_callback)

        budget_streams: list[Sequence[ProjectXMarketCandle]] = [self.all_candles]
        if self.topbot_streams:
            primary_stream_key = _topbot_asset_stream_key(
                str(config.timeframe_unit), int(config.timeframe_unit_number)
            )
            budget_streams.extend(
                stream.candles
                for key, stream in self.topbot_streams.items()
                if key != primary_stream_key
            )
        replay_storage_bytes, replay_eager_rows = _replay_storage_estimate(
            budget_streams
        )
        _enforce_backtest_resource_budget(
            replay_rows=replay_eager_rows,
            execution_rows=len(self.execution_candles),
            replay_storage_bytes=replay_storage_bytes,
            lazy_execution=_is_mmap_candle_sequence(self.execution_candles),
        )

        self.warnings: list[str] = []
        self.notes: list[str] = []
        self.data_quality: dict[str, Any] = {}
        if excluded_partial:
            self.warnings.append(f"Excluded {excluded_partial} partial candle(s); only closed bars were replayed.")
        if auxiliary_excluded_partial:
            self.warnings.append(
                f"Excluded {auxiliary_excluded_partial} partial auxiliary candle(s); only closed bars were replayed."
            )
        if deferred_execution_bars:
            self.notes.append(
                f"Used the first {deferred_execution_bars} candle(s) for warmup; "
                f"replay begins at {self.execution_start_times[0].isoformat()} after "
                "the required closed bars became available."
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
        self.drawdown_series: list[dict[str, Any]] = [
            {
                "timestamp": self.settings.start.isoformat(),
                "equity": _clean(self.settings.starting_balance),
                "drawdown_dollars": 0.0,
                "drawdown_percent": 0.0,
            }
        ]
        self._equity_observation_count = 0
        self._equity_sample_stride = max(
            1,
            math.ceil(
                len(self.execution_candles)
                / max(2, int(BACKTEST_MAX_SERIES_POINTS) - 1)
            ),
        )
        self._equity_peak = float(self.settings.starting_balance)
        self._max_drawdown_dollars = 0.0
        self._max_drawdown_percent = 0.0
        if self._equity_sample_stride > 1:
            self.notes.append(
                "Equity and drawdown output was deterministically sampled to bound replay memory; "
                "trade metrics and maximum drawdown still use every replay bar."
            )
        self.exposed_bar_count = 0
        self._current_bar_exposed = False
        self.daily_entry_counts: dict[Any, int] = defaultdict(int)
        self.daily_net_activity: dict[Any, float] = defaultdict(float)
        self.last_loss_at: datetime | None = None
        self.block_counts: dict[str, int] = defaultdict(int)
        self.unfilled_final_signals = 0
        self.delivery_roll_count = 0
        self.delivery_roll_forced_exit_count = 0
        self.delivery_roll_discarded_signal_count = 0
        self._delivery_history_floor = 0
        self._history_delivery_identity: tuple[str | None, int | None] | None = None
        self._emitted_topbot_signal_identities: set[tuple[str, datetime]] = set()

    def run(self) -> dict[str, Any]:
        closed_history: list[ProjectXMarketCandle] = (
            _ClosedCandleList() if self._uses_real_evaluator else []
        )
        history_cursor = 0
        total_bars = len(self.execution_candles)
        self._report_replay_progress(completed=0, total=total_bars)
        all_close_ns = (
            self.all_candles.close_ns
            if _is_mmap_candle_sequence(self.all_candles)
            else None
        )
        for index in range(total_bars):
            _raise_if_backtest_cancelled(self.cancellation_callback)
            candle = self.execution_candles[index]
            candle_start = self.execution_start_times[index]
            event_time = self.execution_close_times[index]
            inside_session = self.execution_in_session[index]
            self._current_bar_exposed = False
            self._current_event_timestamp = candle_start
            self._current_event_in_session = inside_session

            if index > 0:
                self._handle_delivery_change(index=index)

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

            event_ns = _datetime_to_epoch_ns(event_time)
            while history_cursor < len(self.all_candles) and (
                int(all_close_ns[history_cursor]) <= event_ns
                if all_close_ns is not None
                else self.all_close_times[history_cursor] <= event_time
            ):
                history_candle = self.all_candles[history_cursor]
                history_delivery = _candle_delivery_identity(history_candle)
                if (
                    self._history_delivery_identity is not None
                    and history_delivery is not None
                    and history_delivery != self._history_delivery_identity
                ):
                    closed_history.clear()
                    self._delivery_history_floor = history_cursor
                if history_delivery is not None:
                    self._history_delivery_identity = history_delivery
                if self._sma_close_values is None:
                    closed_history.append(history_candle)
                history_cursor += 1
            if (
                self._uses_real_evaluator
                and len(closed_history) > self.max_evaluator_input_bars
            ):
                del closed_history[: -self.max_evaluator_input_bars]
            if self.strategy_type == _TOPBOT_STRATEGY and not inside_session:
                self._record_equity(event_time=event_time, mark_price=float(candle.close_price))
                self._report_replay_progress(completed=index + 1, total=total_bars)
                continue
            if self._sma_close_values is not None:
                signal = self._evaluate_prepared_sma(history_cursor)
            else:
                signal = self.signal_evaluator(self._evaluator_input(closed_history))
            if signal.action in {"BUY", "SELL"}:
                if not _inside_session(
                    event_time,
                    start_text=str(self.config.trading_start_time),
                    end_text=str(self.config.trading_end_time),
                ):
                    self.block_counts["outside_session"] += 1
                    self._record_equity(
                        event_time=event_time,
                        mark_price=float(candle.close_price),
                    )
                    self._report_replay_progress(completed=index + 1, total=total_bars)
                    continue
                source_signal_timestamp = (
                    _as_utc(signal.candle_timestamp)
                    if self.strategy_type == _TOPBOT_STRATEGY and signal.candle_timestamp is not None
                    else candle_start
                )
                if self.strategy_type == _TOPBOT_STRATEGY:
                    signal_identity = (str(signal.action), source_signal_timestamp)
                    if signal_identity in self._emitted_topbot_signal_identities:
                        self._record_equity(event_time=event_time, mark_price=float(candle.close_price))
                        self._report_replay_progress(completed=index + 1, total=total_bars)
                        continue
                    self._emitted_topbot_signal_identities.add(signal_identity)
                self.pending = _PendingSignal(
                    action=signal.action,
                    signal_timestamp=candle_start,
                    decision_timestamp=event_time,
                    signal_price=float(signal.price) if signal.price is not None else None,
                    reason=signal.reason,
                    payload=dict(signal.raw_payload) if isinstance(signal.raw_payload, dict) else {},
                )

            self._record_equity(event_time=event_time, mark_price=float(candle.close_price))
            self._report_replay_progress(completed=index + 1, total=total_bars)

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
        _raise_if_backtest_cancelled(self.cancellation_callback)
        drawdown_series = self.drawdown_series
        metrics = _build_metrics(
            self.trades,
            equity_curve=self.equity_curve,
            drawdown_series=drawdown_series,
            max_drawdown_dollars=self._max_drawdown_dollars,
            max_drawdown_percent=self._max_drawdown_percent,
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
            "notes": self.notes,
            "data_quality": self.data_quality,
        }

    def _report_replay_progress(self, *, completed: int, total: int) -> None:
        if self.progress_callback is None:
            return
        percent = min(100, max(0, int(completed * 100 / max(1, total))))
        now = monotonic()
        if percent == self._last_replay_progress_percent and now - self._last_replay_progress_at < 1.0:
            return
        self._last_replay_progress_percent = percent
        self._last_replay_progress_at = now
        _notify_backtest_progress(
            self.progress_callback,
            phase="replaying",
            completed=int(completed),
            total=int(total),
            percent=percent,
            remaining_percent=100 - percent,
        )


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
        segment_count = max(0, closed_count - self._delivery_history_floor)
        visible_count = min(segment_count, self.evaluator_history_limit)
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

    def _handle_delivery_change(self, *, index: int) -> None:
        previous_candle = self.execution_candles[index - 1]
        current_candle = self.execution_candles[index]
        previous_delivery = _candle_delivery_identity(previous_candle)
        current_delivery = _candle_delivery_identity(current_candle)
        if (
            previous_delivery is None
            or current_delivery is None
            or previous_delivery == current_delivery
        ):
            return

        self.delivery_roll_count += 1
        if self.pending is not None:
            self.pending = None
            self.delivery_roll_discarded_signal_count += 1
        if self.position is None:
            return

        previous_close = float(previous_candle.close_price)
        previous_close_time = self.execution_close_times[index - 1]
        self._update_excursion(previous_close, previous_close)
        self._close_position(
            raw_exit_price=previous_close,
            exit_timestamp=previous_close_time,
            exit_reason="contract_roll",
        )
        self.delivery_roll_forced_exit_count += 1


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

    def _evaluate_topbot_adaptive(self, candles) -> SignalResult:
        return bot_service_module.evaluate_topbot_adaptive(candles, strategy_params=self.config.strategy_params)





    def _fill_pending_signal(
        self,
        pending: _PendingSignal,
        *,
        candle: ProjectXMarketCandle,
    ) -> None:
        fill_time = _as_utc(candle.candle_timestamp)
        if not _signal_fill_is_in_same_session(
            pending.decision_timestamp,
            fill_time,
            start_text=str(self.config.trading_start_time),
            end_text=str(self.config.trading_end_time),
        ):
            self.block_counts["stale_session_signal"] += 1
            return
        desired_side = "long" if pending.action == "BUY" else "short"
        signal_category = str(pending.payload.get("signal_category") or "entry")
        is_exit_only = signal_category == "exit"
        target_position_qty = _finite_optional_float(
            pending.payload.get("target_position_qty")
        )

        if self.position is not None and self.position.side != desired_side:
            current_position_qty = (
                self.position.quantity if self.position.side == "long" else -self.position.quantity
            )
            if (
                target_position_qty is not None
                and target_position_qty * current_position_qty < -(1e-9**2)
            ):
                # Live routing cannot attach authoritative bracket children to a
                # market order that both flattens and enters the other side.
                # Preserve parity by leaving the position unchanged; a later
                # evaluation may emit the required flatten-only target.
                self.block_counts["atomic_reversal_not_supported"] += 1
                return
            self._current_bar_exposed = True
            self._update_excursion(float(candle.open_price), float(candle.open_price))
            exit_reason = str(
                pending.payload.get("exit_reason") or "opposite_signal_flatten"
            )
            self._close_position(
                raw_exit_price=float(candle.open_price),
                exit_timestamp=fill_time,
                exit_reason=exit_reason,
            )
            # A target-less strategy signal is one market-order delta. Against
            # an equal-size position that delta flattens; it does not also open
            # a new position. Target-aware signals either flatten to zero above
            # or are blocked as unsupported atomic reversals.
            return

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
        timestamp = _as_utc(event_time).isoformat()
        equity = _clean(self.cash + unrealized)
        self._equity_peak = max(self._equity_peak, float(equity))
        drawdown = max(0.0, self._equity_peak - float(equity))
        drawdown_percent = (
            drawdown / self._equity_peak * 100.0 if self._equity_peak > 0 else 0.0
        )
        self._max_drawdown_dollars = max(self._max_drawdown_dollars, drawdown)
        self._max_drawdown_percent = max(self._max_drawdown_percent, drawdown_percent)
        self._equity_observation_count += 1
        should_sample = (
            self._equity_observation_count % self._equity_sample_stride == 0
            or self._equity_observation_count == len(self.execution_candles)
        )
        if should_sample:
            self.equity_curve.append(
                {
                    "timestamp": timestamp,
                    "equity": equity,
                    "realized_pnl": _clean(realized),
                    "unrealized_pnl": _clean(unrealized),
                }
            )
            self.drawdown_series.append(
                {
                    "timestamp": timestamp,
                    "equity": equity,
                    "drawdown_dollars": _clean(drawdown),
                    "drawdown_percent": _clean(drawdown_percent),
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
            self._equity_peak = max(self._equity_peak, float(point["equity"]))
            drawdown = max(0.0, self._equity_peak - float(point["equity"]))
            drawdown_percent = (
                drawdown / self._equity_peak * 100.0 if self._equity_peak > 0 else 0.0
            )
            self._max_drawdown_dollars = max(self._max_drawdown_dollars, drawdown)
            self._max_drawdown_percent = max(self._max_drawdown_percent, drawdown_percent)
            self.drawdown_series[-1] = {
                "timestamp": point["timestamp"],
                "equity": point["equity"],
                "drawdown_dollars": _clean(drawdown),
                "drawdown_percent": _clean(drawdown_percent),
            }
        else:
            self.equity_curve.append(point)

    def _add_data_quality_warnings(self) -> None:
        closed_count = _search_candle_close(
            self.all_candles, self.execution_close_times[0], side="right",
            fallback=self.all_close_times,
        )
        warmup_count = closed_count - _contiguous_delivery_start(
            self.all_candles, start_index=0, end_index=closed_count,
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
                f"Only {warmup_count} of {requested_warmup} configured warmup bars "
                "were closed in the current delivery at the first replay evaluation."
            )
        else:
            self.notes.append(
                f"Warmup ready: {requested_warmup} of {requested_warmup} required "
                "closed bars were available at the first replay evaluation."
            )
        self.data_quality.update({
            "available_start": self.available_start.isoformat(),
            "first_evaluation": self.execution_close_times[0].isoformat(),
            "warmup_required": requested_warmup,
            "warmup_available": min(warmup_count, requested_warmup),
        })
        if len(self.execution_candles) < 100:
            self.warnings.append(
                f"Small bar sample ({len(self.execution_candles)} bars); performance estimates may be unstable."
            )
        zero_volume = _zero_volume_count(self.execution_candles)
        if zero_volume:
            self.warnings.append(f"{zero_volume} execution candle(s) have zero volume.")
        expected_seconds = _timeframe_seconds(
            str(self.config.timeframe_unit), int(self.config.timeframe_unit_number)
        )
        if expected_seconds is not None:
            actual_start = self.available_start
            actual_end = self.execution_close_times[-1]
            if _has_open_futures_interval(
                self.settings.start,
                actual_start,
                interval_seconds=expected_seconds,
            ):
                self.warnings.append(
                    "Stored candles began after the requested start; the first complete candle is at "
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
            gaps = _summarize_futures_session_gaps(
                self.execution_candles,
                interval_seconds=expected_seconds,
                in_entry_session=self._event_in_configured_session,
            )
            self.data_quality["gaps"] = gaps
            if gaps["gap_count"]:
                self.warnings.append(
                    f"{gaps['gap_count']:,} gap(s) in complete candles; "
                    f"{gaps['in_session_gap_count']:,} overlap the configured entry hours. "
                    "The engine used the next available bar without interpolation. "
                    "See candle coverage below for dates and missing-bar counts."
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
                auxiliary_warmup = min(
                    _search_candle_start(
                        stream.candles,
                        self.settings.start,
                        side="left",
                        fallback=stream.start_times,
                    ),
                    _search_candle_close(
                        stream.candles,
                        first_event,
                        side="right",
                        fallback=stream.close_times,
                    ),
                )
                if auxiliary_warmup < spec.warmup_bars:
                    self.warnings.append(
                        f"TopBot replay stream {key} has {auxiliary_warmup} of "
                        f"{spec.warmup_bars} configured warmup bars."
                    )
                expected = _timeframe_seconds(spec.unit, spec.unit_number)
                if expected is None:
                    continue
                execution_start = _search_candle_start(
                    stream.candles,
                    self.settings.start,
                    side="left",
                    fallback=stream.start_times,
                )
                execution_end = _search_candle_close(
                    stream.candles,
                    self.settings.end,
                    side="right",
                    fallback=stream.close_times,
                )
                execution_rows = _closed_candle_slice(
                    stream.candles, execution_start, execution_end
                )
                gaps = _count_futures_session_gaps(
                    execution_rows,
                    interval_seconds=expected,
                )
                if gaps:
                    self.warnings.append(
                        f"TopBot replay stream {key} has {gaps} candle gap(s); no bars were interpolated."
                    )

    def _append_run_warnings(self) -> None:
        if self.delivery_roll_count:
            self.notes.append(
                f"Detected {self.delivery_roll_count} futures delivery change(s); "
                "strategy history was segmented at each roll, "
                f"{self.delivery_roll_forced_exit_count} open position(s) were closed at the "
                "prior delivery's final close, and "
                f"{self.delivery_roll_discarded_signal_count} pending signal(s) were discarded."
            )
        if self.unfilled_final_signals:
            self.notes.append(
                "The final-bar signal was not filled because no next bar existed in the test range."
            )
        for code in sorted(self.block_counts):
            count = self.block_counts[code]
            label = code.replace("_", " ")
            self.notes.append(f"Blocked {count} replay signal(s) due to {label}.")
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
    from .topbot_strategy import HISTORY_BARS
    key = _topbot_asset_stream_key("minute", 5)
    return {key: _TopBotReplayStreamSpec(
        key=key, unit="minute", unit_number=5, warmup_bars=HISTORY_BARS,
        contract_id=str(config.contract_id), symbol=config.symbol,
    )}




def _prepare_topbot_replay_streams(
    config: BotConfig,
    *,
    primary_candles: Sequence[ProjectXMarketCandle],
    replay_streams: Mapping[str, Sequence[ProjectXMarketCandle]] | None,
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
            start_times=_candle_time_sequence(rows, close=False),
            close_times=_candle_time_sequence(rows, close=True),
        )
    return prepared, excluded_partial


def _validate_topbot_replay_stream(
    candles: Sequence[ProjectXMarketCandle],
    *,
    spec: _TopBotReplayStreamSpec,
    config: BotConfig,
) -> tuple[Sequence[ProjectXMarketCandle], int]:
    if _is_mmap_candle_sequence(candles):
        closed = candles
        excluded_partial = 0
    elif isinstance(candles, _ClosedCandleList):
        closed = candles
        excluded_partial = 0
    elif getattr(candles, "_topsignal_sorted_closed", False):
        closed = _ClosedCandleList(candles)
        excluded_partial = 0
    else:
        closed = _ClosedCandleList(
            row for row in candles if not _cached_candle_is_effectively_partial(row)
        )
        excluded_partial = len(candles) - len(closed)
        closed.sort(key=lambda row: _as_utc(row.candle_timestamp))
    _copy_closed_candle_metadata(candles, closed)
    if getattr(closed, "_topsignal_verified_replay", False):
        cached_user = str(getattr(closed, "_topsignal_user_id", ""))
        cached_contract = str(getattr(closed, "_topsignal_contract_id", ""))
        cached_symbol = str(getattr(closed, "_topsignal_symbol", ""))
        if config.user_id is not None and cached_user != str(config.user_id):
            raise MalformedBacktestDataError(f"mixed_user_candles:{spec.key}:cached_context")
        if str(getattr(closed, "_topsignal_unit", "")) != spec.unit or int(
            getattr(closed, "_topsignal_unit_number", 0) or 0
        ) != spec.unit_number:
            raise MalformedBacktestDataError(f"mixed_timeframe_candles:{spec.key}")
        if spec.contract_id is not None and cached_contract != spec.contract_id:
            raise MalformedBacktestDataError(
                f"mixed_contract_candles:{spec.key}:cached_context"
            )
        if (
            spec.contract_id is None
            and spec.symbol is not None
            and cached_contract != spec.symbol
            and cached_symbol != spec.symbol
        ):
            raise MalformedBacktestDataError(
                f"mixed_benchmark_candles:{spec.key}:cached_context"
            )
        return closed, excluded_partial
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
        closed_count = _search_candle_close(
            stream.candles,
            event_utc,
            side="right",
            fallback=stream.close_times,
        )
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






def _load_topbot_replay_streams(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    start: datetime,
    end: datetime,
    primary_rows: list[ProjectXMarketCandle],
) -> dict[str, list[ProjectXMarketCandle]]:
    # TopBot uses only the already-loaded 5-minute MNQ stream.
    return {_topbot_asset_stream_key("minute", 5): primary_rows}


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
    primary_execution_rows: list[ProjectXMarketCandle] | None = None,
) -> int:
    """Populate TopBot's deterministic replay cache without running a replay."""

    if not legacy_projectx_backtest_fixtures_enabled(db):
        raise BacktestConfigurationError(
            "legacy_projectx_backtest_fixture_path_disabled"
        )

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

    if primary_execution_rows is None:
        primary_execution_rows = _load_primary_candle_range(
            db,
            user_id=user_id,
            config=config,
            start_at=start,
            closed_by=fetch_end,
        )
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
            reserved_replay_rows=len(primary_execution_rows),
        ):
            continue
        cursor = fetch_start
        chunk_span = timedelta(seconds=interval_seconds * (MAX_PROVIDER_FETCH_BARS - 1))
        while cursor < fetch_end:
            chunk_end = min(fetch_end, cursor + chunk_span)
            budget.claim()
            # Coverage checks above are database-only, but they leave their
            # read transaction checked out.  Commit at the provider boundary
            # so ProjectX history latency never consumes a pool connection.
            # A commit also preserves (rather than discards) any caller writes
            # that were pending when this legacy preparation path was entered.
            if db.in_transaction():
                db.commit()
            # Use a short-lived cache session as well. The shared candle helper
            # detaches its cached return rows when it owns a transaction; doing
            # that in the caller session could unexpectedly detach objects the
            # surrounding backtest still holds references to.
            provider_db = Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
            try:
                bot_service_module.fetch_and_store_market_candles(
                    provider_db,
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
                if provider_db.in_transaction():
                    provider_db.commit()
            except Exception:
                provider_db.rollback()
                raise
            finally:
                provider_db.close()
            if chunk_end >= fetch_end:
                break
            cursor = chunk_end
        prepared_count += 1

        if identity == primary_identity:
            primary_execution_rows = _load_primary_candle_range(
                db,
                user_id=user_id,
                config=config,
                start_at=start,
                closed_by=fetch_end,
            )
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
        reserved_replay_rows=len(execution_rows),
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
    reserved_replay_rows: int = 0,
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
    rows = _collect_projected_candles(
        query,
        reserved_replay_rows=reserved_replay_rows,
    )
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
    progress_callback: BacktestProgressCallback | None = None,
    cancellation_callback: BacktestCancellationCallback | None = None,
    include_evaluation_split: bool = True,
) -> dict[str, Any]:
    """Run a pure replay. This function cannot create or route an order."""

    engine = BacktestEngine(
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
        progress_callback=progress_callback,
        cancellation_callback=cancellation_callback,
    )
    result = engine.run()
    result["evaluation_split"] = None
    if signal_evaluator is None and include_evaluation_split:
        result["evaluation_split"] = _build_chronological_holdout_evaluation(
            engine,
            full_result=result,
            replay_streams=replay_streams,
            cancellation_callback=cancellation_callback,
        )
        if result["evaluation_split"] is None:
            result["warnings"].append(
                "A chronological holdout was unavailable because fewer than four execution bars "
                "were present; this replay is in-sample only and is not strategy validation."
            )
        else:
            result["notes"].append(
                "The chronological holdout is a fixed-parameter diagnostic, not independent "
                "validation or evidence of future performance."
            )
    return result


def _build_chronological_holdout_evaluation(
    engine: BacktestEngine,
    *,
    full_result: Mapping[str, Any],
    replay_streams: Mapping[str, list[ProjectXMarketCandle]] | None,
    cancellation_callback: BacktestCancellationCallback | None,
) -> dict[str, Any] | None:
    """Replay the final 20% with fresh portfolio/risk state and causal warmup only."""

    total_bars = len(engine.execution_candles)
    if total_bars < 4:
        return None
    holdout_bars = max(2, math.ceil(total_bars * 0.20))
    split_index = total_bars - holdout_bars
    if split_index < 2:
        return None

    split_timestamp = engine.execution_start_times[split_index]
    holdout_engine = BacktestEngine(
        config=engine.config,
        candles=engine.all_candles,
        settings=BacktestSettings(
            start=split_timestamp,
            end=engine.settings.end,
            starting_balance=engine.settings.starting_balance,
            commission_per_contract=engine.settings.commission_per_contract,
            slippage_ticks=engine.settings.slippage_ticks,
            tick_size=engine.settings.tick_size,
            tick_value=engine.settings.tick_value,
            # The isolated window must realize any final position so its trade
            # metrics do not depend on activity outside the holdout.
            force_close_at_end=True,
        ),
        replay_streams=replay_streams,
        cancellation_callback=cancellation_callback,
    )
    holdout_result = holdout_engine.run()
    split_utc = _as_utc(split_timestamp)
    in_sample_trades = [
        trade
        for trade in full_result.get("trades", [])
        if _as_utc(datetime.fromisoformat(str(trade["entry_timestamp"]))) < split_utc
        and _as_utc(datetime.fromisoformat(str(trade["exit_timestamp"]))) <= split_utc
    ]
    crossing_trade_count = sum(
        1
        for trade in full_result.get("trades", [])
        if _as_utc(datetime.fromisoformat(str(trade["entry_timestamp"]))) < split_utc
        < _as_utc(datetime.fromisoformat(str(trade["exit_timestamp"])))
    )
    return {
        "method": "chronological_80_20_fixed_parameters",
        "label": "Chronological holdout diagnostic (not strategy validation)",
        "validation_status": "diagnostic_only",
        "split_timestamp": split_utc.isoformat(),
        "in_sample": {
            "start": engine.execution_start_times[0].isoformat(),
            "end": split_utc.isoformat(),
            "bar_count": split_index,
            "metrics": _backtest_breakdown_snapshot(_trade_breakdown(in_sample_trades)),
        },
        "holdout": {
            "start": holdout_result["range"]["start"],
            "end": holdout_result["range"]["end"],
            "bar_count": int(holdout_result["range"]["bar_count"]),
            "metrics": _backtest_breakdown_snapshot(holdout_result["metrics"]),
        },
        "notes": [
            "Strategy parameters were fixed before the holdout replay; this workflow did not optimize them.",
            "The holdout used a fresh cash, position, pending-signal, cooldown, and daily-risk state.",
            "Only candles closed before each holdout decision were available; pre-split candles were used solely as causal indicator warmup.",
            "The holdout final position was closed at its last bar so holdout trade metrics are self-contained.",
            f"{crossing_trade_count} full-replay trade(s) crossed the split and were excluded from the in-sample summary.",
            "This diagnostic is not proof of future performance and is not a substitute for forward paper trading.",
        ],
    }


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


def _collect_projected_candles(
    query: Any,
    *,
    reserved_replay_rows: int = 0,
    reserved_execution_rows: int = 0,
) -> list[ProjectXMarketCandle]:
    rows: list[ProjectXMarketCandle] = []
    for values in query.yield_per(CANDLE_QUERY_CHUNK_SIZE):
        rows.append(_ProjectedCandle(*values))
        if len(rows) % CANDLE_QUERY_CHUNK_SIZE == 0:
            _enforce_backtest_resource_budget(
                replay_rows=reserved_replay_rows + len(rows),
                execution_rows=reserved_execution_rows,
            )
    # Enforce the final partial chunk too.  This is also the only check for
    # small queries, which must not allocate on top of an exhausted budget.
    _enforce_backtest_resource_budget(
        replay_rows=reserved_replay_rows + len(rows),
        execution_rows=reserved_execution_rows,
    )
    return rows


def _load_primary_candle_range(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    closed_by: datetime,
    start_at: datetime | None = None,
) -> _ClosedCandleList:
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
    )
    if start_at is not None:
        query = query.filter(
            ProjectXMarketCandle.candle_timestamp >= _as_utc(start_at)
        )
    rows = _collect_projected_candles(
        query.order_by(ProjectXMarketCandle.candle_timestamp.asc())
    )
    return _ClosedCandleList(
        row
        for row in rows
        if not _cached_candle_is_effectively_partial(row)
        and _candle_close_time(row) <= cutoff
    )


def _load_primary_closed_candles(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    closed_by: datetime,
) -> list[ProjectXMarketCandle]:
    """Load every eligible primary bar without rolling across futures deliveries."""

    return _load_primary_candle_range(
        db,
        user_id=user_id,
        config=config,
        closed_by=closed_by,
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
    rows: Sequence[ProjectXMarketCandle],
    *,
    payload: Any,
    now: datetime,
) -> _ResolvedBacktestWindow:
    """Resolve absent dates to complete exact-contract coverage at one instant."""

    captured_now = _as_utc(now)
    requested = _requested_backtest_bounds(payload)
    if requested is None:
        eligible_count = len(rows)
        first_eligible_index = 0
        last_eligible_index = len(rows) - 1
        full_history = True
    else:
        requested_start, requested_end = requested
        effective_end = min(requested_end, captured_now)
        first_eligible_index = _search_candle_start(
            rows, requested_start, side="left"
        )
        eligible_end_index = _search_candle_close(
            rows, effective_end, side="right"
        )
        eligible_count = max(0, eligible_end_index - first_eligible_index)
        last_eligible_index = eligible_end_index - 1
        full_history = False

    if eligible_count < MIN_EXECUTION_BARS:
        mode = "full configured-contract history" if requested is None else "requested range"
        raise InsufficientBacktestDataError(
            "insufficient_backtest_data: at least 2 fully closed execution bars are required "
            f"for the {mode}; found {eligible_count}"
        )

    if requested is None:
        start = _candle_start_time_at(rows, first_eligible_index)
        end = _candle_close_time_at(rows, last_eligible_index)
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
) -> _ClosedCandleList:
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

    return _load_primary_closed_candles(
        db,
        user_id=user_id,
        config=config,
        closed_by=captured_now,
    )


def databento_backtest_history_available(db: Session, *, config: BotConfig) -> bool:
    root = normalize_symbol_key(config.symbol) or normalize_symbol_key(config.contract_id)
    if not root:
        return False
    # SQLite relational history is retained only as a deterministic repository
    # fixture. Production PostgreSQL/Supabase never probes market-data tables.
    if _database_databento_fixture_bounds(db, root_symbol=root) is not None:
        return True
    if legacy_projectx_backtest_fixtures_enabled(db):
        return False
    try:
        return get_default_databento_cache().history_bounds(root) is not None
    except DatabentoCacheError:
        return False


def _database_databento_fixture_bounds(
    db: Session,
    *,
    root_symbol: str,
) -> tuple[datetime, datetime] | None:
    bind = db.get_bind() if hasattr(db, "get_bind") else None
    if (
        not ALLOW_LEGACY_DATABENTO_SQLITE_FIXTURES
        or bind is None
        or bind.dialect.name != "sqlite"
    ):
        return None
    try:
        table_names = set(inspect(db.connection()).get_table_names())
    except Exception:
        return None
    if not {"databento_ohlcv_1m", "databento_roll_schedule"}.issubset(table_names):
        return None
    return databento_history_bounds(db, root_symbol=root_symbol)


def legacy_projectx_backtest_fixtures_enabled(db: Session) -> bool:
    bind = db.get_bind() if hasattr(db, "get_bind") else None
    return bool(
        ALLOW_LEGACY_PROJECTX_BACKTEST_FIXTURES
        and bind is not None
        and bind.dialect.name == "sqlite"
    )


def _config_for_backtest_request(config: BotConfig, payload: Any) -> Any:
    """Return non-persistent strategy and instrument overrides for this run."""

    requested = getattr(payload, "strategy_type", None)
    requested_instrument = getattr(payload, "instrument", None)
    instrument = (
        str(requested_instrument).strip().upper()
        if requested_instrument is not None
        else None
    )
    if instrument is not None and instrument not in BACKTEST_INSTRUMENTS:
        raise BacktestConfigurationError(
            f"unsupported_backtest_instrument:{instrument}"
        )
    if (str(requested) if requested is not None else str(config.strategy_type)) == _TOPBOT_STRATEGY:
        from .topbot import TOPBOT_SETTINGS
        if instrument not in (None, "MNQ"):
            raise BacktestConfigurationError("TopBot Adaptive trades MNQ only.")
        view = _SourceConfigView(config, strategy_type=_TOPBOT_STRATEGY,
            strategy_params=deepcopy(TOPBOT_SETTINGS["strategy_params"]),
            fast_period=TOPBOT_SETTINGS["fast_period"], slow_period=TOPBOT_SETTINGS["slow_period"])
        for name, value in TOPBOT_SETTINGS.items():
            setattr(view, name, deepcopy(value))
        view.contract_id = str(config.contract_id) if normalize_symbol_key(config.contract_id) == "MNQ" else "DATABENTO.CONTINUOUS.MNQ"
        view.allowed_contracts = [view.contract_id]
        return view
    current_instrument = normalize_symbol_key(config.symbol) or normalize_symbol_key(
        config.contract_id
    )
    strategy_unchanged = requested is None or str(requested) == str(config.strategy_type)
    instrument_unchanged = instrument is None or instrument == current_instrument
    if strategy_unchanged and instrument_unchanged:
        return config
    strategy_type = str(config.strategy_type) if strategy_unchanged else str(requested)
    if strategy_unchanged:
        strategy_params = config.strategy_params
        fast_period = int(config.fast_period)
        slow_period = int(config.slow_period)
    else:
        _require_supported_strategy(strategy_type)
        strategy_params = bot_service_module._normalize_strategy_params(
            strategy_type,
            {},
        )
        fast_period, slow_period = bot_service_module._normalized_strategy_period_values(
            strategy_type,
            fast_period=int(config.fast_period),
            slow_period=int(config.slow_period),
        )
    selected_instrument = instrument or current_instrument
    replay_contract_id = (
        f"DATABENTO.CONTINUOUS.{selected_instrument}"
        if not instrument_unchanged
        else None
    )
    return _SourceConfigView(
        config,
        strategy_type=strategy_type,
        strategy_params=strategy_params,
        fast_period=fast_period,
        slow_period=slow_period,
        symbol=selected_instrument if not instrument_unchanged else None,
        contract_id=replay_contract_id,
        # A selected historical instrument is an explicit, validated replay
        # scope. Do not inherit the saved bot's exact live-delivery allowlist,
        # which belongs to a different ProjectX contract and cannot authorize
        # the synthetic Databento continuous contract.
        allowed_contracts=(
            [replay_contract_id] if replay_contract_id is not None else None
        ),
    )


def create_bot_backtest(
    db: Session,
    *,
    user_id: str,
    bot_config_id: int,
    payload: Any,
    client: ProjectXClient | None = None,
    now: datetime | None = None,
    progress_callback: BacktestProgressCallback | None = None,
    cancellation_callback: BacktestCancellationCallback | None = None,
) -> BotBacktest:
    """Replay global Databento history; ProjectX is never a production data source."""

    config = (
        db.query(BotConfig)
        .filter(BotConfig.user_id == user_id)
        .filter(BotConfig.id == bot_config_id)
        .one_or_none()
    )
    if config is None:
        raise LookupError("bot_config_not_found")
    config = _config_for_backtest_request(config, payload)
    _raise_if_backtest_cancelled(cancellation_callback)
    root = normalize_symbol_key(config.symbol) or normalize_symbol_key(config.contract_id)
    database_fixture_bounds = (
        _database_databento_fixture_bounds(db, root_symbol=root) if root else None
    )
    replay_store: DatabentoReplayStore | None = None
    bounds = database_fixture_bounds
    if (
        bounds is None
        and root
        and not legacy_projectx_backtest_fixtures_enabled(db)
    ):
        replay_store = get_default_databento_cache()
        try:
            bounds = replay_store.history_bounds(root)
        except DatabentoCacheMissingError:
            bounds = None
        except DatabentoCacheStaleError as exc:
            raise BacktestConfigurationError(str(exc)) from exc
        except DatabentoCacheError as exc:
            raise BacktestConfigurationError(str(exc)) from exc
    if bounds is None:
        # The legacy path is retained solely so the repository's historical
        # SQLite engine fixtures remain useful. Application PostgreSQL requests
        # fail closed and cannot read or fetch ProjectX market history.
        if legacy_projectx_backtest_fixtures_enabled(db):
            return _create_legacy_projectx_bot_backtest(
                db,
                user_id=user_id,
                bot_config_id=bot_config_id,
                payload=payload,
                client=client,
                now=now,
                progress_callback=progress_callback,
                cancellation_callback=cancellation_callback,
            )
        raise InsufficientBacktestDataError(
            f"databento_history_missing:{root or config.contract_id}: import historical data before backtesting"
        )
    return _create_databento_bot_backtest(
        db,
        user_id=user_id,
        config=config,
        payload=payload,
        root_symbol=str(root),
        history_bounds=bounds,
        replay_store=replay_store,
        now=now,
        progress_callback=progress_callback,
        cancellation_callback=cancellation_callback,
    )


def _create_databento_bot_backtest(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    payload: Any,
    root_symbol: str,
    history_bounds: tuple[datetime, datetime],
    replay_store: DatabentoReplayStore | None,
    now: datetime | None,
    progress_callback: BacktestProgressCallback | None,
    cancellation_callback: BacktestCancellationCallback | None,
) -> BotBacktest:
    _raise_if_backtest_cancelled(cancellation_callback)
    _notify_backtest_progress(
        progress_callback,
        phase="preparing",
        completed=None,
        total=None,
        percent=None,
        remaining_percent=None,
    )
    _require_supported_strategy(str(config.strategy_type))
    _validate_replay_configuration(config)
    specs = load_instrument_specs(db)
    instrument_spec = specs.get(root_symbol)
    if instrument_spec is None:
        raise BacktestConfigurationError(f"instrument_metadata_missing:{root_symbol}")

    captured_now = _as_utc(now or datetime.now(timezone.utc))
    source_start, source_end = history_bounds
    closed_by = min(captured_now, _as_utc(source_end))
    requested_bounds = _requested_backtest_bounds(payload)
    if requested_bounds is None:
        load_start = _as_utc(source_start)
        load_end = closed_by
    else:
        requested_start, requested_end = requested_bounds
        warmup_bars = max(
            int(config.lookback_bars),
            _strategy_history_bars(config, hard_minimum=False),
        )
        if str(config.strategy_type) == _TOPBOT_STRATEGY:
            primary_key = _topbot_asset_stream_key(
                str(config.timeframe_unit), int(config.timeframe_unit_number)
            )
            warmup_bars = max(
                warmup_bars,
                _topbot_stream_specs(config)[primary_key].warmup_bars,
            )
        load_start = max(
            _as_utc(source_start),
            _databento_warmup_start(
                requested_start,
                unit=str(config.timeframe_unit),
                unit_number=int(config.timeframe_unit_number),
                warmup_bars=warmup_bars,
            ),
        )
        load_end = min(_as_utc(requested_end), closed_by)

    _notify_backtest_progress(
        progress_callback,
        phase="loading",
        completed=None,
        total=None,
        percent=None,
        remaining_percent=None,
    )
    max_loaded_rows = max(
        MIN_EXECUTION_BARS,
        int(BACKTEST_MEMORY_BUDGET_BYTES)
        // (ESTIMATED_REPLAY_CANDLE_BYTES + ESTIMATED_EXECUTION_RESULT_BYTES),
    )
    try:
        if replay_store is None:
            loaded_primary = load_databento_replay_candles(
                db,
                max_rows=max_loaded_rows,
                user_id=user_id,
                contract_id=str(config.contract_id),
                root_symbol=root_symbol,
                unit=str(config.timeframe_unit),
                unit_number=int(config.timeframe_unit_number),
                start=load_start,
                end=load_end,
                closed_by=closed_by,
            )
            primary_rows: Sequence[ProjectXMarketCandle] = _ClosedCandleList(
                loaded_primary
            )
            _copy_closed_candle_metadata(loaded_primary, primary_rows)
        else:
            primary_rows = replay_store.open_candles(
                user_id=user_id,
                contract_id=str(config.contract_id),
                root_symbol=root_symbol,
                unit=str(config.timeframe_unit),
                unit_number=int(config.timeframe_unit_number),
                start=load_start,
                end=load_end,
                closed_by=closed_by,
            )
    except (DatabentoMarketDataError, DatabentoCacheError) as exc:
        raise BacktestConfigurationError(str(exc)) from exc
    _raise_if_backtest_cancelled(cancellation_callback)
    window = _resolve_backtest_window(primary_rows, payload=payload, now=closed_by)

    primary_start_times = _candle_time_sequence(primary_rows, close=False)
    primary_close_times = _candle_time_sequence(primary_rows, close=True)
    execution_start_index = _search_candle_start(
        primary_rows,
        window.start,
        side="left",
        fallback=primary_start_times,
    )
    execution_end_index = _search_candle_close(
        primary_rows,
        window.end,
        side="right",
        fallback=primary_close_times,
    )
    rolling_warmup_limit = max(
        int(config.lookback_bars),
        _strategy_history_bars(config, hard_minimum=False),
    )
    if str(config.strategy_type) == _TOPBOT_STRATEGY:
        primary_key = _topbot_asset_stream_key(
            str(config.timeframe_unit), int(config.timeframe_unit_number)
        )
        rolling_warmup_limit = max(
            rolling_warmup_limit,
            _topbot_stream_specs(config)[primary_key].warmup_bars,
        )
    warmup_limit = _max_evaluator_input_bars(
        config,
        rolling_limit=rolling_warmup_limit,
    )
    warmup_start_index = max(0, execution_start_index - warmup_limit)
    replay_rows = _closed_candle_slice(
        primary_rows,
        warmup_start_index,
        execution_end_index,
    )

    replay_streams: dict[str, Sequence[ProjectXMarketCandle]] | None = None
    if str(config.strategy_type) == _TOPBOT_STRATEGY:
        replay_streams = _load_databento_topbot_replay_streams(
            db,
            user_id=user_id,
            config=config,
            root_symbol=root_symbol,
            window=window,
            closed_by=closed_by,
            primary_rows=replay_rows,
            max_rows=max_loaded_rows,
            replay_store=replay_store,
        )
    _raise_if_backtest_cancelled(cancellation_callback)

    # Drop the discovery/window lists before the engine allocates its own
    # execution indexes. The replay slice (and any synchronized streams) retain
    # exactly the projected candle objects they need.
    del primary_start_times, primary_close_times, primary_rows

    result_cache_key = (
        _backtest_result_cache_key(
            config=config,
            candles=replay_rows,
            replay_streams=replay_streams,
            start=window.start,
            end=window.end,
            starting_balance=float(payload.starting_balance),
            commission_per_contract=float(payload.commission_per_contract),
            slippage_ticks=float(payload.slippage_ticks),
            tick_size=instrument_spec.tick_size,
            tick_value=instrument_spec.tick_value,
            force_close_at_end=bool(payload.force_close_at_end),
        )
        if replay_store is not None
        else None
    )
    result = (
        _BACKTEST_RESULT_CACHE.get(result_cache_key)
        if result_cache_key is not None
        else None
    )
    _raise_if_backtest_cancelled(cancellation_callback)
    if result is None:
        result = run_backtest(
            config=config,
            candles=replay_rows,
            start=window.start,
            end=window.end,
            starting_balance=float(payload.starting_balance),
            commission_per_contract=float(payload.commission_per_contract),
            slippage_ticks=float(payload.slippage_ticks),
            tick_size=instrument_spec.tick_size,
            tick_value=instrument_spec.tick_value,
            force_close_at_end=bool(payload.force_close_at_end),
            replay_streams=replay_streams,
            progress_callback=progress_callback,
            cancellation_callback=cancellation_callback,
        )
        _raise_if_backtest_cancelled(cancellation_callback)
        if result_cache_key is not None:
            _BACKTEST_RESULT_CACHE.put(result_cache_key, result)
    else:
        _notify_backtest_progress(
            progress_callback,
            phase="replaying",
            completed=int(result["range"]["bar_count"]),
            total=int(result["range"]["bar_count"]),
            percent=100,
            remaining_percent=0,
            cache_hit=True,
        )
    if replay_store is not None:
        result["assumptions"]["historical_source"] = "databento_local_cache"
        result["assumptions"]["market_data_cache"] = "partitioned_parquet_numpy_memmap"
        result["assumptions"]["source_fingerprint"] = getattr(
            replay_rows[0], "source_file_sha256", None
        )
    result["notes"].append(
        f"Historical replay used Databento {root_symbol} data from the "
        f"{'local Parquet/memory-map cache' if replay_store is not None else 'SQLite test fixture'} "
        "with a prior-completed-session volume rollover schedule; ProjectX market history was not read."
    )
    _notify_backtest_progress(
        progress_callback,
        phase="finalizing",
        completed=int(result["range"]["bar_count"]),
        total=int(result["range"]["bar_count"]),
        percent=100,
        remaining_percent=0,
    )
    input_fingerprint = (
        candle_stream_input_fingerprint(replay_streams)
        if replay_streams is not None
        else candle_input_fingerprint(replay_rows)
    )
    _raise_if_backtest_cancelled(cancellation_callback)
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
        tick_size=instrument_spec.tick_size,
        tick_value=instrument_spec.tick_value,
        bar_count=int(result["range"]["bar_count"]),
        input_fingerprint=input_fingerprint,
        config_snapshot=result["config_snapshot"],
        assumptions_snapshot=result["assumptions"],
        result_snapshot=result,
    )
    db.add(row)
    db.flush()
    return row


def _load_databento_topbot_replay_streams(
    db: Session,
    *,
    user_id: str,
    config: BotConfig,
    root_symbol: str,
    window: _ResolvedBacktestWindow,
    closed_by: datetime,
    primary_rows: Sequence[ProjectXMarketCandle],
    max_rows: int,
    replay_store: DatabentoReplayStore | None,
) -> dict[str, Sequence[ProjectXMarketCandle]]:
    # TopBot uses only the already-loaded 5-minute MNQ stream.
    return {_topbot_asset_stream_key("minute", 5): primary_rows}


def _databento_warmup_start(
    requested_start: datetime,
    *,
    unit: str,
    unit_number: int,
    warmup_bars: int,
) -> datetime:
    seconds = _timeframe_seconds(unit, unit_number)
    if seconds is None:
        seconds = 86_400
    # Four bar-spans plus a week covers maintenance gaps, weekends, and normal
    # holidays without scanning all available history for a bounded request.
    return _as_utc(requested_start) - timedelta(
        seconds=max(1, int(warmup_bars)) * seconds * 4,
        days=7,
    )


def _create_legacy_projectx_bot_backtest(
    db: Session,
    *,
    user_id: str,
    bot_config_id: int,
    payload: Any,
    client: ProjectXClient | None = None,
    now: datetime | None = None,
    progress_callback: BacktestProgressCallback | None = None,
    cancellation_callback: BacktestCancellationCallback | None = None,
) -> BotBacktest:
    _raise_if_backtest_cancelled(cancellation_callback)
    if not legacy_projectx_backtest_fixtures_enabled(db):
        raise BacktestConfigurationError(
            "legacy_projectx_backtest_fixture_path_disabled"
        )
    _notify_backtest_progress(
        progress_callback,
        phase="preparing",
        completed=None,
        total=None,
        percent=None,
        remaining_percent=None,
    )
    config = (
        db.query(BotConfig)
        .filter(BotConfig.user_id == user_id)
        .filter(BotConfig.id == bot_config_id)
        .one_or_none()
    )
    if config is None:
        raise LookupError("bot_config_not_found")
    config = _config_for_backtest_request(config, payload)
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
    primary_rows: list[ProjectXMarketCandle] | None = None
    if is_topbot:
        if client is None:
            raise BacktestConfigurationError("topbot_backtest_market_data_client_required")
        if requested_bounds is None:
            primary_rows = _prepare_topbot_primary_full_history(
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

    if primary_rows is None:
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
            primary_execution_rows=primary_rows,
        )

    _notify_backtest_progress(
        progress_callback,
        phase="loading",
        completed=None,
        total=None,
        percent=None,
        remaining_percent=None,
    )

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
        progress_callback=progress_callback,
        cancellation_callback=cancellation_callback,
    )
    _raise_if_backtest_cancelled(cancellation_callback)
    result["assumptions"].update(
        {
            "market_data": "legacy_projectx_sqlite_test_fixture_only",
            "historical_source": "projectx_test_fixture",
            "roll_policy_version": None,
            "engine_version": LEGACY_PROJECTX_BACKTEST_ENGINE_VERSION,
        }
    )
    _notify_backtest_progress(
        progress_callback,
        phase="finalizing",
        completed=int(result["range"]["bar_count"]),
        total=int(result["range"]["bar_count"]),
        percent=100,
        remaining_percent=0,
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
    _raise_if_backtest_cancelled(cancellation_callback)
    assumptions = result["assumptions"]
    snapshot = result["config_snapshot"]
    row = BotBacktest(
        user_id=user_id,
        bot_config_id=int(config.id),
        account_id=int(config.account_id),
        engine_version=LEGACY_PROJECTX_BACKTEST_ENGINE_VERSION,
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


def _backtest_result_cache_key(
    *,
    config: BotConfig,
    candles: list[ProjectXMarketCandle],
    replay_streams: Mapping[str, list[ProjectXMarketCandle]] | None,
    start: datetime,
    end: datetime,
    starting_balance: float,
    commission_per_contract: float,
    slippage_ticks: float,
    tick_size: float,
    tick_value: float,
    force_close_at_end: bool,
) -> str:
    input_fingerprint = (
        candle_stream_input_fingerprint(replay_streams)
        if replay_streams is not None
        else candle_input_fingerprint(candles)
    )
    canonical = {
        "engine_version": BACKTEST_ENGINE_VERSION,
        "config": _config_snapshot(config),
        "input_fingerprint": input_fingerprint,
        "start": _as_utc(start).isoformat(),
        "end": _as_utc(end).isoformat(),
        "starting_balance": float(starting_balance),
        "commission_per_contract": float(commission_per_contract),
        "slippage_ticks": float(slippage_ticks),
        "tick_size": float(tick_size),
        "tick_value": float(tick_value),
        "force_close_at_end": bool(force_close_at_end),
    }
    payload = json.dumps(
        canonical,
        allow_nan=False,
        default=_backtest_cache_json_default,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def candle_input_fingerprint(candles: list[ProjectXMarketCandle]) -> str:
    cached = getattr(candles, "_topsignal_input_fingerprint", None)
    if cached:
        return str(cached)
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
            **_databento_fingerprint_fields(row),
        }
        for row in ordered
        if not _cached_candle_is_effectively_partial(row)
    )


def candle_stream_input_fingerprint(
    streams: Mapping[str, list[ProjectXMarketCandle]],
) -> str:
    cached_streams = {
        key: getattr(candles, "_topsignal_input_fingerprint", None)
        for key, candles in streams.items()
    }
    if cached_streams and all(cached_streams.values()):
        canonical = json.dumps(
            {key: str(cached_streams[key]) for key in sorted(cached_streams)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

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
                    **_databento_fingerprint_fields(row),
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


def _databento_fingerprint_fields(row: Any) -> dict[str, Any]:
    if str(getattr(row, "source", "")) != "databento":
        return {}
    return {
        "source": "databento",
        "source_instrument_id": getattr(row, "source_instrument_id", None),
        "source_raw_symbol": getattr(row, "source_raw_symbol", None),
        "source_file_sha256": getattr(row, "source_file_sha256", None),
        "roll_policy_version": getattr(row, "roll_policy_version", None),
    }


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


def _is_mmap_candle_sequence(candles: Any) -> bool:
    return bool(getattr(candles, "_topsignal_mmap_backed", False))


def _datetime_to_epoch_ns(value: datetime) -> int:
    normalized = _as_utc(value)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = normalized - epoch
    return (
        (delta.days * 86_400 + delta.seconds) * 1_000_000_000
        + delta.microseconds * 1_000
    )


def _datetime_from_epoch_ns(value: int) -> datetime:
    seconds, nanoseconds = divmod(int(value), 1_000_000_000)
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
        seconds=seconds,
        microseconds=nanoseconds // 1_000,
    )


def _candle_time_sequence(
    candles: Sequence[ProjectXMarketCandle],
    *,
    close: bool,
) -> Sequence[datetime]:
    if _is_mmap_candle_sequence(candles):
        return _NanosecondDatetimeSequence(
            candles.close_ns if close else candles.start_ns
        )
    if close:
        return [_candle_close_time(candle) for candle in candles]
    return [_as_utc(candle.candle_timestamp) for candle in candles]


def _candle_start_time_at(
    candles: Sequence[ProjectXMarketCandle], index: int
) -> datetime:
    if _is_mmap_candle_sequence(candles):
        return _datetime_from_epoch_ns(int(candles.start_ns[index]))
    return _as_utc(candles[index].candle_timestamp)


def _candle_close_time_at(
    candles: Sequence[ProjectXMarketCandle], index: int
) -> datetime:
    if _is_mmap_candle_sequence(candles):
        return _datetime_from_epoch_ns(int(candles.close_ns[index]))
    return _candle_close_time(candles[index])


def _search_candle_start(
    candles: Sequence[ProjectXMarketCandle],
    timestamp: datetime,
    *,
    side: str = "left",
    fallback: Sequence[datetime] | None = None,
) -> int:
    search = getattr(candles, "search_start", None)
    if callable(search):
        return int(search(_as_utc(timestamp), side=side))
    values = fallback
    if values is not None:
        return (
            bisect_right(values, _as_utc(timestamp))
            if side == "right"
            else bisect_left(values, _as_utc(timestamp))
        )
    target = _as_utc(timestamp)
    low, high = 0, len(candles)
    while low < high:
        middle = (low + high) // 2
        value = _candle_start_time_at(candles, middle)
        if value < target or (side == "right" and value == target):
            low = middle + 1
        else:
            high = middle
    return low


def _search_candle_close(
    candles: Sequence[ProjectXMarketCandle],
    timestamp: datetime,
    *,
    side: str = "right",
    fallback: Sequence[datetime] | None = None,
) -> int:
    search = getattr(candles, "search_close", None)
    if callable(search):
        return int(search(_as_utc(timestamp), side=side))
    values = fallback
    if values is not None:
        return (
            bisect_left(values, _as_utc(timestamp))
            if side == "left"
            else bisect_right(values, _as_utc(timestamp))
        )
    target = _as_utc(timestamp)
    low, high = 0, len(candles)
    while low < high:
        middle = (low + high) // 2
        value = _candle_close_time_at(candles, middle)
        if value < target or (side == "right" and value == target):
            low = middle + 1
        else:
            high = middle
    return low


def _session_coverage_indexes(values: Sequence[bool]) -> tuple[int, int]:
    first = next((index for index in range(len(values)) if values[index]), 0)
    last = next(
        (index for index in range(len(values) - 1, -1, -1) if values[index]),
        max(0, len(values) - 1),
    )
    return first, last


def _replay_storage_estimate(
    streams: Iterable[Sequence[ProjectXMarketCandle]],
) -> tuple[int, int]:
    """Return unique mmap bytes and the remaining eager row count."""

    intervals: dict[Any, list[tuple[int, int, int]]] = defaultdict(list)
    eager_rows = 0
    for candles in streams:
        if not _is_mmap_candle_sequence(candles):
            eager_rows += len(candles)
            continue
        identity = getattr(candles, "_topsignal_physical_stream", None)
        start = getattr(candles, "_topsignal_slice_start", None)
        end = getattr(candles, "_topsignal_slice_end", None)
        if identity is None or start is None or end is None:
            identity = ("mmap-view", id(candles))
            start, end = 0, len(candles)
        bytes_per_row = max(
            1, int(getattr(candles, "_topsignal_storage_bytes_per_row", 68))
        )
        intervals[identity].append((int(start), int(end), bytes_per_row))

    mapped_bytes = 0
    for ranges in intervals.values():
        ranges.sort(key=lambda item: (item[0], item[1]))
        current_start, current_end, bytes_per_row = ranges[0]
        for start, end, row_bytes in ranges[1:]:
            bytes_per_row = max(bytes_per_row, row_bytes)
            if start <= current_end:
                current_end = max(current_end, end)
            else:
                mapped_bytes += max(0, current_end - current_start) * bytes_per_row
                current_start, current_end, bytes_per_row = start, end, row_bytes
        mapped_bytes += max(0, current_end - current_start) * bytes_per_row
    return mapped_bytes, eager_rows


def _closed_candle_slice(
    candles: Sequence[ProjectXMarketCandle],
    start: int,
    end: int | None = None,
) -> Sequence[ProjectXMarketCandle]:
    sliced = candles[start:end]
    if _is_mmap_candle_sequence(candles):
        return sliced
    if getattr(candles, "_topsignal_sorted_closed", False):
        target = _ClosedCandleList(sliced)
        _copy_closed_candle_metadata(candles, target)
        parent_fingerprint = getattr(candles, "_topsignal_input_fingerprint", None)
        if parent_fingerprint:
            normalized_start, normalized_end, _step = slice(start, end).indices(
                len(candles)
            )
            target._topsignal_input_fingerprint = hashlib.sha256(
                (
                    f"{parent_fingerprint}\0{normalized_start}\0{normalized_end}"
                ).encode("utf-8")
            ).hexdigest()
            base_start = getattr(candles, "_topsignal_slice_start", None)
            if base_start is not None:
                target._topsignal_slice_start = int(base_start) + normalized_start
                target._topsignal_slice_end = int(base_start) + normalized_end
        return target
    return sliced


def _copy_closed_candle_metadata(
    source: Sequence[ProjectXMarketCandle],
    target: Any,
) -> None:
    if source is target:
        return
    physical_stream = getattr(source, "_topsignal_physical_stream", None)
    physical_row_count = getattr(source, "_topsignal_physical_row_count", None)
    if physical_stream is not None and physical_row_count is not None:
        target._topsignal_physical_stream = physical_stream
        target._topsignal_physical_row_count = int(physical_row_count)
    for attribute in (
        "_topsignal_input_fingerprint",
        "_topsignal_series_fingerprint",
        "_topsignal_slice_start",
        "_topsignal_slice_end",
        "_topsignal_verified_replay",
        "_topsignal_user_id",
        "_topsignal_contract_id",
        "_topsignal_symbol",
        "_topsignal_unit",
        "_topsignal_unit_number",
    ):
        value = getattr(source, attribute, None)
        if value is not None:
            setattr(target, attribute, value)


def _enforce_backtest_resource_budget(
    *,
    replay_rows: int,
    execution_rows: int,
    replay_storage_bytes: int = 0,
    lazy_execution: bool = False,
) -> None:
    budget = max(1, int(BACKTEST_MEMORY_BUDGET_BYTES))
    estimated = (
        max(0, int(replay_storage_bytes))
        + max(0, int(replay_rows)) * ESTIMATED_REPLAY_CANDLE_BYTES
        + max(0, int(execution_rows))
        * (
            ESTIMATED_LAZY_EXECUTION_BYTES
            if lazy_execution
            else ESTIMATED_EXECUTION_RESULT_BYTES
        )
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


def _enforce_backtest_work_budget(estimated_evaluator_bar_visits: int) -> None:
    budget = max(1, int(BACKTEST_EVALUATOR_WORK_BUDGET))
    estimated = max(0, int(estimated_evaluator_bar_visits))
    if estimated <= budget:
        return
    raise BacktestConfigurationError(
        "backtest_computation_limit_exceeded: the complete resolved history and "
        "strategy lookback require an estimated "
        f"{estimated:,} evaluator bar-visits, above the configured {budget:,} "
        "work budget; no partial result was saved"
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
    candles: Sequence[ProjectXMarketCandle],
    *,
    config: BotConfig,
) -> tuple[Sequence[ProjectXMarketCandle], int]:
    if _is_mmap_candle_sequence(candles):
        closed = candles
        excluded_partial = 0
    elif isinstance(candles, _ClosedCandleList):
        closed = candles
        excluded_partial = 0
    elif getattr(candles, "_topsignal_sorted_closed", False):
        closed = _ClosedCandleList(candles)
        excluded_partial = 0
    else:
        closed = _ClosedCandleList(
            row for row in candles if not _cached_candle_is_effectively_partial(row)
        )
        excluded_partial = len(candles) - len(closed)
        closed.sort(key=lambda row: _as_utc(row.candle_timestamp))
    _copy_closed_candle_metadata(candles, closed)
    if getattr(closed, "_topsignal_verified_replay", False):
        if str(getattr(closed, "_topsignal_contract_id", "")) != str(
            config.contract_id
        ):
            raise MalformedBacktestDataError("mixed_contract_candles:cached_context")
        if config.user_id is not None and str(
            getattr(closed, "_topsignal_user_id", "")
        ) != str(config.user_id):
            raise MalformedBacktestDataError("mixed_user_candles:cached_context")
        if str(getattr(closed, "_topsignal_unit", "")) != str(
            config.timeframe_unit
        ) or int(getattr(closed, "_topsignal_unit_number", 0) or 0) != int(
            config.timeframe_unit_number
        ):
            raise MalformedBacktestDataError(
                "mixed_timeframe_candles: every replay candle must match the bot timeframe"
            )
        return closed, excluded_partial
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
    fetched_at = candle.fetched_at
    if fetched_at is None:
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


def _candle_delivery_identity(
    candle: ProjectXMarketCandle,
) -> tuple[str | None, int | None] | None:
    raw_symbol_value = getattr(candle, "source_raw_symbol", None)
    raw_symbol = str(raw_symbol_value).strip() if raw_symbol_value is not None else ""
    instrument_value = getattr(candle, "source_instrument_id", None)
    try:
        instrument_id = int(instrument_value) if instrument_value is not None else None
    except (TypeError, ValueError):
        instrument_id = None
    if not raw_symbol and instrument_id is None:
        return None
    return raw_symbol or None, instrument_id


def _contiguous_delivery_start(
    candles: Sequence[ProjectXMarketCandle],
    *,
    start_index: int,
    end_index: int,
) -> int:
    """Exclude another delivery from the bounded history exposed to an evaluator."""

    if end_index - start_index <= 1:
        return start_index
    if _is_mmap_candle_sequence(candles):
        # Verified cache columns have delivery identities for every row. Check
        # both fields: a raw symbol alone can be reused in a later decade.
        instruments = candles.instrument_id_values[start_index:end_index]
        symbols = candles.raw_symbol_code_values[start_index:end_index]
        different = np.flatnonzero(
            (instruments != instruments[-1]) | (symbols != symbols[-1])
        )
        return start_index + int(different[-1]) + 1 if different.size else start_index
    latest_delivery = _candle_delivery_identity(candles[end_index - 1])
    if latest_delivery is None:
        return start_index
    for index in range(end_index - 2, start_index - 1, -1):
        candidate = _candle_delivery_identity(candles[index])
        if candidate is not None and candidate != latest_delivery:
            return index + 1
    return start_index


def _candle_close_time(candle: ProjectXMarketCandle) -> datetime:
    source_close = getattr(candle, "nominal_close_time", None)
    if source_close is not None:
        return _as_utc(source_close)
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
    decision_timestamp: datetime,
    fill_timestamp: datetime,
    *,
    start_text: str,
    end_text: str,
) -> bool:
    session_start, session_end = _session_window_utc_for_reference(
        _as_utc(decision_timestamp),
        start_text=start_text,
        end_text=end_text,
    )
    decision = _as_utc(decision_timestamp)
    fill = _as_utc(fill_timestamp)
    return session_start <= decision <= session_end and session_start <= fill <= session_end


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
    # Replay prices are validated finite numbers and fills are tick-aligned.
    # Keeping this inner-loop calculation in binary float is exact for the
    # supported equity-index quarter ticks and final outputs still pass through
    # the engine's established 10-decimal normalization.
    if float(tick_size) != 0.25:
        direction_decimal = Decimal("1") if side == "long" else Decimal("-1")
        return float(
            (Decimal(str(exit)) - Decimal(str(entry)))
            / Decimal(str(tick_size))
            * Decimal(str(tick_value))
            * Decimal(str(quantity))
            * direction_decimal
        )
    direction = 1.0 if side == "long" else -1.0
    return (
        (float(exit) - float(entry))
        / float(tick_size)
        * float(tick_value)
        * float(quantity)
        * direction
    )


def _build_metrics(
    trades: list[dict[str, Any]],
    *,
    equity_curve: list[dict[str, Any]],
    drawdown_series: list[dict[str, Any]],
    max_drawdown_dollars: float | None = None,
    max_drawdown_percent: float | None = None,
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

    if max_drawdown_dollars is None:
        max_drawdown_dollars = max(
            (float(point["drawdown_dollars"]) for point in drawdown_series), default=0.0
        )
    if max_drawdown_percent is None:
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


def _backtest_breakdown_snapshot(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: metrics[key]
        for key in (
            "trade_count",
            "winning_trades",
            "losing_trades",
            "win_rate",
            "gross_pnl",
            "net_pnl",
            "profit_factor",
            "expectancy",
            "average_win",
            "average_loss",
            "payoff_ratio",
        )
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
        "strategy_replay": "single_strategy",
        "strategy_revision": bot_service_module._normalize_strategy_params(_TOPBOT_STRATEGY, {}).get("revision") if is_topbot else None,
        "source_synchronization": "not_applicable",
        "synchronized_stream_count": 1,
        "event_order": "resting_gap_brackets_then_pending_open_fill_then_intrabar_brackets_then_close_signal",
        "same_bar_exit_rule": "stop_first_when_stop_and_target_are_both_touched",
        "bracket_rule": "evaluator_levels_become_whole_tick_distances_anchored_to_actual_entry_fill",
        "gap_rule": "stops_fill_at_adverse_gap_open; targets receive no favorable price improvement",
        "final_position_handling": (
            "forced_close_at_last_bar_close" if settings.force_close_at_end else "left_open"
        ),
        "position_rule": (
            "same-side_duplicates_do_not_pyramid; target-less opposite signals flatten an "
            "equal-size position; target-aware atomic reversals are blocked to match live routing; "
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
            "databento_global_ohlcv_1m_resampled_on_session_anchored_buckets; "
            "continuous_delivery_uses_only_previous_completed_session_volume; evaluator_history_"
            "is_segmented_at_delivery_changes"
        ),
        "roll_gap_rule": (
            "open_positions_close_at_the_prior_delivery_final_close; pending_cross-roll_signals_"
            "are_discarded"
        ),
        "historical_source": "databento",
        "roll_policy_version": ROLL_POLICY_VERSION,
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
    candles: Sequence[ProjectXMarketCandle],
    *,
    interval_seconds: int,
) -> int:
    symbol = _candle_session_symbol(candles)
    return sum(
        _contains_open_futures_timestamp(
            start, end, step_seconds=interval_seconds, symbol=symbol,
        )
        for start, end in _candidate_candle_gaps(candles, interval_seconds=interval_seconds)
    )


def _candle_session_symbol(candles: Sequence[ProjectXMarketCandle]) -> str | None:
    # Candle slices all belong to one root; avoid scanning an entire unmapped stream.
    if not candles:
        return None
    row = candles[0]
    return next((str(value) for value in (
        getattr(row, "symbol", None), getattr(row, "contract_id", None),
    ) if value is not None and str(value).strip()), None)


def _summarize_futures_session_gaps(
    candles: Sequence[ProjectXMarketCandle],
    *,
    interval_seconds: int,
    in_entry_session: Callable[[datetime], bool],
) -> dict[str, Any]:
    """Count absent complete bars, without guessing whether prices are missing.

    The calendar excludes scheduled closures. OHLCV alone cannot distinguish
    an unrecorded minute from a minute with no trades or an unscheduled halt.
    Keep at most 20 largest examples; totals still cover the entire replay.
    """
    summary: dict[str, Any] = {
        "gap_count": 0, "missing_bar_count": 0,
        "in_session_gap_count": 0, "in_session_missing_bar_count": 0,
        "by_year": [], "largest_gaps": [],
    }
    years: dict[int, dict[str, int]] = {}
    symbol = _candle_session_symbol(candles)
    step = timedelta(seconds=max(1, int(interval_seconds)))
    for start, end in _candidate_candle_gaps(candles, interval_seconds=interval_seconds):
        cursor = start
        missing = in_session = 0
        first_missing: datetime | None = None
        while cursor < end:
            if futures_session_is_open(cursor, symbol=symbol):
                first_missing = first_missing or cursor
                missing += 1
                in_session += int(in_entry_session(cursor))
            cursor += step
        if not missing or first_missing is None:
            continue
        summary["gap_count"] += 1
        summary["missing_bar_count"] += missing
        summary["in_session_gap_count"] += int(in_session > 0)
        summary["in_session_missing_bar_count"] += in_session
        year = first_missing.astimezone(TRADING_TZ).year
        annual = years.setdefault(year, {
            "year": year, "gap_count": 0, "missing_bar_count": 0,
            "in_session_gap_count": 0,
        })
        annual["gap_count"] += 1
        annual["missing_bar_count"] += missing
        annual["in_session_gap_count"] += int(in_session > 0)
        summary["largest_gaps"].append({
            "start": first_missing.isoformat(), "end": end.isoformat(),
            "missing_bar_count": missing, "in_session_missing_bar_count": in_session,
        })
        summary["largest_gaps"].sort(key=lambda row: (-row["missing_bar_count"], row["start"]))
        del summary["largest_gaps"][20:]
    summary["by_year"] = [years[year] for year in sorted(years)]
    return summary


def _candidate_candle_gaps(
    candles: Sequence[ProjectXMarketCandle], *, interval_seconds: int,
) -> Iterable[tuple[datetime, datetime]]:
    """Yield absent bar slots as [start, end), retaining the fast mmap scan."""
    if not candles:
        return
    if _is_mmap_candle_sequence(candles):
        starts = candles.start_ns
        interval_ns = max(1, int(interval_seconds)) * 1_000_000_000
        chunk_rows = 262_144
        previous_ns: int | None = None
        for chunk_start in range(0, len(starts), chunk_rows):
            chunk = np.asarray(
                starts[chunk_start : min(len(starts), chunk_start + chunk_rows)],
                dtype=np.int64,
            )
            if chunk.size == 0:
                continue
            if previous_ns is None:
                combined = chunk
            else:
                combined = np.concatenate(
                    (np.asarray([previous_ns], dtype=np.int64), chunk)
                )
            candidate_offsets = np.flatnonzero(np.diff(combined) > interval_ns)
            for offset in candidate_offsets:
                prior_ns = int(combined[int(offset)])
                current_ns = int(combined[int(offset) + 1])
                elapsed_ns = current_ns - prior_ns
                missing_slots = max(0, int(round(elapsed_ns / interval_ns)) - 1)
                if missing_slots:
                    yield (
                        _datetime_from_epoch_ns(prior_ns + interval_ns),
                        _datetime_from_epoch_ns(current_ns),
                    )
            previous_ns = int(chunk[-1])
        return
    for previous, current in zip(candles, candles[1:]):
        previous_timestamp = _as_utc(previous.candle_timestamp)
        current_timestamp = _as_utc(current.candle_timestamp)
        elapsed = int((current_timestamp - previous_timestamp).total_seconds())
        missing_slots = max(0, int(round(elapsed / interval_seconds)) - 1)
        if missing_slots:
            yield previous_timestamp + timedelta(seconds=interval_seconds), current_timestamp


def _zero_volume_count(candles: Sequence[ProjectXMarketCandle]) -> int:
    if _is_mmap_candle_sequence(candles):
        return int(np.count_nonzero(candles.volume_values == 0))
    return sum(1 for row in candles if float(row.volume or 0) == 0)


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
    candles: Sequence[ProjectXMarketCandle],
    timestamp: datetime,
) -> int:
    if _is_mmap_candle_sequence(candles):
        return _search_candle_start(candles, timestamp, side="left")
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
    if _is_mmap_candle_sequence(candles):
        # Match the microsecond precision exposed by candle datetime proxies.
        starts_us = candles.start_ns // 1_000
        missing = np.flatnonzero(np.diff(starts_us) != expected_interval_seconds * 1_000_000)
        if not missing.size:
            return
        previous = candles[int(missing[0])]
        raise InsufficientBacktestDataError(
            "insufficient_backtest_data: incomplete_session_history: "
            f"{strategy_type} has a missing session candle after "
            f"{_as_utc(previous.candle_timestamp).isoformat()}"
        )
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
