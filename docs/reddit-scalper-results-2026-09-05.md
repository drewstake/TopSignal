# Reddit scalper: independent one-minute proxy results

The independent MNQ one-minute proxy loses money in every completed period and
cost scenario. The original private five-second algorithm remains untestable
from the public description; these results neither reproduce nor refute that
author's undisclosed strategy.

The [source post](https://www.reddit.com/r/algotrading/comments/1rtepah/how_i_improved_results_on_a_scalping_algo_mean/)
withholds its script and describes two mean-reversion parameter settings and
split entries. A later author comment says one setting's edge faded. The
[initial post](https://www.reddit.com/r/algotrading/comments/1r5al3o/finally_having_good_results_with_my_scalping_alog/)
clarifies execution is Nasdaq CFDs on Vantage, using five-second NQ chart bars,
with typical two-to-six-minute holds. These are the author's unverified claims.

The [predeclared protocol](reddit-scalper-protocol-2026-09-05.md) specifies the
independent approximation: confirmed reversals after two-ATR departures from
SMA10 or SMA30, one whole MNQ total, no scaling or fractional contracts, a 2-ATR20
stop clipped to 5–100 points, 1R target, mean-cross exits and six-minute/session
deadlines. Every parameter was fixed before examining these outcomes. The
original source's split-entry claim is not tested.

All six final cases completed, each with fresh $50,000 cash, positions, pending
signals and account-risk state. Fees are $0.61 per contract per side; slippage is
one or two .25-point ticks adversely applied to every entry and exit. MNQ's
point value is $2. The $250 daily entry-risk gate and 30-entry daily cap remain
fixed. Neither is a guaranteed maximum loss.

| Period | Slippage each fill | Trades | Net P&L | Profit factor | Max drawdown | Ending cash |
|---|---:|---:|---:|---:|---:|---:|
| Full: May 2019–July 10, 2026 | 1 tick | 22,006 | -$50,020.82 | 0.7911 | $50,063.89 | -$20.82 |
| Full: May 2019–July 10, 2026 | 2 ticks | 16,250 | -$50,037.50 | 0.7191 | $50,074.57 | -$37.50 |
| Development: May 2019–2023 | 1 tick | 22,006 | -$50,020.82 | 0.7911 | $50,063.89 | -$20.82 |
| Development: May 2019–2023 | 2 ticks | 16,250 | -$50,037.50 | 0.7191 | $50,074.57 | -$37.50 |
| Reused diagnostic: 2024–July 10, 2026 | 1 tick | 10,580 | -$24,809.60 | 0.8543 | $25,957.31 | $25,190.40 |
| Reused diagnostic: 2024–July 10, 2026 | 2 ticks | 10,447 | -$33,985.34 | 0.8038 | $34,881.58 | $16,014.66 |

The full replay traversed the source data through July 10, 2026, but the
simulated account ran out of cash before that date. At one tick the last exit
was September 28, 2023 at 11:52 ET; 32,526 later entry signals were blocked for
nonpositive cash. At two ticks the last exit was August 5, 2022 at 10:12 ET;
47,302 later entries were blocked. A final losing trade can push cash below zero;
there is no historical broker-margin model. Cash was never replenished during
a case. The development ledgers therefore match the corresponding full ledgers;
their nonpositive-cash block counts are 3,015 and 17,791. The diagnostic cases
start independently in 2024 and do not exhaust cash; their final exit is July
10, 2026 at 15:09 ET. Similar full net losses at the two costs reflect earlier
account exhaustion at higher cost, not cost insensitivity.

This uses verified local Databento GLBX.MDP3 actual MNQ delivery history,
format-6 cache with source fingerprint
`e900ae486308de577f0945e21cd54821ed2b206c027761d1973563a9085b4d6a`.
The full run contains 2,532,101 execution bars after framework warmup. There
are 37 gaps overlapping the configured 10:00–16:00 session, totaling 61 missing
minutes; 32 consecutive source minutes are required for a signal. No
five-second bars were invented. Signal eligibility uses minute-close time,
including the 09:59 candle's 10:00 decision. A pending signal fills only at the
immediate next nominal minute open if observed; otherwise it is canceled. The
independent risk clock instead exits at the first observed open at or after its
deadline. One risk-clock fill per full/development case was delayed by a missing
observation; the audited timestamps match the next observed source minute.
OHLC ambiguity is stop-first. Drawdown uses every observed minute close, not
intrabar equity extremes. Queue position, spread variation and subminute delay
are unavailable. These are material limitations for a scalping claim.

All history was already exposed in earlier research. The chronological split is
a reused diagnostic, not untouched validation. No parameter optimization,
production strategy change, broker order or live trading run was performed.

Eight behavioral/integration tests pass, including symmetric signals, no
averaging, missing data/delivery guards, mean-cross exits, holiday and outage
deadlines, the exact 10:00 boundary and cancellation of a missing next-minute
fill. The independent audit checked all 97,539 saved trade records across the
six cases against source signals, clocks, entry prices, brackets, commissions,
P&L, daily risk, position overlap, session equity and cash exhaustion. Records
shared by full/development cases are counted twice in that audit total.

The immutable final run is
[`20260905T034429.204861Z-reddit-scalper-1m-proxy-fixed-clock-ddd23c219bad`](../backend/storage/research/experiments/20260905T034429.204861Z-reddit-scalper-1m-proxy-fixed-clock-ddd23c219bad/manifest.json).
It contains the pre-run manifest and source snapshots, `results.json`, all six
complete trade/session/replay/summary files, and
[`source-fill-audit-complete.json`](../backend/storage/research/experiments/20260905T034429.204861Z-reddit-scalper-1m-proxy-fixed-clock-ddd23c219bad/source-fill-audit-complete.json).
The first audit passed all assertions but used the wrong cache cleanup method;
that CLI error and its already written output remain preserved, followed by a
complete rerun with corrected cleanup. It changed no performance calculation.

The earlier run `20260905T033949.465592Z-reddit-scalper-1m-proxy-17d62c6bf364`
is preserved with `aborted.json`: two full cases completed before review found
the session mask skipped the exact 10:00 decision. Remaining cases were stopped;
all six were rerun after the process-local execution fix. Those superseded
numbers are not used in the table above.

Exact final commands from the repository root:

```powershell
backend/.venv/Scripts/python.exe backend/tools/research_reddit_scalper.py --fixture backend/tools/fixtures/reddit_scalper.py --label reddit-scalper-1m-proxy-fixed-clock --variants reddit_scalper_1m_proxy --periods full development diagnostic --slippage-ticks 1 2 --commission-per-side 0.61 --execution-minutes 1 --bootstrap-repetitions 2000 --protocol docs/reddit-scalper-protocol-2026-09-05.md
backend/.venv/Scripts/python.exe backend/tools/audit_reddit_scalper.py backend/storage/research/experiments/20260905T034429.204861Z-reddit-scalper-1m-proxy-fixed-clock-ddd23c219bad
```

Behavioral checks, from `backend`:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_reddit_scalper.py -q
```

This approximation fails the existing retrospective research screen and provides
no basis for promoting it to live trading. Evaluating the original author claim
would require its exact rules and authentic five-second/tick data plus the
relevant CFD execution assumptions.
