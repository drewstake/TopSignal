# Reddit 15-minute opening-range breakout: six actual MNQ backtests

The fixed MNQ adaptation was profitable in every requested period/cost case.
It passes the existing protocol's measured numerical screen, but these reused
observations do not establish future profitability. No production setting or
live run changed. Four-tick execution stress, parameter stability, realistic
entry-delay/limit-queue sensitivities and independent future evidence remain
untested for this candidate.

The [predeclared rules](reddit-orb-protocol-2026-09-05.md) adapt
[the Reddit post](https://www.reddit.com/r/algotrading/comments/1j9pxsr/backtest_results_for_the_opening_range_breakout/)
from S&P 500 CFDs to one MNQ. A completed 15-minute candle must open at/below
the 09:30–09:45 ET range high and close above it; buy next boundary before noon.
Preserve the absolute range-low stop, take 1.5R from actual slipped entry and
allow another separate crossing while flat. Skip actual risk above 100 points,
reserve that risk under the $250 daily entry gate, and flatten at 15:55 ET or
holiday close minus five minutes. The latter risk and flatten policies are
declared MNQ adaptations. Missing exact entry minutes are skipped.

Every case used fresh $50,000 capital, one contract, $0.61 fees per side and
the listed adverse slippage on every fill. Full stored coverage was May 5,
2019 through July 10, 2026; the split was January 1, 2024 UTC. The diagnostic
period is already exposed data, not an untouched holdout.

| Period | Ticks/side | Trades | Net P&L | Profit factor | Win rate | Max marked drawdown | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Full | 1 | 616 | $6,440.48 | 1.1903 | 49.68% | $1,386.28 | 39.71 |
| Full | 2 | 611 | $6,395.08 | 1.1906 | 49.75% | $1,405.78 | 43.00 |
| Development | 1 | 460 | $3,993.30 | 1.1654 | 49.57% | $1,357.90 | 32.57 |
| Development | 2 | 456 | $4,065.18 | 1.1710 | 49.78% | $1,390.40 | 32.74 |
| Later diagnostic | 1 | 156 | $2,447.18 | 1.2523 | 50.00% | $1,386.28 | 17.64 |
| Later diagnostic | 2 | 155 | $2,329.90 | 1.2383 | 49.68% | $1,405.78 | 17.16 |

All six runs completed without replay failures: 182.82 seconds of summed
replay time. Full-history one-tick gross P&L after slippage was $7,192.00,
fees $751.52, expectancy $10.46/trade and exposure 4.87%. The two-tick run
is a complete rerun: gaps, the risk cap and targets can change trade selection,
so its results are not obtained by subtracting a constant cost.

| Year | One-tick net | Trades |
| --- | ---: | ---: |
| 2019 partial | $198.84 | 78 |
| 2020 | $1,264.18 | 106 |
| 2021 | $362.02 | 109 |
| 2022 | -$442.18 | 44 |
| 2023 | $2,610.44 | 123 |
| 2024 | $1,993.54 | 93 |
| 2025 | $449.90 | 55 |
| 2026 through July 10 | $3.74 | 8 |

Recent activity is sparse: the final trade was June 19, 2026, although the
replay continued through July 10. The fixed absolute-stop cap rejected 503
signals over full history at one tick; the daily budget rejected another eight.
There were 239 clock exits, 140 targets and 237 stops in that case. The cache
reports 42 configured-session execution gaps, including sparse launch data;
missing signal prefixes were not interpolated.

Removing the best five trades leaves $4,989.58 net; removing the best calendar
year leaves $3,830.04. The top 1% of trades supplied 5.02% of positive-trade
profit. The 2,000-repetition circular session-block bootstrap yielded full-history
95% mean-session-P&L intervals of [$0.061, $6.821] for five-session blocks and
[$0.443, $6.713] for 20-session blocks at one tick. At two ticks the corresponding
lower bounds were $0.038 and $0.427. These barely positive short-block bounds
are conditional on this reused sample and do not correct for strategy selection,
unseen regimes, source errors or execution-model error.

The dedicated runner uses observed one-minute fills, stop-first intraminute
ambiguity, quarter-point targets and exact absolute range-low stops. A separate
audit checked all 2,454 saved trade records across the six cases against the
source: complete session prefixes, 15-minute crossings, entry time and slipped
price, actual delivery, stop/target arithmetic, the daily risk gate, fees and
net/session reconciliation. All assertions passed. Maximum drawdown is measured
at observed minute closes, not continuously within each minute.

Eight synthetic tests passed, including preserving the stop across an entry
gap at both cost settings. The initial test run had one malformed synthetic
OHLC fixture (open above its high); correcting the synthetic high yielded
8/8 without changing strategy logic. The first actual-fill audit completed
its checks and wrote its report, then failed on a cleanup call to nonexistent
`store.close()`. It remains preserved. Replacing cleanup with `store.clear()`
and writing a new `source-fill-audit-v2.json` completed with exit code zero.
Neither correction changed any backtest outcome.

Saved base-engine `assumptions.bracket_rule` and `strategy_revision` retain
generic production labels. The captured `execution_adaptation`,
`candidate_definition`, `candidate_fixture_revision` and dedicated wrapper
source identify the actual ORB rules. The audit independently confirms the
absolute stops. Original manifests, ledgers and summaries were not rewritten.

Reproducibility: Git `47dc3e1105ba0b7ee8b632ce7cd97dcb46edb4ff`; captured
source digest `8f6614145f98899e446fe09f1b5d4fc3c8c9fcc5b09048365060e2a5d99a60f4`.
Verified cache/source fingerprint:
`e900ae486308de577f0945e21cd54821ed2b206c027761d1973563a9085b4d6a`.
The fixture revision is `reddit_orb15_mnq_absolute_stop_20260905_v1`. Complete
sources, manifest, per-case started records, replay/summary/session/trade JSON,
results and audits are in:

```text
backend/storage/research/experiments/20260905T033840.343850Z-reddit-orb15-fixed-3a3e01f0f4ec
```

Exact commands, from the repository root:

```powershell
& backend/.venv/Scripts/python.exe backend/tools/run_offline_tests.py tests/test_reddit_orb.py -q
& backend/.venv/Scripts/python.exe backend/tools/research_reddit_orb.py --fixture backend/tools/fixtures/reddit_orb.py --variants reddit_orb15_long --periods full development diagnostic --slippage-ticks 1 2 --commission-per-side 0.61 --execution-minutes 1 --protocol docs/reddit-orb-protocol-2026-09-05.md --label reddit-orb15-fixed
& backend/.venv/Scripts/python.exe backend/tools/audit_reddit_orb.py backend/storage/research/experiments/20260905T033840.343850Z-reddit-orb15-fixed-3a3e01f0f4ec --output backend/storage/research/experiments/20260905T033840.343850Z-reddit-orb15-fixed-3a3e01f0f4ec/source-fill-audit-v2.json
```

Replay invocations always create a new unique output directory. Audit output
files also refuse overwrite; use a new output filename when repeating the audit.
