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
