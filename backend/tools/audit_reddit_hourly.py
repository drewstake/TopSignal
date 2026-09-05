"""Independent source-bar and arithmetic checks for completed hourly research."""
import argparse
import csv
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from tools.research_topbot import offline_environment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    offline_environment()
    from app.services import bot_backtesting as replay
    from app.services.databento_cache import DatabentoReplayStore
    from app.services.trading_day import TRADING_TZ
    manifest = json.loads((args.run / "manifest.json").read_text())
    cache = BACKEND / "storage/databento"
    saved_cache = json.loads((cache / "current.json").read_text())
    assert saved_cache == manifest["cache_manifest"], "Cache changed since simulation"
    store = DatabentoReplayStore(cache)
    first, last = store.history_bounds("MNQ")
    common = dict(user_id="offline-research", contract_id="CON.F.US.MNQ.U26",
                  root_symbol="MNQ", start=first, end=last, closed_by=last)
    hours = store.open_candles(unit="hour", unit_number=1, **common)
    minutes = store.open_candles(unit="minute", unit_number=1, **common)
    audits = []
    for ledger_path in sorted(args.run.glob("*.trades.json")):
        key = ledger_path.name.removesuffix(".trades.json")
        summary = json.loads((args.run / (key + ".summary.json")).read_text())
        ledger = json.loads(ledger_path.read_text())
        slip = summary["assumptions"]["slippage_ticks"] * .25
        prior_exit = None
        for trade in ledger:
            signal = datetime.fromisoformat(trade["signal_timestamp"])
            entered = datetime.fromisoformat(trade["entry_timestamp"])
            exited = datetime.fromisoformat(trade["exit_timestamp"])
            assert entered == signal + timedelta(hours=1)
            local = entered.astimezone(TRADING_TZ)
            assert 8 <= local.hour < 14 and local.minute == 0
            assert entered <= exited and (prior_exit is None or entered >= prior_exit)
            prior_exit = exited
            ix = replay._search_candle_start(hours, signal, side="left")
            assert hours[ix].candle_timestamp == signal
            history = hours[max(0, ix-199):ix+1]
            assert len(history) == 200
            assert all(row.source_instrument_id == trade["source_instrument_id"] for row in history)
            high = max(float(row.high_price) for row in history[-11:-1])
            low = min(float(row.low_price) for row in history[-11:-1])
            close = float(hours[ix].close_price)
            opened = float(hours[ix].open_price)
            values = [float(row.close_price) for row in history]
            ema = sum(values[:100])/100
            for value in values[100:]:
                ema += 2/101*(value-ema)
            direction = 1 if trade["side"] == "long" else -1
            if direction == 1:
                assert opened <= high < close < ema
                assert trade["stop_loss"] == low
            else:
                assert opened >= low > close > ema
                assert trade["stop_loss"] == high
            minute_ix = replay._search_candle_start(minutes, entered, side="left")
            minute = minutes[minute_ix]
            assert minute.candle_timestamp == entered
            assert minute.source_instrument_id == trade["source_instrument_id"]
            assert trade["entry_price"] == float(minute.open_price) + direction*slip
            risk = direction*(trade["entry_price"]-trade["stop_loss"])
            assert 0 < risk <= 100
            reward = math.ceil((high-low)*1.5/.25)*.25
            assert trade["take_profit"] == trade["entry_price"]+direction*reward
            assert trade["quantity"] == 1 and trade["commission"] == 1.22
            gross = direction*(trade["exit_price"]-trade["entry_price"])*2
            assert abs(gross-trade["gross_pnl"]) < 1e-8
            assert abs(gross-1.22-trade["net_pnl"]) < 1e-8
            reason = trade["exit_reason"]
            if reason in ("stop_loss", "stop_loss_same_bar_conservative", "take_profit"):
                boundary = trade["take_profit"] if reason == "take_profit" else trade["stop_loss"]
                assert trade["exit_price"] == boundary-direction*slip
                exit_ix = replay._search_candle_start(minutes, exited-timedelta(minutes=1), side="left")
                bar = minutes[exit_ix]
                assert bar.candle_timestamp == exited-timedelta(minutes=1)
                assert float(bar.low_price) <= boundary <= float(bar.high_price)
            elif reason in ("scheduled_session_flatten", "stop_loss_gap", "take_profit_gap"):
                exit_ix = replay._search_candle_start(minutes, exited, side="left")
                bar = minutes[exit_ix]
                assert bar.candle_timestamp == exited
                if reason != "take_profit_gap":
                    assert trade["exit_price"] == float(bar.open_price)-direction*slip
        metrics = summary["metrics"]
        assert len(ledger) == metrics["trade_count"]
        assert abs(sum(row["net_pnl"] for row in ledger)-metrics["net_pnl"]) < 1e-7
        assert abs(sum(row["commission"] for row in ledger)-metrics["total_commission"]) < 1e-7
        csv_path = args.run / (key + ".trades.csv")
        with csv_path.open("x", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=list(ledger[0]) if ledger else ["id"])
            writer.writeheader()
            writer.writerows(ledger)
        audits.append({"case": key, "status": "passed", "trades_checked": len(ledger), "net_pnl": metrics["net_pnl"]})
    store.clear()
    result = {"cases": audits, "checks": "native source hours, independent EMA/range, signal/entry clock, source minute fills, absolute brackets, risk cap, exit fills, fees, arithmetic, no overlap",
              "status": "passed", "completed_cases": len(audits), "expected_cases": manifest["experiment_count"]}
    assert len(audits) == manifest["experiment_count"], "Backtest matrix incomplete"
    with (args.run / "independent-audit.json").open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
