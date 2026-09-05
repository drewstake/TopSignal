# Reddit strategy backtests — September 4, 2026 ET

All 34 final simulations completed: 26 strategy cases and eight buy-and-hold benchmarks. The opening-range breakout was the strongest of the tested MNQ candidates. The daily IBS strategy was profitable on both ETFs but earned less than buy-and-hold. These results are historical research, not evidence of independently confirmed future profitability.

## Futures: same account, data and baseline costs

Each case uses one MNQ, $50,000 starting cash, $0.61 per contract per side, and one tick of adverse slippage per entry/exit in the table below. The full data window is May 2019–July 10, 2026. The execution stream is observed one-minute data, with completed-bar signals, actual delivery rolls and conservative stop-first treatment when both brackets are touched. Drawdown is marked at minute closes. An exhausted account stops taking new entries even while the replay continues over the remaining data.

| Candidate | Trades | Net profit/loss | Profit factor | Win rate | Maximum marked drawdown |
| --- | ---: | ---: | ---: | ---: | ---: |
| Opening range breakout (MNQ adaptation) | 616 | $6,440.48 | 1.190 | 49.68% | $1,386.28 (2.62%) |
| Hourly range reversal (MNQ adaptation) | 156 | -$1,966.82 | 0.802 | 40.38% | $3,304.74 (6.54%) |
| One-minute scalper (independent proxy) | 22,006 | -$50,020.82 | 0.791 | 48.91% | $50,063.89 (100.04%) |

Two-tick stress is a complete new simulation, not a fee deduction from the original ledger. The later diagnostic starts flat on January 1, 2024 with fresh cash; its data was already exposed in earlier research and is not an untouched holdout.

| Candidate | Full net, 2 ticks | Later net, 1 tick | Later net, 2 ticks |
| --- | ---: | ---: | ---: |
| Opening range breakout (MNQ adaptation) | $6,395.08 | $2,447.18 | $2,329.90 |
| Hourly range reversal (MNQ adaptation) | -$2,103.32 | $165.78 | $143.78 |
| One-minute scalper (independent proxy) | -$50,037.50 | -$24,809.60 | -$33,985.34 |

## Daily IBS on the original ETFs

Each ETF starts with $100,000. Whole-share orders use 95% of available cash, sized using the signal close and filled next session's open. Baseline commission and slippage are each 0.01% per fill; cash dividends are included. These positions and date ranges differ substantially from the fixed one-contract futures tests, so their dollar profits are not directly comparable.

| ETF | Period | Trades | Net profit | CAGR | Maximum daily-close drawdown | Profit factor |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| SPY | 2006-03-01–2026-09-04 | 241 | $179,731.33 | 5.14% | 24.12% | 1.735 |
| QQQ | 2011-01-03–2026-09-04 | 195 | $280,364.40 | 8.90% | 13.33% | 2.254 |

Both ETF strategies remained positive at 0.02% commission plus 0.05% slippage per fill and in the separate 2024–2026 diagnostic. Matching buy-and-hold earned 9.49% annualized on SPY and 17.80% on QQQ, versus 5.14%/8.90% for IBS, with greater drawdowns and exposure. The strategy has no stop loss. Its daily-close drawdowns do not capture intraday extremes.

## What was actually tested

1. **Opening range:** the [Reddit S&P CFD idea](https://www.reddit.com/r/algotrading/comments/1j9pxsr/backtest_results_for_the_opening_range_breakout/) adapted to MNQ. A completed 15-minute candle crosses above the 09:30–09:45 range, with next-open entry before noon, absolute range-low stop and 1.5R target. Added account policy rejects risk above 100 points and flattens before session close. Only eight trades occurred in 2026 through July, earning $3.74, so recent activity is sparse despite the positive aggregate.

2. **Hourly range:** the [Micro Russell post](https://www.reddit.com/r/algotrading/comments/1gchopm/range_breakout_strategy/) adapted to MNQ because no local Micro Russell history was available. A completed hourly candle crosses the preceding ten-bar range against EMA100; target is 1.5 range widths. EMA choice, crossing confirmation and account/flatten policy are explicit assumptions, not fully published source rules.

3. **IBS:** the [daily SPY/QQQ rules](https://www.reddit.com/r/algotrading/comments/1rjvxjy/found_a_simple_mean_reversion_setup_with_70_win/) were reconstructed on actual Yahoo ETF history. Next-open fills, percentage-cost interpretation, 95% prior-close sizing and ex-date dividend cash credit are disclosed assumptions. The original post and comments disagree on some details; this is not an exact reproduction of its reported statistics.

4. **Scalper:** the [author's five-second CFD algorithm](https://www.reddit.com/r/algotrading/comments/1rtepah/how_i_improved_results_on_a_scalping_algo_mean/) cannot be reproduced because its rules are withheld and the local cache has no five-second bars. The executed test is an independently specified one-minute MNQ mean-reversion proxy using two mean/ATR windows and a six-minute maximum holding clock, capped at one contract. Its result says nothing conclusive about the proprietary original. No five-second candles were fabricated.

All futures candidates use the $250 proposed-stop daily entry gate. This gate is not a guaranteed maximum daily loss. Missing exact pending-fill minutes cancel signals; clock exits use the next observed open. Original source rules were not tuned after viewing the results.

## Verification and retained evidence

All 31 dedicated synthetic and integration tests passed together. Independent source/ledger audits verify recorded signals, fills and arithmetic. Review identified a minute-open versus minute-close session gate that skipped exactly-at-start signals in hourly/scalper tests; private adapters were fixed, boundary regressions added, and every affected final case rerun. Earlier attempts are retained and excluded from these final tables. ORB was unaffected because its first signal is after the configured start. No production strategy or live-trading settings were changed.

The inherited engine may label strategy metadata with the production baseline; captured candidate definitions, fixture revisions, private execution adapters and source snapshots identify the actual research strategy. Source gaps, fill assumptions and repeated use of the data limit interpretation. This suite is complete for the requested screening; it does not include untouched future validation, a parameter search, or live promotion.

- [All 34 case summaries (CSV)](C:/Users/drews/Development/TopSignal/docs/reddit-strategy-backtests-2026-09-04.csv)
- [Machine-readable comparison (JSON)](C:/Users/drews/Development/TopSignal/docs/reddit-strategy-backtests-2026-09-04.json)
- [Opening-range details and source audit](C:/Users/drews/Development/TopSignal/docs/reddit-orb-results-2026-09-05.md)
- [Hourly full results, adaptations and execution audit](C:/Users/drews/Development/TopSignal/docs/reddit-hourly-range-results-2026-09-05.md)
- [IBS full results, benchmarks and audit](C:/Users/drews/Development/TopSignal/docs/reddit-ibs-backtest-2026-09-04.md)
- [Scalper results and limitations](C:/Users/drews/Development/TopSignal/docs/reddit-scalper-results-2026-09-05.md)

Final futures runs (each contains manifest, source snapshots, complete ledgers, summaries and diagnostics):

- [Opening range breakout (MNQ adaptation)](C:/Users/drews/Development/TopSignal/backend/storage/research/experiments/20260905T033840.343850Z-reddit-orb15-fixed-3a3e01f0f4ec/results.json)
- [Hourly range reversal (MNQ adaptation)](C:/Users/drews/Development/TopSignal/backend/storage/research/experiments/20260905T034455.216080Z-reddit-hourly-range-fixed-clock-a16661889de7/results.json)
- [One-minute scalper (independent proxy)](C:/Users/drews/Development/TopSignal/backend/storage/research/experiments/20260905T034429.204861Z-reddit-scalper-1m-proxy-fixed-clock-ddd23c219bad/results.json)
- [ETF suite](C:/Users/drews/Development/TopSignal/backend/storage/research/reddit-ibs-20260904-run01/results.json)

Regenerate this comparison with `backend/.venv/Scripts/python.exe backend/tools/summarize_reddit_suite.py`. Run commands for each backtest appear in its linked protocol/report. Retained raw data allow ETF reproduction without downloading changed history.
