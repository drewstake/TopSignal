# Reddit option 3: daily IBS mean reversion

The daily ETF strategy made money in these historical simulations after modeled
fees and slippage. It earned less than the matching buy-and-hold benchmark, with
less exposure and smaller daily close drawdowns. These are retrospective results,
not proof of future profitability or a validated MNQ strategy.

Eight strategy backtests and eight matching benchmark backtests actually ran on
September 4, 2026 ET. No parameters were optimized. Each run started with fresh
$100,000 cash and no position. The later-period results are descriptive diagnostics,
not independent validation. This work made no production or live-trading changes.

## Full-period results

Base costs are **0.01% commission and 0.01% adverse slippage on each fill**, i.e.
one basis point of each. Stress uses **0.02% commission and 0.05% slippage per fill**.
Net results include cash dividends received while holding the ETF.

| ETF / scenario | Dates | Trades | Net profit | Total return | CAGR | Maximum daily close drawdown | Win rate | Profit factor |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| SPY / base | Mar 1, 2006–Sep 4, 2026 | 241 | $179,731.33 | 179.73% | 5.14% | 24.12% | 73.03% | 1.735 |
| SPY / stress | Same | 241 | $122,502.17 | 122.50% | 3.98% | 25.72% | 72.20% | 1.542 |
| QQQ / base | Jan 3, 2011–Sep 4, 2026 | 195 | $280,364.40 | 280.36% | 8.90% | 13.33% | 75.38% | 2.254 |
| QQQ / stress | Same | 195 | $216,058.91 | 216.06% | 7.62% | 14.10% | 73.33% | 2.034 |

Base SPY held positions approximately 17.06% of calendar time, averaging 5.30
calendar days per trade; its longest hold was 29 days. QQQ exposure was 17.70%,
averaging 5.19 days with a longest hold of 20 days. The strategy has no stop loss.
Daily close drawdown can understate the worst intraday loss.

| Matching buy-and-hold benchmark, base costs | Net profit | Total return | CAGR | Maximum daily close drawdown |
| --- | ---: | ---: | ---: | ---: |
| SPY, same full dates | $542,613.32 | 542.61% | 9.49% | 50.71% |
| QQQ, same full dates | $1,201,731.52 | 1,201.73% | 17.80% | 33.76% |

The benchmark uses the same initial cash, 95% initial allocation, whole-share
sizing, cost model, dividend accounting, and end-of-sample liquidation. It does
not rebalance or automatically reinvest dividends. Idle cash earns no interest
for either method. Strategies and benchmarks have different exposure by design.

## January 2, 2024–September 4, 2026 diagnostics

These accounts begin flat with fresh $100,000. Earlier bars warm up indicators
without carrying a position or an earlier entry signal into the period.

| ETF / scenario | Trades | Net profit | CAGR | Maximum daily close drawdown | Win rate | Profit factor |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| SPY / base | 25 | $17,297.27 | 6.15% | 10.74% | 80.00% | 2.550 |
| SPY / stress | 25 | $14,548.67 | 5.21% | 10.88% | 80.00% | 2.263 |
| QQQ / base | 31 | $16,921.95 | 6.03% | 11.60% | 70.97% | 2.046 |
| QQQ / stress | 31 | $13,541.66 | 4.87% | 11.82% | 64.52% | 1.799 |

Base buy-and-hold earned $62,850.08 on SPY and $73,939.19 on QQQ in this period,
with 20.02%/23.02% CAGR and 17.65%/21.56% drawdown respectively. The small
strategy trade counts limit how much confidence these later results can provide.

## Frozen rules and important differences from the Reddit claim

The entry uses a completed daily bar: close below the highest high of the last
10 bars minus 2.5 times the average high-minus-low range of the last 25 bars,
and `(close-low)/(high-low) < 0.3`. Both rolling windows include the current
completed bar. The exit is a completed close above the previous session's high.
These are the rules in the [original post](https://www.reddit.com/r/algotrading/comments/1rjvxjy/found_a_simple_mean_reversion_setup_with_70_win/).

All ordinary fills occur at the **next session's open**, with adverse slippage
and fees. They do not execute at the same close used to compute the signal.
Whole-share order size uses 95% of available cash and the **signal close** plus
modeled costs. There is one long position at a time, no pyramiding or leverage.
An order is cancelled if the next opening gap makes its entire planned size
unaffordable. This happened once in each full ETF strategy run, including stress,
and never in the later runs. This sizing policy is our explicit implementation
assumption; the author's sizing was not fully specified.

The author labels costs `0.01`, then describes percentage-based application in a
[later comment](https://www.reddit.com/r/algotrading/comments/1rjvxjy/comment/o8g9m3s/).
We interpret this as **0.01%=0.0001**, not a 0.01 decimal fraction or a dollar
amount per share. The decimal convention remains ambiguous. A later comment also
describes the exit using the previous close, conflicting with the main post's
previous high. We froze the main-post high rule before testing. Together with
next-open execution, sizing, source data and date differences, these ambiguities
mean this is a disclosed reconstruction, not an exact reproduction of the
author's statistics.

## Data and accounting

Both instruments were retrieved for free from Yahoo Finance's daily chart data:
[SPY history](https://finance.yahoo.com/quote/SPY/history/) and
[QQQ history](https://finance.yahoo.com/quote/QQQ/history/). Each raw response has
5,201 daily OHLC bars from January 3, 2006 through September 4, 2026. There were
82 SPY and 85 QQQ dividend events and no split events in this retrieved window.
Instrument identity, USD currency, exchange timezone, unique increasing dates,
finite positive prices and valid OHLC ordering were checked. The largest date gap
was five calendar days. A separate exchange-calendar completeness certification
or tick-level auction-price validation was not performed.

Signals and fills use Yahoo's quoted OHLC, not dividend-adjusted adjusted close.
Cash dividends are credited once to shares held at the previous close. Thus a
position entered on the ex-date gets no dividend; a position sold at the ex-date
open receives it. This avoids double-counting dividends through both adjusted
prices and cash flows. Yahoo describes the adjustment distinction in its
[adjusted-close documentation](https://help.yahoo.com/kb/SLN28256.html).

For simplicity dividend cash becomes available on the ex-date rather than the
actual payment date, and may fund later orders. Withholding, taxes, interest and
borrowing are not modeled. No split-adjustment machinery was needed; the loader
refuses a dataset containing a split event. A predetermined sample-end close
liquidation is supported and separately labeled; neither ETF strategy required
one in these runs. Buy-and-hold necessarily liquidates at sample end.

## Reproducibility and checks

The retained run is `backend/storage/research/reddit-ibs-20260904-run01/`. It
contains the pretest manifest, exact source and test snapshots, raw responses,
normalized daily bars, source URLs and hashes, full ledgers, daily equity curves,
cancelled orders, annual metrics, benchmark results and artifact hashes.

Raw SHA-256:

- SPY: `72f788e51dee4199c2513a4bea160aba874430b0d5b578de912ad935c5a372cb`
- QQQ: `fba53e4d814992a9f25a934cf3302c87a22f6d0c8fc02c10fe95e8dfd3fc99f7`

`backend/tests/test_research_reddit_ibs.py` passed **8 tests**, covering next-open
execution, historical-only entry calculation and sizing, dividend entitlement,
adverse percentage costs, gap cancellation, terminal policy, fresh account state
and benchmark consistency. The independent audit script in the retained run
imports no runner code and rechecks **992 trade records and 41,792 daily equity
rows** across all 16 cases. It verified every entry/exit signal and fill,
whole-share size, dividend credit, commission, cash/equity reconciliation, and
all 75 original artifact hashes. All checks passed.

To replay the frozen raw data into a new output directory:

```powershell
backend/.venv/Scripts/python.exe backend/tools/research_reddit_ibs.py --raw-dir backend/storage/research/reddit-ibs-20260904-run01 --output backend/storage/research/reddit-ibs-reproduction
```

Use of an already selected ETF and a public strategy adds selection bias. The
fixed later window also overlaps the original author's published testing period.
No interval here is described as independent validation, and these ETF returns
cannot be directly compared with one-contract MNQ dollar results without
accounting for different capital, exposure, leverage and holding periods.
