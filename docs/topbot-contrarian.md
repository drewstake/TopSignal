# TopBot contrarian practice strategy

Revision `dom_contrarian_v1`. User selected 10% imbalance sustained for 30 seconds.
Sum the nearest 100 observed bid levels and nearest 100 observed ask levels.
Imbalance = (bid quantity − ask quantity) / (bid quantity + ask quantity).
At +10% or greater, short; at −10% or less, buy. No price confirmation. This is
an experimental rule, not an established profitable signal.

The worker maintains its own ProjectX depth connection without a browser.
Require both sides to contain 100 observed levels, an uncrossed book, and depth
within two seconds. Reconnects, stale timestamps and skipped events reset
persistence. ProjectX does not document a complete-snapshot boundary, so the
observed book is not claimed to be a certified complete exchange snapshot.
Unchanged qualifying episodes use a stable order-idempotency key. A later entry
requires a new qualifying episode; the previous position must reconcile flat.

One MNQ contract only. Entries retain the 09:35–15:30 New York session,
60-second cooldown, four-entry daily maximum and $250 daily loss budget. Existing
provider classification, account ownership, daily realized/unrealized P&L,
working-order and deployment-lease checks remain mandatory. No funded-account
routing. No averaging down, reversal, or adoption of a pre-existing position.

Submit a 100-tick stop bracket with the market entry (25 points at MNQ's 0.25
tick). No take-profit bracket. After the actual fill, observe liquidation-side
quotes (bid for a long, ask for a short). At +25 points activate a 25-point trail
from the best observed price. Tighten the same verified broker child stop in
place with `/api/Order/modify`, then verify its price. Never cancel/recreate it
to trail or widen it after a restart. A retracement through an unsubmitted trail
level invokes the existing verified contract-flatten path. Missing or overly
wide protection also invokes that path. Stop fills can slip.

Management runs on the worker polling cycle (normally five seconds), not on
every market tick. Favorable excursions are sampled from the independent live
stream between cycles. The last broker-confirmed stop remains if the worker
disconnects, but tightening requires the worker. High-water state is persisted
on management cycles; gaps/restarts may miss intervening extremes. Stop new
entries does not stop management of existing obligations. Flatten at the
existing session deadline (normally 16:08 New York, earlier on early closes).
There is no minimum holding time and no five-minute forced exit.

Practice auto-resume is an account-scoped opt-in (`practice_auto_resume` in the
saved strategy settings). When enabled, a worker restart or lease takeover keeps
the same explicitly started, running TopBot run and checks fresh ProjectX account
classification before evaluating entries. The account must still be simulated,
active, and tradable. Unavailable/unverified checks retry without placing entries.
The run ID and prior decision/idempotency state are retained; no new run is
created. Manual stops, disabled configs, unconfirmed runs, and other strategies
are never automatically restarted. Stop / Start and contract updates preserve
this account's preference. Other accounts default to requiring rearming.
Durable open-position obligations continue to be managed by the worker.
Refreshing or closing the dashboard does not stop a run.

The existing order-attempt JSON stores a bounded runner summary: actual entry
and exit fills, net P&L after reported per-fill fees, hold duration, observed
MFE/MAE in points, first observed times to +10/+25/−10, profit giveback, verified
stop and exit reason. Excursions are incomplete observed-quote measurements,
not certified tick-complete statistics. Ambiguous or missing fills stay pending,
are not counted as closed wins/losses, and block another entry until reconciled.
The panel reports mean/median holding times and counts on up to 500 recent
entries, displaying the latest 50. Break-even trades count as closed but neither
winners nor losers. Raw quote/depth arrays are never stored in Supabase.

Storage check on Oct 31: database 318,368,915 bytes, below the policy's 350 MB
investigation threshold. Typical runner metadata is a few KB per trade (four
entries/day), not an expanding tick history. Idle decisions retain the existing
five-minute cadence. No migration, historical import, deletion or billing change.

API reference checked Oct 31:
https://gateway.docs.projectx.com/docs/api-reference/order/order-modify/
https://gateway.docs.projectx.com/docs/realtime/

## October 31 deployment

Installed release `20261031-contrarian-25` on the existing `peteamdpc` worker,
replacing `20261030-research-24`. The independent depth recorder and feature
tasks were preserved. Local execution remains disabled. The hosted dashboard is
<http://127.0.0.1:4173/bot?account=28278873> through the existing private tunnel.

Fresh provider checks confirmed account `28278873` as simulated, active,
tradable, flat, and without working orders. Started config 39 / run 10 through
the hosted practice API on October 31 at 14:47 New York. The worker heartbeat,
execution lease, and readiness passed after startup. No order attempts occurred
during verification because the market was closed; actual entry fills and stop
modifications remain to be observed during an eligible market session.

Validation: 288 backend checks passed on the curated deployment source; 39 worker
checks passed separately. The final DOM suite passed 26 checks, including two
additional cancelled/unfilled-entry cases. Seventeen frontend checks, targeted
lint, and the production frontend build passed. No broker test order was sent.

The deployed chart initially reported missing continuous history. Copied the
three currently installed Databento source archives from this workstation,
verified their SHA-256 checksums, and rebuilt using `build_databento_cache.py`
under `D:/TopSignal/data/databento`. The new cache contains MNQ 1m, 5m, 15m, 1h,
4h, and 1d series; the hosted chart loaded after refresh. Original archives
remain on both PCs. No candle series were written to Supabase. This restores
available history; it does not certify every provider gap has been filled.

## Practice auto-resume deployment

Release `20261031-auto-resume-26` enables the saved opt-in for practice account
`28278873` / config 39. Verified a real worker restart on October 31: run 10
resumed automatically at 15:29 New York after fresh provider classification,
with its original run ID and decision state preserved. Worker and API readiness
passed; the independent recorder and feature task were unchanged. There were no
order attempts during this closed-market check. Manual stops remain stopped.
Validation passed 54 worker/recovery checks and 291 strategy/execution checks.

## Action audit

The action journal records evaluations (including HOLD and risk rejections),
each management check and its decision, entry and exit fills observed at the
broker, trailing activation, every requested stop movement, stop readbacks,
close/cancel requests, flat verification, run transitions, and recovery attempts.
Events include UTC timestamps (displayed in New York), account/run/entry/order
identifiers where available, reasons, observed prices, and old/new stops.

Broker mutation intent commits before the API call. A separate acknowledgement
does not claim a fill or successful modification; readback/fill events provide
that evidence. Timeouts are explicitly unconfirmed. A crash between steps leaves
the earlier request intact. Subsequent broker observations are additional events,
never replacements. Cloud transaction-dependent events are labeled pending
commit; the existing cloud order/run records remain authoritative for those
transactions. No historical stop movements are invented from current state.

Detailed events are stored in an append-only SQLite WAL/FULL journal below
`TOPSIGNAL_DATABENTO_CACHE_DIR/topbot-audit`, partitioned by owner and queried by
owned account with cursor pagination. Update/delete triggers protect event rows;
the app has no deletion or automatic expiration. This is durable local storage,
not tamper-proof remote storage. The existing `backup_market_cache.py` includes
these journals using SQLite's consistent backup API. No new cloud table or
per-tick market arrays are added. At continuous management/stop-update cadence,
budget tens of MB per active trading day on the worker disk, not Supabase.

If the pre-request audit write fails, that broker mutation is not sent; the
existing protective stop stays at the broker. Existing risk/error reporting
signals the failure and management retries. A manual Stop still works if the
local journal is unavailable, using its existing cloud lifecycle record and
critical runtime diagnostic. Detailed history begins at this release.
