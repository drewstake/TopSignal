> Historical result: the EMA/VWAP TopBot v5 incumbent has since been removed. TopBot Mathematical is the current preset; these diagnostic results do not validate it.

# MNQ probabilistic research results — 21 September 2026

**No candidate qualified for promotion in this historical diagnostic.** TopBot v5 has since been removed; current work uses protocol v3 and TopBot Mathematical. The
implementation adds two experimental models, an unconditional probability
baseline, offline validation and a read-only Dry Run explanation. No real run
was started, no provider order was placed, and no account/risk configuration was
changed. The Bayesian model named by the missing-model UI is a prototype, not a
statistically selected winner.

See the [research and mathematical specification](topbot-probabilistic-research.md),
[preregistered protocol](topbot-probabilistic-protocol-v1.json), and
[machine-readable diagnostic results](topbot-probabilistic-results-2026-09-21.json).
The research document links the original papers, CME specifications and official
ProjectX documentation. There is no claim that those papers establish MNQ alpha.

## Data and experiment

The read-only local snapshot supplied 3,128 five-minute MNQ rows, including one
partial row, on a single owner/contract/sim-data stream. Ten dates had full
09:30–15:45 ET coverage; two regular-session dates were incomplete. There were
12 discontinuities including scheduled closures. These are existing observations,
not restored Databento history. No cloud database was queried or written.

The protocol needs at least 200 complete sessions: three chronological
60/20/20-session train/calibration/validation folds advanced by 20 sessions,
followed by an untouched 60-session final holdout. The current coverage cannot
support that plan. The explicitly labeled **diagnostic** instead uses:

| Segment | Dates | Eligible paths |
| --- | --- | ---: |
| Training | August 25–September 1, six complete sessions | 26 nonoverlapping paths |
| Calibration | September 2–3 | 38 paths |
| Diagnostic evaluation | September 4 and 8 | 36 paths |

Eligibility requires causal 21-bar features, observed positive volume,
continuous paths, a 15-minute horizon and a volatility-sized stop no larger than
25 points. Across the input, 587 candidate windows exceeded the risk cap, 240
lacked a valid contiguous feature window, and three lacked a valid future path;
2,076 were outside entry hours. These counts include non-evaluation dates.
Unknown outcomes are excluded from scoring and disclosed, not counted as wins,
losses or neutral evidence. This small, coverage-selected diagnostic is unsuitable
for performance inference. In particular it is **not an untouched final test**.

## Comparison

Scores evaluate both BUY and SELL payoff distributions (72 forecasts from 36
decisions). Smaller Brier, log loss and CRPS are better; CRPS is in dollars per
contract. Mixtures were chosen using calibration dates only.

| Model | Pool mixture | Brier | Log loss | CRPS ($) | Research trades | Net P&L |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Unconditional empirical baseline | 0 | 0.240878 | 0.674815 | 21.0651 | 0 | $0.00 |
| Bayesian cells | 0 | 0.236065 | 0.664661 | 20.6643 | 0 | $0.00 |
| Kernel paths | 0.5 | 0.225569 | 0.642543 | 20.2330 | 0 | $0.00 |
| Existing TopBot v5, native replay | — | unavailable | unavailable | unavailable | 5 | $88.90 |

The probability models abstained because 26 paths and at most six training
sessions cannot meet the 300-path/20-effective-day evidence gate. **Zero P&L is
not zero-loss validation**: expectancy, win rate, tail estimates, concentration
statistics and confidence bounds are undefined when there are no trades. The
small scoring differences do not justify choosing the kernel model. There are
too few adequately populated reliability bins to assess calibration and too few
dates for 5- or 10-day block intervals. No model is labeled calibrated.

The v5 comparison calls the unchanged production evaluator and native replay
engine. Entry dates use the same complete-session coverage mask. Its bracket-only
positions may span more than 15 minutes; its native fill rules and forced
end-of-window exit also differ. It used $0.61/side and two ticks of adverse
execution allowance. Five trades and $88.90 net do not demonstrate profitability
or establish that v5 is superior. Its reported mark-to-market drawdown was
$164.72; candidate drawdown currently measures closed equity only, an explicit
additional promotion blocker. Paired comparisons include any incumbent exit
date inside the test window, even if no candidate could enter on that date.

Base candidate execution costs are $3.22 round trip; the forecast uncertainty
penalty includes another $1 cost buffer. Larger spread/slippage, an additional
five-minute entry delay and 0.75/1.25 volatility multipliers produced no trades
with evidence gates intact. The delay stress left 35 eligible decision paths;
the multiplier stresses left 78 and 17. Removing every twentieth candle or making
volume unknown left zero eligible forecasts. These exercise abstention and input
handling; **they do not establish economic robustness**, since no supported
candidate trades occurred.

## Implementation and operational boundary

- `probabilistic_strategy.py`: pure versioned interface; causal features, two
  conditional empirical distributions, pooled baseline, scenario repricing,
  day-cluster/HAC uncertainty and conservative expected-utility proposals.
- `probabilistic_validation.py`: chronology, label purging, training-only
  normalization, CRPS mixture calibration, untouched-tail exclusion, scoring,
  fixed-day block intervals, risk-limited replay, regime and stress diagnostics.
- `research_probabilistic_topbot.py`: read-only SQLite reader, single-owner scope,
  no external networking, source/protocol/code fingerprints, immutable experiment
  directories and the native v5 comparator.
- `probabilistic_shadow.py` and `BotProbabilisticPanel.tsx`: optional artifact
  loading for completed Dry Run explanations. Display horizon, probabilities,
  expected net payoff, costs, uncertainty, abstention reasons, model revision and
  freshness. A missing model shows unavailable values. No indicator score is
  relabeled as a probability.

The shared engine invokes the shadow only **after** completing a Dry Run routing
decision. It does not replace the SignalResult or feed order sizing, stops or
broker calls. Live evaluation does not call the research loader. Tenant, contract,
data subscription, feature cutoff, file size, numerical validity and freshness
are checked; an artifact cannot grant calibration or routing permission. Even a
synthetic BUY/SELL proposal remains NO TRADE for routing.

Models and scenario arrays are local experiment files under
`TOPSIGNAL_DATABENTO_CACHE_DIR/probabilistic-v1/experiments/`. Nothing automatically
installs an artifact in the active shadow lookup. The optional lookup is
`probabilistic-v1/models/<sha256(user_id)>/<sha256(contract_id|data_live)>.json`.
A reviewed artifact must be scoped to that owner/stream and trained through less
than seven days before evaluation, with all training labels strictly before the
decision. This diagnostic's models are stale and inadequate for current use;
they are intentionally **not installed**. API payloads contain bounded forecast
summaries, never training arrays. No migrations or retention changes are needed.

## Reproduction

From the repository root in PowerShell, with the existing local snapshot:

```powershell
$env:TOPSIGNAL_DATABENTO_CACHE_DIR = 'backend/storage/probabilistic-mnq'
backend/.venv/Scripts/python backend/tools/research_probabilistic_topbot.py --sqlite backend/storage/offline/topsignal.sqlite3 --mode diagnostic --summary docs/topbot-probabilistic-results-2026-09-21.json
```

`--mode audit` inspects coverage only. `--mode development` enforces the full
session minimum and excludes the reserved final tail before building features or
labels. There is intentionally no CLI final-test or promotion shortcut: unseen
source provenance and a frozen experiment ledger must first be verified. A
different contract is explicit via `--contract`; multiple owners require the
full `--owner-hash`, never implicit pooling. The tool never retrieves or imports
new data. Future Databento imports must use the existing cache builder and follow
the [storage policy](supabase-storage-policy.md).

Python 3.12.10 and NumPy 2.2.6 were used. Input SHA-256:
`b5170166741d2d48ca91753af6c39aef2318e3692affef00a09df3130a07dfc8`.
Exact code/protocol hashes and the scoped source fingerprint are in the JSON;
changing any implementation file produces a distinct local experiment directory.
Synthetic test fixtures are deterministic correctness checks, not market evidence.

Verification: the offline backend suite passed **1,969 tests**, with **9 skips**
(eight require disposable PostgreSQL; one optional workspace capture is not
installed). The offline guard reported zero external connection attempts and
disabled dotenv, workers and live gates. All **938 frontend tests** passed, as
did the production frontend build, lint on the affected frontend files and
`git diff --check`. Focused tests cover numerical payoff accounting, short/long
symmetry, ambiguous-bar ordering, gap losses, train-cutoff leakage, untouched-tail
exclusion, missing-data abstention, deterministic serialization, tenant isolation,
API serialization and the Dry Run/Live Run boundary. A high-win-rate synthetic
case with negative expected payoff correctly fails the BUY decision objective.
Repeated diagnostic runs produced byte-identical reports; development mode
stopped at insufficient coverage without evaluating a holdout.

The CLI verifies the registered protocol hash before reading market data.
Changing declared fees or thresholds without versioning the implementation
cannot silently produce a mislabeled experiment.

## Remaining evidence and limits

1. Obtain an authorized, sufficiently complete MNQ sample with at least 200
   eligible sessions, enough low-risk training paths, and a genuinely unseen
   60-session final tail. Do not restore retired archives without authorization.
2. Complete the registered walk-forward comparison and inspect calibration,
   block intervals, tails, regimes and concentration. No candidate is required
   to win. Change hypotheses only in a logged new experiment.
3. Verify costs and execution with synchronized quotes and fills; the existing
   partial viewer-driven observations cannot establish queueing, sub-bar latency,
   spread distributions or fill probabilities. Implement and verify candidate
   intratrade drawdown and broker time-exit parity before any promotion review.
4. After offline acceptance and a frozen final test, collect at least 20 forward
   Dry Run sessions. A later reviewed execution adapter would still be necessary;
   this task neither enables Live Run nor changes active runs.

Backtests can falsify assumptions and expose failures under stated scenarios.
They cannot prove stable future drift, cover unseen crashes, guarantee fills or
turn a nominal confidence interval into a profit guarantee.
