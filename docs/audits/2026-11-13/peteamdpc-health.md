# peteamdpc operational health — November 13, 2026

**Overall: available, but collection and history delivery are degraded.** The
host has RAM headroom and responds quickly to lightweight readiness checks.
Independent depth and enrolled practice-position recording are advancing.
However, history requests time out, the session contains genuine coverage
breaks, position observations have sampling gaps, worker evidence has substantial
recorded loss, and the full off-device backup is stale. Supabase is **partially
retired**, not fully retired.

## Scope and evidence

Read-only SSH/Windows diagnostics and explicitly read-only PostgreSQL/SQLite
queries ran approximately **18:43–18:54 UTC (13:43–13:54 EST)**. Browser inspection
read the existing hosted tab without changing its account, enrollment or trading
controls. No services were restarted, code deployed, accounts enrolled, broker
orders sent, migrations run, data deleted, credentials removed or billing changed.
No heavy backup, full archive scan, restore or provider-history recovery was run.

Sanitized diagnostic scripts and receipts are retained locally under
`tmp/health-20261113/`: `resources.out`, `inspect.out`, `stores.out`, `detail.out`,
`followup.out`, `final-resources.out`, `coverage.out` and `final-db.out`. These
Git-ignored files are supporting evidence, not backups. Measurements are
individually timed and not an atomic cross-store snapshot. Older reports are
identified separately below. Credentials, signing keys and tokens were not
printed. Configuration evidence is limited to safe values, presence flags and
connection host/port; protected files were not copied.

| Area / timestamp (UTC, November 13 unless stated) | Evidence and finding | Severity / uncertainty |
| --- | --- | --- |
| Host, 18:47–18:52 | `DESKTOP-6EUPDU1` online; boot October 29 19:48:52 UTC; release `20261112-auto-signin-87`; API/frontend started November 12 21:10:42 UTC | Available; about 14 d 23 h OS uptime and 21 h 41 m application uptime |
| Resources, 18:47:59–18:48:21 | Three samples: CPU 22/35/24%; 2,314/2,170/2,156 MiB available; disk queue 7/5/10 | Warning: sustained disk queue during this short window; no RAM exhaustion |
| API/worker, 18:52–18:54 | `/health` 200 in 69 ms, `/ready` 200 in 23 ms; run 18 heartbeat 18:53:43, lease heartbeat 18:53:53 | Ready at sample time, not proof of uninterrupted operation |
| History API, 18:48:30 | Selected account's `order-book-history` returned 500; stack ends at `depth_history.py:42`, `future.result(timeout=125)` | High: confirmed saved-history delivery failure; later 200 at 18:49:13 |
| Independent depth, 18:48:29–18:51:27 | Durable index 16,142,467 → 16,223,838; totals 122,443 → 122,965; accepted depth/quote/trade subscriptions, zero current-process connection errors | Recording now; no provider completeness guarantee |
| Overnight coverage, session beginning November 12 23:00 UTC | Four approximately four-minute holes between derived chunks; disconnected/stale quality intervals also retained | High: full-session continuity not established; exact user-reported price-move interval unknown |
| Position recorder, 18:49:58 | Only practice `29593203` enrolled; fresh heartbeat, no scope error; 91 lifecycles, 34,944 samples, 307 gap events, zero fills | High: current readiness plus incomplete historical capture |
| Worker input evidence, 18:51:28 | 13,253,240 durable events, 844,091 cumulative drops; queue 53/4,096, oldest 0.016 s, zero commit failures | High: historical loss despite current queue keeping up |
| PostgreSQL, 18:49:58 | Local authority active; 59,242,163 bytes; no blocked sessions, no idle-in-transaction sessions, cumulative deadlocks zero | No contention observed; short snapshot, not continuous monitoring |
| Cloud mirror, 18:48:33 | Live task/process, remote Supabase target, acknowledged sequence 170,728, zero pending events, recent success | Active dependency, not retired |
| Backups, inspected 18:48–18:52 | Full off-device task disabled; full verified receipt November 4; host execution snapshot November 12 awaits archive pull | High: later raw recordings not protected by the latest full receipt |

## RAM, CPU, disk and runtime

Usable physical RAM is **7.928 GiB**. Across the three initial samples,
**5.67–5.82 GiB was used/unavailable** and **2.11–2.26 GiB available** (used is
total minus Windows available memory, including system use). At 18:52, available
memory was **2,050 MiB / 2.00 GiB**, leaving about **5.93 GiB used/unavailable**.
Committed memory was 10,174–10,236 MiB initially, against an 18,358 MiB limit.
The 10,240 MiB pagefile held 710 MiB, peak 1,538 MiB. Page input was 91–252 pages/s
initially and 304 pages/s at 18:52; output was zero in these samples. Page input
also includes file-backed hard faults, so it is not a measure of pagefile reads
alone. This is memory pressure/headroom evidence, not proof that paging caused
the history failures.

Major TopSignal processes at 18:52 (MiB; resident working set differs from private
committed memory; shared PostgreSQL pages can be counted in several processes):

| Process | PID | Working set | Private committed |
| --- | ---: | ---: | ---: |
| API, trading worker and position recorder | 16860 | 419.1 | 1,164.9 |
| Independent depth recorder | 4752 | 29.3 | 824.6 |
| Heatmap deriver | 12576 | 139.4 | 150.6 |
| Local PostgreSQL children, summed | multiple | 340.0 | 61.6 |
| Discord TopBot helper | 17212 | 25.4 | 69.2 |
| Cloud sync | 12312 | 13.8 | 38.6 |
| Frontend server | 13648 | 8.3 | 13.7 |

Small Python launcher processes add about 3 MiB each. A replay child was also
present; its memory varies as jobs start and finish. Other major host residents
included Defender (~424–428 MiB), Apollo (~205 MiB), Explorer (~190 MiB), and Plex
processes (~244 MiB combined at the initial samples). API private memory rose
from ~1,051 to ~1,165 MiB over four minutes of changing workload. This warrants a
longer trend but does **not** establish a leak. No Chrome/Edge browser process
was found on the host; background WebView processes existed.

Disk capacity at 18:48: C: **80.04 GB free / 255.48 GB total**; D: **1,329.61 GB
free / 2,000.40 GB total** (decimal). At 18:52 D: had queue length 5 while C: had
zero. D: is the recording HDD identified in the earlier storage inspection;
ample capacity does not imply low read latency. Lightweight HTTP checks took
3–103 ms during the first samples. Those endpoints do not exercise full-session
archive reads.

The application supervisor has not restarted the current application tree since
November 12 21:10 UTC. It logged transient degraded-readiness signals today,
including 18:45:20 UTC, explicitly without initiating a restart. The independent
recorder process began November 13 09:23:54 UTC and connected at 09:24:07 UTC;
the preceding lifecycle/cause was not conclusively recovered. Bounded recent
Windows event checks found no relevant system crash/resource-exhaustion events
or TopSignal application crashes; unrelated Codex provider events were present.
Task Scheduler's repeated-trigger result `0x800710E0` on already-running helpers
must not be counted as a process crash. The microstructure helper had one observed
last result 1 and was running on the next trigger; its failure cause remains
unverified. No task was changed.

## Liquidity: source, delivery and rendering

There are two panels and several independent collection paths:

1. ProjectX feeds the independent `TopSignal Depth Recorder`. It writes raw
   compressed depth/trade/quote segments and derived book totals under
   `D:/TopSignal/data/databento/depth-recordings-v1/`. It runs at boot without
   a browser, login or development PC. The host must stay awake and connected.
2. The **upper heatmap** derives frames from sealed raw segments, with local
   checkpoints in `depth-heatmap-v1/<owner-hash>/`. It excludes interrupted,
   stale, crossed, reset and invalid book intervals. Its process yields during
   replay/recording pressure. The UI showed that pause during inspection; at
   18:49:55 the deriver reported `current`, zero pending segments and processing
   through 18:49:15. Its temporary delay was not permanent source loss.
3. The **lower “Liquidity before the move” chart** reads sealed totals through
   `/api/accounts/{account}/order-book-history`. Release 87 reads local files,
   not SSH back to the development PC. Its reader inventories retained manifests
   and verifies/decompresses selected segments. Duplicate requests share a
   Future; a follower times out after 125 seconds. The timeout propagated to HTTP
   500 during this audit. The browser simultaneously showed `Failed to fetch`,
   no “Recorded through” timestamp and only provisional live samples.
4. Browser samples supplement these histories: lower-chart samples run once per
   second while visible and remain in that tab; heatmap live samples run at 2 Hz
   with a ten-minute bound. Closing/hiding the tab removes that provisional
   source, while independent host collection continues. Chart lines break across
   missing/stale data, source/run changes and gaps; they do not interpolate.

The best-supported explanation for the **observed current display failure** is
slow/failed history delivery, with D: contention a plausible contributor. The
timeout stack is verified; attribution to a particular competing disk reader is
not. It does not prove that the user's earlier gap before a specific price move
had this same cause. No exact move time/chart was supplied.

The current derived session starts at **18:00 EST November 12** and was still
in progress at inspection; today's 16:00 EST cutoff had not yet occurred. Its chunk
metadata identifies these holes, separate from the failed browser request:

| New York interval | Duration | Interpretation |
| --- | ---: | --- |
| November 12 19:25:02–19:29:04 | 242.4 s | No derived chunk coverage |
| November 12 19:50:04–19:53:58 | 234.4 s | No derived chunk coverage |
| November 12 22:00:12–22:03:59 | 227.2 s | No derived chunk coverage |
| November 13 04:20:08–04:24:07 | 239.1 s | Ends at current recorder's connection time |

There were 338 stored quality-gap entries in the inspected session, including
298 stale, seven disconnected, 14 reset, 14 crossed, four no-level and one
awaiting-depth entries. Adjacent entries can describe one interruption; these
are not 338 independent outages. Disconnected entries span roughly
20:53:22–20:56:18 EST November 12; another stale interval spans
13:49:32–13:49:51 EST November 13. Metadata establishes missing derived coverage,
not whether every missing raw event could be recovered from another retained
source. No full raw scan was performed to resolve those individual holes.

The intended **18:00–16:00** period is within the recorder's operating window,
but actual continuity is not guaranteed. Chart sessions are **18:00–17:00 New
York**, excluding the daily halt/weekend; bot entry and flatten cutoffs are
separate. Stored receipt/provider timestamps are UTC; November 12–13 conversions
use EST (UTC−5). The heatmap aligns live samples to server time. Current provider
ages were approximately 0.4 seconds; no evidence establishes clock skew as the
cause of these failures. Older November 11 clock-skew evidence is historical.

Where sealed source records exist, a successful bounded read or later derivation
can restore their display. Provider disconnects are not automatically backfilled
with depth, and never-recorded observations cannot be reconstructed honestly
from candles. Obtain the exact chart/time to match a specific price move to these
boundaries and inspect only its nearby raw/totals manifests.

**Recommendation: keep the browser on the viewing PC.** Moving it to peteamdpc
does not fix API waits, missing enrollment, disk contention or source omissions.
It would add browser/rendering memory and CPU to an 8 GB host. Incremental RAM
was not measured because no browser was launched there; a provisional capacity
allowance of 0.5–1.5 GiB would be an estimate, not a TopSignal measurement or cap.
Continuous backend collection is already the correct mechanism; improve its
coverage and history delivery rather than relying on a perpetually visible tab.

## Trade-progress enrollment and recording

The supplied URL was `http://127.0.0.1:4173/bot?account=28278873`. The observed
active selector, sidebar links and recorded-liquidity account scope were actually
**29672001**. Access logs independently confirm requests for 29672001. Local
PostgreSQL shows 28278873 is invisible/non-tradable, 29672001 visible but
non-tradable at inspection, and practice 29593203 visible/tradable. All three
belong to the same owner as the protected local-auth store and recorder scope.
One unexpired local session and recent `trusted_device_sign_in` events support
the local authentication path; no browser credential was extracted.

`trade_progress_store.status` defaults to `enrolled=false`, `heartbeat=null`.
Only an enabled row for the exact owner/external account changes enrollment.
The UI prints **Not enrolled** and **No recorder heartbeat** directly from those
fields. **1-second observations · includes manual trades** describes the feature;
it does not certify that this selected account is enrolled. **Enable recording**
would change enrollment, so it was not clicked. Switching the account never
enrolls it automatically in release 87.

The only scope is practice **29593203**, enabled with a heartbeat advancing from
18:48:33 to 18:49:57 UTC and no stored error. Its recorder is a manager/thread set
inside API PID 16860, not a separate Windows recorder task. Its store is
`D:/TopSignal/data/databento/trade-progress-v1/progress.sqlite3` (~22.86 MB at the
first check). Heartbeats are durable writes; the latest sampled position
observation at 18:49:58 was an exit at **18:45:19 UTC / 13:45:19 EST**, with zero
positions then open. Additional sampling-gap rows at 18:52 prove later activity
continued. A flat account legitimately has no new position samples.

The November 11 deployment receipt enrolled this same practice scope at
06:04:05 UTC and verified it after restarts. Today's state does not show that
enrollment was lost. Neither selected nor URL account has a scope row under any
owner in this store. There is no enrollment-change audit/timestamp column, so
the exact history of an absent row across all past stores cannot be proven.
The UI/URL mismatch is a concrete usability issue; a cross-owner recorder
mismatch is not supported by the evidence.

Recorded lifecycles begin November 11 **03:12:41 EST**. At 18:49:58 UTC there
were 91 lifecycles, 34,944 samples, 304 `sampling_gap` events and three
`position_snapshot_failed` events. The last observed exit was flagged
`exit_during_missing_coverage`. Recent sampling gaps include
18:52:22.462–18:52:27.729 UTC (5.27 seconds). These are explicit missing intervals,
not zero movement. The scope has **zero recorded fills/revisions** despite real
position observations. The earlier [trade-pipeline audit](trade-pipeline.md)
identified exact-case fill dispatch and timestamp rejection as candidate causes;
the original payloads needed for full historical attribution are not retained.
Its prepared corrections are not deployed in release 87.

The implementation observes enrolled account positions regardless of manual or
bot origin. It does not label every observation with a verified origin, so this
check cannot separately certify complete manual and automated coverage. Both
would be missed on unenrolled accounts; complete fill capture is demonstrably
unverified even on practice. Provider fill-history recovery can recover available
legs, not missing one-second price paths. The November 13 recovered evidence is
separate from production and must not be silently merged or synthesized.

Enrollment and Liquidity are separate systems: market history is owner/contract
scoped, while trade-progress is owner/account scoped. An unenrolled account can
view recorded market depth. Disk/CPU/network interruptions can affect both, but
the selected account's absence of enrollment is not caused by the history timeout.

## Worker evidence and backup risk

The current worker capture was ~549 MB at 18:51, with **844,091 cumulative drops**,
13,253,240 durable events, queue 53/4,096 and no commit failures. Maximum recorded
commit duration was 12.421 seconds; latest commit was 0.094 seconds. The earlier
07:09 UTC report had 303,771 drops in this capture. Current queue recovery cannot
undo earlier losses. Oversized-book rejection is supported by the earlier source
audit; this deployed status has no per-cause counters, so not every drop can be
attributed to that mechanism. Worker evidence is separate from independent raw
depth, and missing exact worker inputs must not be replaced by a different feed.

Existing receipts inspected, without launching new verification work:

| Backup scope | Latest evidence | Protection / limitation |
| --- | --- | --- |
| Full cache off device | November 4 20:52:13 UTC; 20,455 files / 5,561,615,793 bytes; restore receipt | Development PC `.ssh/topbot-archive-20261028/backups/20261104T180605Z-a89f27f7`; later raw capture excluded |
| Execution database on host | November 12 22:45:20 UTC; 23,887,524 encrypted bytes, 37 tables / 12,549 rows; `verified_and_published` | D: `execution-backups-v1/20261112T224503Z-0d7d172ab6a5`; explicitly `pending_archive_pull` |
| Focused off-device recovery | November 13 restore receipt in local `backend/storage/databento/trade-review-v1/restore-check-20261113-01/` | 83 positions / 33,822 samples, study results/jobs and scoped execution export; not full raw history or PostgreSQL disaster recovery |
| November 11 progress readiness backup | Historical 69,632-byte empty recording store | Protects enrollment/readiness then, not subsequent observations |

The development-PC `TopSignal Off-device Archive Backup` task was verified
**Disabled**. The UI reports the November 10 failed attempt (`TimeoutExpired`).
The earlier audit says the helper's timeout/overlap correction is already
installed; this check did not invent or apply another backup fix. Host staging
is not off-device protection. Preserve encryption keys and local auth state,
SQLite online-backup requirements, original source archives and all prior
verified receipts. No cleanup or expansion of budgets is authorized here.

## Supabase retirement status

| Component | Verified present architecture | Dependency classification |
| --- | --- | --- |
| Execution/application DB | Local PostgreSQL `127.0.0.1:5433/topsignal`; authority `b15899c2-5a5b-4352-aa5d-162c600188eb` active | Local authoritative store; no cloud execution fallback |
| Research/recordings | D: cache, local research SQLite, depth/totals segments, progress SQLite, worker evidence, derived heatmap/study stores | Local; not covered by Supabase backup |
| Current sign-in | `trusted_device`, protected local auth SQLite, `AUTH_REQUIRED=true`, local sign-in events | Current session local; no Supabase token refresh on this path |
| Alternative authentication | Backend Supabase URL/JWKS/issuer settings and frontend Supabase URL still configured; local auth also explicitly enabled | Configured cloud path, not merely dead imports; cloud login/refresh was not exercised |
| Compact-record mirror | `TopSignal Local Cloud Sync` running; local source → Supabase pooler; recent acknowledgements and zero pending events | Active Supabase dependency |
| Journal images | `JOURNAL_IMAGE_STORAGE_BACKEND=supabase`, service/bucket configuration present; nine image references / 1,500,711 bytes in local DB | Configured cloud object dependency; objects themselves were not fetched/inventoried |
| Independent depth startup | Task uses `dashboard-18` backend and config 32; its `.env` database points at Supabase pooler | Cloud startup credential/config read; established stream and restart have different dependencies |
| Backups | Encrypted host execution snapshots and development-PC archive/focused receipts | Filesystem recovery; cloud mirror is not full disaster recovery |

Retirement requires a separate authorized migration: inventory and verify all
journal objects locally/off device; switch and validate attachment access; move
the recorder's credential/config startup read to the current local authority with
its read-only/dry-run safeguards; verify local auth issuance/expiry/revocation and
restore without cloud services; then deliberately retire the mirror and its
outbox triggers/tasks after a verified final backup and authority review. Review
all scheduled helpers before removing cloud settings. Do not merely disable the
sync consumer while leaving unbounded outbox growth, delete cloud audit records,
or treat a stale mirror as failover. Compatibility code can remain without an
active dependency once these configured paths are retired and tested.

## Prioritized next steps

1. **Confirmed:** resolve selected-account scope before enabling anything. Select
   practice to inspect its existing recorder; separately review which eligible
   accounts should enroll. Repair stale URL/fallback disclosure. Do not enroll
   every historical account or change trading eligibility.
2. **Confirmed:** correct history request handling so a shared-reader timeout is
   an explicit recoverable response. Measure bounded window reads and replace
   repeated full retained-manifest walks with a bounded/incremental index; keep
   authentication, checksum and gap semantics. Schedule archive/replay work so
   it cannot starve durable recording. Longer timeouts alone are not a fix.
3. **Confirmed:** complete review/validation of prepared fill-dispatch and worker
   evidence corrections from the trade-pipeline audit, then deploy only in an
   authorized maintenance window. Verify actual future fills and sample cadence;
   historical gaps remain excluded. No diagnostic orders are needed.
4. **Confirmed:** obtain a low-load window for current full off-device backup and
   restore verification, then review whether to re-enable the schedule. The
   focused receipt does not close the raw-history exposure.
5. **Further investigation:** get the exact price-move/chart time, inspect nearby
   raw/totals manifests for the four holes and the recorder restart, identify
   D: read contention and microstructure task failure, and collect a longer memory
   trend. Do not declare a memory leak or provider-wide outage from this snapshot.
6. **Optional architecture work:** retire remaining Supabase dependencies through
   the staged migration above. Browser relocation is not a corrective action.

Documentation updated, plus this audit:

- [AGENTS.md](../../../AGENTS.md)
- [README.md](../../../README.md)
- [Storage policy](../../supabase-storage-policy.md)
- [Local-first storage and recovery](../../local-first-storage.md)
- [Hosted dashboard connection](../../hosted-dashboard-connection.md)
- [Windows operations](../../windows-24x7-operations.md)
- [Trade-progress recording](../../trade-progress-recording.md)
- [Depth recording](../../topbot-depth-recording.md)
- [Liquidity heatmap](../../live-liquidity-heatmap.md)
- [Order-book history](../../order-book-history.md)

Historical incident and deployment reports remain unchanged. Documentation edits
do not deploy runtime changes or expand the current maintenance authorization.
Validation: relative documentation links resolve and `git diff --check` passes.
No application test suite was run for these documentation-only changes.
