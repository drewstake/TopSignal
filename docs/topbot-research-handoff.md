# TopBot strategy research handoff

## Fee correction — read before new research

The verified TopstepX MNQ rate is **$0.61 per contract per side ($1.22 round
trip)**. UI, API and TopBot CLI defaults have been corrected. Read
[the fee correction and fresh comparisons](topbot-fee-correction.md) first.
The original $1.20-per-side baselines below and `topbot-research-baseline.json`
are preserved historical higher-cost runs. Use explicit `--commission-per-side
1.20` to reproduce that cost scenario; use `0.61` for the current base case.
Earlier experiments retain their captured $1.20 fee. The fixed 72-case A06
rerun is complete: all matched cases passed source/control/accounting checks
at explicit $0.61 per side. Read
[the complete matched-fee audit](topbot-fee-comparison-audit-2026-09-04.md).
All 72 original A04 cases finished and remain preserved as higher-cost stress.
The separate [48-case legacy reconsideration](topbot-legacy-fee-audit-2026-09-04.md)
also completed, including unchanged older rejected filters at both fee rates.

The corrected application baseline loses $5,357.50 on 4,800 trades; the separate
observed-minute research baseline loses $3,449.28 on 4,224 trades. Both use one
tick of slippage. Neither establishes a profitable combined strategy. The
machine-readable corrected reference is `topbot-fee-corrected-baseline.json`.

Opening drive alone passes A06's measured historical gates: $11,413.04 full
net, 543 trades, $2,692.84 drawdown and $1,999.74 later-period net at one tick.
All 46 A07 parameter, entry-delay and target-fill cases passed their registered
robustness checks at $0.61; see
[the complete robustness results](topbot-opening-drive-robustness-results-2026-09-04.md).
The original center was then frozen and evaluated once on the entire reserved
ProjectX pool under the
[predeclared newer-data protocol](topbot-unseen-opening-drive-protocol-2026-09-04.md).
That [audited A08 evaluation](topbot-unseen-opening-drive-results-2026-09-04.md)
failed its preliminary screen: 12 trades lost $272.64,
$279.14 and $892.14 at 1/2/4 ticks respectively. Independent source-minute audit
verified all 36 trade records. This small sample does not establish the absence
of an edge, but it does not support promotion. Its 40 sessions also cannot meet
the original six-month/200-trade confirmation requirement.

**The entire July–September 2026 ProjectX pool is now exposed.** Neither a subset
nor a different strategy's result on it may be called an untouched holdout.
The previously declared four overnight hypotheses completed all 36 A09 cases
on the reused Databento history, with original rules, criteria and explicit
$0.61 fees. All four fail the unchanged numerical screens; see
[the complete overnight results](topbot-overnight-results-2026-09-04.md).
The 75-point long center's two-tick development/diagnostic profits are only
$117.42/$5.02; its later-period base-cost profit factor is 1.0086, both bootstrap
lower bounds are negative, and removing its best five trades leaves -$228.68.
The wider neighbor also fails later-period profitability. Independent audits
found no execution/accounting defect that would invalidate these failures.

**No tested candidate qualifies for promotion.** The corrected-fee comparisons
are complete, and the continued research has not established credible future
profitability. Further research must declare a new bounded hypothesis before
testing and obtain fresh reserved observations for independent confirmation;
neither exposed pool can be relabeled. Production strategy integration remains
unjustified, and no live run was started.

Prepared September 4, 2026. Start by reading this file, `topbot-strategy.md`,
`topbot-improvement-comparison.md`, and `topbot-replay-quality.md` in this directory.

## Active autonomous research update

The research branch `feature/mnq-credible-research` has completed the baseline
audit, corrected-fee comparisons and the declared hypothesis sets. Read these
newer records before relying on the original runtime instructions below:

- [Predeclared protocol](topbot-research-protocol-2026-09-04.md).
- [Complete experiment and interruption record](topbot-experiments-2026-09-04.md).
- [Data provenance and calendar audit](topbot-data-audit-2026-09-04.md).

The original baseline was reproduced exactly before changes. Subsequent audit
found incomplete aggregate execution, noncausal roll exits, an obsolete CME
halt, and disagreement with the live proposed-stop daily-risk gate. New research
uses a separate observed-minute execution stream and actual old-contract roll
prices. Its numerical risk model reserves stop risk; runtime lifecycle and
clock-exit integration remain separate requirements before any production
candidate can be adopted. Preliminary batches were stopped and preserved after
the risk defect was identified. They are not strategy-selection evidence.

The default `backend/storage/databento` now contains verified format 6, with
source fingerprint `e900ae486308de577f0945e21cd54821ed2b206c027761d1973563a9085b4d6a`.
All 60 arrays match the separate `backend/storage/databento-calendar-v6` cache,
which remains unchanged. The original format-4 cache is preserved at
`backend/storage/databento-format4-reference` and requires the original source
revision. The older cache paths/fingerprint below describe the pre-migration
baseline; use [the data audit's migration and reproduction instructions](topbot-data-audit-2026-09-04.md#verified-default-cache-migration)
for that historical run. Live/backtest integration remains outstanding.
Research tools are `backend/tools/research_topbot.py` and
`backend/tools/summarize_topbot_research.py`; they record immutable source/code
snapshots, hypotheses, complete ledgers, costs, periods, interruptions and
uncertainty. No strategy is currently certified profitable or enabled live.

## Objective and authority

Develop and test a credible profitable MNQ strategy, then integrate an improvement
into the single **TopBot Adaptive** strategy. The owner authorizes changing entries,
exits, indicators, timeframe, directional bias, sizing and risk rules. The existing
50/50-point bracket and bullish bias are the baseline, not requirements for new
research. Keep the product simple: MNQ, one strategy, Dry Run / Live Run controls,
and code-owned settings. Reusable indicator functions can remain in the library.

Work autonomously through implementation and offline experiments. This research
request does not authorize starting a live run, placing orders, changing operator
credentials or buying data/services. A successful backtest is not authorization to
arm the bot. Additional free data may be investigated, but verify provenance,
licensing, timestamps and instrument mapping before using it.

The current baseline is **not profitable after modeled costs**. Do not interpret
the request to keep testing as permission to search reused data until a chance
winner appears. Log failed ideas, preserve fixed evaluation criteria, and report
honestly if no candidate meets them. Continue useful research while resources and
credible hypotheses remain; if an external dependency prevents progress, record
exactly what is missing and leave reproducible commands and results.

## Reproduce before changing anything

Current strategy revision: `mnq_ema_vwap_pullback_v5_bracket_exits`.
Replay engine: `5.0.0-topbot-bracket-exits`.

`topbot-research-baseline.json` stores the verified metrics, effective settings,
assumptions, coverage, data fingerprint, ledger hash and normalized source hashes
for machine-readable comparisons. The checkpoint replay reproduced the previous
v5 metrics and complete trade ledger exactly.

Full cached history, $50,000 initial balance, one contract, $1.20 commission per
contract per side, and one tick of modeled slippage:

| Metric | V5 baseline |
| --- | ---: |
| Closed trades | 4,798 |
| Long / short trades | 3,026 / 1,772 |
| Net P&L | -$11,015.70 |
| Gross P&L after slippage, before commissions | $499.50 |
| Commissions | $11,515.20 |
| Profit factor | 0.955132 |
| Win rate | 50.3543% |
| Expectancy per trade | -$2.29589 |
| Maximum drawdown | $16,039.30 |
| Long / short net P&L | -$1,383.90 / -$9,631.80 |
| Fresh final-20% diagnostic net P&L | -$4,279.20 |

The previous v4 baseline lost $16,658.60 on 5,334 trades. Three fixed alternatives
were evaluated: bracket-only exits, extra long trend alignment, and an entry
distance filter. Only bracket-only exits were adopted, as an improvement to the
losing baseline. The entry filter's small selection-period profit did not survive
the later-period quality check. Read the comparison document before retrying
these ideas or interpreting their results as new evidence.

**All available 2019–July 2026 history has already been examined.** The existing
2020–2023 selection split, 2024–2026 diagnostic split and final-20% report are
retrospective. They are not untouched holdouts. New walk-forward studies on these
dates may be informative, but cannot erase this prior exposure. Reserve genuinely
unseen data or subsequent forward observations for confirmation if available.

## Local runtime and data

The existing checkout is `C:\Users\drews\Development\TopSignal`.
Its Python runtime is `backend\.venv\Scripts\python.exe`; frontend dependencies
are installed in `frontend\node_modules`. A separate worktree does not inherit
these ignored directories. Use the existing Python interpreter with the new
worktree's script path, or create a Python 3.11 environment and install
`backend/requirements.txt`. Use `npm ci` and `npm --prefix frontend ci` for a fresh
JavaScript checkout. The committed CI workflow specifies supported runtimes.

The ready historical cache is at:

```text
C:\Users\drews\Development\TopSignal\backend\storage\databento
```

It contains both OHLCV-1m and instrument Definitions, Parquet data and materialized
mmap timeframes. MNQ source bounds are May 5, 2019 22:03 UTC through July 10, 2026
20:20 UTC. Complete 5-minute bars start later; the full v5 replay evaluates 504,185
bars after warmup. Do not confuse source bounds, complete-bar bounds and the first
eligible signal time.

Expected source fingerprint:

```text
cd56b8dbe08abc26b6bbbb9351e337984c603fe2562942ecb85ad0b9383a897d
```

The archive copies are under that cache's `sources` directory:

- OHLCV: `GLBX-20260904-PSANB6M5GT/GLBX-20260904-PSANB6M5GT.zip`
- Definitions: `GLBX-20260904-G7KQLJS9K9/GLBX-20260904-G7KQLJS9K9.zip`

Archives, cache, reports, virtual environments and credentials are intentionally
outside Git. A cloud agent or another machine will not have these files merely
by cloning. Run locally with access to this cache, or arrange authorized data
access first. Do not substitute ProjectX candles for historical replay. The
ProjectX signal chart has a separate data path. Offline research does not require
the continuous worker, a running web app or broker credentials.

From the research checkout's root in PowerShell:

```powershell
$researchPython = 'C:\Users\drews\Development\TopSignal\backend\.venv\Scripts\python.exe'
$researchCache = 'C:\Users\drews\Development\TopSignal\backend\storage\databento'
git status --short --branch
git rev-parse HEAD
& $researchPython backend/tools/benchmark_topbot_replay.py --cache-dir $researchCache --days 3000 --holdout --commission-per-side 0.61 --output backend/storage/research/baseline-fee061.json --trades-output backend/storage/research/baseline-fee061-trades.json
```

`--days 3000` covers this cache's full history, ending at the stored last bar. The
benchmark runs the current code-owned preset at $0.61 per side. Use the corrected
reference at the top of this handoff; the original $1.20 checkpoint is historical. It
does not optimize. Expect roughly 25–40 seconds on the existing machine when
idle; runtime varies. Check metrics and source fingerprint against this document
before treating a mismatch as a strategy change.

The old evaluator is preserved in `backend/tools/fixtures/topbot_v4.py` for these
fixed historical comparisons:

```powershell
& $researchPython backend/tools/compare_topbot_variants.py --cache-dir $researchCache --period selection --commission-per-side 1.20 --output backend/storage/research/selection-fee120-stress.json
& $researchPython backend/tools/compare_topbot_variants.py --cache-dir $researchCache --period diagnostic --variants baseline bracket_only --slippage 2 --commission-per-side 1.20 --output backend/storage/research/cost-stress-fee120.json
```

The comparison tool still uses the current shared indicators, replay engine and
account limits. Freeze the Git commit and compare configuration snapshots as
well as the evaluator source when reproducing old results. Do not change shared
code mid-comparison. Reports contain configuration, execution assumptions,
coverage diagnostics, source fingerprints and trade-ledger hashes. Preserve all
experiment records; choose a new output name for each run.

## Files to work in

- `backend/app/services/topbot_strategy.py`: the single strategy evaluator and rules.
- `backend/app/services/topbot.py`: code-owned timeframe, size, daily loss entry
  gate, session and other operational defaults.
- `backend/app/services/bot_service.py`: shared indicators, live evaluator bridge,
  authoritative position checks and attached broker brackets.
- `backend/app/services/bot_backtesting.py`: chronological fills, costs, portfolio
  accounting, rollover, coverage reporting and replay request integration.
- `backend/app/services/databento_cache.py`: historical cache and stream loading.
- `backend/tools/build_databento_cache.py`: explicit offline rebuild/import when
  necessary; run `--help` and read the README before rebuilding a shared cache.
- `frontend/src/pages/bot/BacktestTradeAnalysis.tsx` and `backtestAnalytics.ts`:
  long/short, duration, excursion, time buckets and complete ledger exports.
- `backend/tests/test_topbot_strategy.py`, `test_topbot.py`,
  `test_bot_backtesting.py`, `test_bot_execution_safety.py`,
  `test_bot_replay_arrays.py`: existing behavioral and integration coverage.

Keep historical and live strategy decisions on the same implementation. Update
the revision and replay cache version when behavior changes. Preserve server
authentication, tenant isolation, quantity checks, audit records and live arming
gates. The entry cutoff currently does not flatten an overnight position; model
and document any new exit/session policy consistently across replay and execution.

## Research protocol

1. Reproduce the baseline and inspect losing versus winning trades, long versus
   short results, duration, adverse/favorable excursion, session, year and exits.
   Include coverage exclusions and risk-blocked signals in the diagnosis.
2. Audit timestamps, contract expiration/roll selection, warmup, tick arithmetic,
   commissions and fill logic. Use only information available at decision time,
   next-bar execution, conservative ambiguous-bar handling and real gaps. Never
   interpolate executable candles just to remove warnings. The calendar is an
   approximation: 1,570 reported gaps include 1,491 starting in 2019 and only 24
   overlapping configured entry hours; historical closures still need review.
3. Before each experiment, record its rationale, exact rules, parameters, search
   budget, evaluation periods, costs, risk limits and acceptance criteria. Track
   the total number of variants tried, including rejected ones. Keep experiments
   in a separate script/module until there is a justified integration decision.
4. Prefer the simplest candidate supported across chronological periods, nearby
   parameters and higher-cost stress. Compare net expectancy, profit factor,
   drawdown, exposure, sufficient trade counts, long/short behavior, holding time
   and concentration of returns. Use block/session-aware uncertainty estimates;
   a small positive total or higher win rate alone is insufficient.
5. Use a fixed-position comparison to distinguish a better signal from leverage
   or reduced activity. Do not hide losses, move test boundaries after seeing
   outcomes, remove safeguards to improve a score, or use unbounded averaging
   down. Any new risk policy needs explicit replay assumptions and tests.
6. Record Git commit/diff, data fingerprint, complete effective settings, costs,
   periods, trade ledger, metrics and timing for each run. Include operational
   differences between historical fills and executable orders. Keep generated
   private/large artifacts in ignored storage, and commit concise experiment
   summaries plus reproducible source code.
7. Integrate only a justified improvement, test it, and document whether it is
   still losing, retrospectively profitable, or confirmed on new data. Leave the
   bot stopped. If none qualifies, retain the baseline and report the evidence.

## Verification commands

Run these from the checkout root. On this machine, limit Vitest worker concurrency
when other CPU-heavy checks are running to avoid starving its short UI waits.

```powershell
& $researchPython backend/tools/run_offline_tests.py
npm --prefix frontend test -- --maxWorkers=4
npm --prefix frontend run lint -- --max-warnings=0
npm --prefix frontend run build
npm run test:dev-scripts
npm audit
node scripts/audit-frontend.cjs
git diff --check
```

The offline runner disables dotenv, live gates and external network connections.
PostgreSQL integration tests require a disposable database and are skipped by
that runner; the committed CI database job provisions one. Do not point tests at
the operator's production database. Baseline tests passing establish software
behavior, not future profitability.

Checkpoint verification: 1,474 backend tests passed and 8 PostgreSQL tests were
skipped locally; all 840 frontend tests passed with four workers. Frontend lint,
production build, 16 deployment-script tests and both dependency audits passed.
The initial unrestricted parallel frontend run hit three dashboard test failures;
the complete bounded-worker rerun passed. No trading run was started.

The subsequent fee/research verification passed 1,672 offline backend tests,
with the same eight database-dependent skips and zero external connection
attempts. Thirty additional synthetic tests passed for the frozen ProjectX
adapter. Independent audits also verified the actual A06/A07/A08/A09 and legacy
ledgers; those evidence checks are separate from software-test counts.

Deliver the chosen rules and rationale, the complete experiment summary including
failures, realistic-cost results and uncertainty, known data/execution limitations,
reproduction commands, and test outcomes. Do not claim success if the evidence
does not support it.
