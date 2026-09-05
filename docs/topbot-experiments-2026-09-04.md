# TopBot research experiment record — September 4, 2026

Research branch: `feature/mnq-credible-research`, original commit `592791b`.
Protocol: [predeclared hypotheses and acceptance gates](topbot-research-protocol-2026-09-04.md).
All 2019–July 2026 data is reused historical evidence. No candidate has yet met
the independent-evidence requirement. No real orders or live runs were started.

## A00 — reproduce the original baseline

Hypothesis: the v5 evaluator and original replay should exactly reproduce the
saved checkpoint before changing execution assumptions or strategy rules.

Command from repository root:

```powershell
& backend/.venv/Scripts/python.exe backend/tools/benchmark_topbot_replay.py --days 3000 --holdout --output backend/storage/research/audit-baseline-20260904.json --trades-output backend/storage/research/audit-baseline-20260904-trades.json
```

Outcome: **reproduced**. All metrics and the complete 4,798-trade ledger exactly
match `checkpoint-baseline.json`. Ledger SHA-256:
`6c76cb6d116e58bb4ea01f8b9e698bcd163c1fd3a31dd3d3edc3761bac56f39a`.
Source fingerprint:
`cd56b8dbe08abc26b6bbbb9351e337984c603fe2562942ecb85ad0b9383a897d`.
This is the original engine's reproducible result, **not a corrected fill-model
claim**; the audit below identifies material limitations.

| Measure | Original v5 |
| --- | ---: |
| Net P&L | -$11,015.70 |
| Gross P&L after modeled slippage | $499.50 |
| Commissions | $11,515.20 |
| Trades | 4,798 |
| Expectancy per trade | -$2.2959 |
| Profit factor | 0.9551 |
| Maximum drawdown, observed bar-close marks | $16,039.30 |
| Exposure, fraction of execution bars | 23.07% |
| Long trades / net | 3,026 / -$1,383.90 |
| Short trades / net | 1,772 / -$9,631.80 |

The main baseline problem is insufficient gross edge to pay fees, especially
on shorts. Trades held less than one hour contributed -$19,001.20 across 3,298
trades; 1–6 hour holds contributed $3,290.80 and longer holds $4,694.70. These are
post-outcome descriptive groups, not usable entry filters or causal estimates.
Seven stop gaps lost $1,515.30 total. Seven roll exits contributed -$91.30 under
the original noncausal previous-close assumption.

| Entry year | Trades | Net P&L |
| --- | ---: | ---: |
| 2019, partial and sparse | 191 | $643.10 |
| 2020 | 626 | $1,849.10 |
| 2021 | 583 | -$4,409.70 |
| 2022 | 814 | $2,770.40 |
| 2023 | 639 | -$2,953.10 |
| 2024 | 657 | -$3,718.80 |
| 2025 | 793 | -$3,080.20 |
| 2026, partial | 495 | -$2,116.50 |

## A01 — audit corrections before candidate selection

The original engine executes resting brackets only on complete five-minute
signal bars. Real observed minutes inside an excluded aggregate can therefore
be skipped. The new optional execution stream processes every observed one-minute
bar while generating decisions only at an actual newly closed five-minute bar.
Missing next-minute executions discard the entry instead of delaying it across a
gap. Intrabar exits retain conservative stop-first ordering and use the bar close
as the upper bound of the unknown execution time for cooldown accounting.

The original contract-roll exit uses the previous delivery's final close after
the new session's roll is detected. Corrected research requires an actual
old-delivery open at the known roll time or fails explicitly. It cannot substitute
the new contract price or look backward to a favorable previous close.

The calendar incorrectly retained the equity-index 16:15–16:30 Eastern halt
after CME removed it on June 28, 2021. The corrected calendar preserves the halt
for earlier dates and requires a new cache version. A separate rebuilt cache
keeps the original baseline artifacts intact. See the data audit for provenance,
coverage and remaining historical-calendar limitations.

## A02 — minute execution, before daily-risk parity correction

Run `20260905T002140.238988Z-corrected-baseline-6cdcad805f0b` replayed the full
baseline in 72 seconds against corrected data with one-minute execution. It
produced 4,696 trades, -$9,514.40 net, $1,756 gross, $11,270.40 commissions,
0.9603 profit factor, -$2.0261 expectancy and $14,741.20 minute-close drawdown.
Complete ledger SHA-256:
`bfd742b4ae24602ef1c633699b57a53a6be9e728226f9adcb8026ed86724c392`.
Ledger, marked session returns and cash accounting reconcile. This remains an
intermediate diagnostic: the subsequent live audit found a risk-gate mismatch.

Three predeclared batches launched together with identical frozen code and
protocol, planning 72 cases across eight variants, three periods and three
slippage settings. They were deliberately stopped after the risk defect was
identified. Their `interruption.json` records explain the stop; all completed
results and incomplete `.started.json` cases remain preserved:

- `20260905T002426.194903Z-hypotheses-controls-5777400469ac`: one completed,
  one interrupted case.
- `20260905T002426.361369Z-hypotheses-opening-50e1a7cb8eb7`: one completed,
  one interrupted case.
- `20260905T002426.589342Z-hypotheses-alternatives-d9fc6f6e2f88`: one interrupted
  case, no completed cases.

These results are preliminary and are not eligible for selecting a strategy.
The baseline repeat is a reproducibility check, not another strategy hypothesis.
There has been no outcome-based change to the seven registered candidate rules.

## A03 — match the live daily-risk gate

Hypothesis: a chronological replay must reject any new position whose proposed
provider-held stop would consume the remaining daily-loss budget, because the
live risk gate already does so. The previous replay checked realized losses
alone and could accept trades the live bot rejects. Live also attributes full
round-trip trade net to the exit day when computing this gate; cash bookkeeping
can still charge each commission when incurred. The corrected mode must keep
these distinct accounting purposes explicit. A nonpositive account balance
must not finance further entries. No arbitrary margin number is invented.

The same candidate rules and test periods will be replayed after this parity
correction. Other identified integration work remains: the clock-based flatten
policy must act independently of signal-bar completeness in production, and
authoritative live positions must reach its strategy evaluator. No research
candidate is integrated or armed merely because its retrospective result is
positive.

Verification so far: the first broad backend run found three tests expecting
the obsolete 2026 halt; they were corrected to preserve historical coverage,
and a modern open-market regression was added. The complete rerun passed
1,510 tests with eight disposable-PostgreSQL skips and zero external network
attempts. Further tests cover the new risk correction before the next batch.
