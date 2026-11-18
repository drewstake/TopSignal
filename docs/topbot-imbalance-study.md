# TopBot imbalance study and profitability display

The practice account's prospective study was registered November 17, 2026 at
14:51:22 New York. It follows configuration 40 on peteamdpc. The limited
Windows task **TopSignal Imbalance Study** runs at priority 7 and starts at boot.
It has no broker mutation path. The application and independent recorder stay
at priority 4. Trading, recording enrollment, exit rules, the existing exit
experiment and its validation cohort are independent of this study.

## What the page measures

- **Bot profitability** uses confirmed practice entries and closed runner outcomes
  scoped to the account and configuration. The default filter is fixed exits across
  saved versions. The display can narrow to a version or include trailing exits.
  Break-even is average absolute net loss divided by the sum of average net win
  and average absolute net loss. Both win rates omit flats. Closed drawdown orders
  trades by exit time and starts the equity curve at zero. Up to 5,000 recent
  attempts are read; truncation is labeled. Fill prices already incorporate slippage.
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
