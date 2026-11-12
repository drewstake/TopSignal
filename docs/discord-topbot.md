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
net P&L. Exit-reason text is intentionally omitted. Practice accounts are labeled PRACTICE using account classification,
not the internal `live` flag (which also describes routing to practice accounts).
Unknown account classification is explicitly labeled unverified. Account IDs,
balances, credentials, raw request/response objects and execution diagnostics
are excluded. Mentions are disabled.

Amounts come from TopBot's existing fill reconciliation. Configured commission
estimates are labeled. Missing or inconsistent P&L is not invented. An entry
without a confirmed fill is not published. Delayed exit reconciliation keeps
the report open/pending. If entry and exit are both first observed after a brief
trade or outage, the initial message contains the completed result.

The owner selected **option C: Trade receipt**. A deterministic image card
puts labels on the left and values on the right: Entry, Exit, Stop, Target,
Gross and Fees. Net P&L is prominent above the ledger. Green/red indicates the
net outcome; amber indicates an open or reconciling trade. No "Take profit",
"Stop loss", or other exit reason is displayed. Stop/Target remain bracket
prices. The removed explanatory completion/commission paragraph stays removed;
configured fee estimates retain a concise `(est.)` label. Date, contract count,
practice/live classification and trade number remain visible.

Receipt revision v3 sends a single standalone 1000×720 image attachment, with
no enclosing embed or inner rounded outline. Its sole colored edge is drawn
in the image. The wider aspect ratio and larger type improve actual Discord
display size; the buttons sit directly below the attachment. Edits explicitly
clear old embeds. The compact timeline is drawn directly into the receipt,
including a 32-frame waiting ring, rather than inserting a separate strip.

Card times use America/New_York, explicitly labeled ET. Overnight exits include
their different date. USD is labeled once. Only verified completed results
populate realized net/gross/fees; pending amounts display a dash. The large
three-stage timeline is retained without secondary captions. Open cards use
an animated GIF; complete cards and private button snapshots use a PNG.
View trade and Refresh status remain actual Discord buttons below the image.

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

- Current bundle/config: `C:/ProgramData/TopSignal/tools/discord-topbot-v5/`
- Trade webhook secret remains in the preserved `discord-topbot-v1/` bundle;
  recap webhook secret is `discord-topbot-v2/recap-webhook.secret`.
- State/lock/health/STOP: `C:/ProgramData/TopSignal/runtime/discord-topbot/`
- Reporter Python: `discord-topbot-v5/.venv/Scripts/python.exe`; source environment
  remains release `20261111-research-heatmap-82/backend/.env`.
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
source environment path during a future release lifecycle. The v5 reporter has
its own Python environment; its dependencies do not modify the trading runtime.

Use `scripts/install-discord-topbot-task.ps1` with the existing service credential
helper. The installer refuses to replace an existing task. The task is hidden,
starts at boot, restarts after failure in one minute, and has a five-minute
recovery trigger. Task Scheduler IgnoreNew plus an OS file lock prevents overlap.
The existing trading and recording tasks do not need restarting.

Check the connection without posting:

```powershell
& 'C:/ProgramData/TopSignal/tools/discord-topbot-v5/.venv/Scripts/python.exe' -B 'C:/ProgramData/TopSignal/tools/discord-topbot-v5/discord_topbot.py' --config 'C:/ProgramData/TopSignal/tools/discord-topbot-v5/config.json' --check
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

For recovery, stop the reporter, inspect `#topbot` for its `Trade #N`
footer, and reconcile the corresponding local delivery row. If the message
exists, save its actual message ID and set status to `ready` with a null digest;
the next cycle updates it. Only reset to a new unsent row after confirming no
message exists and authorizing a resend. Preserve an SQLite online backup before
manual state repair. No uncertain reports are automatically discarded.

Automated tests cover fill filtering, privacy, arithmetic validation, open-to-close
editing, restart deduplication, late discovery, rate limits, ambiguous POSTs,
idempotent PATCH retries, deleted messages and endpoint/channel validation.

## Trade timeline and buttons — active November 12, 2026

The current receipt renderer is `discord_topbot_receipt.py`, using Pillow in
the isolated reporter environment. It receives only an explicitly whitelisted
public model after fill/P&L validation. `_receipt` is local rendering input,
removed before REST serialization. Content-hashed filenames and a bounded
64-card byte cache allow unchanged polls to avoid image rendering and uploads.
No per-trade image files or input data are persisted to cloud storage. Images
include an accessible description of the trade values. Segoe UI comes from the
Windows host, with a portable font fallback; font files are not redistributed.

The following records the earlier standalone timeline implementation:

The reporter renders option B with a graphical progress strip inside the native
Discord embed: **Entry filled → Trade open → Result verified**. This replaces
the initial Unicode timeline at the owner's request. Drawn checkpoints and
connecting lines show progress; a 32-frame amber ring rotates while waiting.
The roughly 142 KB GIF animates in the client, with no periodic animation PATCH
requests or completion estimate. Polling remains every five seconds; real data
changes publish on the next poll. Exit reconciliation has its own pending
graphic. Only reconciled completed fills select the static, all-checked PNG.
In the original v4 layout, trade fields and buttons remained native Discord controls. Private button replies
use a static PNG and explicitly say they are snapshots. Discord users with GIF
autoplay disabled still see a meaningful first frame.

`discord_topbot_media.py` resolves the receipt renderer or five allowlisted bundled assets from
`discord_topbot_assets/`. REST requests use multipart `payload_json` plus the
image, and PATCH explicitly replaces the prior attachment rather than adding
files indefinitely. Images include accessible descriptions. The development
builder `build_discord_topbot_media.py` uses Pillow and Segoe UI; shipped assets
originally required neither on the reporter host; v5 uses Pillow for the full receipt.
Run the builder locally after timeline design
changes and deploy the assets together with the source.

The v2 assets remove the secondary captions (Confirmed, Monitoring, Pending,
and their completed/reconciling equivalents). Main labels increase from 25 to
36 pixels and checkpoints from 25 to 38 pixels in an 840×230 strip, giving
roughly 50% larger visual elements at Discord's embed width. Versioned filenames
ensure existing reports replace their cached image on the next normal poll.

The existing incoming webhook has no application owner. Discord does not allow
callback buttons on such messages. Timeline-only mode therefore works with the
current configuration; interactive mode uses an optional Discord bot connection.
Interactive mode is **active on peteamdpc** using TopBot application/bot
`1570523660211589231`. The owner approved creation, developer terms, token
generation and installation, and completed Discord's MFA prompt.

Interactive mode adds two buttons to newly created reports:

- **View trade** reads the same scoped source row and shows verified trade
  details privately to the person clicking. It does not expose account IDs,
  credentials or execution diagnostics.
- **Refresh status** reads that row, edits the original report and privately
  acknowledges the result. It never submits an order or forces broker
  reconciliation. A trade still awaiting reconciliation remains pending.

Gateway callbacks work after restarts without a per-message in-memory registry.
They verify the server, channel, message author, saved message ID and trade ID.
Reads and public edits run on the reporter thread with normal polling. A bounded
32-item queue, at most four processed clicks per cycle and a ten-second per-trade
cooldown limit repeated clicks. Discord rate limits feed back into polling delay.
Gateway disconnection is reported as `buttons_disconnected` in `status.json`;
automatic reporting continues. No privileged Gateway intents are requested.

### Activation prerequisites

1. Create a **TopBot** Discord application and bot, accepting Discord's developer
   terms only with the owner's approval. Install it in the TopSignal server.
   Give its bot member **View Channel**, **Send Messages**, **Embed Links**,
   **Attach Files** and **Read Message History** in `#topbot`; no Administrator, broker access,
   privileged intents or inbound public HTTP endpoint is needed.
2. Store the bot token in a private file on peteamdpc with the same service-read,
   administrator/SYSTEM-write ACL as the webhook secret. Do not put the token
   in config JSON, command arguments, source control, chat or diagnostic output.
3. Install `backend/requirements-discord-reporter.txt` in a dedicated reporter
   Python environment (`requirements-discord.txt` adds only the optional SDK).
   Copy `discord_topbot.py`, `discord_topbot_buttons.py`, `discord_topbot_media.py`, `discord_topbot_receipt.py`
   and `discord_topbot_assets/` into the reporter bundle, preserving
   `discord_topbot_recap.py` and the existing webhook secret.
4. Add `bot_token_file` (the absolute private token-file path) and `guild_id`
   (`1479619303098814486`) to the existing config. Keep `webhook_file`,
   `channel_id`, `runtime_root`, `env_path`, `bot_config_id` and `recap` unchanged.
5. Gracefully stop only the reporter using its STOP marker; preserve a verified
   SQLite online backup and the old bundle/config before replacing source. Run
   `--check`, remove STOP and restart the reporter task. Verify its heartbeat,
   Gateway connection and both buttons on the next naturally occurring report.
   Do not restart TopBot execution or recording tasks, seed a fake trade, reset
   delivery state, or repost historical trades to test the UI.

The bot edits its own new messages. Existing webhook-authored messages (including
trade #266) continue to be edited through their original webhook, with the new
timeline but without callback buttons. Discord does not permit changing their
author or attaching another application's callbacks. This preserves message IDs
and avoids duplicate reports. The recap continues to use its existing webhook.

No database schema, cloud storage or trading code changes are required. Once
bot-authored reports exist, preserve the bot connection for their future edits;
removing its config cannot turn those reports into webhook-authored messages.

Offline checks:

```powershell
cd backend
& .venv/Scripts/python.exe -m pytest tests/test_discord_topbot.py tests/test_discord_topbot_buttons.py tests/test_discord_topbot_recap.py -q
```

### Deployment verification

The v3 task uses its own virtual environment and preserves the original source
environment, recap configuration and delivery database. An SQLite online backup
at `discord-topbot-v3/delivery-before-v3.sqlite3` passed integrity and record
checks before activation; all 15 existing delivery records were retained.
The previous task XML and v2 bundle are preserved. Only the reporter task was
restarted. The token is stored in `discord-topbot-v3/bot-token.secret`, readable
by TopSignalSvc and writable by administrators/SYSTEM; the local transfer copy
was removed.

Member-specific channel overrides permit TopBot to view/send/embed/attach/read
history only in `#topbot` (allow bitfield `117760`). Attach Files was added for
the graphical strip. All other existing channels and
categories deny its View Channel permission. The installation grants no guild
permissions, and user installation is disabled. Recaps retain their webhook.

The deployed standalone command passed source and destination checks. Its
heartbeat advanced with `state: watching`, no errors, and a connected Gateway.
Existing reports visibly received the timeline without changing message IDs;
trade #268's natural close at 3:00 PM Eastern updated in place. All **37 focused
tests passed**, covering button authorization, private replies, refresh,
cooldowns, rate limits, legacy messages and restart behavior. A standalone
module-identity regression discovered during activation was fixed and tested
before the healthy restart.

Natural trade **#269** opened at 3:01 PM Eastern and was published as bot-authored
message `1570529031063871695` with both buttons. Live Discord verification showed
the waiting indicator advance through all three dot positions, **View trade**
return private verified details, and **Refresh status** return “Status checked;
the trade message is up to date.” Repeated clicks also exercised the cooldown.
No synthetic trade, duplicate historical report or trading action was used.

Initial v3 source hashes (before the graphical revision):

- `discord_topbot.py`: `a7810c32418162ef704083dd5df985017667b23d383784e66ffee08d60ee5d06`
- `discord_topbot_buttons.py`: `c8c4a20d586fd21c268c97bbc1467ceddcb0f95e90e5512da1911a16a53a368c`
- `discord_topbot_recap.py`: `5291e3b9eedd695d128c04e7843ec7a532c0d15622076021fe40ee6a5f3d4de4`

The graphical v4 revision preserves v3's isolated interpreter and private token
path. The task now runs v4 source/config, with the previous task definition and
an integrity-checked 17-record delivery backup in `discord-topbot-v4/`.
All **39 focused tests passed**, including multipart attachment replacement,
asset path restrictions, static private snapshots, and unchanged-poll deduplication.
The healthy reporter updated existing messages in place, including open #270.
Both buttons were exercised on #270 after deployment: refresh edited the same
message and returned a private static snapshot; View trade returned private
details with the new graphic. Discord visually displayed the attached progress
strip, and the subsequent heartbeat remained `watching` with no errors.

Verified v4 source hashes:

- `discord_topbot.py` (compact card): `2828e25126abdbaa3e5cb2962997a7bc8ba429d63392caf83929e1cbb21b5f0e`
- `discord_topbot_buttons.py`: `3f4ab10ae141d30774abdc1c0029a27b39cb052a2855217cf1518a10474a84f8`
- `discord_topbot_media.py` (v2 assets): `e7b0314be5d3ab34667b0c5dfce79dbbb8f775e9172f02511ec76163ccb99645`

The receipt v5 revision runs from `discord-topbot-v5/` in its own virtual
environment with Pillow pinned. The previous task definition and an
integrity-checked 18-record delivery backup are preserved in that bundle;
the v4 bundle remains available for rollback. The source scope, private token
path, recap configuration, and delivery database were retained. Only the
reporter task was restarted.

All **40 focused tests passed**, including receipt rendering, animated/open
versus static/completed media, loss colors, overnight timestamps, private
snapshots, attachment transport, and exclusion of exit reasons. Local and
remote source hashes match. The deployed heartbeat is `watching`, with no
errors and all 18 delivery records ready. Live Discord verification showed
red trade #269 and green trade #270 in the new receipt format, retaining their
message IDs. Both **View trade** and **Refresh status** returned private receipt
snapshots; refresh updated the same public message successfully. No synthetic
trade or duplicate historical message was created.

The larger receipt revision updates existing messages in place and explicitly
removes their old embeds. Public open trades remain animated; private replies
remain static attachments. All 40 focused checks passed again. During activation
the scheduled reporter exited once; a diagnostic run passed and the limited
service task was restarted successfully before final verification.
The live #272 open GIF and private View trade snapshot were verified, followed
by its natural close updating the same message to a red completed receipt.
In the checked desktop view the image grew from about 303 to 486 pixels wide;
the primary ledger text is approximately twice its former displayed size.

Verified v5 source hashes:

- `discord_topbot.py` (standalone attachment): `8ee5ccaa1634a0a3f459391deb7d3725cc7a7227ef4ef542cc929dd74b732016`
- `discord_topbot_receipt.py` (larger receipt): `420cdf1a182d1294b17fc93b2d3112b8e14b47eaec9f396c2a4fb2a07723df7c`
- `discord_topbot_media.py`: `26e167d9ef06416d097cf14252a30358ece6243e329a9c1f7e569219630cd04f`

Protocol references: [Discord webhooks](https://docs.discord.com/developers/resources/webhook),
[attachment uploads](https://docs.discord.com/developers/reference#uploading-files),
[message components](https://docs.discord.com/developers/components/reference),
and [discord.py Gateway interactions](https://discordpy.readthedocs.io/en/stable/api.html#discord.on_interaction).

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
