# Local-first storage deployment — November 2–3, 2026

**Deployed and validated on peteamdpc. Local PostgreSQL owns execution; research
uses D:; Supabase receives asynchronous compact-record updates.** Times below
are UTC on November 3. No cloud execution fallback is permitted.

## Production state and preservation

Release `20261103-local-execution-45` started at approximately **06:31** on
`DESKTOP-6EUPDU1` (peteamdpc). It preserves actual deployment
`20261103-entry-limit-44`, including the other chat's runner-clock fix and the
user-requested `daily_entry_limit_enabled=false`. Configuration 39 and run 11
match the reviewed pre-cutover fingerprint exactly; other risk limits, enabled
state and order mode are unchanged. The account was verified simulated.
This migration issued no broker orders, cancellations or flatten requests.

The original Depth Recorder remained PID **12224**, started October 30 at
17:04:49 UTC. At 06:47:53 it was still recording with fresh durable events and
zero broker mutation calls. Its task and the Microstructure Features task match
their pre-cutover XML exactly. Main process PID **10840** survived validation.
Unrelated workspace changes and frontend assets were preserved; no repository
reset, revert or commit was performed.

| Data | Production location |
| --- | --- |
| Raw observations, candle cache, detailed research | `D:/TopSignal/data/databento/research-store-v1/research.sqlite3` |
| Databento sources, replay data, recorder/archive files | Existing layout under `TOPSIGNAL_DATABENTO_CACHE_DIR=D:/TopSignal/data/databento` |
| Execution state, leases, transactional outbox | PostgreSQL 17.11, `127.0.0.1:5433/topsignal`, C: ADATA SU800 SSD |
| Required compact records | Supabase asynchronous mirror, 30 eligible tables |

`TopSignalPostgreSQL` starts automatically under its restricted Windows service
identity. Checksums, fsync, synchronous commits and full-page writes are on;
timezone is UTC. Application, sync, administration and backup roles are separate.
PostgreSQL listens only on loopback.

## Measured Supabase savings

Research cutover fully restored these frozen rows on **both computers**, matching
canonical hashes, SHA-256, exact counts and SQLite integrity checks:

| Table | Frozen rows | Allocation before cleanup | After cleanup |
| --- | ---: | ---: | ---: |
| market_observations | 250,000 | 185,376,768 bytes | 40,960 bytes |
| projectx_market_candles | 188,759 | 125,337,600 bytes | 32,768 bytes |
| decision_research_snapshots | 3,261 | 54,091,776 bytes | 40,960 bytes |

After explicit approval, only those three table contents were truncated at
**03:29:19**, without CASCADE or sequence reset. Their fences remain.
Database allocation fell from **450,546,835 to 85,855,379 bytes**, reclaiming
**364,691,456 bytes (364.7 MB, about 81%)**. At 06:46:46 allocation was
**86,060,179 bytes** and the three cloud tables remained empty. This measures
allocation, not billing or quota-release timing.

Accounts, trades, journals, credentials, bot settings, order attempts and saved
results were retained. No other cloud deletion was authorized or performed.
Legacy Databento history was neither imported nor restored to PostgreSQL.

## Execution cutover and duplicate-execution protection

The user approved cutover once verified flat. Read-only checks confirmed no
positions, open orders, pending exits, uncertain submissions or unresolved
emergency actions before and after gracefully stopping only the main app at
about 06:04. Cloud execution writes were fenced at **06:04:55**.

The final frozen encrypted backup contained **35 tables / 10,540 rows**. All six
artifacts transferred with matching checksums. Fresh databases on both computers
were fully restored before activation: every ordered row hash/count, sequences
and provider-credential decryption matched. Final dump SHA-256:
`8960eb5a76d304acf218d0cff56274a530ff93c72d005184ca0cb69dcf354bb2`.

Local authority **`b15899c2-5a5b-4352-aa5d-162c600188eb`** activated at
**06:23:48**. Copied leases were cleared before activation; one fresh, healthy
local lease was verified afterward. All **41 cloud execution fences** remain
enabled. Old cloud order-attempt and lease writes are rejected, including
statements matching no rows. Auth, ownership, risk, idempotency and uncertain
submission reconciliation remain enforced.

The outbox commits transactionally with application changes. The independent
consumer acknowledges locally only after the remote transaction and receipt
commit. It never submits broker orders. Logical deletion tombstones preserve
cloud records; there is no automatic physical cloud deletion.

Provisioning compatibility corrections were validated:

- Only the five intentionally excluded legacy Databento definitions may be absent
  from a restore. Offline DDL locks tables before grants/policies to avoid
  autovacuum lock-order races; missing application tables still fail.
- Fresh restores use UTC, preserving timestamp serialization and configuration
  fingerprints.
- Restored ledger RLS hid existing rows from the restricted app role. SELECT-only
  policies expose 56 migration and 2 baseline records; writes remain forbidden.
  Readiness then passed without restarting the worker or changing data.

## Validation

- **335 focused tests** passed against isolated, manifest-verified candidate
  application code, covering preserved entry-limit behavior, runner/risk/order
  safety, research routing and backups. The current real-PostgreSQL suite passed
  **20 tests** after final corrections; cache backup/restore durability passed
  **27 tests**. These groups overlap and are not an additive unique-test count.
- PostgreSQL tests cover concurrent writers/consumers, atomic multi-table sync,
  rollback, remote failure before/after commit, replay after restart, precision,
  uniqueness, uncertain submissions, stale cloud writers and restored/inactive
  authority. Existing tenant/risk tests remain passing.
- The host PostgreSQL service restart preserved a restored rehearsal's hashes
  and durable settings. An isolated immediate-crash recovery preserved uncertain
  order/outbox state with no duplicate broker calls. Production was not crashed
  or restarted while exposed just to exercise a test.
- **Production sync outage:** stopped only sync gracefully, observed natural
  local commits, and ran an isolated consumer against an unreachable cloud port.
  Five events survived timeout unchanged; readiness stayed 200. Restarting normal
  sync drained the queue and advanced acknowledgement from 979 to 993. No test
  broker mutations occurred.
- **Full mirror verification at 06:45:48:** all **30 tables / 10,484 rows** matched
  at checkpoint 999, with zero mismatches/tombstones. Both repeatable-read
  snapshots represented the same acknowledged prefix while trading continued.
  Later acknowledgements reached 1011 on both sides and the queue was empty.
  Empty queue alone was not used as proof.
- Concurrent HTTP checks at **06:45:47** returned 200 for health, readiness,
  worker, authenticated runtime, local observations/research/cached candles.
  The three research requests took 1.047 / 0.406 / 0.078 seconds. Provider and
  lease were healthy, with no unresolved submission/emergency blocks.
  Unauthenticated access stayed **401**, unowned account access **404**, frontend
  **200**.
- At **06:46:46**, config/run fingerprints, authority, durable settings, sequences,
  one fresh lease, empty queue and cloud fences passed again. Recorder/task
  continuity passed at **06:47:53**.

## Backups and recovery evidence

Frozen research exports and the final pre-cutover execution backup remain on both
computers. Keys are held separately under restricted ACLs and DPAPI, never in Git
or plaintext cache files. Execution dumps exclude authority data; restoring one
cannot automatically resume trading.

The first scheduled live backup,
`execution-backups-v1/20261103T063054Z-6b2226ac65dc`, published at 06:31:11.
It contains **37 tables / 10,541 rows**, encrypted runtime settings, sign-in SQLite
and credential key. Full fresh restores on both computers matched all hashes and
counts, verified sequences and decrypted the provider credential row.
Dump SHA-256:
`a2c7412ea183b724ddf4b72001868f3f13b5d69c8caae48e6a80ba4e693f20e6`.

Complete cache archive **`20261103T045810Z-833c293f`** was reverified off device at
**06:41:31**: **10,616 files / 2,607,351,490 bytes**, including original sources.
File checksums and SQLite integrity checks passed for the snapshot and separate
restored tree. All **442,178 research rows** matched: 250,000 observations, 188,913
candles and 3,265 snapshots. Archived inputs and original model also verified.

The restore checker initially rejected a rule-based SELL explicitly marked
`no_model_used`. It now follows model-use metadata independently of direction.
Referenced missing/corrupt models and input/hash mismatches still fail; tests
cover model-free BUY/SELL and missing models. Completed evidence was reused and
verified. The redundant retry was stopped, retaining incomplete staging and all
originals.

Daily execution backup runs **17:45 Eastern**; the existing off-device pull runs
**18:15** with weekly full cache restore verification. It requires the development
computer powered on, `drews` signed in and private SSH available. Full PostgreSQL
restore checks must also be repeated periodically. No automatic pruning occurs;
backup age and free space remain operational checks.

HDD backup uses Windows background scheduling, pinned WAL snapshots, a 15-minute
per-database deadline and a 128 MiB connection-local integrity-check cache.
Heavy backup work temporarily caused observation-summary timeouts; execution
and readiness stayed healthy. Final concurrent checks passed after the redundant
retry stopped. Monitor API latency during full backups; an observation timeout
does not mean the backup completed or authorize overlapping retries.

## Rollback and remaining Supabase dependencies

Code rollback must retain the **current local PostgreSQL authority and D: store**.
Previous releases/backups are retained, but old cloud-configured releases must
not restart. Returning execution to Supabase requires a separate stopped-writer
reverse migration, complete sync, full verification and broker reconciliation.
Restored authority stays absent; reconcile uncertain orders before authorizing
a fresh one.

Supabase still supplies Supabase sign-in/token refresh, remote journal attachments
and compact-record mirroring. Trusted-device local sign-in was validated
separately. A cloud outage may affect cloud authentication and attachments while
local execution continues. The unchanged recorder reads configuration/provider
credentials from Supabase at startup, then disposes the connection. Its running
process was preserved; recorder restart still depends on that cloud lookup.

Host evidence: `C:/ProgramData/TopSignal/staging/local-first-20261102` and
`D:/TopSignal/local-first-migration`. Off-device evidence:
`C:/Users/drews/.ssh/topsignal-local-first-20261102` and
`C:/Users/drews/.ssh/topbot-archive-20261028`.

Key receipts: `approved-research-cloud-cleanup.json`, final execution restore
receipts, `execution-local-authority-active.json`,
`research-recurring-backup-verified.json`, both `execution-live-*-restore.json`,
`production-sync-recovery.json`, `production-mirror-post-recovery.json`,
`execution-http-verification.json`, `final-storage-proof.json` and
`recorder-continuity-final.json`.

See [architecture/recovery](../../local-first-storage.md) and
[storage policy](../../supabase-storage-policy.md). The migration follow-up is
paused after verified completion; normal worker, sync, recorder and scheduled
backup tasks remain enabled.

### Subsequent deployment handoff

After the storage checks ended at 06:47:53, the separate liquidity-fix chat
started its authorized release switch. Its first startup failed and its guarded
rollback restarted release 45 at 06:51:20. At 06:51:42, main PID 7204 was healthy,
readiness was ready, and sync had zero pending events at acknowledgement 1057.
The original recorder was still running then. Further liquidity deployment and
recorder updates belong to that separate task; inspect the actual current
deployment before using this dated storage evidence for future operations.
