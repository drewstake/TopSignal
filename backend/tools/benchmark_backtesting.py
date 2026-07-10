#!/usr/bin/env python3
"""Reproducible performance harness for TopSignal's backtest engine.

The timed passes exclude synthetic candle construction, semantic hashing, and
SQLite setup/seeding.  ``tracemalloc`` is deliberately run in a separate pass
because its instrumentation materially changes wall-clock timings.

Examples (run from the repository root):

    python backend/tools/benchmark_backtesting.py
    python backend/tools/benchmark_backtesting.py --bars 20000 50000 --repeats 5
    python backend/tools/benchmark_backtesting.py --bars 20000 --lookback-bars 200 --fast-period 20 --slow-period 50
    python backend/tools/benchmark_backtesting.py --bars 5000 --sqlite-public
    python backend/tools/benchmark_backtesting.py --bars 100 --repeats 1 --json
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import platform
import statistics
import sys
import time
import tracemalloc
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Sequence


BACKEND_ROOT = Path(
    os.environ.get("TOPSIGNAL_BENCHMARK_BACKEND_ROOT")
    or Path(__file__).resolve().parents[1]
).resolve()
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# App settings require a syntactically valid database URL at import time.  The
# benchmark's optional database case still creates and owns a separate SQLite
# engine; it never connects through this setting.
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

OWNER_ID = "11111111-1111-1111-1111-111111111111"
CONTRACT_ID = "CON.F.US.MNQ.BENCH"
SYMBOL = "MNQ"
INTERVAL = timedelta(minutes=5)
START = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)
FAST_PERIOD = 9
SLOW_PERIOD = 21
MINIMUM_BARS = SLOW_PERIOD + 2
DEFAULT_BARS = 5_000
DEFAULT_REPEATS = 3
DEFAULT_WARMUPS = 1
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

Result = dict[str, Any]
Runner = Callable[[], Result]


@dataclass(frozen=True)
class AppApi:
    """Late-bound application objects so ``--help`` needs only the stdlib."""

    engine_version: str
    Base: Any
    BotBacktest: Any
    BotBacktestIn: Any
    BotConfig: Any
    ProjectXMarketCandle: Any
    SignalResult: Any
    create_bot_backtest: Callable[..., Any]
    run_backtest: Callable[..., Result]


@dataclass(frozen=True)
class Observation:
    seconds: float
    digest: str
    output_bars: int
    trade_count: int
    warning_count: int


@dataclass
class SqlCounter:
    """Count statements issued only while one public API sample is running."""

    statements: int = 0
    selects: int = 0
    candle_selects: int = 0

    def reset(self) -> None:
        self.statements = 0
        self.selects = 0
        self.candle_selects = 0

    def before_cursor_execute(
        self,
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        self.statements += 1
        normalized = " ".join(str(statement).upper().split())
        is_select = normalized.startswith("SELECT") or normalized.startswith("WITH")
        if is_select:
            self.selects += 1
            if "PROJECTX_MARKET_CANDLES" in normalized:
                self.candle_selects += 1

    def snapshot(self) -> dict[str, int]:
        return {
            "statements": self.statements,
            "selects": self.selects,
            "candle_selects": self.candle_selects,
        }


def _load_app_api() -> AppApi:
    from app.bot_schemas import BotBacktestIn
    from app.db import Base
    from app.models import BotBacktest, BotConfig, ProjectXMarketCandle
    from app.services.bot_backtesting import (
        BACKTEST_ENGINE_VERSION,
        create_bot_backtest,
        run_backtest,
    )
    from app.services.bot_service import SignalResult

    return AppApi(
        engine_version=BACKTEST_ENGINE_VERSION,
        Base=Base,
        BotBacktest=BotBacktest,
        BotBacktestIn=BotBacktestIn,
        BotConfig=BotConfig,
        ProjectXMarketCandle=ProjectXMarketCandle,
        SignalResult=SignalResult,
        create_bot_backtest=create_bot_backtest,
        run_backtest=run_backtest,
    )


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def _nonnegative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark deterministic real-SMA and scripted-HOLD backtests. "
            "Input construction and semantic hashing are excluded from wall timings."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--bars",
        nargs="+",
        type=_positive_int,
        default=[DEFAULT_BARS],
        metavar="N",
        help=(
            f"one or more synthetic input sizes (minimum {MINIMUM_BARS}); "
            "use 20000 and/or 50000 for full-history scaling runs"
        ),
    )
    parser.add_argument(
        "--repeats",
        type=_positive_int,
        default=DEFAULT_REPEATS,
        help="timed samples per case",
    )
    parser.add_argument(
        "--warmups",
        type=_nonnegative_int,
        default=DEFAULT_WARMUPS,
        help="untimed warmup passes per case",
    )
    parser.add_argument(
        "--case",
        choices=("all", "sma", "hold"),
        default="all",
        help="engine-only case selection",
    )
    parser.add_argument(
        "--lookback-bars",
        type=_positive_int,
        default=25,
        help="configured rolling evaluator history",
    )
    parser.add_argument(
        "--fast-period",
        type=_positive_int,
        default=FAST_PERIOD,
        help="SMA fast period",
    )
    parser.add_argument(
        "--slow-period",
        type=_positive_int,
        default=SLOW_PERIOD,
        help="SMA slow period",
    )
    parser.add_argument(
        "--sqlite-public",
        action="store_true",
        help=(
            "also exercise create_bot_backtest through an in-memory SQLite database "
            "and report SQL/candle SELECT counts"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of the text summary",
    )
    return parser


def _config(
    api: AppApi,
    *,
    persisted: bool,
    lookback_bars: int,
    fast_period: int,
    slow_period: int,
) -> Any:
    return api.BotConfig(
        id=None if persisted else 1,
        user_id=OWNER_ID,
        account_id=9001,
        name="Backtest benchmark",
        provider="projectx",
        enabled=False,
        execution_mode="dry_run",
        strategy_type="sma_cross",
        strategy_params={},
        contract_id=CONTRACT_ID,
        symbol=SYMBOL,
        timeframe_unit="minute",
        timeframe_unit_number=5,
        lookback_bars=lookback_bars,
        fast_period=fast_period,
        slow_period=slow_period,
        order_size=2,
        max_contracts=10,
        max_daily_loss=1_000_000,
        max_trades_per_day=10_000,
        max_open_position=10,
        allowed_contracts=[CONTRACT_ID],
        trading_start_time="00:00",
        trading_end_time="23:59",
        cooldown_seconds=0,
        max_data_staleness_seconds=3_600,
        allow_market_depth=False,
    )


def _generate_candles(api: AppApi, bar_count: int) -> list[Any]:
    """Build tick-aligned candles using integer arithmetic only."""

    candles: list[Any] = []
    previous_close_ticks = 40_000
    for index in range(bar_count):
        phase = index % 80
        triangle = phase if phase <= 40 else 80 - phase
        wave_ticks = (triangle - 20) * 3
        drift_ticks = (index // 240) % 20
        close_ticks = 40_000 + wave_ticks + drift_ticks
        open_ticks = previous_close_ticks + ((index % 3) - 1)
        wick_ticks = 2 + (index % 4)
        high_ticks = max(open_ticks, close_ticks) + wick_ticks
        low_ticks = min(open_ticks, close_ticks) - wick_ticks
        timestamp = START + INTERVAL * index
        candles.append(
            api.ProjectXMarketCandle(
                user_id=OWNER_ID,
                contract_id=CONTRACT_ID,
                symbol=SYMBOL,
                live=False,
                unit="minute",
                unit_number=5,
                candle_timestamp=timestamp,
                open_price=open_ticks * 0.25,
                high_price=high_ticks * 0.25,
                low_price=low_ticks * 0.25,
                close_price=close_ticks * 0.25,
                volume=100 + ((index * 37) % 900),
                is_partial=False,
                source="benchmark",
                raw_payload=None,
                fetched_at=timestamp + INTERVAL,
            )
        )
        previous_close_ticks = close_ticks
    return candles


def _scripted_hold(api: AppApi) -> Callable[[list[Any]], Any]:
    def evaluate(candles: list[Any]) -> Any:
        latest = candles[-1]
        timestamp = latest.candle_timestamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        else:
            timestamp = timestamp.astimezone(timezone.utc)
        return api.SignalResult(
            action="HOLD",
            reason="deterministic benchmark hold",
            candle_timestamp=timestamp,
            price=float(latest.close_price),
            raw_payload={"benchmark_case": "scripted_hold"},
        )

    return evaluate


def _direct_runner(
    api: AppApi,
    candles: list[Any],
    *,
    scripted_hold: bool,
    lookback_bars: int,
    fast_period: int,
    slow_period: int,
) -> Runner:
    config = _config(
        api,
        persisted=False,
        lookback_bars=lookback_bars,
        fast_period=fast_period,
        slow_period=slow_period,
    )
    start = candles[0].candle_timestamp
    end = candles[-1].candle_timestamp + INTERVAL
    evaluator = _scripted_hold(api) if scripted_hold else None

    def run() -> Result:
        return api.run_backtest(
            config=config,
            candles=candles,
            start=start,
            end=end,
            starting_balance=50_000,
            commission_per_contract=1.25,
            slippage_ticks=1,
            tick_size=0.25,
            tick_value=0.50,
            force_close_at_end=True,
            signal_evaluator=evaluator,
        )

    return run


def _semantic_json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        normalized = (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )
        return normalized.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"unsupported semantic value: {type(value).__name__}")


def _semantic_digest(result: Result) -> str:
    missing = [key for key in SEMANTIC_KEYS if key not in result]
    if missing:
        raise KeyError(f"backtest result is missing semantic fields: {', '.join(missing)}")
    semantic = {key: result[key] for key in SEMANTIC_KEYS}
    canonical = json.dumps(
        semantic,
        allow_nan=False,
        default=_semantic_json_default,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _observe(result: Result, seconds: float) -> Observation:
    result_range = result.get("range") if isinstance(result.get("range"), dict) else {}
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    return Observation(
        seconds=seconds,
        digest=_semantic_digest(result),
        output_bars=int(result_range.get("bar_count", 0)),
        trade_count=int(metrics.get("trade_count", 0)),
        warning_count=len(warnings),
    )


def _assert_equivalent(name: str, observations: Sequence[Observation]) -> Observation:
    if not observations:
        raise RuntimeError(f"{name}: benchmark produced no observations")
    reference = observations[0]
    for observation in observations[1:]:
        if observation.digest != reference.digest:
            raise RuntimeError(
                f"{name}: semantic hash changed between benchmark passes "
                f"({reference.digest} != {observation.digest})"
            )
        if (
            observation.output_bars,
            observation.trade_count,
            observation.warning_count,
        ) != (
            reference.output_bars,
            reference.trade_count,
            reference.warning_count,
        ):
            raise RuntimeError(f"{name}: result summary changed between benchmark passes")
    return reference


def _distribution(values: Sequence[float | int]) -> dict[str, float | int]:
    if not values:
        raise ValueError("cannot summarize an empty distribution")
    return {
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def _benchmark_runner(
    *,
    name: str,
    input_bars: int,
    runner: Runner,
    repeats: int,
    warmups: int,
    sql_counter: SqlCounter | None = None,
) -> dict[str, Any]:
    observations: list[Observation] = []
    sql_samples: list[dict[str, int]] = []

    for _ in range(warmups):
        gc.collect()
        if sql_counter is not None:
            sql_counter.reset()
        observations.append(_observe(runner(), seconds=0.0))

    timed: list[Observation] = []
    for _ in range(repeats):
        gc.collect()
        if sql_counter is not None:
            sql_counter.reset()
        started = time.perf_counter()
        result = runner()
        elapsed = time.perf_counter() - started
        if sql_counter is not None:
            sql_samples.append(sql_counter.snapshot())
        observation = _observe(result, seconds=elapsed)
        observations.append(observation)
        timed.append(observation)

    # This is intentionally not a timed sample.  Starting tracemalloc only
    # after input construction reports incremental engine/public-path memory.
    gc.collect()
    if sql_counter is not None:
        sql_counter.reset()
    tracemalloc.start()
    try:
        memory_result = runner()
        _current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    memory_sql = sql_counter.snapshot() if sql_counter is not None else None
    memory_observation = _observe(memory_result, seconds=0.0)
    observations.append(memory_observation)

    reference = _assert_equivalent(name, observations)
    wall_samples = [observation.seconds for observation in timed]
    wall = _distribution(wall_samples)
    output: dict[str, Any] = {
        "name": name,
        "input_bars": input_bars,
        "output_bars": reference.output_bars,
        "trade_count": reference.trade_count,
        "warning_count": reference.warning_count,
        "warmups": warmups,
        "timed_repeats": repeats,
        "wall_seconds": {
            "median": round(float(wall["median"]), 9),
            "min": round(float(wall["min"]), 9),
            "max": round(float(wall["max"]), 9),
            "samples": [round(value, 9) for value in wall_samples],
        },
        "median_seconds_per_1000_input_bars": round(
            float(wall["median"]) * 1_000 / input_bars,
            9,
        ),
        "tracemalloc_peak_bytes": peak_bytes,
        "tracemalloc_peak_mib": round(peak_bytes / (1024 * 1024), 3),
        "semantic_sha256": reference.digest,
        "semantic_fields": list(SEMANTIC_KEYS),
    }
    if sql_counter is not None:
        output["sql_counts"] = {
            "timed_samples": sql_samples,
            "statements": _distribution(
                [sample["statements"] for sample in sql_samples]
            ),
            "selects": _distribution([sample["selects"] for sample in sql_samples]),
            "candle_selects": _distribution(
                [sample["candle_selects"] for sample in sql_samples]
            ),
            "memory_pass": memory_sql,
        }
    return output


def _candle_mapping(candle: Any) -> dict[str, Any]:
    return {
        "user_id": candle.user_id,
        "contract_id": candle.contract_id,
        "symbol": candle.symbol,
        "live": candle.live,
        "unit": candle.unit,
        "unit_number": candle.unit_number,
        "candle_timestamp": candle.candle_timestamp,
        "open_price": candle.open_price,
        "high_price": candle.high_price,
        "low_price": candle.low_price,
        "close_price": candle.close_price,
        "volume": candle.volume,
        "is_partial": candle.is_partial,
        "source": candle.source,
        "raw_payload": candle.raw_payload,
        "fetched_at": candle.fetched_at,
    }


def _sqlite_public_runner(
    api: AppApi,
    candles: list[Any],
    *,
    lookback_bars: int,
    fast_period: int,
    slow_period: int,
) -> tuple[Runner, SqlCounter, Callable[[], None]]:
    """Create a seeded SQLite session; setup work is outside all samples."""

    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        api.BotConfig.__table__,
        api.ProjectXMarketCandle.__table__,
        api.BotBacktest.__table__,
    ]
    api.Base.metadata.create_all(bind=engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    config = _config(
        api,
        persisted=True,
        lookback_bars=lookback_bars,
        fast_period=fast_period,
        slow_period=slow_period,
    )
    session.add(config)
    session.flush()
    config_id = int(config.id)

    insert_candle = api.ProjectXMarketCandle.__table__.insert()
    chunk_size = 1_000
    for offset in range(0, len(candles), chunk_size):
        stop = min(len(candles), offset + chunk_size)
        batch = [_candle_mapping(candles[index]) for index in range(offset, stop)]
        session.execute(insert_candle, batch)
    session.commit()

    payload = api.BotBacktestIn(
        starting_balance=50_000,
        commission_per_contract=1.25,
        slippage_ticks=1,
        force_close_at_end=True,
    )
    captured_now = candles[-1].candle_timestamp + INTERVAL
    counter = SqlCounter()
    event.listen(engine, "before_cursor_execute", counter.before_cursor_execute)

    def run() -> Result:
        row = api.create_bot_backtest(
            session,
            user_id=OWNER_ID,
            bot_config_id=config_id,
            payload=payload,
            now=captured_now,
        )
        return row.result_snapshot

    def close() -> None:
        event.remove(engine, "before_cursor_execute", counter.before_cursor_execute)
        session.close()
        api.Base.metadata.drop_all(bind=engine, tables=reversed(tables))
        engine.dispose()

    return run, counter, close


def _unique_bar_counts(values: Sequence[int]) -> list[int]:
    output: list[int] = []
    for value in values:
        if value not in output:
            output.append(value)
    return output


def _run_benchmarks(args: argparse.Namespace) -> dict[str, Any]:
    api = _load_app_api()
    cases: list[dict[str, Any]] = []
    bar_counts = _unique_bar_counts(args.bars)
    for bar_count in bar_counts:
        candles = _generate_candles(api, bar_count)
        if args.case in {"all", "sma"}:
            cases.append(
                _benchmark_runner(
                    name="direct_real_sma",
                    input_bars=bar_count,
                    runner=_direct_runner(
                        api,
                        candles,
                        scripted_hold=False,
                        lookback_bars=args.lookback_bars,
                        fast_period=args.fast_period,
                        slow_period=args.slow_period,
                    ),
                    repeats=args.repeats,
                    warmups=args.warmups,
                )
            )
        if args.case in {"all", "hold"}:
            cases.append(
                _benchmark_runner(
                    name="direct_scripted_hold",
                    input_bars=bar_count,
                    runner=_direct_runner(
                        api,
                        candles,
                        scripted_hold=True,
                        lookback_bars=args.lookback_bars,
                        fast_period=args.fast_period,
                        slow_period=args.slow_period,
                    ),
                    repeats=args.repeats,
                    warmups=args.warmups,
                )
            )
        if args.sqlite_public:
            runner, counter, close = _sqlite_public_runner(
                api,
                candles,
                lookback_bars=args.lookback_bars,
                fast_period=args.fast_period,
                slow_period=args.slow_period,
            )
            try:
                cases.append(
                    _benchmark_runner(
                        name="sqlite_public_create_bot_backtest_sma",
                        input_bars=bar_count,
                        runner=runner,
                        repeats=args.repeats,
                        warmups=args.warmups,
                        sql_counter=counter,
                    )
                )
            finally:
                close()

    return {
        "benchmark": "topsignal_backtest_engine",
        "backend_root": str(BACKEND_ROOT),
        "engine_version": api.engine_version,
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "timer": "time.perf_counter wall seconds",
        "methodology": {
            "deterministic_input": (
                "five-minute tick-aligned triangular price series; integer construction"
            ),
            "excluded_from_wall_time": [
                "synthetic candle generation",
                "garbage collection immediately before each sample",
                "semantic SHA256 serialization",
                "SQLite schema creation and candle seeding",
            ],
            "memory": (
                "separate tracemalloc pass; input candles and SQLite seed state pre-exist tracing"
            ),
            "semantic_sha256_fields": list(SEMANTIC_KEYS),
            "strategy_parameters": {
                "lookback_bars": args.lookback_bars,
                "fast_period": args.fast_period,
                "slow_period": args.slow_period,
            },
        },
        "cases": cases,
    }


def _print_text(report: dict[str, Any]) -> None:
    print("TopSignal backtest benchmark")
    print(
        f"engine={report['engine_version']} python={report['python']} "
        f"implementation={report['python_implementation']}"
    )
    print(
        "wall timings exclude input generation, pre-sample GC, semantic hashing, "
        "and SQLite setup/seeding"
    )
    print("tracemalloc peak comes from a separate, untimed pass")
    for case in report["cases"]:
        wall = case["wall_seconds"]
        print()
        print(
            f"{case['name']} bars={case['input_bars']} output_bars={case['output_bars']} "
            f"trades={case['trade_count']} warnings={case['warning_count']}"
        )
        print(
            "  wall_seconds "
            f"median={wall['median']:.6f} min={wall['min']:.6f} max={wall['max']:.6f} "
            f"samples={wall['samples']}"
        )
        print(
            f"  tracemalloc_peak={case['tracemalloc_peak_mib']:.3f} MiB "
            f"({case['tracemalloc_peak_bytes']} bytes)"
        )
        print(f"  semantic_sha256={case['semantic_sha256']}")
        sql_counts = case.get("sql_counts")
        if sql_counts is not None:
            statements = sql_counts["statements"]
            selects = sql_counts["selects"]
            candle_selects = sql_counts["candle_selects"]
            print(
                "  SQL/sample "
                f"statements median={statements['median']} min={statements['min']} "
                f"max={statements['max']}; selects median={selects['median']}; "
                f"candle_selects median={candle_selects['median']}"
            )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.fast_period >= args.slow_period:
        parser.error("--fast-period must be less than --slow-period")
    minimum_bars = args.slow_period + 2
    too_small = [value for value in args.bars if value < minimum_bars]
    if too_small:
        parser.error(
            f"--bars must be at least {minimum_bars} for the real SMA warmup; "
            f"received {', '.join(str(value) for value in too_small)}"
        )
    report = _run_benchmarks(args)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_text(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
