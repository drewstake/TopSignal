# TopBot Mathematical — current execution contract

Updated September 26, 2026. TopBot uses the Bayesian payoff model, with the API
representing NO TRADE as HOLD. This selection is not evidence of profitability.
No qualified artifact is currently installed in the local workspace.

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
