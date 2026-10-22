# TopBot Mathematical — current execution contract

Updated September 26, 2026. TopBot uses the Bayesian payoff model, with the API
representing NO TRADE as HOLD. This selection is not evidence of profitability.
No qualified artifact is currently installed in the local workspace.

## Explicit unvalidated dry-run observation

`TOPSIGNAL_UNVALIDATED_DRY_RUN=true` enables a separate observation artifact path.
Both live-execution environment flags must explicitly be false: startup rejects
conflicting settings, the central live gate returns false, and mathematical live
start/evaluation rejects this mode independently. It cannot promote an artifact.
The ordinary validated loader and its seven-day limit remain unchanged.

`backend/tools/fit_observation_model.py` fits the existing Bayesian model using
240 complete local historical sessions (at most 4,096 eligible nonoverlapping
paths), with default pool mixture zero. This is an explicitly unvalidated training
policy, not the registered 60-session validated refit. Numerical integrity,
scope, current-code hashes, future-label embargo, fresh market inputs, risk and
signal support thresholds still apply. Old training is disclosed with its age;
forecasts are never labeled `offline_passed` or `passed`.

Artifacts go in `TOPSIGNAL_DATABENTO_CACHE_DIR/probabilistic-v3/unvalidated-models`.
Bounded pending state and default result logs live in `probabilistic-v3/observations`.
`TOPSIGNAL_OBSERVATION_LOG_PATH` optionally places the rotating JSONL log in the
runtime log directory (10 MiB plus 14 backups). Forecast records include BUY and
SELL distributions even for HOLD. After three subsequent closed five-minute bars,
outcome records contain hypothetical net payoffs using the registered cost and
stop-first OHLC assumptions; these are not broker fills or portfolio P&L. Missing
bars/rolls are recorded as unknown, not wins or zero returns. Deduplicate by event
and observation ID if a process crashes between logging and saving pending state.
No historical arrays or pending state are written to cloud tables.

Use the SSH log reader's `research` stream for these observations. Outside the
registered regular-session window the strategy still HOLDs; installing an
unvalidated model does not force BUY/SELL signals or extend trading hours.

## Forward dashboard and simulated account (October 22, 2026)

The worker records committed mathematical dry-run evaluations to a local SQLite
ledger under `TOPSIGNAL_DATABENTO_CACHE_DIR/forward-results`, scoped by owner,
account and bot. No input candle arrays or extra cloud tables are created.
The authenticated `/api/bots/{id}/forward-results?days=7` endpoint checks both bot
and account ownership. It returns at most 9,000 evaluations over 1–30 days.
The dashboard separates evaluation dispositions, forecast outcomes, and paper
account results. Pre-evaluation provider failures remain visible in Diagnostics.

Forecast outcomes retain the existing next-open 15-minute OHLC assumptions.
Calibration uses a nonoverlapping 15-minute lattice, with BUY and SELL reported
separately. Counts, pending/overdue/unknown outcomes, model-version count and
small reliability bins are explicit. These are descriptive, unvalidated results.

The separate paper account starts with zero cumulative net change, no invented
broker balance. It queues qualifying, risk-approved decisions for the first bar
opening strictly after recording, retaining the original 15-minute deadline.
It enforces one MNQ position, 300-second cooldown, 30 daily entries, and $250
daily stop-risk admission. Bars use conservative stop-first ordering, gap losses,
and registered fees/slippage. Pending orders reserve the position and entry slot.
Missing exposure paths block the simulation for review instead of booking zero.
It never creates or calls a broker client. Closed-equity drawdown is not an
intrabar drawdown measurement. State, evaluations and trade history commit
atomically; duplicates do not add positions or trades. The UI displays the last
100 trades and 30 daily totals; the local ledger retains the full trade history.

Collection begins at deployment; older observations are not backfilled into a
paper account. Local evidence needs an off-device backup. API reads do not run
the simulator; storage failures are logged and stale evaluation timestamps remain
visible. A process failure between the execution commit and local evidence write
can leave a coverage gap; evidence must not be treated as complete by assumption.

## Forward evaluation instrumentation (October 22, 2026)

The direct authenticated results page `/bot-results/{bot_id}` remains available
when a saved bot's broker account no longer appears in the active account picker.
It now shows candle-close-to-record delay, measured evaluation duration, the next
post-record five-minute entry opportunity, and remaining time to the original
exit deadline. Quotes and broker fills are not measured. Forecast outcomes retain
their original candle-open definition; separate delayed-entry hypothetical
outcomes use the same conservative OHLC simulator as the paper account.

Each recorded forecast exposes BUY/SELL expected net, uncertainty deduction,
lower utility, margin above the strict $1 threshold, training support, assumed
costs and separate risk/paper blocks. Recording this information does not change
model parameters, signal thresholds or actual routing behavior.

Local SQLite comparison accounts begin prospectively together for each artifact,
configuration hash and comparison-policy version: TopBot, flat, always-long,
always-short and the empirical historical pool. The pool uses identical frozen
training samples without feature conditioning. Directional rules intentionally
ignore signal-edge/support gates but retain the shared execution/risk blocks and
the simulator's costs, future-bar entry, original deadline, one-position rule,
cooldown and daily limits. Comparisons apply only to observed, forecast-eligible
opportunities. These independently simulated accounts never call a broker.
Existing paper history is preserved; no earlier baseline trades are invented.

Net account totals are since each comparison began. Statistical comparisons use
only complete sessions within the selected period, retaining zero-trade days.
The exchange calendar determines expected five-minute evaluations. Current or
incomplete sessions, missing/unknown outcomes and unavailable pool forecasts are
excluded; unknown exposure, finished-session coverage gaps or a truncated report
suppresses intervals entirely. Entire missing sessions remain visible. Returning
to an earlier model/configuration starts a new comparison period.
At least 20 complete sessions and nonconstant paired daily differences are needed
for a descriptive interval. Fixed-seed 20,000-replicate non-circular moving blocks
of five and ten sessions produce a conservative envelope, with a four-comparison
Bonferroni adjustment. This does not account for repeated peeking or authorize
validation/promotion. The UI defaults to 30 days so enough sessions can accumulate.
Model versions remain separate, and baseline exposure survives restarts. Small
state/trade records stay on the backend filesystem, never cloud candle tables.

## Population and risk

The registered population is MNQ five-minute closed bars, with decisions from
09:35 through 15:30 America/New_York and a 15-minute horizon ending by 15:45.
Overnight decisions HOLD. Scheduled closes also constrain entries: the horizon
plus a two-minute buffer must finish before the earlier of the exchange close
and 16:10 Eastern, the conservative Topstep flat deadline. Early closes use the
exchange calendar. Timed exits are anchored to the decision close, not delayed
submission time, and are clamped before that deadline.

Protocol v3 owns costs, population and risk constants. Limits remain one MNQ,
$250 daily loss, 30 entries per day and 300 seconds cooldown after every entry.
The stop is 4–25 points, target 1.5R. Stop-risk admission includes assumed
round-trip fees and two adverse exit ticks; actual gaps can exceed the budget.
High volatility produces `volatility_above_risk_cap`, with sigma and implied stop.

## Evidence gate

[Protocol v3](topbot-probabilistic-protocol-v3.json) supersedes v1/v2. The local
loader requires a passed development experiment, protocol and implementation
hashes, a reviewed frozen refit, correct owner/subscription/root/roll scope,
and training labels ending at least 15 minutes before the decision. Refits
use 60 complete sessions and expire after seven days. It does not recalibrate
or select parameters while making a decision.

Artifacts live under:

```
TOPSIGNAL_DATABENTO_CACHE_DIR/probabilistic-v3/models/
  SHA256(owner)/SHA256("MNQ|volume_previous_completed_session_v1|data_live").json
```

Delivery rolls share the root model, but an active run whose delivery changes
stops explicitly with `contract_rolled`. Review the current delivery and restart
through normal controls. An old delivery is never silently substituted in a run.

`offline_passed` permits forward Dry Run proposals only. `passed` additionally
requires immutable, reviewed evidence files for untouched post-freeze results,
at least 20 forward sessions, fills/exits/fees parity, intratrade drawdown and
the complete historical experiment ledger. Missing, changed or future evidence
blocks Practice Live routing. See the [research workflow](topbot-probabilistic-research.md).

## Execution and observations

Live start requires explicit user confirmation and an enabled worker. The API
start itself records a HOLD; only a worker holding a current database lease may
route an entry. Existing account, position, freshness and idempotency gates apply.

Each entry carries a durable timed-exit obligation. Worker cycles monitor the
position and a correctly sized opposite-side protective stop. A missing stop
triggers a critical event and immediate flatten attempt. Closing happens before
remaining orders are cancelled; an unconfirmed close retains the working stop.
A pending exit produces HOLDs without disabling the run. Manual test orders are
blocked while any timed exit remains outstanding on the account.

An unresolved entry escalates once after three reconciliation cycles. Inspect
its original custom tag, broker order and position; never resend it. Normal
reconciliation resolves a confirmed cancelled/expired/rejected entry. If the
provider cannot establish its disposition, retain the entry block and resolve
with provider support before an audited administrative repair. Do not clear an
obligation solely because one position snapshot is empty.

The analysis panel leads with the recorded forecast; indicator context is
collapsed. VWAP is context only and resets at 18:00 ET. Chart arrows represent
model decisions, hollow labels blocked decisions, and squares observed fills or
verified-flat observations. A verified-flat time is not an exact exit-fill time.
Duplicate decisions are hidden. Range, run and execution-mode filters prevent
mixing older signal histories into the selected view.

New configurations default to this strategy. Legacy configuration creation and
live execution require `TOPSIGNAL_ENABLE_LEGACY_STRATEGIES=true`; their historical
backtest engine remains for reproducibility. TopBot Mathematical requires the
chronological research pipeline, not a backtest of today's fitted artifact.

## Hypothetical outcomes for volatility rejections

In explicit unvalidated dry-run mode, policy `volatility-blocked-what-if-v1`
records independent BUY and SELL scenarios for new committed volatility HOLDs.
The collector rechecks the 21 complete, contiguous input candles, freshness and
volatility rejection. It freezes the implied stop above 25 points and its
tick-rounded 1.5-times target; the actual strategy still rejects that bracket.
No fitted-model probability is produced outside the admissible volatility range.

Entry is the first five-minute open strictly after recording, with the original
candle-close-plus-15-minute deadline. One MNQ, existing cost assumptions,
stop-first ambiguous OHLC ordering and adverse stop gaps apply. Outcomes mature
only from complete observed bars for the same contract and subscription.
Missing paths stay pending, become overdue after two minutes past the deadline,
and become unknown after two hours; contract rolls are unknown immediately.
Retries cannot duplicate scenarios or overwrite settled outcomes. Earlier HOLDs
are not retrospectively reconstructed.

These compact records live in the existing local SQLite forward ledger and are
available through the authenticated results endpoint and SSH results helper.
They have separate dashboard counts and per-side net outcomes. They do not enter
TopBot or baseline account balances, forecast calibration, or uncertainty tests.
Overlapping hypothetical paths are neither portfolio returns nor independent
samples. All execution gates, risk limits, model parameters and validation status
remain unchanged.

## Current evidence limits

The user authorized recovery of the original Databento source archives and a
local cache rebuild. Historical coverage now includes 1,622 complete sessions.
The registered 74-fold development run failed acceptance: neither candidate
produced an admissible trade. No tradable edge was established.
No order was placed, bot run started, model promoted or cloud migration applied
for this audit. Actual fee records, overlapping ProjectX parity observations,
forward evidence and a complete historical experiment inventory remain open.
Synthetic regressions cannot establish trading safety under actual broker faults
or a profitable edge. See the audit remediation ledger for measured results.
