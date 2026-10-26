# October 23 after-close comparison protocol

Prepared before the October 23 regular session. The review was subsequently
completed after the close; see the [dated findings](topbot-after-close-review-2026-10-23.md).
This protocol itself is not an outcome report or a validation claim.

One-time review: October 23, 2026 at or after **17:45 America/New_York**
(21:45 UTC), after the futures session closes and the final normal research
outcome's two-hour missing-data deadline has elapsed. The Codex heartbeat
`review-topbot-after-october-23-close` targets the current chat. Do not create a
second recurring monitor or mark the goal complete merely because it is scheduled.

## Frozen comparison

Use [the first-day report](topbot-first-day-review-2026-10-23.md) and its immutable
private evidence at `C:\Users\drews\.ssh\topbot-review-20261023\evidence`.
Verify the snapshot manifest before analysis.

Compare October 22 and October 23 **09:35–15:30 Eastern scheduled closes**,
including the session-boundary HOLD. Separately report whole-session health and
off-window/maintenance events. Distinguish repeated events from distinct candle
closes. The baseline window contains 72 distinct closes: 68 volatility HOLDs,
three net-edge HOLDs and one boundary HOLD.

Keep four categories separate: numerical model forecasts; actual simulated-account
trades and P&L; volatility-blocked alternative BUY/SELL scenarios; and forward
baseline accounts. The baseline has three forecasts, zero simulated trades,
62 wider-stop scenarios and no baseline cohort. The three early forecasts lack
collected delayed-entry outcomes. Do not backfill those gaps.

For wider-stop descriptive comparisons, report both each day's complete collection
and the common **10:20–15:25** clock window. A shared clock window does not make
the days' opportunities or volatility regimes identical. Never add the two
directional sums or select the favorable direction after observing its result.
Report pending, overdue, missing and unknown outcomes before interpreting means.
Use independently repriced OHLC paths where evidence permits and disclose the
absence of frozen original input revisions and measured fill costs.

Do not treat two sessions or correlated five-minute observations as sufficient
validation. Describe training age separately from market-data freshness. No model
refit, threshold change, feature candidate or risk change is part of this review.

## Collection and safety procedure

Discover the actual release from the scheduled task. Read the latest deployment
receipt and access guide; use strict-host-key SSH. Take a new consistent local
SQLite snapshot and bounded read-only database/log export with new filenames.
Preserve all prior evidence, uncommitted changes and model parameters. Historical
candles stay on the backend filesystem; no bulk cloud write or cleanup is needed.

The existing private `snapshot.py` takes an explicit release and a new output
directory. `compare_after_close.py` next to it reconstructs both sessions from
manifest-verified snapshots and refuses evidence captured before 21:45 UTC. Its
baseline reconstruction and early-execution rejection were checked before the
session. It is an analysis helper, not a replacement for independent repricing
or current runtime/broker checks.

Verify the intended bot, account, complete configuration, fitted parameters and
both disabled live gates against the baseline receipt. Preserve the 25-point
stop cap and every other risk limit. Verify current process/lease/heartbeat and
read-only stored order attempts and broker order/open-order/fill history. Do not
invoke order mutation endpoints, enable another bot or change account.

After 17:00, a healthy collector may still reference the 17:00 final close.
The existing checker's `market-check` freshness assumptions apply to an open
market and can falsely flag expected closure. Its `preflight` mode may supply
current runtime/order checks, but it does not prove completed-session coverage.
Independently inspect the complete October 23 ledger, final open-session bars,
pending outcomes and recent worker heartbeats. Do not suppress genuine gaps by
calling every stale record a session closure.

Save a dated comparison report and private evidence. Completion requires an
actual after-close snapshot, comparison of all four outcome categories and
verified unchanged safety invariants. Notify once with findings or a concrete
failure requiring attention.
