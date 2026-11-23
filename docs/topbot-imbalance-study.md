# TopBot imbalance study and profitability display

The practice account's prospective study was registered November 17, 2026 at
14:51:22 New York. It follows configuration 40 on peteamdpc. The limited
Windows task **TopSignal Imbalance Study** runs at priority 7 and starts at boot.
It has no broker mutation path. The application and independent recorder stay
at priority 4. Trading, recording enrollment, exit rules, the existing exit
experiment and its validation cohort are independent of this study.

## What the page measures

- **Bot profitability** uses confirmed practice entries and closed runner outcomes
  scoped to the account and configuration. The dashboard combines saved versions
  and exit plans under Orderflow Imbalance. The practice editor changes the size,
  account daily limits and bracket for subsequent entries on that configuration.
  Break-even is average absolute net loss divided by the sum of average net win
  and average absolute net loss. Both win rates omit flats. Closed drawdown orders
  trades by exit time and starts the equity curve at zero. Up to 5,000 recent
  attempts are read; truncation is labeled. Fill prices already incorporate slippage.
  Historical version labels show the earliest entry in the report for that version (New
  York time), along with the recorded direction, size, exit plan and daily limits
  when available. The version ID remains the filter key.
- **Exit-study inclusion** uses each registered exploratory entry's actual broker
  outcome and the latest checksummed research result. Pending entries remain in the
  denominator; the main exclusion counts distinct affected entries. The controls
  above the existing exit comparison also filter inclusion. Unresolved outcomes
  have their own row. The holdout is excluded.
- **Imbalance signal study** records each distinct first qualification, even if
  a position, risk check, missing decision or failed submission prevents a fill.
  Worker qualifications use the durable transition recorded by the actual worker.
  Orders are joined on the exact capture, episode, contract, side and scoped
  account. A filled bot position spanning qualification can explain a skip.
  Missing evidence leaves the reason unknown. An order acknowledgement is not a fill.
- The independent source follows the market-only local depth stream and applies
  the same observed-book rule with the saved threshold and persistence. It works
  with the bot disarmed and the browser closed. Its receipt clock and book can
  differ from the worker. Its counts and controls form a separate sample.

Each signal records local qualification time, contract, direction, rule, source,
capture, episode, bid and ask. At 30, 120 and 300 seconds the first usable quote
within two seconds marks the trade at the executable exit side. A long enters
at ask and marks at bid; a short enters at bid and marks at ask. Points include
the quoted spread and exclude fees. These are hypothetical market observations.
Recorded gaps censor unresolved horizons.

Every 30 seconds a below-threshold observation can become a control. The most
recent unused control in the same source, capture, contract, rule, local date
and ET hour, spread in ticks, and two-point band of preceding 60-second midpoint
movement is paired at qualification. Its five-minute window must have finished
before the signal. Matching never examines the control's outcome. Controls are
used once. Unmatched signals stay in the ledger and in the all-signal mean.
Paired differences include only pairs with both prices at the specified horizon.
The display shows the latest 40 episodes; the compact SQLite store retains the rest.

## Replay a recorded qualification

Open **Recorders** for the selected ProjectX account. Choose a New York capture
date, select worker signals, and enter stop and target distances in MNQ points
for brackets A and B. Both brackets share the original qualification time,
replay direction, quantity and entry price. The replay reads the retained worker
capture, including signals without a linked order. The date picker and previous
and next recorded-day controls include older entries beyond the study summary.

Each result uses the executable bid or ask, displayed size, a 250 ms fill model
and $1.22 round-trip costs per contract. Optional timed exits use the same
entry. The comparison total includes signals where both brackets resolved.
Unresolved exits show the coverage limitation. Each signal is an independent
hypothetical trade. Portfolio daily limits, overlapping positions and live bot
settings do not enter this comparison.

## Simulated while blocked

The Bot page follows each armed MNQ Orderflow Imbalance configuration prospectively.
After a daily profit or loss latch the host worker checks fresh episodes at its
normal cadence, using a transient decision and the same broker-read risk gates.
An actual final preflight blocked solely by remaining stop-risk budget also
supplies an entry candidate. Each receipt pins the direction, size, entry and
exit settings, session deadline and exact preflight book reference. Confirmed
instrument pauses, operator stops and unavailable depth prevent new entries.

A release-owned, below-normal-priority reader processes each account and
configuration in order, including with the dashboard closed. It uses verified
worker capture batches, the executable spread, displayed one- or two-contract
sweep, one-tick target trade-through, 250 ms latency, one adverse stop tick
and $1.22 round-trip fees per contract. Trailing stops use the runner's
five-second management model. A session flatten uses the saved runner deadline.
An interrupted recording censors the outcome and reserves the possible position
through its session deadline. A restart reads the original entry and subsequent
batches again. The same episode can enter only once.

The compact local ledger lives at
`TOPSIGNAL_DATABENTO_CACHE_DIR/blocked-trades-v1/sessions.sqlite3`.
It stores signal receipts and outcomes, with quotes remaining in worker evidence.
Allow roughly 2–4 KiB per candidate, or 2–4 MiB per thousand candidates.
New enrollments stop at the 512 MiB review threshold or 10 GiB free-space floor.
Win rate divides profitable simulated exits by all resolved simulated exits,
including flat exits. Open and unresolved cases have separate counts.

## Operations

The study reads local PostgreSQL using read-only transactions and tails verified
worker capture batches with a persisted cursor. Its initial seek includes
book warmup; usable prices require a full snapshot. Event rows, future marks
and the cursor commit together. New subscriptions start a distinct capture.
A process interruption can leave a gap in the independent observation stream.
Source availability and effective sample size are visible on the page.

The study store, status files and account report live in
`TOPSIGNAL_DATABENTO_CACHE_DIR/imbalance-study-v1/<owner SHA256>/<account>/`.
The helper stops new collection on its local 512 MiB review limit or 10 GiB
free-space floor; it never deletes observations. The optional worker reader
defers under recorder pressure. The configured release path supplies the
matching code and local database settings. Verify that binding when changing
releases or signal semantics. No cloud storage or historical bars are involved.

The November 19 Recorders release uses the helper in
`C:/ProgramData/TopSignal/tools/imbalance-study-20261119-v3`. The study task
uses the existing protected v1 config. Its v3 runner and the replay API share
the same capture-seeking logic. Include the active paths for `track_imbalances.py`,
`imbalance_study.py` and `imbalance_report.py` in host release companion checks.

The viewing PC serves the selected account at `http://127.0.0.1:4173/bot` using its
limited **TopSignal Study View** logon task. The existing host connection
forwards the original dashboard to local 4176 and the original API to local
8000. The host SSH allowlist is unchanged. The viewer verifies the bearer
session and owned config with the original API before retrieving the compact
report through the existing private file-transfer connection. The last successful
copy lives outside the public asset directory under the viewing user's protected
bundle. Only the viewer depends on the viewing PC. Other PCs and the host's
original frontend need their own UI rollout or a subsequent normal application
release. The regular application route reads the same owner/account report.

The viewing-PC connection definition before this rollout is preserved under
`tmp/imbalance-20261117/previous-viewing-connection.xml`. To revert the viewer,
stop only its dedicated viewing task, restore the connection's original local
4173 to remote 4173 mapping and restart only the viewing connection. Host
collection continues. No database migration or trading task restart is needed.

## Independent processing warning and worker attribution

Recorders shows the last same-contract raw market receipt and independent processed market
timestamp. Host recording diagnostics poll this status with the browser closed. Five minutes
of advancing raw receipts without processed market progress raises
`independent_study_stalled`. A new processed timestamp clears it. The MNQ session calendar,
stale raw receipts and the reconnect interval have separate statuses. Only processed market
events advance the watermark. Full release verification records study freshness alongside
recorder continuity.

On November 19 the independent receipt stopped at 19:06:24 ET with `OperationalError`.
The host task and worker reader continued. The error receipt omitted the SQLite subcode.
A second busy writer during boundary cleanup could terminate the independent thread after
its error was recorded. The helper now retries boundary writes, supervises the thread,
and retains the last committed market timestamp across reconnects.

The original v1 anchor side encodes the contrarian reference. Reports project the observed
heavy side separately and derive each worker's order side from saved rules. Reconciliation
uses a connection-local table and preserves original anchor dispositions and IDs for audit.
The independent sample retains its original contrarian reference horizons.
