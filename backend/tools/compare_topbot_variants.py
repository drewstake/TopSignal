"""Offline, fixed-hypothesis TopBot comparison; never opens a broker or database.

Selection uses 2020-2023. Later periods are retrospective diagnostics because
this repository has already examined the full history. This is not an optimizer.
The exact baseline source and fixed candidate definitions accompany each report.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from time import perf_counter
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.trading_costs import MNQ_FEES_PER_CONTRACT_PER_SIDE

CANDIDATES = {
    "baseline": "V4 EMA/VWAP pullback, long bias, opposite-signal exits.",
    "bracket_only": "Same entries; hold for the 50/50 bracket, rollover or replay end. No opposite-signal exits.",
    "trend_alignment": "Long entries additionally require EMA20 above a rising EMA50. Preserve opposite-signal exits.",
    "no_chase": "Entries must close within 25 points (half planned risk) of EMA20. Preserve opposite-signal exits.",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source", type=Path, default=ROOT / "tools/fixtures/topbot_v4.py")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "storage/databento")
    parser.add_argument("--period", choices=["selection", "diagnostic", "full"], required=True)
    parser.add_argument("--variants", nargs="+", choices=list(CANDIDATES), default=list(CANDIDATES))
    parser.add_argument("--slippage", type=float, default=1)
    parser.add_argument("--commission-per-side", type=float, default=MNQ_FEES_PER_CONTRACT_PER_SIDE,
                        help="all transaction fees per contract per side; default 0.61 ($1.22 round trip)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not math.isfinite(args.slippage) or args.slippage < 0:
        parser.error("--slippage must be a finite nonnegative number")
    if not math.isfinite(args.commission_per_side) or args.commission_per_side < 0:
        parser.error("--commission-per-side must be a finite nonnegative number")
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    from app.models import BotConfig
    from app.services import bot_backtesting as replay, bot_service
    from app.services.databento_cache import DatabentoReplayStore
    from app.services.topbot import TOPBOT_SETTINGS

    source = args.baseline_source.read_text(encoding="utf-8")
    baseline = ModuleType("app.services._topbot_comparison_baseline")
    baseline.__package__ = "app.services"
    exec(compile(source, str(args.baseline_source), "exec"), baseline.__dict__)
    store = DatabentoReplayStore(args.cache_dir)
    try:
        bounds = store.history_bounds("MNQ")
        if bounds is None:
            parser.error("MNQ history is missing from the local cache; see docs/topbot-research-handoff.md")
        earliest, latest = bounds
        start, end = {
            "selection": (datetime(2020, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 1, tzinfo=timezone.utc)),
            "diagnostic": (datetime(2024, 1, 1, tzinfo=timezone.utc), latest),
            "full": (earliest, latest),
        }[args.period]
        if start >= end or end <= earliest or start >= latest:
            parser.error("requested period does not overlap cached MNQ history")
        config = BotConfig(
            id=1, user_id="offline-comparison", account_id=1, name="TopBot",
            provider="projectx", enabled=False, execution_mode="dry_run",
            contract_id="CON.F.US.MNQ.U26", **deepcopy(TOPBOT_SETTINGS),
        )
        primary = store.open_candles(
            user_id=config.user_id, contract_id=config.contract_id, root_symbol="MNQ",
            unit="minute", unit_number=5,
            start=max(earliest, replay._databento_warmup_start(start, unit="minute", unit_number=5, warmup_bars=200)),
            end=end, closed_by=end,
        )
        streams = {replay._topbot_asset_stream_key("minute", 5): primary}
        report = {
            "period": args.period, "requested_start": start.isoformat(), "requested_end": end.isoformat(),
            "baseline_source": source, "baseline_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "candidates": CANDIDATES, "commission_per_side": args.commission_per_side, "slippage_ticks": args.slippage,
            "source_fingerprint": primary[0].source_file_sha256, "results": {},
            "limitation": "Previously examined history; later periods are retrospective diagnostics, not independent validation.",
        }
        for variant in args.variants:
            class ComparisonEngine(replay.BacktestEngine):
                def _evaluate_topbot_adaptive(self, candles):
                    signal = baseline.evaluate(candles)
                    if signal.action == "HOLD" or variant == "baseline":
                        return signal
                    payload = dict(signal.raw_payload)
                    if variant == "bracket_only":
                        if payload.get("signal_category") == "exit":
                            return replace(signal, action="HOLD", reason="Experiment: bracket exits only")
                        payload["target_position_qty"] = 1.0 if signal.action == "BUY" else -1.0
                    elif payload.get("signal_category") != "exit":
                        allowed = True
                        if variant == "trend_alignment" and signal.action == "BUY":
                            ema50 = bot_service._ema_series(bot_service._candle_close_values(candles[-200:]), period=50)
                            allowed = payload["ema"] > ema50[-1] and ema50[-1] > ema50[-4]
                        elif variant == "no_chase":
                            allowed = abs(signal.price - payload["ema"]) <= 25.0
                        if not allowed:
                            payload.update(signal_category="exit", target_position_qty=0.0, exit_reason="opposite_signal_flatten")
                    return replace(signal, raw_payload=payload)

            started = perf_counter()
            engine = ComparisonEngine(
                config=config, candles=primary, replay_streams=streams,
                settings=replay.BacktestSettings(
                    start=start, end=end, starting_balance=50_000, commission_per_contract=args.commission_per_side,
                    slippage_ticks=args.slippage, tick_size=.25, tick_value=.5,
                ),
            )
            result = engine.run()
            annual = defaultdict(lambda: {"trades": 0, "net_pnl": 0, "long_net": 0, "short_net": 0})
            for trade in result["trades"]:
                group = annual[trade["entry_timestamp"][:4]]
                group["trades"] += 1
                group["net_pnl"] += trade["net_pnl"]
                group[f"{trade['side']}_net"] += trade["net_pnl"]
            report["results"][variant] = {
                "metrics": result["metrics"], "years": dict(annual), "range": result["range"],
                "config_snapshot": result["config_snapshot"], "assumptions": result["assumptions"],
                "warnings": result["warnings"], "notes": result["notes"],
                "data_quality": result["data_quality"],
                "seconds": perf_counter() - started,
                "trades_sha256": hashlib.sha256(json.dumps(result["trades"], sort_keys=True).encode()).hexdigest(),
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps({"variant": variant, **report["results"][variant]}), flush=True)
    finally:
        store.clear()


if __name__ == "__main__":
    main()
