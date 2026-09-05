# MNQ research protocol — September 4, 2026

The objective is credible net profitability for one TopBot Adaptive strategy.
This protocol was recorded before testing the new candidates. The baseline Git
revision is `592791b`; research changes live on `feature/mnq-credible-research`.
No live run, order, or paid data purchase is authorized by this protocol.

## Evidence and chronology

All locally available May 2019–July 10, 2026 data has already been examined.
Development (2019–2023) and later chronological diagnostics (2024–cache end)
are reused historical data, never independent validation. Each period and cost
scenario must start with fresh cash, positions, pending signals and risk state.
Full history is also replayed. Sparse launch coverage remains included and
disclosed; calendar and execution defects must be investigated before results
are used to select a candidate.

Additional data must have verified provenance, actual MNQ instrument mapping,
timestamps, rolls and coverage. A future final evaluation must be reserved before
viewing its outcomes, with a frozen candidate and code/data hashes. Once its
outcomes affect design it becomes development data. Aim for at least 200 trades
and six months across market conditions for independent confirmation; shorter
new samples can falsify a candidate but cannot meet that confirmation threshold.

## First hypothesis budget

Seven new fixed hypotheses plus the original v5 control are defined in
`backend/tools/fixtures/topbot_research.py`. Their rules, descriptions and specific
hypotheses are machine-readable and captured before each replay. They test:
long-only v5; long v5 with volatility-scaled brackets; first 30-minute opening
range breakout both ways; the same long only; confirmed VWAP reversion; strong
opening displacement; and late-session continuation. No grid optimizer is used.
All failures, execution errors and abandoned runs remain in the experiment log.
Any later hypotheses or parameter neighbors must be registered before testing,
and the running count of inspected alternatives must remain visible.

Use one MNQ, $50,000 starting balance, $1.20 commission per contract per side,
and one tick adverse slippage per fill, plus full replays at two and four ticks.
Costs may alter fills, positions, stop levels and subsequent opportunities, so
stress is rerun instead of simply subtracting a fee from the old ledger.
Retain the $250 daily realized-loss entry gate; this is not a guaranteed maximum
daily loss. The new bracket risk is capped at 100 points per contract ($200
before costs and gaps). No leverage escalation or averaging down is allowed.

Pre-run audit refinement: new intraday hypotheses flatten at the earlier of
15:55 Eastern or a known holiday close minus five minutes. The risk clock runs
on observed execution minutes independently of complete signal bars. If an
execution price is missing, it uses the next observed open; it never fabricates
an on-time fill or resets an old position's deadline at midnight. This policy
must be implemented in the live runtime before any candidate integration. The
original baseline and long-only baseline control retain their overnight holds.

The common research account policy reserves proposed whole-tick stop risk
against the $250 daily budget and allows entries again on the next trading day.
This is a newly specified daily entry gate, not a claim of full operational
parity with the current live worker: the worker currently disarms on some risk
blocks until an operator restarts it. The baseline control is compared under
the same research account policy. Integration requires matching the selected
policy and idempotent clock exits in the live runtime, while keeping explicit
operator stops and all provider/tenant authorization guards intact. Ordinary
OHLC market fills also approximate live scheduling/transport delay; any promising
candidate needs a delayed-entry replay as well as higher-cost stress.

## Existing-data shortlist requirements

These are necessary research gates, not a profitability claim:

- At least 500 trades overall, with positive development and chronological
  diagnostic net P&L at both baseline and two-tick costs.
- At least five profitable complete calendar years among 2020–2025, and later
  diagnostic profit factor at least 1.10.
- Positive lower endpoint of a 95% interval for mean marked-to-market session
  return under both 5-session and 20-session moving-block bootstrap estimates.
  Report sampling assumptions and recognize that searching alternatives reduces
  the evidential value of unadjusted intervals.
- Full-history net P&L stays positive after separately removing the best five
  trades and the best calendar year. Show top-day and top-trade concentration.
- Nearby fixed parameter values preserve positive full and diagnostic net P&L;
  report all neighbors, including failures. Four-tick costs are a severe stress
  diagnostic and must be disclosed.

A candidate that meets these gates is only retrospectively promising. The final
goal also requires credible untouched evidence, consistent live/backtest
behavior, software tests, complete report and ledger, and disclosed limitations.
Absence of independent data cannot be repaired by renaming a reused split.

## Reproducibility and audit

Each run writes a pre-test manifest, exact source snapshots, Git revision/diff,
parameters, data fingerprint, requested/actual periods, fills/risk assumptions,
then complete ledgers and results to a unique ignored storage directory. Reports
include net/gross P&L, expectancy, drawdown, exposure, trade counts, long/short,
yearly returns, concentration, uncertainty, gaps and risk-blocked opportunities.
Concise results and rejected hypotheses belong in a tracked experiment summary.
