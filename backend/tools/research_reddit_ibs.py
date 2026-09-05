"""Frozen Reddit daily IBS research; standalone, no broker or production writes.

Run from repository root with backend/.venv/Scripts/python.exe. The manifest and
source snapshot are written before fetching data or evaluating any strategy.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "https://www.reddit.com/r/algotrading/comments/1rjvxjy/found_a_simple_mean_reversion_setup_with_70_win/"
COST_SOURCE = "https://www.reddit.com/r/algotrading/comments/1rjvxjy/comment/o8g9m3s/"
INITIAL_CASH = 100_000.0
ALLOCATION = 0.95
NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    dividend: float = 0.0


@dataclass(frozen=True)
class Costs:
    name: str
    commission_rate: float
    slippage_rate: float


SCENARIOS = (
    Costs("base_1bp_fee_1bp_slippage", 0.0001, 0.0001),
    Costs("stress_2bp_fee_5bp_slippage", 0.0002, 0.0005),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, default=str, allow_nan=False) + "\n", encoding="utf-8")


def parse_yahoo(payload: dict, symbol: str) -> tuple[list[Bar], dict]:
    if payload["chart"].get("error"):
        raise ValueError(payload["chart"]["error"])
    result = payload["chart"]["result"][0]
    meta = result["meta"]
    if meta["symbol"] != symbol or meta["exchangeTimezoneName"] != "America/New_York":
        raise ValueError("Unexpected Yahoo symbol/timezone")
    events = result.get("events", {})
    if events.get("splits"):
        raise ValueError("Split event in requested history; explicit split accounting required")
    dividends = {
        datetime.fromtimestamp(int(v["date"]), timezone.utc).astimezone(NY).date(): float(v["amount"])
        for v in events.get("dividends", {}).values()
    }
    quote = result["indicators"]["quote"][0]
    bars = []
    for i, timestamp in enumerate(result["timestamp"]):
        day = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(NY).date()
        prices = [quote[k][i] for k in ("open", "high", "low", "close")]
        if any(v is None or not math.isfinite(v) or v <= 0 for v in prices):
            raise ValueError(f"Missing/nonpositive OHLC: {symbol} {day}")
        o, h, l, c = prices
        if not l <= min(o, c) <= max(o, c) <= h:
            raise ValueError(f"Invalid OHLC: {symbol} {day}")
        if bars and day <= bars[-1].day:
            raise ValueError("Dates not unique/increasing")
        bars.append(Bar(day, o, h, l, c, int(quote["volume"][i] or 0), dividends.get(day, 0)))
    missing_dividend_days = sorted(str(d) for d in dividends if d not in {b.day for b in bars})
    if missing_dividend_days:
        raise ValueError(f"Dividend dates missing from OHLC: {missing_dividend_days}")
    audit = {
        "symbol": symbol, "exchange": meta["exchangeName"], "timezone": meta["exchangeTimezoneName"],
        "currency": meta["currency"], "instrument_type": meta["instrumentType"],
        "bars": len(bars), "first": bars[0].day, "last": bars[-1].day,
        "dividend_events": len(dividends), "split_events": 0,
        "largest_observed_gap_days": max((b.day - a.day).days for a, b in zip(bars, bars[1:])),
        "gaps_over_four_days": [{"after": a.day, "before": b.day, "days": (b.day-a.day).days}
                                for a, b in zip(bars, bars[1:]) if (b.day-a.day).days > 4],
    }
    return bars, audit


def entry_signal(bars: list[Bar], i: int) -> bool:
    """Rolling windows include the completed signal bar and no future bars."""
    if i < 24:
        return False
    bar = bars[i]
    if bar.high == bar.low:
        return False
    high10 = max(b.high for b in bars[i-9:i+1])
    range25 = sum(b.high-b.low for b in bars[i-24:i+1]) / 25
    return bar.close < high10 - 2.5*range25 and (bar.close-bar.low)/(bar.high-bar.low) < 0.3


def planned_quantity(cash: float, close: float, costs: Costs) -> int:
    return max(0, math.floor(ALLOCATION*cash / (close*(1+costs.slippage_rate)*(1+costs.commission_rate))))


def backtest(bars: list[Bar], start: date, end: date, costs: Costs, buy_hold: bool = False) -> dict:
    """Causal next-open fills, prior-close sizing, fresh independent account state.

    'Independent' here refers only to mutable simulation state, not evidence.
    Cash dividends go to previous-close owners before any ex-date open fill.
    """
    indices = [i for i, b in enumerate(bars) if start <= b.day <= end]
    if not indices or indices[0] < 25:
        raise ValueError("Need evaluation bars and 25 prior warmup bars")
    first, last = indices[0], indices[-1]
    cash = INITIAL_CASH
    quantity = 0
    position = None
    pending = None
    trades, curve, cancellations = [], [], []
    total_dividends = total_fees = total_slippage = 0.0
    exposed_days = exposed_sessions = 0
    if buy_hold:
        # Buy/hold's opening order is sized using the preceding available close.
        pending = {"action": "buy", "signal": bars[first-1].day,
                   "quantity": planned_quantity(cash, bars[first-1].close, costs)}

    def sell(bar: Bar, reference: float, signal: date | None, reason: str) -> None:
        nonlocal cash, quantity, position, total_fees, total_slippage
        fill = reference*(1-costs.slippage_rate)
        fee = quantity*fill*costs.commission_rate
        slip = quantity*(reference-fill)
        cash += quantity*fill-fee
        total_fees += fee
        total_slippage += slip
        gross = quantity*(reference-position["entry_reference"])
        net = quantity*(fill-position["entry_fill"])-fee-position["entry_fee"]+position["dividends"]
        trades.append({
            **position, "exit_signal_date": signal, "exit_date": bar.day,
            "exit_reference": reference, "exit_fill": fill, "exit_fee": fee,
            "exit_slippage": slip, "exit_reason": reason, "gross_price_pnl": gross,
            "net_pnl": net, "holding_calendar_days": (bar.day-position["entry_date"]).days,
            "return_on_entry_notional_pct": 100*net/(quantity*position["entry_fill"]),
        })
        quantity, position = 0, None

    for i in indices:
        bar = bars[i]
        if quantity:
            days = (bar.day-bars[i-1].day).days
            exposed_days += days
            exposed_sessions += 1
            credit = quantity*bar.dividend
            cash += credit
            position["dividends"] += credit
            total_dividends += credit
        if pending:
            if pending["action"] == "sell":
                sell(bar, bar.open, pending["signal"], "next_open_exit_signal")
            else:
                qty = pending["quantity"]
                fill = bar.open*(1+costs.slippage_rate)
                fee = qty*fill*costs.commission_rate
                debit = qty*fill+fee
                if qty <= 0 or debit > cash:
                    cancellations.append({"signal_date": pending["signal"], "execution_date": bar.day,
                                          "planned_quantity": qty, "cash": cash, "debit": debit})
                else:
                    cash -= debit
                    quantity = qty
                    slip = qty*(fill-bar.open)
                    total_fees += fee
                    total_slippage += slip
                    position = {"entry_signal_date": pending["signal"], "entry_date": bar.day,
                                "entry_reference": bar.open, "entry_fill": fill, "quantity": qty,
                                "entry_fee": fee, "entry_slippage": slip, "dividends": 0.0}
            pending = None
        if i == last and quantity:
            # Predetermined sample-end liquidation, distinct from a close signal.
            sell(bar, bar.close, None, "sample_end_close_liquidation")
        elif not buy_hold:
            if quantity and bar.close > bars[i-1].high:
                pending = {"action": "sell", "signal": bar.day}
            elif not quantity and entry_signal(bars, i):
                pending = {"action": "buy", "signal": bar.day,
                           "quantity": planned_quantity(cash, bar.close, costs)}
        equity = cash+quantity*bar.close
        if cash < -1e-7:
            raise AssertionError("Negative cash / unintended leverage")
        curve.append({"date": bar.day, "cash": cash, "quantity": quantity, "close": bar.close,
                      "equity": equity, "pending": pending})
    peak = INITIAL_CASH
    max_dd = max_dd_pct = 0.0
    for row in curve:
        peak = max(peak, row["equity"])
        max_dd = max(max_dd, peak-row["equity"])
        max_dd_pct = max(max_dd_pct, 100*(peak-row["equity"])/peak)
    wins = [t["net_pnl"] for t in trades if t["net_pnl"] > 0]
    losses = [t["net_pnl"] for t in trades if t["net_pnl"] < 0]
    net = cash-INITIAL_CASH
    if abs(sum(t["net_pnl"] for t in trades)-net) > 1e-6:
        raise AssertionError("Ledger/account reconciliation failed")
    years = max((bars[last].day-bars[first].day).days/365.2425, 1/365.2425)
    yearly = []
    previous = INITIAL_CASH
    for year in sorted({r["date"].year for r in curve}):
        rows = [r for r in curve if r["date"].year == year]
        value = rows[-1]["equity"]
        yearly.append({"year": year, "net": value-previous, "return_pct": 100*(value/previous-1)})
        previous = value
    summary = {
        "requested_start": start, "requested_end": end,
        "actual_start": bars[first].day, "actual_end": bars[last].day, "sessions": len(indices),
        "strategy": "buy_and_hold" if buy_hold else "ibs_daily_reddit", "costs": asdict(costs),
        "initial_cash": INITIAL_CASH, "ending_equity": cash, "net_pnl": net,
        "total_return_pct": 100*net/INITIAL_CASH, "cagr_pct": 100*((cash/INITIAL_CASH)**(1/years)-1),
        "max_close_equity_drawdown_dollars": max_dd, "max_close_equity_drawdown_pct": max_dd_pct,
        "closed_trades": len(trades), "winning_trades": len(wins), "losing_trades": len(losses),
        "win_rate_pct": 100*len(wins)/len(trades) if trades else 0,
        "profit_factor": sum(wins)/(-sum(losses)) if losses else None,
        "expectancy": net/len(trades) if trades else 0,
        "gross_price_pnl": sum(t["gross_price_pnl"] for t in trades),
        "commission": total_fees, "slippage": total_slippage, "cash_dividends": total_dividends,
        "calendar_exposure_pct": 100*exposed_days/max((bars[last].day-bars[first].day).days, 1),
        "prior_close_exposed_sessions": exposed_sessions,
        "mean_holding_calendar_days": sum(t["holding_calendar_days"] for t in trades)/len(trades) if trades else 0,
        "longest_holding_calendar_days": max((t["holding_calendar_days"] for t in trades), default=0),
        "cancelled_unaffordable_entries": len(cancellations),
        "terminal_liquidations": sum(t["exit_reason"] == "sample_end_close_liquidation" for t in trades),
        "unexecuted_final_signal": pending, "yearly": yearly,
    }
    return {"summary": summary, "trades": trades, "equity": curve, "cancellations": cancellations}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory; never overwrite runs")
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 4))
    parser.add_argument("--raw-dir", type=Path, help="Replay retained SYMBOL-yahoo.json without network")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).read_bytes()
    (args.output/"research_reddit_ibs.py").write_bytes(source)
    git_rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    diff = subprocess.run(["git", "diff", "--", "backend/tools/research_reddit_ibs.py", "backend/tests/test_research_reddit_ibs.py"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    (args.output/"source.diff").write_text(diff, encoding="utf-8")
    tests_path = ROOT/"backend/tests/test_research_reddit_ibs.py"
    if tests_path.exists():
        (args.output/tests_path.name).write_bytes(tests_path.read_bytes())
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(), "git_revision": git_rev,
        "source_sha256": sha256(source), "source_post": SOURCE, "source_cost_comment": COST_SOURCE,
        "hypotheses": ["SPY_daily_IBS", "QQQ_daily_IBS"], "optimization": "none; fixed published parameters",
        "entry": "completed close < inclusive rolling10 maximum high - 2.5*inclusive rolling25 mean(high-low); IBS<0.3",
        "exit": "completed close > previous session high; no stop loss; next-session open execution",
        "source_ambiguity": "Main post exit says yesterday high; later author comment says yesterday close. Main post is frozen.",
        "cost_ambiguity": "Post says commission/slippage 0.01; author clarifies percentage multiplication but not decimal convention. Primary interprets 0.01 as 0.01%=one basis point, not 1% and not $0.01/share.",
        "scenarios": [asdict(c) for c in SCENARIOS], "initial_cash": INITIAL_CASH,
        "sizing": "95% available cash, whole shares calculated from signal close plus modeled costs; cancel if next-open price makes entire order unaffordable; no pyramiding/leverage",
        "periods": {"SPY": {"full": "2006-03-01", "later_diagnostic": "2024-01-01"},
                    "QQQ": {"full": "2011-01-01", "later_diagnostic": "2024-01-01"}},
        "end": args.end, "raw_start": "2006-01-01", "warmup": "25 or more prior bars, indicators only, no inherited position",
        "adjustments": "Yahoo quote OHLC used, not dividend-adjusted adjusted-close; reject any split event in requested history; credit cash dividends once to previous-close owners on ex-date",
        "dividend_limitation": "Cash available on ex-date rather than pay date; no withholding, interest or automatic dividend reinvestment; cash contributes to later strategy sizing",
        "benchmark": "95% initial cash whole-share buy/hold, prior-close size, identical fills/fees/dividend handling; no rebalancing",
        "terminal_policy": "Liquidate at predetermined final sample close with normal costs; label distinctly",
        "metrics": "Daily close marked-to-market drawdown including initial cash; net dividends and costs; intraday drawdown can be worse",
        "evidence_status": "Retrospective replication research; dates already selected by source author, not independent out-of-sample validation",
        "data_warning": "Free Yahoo daily data, not independently certified tick or executable auction data; no promise of reproducing Reddit statistics",
        "data_method_reference": "https://help.yahoo.com/kb/SLN28256.html",
    }
    write_json(args.output/"manifest.pretest.json", manifest)
    summaries = []
    for symbol, periods in manifest["periods"].items():
        query = urlencode({"period1": int(datetime(2006, 1, 1, tzinfo=timezone.utc).timestamp()),
                           "period2": int(datetime.combine(args.end+timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp()),
                           "interval": "1d", "events": "div,splits"})
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}"
        if args.raw_dir:
            raw = (args.raw_dir/f"{symbol}-yahoo.json").read_bytes()
        else:
            with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as response:
                raw = response.read()
        (args.output/f"{symbol}-yahoo.json").write_bytes(raw)
        bars, audit = parse_yahoo(json.loads(raw), symbol)
        audit.update({"source_url": url, "downloaded_utc": datetime.now(timezone.utc).isoformat(),
                      "raw_sha256": sha256(raw), "raw_reused_from": str(args.raw_dir) if args.raw_dir else None})
        write_json(args.output/f"{symbol}-data-audit.json", audit)
        write_json(args.output/f"{symbol}-normalized-bars.json", [asdict(b) for b in bars])
        for period, start in periods.items():
            for costs in SCENARIOS:
                for buy_hold in (False, True):
                    result = backtest(bars, date.fromisoformat(start), args.end, costs, buy_hold)
                    summary = result["summary"]
                    summary.update({"symbol": symbol, "period": period, "data_sha256": audit["raw_sha256"]})
                    prefix = f"{symbol}-{period}-{costs.name}-{'buyhold' if buy_hold else 'ibs'}"
                    for name, value in result.items():
                        write_json(args.output/f"{prefix}-{name}.json", value)
                    summaries.append(summary)
                    print(json.dumps({k:summary[k] for k in ("symbol", "period", "strategy", "costs", "closed_trades", "net_pnl", "cagr_pct", "max_close_equity_drawdown_pct", "win_rate_pct", "profit_factor")}))
    write_json(args.output/"results.json", summaries)
    hashes = {p.name: sha256(p.read_bytes()) for p in sorted(args.output.iterdir()) if p.is_file()}
    write_json(args.output/"artifact-hashes.json", hashes)


if __name__ == "__main__":
    main()
