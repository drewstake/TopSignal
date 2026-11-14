# Storage maintenance — November 13, 2026

The owner authorized the prepared maintenance after the 4 p.m. New York stock
market close. MNQ futures continue through 5 p.m.; fresh broker checks, recovery
gates and existing execution safeguards still apply. The application fixes are
deployed; the current scope and deferred recovery/restart checks are recorded
below. This receipt does not declare all original acceptance work complete.

## Commit, push and deployment review — November 13, 23:03 New York

The owner subsequently requested finishing commit/push/deployment review without
the archive transfer. **Application release 99 is already deployed and healthy.**
Comparison of commit `2ba78b62f04bf67c4ec6036e71efcd8be9702c5b` with
`af4db13cb68eef3a61bad23bbbf8d71ce843cbb4` found no changes in `backend/app`,
`frontend`, `scripts`, `db`, or the root package manifests. Subsequent changes
are the separate cache-backup helper, its tests, and documentation. There is no
new dashboard/API/database change requiring another application restart.

Fresh served checks at 23:03 New York (04:03 UTC) returned HTTP 200 for health,
readiness and worker health. The dashboard HTML and its JavaScript/CSS bytes
exactly matched immutable release 99. Journal routes returned 404; recorder
status returned 200; the diagnostic session signed out and was rejected with
401 afterward. At 23:01 all six owned account recorders were healthy, with maximum
heartbeat age 1.322 seconds and zero commit failures, retries, discarded events
or queued observations. The application, independent recorder and Discord helper
remained running; no optional maintenance process was active.

The component inventory again verified loopback PostgreSQL configuration for
the application, recorder, Discord and execution backup, local trusted-device
authentication, no active cloud/Journal/Gemini settings, retired sync, an empty
outbox, disabled capture triggers and retained authority fences. No reboot test
was performed. The later backup helper is installed separately at protected v9
and passed 33 focused checks, the isolated Windows fixture and all six CI jobs
(43553484611); the archive job remains disabled and does not run this helper.

The existing release-99 activation receipt remains the deployment receipt,
including its completed broker, integrity, served-asset and recording gates.
No redundant package/activation or weakened recovery gate was used for this
documentation finish. The final commit retains the full maintenance chronology
and pause checkpoint below. Archive transfer/full restore, schedule enablement,
host-reboot acceptance and further session-flow warming remain deferred; none
is represented as a completed check or silently restarted by this request.

## Paused at the owner's request — November 14, 03:54 UTC

**Maintenance is paused, not complete. The development PC can be shut down;
peteamdpc must remain powered and connected for recording.** No host reboot,
new rollout, backup transfer, restore, cache warm-up or schedule enablement was
started after the pause request. The private dashboard connection will close
when the development PC shuts down; host recording does not depend on it.

At 03:56 UTC, the application, independent depth recorder and Discord helper
were still running. All six account recorders were healthy at 03:55:28 UTC,
with heartbeat ages below 1.91 seconds and zero new commit failures, BUSY retries,
discarded observations or queued work. Release 99 and its settings remain active.
The normal execution-backup task remains enabled/Ready. The cloud-sync task,
temporary reboot observer and off-device archive schedule remain disabled.
No optional maintenance backup, transport, fixture or warm-up process remained.

The completed **59,635-file / 16,014,080,224-byte** source snapshot is preserved at
`D:/TopSignal/archive-backup-staging/20261114T021916Z-9270ec34`. After verifying
the exact executable, command, PID and creation time, only the unfinished tar
writer (PID 14980) was terminated; its source snapshot had already completed at
03:40 UTC. The local maintenance driver (PID 35600) was stopped first to prevent
automatic retries or transfer. Its SSH child and the host writer/wrapper exited.
Application, recorder and trading processes were not stopped.

The partial tar is retained at
`C:/ProgramData/TopSignal/staging/archive-transports-20261113/20261114T021916Z-9270ec34.tar`:
636,866,048 bytes, SHA-256
`2130a4b773e0b00adabb4a0e3b2d35bc8930f10caf26646c8f181ee3fb24ae03`.
The completed source manifest remained unchanged (SHA-256
`68f74ad237dbe19c67b635839c2103cc43162efd69a8b2f58214b5653752162c`).
`packer-v8-paused.json` is beside the partial tar and copied to the development
checkout's `tmp/remediation-20261113/`. No source, failed snapshot or partial tar
was deleted. The new full snapshot is **not yet verified off device**; the last
full-cache off-device restore remains November 4. The newer focused
PostgreSQL/auth, configuration and Journal restore receipts below remain valid
for their narrower scopes.

The temporary pre-reboot diagnostic sign-in was revoked, its 401 rejection
verified, and only its private token file removed. Other sessions were untouched.
No reboot was requested; the host's observed boot remains October 29.

Resume only when the owner asks. The precise machine checkpoint is
`tmp/remediation-20261113/resume-99-state.json`. Resume work is:

1. Revalidate the completed snapshot, preserved partial and helper v9 source/CI
   (`af4db13cb68eef3a61bad23bbbf8d71ce843cbb4`, CI 43553484611 passed). Reuse
   that completed snapshot; do not restart its long source-copy phase.
2. Adapt the prepared `finish_continuation_backup_v9.py` to the actual
   **pause** receipt (`packer-v8-paused.json`). Its current guard expects the
   unused planned replacement receipt, so it must not be run unchanged. Pack to
   the separate `.v9.tar`, then transfer, verify every file/SQLite database,
   perform the isolated input/model restore and publish the verified receipt.
3. Enable the reviewed daily 17:10 New York pull using configuration v26, runner
   v25 and helper v9 only after full verification. The old weekly preparation is
   obsolete. Refresh PostgreSQL/auth off-device restore evidence for the two-hour
   reboot gate; the 00:41 backup is too old for that gate.
4. Update the reboot gate's old snapshot ID to `20261114T021916Z-9270ec34` and
   its protected source receipt, issue a fresh own diagnostic session, and obtain
   fresh all-account flat/order checks before the already-authorized unforced
   reboot. Verify automatic startup, auth persistence/revocation, all runtime
   components and served UI; then finish bounded session-flow derivation.
5. Update final acceptance documentation/PR and run required CI. Do not claim
   full Supabase retirement or complete recovery acceptance before these checks.

This chat's earlier Computer Use connection was released. A read-only cleanup
check found shared native helper PID 73792 (created 00:50 UTC); the recent-chat
inventory did not establish exclusive ownership, so it was preserved. This is
the standing cleanup workaround's shared-ownership limitation, not a guaranteed
fix for the application's capture-resource lifecycle bug.

## Current checkpoint — November 14, 02:54 UTC

The sections below preserve the maintenance chronology, including failed attempts;
their earlier release numbers and pending items are not the current runtime.

| Area | Verified state at this checkpoint |
| --- | --- |
| Application | Immutable release `20261113-local-storage-99`, commit `2ba78b62f04bf67c4ec6036e71efcd8be9702c5b`, active since November 13 23:46 UTC; all six exact-commit CI jobs passed |
| Account recording | Six owned accounts enrolled, healthy, with zero new commit failures, BUSY retries, discarded observations or queued events in the 02:52 check; historical losses remain recorded below |
| History and UI | Served account switching, explicit invalid links, Journal removal, upper heatmap and lower history verified; full-session response 11.985 s cold / 1.250 s warm under backup load |
| Runtime storage | Application, auth, recorder, Discord and execution backup use protected local stores; mirror retired, outbox empty, dedicated logins disabled, authority fences retained |
| Independence | Five-minute development-PC network isolation passed; actual host reboot/autostart verification remains pending |
| Recovery | Journal archive and final 36-member configuration archive verified off device; PostgreSQL/auth isolated restore passed at 00:44 UTC and must be refreshed for the reboot freshness gate |
| Full cache | Original attempt preserved after SQLite failure; continuation `20261114T021916Z-9270ec34` is running, with 22,347 raw files / 6,193,328,940 bytes staged at 02:54 UTC; off-device verification and isolated replay restore are still required |
| Backup helper | Protected v8 matches `781659c2c94c29b971020f14125b956a91dc0148`; 32 focused checks, six CI lanes and isolated host copy/transport verification passed; the active continuation remains pinned to v6 |
| Remaining acceptance | Complete full recovery verification, enable the reviewed pull schedule, refresh PostgreSQL/auth recovery, perform gated unforced reboot, verify automatic startup/auth and finish session-flow derivation |

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

## Release 98 delivery verification and heatmap price-window correction

Release `20261113-local-storage-98` (`315e71f`) completed guarded activation at
23:32:56 UTC after all six CI jobs (run 43526518816), full local validation,
fresh six-account flat/zero-order checks and the verified 23:11 recovery bundle.
The lower full-session response now sends 4,120,804 gzip bytes for the same
54,562,449-byte JSON, 269,896 raw measurements and zero skipped segments.
Cold delivery under the full-cache snapshot took 11.985 seconds; immediate reuse
took 1.250 seconds. Recent-window delivery took 0.562 seconds. All 31 concurrent
health probes returned 200, with a maximum of 0.860 seconds. Six flat-account
recorders reported zero BUSY retries, failed commits or discarded callbacks at
the first post-activation check. This remains closed-market readiness evidence.

The actual served browser rendered the lower chart's 47,865 observations and
explicit quality gaps. Switching from 29593203 to 29672001 updated the URL,
selector and recording status together. No console errors were observed. The
browser was closed after verification; no native Computer Use helper remained.

The upper heatmap delivered valid historical tiles in 0.062–0.125 seconds, but
its paused price axis stayed near 27,920 while session-open recorded bids were
28,393.50–28,477.50. Its price selection preferred the current live book and the
summary's final frame even when inspecting an earlier window. A regression
reproduces the resulting off-screen historical data. The prepared correction
uses valid recorded observations inside the paused window, retains live-price
following when returning live, and preserves manual price pan/zoom. The three
affected heatmap test files pass. This correction still requires its own
immutable release and served visual check.

## Release 99 and served chart acceptance

Release `20261113-local-storage-99` (`2ba78b62f04bf67c4ec6036e71efcd8be9702c5b`)
completed guarded activation at 23:46:48 UTC. Exact-commit CI run 43528625164
passed all six jobs. Local validation passed 3,435 backend tests plus eight
subtests (21 intentional skips), 1,292 frontend tests, all 47 deployment checks,
lint, dependency audits and production build. The initial local packaging
invocation used the system Python without pytest; that failed before tests ran.
Its receipt is preserved separately, and the complete successful run used the
repository's existing virtual environment. All six provider accounts were flat
with zero open orders at 23:44:10 UTC; activation repeated its own strict gates.

The served session-open heatmap now centers on recorded prices and visibly
renders depth bands, quote paths and executed-trade bubbles. Whole-session
navigation renders the full retained session with explicit coverage gaps and
the existing zoom-dependent bubble filter. Browser console errors were absent.
The lower full-session chart was already visually verified on release 98 and
its backend is unchanged in 99. Screenshots are preserved off device under
`tmp/remediation-20261113/heatmap-chart-99.jpg`, `heatmap-session-99.jpg` and
`history-chart-98.jpg`. All verification browser tabs were closed; a read-only
process check found no native capture helper.

Actual served Journal probes returned 404. A separately issued local test
session read recording status successfully, signed out with 200, and was then
rejected with 401. This did not revoke the user's browser session.

At 23:46:24 UTC the application, recorder, Discord and backup database
environments all selected loopback PostgreSQL and contained no active
Supabase/Journal/Gemini bindings. The served frontend had no Supabase bindings.
Trusted-device auth remained selected. The execution backup resolves the
current scheduled release; the sync configuration remained retired. The
outbox contained zero rows, all 30 capture triggers were disabled and all 30
serialize/authority triggers enabled. The dedicated local sync login remained
disabled. The recorder role remained non-superuser, NOINHERIT, without role/DB
creation, limited to two connections and default read-only transactions.

### Measured resource comparison

These are 60-sample operational windows with different workloads, not a
controlled causal experiment. The last window includes the full-cache snapshot,
history delivery, flow aggregation and UI verification. API memory changed
with these workloads; these short samples do not establish a memory leak.

| Metric | Initial 19:08–19:20 UTC | Final backend 23:32:59–23:44:22 UTC |
| --- | ---: | ---: |
| CPU median / p95 | 10% / 37% | 10.5% / 29% |
| Available RAM median / minimum | 2,106.5 / 1,082 MiB | 1,839 / 1,484 MiB |
| API private memory median | 1,188.5 MiB | 1,175.3 MiB |
| D: read latency median / p95 | 46.674 / 117.522 ms | 7.990 / 40.256 ms |
| D: write latency median / p95 | 33.557 / 111.498 ms | 4.009 / 42.731 ms |
| D: queue median / p95 / maximum | 6 / 14 / 17 | 1 / 7 / 8 |

Release 98's 23:39:55 load check still showed zero BUSY retries, failed commits,
discarded callbacks, snapshot failures or queued events for all six accounts;
maximum commit latency was 1.719 seconds. Release 99's first six-account check
also showed zero in those failure/loss counters. Prior callback losses recorded
above remain historical losses; process restarts resetting counters do not
erase them. Depth PID 17264 stayed running throughout these application switches,
with eight reconnect errors unchanged. A second recoverable status-publication
PermissionError occurred at 23:39:20 UTC; it did not terminate recording, and
fresh status publication resumed.

The host power inspection found AC sleep and hibernation disabled, automatic
PostgreSQL/SSH/Tailscale services, and boot/retry triggers for the application,
recorder and Discord. Its last actual boot remained October 29; Windows also
reported pending update/component-servicing reboot state. These settings are
not a completed reboot or wake test. Actual host restart verification and the
new full-cache off-device restore remain outstanding at this point.

## Full-cache copy priority correction — November 14, 00:10 UTC

The still-running v20 copy fell from megabytes per second to tens of kilobytes
per second. A nonblocking stack inspection found ordinary file open/write calls,
not the rate limiter. Its Windows process/thread priorities were Idle CPU,
very-low I/O (0) and memory (1). The six account recorders remained healthy.
After exact PID/path/creation-time validation, only the owned backup PID 13444
was changed to BelowNormal CPU, normal I/O (2) and memory (5), without a restart
or snapshot modification. The 8 MiB/s cap and recorder-health admission remained.
At 00:11:47 the backup wrote approximately 1.16 MB/s. The same sample also found
Plex reading approximately 19 MB/s, so disk contention was not exclusively caused
by TopSignal; unrelated processes were left alone. This is an observed recovery,
not a controlled attribution experiment.

The future helper now uses BelowNormal CPU and the explicit rate/health limits
throughout, removing background-resource-mode transitions that could starve bulk
file copies as well as SQLite pages. Closed SQLite integrity copies are paced as
well. Source, WAL consistency, checksum, authority, capacity and retention rules
are unchanged. The active application remains release 99; helper rollout and the
full off-device/restore result are recorded separately below when verified.

## Final configuration recovery scope — November 14, 00:29 UTC

Release 99 configuration was archived at 00:28:45 UTC and independently decrypted
and verified off device at 00:29:26 UTC. Snapshot `20261114T002845Z` contains
**36 members, including eight permanent scheduled tasks**, in 125,324 encrypted
bytes; ciphertext SHA-256 is
`afd622504a8e8367f1648daf462105a0b2b937a85db07844dc65255fe43848f2`.
Every member's size and hash matched. The temporary reboot observer was excluded.

The earlier 21-member archive omitted some host recovery material. This final
archive adds PostgreSQL server/HBA/identity configuration, protected local admin
and role configuration, the original machine-bound service credential, and a
portable service-credential record inside the authenticated encryption envelope.
The deployment credential was decrypted only in memory and never written or
printed in plaintext. The replacement-host recovery copy can decrypt this record;
the original DPAPI blob alone would not establish that. Retired mirror environment
files remain inert recovery evidence, not runtime configuration.

Host: `D:/TopSignal/data/databento/configuration-recovery-v1/20261114T002845Z`.
Off device: `C:/Users/drews/.ssh/topsignal-local-first-20261102/configuration-recovery/20261114T002845Z`.
The off-device receipt is also beside the host archive. Preserve the separately
protected recovery key: the archive cannot decrypt itself. This configuration
archive complements the consistent PostgreSQL/auth dump and full-cache snapshot;
it does not replace either one or automatically provision execution authority.

The backup helper at `tools/backup-remediation-v4` matches commit
`77ca014d67c414fa542bd1c2630e56b1e3d2c7c3`, with all six CI jobs successful
(run 43532730256). At 00:21:07 UTC an isolated Windows fixture copied and verified
4,464,640 bytes / two files, restored SQLite content/integrity, and observed
BelowNormal CPU with normal I/O/memory priorities. Its 1 MiB/s verification cap
produced a 14-second run. Production data was untouched. The future recurring
profile remains 8 MiB/s; its prepared configuration is v21 and local runner v20.
The ongoing full snapshot still uses v20/v3 plus the recorded in-place priority
correction. Schedule enablement awaits full off-device/restore success.

## Latest focused recovery and concurrent load — November 14, 00:44 UTC

The permanent execution-backup task resolved the actual release-99 environment
and created `20261114T004150Z-1d701a7a1e05` at 00:41:51 UTC, publishing at
00:44:02 under concurrent full-cache/Plex disk load. The 24,058,916-byte encrypted
dump covers 38 tables / 13,078 rows. An isolated off-device restore completed at
00:44:41 UTC in `topsignal_audit_local_storage_20261114_1`: all row/sequence hashes
matched, one provider credential decrypted, execution authority was absent, and
local-auth issuance/restart/revocation passed with zero network calls. Dump hash:
`f497b6758907e0e23ea6b04064e9ad6b1878b711590cf1d8ded6033190a5535d`.
The receipt was returned beside the host ciphertext files. The disposable local
PostgreSQL server on loopback port 55432 was then stopped gracefully.

Host bundle: `D:/TopSignal/data/databento/execution-backups-v1/20261114T004150Z-1d701a7a1e05`.
Off-device bundle: `C:/Users/drews/.ssh/topsignal-local-first-20261102/remediation-backups/20261114T004150Z-1d701a7a1e05`.
The local pre-restore capacity check found 1.164 TB free against a 65 MB database
growth estimate; previous audit databases and backups were retained.

A further 20-sample window at 00:32:35–00:34:40 UTC measured CPU median 7.5%,
available RAM median 1,830 MiB (minimum 1,790), and API private memory 1,095.0–
1,095.1 MiB. D: read latency was 49.72 ms median / 155.65 ms p95; write latency
48.81 / 135.27 ms; queue median 7 / p95 13. This later mixed Plex/archive workload
was heavier than the earlier served-history window; the earlier 7.99 ms read
median is not a permanent latency guarantee. At 00:26:37 all six recorders were
healthy with zero commit failures, BUSY retries, discarded events or queued
observations; maximum observed commit time was 3.047 seconds. These closed-market
checks establish observed readiness, not new live-position capture completeness.

## Transport helper and preserved snapshot — November 14, 01:23 UTC

The original full snapshot exceeded its two-hour SSH wait at 01:14 UTC. Both
retries refused to overlap the still-running host process; their failure receipts
are retained. The original snapshot was not killed or restarted. At 01:21 UTC it
had reached the worker-evidence databases after copying the raw/totals, research,
Journal archive and other earlier stores. Transfer, complete receiver verification
and isolated replay restore are still required at this checkpoint.

Helper commit `abbf31761a6de65e09a619fa7f7f2949166d2c18` passed all six CI lanes
(run 43539463205). Its transport writer hashes tar headers, payloads and padding
as written, avoiding a second complete HDD read. The receiver still checks the
transport hash before extraction and every reconstructed member afterward.
The 76 targeted evidence/recording/backup checks passed, with one environment
skip. Two burst tests now wait for the asynchronous drain beyond `close()`'s
bounded caller wait; every event and the durable hash chain remain checked.
Application shutdown timing was not changed.

Protected host helper `backup-remediation-v6` and off-device runner
`scheduled-tools-v22` have exact source receipts. At 01:23:15 UTC the isolated
Windows fixture verified a 4,474,880-byte tar against an independent file hash
and all member hashes, after checking the two source files and restored SQLite
contents. CPU priority was BelowNormal, with normal I/O and memory priority.
Production data was untouched. The catch-up remains capped at 8 MiB/s and the
capacity budget remains 250 GB. Future configuration v22 is prepared; its weekly
schedule remains disabled until successful full verification and restore.

At 01:17 UTC all six account recorders were healthy, with heartbeat ages below
2.4 seconds and zero commit failures, retries, discarded events or queued work.

## Preserved SQLite failure and continuation — November 14, 02:23 UTC

The original snapshot stopped at 01:57:42 UTC with `OperationalError` while
reaching worker capture `da176e7c91c3491b8b071c2ec321b125`. Its partial directory
and failure receipt remain unchanged. That helper did not preserve the SQLite
code, so the exact cause cannot be assigned from its receipt. A subsequent
read-only open took 299.656 seconds to process the 2,792,503,072-byte WAL and
succeeded: WAL mode, 4,096-byte pages, 178,311 logical pages and five schema
entries. This is evidence of a successful open, not a complete integrity check.
The source database/WAL modification times remain November 4/5; no application
rows were changed by the diagnostic.

Commit `571d82c323e61d19a59a627a7a337049f94de6d5` adds the cache-relative member
and fixed SQLite error code/name to future failure receipts, without persisting
arbitrary error text. All six CI lanes passed (43544787795), as did 31 focused
backup checks. Protected helper `backup-remediation-v7` and matching off-device
`scheduled-tools-v23` are prepared. An isolated host corruption fixture returned
`SQLITE_NOTADB`/26 and the correct member at 02:23:13; production data was untouched.

A tested continuation began in new staging directory
`20261114T021916Z-9270ec34` at 02:19:18 UTC. It leaves the failed attempt intact,
links unchanged completed files, checks raw segments against committed hashes
on the receiver, hashes reused SQLite copies, and takes fresh online snapshots
when sources changed or were unfinished. The already-opened large-WAL source
still uses SQLite's online backup API; its redundant warm-up is skipped. Three
isolated checks proved preservation, changed-source handling, exclusion of
uncommitted rows, and rejection of corrupt partial raw bytes at verification.
The first continuation launcher stopped during preflight because it counted its
own Python launcher; it created no snapshot. The corrected launcher excludes
only its exact parent running the same script.

Capacity preflight measured 104,992,528,435 bytes of existing staging and allowed
40,236,722,934 bytes of conservative growth within the unchanged 250 GB limit.
The rate remains 8 MiB/s. The eventual transport tar will use the protected host
SSD staging folder, whose 74.9 GB free space exceeded the estimate plus reserve;
primary recordings and snapshots remain on D:. Full receiver verification,
isolated restore, schedule enablement and reboot are still pending here.
At 02:23 all six account recorders were healthy, with zero commit failures,
retries, discarded observations or queued events.

## Invariant-root I/O reduction — November 14, 02:45 UTC

A live stack during the continuation's inventory scan showed filesystem work
while repeatedly resolving invariant storage roots. Commit
`781659c2c94c29b971020f14125b956a91dc0148` resolves those roots once per snapshot
and resolves each marker once, while preserving member resolution, containment,
size, hash and ownership checks. The regression fails against the prior helper
(root resolutions grow from four for one segment to thirteen for four) and
passes with the change; all 32 focused backup checks and all six CI lanes passed
(43547234180). No running helper was modified.

Protected helper `backup-remediation-v8` and matching off-device
`scheduled-tools-v24` / configuration v24 are staged. At 02:45:31 UTC its isolated
host fixture verified eight files including three sealed raw segments, SQLite
contents, every file hash, and an independently checked 4,495,360-byte transport
tar. Priorities remained BelowNormal CPU / normal I/O / normal memory. The
continuation remains pinned to helper v6; packing, receiver verification and
future scheduling are prepared against v8. At 02:43 the continuation had staged
5,943 raw files / 2,051,414,915 bytes by reusing the preserved partial. This is
progress evidence, not a completed backup receipt.

## Backup cadence review — November 14, 03:34 UTC

The prepared Saturday-only schedule was never enabled. Review found that it
would exceed the application's existing 36-hour off-device freshness threshold
between every run. That warning correctly describes recovery exposure and was
not weakened. The replacement preparation preserves daily recovery, moving the
former 18:15 start to **17:10 America/New_York**, during the regular futures
maintenance break. This is a start time, not a guarantee that heavy work finishes
before the next session; the 8 MiB/s limit and recorder-health admission remain.

Private configuration v25 uses the same verified helper v8 and local runner v24;
only its schedule description differs from v24. The daily enablement script
passed PowerShell parsing and still requires the completed verified continuation,
isolated restore and published receipt. Nothing has been enabled at this point.
Snapshots reuse unchanged members, verify every reconstructed file off device,
and run the isolated input/model restore at least weekly. The recovery PC must
be on, signed in and privately connected. Capacity remains 250 GB with no
automatic cleanup; retained attempts and daily growth still need review.

## Completed source snapshot and transport timer finding — November 14, 03:49 UTC

The continuation completed at **03:40:32.339875 UTC**. Its manifest, created at
03:39:38.377069 UTC, contains **59,635 files / 16,014,080,224 bytes**, including
28,546 sealed depth segments. It reuses 18,688 members from the verified November
4 backup. The preserved failed attempt contributed 19,195 raw files, 20,813 other
files and 39 SQLite copies; 26 SQLite databases were snapshotted afresh. The
previously failing worker database produced a 730,361,856-byte consistent online
copy. Manifest SHA-256:
`68f74ad237dbe19c67b635839c2103cc43162efd69a8b2f58214b5653752162c`.
This completes host staging, not off-device verification or restore.

Transport packaging began at 03:42:12 UTC into the protected C: SSD directory.
Its preflight measured 117,907,719,168 staging bytes and 75,494,543,360 bytes of
SSD free space against a 16,277,233,009-byte conservative transport estimate.
Observed throughput was about 1 MiB/s despite the configured 8 MiB/s cap.
A memory-only host probe reproduced the cause: 16 KiB pacing steps took 2.000
seconds for 2 MiB, while 1 MiB steps took 0.250 seconds. The host rounded the tiny
requested sleeps to coarse timer ticks; this result did not depend on HDD load.

Commit `af4db13cb68eef3a61bad23bbbf8d71ce843cbb4` changes tarfile's copy buffer
to the same bounded 1 MiB chunks used by ordinary copies. The byte cap, health
admission, hashing and receiver checks remain intact. A deterministic coarse-timer
regression fails on the old packer (4.015625 seconds for 4 MiB), and all 33 focused
backup checks pass with the change. Protected helper v9 and local runner v25 /
daily configuration v26 are staged separately; the active v8 process was not
modified. At 03:49:34 UTC the isolated host fixture verified every member, SQLite
contents and the independent hash of a 17,858,560-byte tar in 2.422 seconds
(7.023 MiB/s payload throughput). CPU/I/O/memory priorities remained 16384/2/5.
Production data was untouched. Transport replacement still requires exact-commit
CI and exact process identity checks; the completed snapshot and partial tar
will be preserved. Final off-device verification and reboot remain pending here.
