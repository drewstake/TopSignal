# Trade progress recording

Introduced on peteamdpc November 11, 2026 as `20261111-trade-progress-77`
(`46afcd9`). At activation, 25 checks confirmed readiness for practice account
`29593203`. The [November 13 audit](audits/2026-11-13/peteamdpc-health.md) found
only that account enrolled, with a fresh heartbeat, 91 position lifecycles and
34,944 observations at 18:49:58 UTC, but 307 gap events and zero fill rows. These
are dated observations, not proof of complete coverage. The subsequent
[fixed-bracket deployment and start](audits/2026-11-11/fixed-brackets-deployment.md)
preserved recording and started the practice bot with persistent 25-point SL/TP.
See the [deployment record](audits/2026-11-11/trade-progress-deployment.md)
for activation, validation and backup evidence. This implements
[the recording proposal](trade-progress-recording-idea.md).

## What is recorded

`Not enrolled` means the selected owner/external account has no enabled scope
row, not that the global recorder is stopped. `No recorder heartbeat` means that
scope has no heartbeat value; a disabled scope can retain an old one. The
cadence/manual-trade subtitle describes the feature, not proof of capture.
Enrollment does not follow account selection or automatically transfer to new
accounts in release 87. A heartbeat older than 30 seconds or a stored error makes
an enrolled scope unhealthy.

Check the active account control and actual API account, not only the URL. On
November 13, the URL named unavailable account `28278873` while the UI selected
`29672001`; neither had enrollment. Practice `29593203` remained enabled under
the same owner. No disable event was established. Scope rows have no enrollment
change timestamp/history, so absence cannot prove an account was never enrolled
in any older store.

An independently enrolled ProjectX account gets read-only user/market hub
subscriptions, a position reconciliation thread and a local SQLite writer.
The API lifespan owns the manager; an OS file lock permits one manager across
API processes. Recording continues with the browser closed and the bot disarmed,
and includes manual positions. CSV accounts cannot enroll. Existing excursion
enrollment does not implicitly enroll accounts in this feature.

The shared hub runner sends a SignalR protocol ping every 10 seconds on both
connections, independently of quotes or positions. This is separate from the
one-second sample interval. Ping-send failures and server Close frames end
coverage and reconnect with backoff; all connection tasks are reaped on stop.
Fixed local reason codes distinguish handshake failures from server closure
without logging provider messages or bearer tokens.
After three failures without a stable connection, it invalidates only the token
that failed once per failure streak, retaining reconnect backoff. This matches
the existing depth stream's bounded authentication recovery.

Each position receives observations approximately every second, plus entry,
scale-in, basis change, partial exit, reversal and exit observations. Each sample
contains owner, external account, local position UUID, provider position ID,
delivery contract, observation timestamp, direction, quantity, current average
entry, mark, mark timestamp/basis, signed points, point value and unrealized
dollars before fees. Timestamps are UTC epoch seconds; the review displays ET.

`points = mark - average entry` for longs and `average entry - mark` for shorts.
`unrealized dollars = points × remaining absolute quantity × instrument point value`.
Missing point values leave dollars unavailable. Points are never divided by a
maximum or original position size. Each observation preserves its own basis.

The existing `parse_quote_trade` adapter supplies a bid/ask midpoint when a valid
two-sided market is present, otherwise a timestamped last/mark price. This is a
valuation mark, not an executable liquidation quote or exit fill. Missing dates,
future-dated and older prices are rejected. Prices more than **30 seconds** old
remain identified as stale with null points/dollars. No last-price carry-forward
is plotted as fresh after that limit.

Every accepted incoming price can update best/worst points and dollar extrema.
Extrema retain their exact price timestamps, quantity and entry basis. Point
and dollar extrema are separate because scaling can make their winners differ.
Only the final extrema are retained, not an unbounded tick array. The chart plots
point extrema independently of the one-second path, including brief spikes.
The one-second cadence was requested November 11, replacing the original
10-second interval. Samples use the latest received market price and retain its
timestamp; sampling faster does not guarantee a new price every second.

`GatewayUserTrade` legs are stored separately by owner/account/provider trade ID.
They retain provider-reported realized P&L, fees and commissions; they are never
added to remaining-position unrealized P&L. Every distinct correction is retained
by payload hash. Because the provider does not supply a revision sequence,
conflicting versions withhold an authoritative realized amount instead of letting
a replay undo a correction. Voids are identified. The UI labels fill matching as
an interval candidate, particularly at reversals; it does not manufacture a
position realized total or assume missing fills mean zero.

## Lifecycle matching and coverage

The provider documents position ID, creation timestamp, direction, size and
average price, but no guaranteed position update timestamp. Its documented quote
fields include bid, ask, last and timestamps. Trade corrections reuse the leg ID.
See [ProjectX realtime payloads](https://gateway.docs.projectx.com/docs/realtime/)
and [open-position snapshots](https://gateway.docs.projectx.com/docs/api-reference/positions/search-open-positions/),
checked November 11, 2026.

- Scope is always owner + external account + exact delivery contract. A local
  UUID identifies each recorded position. Provider ID plus creation timestamp
  establishes continuity across snapshots and reconnects. A sign reversal closes
  one lifecycle and opens another; an identity replacement is explicitly flagged.
- Scaling keeps the UUID and updates quantity/average entry at the observed
  transition. Partial exits keep the remaining basis. An exit sample retains the
  pre-exit quantity/basis plus `quantity_after=0`; its mark is not the fill price.
- Explicit update timestamps reject older/conflicting events. Consecutive
  identical states are deduplicated, and durable cursors reject old provider
  identities after closure. The most recent 256 retired identities and the
  creation-time watermark prevent ordinary replay resurrection.
- Undated changes use **receipt time**, marked `position_update_time_unavailable`.
  Undated nonconsecutive replays cannot be perfectly ordered. REST reconciliation
  every 15 seconds corrects the current state and marks changes found by snapshot.
  A request started before a newer stream event cannot overwrite it. Receipt-time
  observations never claim provider-exact transition timing.
- Disconnects, failed snapshots, queue overflow, backlog, delayed sampling,
  recorder stop and restart create explicit gaps. A restored position waits for
  fresh reconciliation and connected streams. Missing identities split uncertain
  continuity into separate recordings. A position absent from a successful
  snapshot gets an `exit_detected` event with unknown exact exit time and price.
- Recording discovered after entry is flagged. Entire positions opened and
  closed while the backend was absent may have **no recording at all**. No
  synthetic observations are inserted into any gap. This implementation performs
  no historical reconstruction; all stored samples are tagged `live_observation`.
  Historical excursion estimates and the market archive remain separate.
- Trade review finds candidate recordings by owned account, delivery contract and
  the trade's timestamp. It never selects a nearest position. Multiple candidates
  require selection; the review describes the whole position, not an allocation
  to each partial fill. An unmatched trade is "not recorded", not a flat path.

The state machine does not assert complete coverage, even for a connected feed.
Provider delivery, sequence information and backend availability limit the
observations. Currently open or historical production trades have not acquired
coverage merely because this code exists.

## Storage and failure isolation

The only destination is:

`TOPSIGNAL_DATABENTO_CACHE_DIR/trade-progress-v1/progress.sqlite3`

All samples, lifecycle state, fill revisions, cursors, enrollment, health and
destination verification live there. No SQLAlchemy feature models, cloud JSON
attachments, Supabase tables, object-storage objects or cloud fallback are used.
All tables use the `trade_progress_` prefix, also rejected by both discovery and
row application in the existing PostgreSQL mirror. Normal authentication,
accounts, trades, journal storage and existing excursion behavior are preserved.

Authenticated endpoints first verify account ownership in the existing account
store, then restrict SQLite reads by the same owner/account. Timeline IDs alone
grant no access. Timeline pages contain at most 5,000 observations, with a cursor;
fill lists are capped at 1,000 with an explicit truncation flag. UI requests are
aborted on navigation and the review is scoped to the selected account.

Socket callbacks use a bounded 20,000-event queue and perform no disk/provider
I/O. The writer updates extrema in memory and commits approximately once per
second with `synchronous=FULL` and WAL mode. State, cursors, fills and observations
commit together. A process crash can lose the uncommitted tail; restart starts a
gap at the last durable observation. A failed commit reloads committed state,
discards the uncertain queue tail and requests reconciliation. Optional recording
failure does not prevent trading execution.

The verified root, host, drive anchor, device identity and volume size are checked
before enrollment and writer start, then every 10 seconds. The resolved database
must remain beneath the cache; relocating/restoring requires reverification.
Writes pause below **10 GB free**, without deleting data. The status endpoint
checks storage independently; failed writes also age the heartbeat. The UI polls
every 15 seconds and treats a heartbeat older than 30 seconds as unhealthy.
Logs include stable error types, not provider payloads or credentials. Stale or
missing prices on active positions make recorder health unavailable.

There is **no age-based deletion**. Disabling enrollment stops future recording
and retains the database. Do not delete SQLite/WAL files, enable a cloud fallback,
change billing, or purge old recordings to recover space.

## Measured size and capacity

Run the disposable synthetic benchmark from the repository root:

```powershell
& backend/.venv/Scripts/python.exe backend/tools/benchmark_trade_progress.py
```

November 11 one-second measurement: 86,400 periodic samples plus entry occupied
**49,201,152 bytes**, including every table and index, about **569 bytes per sample**
or **2.05 MB per open position-hour**. The synthetic WAL high-water allocation was
**4,396,072 bytes**. This Windows SQLite build lacks `dbstat`, so index bytes are
included in the total rather than reported separately. The benchmark uses batch
commits for sizing, not a production throughput/durability benchmark. It derives
its sample count from the recorder cadence and simulates 24 hours with one open
position. The recorder still commits about once per second; the shorter sample
interval does not add provider polling or move writes onto the trading path.

Use **1 KB per sample** for planning: 3.6 MB/open hour, 86.4 MB/continuous day,
31.54 GB/continuous position-year, plus event samples, position metadata and fill
revisions. At 8 hours/day for 252 days this is about 7.26 GB/year/position. Multiply
by concurrently open positions/accounts. Short trades add more lifecycle overhead.
These totals count time with an open position, not time the app is running.

WAL autocheckpoint is 1,000 pages (normally about 4 MB), not a hard size cap.
Long readers/backups can retain a larger WAL. Allow at least **1 GB WAL headroom**
in addition to the 10 GB free-space floor, the current database size for snapshot
staging, backup transport/scratch capacity and other archive growth. Monitor the
actual database and `-wal` sizes through status, disk usage and backup receipts.

A read-only SSH check around **00:04 ET November 11** confirmed:

| Item | Observed value |
| --- | --- |
| Host | peteamdpc / Windows `DESKTOP-6EUPDU1` |
| Configured cache backup source | `D:/TopSignal/data/databento` (exists) |
| Volume | D:, `Storage`, 2,000,396,742,656 bytes |
| Device | SATA `ST2000DM006-2DM164`, 2,000,398,934,016 bytes |
| Actual free space at inspection | **1,094,626,144,256 bytes**, approximately 1.09 TB |
| New progress database | Not present |
| Legacy excursion store at this cache root | Not present |

Those values describe the pre-deployment inspection. Activation subsequently
verified the deployed API's cache root and service permissions on that same D:
volume. Post-deployment free space was **1,094,932,373,504 bytes** at 06:42:52 UTC;
the new database was 69,632 bytes with no position samples while the account was
flat. Available space changes with other recorders and backups.

## Activation on peteamdpc

The November 11 enrollment covered only practice account `29593203`. These steps
describe separately authorized activation or recovery, not read-only diagnostics.

1. Deploy an authorized, tested release. Preserve production strategy/risk configuration. The API
   will create no recorder subscriptions until an account is explicitly enrolled.
2. Ensure the API's existing deployment environment sets
   `TOPSIGNAL_DATABENTO_CACHE_DIR=D:/TopSignal/data/databento`, with persistent
   storage and write permission for the API service identity. Use the same cache
   root in the existing backup configuration. No new database migration is needed.
3. Inspect the actual destination and free space on the host:

   ```powershell
   Get-Volume -DriveLetter D | Select-Object DriveLetter,FileSystemLabel,Size,SizeRemaining
   Get-Partition -DriveLetter D | Get-Disk | Select-Object Number,FriendlyName,BusType,Size
   ```

   From the deployed release root, using its Python/environment, first run the
   report, then register the verified destination. The Windows host name differs
   from the SSH alias `peteamdpc`:

   ```powershell
   & backend/.venv/Scripts/python.exe backend/tools/trade_progress_storage.py
   & backend/.venv/Scripts/python.exe backend/tools/trade_progress_storage.py --verify-host DESKTOP-6EUPDU1 --verify-root D:/TopSignal/data/databento --verify-drive D:/
   ```

   Registration writes only local feature metadata. It does not enroll accounts.
   A mismatch or less than 10 GB free rejects activation. Repeat under the actual
   service environment if the command reports an unexpected root or host.
4. On **Bot → Overview**, find **Trade recorder · 1 second** directly below the
   TopBot heading. Select the intended connected account and choose **Enable
   recording** (the same controls remain on **Trades**). This calls the ownership-checked local enrollment
   endpoint. Wait for **Recording** and a recent heartbeat; inspect failures before
   claiming coverage. Manual positions are included without arming a bot. Enrolling
   during an existing position starts partial coverage, not a retroactive path.
5. After naturally occurring activity, choose **Review trade paths** on Bot, then
   **Review path** beside a recent trade to open its chart without leaving Bot.
   The list loads the latest 20 account trades on demand; reopening it refreshes
   the list. **All trades** opens the account-scoped Trades page for older trades.
   Switching accounts closes the previous account's list and review. Confirm
   contract, basis changes, mark age and gaps. Do not place a test trade solely to
   validate the recorder. The backend can be checked while flat via its health
   endpoint and independent recorder heartbeat.
6. Confirm a subsequent **off-device** backup manifest includes
   `trade-progress-v1/progress.sqlite3`, has passed checksum and SQLite integrity
   verification on the receiver, and can be opened read-only after restore.

## Backup and recovery

`backup_market_cache.py` recursively includes this database as an SQLite member.
Its existing online backup API takes a consistent snapshot including committed
WAL pages; raw `-wal`/`-shm` files are excluded from copying. The ephemeral
`trade-progress-v1/progress.lock` is also excluded: Windows prevents reads while
the manager holds it, and it contains no recording data. A restored recorder
creates its own OS lock. Deploy the matching `backup_market_cache.py` helper
before enrollment so scheduled backups retain all recordings without trying to
copy the active lock. The existing
`run_market_archive_backup.py` schedule pulls the cache to the development PC and
verifies hashes and database integrity. No new schedule or destination is needed.
At the November 13 check this schedule was disabled; the last full verified copy
was November 4. The November 13 focused restore includes 33,822 progress samples
but is not full-cache coverage. Do not describe a configured schedule as an
operating daily backup. The development PC and private SSH connection must be
available. Production staging on D: is not an off-device backup.

Each SQLite snapshot is consistent individually; it is not one transaction with
the separate execution database. Restore the verified file into a separate review
directory first. Before replacing any production recording database, stop its
recorder, preserve the originals and obtain explicit authorization. Do not copy a
live `.sqlite3` file without the SQLite backup API. Reverify the destination after
host/drive changes. Enrollment in a restored database is retained; recovery gaps
remain visible. Restoring does not recover observations that were never recorded.

## Validation

- Deployed release `46afcd9`: all 3,280 offline backend tests passed (20 skipped,
  plus 8 subtests), all 1,288 frontend tests passed, and lint, 47 deployment script
  tests, dependency audits and the production build passed. All six exact-commit
  CI jobs succeeded; the deployment record describes the initial CI retries.
- Production verification passed frontend/API/worker readiness, flat broker
  checks, immutable release checks and existing recorder continuity. Twenty-five
  healthy progress status observations over 121 seconds confirmed enrollment,
  advancing heartbeats, no recorder error and a one-second interval. A consistent
  snapshot was copied off-device and opened read-only after checksum and SQLite
  integrity verification. No live trade was opened for testing.
- Final connection hardening passed 241 focused hub/progress/route/order-book/client
  checks, including protocol pings, failed sends, cancellation and bounded token recovery.
- November 11 one-second update: 34 focused backend tests and 7 frontend chart
  tests passed; frontend build and lint passed. Cadence checks cover one sample
  per second, duplicate worker wakeups, subsecond extrema, stopping at exit,
  delayed sampling gaps without backfill, restart handling and pagination.
- Initial implementation: 129 focused backend tests passed: new state-machine/API tests plus existing
  tracker, hub, excursion, runtime isolation and SQLite backup tests.
- Tests cover long/short sizing, scaling, partial/final exits, reversal, cadence,
  between-sample extremes, missing/stale data, duplicate/out-of-order events,
  correction/void ambiguity, account/owner isolation, restart/connection gaps,
  stale snapshot races, atomic write failure, bounded callback queues, actual
  free-space gating and a live-WAL backup/restore.
- Feature tables are absent from ORM metadata and denied by the mirror. Existing
  PostgreSQL mirror integration tests require a disposable PostgreSQL URL; 10 were
  skipped because it was not configured. No production DB was used for tests.
- Frontend build, lint and 9 focused chart/account tests pass. Browser inspection
  used synthetic fixtures at desktop and 390 px width, including missing-data
  and expanded position-event states. The narrow layout was corrected to preserve
  readable axes. Temporary preview files/server/tab were removed afterward.
- No broker orders, strategy/risk parameters, retention or historical paths were
  changed by the initial recorder deployment. Bot run 17 was stopped normally
  while flat. The later authorized fixed-bracket deployment started run 18;
  progress recording operates independently of bot arm state.
