# Discord trade reporting

The TopSignal server has `daily-bias` and `trades` under **Trading**
(`1570480841560170547`), and `topbot` and `topbot-recap` under **Trading Bot**
(`1570483290761334864`). The bot channels were moved into their own category
at the user's request without syncing/replacing their channel permissions.
`daily-bias` is for the owner's bias and reasoning before 9:30 AM New York time.
`trades` is for the owner's manual trade callouts. Members can view all four
channels and read their history, but cannot send messages, create or reply to
threads, manage the channel/webhooks/permissions, or use app commands, activities,
or external apps. Server owners and administrators bypass channel restrictions;
do not grant Administrator to ordinary members. No daily reminder is scheduled.

`backend/tools/discord_topbot.py` is an independent read-only reporter. It does
not import the trading application, submit orders, change bot settings, or write
to PostgreSQL. Each database transaction is explicitly read-only, bounded by a
10-second statement timeout, and restricted to local execution PostgreSQL.
SQL joins preserve owner, account, and TopBot configuration scope. No cloud
tables or historical candle data are added.

## Trade messages

The reporter publishes a verified entry fill, then edits that same message when
the runner reconciles the exit. It includes symbol, side, quantity, entry/exit
prices and times, verified bracket prices, gross P&L, fees plus commissions, and
net P&L. Practice accounts are labeled PRACTICE using account classification,
not the internal `live` flag (which also describes routing to practice accounts).
Unknown account classification is explicitly labeled unverified. Account IDs,
balances, credentials, raw request/response objects and execution diagnostics
are excluded. Mentions are disabled.

Amounts come from TopBot's existing fill reconciliation. Configured commission
estimates are labeled. Missing or inconsistent P&L is not invented. An entry
without a confirmed fill is not published. Delayed exit reconciliation keeps
the report open/pending. If entry and exit are both first observed after a brief
trade or outage, the initial message contains the completed result.

On first startup, completed history is skipped. Existing open/pending entries
and future entries are watched. After that, the SQLite discovery cursor and
message IDs survive restarts; downtime does not reset the cursor. Incomplete
trades are checked indefinitely, and completed records are checked for changes
for seven days after their last report update. Polling normally runs every five
seconds; provider reconciliation and Discord/network delays add latency.

## Host installation

Prepared on peteamdpc using the existing limited `TopSignalSvc` identity:

November 12 setup status: **active**. All three Discord channels and their
permissions are saved and verified. The user approved the channel-scoped webhook,
which is named TopBot. Its private secret file is readable only by the existing
service identity, administrators and SYSTEM. The temporary development-PC secret
was removed after the verified transfer. The source read and webhook channel
checks passed, followed by a real POST and PATCH of completed practice trade 254
(message `1570479919115272284`). That one completed report is the connection
verification example; other completed history was skipped. Its durable delivery
record prevents a duplicate when the watcher starts. No synthetic trade was
created and no broker order was placed for verification.

The scheduled task is enabled and running under limited `TopSignalSvc`, with an
advancing healthy heartbeat and no errors. The trading and depth-recorder tasks
remained running without restart. All 13 offline reporter tests passed. Host and
source reporter SHA-256 both match
`07db17d87eb36dcbcaf99a1d8cd83f1d5f65f20c08192d78a789490b254f19e0`.
The browser visibly confirmed the TopBot identity, PRACTICE label, entry/exit,
fees and net result, and edited marker. A future naturally occurring trade's
full entry-to-exit lifecycle has not yet been observed; that transition is covered
by the offline tests and the live post/edit transport verification.

- Current bundle/config: `C:/ProgramData/TopSignal/tools/discord-topbot-v2/`
- Trade webhook secret remains in the preserved `discord-topbot-v1/` bundle;
  recap webhook secret is `discord-topbot-v2/recap-webhook.secret`.
- State/lock/health/STOP: `C:/ProgramData/TopSignal/runtime/discord-topbot/`
- Python and source environment: release `20261111-research-heatmap-82/backend/`
- Task: `TopSignal Discord TopBot`
- Bot configuration: 40
- Discord server: `1479619303098814486`
- Discord channel: `1570477343187664986`

The bundle is writable only by administrators/SYSTEM; the service can read it.
The service can modify its own runtime directory. `webhook.secret` must contain
only the Discord webhook URL and must inherit the private bundle ACL. Never
commit, print, or paste that URL into chat or logs. A webhook has only its
channel's posting capability; it does not need server Administrator or message
reading access. Retain the pinned release or explicitly update the reporter's
Python path and environment path during a future release lifecycle.

Use `scripts/install-discord-topbot-task.ps1` with the existing service credential
helper. The installer refuses to replace an existing task. The task is hidden,
starts at boot, restarts after failure in one minute, and has a five-minute
recovery trigger. Task Scheduler IgnoreNew plus an OS file lock prevents overlap.
The existing trading and recording tasks do not need restarting.

Check the connection without posting:

```powershell
& 'C:/ProgramData/TopSignal/releases/20261111-research-heatmap-82/backend/.venv/Scripts/python.exe' -B 'C:/ProgramData/TopSignal/tools/discord-topbot-v2/discord_topbot.py' --config 'C:/ProgramData/TopSignal/tools/discord-topbot-v2/config.json' --check
```

`status.json` contains the heartbeat, process ID, delivery counts, and sanitized
error categories. It contains no webhook or database secrets. Add the runtime
`STOP` file to stop gracefully; leave it present to prevent the recovery trigger
from resuming the loop. Disable the scheduled task for a persistent operator stop.
Do not delete `delivery.sqlite3` or its WAL files: they prevent duplicate reports.

## Ambiguous delivery recovery

Discord webhook creation has no idempotency key. A timeout, server failure, or
process interruption between sending a new message and saving its ID may have
an unknown result. The reporter marks that trade `uncertain`, leaves other trades
running, and reports `attention` rather than blindly creating a duplicate.
An explicitly deleted message becomes `missing_message` and is not recreated.
Rate limits use Discord's retry delay; PATCH retries safely edit the same ID.

For recovery, stop the reporter, inspect `#topbot` for its `TopBot trade #N`
footer, and reconcile the corresponding local delivery row. If the message
exists, save its actual message ID and set status to `ready` with a null digest;
the next cycle updates it. Only reset to a new unsent row after confirming no
message exists and authorizing a resend. Preserve an SQLite online backup before
manual state repair. No uncertain reports are automatically discarded.

Automated tests cover fill filtering, privacy, arithmetic validation, open-to-close
editing, restart deduplication, late discovery, rate limits, ambiguous POSTs,
idempotent PATCH retries, deleted messages and endpoint/channel validation.

## Daily recaps — active November 12, 2026

`discord_topbot_recap.py` runs inside the existing reporter on peteamdpc, checking
once per minute. It publishes to `#topbot-recap` (`1570481826840191097`) at
**5:05 PM America/New_York, Monday–Friday**. ZoneInfo handles daylight saving
changes. Holidays still receive a zero-trade recap if there were no fills.
This is a feature of the continuously running reporter, not a separate Codex
automation or an additional Windows scheduled task.

The session is identified by its end date and covers entries from 6 PM the prior
evening through just before 6 PM on the report date, matching TopSignal's futures
trading-day convention. The scheduled publication is after the exchange's normal
5 PM closure and TopBot's earlier flatten deadline. It includes all matching
TopBot configuration 40 entry audits from that session, including trades that
occurred before the Discord reporter was installed; it does not count posted
messages, manual orders, rejected/unfilled attempts, or other bot configurations.

Each recap includes total trades, wins, losses, breakeven trades, pending results,
win rate, gross P&L, fees/commissions, and net P&L. Wins and losses use net P&L,
rounded to cents per trade; win rate uses completed trades including breakevens.
Open or incompletely reconciled trades count toward total trades but not realized
P&L or completed outcomes, and the recap is clearly marked provisional. Invalid
completed data prevents publication of a misleading partial total. Configured
commission estimates and practice-account status remain explicit.

The local delivery database now also stores one compact `recaps` row per session
and a durable scheduling cursor. No database migration or cloud write is needed.
Missed weekday recaps catch up after downtime (at most five newly queued sessions
per minute). Recent recaps are recomputed for seven days; pending sessions are
revisited indefinitely. Changes edit the same message. The same ambiguous-POST,
deleted-message, sanitized error and restart deduplication rules apply. Recap
connection/query failures do not prevent individual trade reporting.

The user approved the new channel's webhook separately. The `TopBot` webhook
secret is protected with the same service-read/admin-write ACL and the temporary
local transfer copy was removed. The existing reporter was gracefully stopped,
its SQLite state backed up and integrity-checked to
`discord-topbot-v2/delivery-before-recap.sqlite3`, then its task action switched
to v2 using the existing limited service credential. The original task XML is
preserved as `discord-topbot-v2/previous-task.xml`. An initial action update
required supplying that existing credential; it failed before changing the task
and was recovered with the reporter stopped and its STOP marker preserved.
The TopBot execution and depth-recording tasks were not restarted.

Live verification published the completed November 11 session through the normal
recap scheduler: **40 trades, 19 wins, 21 losses, 0 breakeven, 0 pending;
gross −$183.50, fees $48.80, net −$232.30**. Discord message
`1570482859096146092` was visually verified along with the Trading category and
all four channels. The next automatic report is November 12 at 5:05 PM Eastern.
All 22 offline tests passed, including DST, schedule boundary, weekend skipping,
catch-up, overnight attribution, incomplete results, fee-based wins/losses,
restart deduplication, and late-result edits. No synthetic trade was submitted.

Verified deployed hashes:

- `discord_topbot.py`: `09aec2786b484ac542829c616fee0b2250139605b248bc5c78e3b399b8d34934`
- `discord_topbot_recap.py`: `5291e3b9eedd695d128c04e7843ec7a532c0d15622076021fe40ee6a5f3d4de4`
