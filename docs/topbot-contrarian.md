# TopBot contrarian practice strategy

Revision `dom_contrarian_v1`. User selected 10% imbalance sustained for 30 seconds.
Sum the nearest 100 observed bid levels and nearest 100 observed ask levels.
Imbalance = (bid quantity − ask quantity) / (bid quantity + ask quantity).
At +10% or greater, short; at −10% or less, buy. No price confirmation. This is
an experimental rule, not an established profitable signal.

The worker maintains its own ProjectX depth connection without a browser.
Use up to 100 available observed levels on each side; require a nonempty,
uncrossed two-sided book and depth within two seconds. Level-count changes alone
do not reset qualification. Reconnects, stale timestamps and skipped events reset
persistence. ProjectX does not document a complete-snapshot boundary, so the
observed book is not claimed to be a certified complete exchange snapshot.
Unchanged qualifying episodes use a stable order-idempotency key. A later entry
requires a new qualifying episode; the previous position must reconcile flat.

One MNQ contract only. Entries are eligible throughout all open trading sessions,
including overnight; the saved window is 00:00–23:59 New York. The DOM execution
path already bypassed the old 09:35–15:30 preset, which was misleading in the UI.
New entries pause at the runner's daily flatten cutoff (normally 16:08 New York,
earlier on early closes) until the next session opens, normally at 18:00 New York
on Sunday through Thursday. During this pause the worker stays armed without
generating or consuming entry signals, so scheduled downtime does not produce
blocked BUY/SELL activity. Direct strategy evaluations return HOLD. Weekend and
holiday closures still apply, and position management continues. Exchange closure checks,
60-second cooldown, four-entry daily maximum and $250 daily loss budget remain. Existing
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

Daily P&L protection uses account-wide realized net P&L on the existing daily
loss-check fee basis plus current unrealized P&L. At **+$500** or **−$500**, close
TopBot's owned position and pause new entries until the next trading session.
The break-even profit floor is disabled. A jump past either threshold also
triggers the stop. The daily loss entry budget is **$500**; the existing proposed
stop-risk check can reject an entry before the remaining budget is exhausted.
Daily stops do not open an offsetting position or flatten unrelated contracts.

The account preference `daily_entry_limit_enabled=false` removes only the daily
entry-count cap and survives Stop/Start and contract rolls. Signal qualification,
position limits, cooldown, session cutoffs and protective-stop checks still apply.

The worker observes P&L on its normal polling cycle, including while a runner
is open or entries are manually stopped. A flat, active run is checked even
without a qualifying DOM signal. Broker unrealized P&L is preferred; when absent,
an MNQ position can be valued using fresh executable bid/ask quotes. Incomplete,
stale, or failed P&L checks block new entries while existing stop/session
management continues. Polling can miss brief threshold touches; fill slippage,
fees, data gaps, and broker latency mean +$500 and −$500 are trigger levels, not
guaranteed exit proceeds. Monitoring starts with available observations and
does not reconstruct unobserved earlier peaks.

The triggered stop survives Stop/Start, configuration changes,
SQL rollbacks, and worker restarts. They reset at the existing **18:00 New York**
trading-day rollover, after which an enabled run may trade again. An unfinished
close retains its exit intent across that rollover and is reconciled/retried
before new entries. Stopping a run manually still requires starting it again.

State is one bounded row per owner/account in the worker's existing local
`topbot-audit/.../actions.sqlite3` journal under `TOPSIGNAL_DATABENTO_CACHE_DIR`
(or its existing audit-directory override). Trigger transitions append audit
events atomically with the state update. Preserve this directory during releases
and worker moves; the existing SQLite backup captures both state and events.
No cloud migration, bulk write, historical cache, or per-tick series is added.
The checklist displays current/peak observed daily P&L, protection state, and
the session stop reason from this same worker state.

The entry checklist beneath the imbalance timer combines live depth qualification
with bot/worker status and the last recorded broker risk pass. Preliminary checks
do not certify broker readiness. Unchecked items remain unknown; recorded checks
become stale after 30 seconds, a different qualifying episode, or unavailable
monitoring/depth. A passing checklist item does not confirm an order or fill.
The consumed-episode marker explains why an already-evaluated imbalance does not
trigger another attempt. The display is read-only and never triggers evaluation.
Risk passes attach a bounded checklist to the existing decision JSON, without
new tables, market series, or increased evaluation frequency. Older decisions
without this evidence show unknown checks and any explicit recorded rejection.

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

November 11, 2026: the Databento cache was retired on the host (backtesting is
no longer used). Its data ended October 23, and the chart had already stopped
joining it to live bars after November 6. The Bot chart no longer depends on
it: every timeframe, including 1D, loads the bot contract's own history from
ProjectX. History stitched across earlier contract months is no longer shown.

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
