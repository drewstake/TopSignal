#!/usr/bin/env python3
"""Benchmark lazy Databento mmap replay plus TopSignal's real replay engine.

Lazy mmap input is the default and matches the production application path.
Pass ``--input-mode eager`` to measure the optional Python-candle
materialization path. "Cold" samples create a new ``DatabentoReplayStore``;
they do not evict the operating system's filesystem page cache. Warm samples
reuse one store and the exact same slice.

Examples (run from the repository root):

    backend\.venv\Scripts\python backend\tools\benchmark_databento_cache.py
    backend\.venv\Scripts\python backend\tools\benchmark_databento_cache.py --json
    backend\.venv\Scripts\python backend\tools\benchmark_databento_cache.py --input-mode eager
    backend\.venv\Scripts\python backend\tools\benchmark_databento_cache.py --sqlite-persistence
    backend\.venv\Scripts\python backend\tools\benchmark_databento_cache.py --profile backend\storage\warm.prof
"""

from __future__ import annotations

import argparse
import cProfile
import gc
import hashlib
import json
import os
import platform
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Sequence
from uuid import UUID


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Application modules validate the URL at import time. Direct replay does not
# touch a database; --sqlite-persistence creates only an explicit in-memory DB.
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

OWNER_ID = "11111111-1111-1111-1111-111111111111"
SEMANTIC_KEYS = (
    "range",
    "metrics",
    "equity_curve",
    "drawdown_series",
    "daily_results",
    "monthly_results",
    "trades",
    "warnings",
)
ROOT_MARKET_VALUES = {
    "MNQ": (0.25, 0.50),
    "MES": (0.25, 1.25),
    "NQ": (0.25, 5.00),
    "ES": (0.25, 12.50),
}


@dataclass(frozen=True)
class RuntimeApi:
    base: Any
    replay_store: type[Any]
    cache_error: type[Exception]
    parse_timeframe: Callable[[str], tuple[str, int]]
    timeframe_key: Callable[[str, int], str]
    bot_config: type[Any]
    bot_backtest: type[Any]
    instrument_metadata: type[Any]
    bot_backtest_input: type[Any]
    run_backtest: Callable[..., dict[str, Any]]
    create_bot_backtest: Callable[..., Any]
    backtesting_module: Any
    engine_version: str


@dataclass(frozen=True)
class BenchmarkContext:
    root: str
    unit: str
    unit_number: int
    start: datetime
    end: datetime
    input_mode: str
    max_rows: int
    config: Any
    contract_id: str
    starting_balance: float
    commission: float
    slippage_ticks: float
    tick_size: float
    tick_value: float


@dataclass
class SqlCounter:
    total: int = 0

    def before_cursor_execute(
        self,
        _connection: Any,
        _cursor: Any,
        _statement: Any,
        _parameters: Any,
        _context: Any,
        _executemany: Any,
    ) -> None:
        self.total += 1


@dataclass
class ReplayStoreCallCounter:
    open_candles: int = 0
    load_candles: int = 0


class InstrumentedReplayStore:
    """Record which public source path the application benchmark exercises."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self.calls = ReplayStoreCallCounter()

    def open_candles(self, **kwargs: Any) -> Any:
        self.calls.open_candles += 1
        return self._store.open_candles(**kwargs)

    def load_candles(self, **kwargs: Any) -> Any:
        self.calls.load_candles += 1
        return self._store.load_candles(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


def _load_runtime_api() -> RuntimeApi:
    from app.bot_schemas import BotBacktestIn
    from app.db import Base
    from app.models import BotBacktest, BotConfig, InstrumentMetadata
    import app.services.bot_backtesting as backtesting_module
    from app.services.bot_backtesting import (
        BACKTEST_ENGINE_VERSION,
        create_bot_backtest,
        run_backtest,
    )
    from app.services.databento_cache import (
        DatabentoCacheError,
        DatabentoReplayStore,
        parse_timeframe,
        timeframe_key,
    )

    return RuntimeApi(
        base=Base,
        replay_store=DatabentoReplayStore,
        cache_error=DatabentoCacheError,
        parse_timeframe=parse_timeframe,
        timeframe_key=timeframe_key,
        bot_config=BotConfig,
        bot_backtest=BotBacktest,
        instrument_metadata=InstrumentMetadata,
        bot_backtest_input=BotBacktestIn,
        run_backtest=run_backtest,
        create_bot_backtest=create_bot_backtest,
        backtesting_module=backtesting_module,
        engine_version=BACKTEST_ENGINE_VERSION,
    )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare cold and warm Databento replay using a lazy mmap view by "
            "default, including an actual run_backtest call."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        help=(
            "cache root; otherwise TOPSIGNAL_DATABENTO_CACHE_DIR or "
            "backend/storage/databento"
        ),
    )
    parser.add_argument("--root", choices=tuple(ROOT_MARKET_VALUES), default="MNQ")
    parser.add_argument(
        "--timeframe",
        default="5m",
        help="prebuilt cache timeframe such as 1m, 5m, 15m, 1h, 4h, or 1d",
    )
    parser.add_argument(
        "--start",
        help="ISO-8601 UTC start; when omitted, derive it from --days",
    )
    parser.add_argument(
        "--end",
        help="ISO-8601 UTC end; when omitted, use the cached source end",
    )
    parser.add_argument(
        "--days",
        type=_positive_float,
        default=30.0,
        help="history window when --start is omitted",
    )
    parser.add_argument(
        "--cold-repeats",
        type=_positive_int,
        default=1,
        help="samples using a newly constructed, empty in-process cache",
    )
    parser.add_argument(
        "--warm-repeats",
        type=_positive_int,
        default=3,
        help="timed prepare-and-replay samples after one untimed preparation pass",
    )
    parser.add_argument(
        "--input-mode",
        choices=("lazy", "eager"),
        default="lazy",
        help=(
            "direct-replay input path: lazy opens an O(1) mmap view; eager "
            "materializes Python candle objects (the application persistence "
            "benchmark always uses its canonical lazy path)"
        ),
    )
    parser.add_argument(
        "--max-rows",
        type=_positive_int,
        default=500_000,
        help="hard ceiling on candles materialized in eager input mode",
    )
    parser.add_argument("--lookback-bars", type=_positive_int, default=200)
    parser.add_argument("--fast-period", type=_positive_int, default=9)
    parser.add_argument("--slow-period", type=_positive_int, default=21)
    parser.add_argument(
        "--starting-balance", type=_positive_float, default=50_000.0
    )
    parser.add_argument(
        "--commission", type=_nonnegative_float, default=1.25
    )
    parser.add_argument(
        "--slippage-ticks", type=_nonnegative_float, default=1.0
    )
    parser.add_argument(
        "--profile",
        type=Path,
        metavar="PATH",
        help="write one additional warm prepare-and-replay sample as cProfile data",
    )
    parser.add_argument(
        "--sqlite-persistence",
        action="store_true",
        help=(
            "also benchmark create_bot_backtest, the deterministic result LRU, "
            "and an in-memory SQLite commit for the same Databento window"
        ),
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    return parser


def _parse_datetime(value: str, *, option: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{option} must be an ISO-8601 timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _resolve_window(
    api: RuntimeApi,
    args: argparse.Namespace,
) -> tuple[datetime, datetime, tuple[datetime, datetime], Path]:
    probe = api.replay_store(args.cache_dir, build_missing_timeframes=False)
    try:
        cache_root = Path(probe.cache_root)
        bounds = probe.history_bounds(args.root)
    finally:
        probe.clear()
    if bounds is None:
        raise ValueError(f"cache does not contain root {args.root}")
    history_start, history_end = bounds
    end = _parse_datetime(args.end, option="--end") if args.end else history_end
    start = (
        _parse_datetime(args.start, option="--start")
        if args.start
        else end - timedelta(days=args.days)
    )
    if args.start and start < history_start:
        raise ValueError(
            f"--start precedes cached history ({history_start.isoformat()})"
        )
    if args.end and end > history_end:
        raise ValueError(f"--end exceeds cached history ({history_end.isoformat()})")
    start = max(start, history_start)
    end = min(end, history_end)
    if start >= end:
        raise ValueError("benchmark start must be before end")
    return start, end, bounds, cache_root


def _make_config(
    api: RuntimeApi,
    args: argparse.Namespace,
    *,
    unit: str,
    unit_number: int,
    contract_id: str,
) -> Any:
    return api.bot_config(
        id=1,
        user_id=OWNER_ID,
        account_id=9001,
        name="Databento cache benchmark",
        provider="projectx",
        enabled=False,
        execution_mode="dry_run",
        strategy_type="sma_cross",
        strategy_params={},
        contract_id=contract_id,
        symbol=args.root,
        timeframe_unit=unit,
        timeframe_unit_number=unit_number,
        lookback_bars=args.lookback_bars,
        fast_period=args.fast_period,
        slow_period=args.slow_period,
        order_size=2,
        max_contracts=10,
        max_daily_loss=1_000_000,
        max_trades_per_day=10_000,
        max_open_position=10,
        allowed_contracts=[contract_id],
        trading_start_time="00:00",
        trading_end_time="23:59",
        cooldown_seconds=0,
        max_data_staleness_seconds=3_600,
        allow_market_depth=False,
    )


def _semantic_json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        normalized = (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )
        return normalized.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (UUID, Path)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unsupported semantic value: {type(value).__name__}")


def _semantic_digest(result: dict[str, Any]) -> str:
    missing = [key for key in SEMANTIC_KEYS if key not in result]
    if missing:
        raise RuntimeError(
            f"backtest result is missing semantic fields: {', '.join(missing)}"
        )
    payload = json.dumps(
        {key: result[key] for key in SEMANTIC_KEYS},
        allow_nan=False,
        default=_semantic_json_default,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _round_seconds(value: float) -> float:
    return round(value, 9)


def _execute_sample(
    api: RuntimeApi,
    store: Any,
    context: BenchmarkContext,
) -> dict[str, Any]:
    stats_before = store.stats()
    total_started = time.perf_counter()
    prepare_started = total_started
    source_options = {
        "user_id": OWNER_ID,
        "contract_id": context.contract_id,
        "root_symbol": context.root,
        "unit": context.unit,
        "unit_number": context.unit_number,
        "start": context.start,
        "end": context.end,
        "closed_by": context.end,
    }
    if context.input_mode == "lazy":
        candles = store.open_candles(**source_options)
        source_method = "open_candles"
    else:
        candles = store.load_candles(
            **source_options,
            max_rows=context.max_rows,
        )
        source_method = "load_candles"
    prepare_finished = time.perf_counter()
    stats_after_prepare = store.stats()
    minimum = max(25, int(context.config.slow_period) + 2)
    if len(candles) < minimum:
        raise RuntimeError(
            f"benchmark window returned {len(candles)} candles; "
            f"at least {minimum} are required"
        )
    result = api.run_backtest(
        config=context.config,
        candles=candles,
        start=context.start,
        end=context.end,
        starting_balance=context.starting_balance,
        commission_per_contract=context.commission,
        slippage_ticks=context.slippage_ticks,
        tick_size=context.tick_size,
        tick_value=context.tick_value,
        force_close_at_end=True,
    )
    run_finished = time.perf_counter()
    stats_after_run = store.stats()
    digest_started = time.perf_counter()
    digest = _semantic_digest(result)
    digest_finished = time.perf_counter()
    result_range = result.get("range") if isinstance(result.get("range"), dict) else {}
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    warnings = (
        result.get("warnings") if isinstance(result.get("warnings"), list) else []
    )
    lazy_mmap = bool(getattr(candles, "_topsignal_lazy_replay", False))
    storage_bytes_per_row = getattr(
        candles, "_topsignal_storage_bytes_per_row", None
    )
    return {
        "input_mode": context.input_mode,
        "source_method": source_method,
        "input_type": type(candles).__name__,
        "lazy_mmap": lazy_mmap,
        "storage_bytes_per_row": (
            int(storage_bytes_per_row)
            if storage_bytes_per_row is not None
            else None
        ),
        "input_bars": len(candles),
        "output_bars": int(result_range.get("bar_count", 0)),
        "trade_count": int(metrics.get("trade_count", 0)),
        "warning_count": len(warnings),
        "semantic_sha256": digest,
        "input_fingerprint": getattr(candles, "_topsignal_input_fingerprint", None),
        "series_fingerprint": getattr(candles, "_topsignal_series_fingerprint", None),
        "phase_seconds": {
            "prepare_input": _round_seconds(prepare_finished - prepare_started),
            source_method: _round_seconds(prepare_finished - prepare_started),
            "run_backtest": _round_seconds(run_finished - prepare_finished),
            "prepare_plus_backtest": _round_seconds(run_finished - total_started),
            # Retained for consumers of the previous report schema.
            "load_plus_backtest": _round_seconds(run_finished - total_started),
            "semantic_digest": _round_seconds(digest_finished - digest_started),
        },
        "cache_stats": {
            "before": stats_before,
            "after_prepare": stats_after_prepare,
            "after_run": stats_after_run,
        },
    }


def _phase_summary(
    samples: Sequence[dict[str, Any]],
    *,
    keys: Sequence[str] = (
        "store_init",
        "prepare_input",
        "run_backtest",
        "prepare_plus_backtest",
    ),
) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in keys:
        values = [
            float(sample["phase_seconds"][key])
            for sample in samples
            if key in sample["phase_seconds"]
        ]
        if values:
            summary[key] = {
                "median": _round_seconds(float(statistics.median(values))),
                "min": _round_seconds(min(values)),
                "max": _round_seconds(max(values)),
                "samples": values,
            }
    return summary


def _assert_equivalent(samples: Sequence[dict[str, Any]]) -> str:
    if not samples:
        raise RuntimeError("benchmark produced no observations")
    reference = str(samples[0]["semantic_sha256"])
    for sample in samples[1:]:
        actual = str(sample["semantic_sha256"])
        if actual != reference:
            raise RuntimeError(
                "semantic digest changed between cold and warm runs: "
                f"{reference} != {actual}"
            )
    return reference


def _execute_sqlite_persistence_sample(
    api: RuntimeApi,
    *,
    session: Any,
    counter: SqlCounter,
    store: InstrumentedReplayStore,
    context: BenchmarkContext,
    bot_config_id: int,
    payload: Any,
) -> dict[str, Any]:
    progress_events: list[dict[str, Any]] = []
    sql_before = counter.total
    stats_before = store.stats()
    open_before = store.calls.open_candles
    load_before = store.calls.load_candles
    started = time.perf_counter()
    try:
        row = api.create_bot_backtest(
            session,
            user_id=OWNER_ID,
            bot_config_id=bot_config_id,
            payload=payload,
            now=context.end,
            progress_callback=lambda event: progress_events.append(dict(event)),
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finished = time.perf_counter()
    result = dict(row.result_snapshot)
    digest_started = time.perf_counter()
    digest = _semantic_digest(result)
    digest_finished = time.perf_counter()
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    return {
        "persisted_id": int(row.id),
        "input_fingerprint": str(row.input_fingerprint),
        "output_bars": int(row.bar_count),
        "trade_count": int(metrics.get("trade_count", 0)),
        "semantic_sha256": digest,
        "result_cache_hit": any(
            event.get("cache_hit") is True for event in progress_events
        ),
        "source_calls": {
            "open_candles": store.calls.open_candles - open_before,
            "load_candles": store.calls.load_candles - load_before,
        },
        "sql_statements": counter.total - sql_before,
        "phase_seconds": {
            "create_and_commit": _round_seconds(finished - started),
            "semantic_digest": _round_seconds(digest_finished - digest_started),
        },
        "cache_stats": {
            "before": stats_before,
            "after": store.stats(),
        },
    }


def _run_sqlite_persistence_benchmark(
    api: RuntimeApi,
    args: argparse.Namespace,
    context: BenchmarkContext,
) -> dict[str, Any]:
    from sqlalchemy import create_engine, event, inspect
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        api.instrument_metadata.__table__,
        api.bot_config.__table__,
        api.bot_backtest.__table__,
    ]
    api.base.metadata.create_all(bind=engine, tables=tables)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    config = _make_config(
        api,
        args,
        unit=context.unit,
        unit_number=context.unit_number,
        contract_id=context.contract_id,
    )
    session.add(
        api.instrument_metadata(
            symbol=context.root,
            tick_size=context.tick_size,
            tick_value=context.tick_value,
        )
    )
    session.add(config)
    session.commit()
    bot_config_id = int(config.id)
    payload = api.bot_backtest_input(
        start=context.start,
        end=context.end,
        starting_balance=context.starting_balance,
        commission_per_contract=context.commission,
        slippage_ticks=context.slippage_ticks,
        force_close_at_end=True,
    )
    raw_store = api.replay_store(args.cache_dir, build_missing_timeframes=False)
    store = InstrumentedReplayStore(raw_store)
    module = api.backtesting_module
    prior_default_store = module.get_default_databento_cache
    prior_result_cache = module._BACKTEST_RESULT_CACHE
    result_cache_type = type(prior_result_cache)
    module.get_default_databento_cache = lambda: store
    counter = SqlCounter()
    event.listen(engine, "before_cursor_execute", counter.before_cursor_execute)
    try:
        cold: list[dict[str, Any]] = []
        for _ in range(args.cold_repeats):
            store.clear()
            module._BACKTEST_RESULT_CACHE = result_cache_type()
            gc.collect()
            cold.append(
                _execute_sqlite_persistence_sample(
                    api,
                    session=session,
                    counter=counter,
                    store=store,
                    context=context,
                    bot_config_id=bot_config_id,
                    payload=payload,
                )
            )

        warm: list[dict[str, Any]] = []
        for _ in range(args.warm_repeats):
            gc.collect()
            warm.append(
                _execute_sqlite_persistence_sample(
                    api,
                    session=session,
                    counter=counter,
                    store=store,
                    context=context,
                    bot_config_id=bot_config_id,
                    payload=payload,
                )
            )

        samples = [*cold, *warm]
        digest = _assert_equivalent(samples)
        fingerprints = {sample["input_fingerprint"] for sample in samples}
        if len(fingerprints) != 1:
            raise RuntimeError(
                "input fingerprint changed between SQLite cold and warm runs"
            )
        if any(sample["result_cache_hit"] for sample in cold):
            raise RuntimeError("a cold SQLite sample unexpectedly hit the result LRU")
        if any(sample["source_calls"]["open_candles"] < 1 for sample in cold):
            raise RuntimeError(
                "a cold SQLite sample did not exercise the application lazy mmap path"
            )
        if any(sample["source_calls"]["load_candles"] for sample in samples):
            raise RuntimeError(
                "the application benchmark unexpectedly materialized candle objects"
            )
        if not all(sample["result_cache_hit"] for sample in warm):
            raise RuntimeError(
                "a warm SQLite sample missed the result LRU; increase the result cache limits"
            )
        cold_summary = _phase_summary(cold, keys=("create_and_commit",))
        warm_summary = _phase_summary(warm, keys=("create_and_commit",))
        cold_total = float(cold_summary["create_and_commit"]["median"])
        warm_total = float(warm_summary["create_and_commit"]["median"])
        active_result_cache = module._BACKTEST_RESULT_CACHE
        persisted_rows = int(session.query(api.bot_backtest).count())
        return {
            "definition": (
                "create_bot_backtest plus an in-memory SQLite commit; schema setup "
                "and semantic digest serialization are excluded"
            ),
            "input_mode": "application_lazy_mmap",
            "cold_source_method": "open_candles",
            "tables": sorted(inspect(engine).get_table_names()),
            "persisted_rows": persisted_rows,
            "semantic_sha256": digest,
            "semantic_match": True,
            "input_fingerprint": next(iter(fingerprints)),
            "result_cache": {
                "max_entries": int(active_result_cache.max_entries),
                "max_bytes": int(active_result_cache.max_bytes),
            },
            "cold": {
                "repeats": len(cold),
                "phase_seconds": cold_summary,
                "samples": cold,
            },
            "warm": {
                "repeats": len(warm),
                "phase_seconds": warm_summary,
                "samples": warm,
            },
            "cold_to_warm_speedup": (
                round(cold_total / warm_total, 3) if warm_total > 0 else None
            ),
        }
    finally:
        event.remove(engine, "before_cursor_execute", counter.before_cursor_execute)
        module.get_default_databento_cache = prior_default_store
        module._BACKTEST_RESULT_CACHE = prior_result_cache
        store.clear()
        session.close()
        api.base.metadata.drop_all(bind=engine, tables=list(reversed(tables)))
        engine.dispose()


def _run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    api = _load_runtime_api()
    if args.fast_period >= args.slow_period:
        raise ValueError("--fast-period must be less than --slow-period")
    unit, unit_number = api.parse_timeframe(args.timeframe)
    timeframe = api.timeframe_key(unit, unit_number)
    start, end, history_bounds, cache_root = _resolve_window(api, args)
    contract_id = f"CON.F.US.{args.root}.DATABENTO"
    tick_size, tick_value = ROOT_MARKET_VALUES[args.root]
    context = BenchmarkContext(
        root=args.root,
        unit=unit,
        unit_number=unit_number,
        start=start,
        end=end,
        input_mode=args.input_mode,
        max_rows=args.max_rows,
        config=_make_config(
            api,
            args,
            unit=unit,
            unit_number=unit_number,
            contract_id=contract_id,
        ),
        contract_id=contract_id,
        starting_balance=args.starting_balance,
        commission=args.commission,
        slippage_ticks=args.slippage_ticks,
        tick_size=tick_size,
        tick_value=tick_value,
    )

    cold: list[dict[str, Any]] = []
    for _ in range(args.cold_repeats):
        gc.collect()
        init_started = time.perf_counter()
        store = api.replay_store(args.cache_dir, build_missing_timeframes=False)
        init_seconds = time.perf_counter() - init_started
        try:
            sample = _execute_sample(api, store, context)
            sample["phase_seconds"]["store_init"] = _round_seconds(init_seconds)
            cold.append(sample)
        finally:
            store.clear()

    warm_store = api.replay_store(args.cache_dir, build_missing_timeframes=False)
    profile_report: dict[str, Any] | None = None
    try:
        gc.collect()
        warmup = _execute_sample(api, warm_store, context)
        warm: list[dict[str, Any]] = []
        for _ in range(args.warm_repeats):
            gc.collect()
            warm.append(_execute_sample(api, warm_store, context))

        profile_sample: dict[str, Any] | None = None
        if args.profile is not None:
            profile_path = args.profile.expanduser().resolve()
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            profiler = cProfile.Profile()
            profiler.enable()
            try:
                profile_sample = _execute_sample(api, warm_store, context)
            finally:
                profiler.disable()
                profiler.dump_stats(str(profile_path))
            profile_report = {
                "path": str(profile_path),
                "semantic_sha256": profile_sample["semantic_sha256"],
                "phase_seconds": profile_sample["phase_seconds"],
                "cache_stats": profile_sample["cache_stats"],
            }
    finally:
        warm_store.clear()

    equivalent_samples = [*cold, warmup, *warm]
    if profile_sample is not None:
        equivalent_samples.append(profile_sample)
    digest = _assert_equivalent(equivalent_samples)
    cold_summary = _phase_summary(cold)
    warm_summary = _phase_summary(warm)
    cold_total = float(cold_summary["prepare_plus_backtest"]["median"])
    warm_total = float(warm_summary["prepare_plus_backtest"]["median"])
    report = {
        "benchmark": "topsignal_databento_cache_backtest",
        "engine_version": api.engine_version,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cache_dir": str(cache_root),
        "case": {
            "root": args.root,
            "timeframe": timeframe,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "cached_history_start": history_bounds[0].isoformat(),
            "cached_history_end": history_bounds[1].isoformat(),
            "input_bars": cold[0]["input_bars"],
            "output_bars": cold[0]["output_bars"],
            "trade_count": cold[0]["trade_count"],
            "warning_count": cold[0]["warning_count"],
            "input_mode": cold[0]["input_mode"],
            "source_method": cold[0]["source_method"],
            "input_type": cold[0]["input_type"],
            "lazy_mmap": cold[0]["lazy_mmap"],
            "storage_bytes_per_row": cold[0]["storage_bytes_per_row"],
            "series_fingerprint": cold[0]["series_fingerprint"],
            "input_fingerprint": cold[0]["input_fingerprint"],
        },
        "methodology": {
            "cold_definition": (
                "new DatabentoReplayStore with no mapped series or materialized "
                "slices; operating-system page cache is not evicted"
            ),
            "warm_definition": (
                "same store and identical slice after one untimed "
                "input-preparation-and-backtest pass"
            ),
            "direct_input_mode": args.input_mode,
            "timed_phases": ["store_init", "prepare_input", "run_backtest"],
            "semantic_digest_excluded_from_replay_timing": True,
            "semantic_fields": list(SEMANTIC_KEYS),
        },
        "semantic_sha256": digest,
        "semantic_match": True,
        "cold": {
            "repeats": len(cold),
            "phase_seconds": cold_summary,
            "samples": cold,
        },
        "warm": {
            "preparation": warmup,
            "repeats": len(warm),
            "phase_seconds": warm_summary,
            "samples": warm,
        },
        "cold_to_warm_total_speedup": (
            round(cold_total / warm_total, 3) if warm_total > 0 else None
        ),
        "profile": profile_report,
    }
    report["sqlite_persistence"] = (
        _run_sqlite_persistence_benchmark(api, args, context)
        if args.sqlite_persistence
        else None
    )
    return report


def _print_text(report: dict[str, Any]) -> None:
    case = report["case"]
    cold = report["cold"]["phase_seconds"]
    warm = report["warm"]["phase_seconds"]
    print("TopSignal Databento cache + backtest benchmark")
    print(
        f"root={case['root']} timeframe={case['timeframe']} "
        f"bars={case['input_bars']:,} "
        f"trades={case['trade_count']} warnings={case['warning_count']}"
    )
    print(
        f"input={case['input_mode']} method={case['source_method']} "
        f"type={case['input_type']} lazy_mmap={case['lazy_mmap']}"
    )
    print(f"range={case['start']} .. {case['end']}")
    print("cold means an empty in-process LRU; the OS filesystem cache is not evicted")
    for name, phases in (("cold", cold), ("warm", warm)):
        print(f"{name} ({report[name]['repeats']} sample(s))")
        print(
            "  median seconds: "
            f"prepare_input={phases['prepare_input']['median']:.6f} "
            f"run_backtest={phases['run_backtest']['median']:.6f} "
            f"combined={phases['prepare_plus_backtest']['median']:.6f}"
        )
    print(f"cold/warm combined speedup: {report['cold_to_warm_total_speedup']}x")
    print(f"semantic match: {report['semantic_match']} ({report['semantic_sha256']})")
    cold_stats = report["cold"]["samples"][0]["cache_stats"]["after_prepare"]
    warm_stats = report["warm"]["samples"][-1]["cache_stats"]["after_prepare"]
    print(f"cache after cold input preparation: {cold_stats}")
    print(f"cache after final warm input preparation: {warm_stats}")
    if report["profile"] is not None:
        print(f"cProfile data: {report['profile']['path']}")
    persistence = report.get("sqlite_persistence")
    if persistence is not None:
        persistence_cold = persistence["cold"]["phase_seconds"][
            "create_and_commit"
        ]
        persistence_warm = persistence["warm"]["phase_seconds"][
            "create_and_commit"
        ]
        print("SQLite create_bot_backtest + commit")
        print(
            "  median seconds: "
            f"cold={persistence_cold['median']:.6f} "
            f"warm={persistence_warm['median']:.6f} "
            f"speedup={persistence['cold_to_warm_speedup']}x"
        )
        print(
            f"  persisted rows={persistence['persisted_rows']} "
            f"semantic match={persistence['semantic_match']} "
            f"warm result-LRU hits="
            f"{sum(sample['result_cache_hit'] for sample in persistence['warm']['samples'])}/"
            f"{persistence['warm']['repeats']}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        report = _run_benchmark(args)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        if args.json:
            print(
                json.dumps(
                    {"ok": False, "error": type(exc).__name__, "message": str(exc)},
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_text(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
