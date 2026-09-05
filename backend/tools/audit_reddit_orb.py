"""Audit all saved ORB fills against source clocks, ranges, prices and accounting."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
import json
import math
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from tools.research_topbot import offline_environment, write_new_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=BACKEND / "storage/databento")
    parser.add_argument("--output", type=Path, help="new JSON file; existing evidence is never overwritten")
    args = parser.parse_args()
    offline_environment()
    from app.services.databento_cache import DatabentoReplayStore
    from app.services.trading_day import TRADING_TZ, trading_day_date
    store = DatabentoReplayStore(args.cache_dir)
    first, last = store.history_bounds("MNQ")
    common = dict(user_id="orb-audit", contract_id="CON.F.US.MNQ.U26", root_symbol="MNQ",
                  unit="minute", start=first, end=last, closed_by=last)
    fives, minutes = (store.open_candles(unit_number=interval, **common) for interval in (5, 1))

    def get(sequence, stamp):
        index = int(sequence.start_ns.searchsorted(int(stamp.timestamp() * 1_000_000_000)))
        row = sequence[index]
        assert row.candle_timestamp == stamp, (stamp, row.candle_timestamp)
        return row

    def equal(left, right):
        assert math.isclose(left, right, abs_tol=1e-7), (left, right)

    cases = {}
    for path in sorted(args.run_directory.glob("*.trades.json")):
        key = path.name.removesuffix(".trades.json")
        slip = float(key.rsplit("-", 1)[1]) * .25
        trades = json.loads(path.read_text(encoding="utf-8"))
        summary = json.loads(path.with_name(key + ".summary.json").read_text(encoding="utf-8"))
        sessions = json.loads(path.with_name(key + ".sessions.json").read_text(encoding="utf-8"))
        daily = defaultdict(float)
        exits = defaultdict(int)
        prior_exit = None
        for trade in trades:
            entered = datetime.fromisoformat(trade["entry_timestamp"])
            exited = datetime.fromisoformat(trade["exit_timestamp"])
            signal = datetime.fromisoformat(trade["signal_timestamp"])
            local = entered.astimezone(TRADING_TZ)
            assert time(10) <= local.time() < time(12) and local.minute % 15 == 0
            assert entered == signal + timedelta(minutes=5)
            assert prior_exit is None or prior_exit <= entered
            prior_exit = exited
            start = datetime.combine(local.date(), time(9, 30), tzinfo=TRADING_TZ).astimezone(timezone.utc)
            count = int((entered - start).total_seconds() / 300)
            prefix = [get(fives, start + timedelta(minutes=5 * index)) for index in range(count)]
            high = max(float(row.high_price) for row in prefix[:3])
            low = min(float(row.low_price) for row in prefix[:3])
            assert float(prefix[-3].open_price) <= high < float(prefix[-1].close_price)
            equal(trade["stop_loss"], low)
            source_entry = get(minutes, entered)
            equal(trade["entry_price"], float(source_entry.open_price) + slip)
            assert trade["source_raw_symbol"] == source_entry.source_raw_symbol
            assert trade["source_instrument_id"] == source_entry.source_instrument_id
            risk = trade["entry_price"] - trade["stop_loss"]
            assert .25 <= risk <= 100
            equal(trade["take_profit"], trade["entry_price"] + math.ceil(risk * 1.5 * 4) / 4)
            assert daily[trading_day_date(entered)] - risk * 2 > -250
            assert trade["side"] == "long" and trade["quantity"] == 1
            equal(trade["commission"], 1.22)
            equal(trade["gross_pnl"], (trade["exit_price"] - trade["entry_price"]) * 2)
            equal(trade["net_pnl"], trade["gross_pnl"] - 1.22)
            daily[trading_day_date(exited)] += trade["net_pnl"]
            reason = trade["exit_reason"]
            exits[reason] += 1
            if reason in ("stop_loss", "stop_loss_same_bar_conservative"):
                equal(trade["exit_price"], trade["stop_loss"] - slip)
                bar = get(minutes, exited - timedelta(minutes=1))
                assert float(bar.low_price) <= trade["stop_loss"]
                if reason == "stop_loss_same_bar_conservative":
                    assert float(bar.high_price) >= trade["take_profit"]
            elif reason == "take_profit":
                equal(trade["exit_price"], trade["take_profit"] - slip)
            elif reason in ("stop_loss_gap", "scheduled_session_flatten"):
                equal(trade["exit_price"], float(get(minutes, exited).open_price) - slip)
        net = sum(row["net_pnl"] for row in trades)
        equal(net, summary["metrics"]["net_pnl"])
        equal(net, sum(row["net_pnl"] for row in sessions))
        equal(summary["metrics"]["total_commission"], 1.22 * len(trades))
        cases[key] = {"trade_count": len(trades), "net_pnl": net,
                      "source_and_accounting_audit": "passed", "exit_reasons": dict(exits)}
    assert len(cases) == 6, "Audit requires all six declared cases"
    result = {"status": "passed", "cases": cases,
              "metadata_interpretation": "Original replay assumptions.bracket_rule and strategy_revision retain base-engine labels. The saved execution_adaptation, candidate_definition, candidate_fixture_revision and source snapshots identify the actual ORB rules. This audit independently checks absolute range-low stops; immutable replay files are unchanged.",
              "limitations": "Checks actual saved fills and signals; does not establish future profitability or actual queue execution."}
    store.clear()
    write_new_json(args.output or args.run_directory / "source-fill-audit.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
