"""Measure the code-owned TopBot preset against the local MNQ cache.

This offline tool runs one chronological replay, optionally followed by a fresh
final-20% diagnostic. It never uses the result LRU, writes to the database, or
calls a provider or order router.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import os
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.trading_costs import MNQ_FEES_PER_CONTRACT_PER_SIDE


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=BACKEND_ROOT / "storage/databento")
    parser.add_argument("--days", type=float, default=30)
    parser.add_argument("--commission-per-side", type=float, default=MNQ_FEES_PER_CONTRACT_PER_SIDE,
                        help="all transaction fees per contract per side; default 0.61 ($1.22 round trip)")
    parser.add_argument("--end", help="UTC ISO timestamp; defaults to the cached history end")
    parser.add_argument("--profile", type=Path, help="optional cProfile output file")
    parser.add_argument("--holdout", action="store_true", help="also replay the final 20% with fresh portfolio state")
    parser.add_argument("--output", type=Path, help="save the metrics report as JSON")
    parser.add_argument("--trades-output", type=Path, help="save the complete closed-trade ledger as JSON")
    args = parser.parse_args()
    if not 0 < args.days < float("inf"):
        parser.error("--days must be a finite positive number")
    if not 0 <= args.commission_per_side < float("inf"):
        parser.error("--commission-per-side must be finite and nonnegative")

    # Imports need database configuration; this command never opens a session.
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    from app.models import BotConfig
    from app.services import bot_backtesting as replay
    from app.services.databento_cache import DatabentoReplayStore
    from app.services.topbot import TOPBOT_SETTINGS

    store = DatabentoReplayStore(args.cache_dir)
    try:
        bounds = store.history_bounds("MNQ")
        if bounds is None:
            parser.error("MNQ history is missing from the local cache")
        end = datetime.fromisoformat(args.end.replace("Z", "+00:00")) if args.end else bounds[1]
        end = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end.astimezone(timezone.utc)
        end = min(end, bounds[1])
        start = max(bounds[0], end - timedelta(days=args.days))
        if start >= end:
            parser.error("requested window does not overlap cached MNQ history")
        config = BotConfig(
            id=1, user_id="benchmark", account_id=1, name="TopBot",
            provider="projectx", enabled=False, execution_mode="dry_run",
            contract_id="CON.F.US.MNQ.U26", **deepcopy(TOPBOT_SETTINGS),
        )
        key = replay._topbot_asset_stream_key(config.timeframe_unit, config.timeframe_unit_number)
        warmup = replay._topbot_stream_specs(config)[key].warmup_bars
        primary = store.open_candles(
            user_id=config.user_id, contract_id=config.contract_id, root_symbol="MNQ",
            unit=config.timeframe_unit, unit_number=config.timeframe_unit_number,
            start=max(bounds[0], replay._databento_warmup_start(
                start, unit=config.timeframe_unit, unit_number=config.timeframe_unit_number,
                warmup_bars=warmup,
            )), end=end, closed_by=end,
        )
        streams = replay._load_databento_topbot_replay_streams(
            None, user_id=config.user_id, config=config, root_symbol="MNQ",
            window=replay._ResolvedBacktestWindow(start, end, start, end, False),
            closed_by=end, primary_rows=primary, max_rows=10_000_000, replay_store=store,
        )
        before = perf_counter()
        engine = replay.BacktestEngine(
            config=config, candles=primary, replay_streams=streams,
            settings=replay.BacktestSettings(
                start=start, end=end, starting_balance=50_000,
                commission_per_contract=args.commission_per_side, slippage_ticks=1, tick_size=0.25, tick_value=0.5,
            ),
        )
        initialization = perf_counter() - before
        profiler = cProfile.Profile() if args.profile else None
        before = perf_counter()
        if profiler:
            profiler.enable()
        result = engine.run()
        if args.holdout:
            result["evaluation_split"] = replay._build_chronological_holdout_evaluation(
                engine, full_result=result, replay_streams=streams, cancellation_callback=None,
            )
        elapsed = perf_counter() - before
        if profiler:
            profiler.disable()
            args.profile.parent.mkdir(parents=True, exist_ok=True)
            profiler.dump_stats(str(args.profile))
        serialized = json.dumps(result, default=str, sort_keys=True)
        report = {
            "strategy": config.strategy_type,
            "strategy_params": config.strategy_params,
            "config_snapshot": result["config_snapshot"],
            "assumptions": result["assumptions"],
            "engine_initialization_seconds": initialization,
            "replay_seconds": elapsed,
            "holdout_included": args.holdout,
            "range": result["range"],
            "trades": len(result["trades"]),
            "metrics": result["metrics"],
            "evaluation_split": result.get("evaluation_split"),
            "warnings": result["warnings"],
            "notes": result["notes"],
            "data_quality": result["data_quality"],
            "source_fingerprint": primary[0].source_file_sha256,
            "trades_sha256": hashlib.sha256(json.dumps(result["trades"], sort_keys=True).encode()).hexdigest(),
            "semantic_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
        }
        serialized_report = json.dumps(report, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized_report, encoding="utf-8")
        if args.trades_output:
            args.trades_output.parent.mkdir(parents=True, exist_ok=True)
            args.trades_output.write_text(json.dumps(result["trades"], indent=2), encoding="utf-8")
        print(serialized_report)
    finally:
        store.clear()


if __name__ == "__main__":
    main()
