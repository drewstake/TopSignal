# TopBot local market archive

## October 29 local reliability patch — not deployed

Optional quotes now have a dedicated background consumer and an independent
`optional-quotes.sqlite3` WAL/FULL spool beside the archive. The critical-event
spool and trading evaluation loop do not execute quote retries. Accepted quote
samples carry a stable process/receipt identity across transport retries, including
samples lacking a provider timestamp. Archive-commit/acknowledgment crashes dedupe
on restart. Both spools are copied before the archive during an online backup.

Limits are 4,096 memory records, 4 KiB encoded per record (plus Python object
overhead), 256 characters per provider string field, 4,096 durable pending rows,
32 MiB database pages and a 34 MiB physical spool budget including WAL overhead.
New quote growth stops at 32 MiB of physical files, retaining maintenance
headroom. Maintenance stops at 33 MiB, reserving the last MiB for its bounded
transaction. Before refusing either, a checkpoint with the same short SQLite
timeout can reclaim WAL after readers release their snapshots. A still-pinned
reader stops growth; it never triggers deletion of archive evidence.
There is a 512 MiB free-space floor and optional quotes stop adding archive rows
when the archive files reach 8 GiB. Existing archive evidence is never purged.
Each pass handles at most 128 memory records and 16 persisted records per owner
(fewer with larger SQLite pages to preserve the maintenance write budget).
SQLite waits are 50 ms for the spool and 250 ms for the archive. Shutdown waits at
most five seconds, preserves durable backlog and counts/logs volatile loss or
unresolved shutdown exposure; it does not synchronously flush an unbounded queue.

Only SQLite BUSY/LOCKED and explicitly transient OS error codes retry. Unknown
OperationalError, read-only, disk-full and other permanent failures are not called
lock contention. Retry backoff is 2/4/8/16/30 seconds, at most five attempts and
120 seconds in each memory/durable stage. Attempt counters commit before the
archive call. Before disposing an expired, exhausted or permanently failed batch,
the spool checks its stable IDs against the archive in a bounded read. Confirmed
presence counts as persisted/recovered, including a successful last attempt whose
acknowledgment was interrupted. Confirmed absence counts as a terminal drop.
If archive presence cannot be checked, disposal increments a separate persistent
uncertain-outcome counter, not a persistence or loss counter. This terminal
uncertainty does not automatically resolve later. Only transport rows are removed,
never historical archive data. If storage itself
cannot commit counters, errors and pending/unavailable status remain visible.

Diagnostics contain bounded exception type, SQLite code, errno, fixed category,
batch size and retry disposition, never arbitrary exception strings or provider
payloads. Optional-error logs and cumulative memory-loss summaries are limited
to one per 30 seconds per scope, with a final shutdown summary. Received, sampled
out, queued, memory retry/loss and backlog counts are process-scoped. Durably
queued, persisted acknowledgments, inserted rows, retries, terminal drops and
uncertain outcomes are
persistent spool counters. Persisted acknowledgments include deduplicated archive
rows; they are not a claim that every receipt is a unique quote. The UI keeps
process counts and durable totals separate. Process counters reset on restart.

An isolated writer lock reproduced the old remove-before-write loss. The retained
131 historical errors contain only `OperationalError`, so their actual causes
remain unknown and those missing samples cannot be recovered by this patch.
Crashes before durable admission, undelivered network events, sampling and terminal
drops remain loss windows. There is no claim of lossless quote recording.

The deployment descriptions below are historical; they do not describe activation
of this local patch.

Current deployment: **20261028-archive-15** on peteamdpc, verified October 28,
2026. Independent MNQ collection, critical-event spooling, and recurring off-device
backups are active. The final verification and rollback details are below.

The archive extends the existing lease-owning worker and ProjectX account hub.
It does not arm a bot, route orders, retrain, change stop sizing, tune thresholds,
or promote a model. `TOPSIGNAL_MARKET_ARCHIVE_ENABLED=true` enables decision
recording. Independent collection additionally requires
`TOPSIGNAL_MARKET_COLLECTION_ENABLED=true` and `TOPSIGNAL_MARKET_COLLECTION_SCOPES`,
a JSON array of explicit `{id, owner, account, contracts}` scopes. Initially the
single scope is `mnq-primary`, the existing credential owner/account, and delivery
`CON.F.US.MNQ.Z26`. Each owner has one account and one MNQ delivery contract.
Account ownership is rechecked against the existing account store; credentials
come only from the existing user-scoped ProjectX client factory. Invalid scopes
fail closed. Restart the existing service to change environment settings.

Stopping/disabling a BotRun or BotConfig does not stop independent collection.
The existing `TOPSIGNAL_BOT_WORKER_ENABLED` coordinator and host service must
remain enabled: its single lease schedules read-only collection and owns the
reused hub. No second trading worker is started. Turning off the independent
collection flag removes archive-only subscriptions, but does not disable ordinary
live-account subscriptions or exact decision recording. The collection scope
cannot arm runs or authorize an order. A stable scope ID with a changed delivery
contract records an explicit roll and keeps separate histories; it never rolls
automatically. Verify provider entitlement for the chosen account/contract.

## Files and evidence

Everything new is below `TOPSIGNAL_DATABENTO_CACHE_DIR/market-archive/v1/` in an
owner-hashed directory containing `archive.sqlite3`. SQLite is a **local file**,
with WAL, FULL synchronous transactions, a 250 ms busy limit, schema version,
calendar implementation hash, compressed payloads and indexed identities. No
archive ORM model, PostgreSQL migration or Supabase writer was added. The existing
small operational candle cache and account/trade stores retain their behavior.
Databento source imports still use `backend/tools/build_databento_cache.py`; the
SQLite-only legacy relational import guard remains unchanged.

Provider-native candles retain delivery contract, provider, timeframe, UTC bar
open timestamp, receipt timestamp, requested live environment and unverified
latency status. `live=False` does not prove a feed is delayed. Partial 1m bars are
excluded. The collector allows two minutes for delivery, overlaps 20 minutes,
and initially requests seven days of history in daily chunks. Later restarts
resume the persistent cursor rather than silently dropping older downtime.
At most two History requests run per minute: recent overlap and one catch-up or
gap-recovery window. No synthetic zero-volume candles are inserted.

Content-addressed versions never change. An append-only transition log preserves
corrections, including a later return to an earlier value. Identical retries do
not create new versions. Heads are only a current-version index. Decisions store
the exact ordered five-minute inputs, their original receipt/revision metadata,
forecast, model hash, configuration snapshot/hash, reason, risk codes, correlation,
run/account/decision/order IDs and existing forward-ledger outcome reference.
Matching model artifacts are copied into the archive by content hash. An artifact
that changed before capture is explicitly unavailable, never substituted.

Inputs are staged durably before the application transaction commits. Confirmation
is recorded after commit. Restart recovery checks database decision ID **and**
correlation ID. Staged evidence whose transaction rolled back remains unconfirmed;
it is not counted as a confirmed decision. The existing forward ledger remains
authoritative for its hypothetical forecasts and simulated paper account.
Earlier decisions without archived inputs are not reconstructed retroactively.

Reconstruct exact evidence, without starting the app:

```powershell
python backend/tools/market_archive.py --root D:/TopSignal/data/databento --owner <owner-id> --decision <decision-id>
```

## Sessions, gaps and rolls

The existing CME equity calendar defines 18:00 America/New_York session starts,
17:00 normal closes, maintenance/weekend closures, DST and early closes. Its
implementation hash accompanies derived bars and coverage. It contains recurring
holiday rules and audited historical exceptions, not a guarantee of every future
exchange notice. Review [CME's published schedules](https://www.cmegroup.com/trading-hours.html)
before future holiday exceptions. Calendar changes create new derived revisions.

5m, 15m, 1h, 4h and daily bars use complete expected 1m buckets anchored at the
session open. Final short buckets end at the scheduled close (a normal final 4h
bucket has three hours). Every derived bar carries its minute-input IDs and is
marked `derived_1m`; it never substitutes for provider-native decision candles.
No delivery contracts or subscription environments are spliced. A configuration
delivery change records a roll event and starts a separate stream; existing
TopBot run-disarm rules still govern trading. The collector does not choose rolls.

Coverage distinguishes scheduled closed minutes, observed minutes, unexpected
minutes and missing expected ranges. Empty provider responses are not closures.
Missing minutes could reflect provider omission/no trades, feed delay or outage;
the archive does not infer their cause. Each affected session receives at most
three historical recovery attempts, 15 minutes apart, with durable attempt counts.
Unresolved gaps remain visible indefinitely. Corrections beyond the 20-minute
overlap are observed when that session is fetched again; there is no claim that
the provider exposes every historical correction automatically.

## Quotes, executions and limits

The [ProjectX realtime API](https://gateway.docs.projectx.com/docs/realtime/)
supports `GatewayQuote`, `GatewayUserOrder` and `GatewayUserTrade`. Existing hub
authentication, reconnects, account scope and subscriptions are reused. The archive
adds only contract quotes, samples at most once every five seconds, and captures
broker order/trade/position/account events. It enables neither depth nor market
trade ticks. Subscription support is not entitlement proof; actual receipts in
status establish observed availability. Provider timestamps and local receipt
timestamps are distinct. Missing/crossed BBO is marked unusable.

Broker event IDs, order IDs, contract and account identity support exact joins to
decision order-attempt references. Account simulation classification is separate
broker evidence: a broker-reported practice fill is not a real-money execution,
and neither is a TopBot simulated paper fill. Do not infer fills from quote prices,
position disappearance or hypothetical outcomes. A 15-minute bounded read-only
order/trade history sweep recovers available reported states/fills after restarts;
it cannot restore intermediate order events. Connection changes are archived.

Critical orders, fills, positions, account classification and connection changes
first commit to `critical-spool.sqlite3` beside the archive (SQLite WAL/FULL).
Receipt timestamp, process ID, process receipt sequence, durable timestamp and
persistent spool sequence distinguish arrival order from durable admission order.
Provider timestamps remain unchanged inside the payload; connection timestamps
are generated locally. Identity hashes exclude receipt/capture metadata, so an
archive commit followed by a crash before acknowledgement safely deduplicates on
restart. Corrections with different provider content retain separate identities.
Only acknowledged transport duplicates are removed from the spool; their original
evidence remains permanently in `archive.sqlite3`.

Spool writes allow 50 ms of SQLite lock waiting on the hub thread, not the trading
loop. If they fail, a separate queue holds up to 1,024 critical records, each at
most 64 KiB; this volatile fallback retries every five seconds. Overflow or
oversize records are explicitly unrecoverable, counted in process health and
logged to the existing runtime logs. A crash before spool commit can lose such
memory records. Network events never delivered to the callback cannot be counted.
Do not equate received, memory queued, durably queued, archived, or recovered.
Process counters reset on restart; durable spool counters and runtime logs remain.

The spool permits at most 100,000 pending records / 256 MiB of used SQLite pages
(plus bounded WAL/page overhead). Free space must remain above 512 MiB. It flushes
up to 128 in spool order, retains failed batches, and backs off exponentially to
five minutes without a retry-count discard threshold. Repeated errors stay visible.
Acknowledged pages are reused. Pending durable records survive service shutdown.
The existing lease loop resumes draining at startup, even without new hub traffic.
If the whole cache is unavailable, both archive and spool fail: this is not a
lossless system. Broker-history reconciliation cannot recover every intermediate
state. Sampled quotes use a separate 4,096-record queue / 256-record batches;
quote loss cannot evict critical evidence. Trading continues on recording errors.

## Status, size and retention

Forward Results includes a concise local-archive panel: minute coverage/gaps,
last write, file bytes/free disk, quote receipts and recording errors. The same
authenticated owner scope applies. Full local status is available via the CLI
above without `--decision`; it also includes counters, collection cursors and
broker recovery. The CLI's `enabled` field reflects its invoking environment;
inspect service health/configuration to establish whether the running collector
is enabled. Recent writes alone do not prove complete coverage. Logs remain
the durable record of errors across process restarts.

A synthetic full 1,380-minute session measured 2,306,048 bytes for 1m and derived
bars, 888,832 additional bytes for 276 decisions with 25 inputs each, and 8,515,584
bytes for 16,560 five-second quote samples. At 250 sessions/year: **0.58 GB candles
and derived bars + 0.22 GB decisions + 2.13 GB quotes**, decimal, before extra
corrections, model versions and broker events. Plan at least twice this amount.
Peak traced Python allocations in that workload were 3.1 MB; this excludes the
existing process, provider transport and SQLite native caches. Reproduce using
`backend/tools/benchmark_market_archive.py --output <new-directory>`.

Finalized candles, every revision, model evidence, decisions and broker events
are retained indefinitely. Sampled quotes are also retained until an explicitly
approved, checksum-verified off-device archival policy is adopted. There is no
automatic purge or filesystem move. Estimate events separately: approximately
0.5–1.5 KB per event including indexes, multiplied by actual lifecycle event
counts. At 30 trades/session and 20 events/trade, allow 75–225 MB/year. Actual
observed density and corrections determine growth; check status regularly.

## Deployment and rollback procedure

1. Discover the running release from `TopSignal Personal Device`, inspect its
   manifest/process/configuration, verify both execution gates remain false,
   compare model hash/implementation hash, and recheck C:/D: capacity. Do not use
   an older deployment note as the release selector.
2. Build an isolated immutable copy of that exact release plus only reviewed
   archive changes. Run archive, worker, execution-safety, hub and existing
   forward-result tests against that copy, then its frontend tests/build. No
   schema migration or model fitting is required.
3. Stage the new release with the established service ACLs. Export the existing
   task and private environment/configuration; preserve the old release. Take
   SQLite online backups and checksum all other cache files with
   `backend/tools/backup_market_cache.py --source <old-cache> --output <new-staging>`.
   Copy the completed backup off the host and run `--verify <copied-directory>`.
4. Stop the existing supervisor using its `stop-production.ps1`; verify listeners
   and worker stopped. Take a final consistent backup and verify it off-device.
   Current upgrades retain `D:/TopSignal/data/databento` directly; do not copy the
   frozen C: data over it. Only an explicitly needed first migration should copy
   a cache into a new D: directory and verify all hashes/SQLite integrity before
   switching paths. Preserve the original and every backup.
5. Grant only SYSTEM/Administrators full control and `TopSignalSvc` modify on
   the private D: data root; harden the release read/execute with `.env` read-only
   for that service. Set the new release's cache root to D: and archive flag true.
   Retain the prior strategy/model/risk settings and both disabled execution gates.
   Reinstall/start the same hosting task, not another worker.
6. Verify healthy existing run, unchanged config/model, increasing on-disk minute
   coverage, exact next-decision reconstruction, BBO receipts (or honest absence),
   and no new historical cloud writes. Watch a subsequent worker decision and
   inspect recording errors. Local tests or a healthy HTTP listener alone do not
   establish active collection.
7. For rollback, stop the new supervisor and retain/snapshot the entire D: cache.
   Use the prior immutable release with its environment pointing to the
   **current D: cache**, the same model and disabled live gates. For the current
   one-step rollback to release 14, independent collection and spooling remain
   enabled. If intentionally disabling archival, turn both archive and independent
   collection flags off; retain the pending spool for later recovery.
   This preserves access to forward results accrued since deployment. Restore
   the same task identity and start once. Do not overwrite D: with an old ledger
   or point an active run back at the frozen C: ledger. Archive files can remain
   dormant. If a storage-path failure requires another location, copy/verify the
   latest data first; never silently restore over new evidence.

## Recurring off-device backup

`backend/tools/run_market_archive_backup.py` pulls over the existing private SSH
connection into the already authorized development-PC destination
`C:/Users/drews/.ssh/topbot-archive-20261028`. The scheduled job runs on the
**development PC**, not peteamdpc. It needs that computer powered on, its task
identity/key available, and private SSH connectivity to peteamdpc. Host collection
continues independently when that computer is offline. A host/drive failure before
the next successful backup can lose unbacked observations.

Each run uses `backup_market_cache.py` on the current D: cache for online SQLite
snapshots and checksum-verified files, including models, ledgers, observation state,
spool and any original source archives present. Staging is under
`D:/TopSignal/archive-backup-staging`; it is **not** an off-device backup.
Destination copies go to dated `backups/` folders, with full hash and SQLite
integrity verification. Spools are snapshotted before archive databases: an event
committed/acknowledged between file snapshots remains in at least one copy.
SQLite snapshots are individually consistent; the whole directory is not a single
cross-file transaction. Original decision inputs and model evidence are atomic
inside the archive database. At least weekly (and on the first run),
`check_market_archive_restore.py` restores to a new `restore-checks/` directory,
reconstructs the latest confirmed sampled decision per owner, verifies every
original input and the original embedded model against their hashes, and retains
the restored files and receipt. Missing model evidence fails the restore check.

The runner saves status/dated attempt receipts beside its private configuration,
then atomically publishes a small `market-archive/backup-status.json` on D:.
The panel shows last verified backup/restore and flags age above 36 hours. An
unreachable destination gets three attempts with 30/60-second retry delays;
subprocess timeouts also bound outages. If publishing to the host fails, the local
status records it and the host's last success ages visibly. Failed staging copies
are retained. No backup or original evidence is automatically deleted.

Both source staging and off-device copies have a 250 GB budget and 5 GB free-space
reserve; reaching it reports failure rather than deleting files. Daily full copies
of a growing ~3 GB/year archive could use roughly 0.5 TB during the first year
before weekly restore copies. Review capacity and an explicitly authorized
version-retention/incremental-backup plan well before the budget is reached.
Encryption at rest and an additional geographically separate copy remain operator
choices; existing SSH protects transit. Git/Supabase and another folder on D:
are not backups for this data. No paid service or new destination is configured.

Manual run on the development PC (private config contains paths, no feed secrets):
`python <installed-tools>/run_market_archive_backup.py --config <private-config> --restore-check`.
The final deployment receipt records the installed schedule and task identity.

## Production verification — October 28, 2026

Production collection is **active** on `peteamdpc`. The existing limited
`TopSignal Personal Device` task runs
`C:/ProgramData/TopSignal/releases/20261028-archive-13`; all 866 manifest files
were hash-verified. Configuration 32 / run 9 continues in dry-run, with both live
execution gates false. Configuration, model bytes/digest and the strategy
implementation digest match the previously running `20261028-uncapped-11`.
Volatility sizing, thresholds and all risk limits are unchanged. No model was
trained, tuned or promoted.

The configured cache is `D:/TopSignal/data/databento`. The archive is beneath
`market-archive/v1/<owner-sha256>/archive.sqlite3`. Existing models, observation
state and `forward-results/` were copied and verified; C: originals remain at
`C:/ProgramData/TopSignal/runtime/data/databento`. At 12:35 Eastern, D: had
1,418,635,374,592 bytes free of 2,000,396,742,656; C: had 84,912,422,912 bytes free.
No existing evidence was deleted or overwritten.

Host checks at **12:37 Eastern** established:

- Healthy backend, readiness and existing worker; live routing remains disabled.
- 8,014 distinct provider-native minutes for `CON.F.US.MNQ.Z26`, from October 20
  at 22:00 UTC through October 28 at 16:33 UTC. There were zero missing expected
  minutes **through the recorded 16:34 UTC cutoff**: five completed normal
  sessions and the elapsed current session. Weekends were separate zero-expected
  rows. This verifies that window against the repository calendar; it does not
  certify older data, future coverage or future calendar exceptions.
- 1,602 derived 5m, 534 derived 15m, 133 derived 1h, 34 derived 4h and five daily
  bars, separate from 27 provider-native five-minute decision revisions.
- Three committed HOLDs (2000–2002), each with 25 exact inputs and its preserved
  model. The strategy's last-21 revision references match the committed signal.
  All 1,132 pre-deployment forward evaluations retain their original
  decision/forecast fields; 1,135 evaluations were present afterward.
- 149 received BBO samples and six connection lifecycle events. Collection
  resumed after a controlled restart; decision 2002 was confirmed afterward.
  The archive used 14,036,992 bytes at that snapshot.
  An earlier off-device sample contained 100 paired BBO records and nine marked
  unusable; all 109 carried provider timestamps. Invalid pairs remain evidence,
  not usable execution quotes.
- A read-only broker check found zero orders, open orders and fills. Order/fill
  capture is implemented and regression-tested, but production has not yet
  supplied an execution event to demonstrate it. No test order was placed.
- Legacy `databento_ohlcv_1m` remained at zero estimated rows / 24,576 bytes and
  cloud `market_observations` at zero estimated rows. The existing operational
  candle cache added only five estimated rows while 8,014 minutes accumulated
  locally. Existing compact decision snapshots continued normally. No bulk
  history, quotes, replay arrays or new archive tables were written to Supabase.

Validation: 312 backend tests against the isolated release, 381 bot UI tests,
and the production frontend build passed. The final timing separation and
repeat-backup fix passed 59 targeted tests, including one new backup test
(313 distinct backend tests across validation). The final package contains the
verified deployed base plus archive changes, excluding unrelated development
changes. Its ZIP SHA-256 is
`cb54c077a99231bee5a0883d7178c62c713df952a37c6b0d70a1aa4155b056cc`.

Private evidence and off-device backups are on the development computer under
`C:/Users/drews/.ssh/topbot-archive-20261028/`. The original cache backup contains
3 files / 9,368,771 bytes; a subsequent live-cache backup contains 5 files /
23,357,638 bytes. Destination hashes and SQLite integrity checks passed.
Decisions 2000 and 2001 were reconstructed from that off-device copy with their
original input digests. Older backup manifests are retained under
`source-manifests/<sha256>.json` when backing up a restored cache.

Rollback is staged and ACL-hardened at
`C:/ProgramData/TopSignal/releases/20261028-archive-rollback-11`, using prior code,
the **current D: cache**, and disabled archive collection. The host helper
`C:/ProgramData/TopSignal/tools/rollback-archive13.ps1` stops the current
supervisor, verifies a fresh data snapshot and reinstalls the same task with
that rollback release. It never restores an old ledger. This rollback procedure
is prepared, not exercised against the running service. The backup utility is
also installed at `C:/ProgramData/TopSignal/tools/backup-market-cache-v2.py`.

Remaining limits: quotes are sampled, not lossless tick data; feed latency
classification remains unverified. Older corrections require a later fetch.
Collection depends on an enabled TopBot run. The verified off-device copies
above are one-time backups; an ongoing independent destination/schedule still
needs to be established.


## Follow-up deployment verification — October 28, 2026, 13:37 Eastern

The final service release is `C:/ProgramData/TopSignal/releases/20261028-archive-15`.
All 871 packaged files were checked against the release manifest on the host.
It uses the existing single `TopSignal Personal Device` task at Limited run level;
the actual backend listener process (PID 18492 at verification) belongs to
`TopSignalSvc`. `/health`, worker health and `/ready` passed. The independent
collector recorded `scope:mnq-primary` activity on the real service identity,
including new one-minute bars and fresh quote samples after restart. No browser
or second trading worker was started. The stopped/disabled-bot collection behavior
was exercised in isolated integration tests, not by changing production run 9.

At 17:35:49 UTC, the archive held 8,073 native one-minute bars, 15 confirmed exact
input decisions, 793 sampled quotes and 14 connection events. Expected-session
coverage had zero unresolved minutes from October 20 at 22:00 UTC through the
October 28 17:33 UTC cutoff, excluding scheduled closures. This is a verified
window, not a claim about all history or future coverage. Six critical connection
events since release 14 had committed to the spool and archive, with zero pending
or retry records. No actual order/fill events were available: read-only broker
checks at 17:36:35 UTC found zero orders, open orders and fills in the checked
account/window. Synthetic fault tests establish retry behavior; production did
not receive injected errors or test orders.

The final-release HOLD decision 2014 reconstructed all 25 exact inputs, matched
the committed signal's input revision references, and retained the original model
`c3003d754ac3ffb38cdaac95bf06e652d0f7b824c8f5e21ef46828635023de3b`.
The original 1,132 forward evaluations retained their immutable fields (1,147
current at that check). Configuration, strategy implementation hash, volatility
stop behavior and fitted model remained unchanged; both execution gates remain
false. The legacy Databento table remained empty and archive collection added no
historical Supabase writer. Existing small operational stores continue normally.

Validation passed 361 distinct backend checks across the full 358-test safety/
worker/hub/archive suite and three added regression checks, plus 382 bot UI tests
and the production frontend build. The final patch's 32 archive/backup tests were
rerun against its isolated release. Coverage includes abrupt process exit, the
archive-commit/spool-ack crash boundary, deduplication, corrected versions,
aggregation/session boundaries, gap recovery, delivery rolls, exact inputs,
write/destination failures, queue saturation, ownership and lease fences, stopped
and disabled bots, a concurrent spool-to-archive backup handoff, and first-file
schema initialization. An initial release-14 startup reader race was reproduced
and fixed; no new recording errors appeared in the checked release-15 startup log.

The development-PC scheduled task is `TopSignal Off-device Archive Backup`, daily
at **18:15 Eastern**, StartWhenAvailable, no overlapping runs, as `PC\drews`
using an Interactive principal and `pythonw.exe`. The PC must be powered on and
**drews signed in** (a locked session is sufficient). No dashboard is needed.
The task was manually triggered and completed with exit code 0 using the real
scheduler identity. Its next calendar run remains to be observed.

Installed backup tools are under
`C:/Users/drews/.ssh/topbot-archive-20261028/scheduled-tools-v14.1/`, with private
`scheduled-backup-config.json`, `scheduled-backup-status.json`, and dated
`backup-receipts/` alongside them. The host helper is
`C:/ProgramData/TopSignal/tools/backup-market-cache-v3.py`, SHA-256
`23c22daec2f612f14a274088fd76d6b20548b1d47f250ff78dcf32d18c76e486`.
It matches the final release's backup helper. Backups are in the existing
`backups/` destination and restore evidence in `restore-checks/`. A post-release
backup at 17:37:10 UTC verified 15 files / 24,156,722 bytes, and independently
restored decision 2014's original inputs and model. Checksums and SQLite integrity
passed at the destination; both host status and local receipts report success.
Original source archives are included if present under the cache; the inspected
production cache had no Databento source ZIPs. No old backup was deleted.

At 17:36 UTC D: had approximately 1.418 TB free of 2.000 TB, and C: 82.2 GB free.
The existing growth estimate remains roughly 3 GB/year for archive candles,
decisions and five-second quotes, plus broker events/model revisions; daily full
backup copies grow separately and stop at the documented capacity budget.
Critical capture is durable only after its spool commit. Volatile fallback and
network gaps remain loss windows, quotes are sampled, feed latency remains
unverified, and broker history cannot recover every intermediate transition.
Higher-frequency capture, late-session correction sweeps and additional market
subscriptions remain future work. No feed was purchased and no live gate enabled.

For rollback, first run the backup job with `--restore-check` and verify its
receipt. On peteamdpc execute
`powershell -NoProfile -File C:/ProgramData/TopSignal/tools/rollback-archive15.ps1`.
That prepared script checks the active release, stops the same service, takes a
new verified current-D snapshot, reinstalls release 14 under the same task/user,
and keeps the latest D: ledgers, archive and pending spool. It never restores an
old ledger over new evidence. The script is prepared but was not exercised as a
rollback; the previous releases and off-device copies remain available. If a
snapshot fails it retains STOP for inspection. Copy that rollback snapshot off
device or run the scheduled pull again before relying on it as a device backup.
