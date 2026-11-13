# Storage maintenance — November 13, 2026

The owner authorized the prepared maintenance after the 4 p.m. New York stock
market close. MNQ futures continue through 5 p.m.; fresh broker checks, recovery
gates and existing execution safeguards still apply. This receipt records work
in progress and does not declare Supabase retired.

## Recovery and preparation

The original immutable candidate `053e0c6` passed all six CI jobs, 3,411 backend
tests, 1,291 frontend tests, 47 release-script tests, lint, dependency audits and
production build. Fourteen additional PostgreSQL integration checks passed.

At 21:02:58 UTC a new encrypted execution/auth backup was published at
`D:/TopSignal/data/databento/execution-backups-v1/20261113T210239Z-1107a76a71e6`.
Its off-device copy under
`C:/Users/drews/.ssh/topsignal-local-first-20261102/remediation-backups/`
restored at 21:05:45 UTC: 37 tables, 12,862 rows, verified hashes and credential
decryption, execution authority absent, and local authentication issuance,
restart and revocation with network calls blocked. The isolated database is
`topsignal_audit_maintenance_20261113_1`. This focused backup does not replace
the pending full-cache catch-up.

Seventeen configuration/task files, including the new dedicated recorder
credentials and prior cloud/helper environments, were encrypted into
`D:/TopSignal/data/databento/configuration-recovery-v1/20261113T210757Z`.
The off-device copy under the protected `configuration-recovery/` directory was
decrypted in memory and every original byte count/hash verified. Ciphertext
SHA-256: `892979453827d2184b837d6b3f7eaeb163b3bff05075c08c74c648fb2ce45b94`.
No old environment, recovery copy, cloud data or audit record was deleted.

The local `topsignal_depth_recorder` role was provisioned and verified: only
owned paper configuration 32 and its one encrypted ProjectX credential are
readable; foreign-owner rows are invisible; writes remain denied even with
the read-only session default disabled. All helper files are protected;
the limited service identity can read the private environment but cannot write it.

## Initial recorder switch and recovery

The 21:08:50 UTC broker gate found practice account 29593203 simulated/eligible,
zero positions/orders, zero unresolved attempts and no open research windows.
Earlier research windows were allowed to finish instead of accepting gaps.

The recorder drained cleanly, sealing capture `839cd91d7dc747228386733572a4c0e4`
at durable index 19,452,222. The new helper was started at 21:09:17 UTC but failed
before market connection. A shared hub import loaded `StreamingPnlTracker`,
which imported application models/database initialization despite the independent
recorder's correctly isolated credential lookup.

The prior recorder task action was restored without forced termination and
restarted at 21:11:36 UTC. At 21:11:55 UTC PID 18808 was recording capture
`1f67d065ffe24c98ad678db9552773ad`, advancing durable data with zero errors,
zero reconnects and zero broker mutations. Preserve this maintenance gap; do
not present it as provider coverage or backfill observations.

The correction makes the hub's tracker reference a type-checking-only import.
A fresh-process regression reproduces the failure without any application
DATABASE_URL and proves the independent recording loop does not load `app.db`
or `app.models`. The 61 targeted recorder/hub checks pass. A new immutable
candidate and exact-commit CI are required before a second switch; the failed
helper directory and its receipts remain intact.

The application remains release `20261112-auto-signin-87`. No application
restart, fill migration, mirror retirement or enrollment change has occurred
at this checkpoint. The dedicated local role is prepared, while the restored
recorder still uses its prior startup configuration.

## Subsequent cutover and resource-pressure finding

The corrected immutable commit `8adcbb3` passed all six CI jobs, 3,412 backend
checks, 1,291 frontend checks and 47 deployment checks. A limited-service startup
probe invoked the real recorder loop with application DATABASE_URL absent and
broker calls prohibited, successfully reading/decrypting its one local credential
without importing application models or the database initializer. The recorder
then changed cleanly to the local-only helper at 21:22:15 UTC, PID 17264, capture
`1624b5417a294a7e8d4775dd2727e306`.

Both local and mirror migration ledgers now contain the 57 checked migrations.
The fill-leg partial index was applied without modifying trade rows. The cloud
ledger itself was fenced: its existing dedicated mirror identity inserted the
exact ledger row inside a bounded transaction using temporary, narrowly scoped
membership/INSERT rights. Those temporary rights were removed in the same
transaction and the original memberships, all 41 cloud fences and all 4,681
trade content hashes were verified unchanged. Full ordered mirror comparison
passed afterward. No fence was disabled and no cloud data was deleted.

The previous release cannot start against the new ledger: its checked migration
runner rejects an unknown future migration. Thus the earlier proposed blind
rollback to release 87 is **not valid even before mirror retirement**. A stopped-
service cutover retained source/dependency/ACL/CI/backup/broker gates, and started
candidate `20261113-local-storage-90` at 21:39:18 UTC. Its initial readiness
verification timed out during recovery. Existing configuration 40 had practice
auto-resume enabled; following a fresh six-account flat/order-free check, the
normal stop API disabled configuration 40 and stopped run 18 with `manual_stop`.
The client timed out, but authoritative state confirmed the stop committed.
Every other configuration field, including strategy settings, was verified
unchanged. No order/flatten/rearm call was made.

Readiness recovered, but release acceptance remains incomplete. The independent
recorder retained its PID/capture and reported no raw rejected events or status
publication failures; connection errors increased and its metadata inventory
stalled. Nonblocking read-only Python stack samples at 21:49 UTC found the raw
recorder waiting in filesystem flush/inventory and the optional heatmap helper
walking archive metadata. Disk queue was 7–13 in three samples. This is evidence
of concurrent I/O pressure, not proof that every disconnect has that cause.

A further correction makes production heatmap admission check the independent
recorder's small protected status file, deferring on stale/malformed status,
stalled inventory or storage refusal. Its metadata scan now honors pause/stop
requests between entries and preserves verified manifest progress on resume.
It changes no strategy, recording budget, retention or authority setting. This
correction requires its own exact-commit CI/package before activation. Full-
cache backup, mirror retirement and account enrollment remain pending while
recording stability and this release are verified.

## Deployed local-only release and multi-account observation

Release `20261113-local-storage-91` (`c37975f`) passed all six exact-commit CI
jobs and immutable activation at 22:04:19 UTC. The independent recorder now
uses its restricted local credential role. Mirror retirement passed two full
ordered comparisons; at 22:07:04 UTC the receipt acknowledged sequence 175050,
disabled all 30 capture triggers, retained all 30 authority/serialization
triggers and all cloud fences, and left the outbox empty. The application
restarted successfully against the retired state. Both dedicated mirror logins
were changed to NOLOGIN after verifying zero connections. Old credentials and
configuration remain only in protected recovery material; cloud data is intact.

Passive policy enrollment covers exactly the six fresh, owned visible accounts:
29593203, 29671908, 29671939, 29671977, 29672001 and 29672024. The last account
classification had 820 other accounts excluded. Account 29672001 cannot trade
but remains eligible for passive recording. Config 40 stays disabled/run 18
stopped; no diagnostic trades or strategy/risk changes were made.

At 22:21 UTC the served UI verified practice selection, selector-driven switching
to 29672001 with a matching URL and flat recorder status, unavailable account
28278873 failing explicitly, and duplicate URL account parameters failing
explicitly. Journal is absent from navigation. The temporary verification tab
was closed. The browser also exposed pending history/backup warnings; those
remain acceptance work, not evidence of complete success.

Longer six-account observation exposed intermittent SQLite OperationalError
writes and three discarded queued events on 29672001, despite current healthy
heartbeats. The follow-up patch serializes in-process writers and retries the
same rolled-back pending batch for SQLite BUSY/LOCKED only, with four bounded
attempts. It retains the one-second SQLite busy timeout, queue/storage bounds,
full synchronous commits, and gap handling for unrecoverable failures. Tests
exercise six competing accounts, a real external write lock, no duplicate/lost
fills or observations, and bounded retries versus immediate disk-error failure.
Error diagnostics now retain sanitized SQLite codes without payloads/secrets.
This follow-up is prepared; it requires its own CI/package and activation.

The API and independent recorder tasks had inherited scheduler priority 7
(background I/O and memory). At 21:59 UTC only these two task priorities changed
to 4, and their verified running processes received normal CPU/I/O/memory
priority without restarting the recorder. The stalled inventory completed ten
seconds later; later inventories took 16 seconds. This establishes a contention
contributor, not proof that all provider disconnects had the same cause. The
installers now preserve priority 4; optional helpers remain background work.

The execution backup created 22:11:10 UTC has an off-device verified isolated
restore at 22:14:02 UTC: 38 tables, 12,924 rows, credential decryption and local
auth issuance/restart/revocation, with execution authority absent. The final
21-file encrypted configuration archive verified off device at 22:16:03 UTC.
A full-cache backup started 22:17 UTC at 8 MiB/s under the unchanged capacity
and deadlines; it is still in progress and does not yet replace the stale
November 4 full-cache receipt. The current Discord helper restarted with a
minimal local-only environment and reported `watching`, PID 15304, at 22:23 UTC.

## Follow-up history delivery findings

The first served history check on release 91 returned recent lower history in
7.718 seconds, the full-session lower view as bounded HTTP 503 at 20.032 seconds,
and the complete-session heatmap summary in 0.125 seconds (7 coverage intervals,
463 explicit quality/coverage gaps). No HTTP 500 occurred. Session order flow
still exceeded a 40-second client deadline: its separate reader performed a
fresh raw archive walk despite caching derived summaries.

A follow-up changes session-flow inventory to bounded, resumable incremental
metadata scans and rechecks selected manifest ownership/file identities before
using summaries. The selected lower-history path validates each run's ancestry
once per request and every child for reparse points and fresh identity, avoiding
repeated ancestor opens per segment. Compressed/uncompressed source checksums
remain mandatory on cache misses, and changed cached content stays rejected.
No storage budget or strategy changes are included. New tests cover interrupted
cold scans, warm reuse, changed ownership, child reparse metadata and bounded
processing with explicit pending coverage. These are prepared changes until a
new immutable release is accepted.

Candidate 92 was not packaged: its backend suite passed 3,425 tests plus eight
subtests, but one unrelated frontend CSV-import test timed out awaiting its
confirmation dialog. The unchanged six-test CSV-import file passed immediately
in isolation. The next complete candidate must pass all release checks anew;
the failed validation directory is retained.


## Release 93 and grouped-commit follow-up

Release `20261113-local-storage-93` (`9010312`) completed immutable activation at
22:41:56 UTC after all six CI jobs, 3,429 backend tests plus eight subtests,
1,291 frontend tests, 47 deployment checks, lint, audits and build passed. The
independent recorder retained its PID/capture. A fresh six-account provider read
at 22:42:32 UTC found zero positions/orders and all six currently trade-enabled;
29672001's provider flag changed since the earlier observation. Config 40 remains
disabled/run 18 stopped. No broker mutation was made.

Under the concurrent full-cache backup, recent lower history returned HTTP 200
in 1.828 seconds, but full-session requests returned bounded 503 at 20 seconds
on both attempts. The upper summary returned 200 in 17.812 seconds and session
flow returned bounded 503 in 12.047 seconds while retaining scan progress. The
125-second Future/500 failure is fixed; full-session delivery acceptance is not
complete. Profiling attributed 14.423 of 20.094 seconds to 539 manifest reads.

All six recorders were currently healthy/flat at 22:44 UTC, but bounded BUSY
retries still accumulated and 29671908 had one exhausted commit attempt. Separate
FULL-synchronous heartbeat commits compete on the HDD. The prepared next change
collects simultaneously waiting account batches for at most 50 ms and commits
them in one SQLite transaction. Owner/account boundaries and per-leg revisions
remain unchanged; pending batches clear only after a successful durable commit.
A rollback preserves every pending batch for bounded retry. Callback queues stay
bounded, and shutdown retains the manager lease until accepted writes finish.
Regression checks cover six accounts across two owners, shared broker leg IDs,
whole-group rollback/retry and shutdown draining. This is not deployed until its
own complete release and exact-commit CI pass.

The scheduled execution backup at 22:45 UTC successfully resolved release 93,
covering 38 tables and 13,078 rows. Its off-device restore is still pending.
The separate full-cache snapshot continues; the November 4 receipt remains the
last verified full-cache recovery until the new snapshot is restored off device.


## Full-cache pacing failure and verified focused recovery

The scheduled 22:45:06 UTC PostgreSQL/auth backup restored off device at
22:53:10 UTC: all 38 tables/13,078 rows and sequence/content checks matched,
one credential decrypted, authority was absent, and local auth issuance,
restart and revocation passed with network calls forbidden. Its dump SHA-256 is
`9c69a64d2e50dbcc3f9879db608adca608e7735a41cb6c3fed1c54fcdd6f1688`.

The full-cache snapshot failed at 22:56:10 UTC with `ValueError: sleep length
must be non-negative`. The pacing loop read its clock twice across a deadline,
allowing the second reading to produce a negative sleep. The failed stage
`20261113T221652Z-659db5fb` and failure receipt remain intact. After proving the
snapshot process had exited, only this chat's local backup runner was stopped
during its read-only estimate retry; no live SQLite snapshot or recorder was
terminated. The correction uses one remaining-time calculation per sleep and a
regression test advances the fake clock across that exact boundary. A new
immutable helper and verified full off-device restore are still required.

A warm lower-history profile after the failed snapshot exited completed the
same full session in 9.860 and 8.641 seconds (269,896 raw measurements, 47,865
plotted points, zero skipped segments). Its first cold attempt took 20.188
seconds and hit the bounded retry. These are direct helper measurements, not
served API acceptance under backup load. Session flow now returns partial HTTP
200 with explicit `building` status and pending segment counts; partial response
does not imply complete session coverage.


## Release 94, development-PC isolation and response serialization

Release `20261113-local-storage-94` (`8196fca`) completed activation at
23:00:50 UTC after exact-commit CI and full local validation. Its grouped writer
reported no BUSY retries, failed commits or discarded callbacks through the
23:16 backup-load check. Six flat-account heartbeats remained fresh. Its served
full-session lower history returned 200 in 18.672/17.782 seconds, with 269,896
raw measurements and zero skipped segments; upper summary returned in 1.032
seconds. These successful responses still left a delivery-performance problem.

The host blocked both directions over both configured development-PC Tailscale
addresses from 23:05:54 to 23:10:56 UTC (301.699 seconds). SSH probes from the
PC timed out. An autonomous host observer retained 67 samples, including 60
inside isolation: all six account heartbeats stayed below 1.116 seconds, local
API health/readiness stayed 200, and depth PID 17264/capture
`1624b5417a294a7e8d4775dd2727e306` persisted with errors unchanged at eight.
Depth status age stayed below 5.709 seconds. The market was closed and raw event
count did not advance during isolation, so this proves runtime independence and
readiness rather than new trade/depth-event capture. The ordinary test cleanup
restored access; both narrow firewall rules and both temporary tasks were removed
at 23:11:43 UTC. Scripts, rules, task XML and observations are preserved under
`independence-verification-20261113` on the host and `tmp/remediation-20261113/independence-receipts`
off device. The local connection task automatically reconnected afterward.

A fresh release-94 PostgreSQL/auth backup created 23:11:48 UTC restored off device
at 23:13:12 UTC, matching 38 tables/13,078 rows, credential decryption, absent
execution authority and network-blocked local auth issuance/restart/revocation.
Its dump SHA-256 is
`1c4071101020d8692605b5a6f72668dbca250033d807c756b4681be092682e96`.

Backup helper bundle 97 (`05348c8`) passed the complete local validation and all
six CI jobs (run 43524691401). The immutable `backup-remediation-v3` helper and
recovery-PC `scheduled-tools-v19` carry the pacing correction. Config v20 started
a fresh full-cache run at 23:14:15 UTC, retaining the 8 MiB/s cap, 250 GB capacity,
existing deadlines and every failed stage. Its 20,118,361,467-byte capacity
estimate completed in 26 seconds. The full-cache restore remains pending.
Candidate 95 failed an existing pagination fixture immediately after the 18:00
New York rollover; candidate 96 failed an archive fixture that assumed open
market minutes on Friday evening. The fixtures now use the requested trading-day
window and a fixed open-session collection time respectively; 177 affected tests
pass without changing production calendar or risk logic.

The actual served browser exposed status timeouts during full-history delivery.
A read-only live stack profile found `jsonable_encoder`/`serialize_response` on
the API main event loop. A 54,562,449-byte response took 17.25 seconds; it exceeded
the existing 24 MiB response cache and was immediately evicted, so repeated
requests still rebuilt and traversed the whole response. The prepared correction
encodes/compresses JSON in the synchronous worker, stores compressed bytes under
the same cache limit, and returns those bytes directly with negotiated gzip and
`Cache-Control: no-store`. The JSON schema, owner isolation, checksums, bounded
reader, point limits and history contents are unchanged. Regression tests keep
another API request responsive while history rendering is held, check gzip and
identity transport, and verify cache reuse for a response exceeding 24 MiB before
compression. This response correction is prepared until its own release passes
all checks and activation.
