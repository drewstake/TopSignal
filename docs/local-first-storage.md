# Local-first storage migration

Deployed on peteamdpc as `20261103-local-execution-45` on November 3, 2026.
Local PostgreSQL is the execution authority; Supabase is fenced against stale
execution writers. See the [verified deployment evidence](audits/2026-11-02/local-first-storage.md).

## Storage boundaries

- Raw observations, ProjectX candles and decision research snapshots use
  `TOPSIGNAL_DATABENTO_CACHE_DIR/research-store-v1/research.sqlite3` when
  `TOPSIGNAL_RESEARCH_STORAGE=local`. The default `application` setting preserves
  the existing deployment until a verified cutover. Local SQLite uses WAL and
  FULL synchronous commits, retaining owner IDs, unique keys and query filters.
- Databento sources, Parquet/replay data, independent depth recordings, market
  archives and forward-result ledgers retain their existing filesystem layout.
- Execution state uses one PostgreSQL authority. Authentication remains separate
  and ownership, risk, order-idempotency and worker-lease checks still apply.
- Supabase remains the authentication provider, journal object-storage provider
  and asynchronous compact-record mirror. It is never an automatic execution
  fallback. A cloud outage can still prevent new sign-in/token refresh or access
  to remotely stored journal images.

On peteamdpc the preserved independent recorder currently loads its configuration
and provider credentials from the cloud read-only at process startup, then closes
the database connection. That is a separate remaining cloud dependency: an
already running recorder and a recorder restart during a cloud outage have
different recovery requirements. Do not claim recorder outage/restart independence
from main-application tests.

Research routing fails closed if the configured store is missing or incomplete.
Cross-store SQL joins and textual research queries are rejected. Explicit model
bindings select the correct candle-upsert dialect. Research retention does not
silently prune the migrated local evidence. Monitor D: capacity; cleanup needs a
verified backup and the appropriate authorization.

Local summary indexes cover the observation fields and decision metadata needed
by aggregates, keeping large JSON pages out of routine scans. A partial index
supports latest-spread lookup without sorting the raw history. Fresh restores
refresh SQLite planner statistics after import. Existing stores need an explicit
index migration; application startup does not perform DDL.

## Research migration and verification

`backend/tools/migrate_research_storage.py` exports the three tables using a
single read-only repeatable-read snapshot. It checks source/model columns,
records exact row counts, compressed-file hashes, full-content hashes and source
relation sizes. It never deletes cloud records or changes runtime settings.

Restore only into a new destination. Restore verification reads every local row
in primary-key order and requires an identical canonical content hash, including
decimals, UTC timestamps and JSON. It also runs SQLite integrity checks. Repeat
the restore on the off-device computer before activation. An initial online
snapshot is a rehearsal; stop application writers and repeat the final export
and verification for cutover. Keep the independent recorder running.

The existing `backup_market_cache.py` discovers the new `.sqlite3` file beneath
the cache root and uses SQLite's online backup API. The recurring archive pull
therefore includes the store without copying a live database file directly.

For WAL databases, the helper pins a read snapshot before incremental backup so
ongoing writers do not continually restart the copy. Writers can still commit;
the snapshot is released in `finally`. A fifteen-minute per-database progress
deadline bounds snapshot/WAL retention and fails the backup rather than calling
an incomplete copy verified. Non-WAL databases retain incremental read locking.
This behavior is covered by a test that commits through a separate connection
after every copy step, plus deadline cleanup and archive handoff tests.
SQLite documents the restart behavior of ordinary incremental copies in its
[online backup API](https://www.sqlite.org/c3ref/backup_finish.html).

Backup verification retains full `integrity_check` and all file checksums. Its
read-only SQLite connection requests a 128 MiB page cache, one database at a time,
to reduce repeated HDD reads while checking large indexes. This is an on-demand,
connection-local setting, not a database-file change; see SQLite's
[cache-size documentation](https://www.sqlite.org/pragma.html#pragma_cache_size).

## Execution migration and synchronization

`app/local_sync.py` provides explicit source/target installation and a
transactional outbox. This is migration tooling, not automatic startup DDL.
Application mutations and their outbox events commit together. A transaction
advisory lock in a deferred constraint trigger assigns event order at commit,
giving the queue an unambiguous transaction order. The ordering lock is not held
during broker I/O or ordinary account-lock acquisition. Existing account/lease
locks and unique order keys remain necessary. PostgreSQL can abort a conflicting
transaction; provider operations must never be retried blindly.

Each sync iteration applies one complete transaction and updates the cloud
receipt atomically, then acknowledges locally. A crash between acknowledgements
replays the transaction without applying it twice. Concurrent sync workers use a
separate lock. Outages leave the queue durable. There is no queue truncation or
automatic failover. Queue age, bytes and free SSD space require monitoring.

Cloud fences reject writes from old application identities, including new order
attempts, worker-lease acquisition and TRUNCATE. Only a separately provisioned
sync role may write the mirror. Never give that role application execution
credentials or run a worker with it. Fence every application table, and grant
the sync role only its explicitly mirrored tables and receipt metadata.

Cloud user/audit records are preserved on local deletion: sync records a logical
tombstone. A restore must apply tombstones before enabling execution. Physical
cloud cleanup remains a separate, explicitly approved action. Runtime leases,
research/cache tables, legacy Databento tables and migration metadata are not
replicated. New mirrored tables require an explicit registration/migration.

## Required activation gates

1. Record the actual release, task definitions, configuration fingerprint, active
   runs, recorder PIDs, disk media/free space and current cloud sizes.
2. Validate research restores on both devices; preserve immutable originals.
3. Stage PostgreSQL on the SSD, loopback only, SCRAM authentication, durable
   commits, checksums and a dedicated service identity. Keep application and
   synchronization roles separate from administration.
4. Verify encrypted off-device execution backups and restore them into a
   disposable database. Preserve the credential-encryption key separately.
5. Test outage/recovery, process restart, transaction replay, concurrent requests,
   uniqueness, stale-worker fencing and existing risk/tenant protections.
6. Perform read-only broker preflight. Gracefully stop the old application and
   its worker, verify no live process/lease or unresolved provider submission,
   then take the final snapshot. Preserve recorder processes and task definition.
7. Fence the cloud database before activating local execution. Validate exact
   counts/content, sequences, configuration, pending actions and order keys.
   Do not activate a copied stale lease. Run broker reconciliation before resume.
8. Verify local readiness, worker ownership, sync catch-up, unchanged bot
   configuration and recorder continuity after activation, including a soak.

## Recovery and rollback

Retain previous releases and backups. After local execution begins, a code
rollback must still use the current local PostgreSQL authority and current D:
store. Repointing at the cloud copy is a separate, fenced reverse migration,
requiring stopped writers, complete synchronization, count/content verification,
and broker reconciliation. An asynchronous mirror may lag and cannot safely
authorize another execution worker.

Execution backups must exclude the data in `topsignal_local.authority`. A
restored database without authority fails closed; restoring a backup must never
automatically resume trading. Recovery must reconcile uncertain orders using
the existing stable provider tags and idempotency keys before a fresh authority
is approved. Never overwrite an active database or restore stale outbox state
over newer execution records.

`tools/run_execution_backup.py` uses a dedicated read-only database role. It
encrypts the consistent PostgreSQL dump, credential key, runtime settings and
online sign-in SQLite snapshot. Authenticated checksums are verified before
atomically publishing an immutable directory under `execution-backups-v1` in
the cache. Incomplete backups stay outside the cache; nothing is pruned. The
existing off-device archive pull transports these with research and source data.
Publication is not off-device verification: check that pull's receipt and
periodically perform a full isolated PostgreSQL restore too.

The cache backup CLI uses Windows background resource scheduling to reduce
disk/memory contention with the recorder and local SQLite. CPU priority alone
does not control that contention. Monitor live response times during the first
full backup after adding a large store; retain existing verified snapshots if a
manual verification run must be stopped.

`run_market_archive_backup.py` accepts `remote_timeout_seconds` in its private
configuration, bounded from 120 to 7,200 seconds. Increase this deliberately for
background HDD work; the default remains 1,200 seconds. A timeout is a failed
attempt, never a verified backup. Keep task execution limits consistent with the
longer bounded copy/verification phases.

Keep the key separately on both devices with restricted ACLs. The host uses
machine-bound DPAPI for its noninteractive service; the development copy uses
user-bound DPAPI. Never put the unwrapped key in the cache, Git, logs or command
arguments. Restore checks include provider-credential decryption. Restore local
sign-in state consistently with its backed-up runtime configuration.

`tools/run_local_sync.py` uses private configuration for restricted source and
mirror connections, a status file and a dedicated STOP file. It reports pending
events, oldest event, queue bytes, last acknowledgement and sanitized retry
errors. Stopping sync must not stop trading or the recorder. Provisioning
functions require a transaction; authority activation is a separate step.

Schema changes require coordinated migrations on both databases. Incompatible
schemas or uniqueness conflicts, including conflicts with logically deleted
cloud rows, retain the queue and fail synchronization. Resolve them deliberately
without deleting user data or skipping events.

`tools/verify_local_mirror.py` reconciles every mirrored table using read-only
repeatable-read snapshots and full ordered row hashes/counts. It compares source
and target acknowledgement checkpoints before reading. The source snapshot must
have no pending events and its acknowledgement must equal the target snapshot's.
These fixed snapshots then represent the same committed prefix. Later writes do
not invalidate that proof: the receipt records subsequent checkpoint advancement
and queue size separately. A pending initial queue, unequal initial checkpoints
or changed authority is inconclusive. Content drift fails verification even when
the queue is empty. Target rows with active logical-deletion tombstones
are excluded without deleting them. Use the local read-only backup role and the
restricted target role; this verifier never locks application writes. Preserve
its receipt after cutover and after testing synchronization recovery.

`tools/provision_execution_storage.py` explicitly locks an offline restore before
DDL, replaces copied cloud fences only with a matching inactive local authority,
and permits only the five deliberately omitted Databento tables to be absent.
Its schema-ledger helper grants the restricted backend SELECT access through
restored RLS on migration/baseline metadata, without granting writes. Validate
readiness using the actual application role, not an administrator. Fresh restored
databases use UTC so timestamp serialization matches the cloud source.

The cache restore verifier checks the latest decision's recorded inputs plus
the most recent model-bearing decision and its exact original model. Explicitly
model-free rule-based BUY/SELL decisions are valid; a referenced missing model,
forecast without model identity, corrupt input, or changed file still fails.

PostgreSQL documents the potential lag/data-loss window of asynchronous copies:
[replication behavior](https://www.postgresql.org/docs/17/warm-standby.html).
Windows runtime provenance:
[PostgreSQL Windows downloads](https://www.postgresql.org/download/windows/) and
[EDB binary archives](https://www.enterprisedb.com/download-postgresql-binaries).
