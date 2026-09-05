"""Independent source-price, signal-clock and accounting audit of proxy ledgers."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from tools.research_topbot import offline_environment, write_new_json


def audit(run_directory, cache_dir):
    offline_environment()
    from app.services.databento_cache import DatabentoReplayStore
    from app.services.trading_day import TRADING_TZ, futures_holiday_schedule, trading_day_date

    manifest = json.loads((run_directory / "manifest.json").read_text(encoding="utf-8"))
    for relative in ("backend/tools/fixtures/reddit_scalper.py", "backend/tools/research_reddit_scalper.py"):
        captured = run_directory / "sources" / relative
        expected = manifest["code"]["files"][relative]["sha256"]
        assert hashlib.sha256(captured.read_bytes()).hexdigest() == expected
        assert hashlib.sha256((BACKEND.parent / relative).read_bytes()).hexdigest() == expected
    store = DatabentoReplayStore(cache_dir)
    first, last = store.history_bounds("MNQ")
    minutes = store.open_candles(user_id="scalper-audit", contract_id="CON.F.US.MNQ.U26",
        root_symbol="MNQ", unit="minute", unit_number=1, start=first, end=last, closed_by=last)

    def index(stamp):
        return int(minutes.start_ns.searchsorted(int(stamp.timestamp() * 1_000_000_000)))

    def get(stamp):
        row = minutes[index(stamp)]
        assert row.candle_timestamp == stamp, (stamp, row.candle_timestamp)
        return row

    def equal(left, right):
        assert math.isclose(left, right, abs_tol=1e-7), (left, right)

    def atr(rows, period):
        return sum(max(float(row.high_price) - float(row.low_price),
                       abs(float(row.high_price) - float(prior.close_price)),
                       abs(float(row.low_price) - float(prior.close_price)))
                   for prior, row in zip(rows[-period-1:-1], rows[-period:])) / period

    def deadline(entry):
        day = trading_day_date(entry)
        holiday = futures_holiday_schedule(day, symbol="MNQ")
        close = min(time(16), holiday.early_close) if holiday and holiday.early_close else time(16)
        session_deadline = datetime.combine(day, close, tzinfo=TRADING_TZ) - timedelta(minutes=5)
        return min(entry + timedelta(minutes=6), session_deadline.astimezone(timezone.utc))

    cases = {}
    for path in sorted(run_directory.glob("*.trades.json")):
        key = path.name.removesuffix(".trades.json")
        slip = float(key.rsplit("-", 1)[1]) * .25
        trades = json.loads(path.read_text(encoding="utf-8"))
        summary = json.loads(path.with_name(key + ".summary.json").read_text(encoding="utf-8"))
        sessions = json.loads(path.with_name(key + ".sessions.json").read_text(encoding="utf-8"))
        period = key.split("__")[1]
        start, end = (datetime.fromisoformat(manifest["periods"][period][name]) for name in ("start", "end"))
        daily, counts, exits = defaultdict(float), defaultdict(int), defaultdict(int)
        prior_exit = None
        delayed_deadlines = 0
        for trade in trades:
            entered, exited, signal = (datetime.fromisoformat(trade[name]) for name in
                                       ("entry_timestamp", "exit_timestamp", "signal_timestamp"))
            assert start <= entered <= exited <= end
            assert time(10) <= entered.astimezone(TRADING_TZ).time() < time(15, 45)
            assert entered == signal + timedelta(minutes=1)
            assert prior_exit is None or prior_exit <= entered
            prior_exit = exited
            side = 1 if trade["side"] == "long" else -1
            assert trade["quantity"] == 1
            source_entry = get(entered)
            equal(trade["entry_price"], float(source_entry.open_price) + side * slip)
            assert trade["source_instrument_id"] == source_entry.source_instrument_id
            assert trade["source_raw_symbol"] == source_entry.source_raw_symbol
            prefix = [get(signal - timedelta(minutes=31-i)) for i in range(32)]
            assert len({row.source_instrument_id for row in prefix}) == 1
            close = [float(row.close_price) for row in prefix]
            confirmed = False
            for period in (10, 30):
                mean = sum(close[-period:]) / period
                prior_mean = sum(close[-period-1:-1]) / period
                prior_atr = atr(prefix[:-1], period)
                long = close[-2] < prior_mean - 2 * prior_atr and close[-2] < close[-1] < mean
                short = close[-2] > prior_mean + 2 * prior_atr and close[-2] > close[-1] > mean
                if long or short:
                    assert side == (1 if long else -1)
                    confirmed = True
                    break
            assert confirmed
            risk = math.ceil(max(5., min(100., 2 * atr(prefix, 20))) * 4) / 4
            equal(trade["stop_loss"], trade["entry_price"] - side * risk)
            equal(trade["take_profit"], trade["entry_price"] + side * risk)
            day = trading_day_date(entered)
            assert daily[day] - risk * 2 > -250
            counts[day] += 1
            assert counts[day] <= 30
            equal(trade["commission"], 1.22)
            equal(trade["gross_pnl"], (trade["exit_price"] - trade["entry_price"]) * side * 2)
            equal(trade["net_pnl"], trade["gross_pnl"] - 1.22)
            daily[trading_day_date(exited)] += trade["net_pnl"]
            reason = trade["exit_reason"]
            exits[reason] += 1
            if reason in ("stop_loss", "stop_loss_same_bar_conservative"):
                equal(trade["exit_price"], trade["stop_loss"] - side * slip)
                bar = get(exited - timedelta(minutes=1))
                assert (float(bar.low_price) <= trade["stop_loss"] if side == 1
                        else float(bar.high_price) >= trade["stop_loss"])
            elif reason == "take_profit":
                equal(trade["exit_price"], trade["take_profit"] - side * slip)
            elif reason in ("stop_loss_gap", "scheduled_session_flatten", "proxy_mean_reversion"):
                equal(trade["exit_price"], float(get(exited).open_price) - side * slip)
                if reason == "scheduled_session_flatten":
                    due = deadline(entered)
                    assert exited == minutes[index(due)].candle_timestamp
                    delayed_deadlines += exited > due
                elif reason == "proxy_mean_reversion":
                    exit_signal = exited - timedelta(minutes=1)
                    values = [float(get(exit_signal - timedelta(minutes=29-i)).close_price) for i in range(30)]
                    midpoint = (sum(values[-10:]) / 10 + sum(values) / 30) / 2
                    assert values[-1] >= midpoint if side == 1 else values[-1] <= midpoint
            else:
                raise AssertionError(f"Unrecognized exit requires inspection: {reason}")
        net = sum(row["net_pnl"] for row in trades)
        equal(net, summary["metrics"]["net_pnl"])
        equal(net, sum(row["net_pnl"] for row in sessions))
        equal(summary["metrics"]["total_commission"], 1.22 * len(trades))
        cases[key] = {"trade_count": len(trades), "net_pnl": net,
            "ending_cash": manifest["starting_balance"] + net,
            "first_entry": trades[0]["entry_timestamp"] if trades else None,
            "last_exit": trades[-1]["exit_timestamp"] if trades else None,
            "nonpositive_cash_blocks": sum(int(match.group(1)) for note in summary["notes"]
                if (match := re.fullmatch(r"Blocked (\d+) replay signal\(s\) due to nonpositive cash\.", note))),
            "source_signal_fill_accounting_audit": "passed", "exit_reasons": dict(exits),
            "clock_exits_delayed_by_missing_observations": delayed_deadlines,
            "trade_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    assert len(cases) == 6, "Audit requires all six declared cases"
    result = {"status": "passed", "cases": cases,
              "audit_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "limitations": "Independent arithmetic and source-clock checks on executed trades; no queue/tick or original proprietary strategy validation."}
    store.clear()
    write_new_json(run_directory / "source-fill-audit-complete.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=BACKEND / "storage/databento")
    args = parser.parse_args()
    print(json.dumps(audit(args.run_directory, args.cache_dir), indent=2))


if __name__ == "__main__":
    main()
