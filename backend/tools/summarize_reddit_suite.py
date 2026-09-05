"""Build the final Reddit comparison from completed, explicitly selected runs."""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENTS = ROOT / "backend/storage/research/experiments"
RUNS = {
    "Opening range breakout (MNQ adaptation)": ("20260905T033840.343850Z-reddit-orb15-fixed-3a3e01f0f4ec", "reddit_orb15_long"),
    "Hourly range reversal (MNQ adaptation)": ("20260905T034455.216080Z-reddit-hourly-range-fixed-clock-a16661889de7", "reddit_hourly_range_mnq"),
    "One-minute scalper (independent proxy)": ("20260905T034429.204861Z-reddit-scalper-1m-proxy-fixed-clock-ddd23c219bad", "reddit_scalper_1m_proxy"),
}
ETF_RUN = ROOT / "backend/storage/research/reddit-ibs-20260904-run01"
REPORT = ROOT / "docs/reddit-strategy-backtests-2026-09-04.md"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def money(value):
    return f"{'-' if value < 0 else ''}${abs(value):,.2f}"


def link(label, path):
    return f"[{label}]({path.as_posix()})"


def main():
    futures = []
    for label, (directory, variant) in RUNS.items():
        path = EXPERIMENTS / directory
        outcomes = read(path / "results.json")["results"]
        assert len(outcomes) == 6 and all(r["status"] == "completed" for r in outcomes.values())
        for period in ("full", "development", "diagnostic"):
            for slip in (1, 2):
                key = f"{variant}__{period}__slip-{slip}"
                summary = read(path / f"{key}.summary.json")
                assert summary["status"] == "completed"
                futures.append({"label": label, "period": period, "slippage_ticks": slip,
                    "metrics": summary["metrics"], "range": summary["range"],
                    "summary_path": str(path / f"{key}.summary.json"),
                    "trades_path": str(path / f"{key}.trades.json"),
                    "manifest_path": str(path / "manifest.json")})
    etfs = read(ETF_RUN / "results.json")
    assert len(etfs) == 16
    result = {"completed_simulations": 34, "strategy_cases": 26, "benchmark_cases": 8,
        "futures": futures, "etfs": etfs,
        "interpretation": "Retrospective research. Source adaptations/proxy are not exact replicas; no independent validation or live deployment."}
    REPORT.with_suffix(".json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    with REPORT.with_suffix(".csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(["strategy", "market", "period", "cost_scenario", "initial_cash", "net_pnl", "trades", "profit_factor", "win_rate_pct", "max_marked_drawdown_dollars", "max_marked_drawdown_pct", "cagr_pct"])
        for row in futures:
            m = row["metrics"]
            writer.writerow([row["label"], "MNQ", row["period"], f'{row["slippage_ticks"]} ticks/side; $0.61 fee/side', 50000,
                             m["net_pnl"], m["trade_count"], m["profit_factor"], m["win_rate"], m["max_drawdown_dollars"], m["max_drawdown_percent"], ""])
        for row in etfs:
            writer.writerow([row["strategy"], row["symbol"], row["period"], row["costs"]["name"], row["initial_cash"],
                row["net_pnl"], row["closed_trades"], row["profit_factor"], row["win_rate_pct"],
                row["max_close_equity_drawdown_dollars"], row["max_close_equity_drawdown_pct"], row["cagr_pct"]])
    lines = [
        "# Reddit strategy backtests — September 4, 2026 ET", "",
        "All 34 final simulations completed: 26 strategy cases and eight buy-and-hold benchmarks. "
        "The opening-range breakout was the strongest of the tested MNQ candidates. The daily IBS strategy "
        "was profitable on both ETFs but earned less than buy-and-hold. These results are historical research, "
        "not evidence of independently confirmed future profitability.", "",
        "## Futures: same account, data and baseline costs", "",
        "Each case uses one MNQ, $50,000 starting cash, $0.61 per contract per side, and one tick of adverse "
        "slippage per entry/exit in the table below. The full data window is May 2019–July 10, 2026. "
        "The execution stream is observed one-minute data, with completed-bar signals, actual delivery rolls "
        "and conservative stop-first treatment when both brackets are touched. Drawdown is marked at minute closes. "
        "An exhausted account stops taking new entries even while the replay continues over the remaining data.", "",
        "| Candidate | Trades | Net profit/loss | Profit factor | Win rate | Maximum marked drawdown |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in futures:
        if row["period"] == "full" and row["slippage_ticks"] == 1:
            m = row["metrics"]
            lines.append(f'| {row["label"]} | {m["trade_count"]:,} | {money(m["net_pnl"])} | {m["profit_factor"]:.3f} | {m["win_rate"]:.2f}% | {money(m["max_drawdown_dollars"])} ({m["max_drawdown_percent"]:.2f}%) |')
    lines += ["", "Two-tick stress is a complete new simulation, not a fee deduction from the original ledger. "
              "The later diagnostic starts flat on January 1, 2024 with fresh cash; its data was already exposed "
              "in earlier research and is not an untouched holdout.", "",
              "| Candidate | Full net, 2 ticks | Later net, 1 tick | Later net, 2 ticks |", "| --- | ---: | ---: | ---: |"]
    for label in RUNS:
        values = []
        for period, slip in (("full",2), ("diagnostic",1), ("diagnostic",2)):
            row = next(r for r in futures if r["label"] == label and r["period"] == period and r["slippage_ticks"] == slip)
            values.append(money(row["metrics"]["net_pnl"]))
        lines.append("| "+label+" | "+" | ".join(values)+" |")
    lines += ["", "## Daily IBS on the original ETFs", "",
        "Each ETF starts with $100,000. Whole-share orders use 95% of available cash, sized using the signal "
        "close and filled next session's open. Baseline commission and slippage are each 0.01% per fill; "
        "cash dividends are included. These positions and date ranges differ substantially from the fixed "
        "one-contract futures tests, so their dollar profits are not directly comparable.", "",
        "| ETF | Period | Trades | Net profit | CAGR | Maximum daily-close drawdown | Profit factor |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for row in etfs:
        if row["strategy"] == "ibs_daily_reddit" and row["period"] == "full" and row["costs"]["name"].startswith("base"):
            lines.append(f'| {row["symbol"]} | {row["actual_start"]}–{row["actual_end"]} | {row["closed_trades"]} | {money(row["net_pnl"])} | {row["cagr_pct"]:.2f}% | {row["max_close_equity_drawdown_pct"]:.2f}% | {row["profit_factor"]:.3f} |')
    lines += ["", "Both ETF strategies remained positive at 0.02% commission plus 0.05% slippage per fill "
        "and in the separate 2024–2026 diagnostic. Matching buy-and-hold earned 9.49% annualized on SPY and "
        "17.80% on QQQ, versus 5.14%/8.90% for IBS, with greater drawdowns and exposure. The strategy has "
        "no stop loss. Its daily-close drawdowns do not capture intraday extremes.", "",
        "## What was actually tested", "",
        "1. **Opening range:** the [Reddit S&P CFD idea](https://www.reddit.com/r/algotrading/comments/1j9pxsr/backtest_results_for_the_opening_range_breakout/) "
        "adapted to MNQ. A completed 15-minute candle crosses above the 09:30–09:45 range, with next-open "
        "entry before noon, absolute range-low stop and 1.5R target. Added account policy rejects risk above "
        "100 points and flattens before session close. Only eight trades occurred in 2026 through July, "
        "earning $3.74, so recent activity is sparse despite the positive aggregate.", "",
        "2. **Hourly range:** the [Micro Russell post](https://www.reddit.com/r/algotrading/comments/1gchopm/range_breakout_strategy/) "
        "adapted to MNQ because no local Micro Russell history was available. A completed hourly candle "
        "crosses the preceding ten-bar range against EMA100; target is 1.5 range widths. EMA choice, "
        "crossing confirmation and account/flatten policy are explicit assumptions, not fully published source rules.", "",
        "3. **IBS:** the [daily SPY/QQQ rules](https://www.reddit.com/r/algotrading/comments/1rjvxjy/found_a_simple_mean_reversion_setup_with_70_win/) "
        "were reconstructed on actual Yahoo ETF history. Next-open fills, percentage-cost interpretation, "
        "95% prior-close sizing and ex-date dividend cash credit are disclosed assumptions. The original "
        "post and comments disagree on some details; this is not an exact reproduction of its reported statistics.", "",
        "4. **Scalper:** the [author's five-second CFD algorithm](https://www.reddit.com/r/algotrading/comments/1rtepah/how_i_improved_results_on_a_scalping_algo_mean/) "
        "cannot be reproduced because its rules are withheld and the local cache has no five-second bars. "
        "The executed test is an independently specified one-minute MNQ mean-reversion proxy using two "
        "mean/ATR windows and a six-minute maximum holding clock, capped at one contract. Its result says "
        "nothing conclusive about the proprietary original. No five-second candles were fabricated.", "",
        "All futures candidates use the $250 proposed-stop daily entry gate. This gate is not a guaranteed "
        "maximum daily loss. Missing exact pending-fill minutes cancel signals; clock exits use the next "
        "observed open. Original source rules were not tuned after viewing the results.", "",
        "## Verification and retained evidence", "",
        "All 31 dedicated synthetic and integration tests passed together. Independent source/ledger audits "
        "verify recorded signals, fills and arithmetic. Review identified a minute-open versus minute-close "
        "session gate that skipped exactly-at-start signals in hourly/scalper tests; private adapters were "
        "fixed, boundary regressions added, and every affected final case rerun. Earlier attempts are retained "
        "and excluded from these final tables. ORB was unaffected because its first signal is after the "
        "configured start. No production strategy or live-trading settings were changed.", "",
        "The inherited engine may label strategy metadata with the production baseline; captured candidate "
        "definitions, fixture revisions, private execution adapters and source snapshots identify the actual "
        "research strategy. Source gaps, fill assumptions and repeated use of the data limit interpretation. "
        "This suite is complete for the requested screening; it does not include untouched future validation, "
        "a parameter search, or live promotion.", "",
        "- "+link("All 34 case summaries (CSV)", REPORT.with_suffix(".csv")),
        "- "+link("Machine-readable comparison (JSON)", REPORT.with_suffix(".json")),
        "- "+link("Opening-range details and source audit", ROOT / "docs/reddit-orb-results-2026-09-05.md"),
        "- "+link("Hourly full results, adaptations and execution audit", ROOT / "docs/reddit-hourly-range-results-2026-09-05.md"),
        "- "+link("IBS full results, benchmarks and audit", ROOT / "docs/reddit-ibs-backtest-2026-09-04.md"),
        "- "+link("Scalper results and limitations", ROOT / "docs/reddit-scalper-results-2026-09-05.md"), "",
        "Final futures runs (each contains manifest, source snapshots, complete ledgers, summaries and diagnostics):", "",
    ]
    for label, (directory, _) in RUNS.items():
        lines.append("- "+link(label, EXPERIMENTS / directory / "results.json"))
    lines += ["- "+link("ETF suite", ETF_RUN / "results.json"), "",
              "Regenerate this comparison with `backend/.venv/Scripts/python.exe backend/tools/summarize_reddit_suite.py`. "
              "Run commands for each backtest appear in its linked protocol/report. Retained raw data allow "
              "ETF reproduction without downloading changed history.", ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    main()
