# peteamdpc disk reduction — November 13, 2026

Follow-up to [the disk-contention investigation](peteamdpc-disk-contention.md).
The user authorized reducing repeated scans and archive reads, moving MusicDesk's
database to C: after backup, and coordination with the concurrent TopSignal release
chat. TopSignal recording was to remain running.

## Live changes

- The obsolete **TopSignal Microstructure Features** five-minute helper remains
  disabled. Its original task XML and existing data are preserved.
- At **19:35:01 UTC**, added `decisions_recovery_time_id ON decisions(at,id)` to the
  active market archive. SQLite's existing recovery query automatically selects it,
  allowing confirmed decisions to be rejected from indexes before table/payload
  reads. The transaction took 0.047 seconds and added 139,264 bytes. The original
  index, records, WAL/FULL durability, and schema user version were retained.
- At **19:37:25 UTC**, switched `D:\PlexTools\MusicDesk` to a directory junction to
  `C:\ProgramData\MusicDesk\app`. Several tools hard-code the old path, so the small
  application/data directory was moved together rather than leaving inconsistent
  database paths. Media remains `D:\Media\Music`. StreamingDesk's separate queue
  database remains on D:; its shared scripts are in the migrated folder.

Only **Home Media Music Desk** and **Home Media Streaming Desk** were paused
(19:34:00–19:37:25 UTC). Both tasks were restored and started. There were no active
downloading/tagging/importing jobs at the stopped-writer checkpoint. Full copy
verification compared SHA-256 hashes for all 10,762 files. The SSD database passed
SQLite integrity checks and retained all table counts, including 11,351 tracks,
10,617 jobs, 27 groups, and 13,572 group items. At 19:39:59 UTC, the old database path
resolved to C:, its heartbeat was current, and its WAL was being updated on C:.
The retained original D: database still had its 19:34:04 UTC stopped-writer mtime.

## Backups

MusicDesk backups reside at
`C:\ProgramData\MusicDesk\backups\ssd-migration-20261113`:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `musicdesk-online.sqlite3` | 13,713,408 | `c476cb4a51ca5490a22241646343a8e4f293ed96ed7a435ef7ad7f89166e03a2` |
| `musicdesk-final.sqlite3` | 13,713,408 | `a442a6770e7eb8b542d82b79820ef94a56895da2d798468404cd952a7f1ee932` |

Both snapshots were copied off-device to the development PC and independently
checked for hash, integrity, and table counts. The complete original application
folder is retained at `D:\PlexTools\MusicDesk.before-ssd-20261113`. The backup
directory also contains the original task XML, copy log, migration receipt, and
copied-file manifest. These targeted backups do not replace full-host recovery.

Before the archive index change, SQLite's online backup API produced
`archive-before-index.sqlite3` (172,195,840 bytes; SHA-256
`473335c2855da6517707c3afdddae3d4fa137d3469e53c950f487990e308ca65`).
Integrity and table counts passed, and the off-device copy's hash matched. The
snapshot took 71.83 seconds. Index growth and unchanged recovery results were
checked on a disposable copy before the live transaction.

## Prepared changes requiring coordinated release

`backend/tools/depth_recording.py` now inventories each directory using fresh
`os.scandir` enumeration metadata rather than repeatedly opening each file to
check type, links, size, and mtime. It retains reparse-point rejection, counting of
hidden/partial/sealed files, propagation of enumeration errors, and existing byte,
free-space, and age-review limits. There is no persistent metadata cache or
automatic deletion.

A standalone copy of this candidate scanned the live recording folder at
19:40:05 UTC in **0.219 seconds**, reporting 28,238 sealed segments, two partials,
9,503,090,802 bytes, and no storage-block reason. The running recorder previously
reported a 606.547-second scan. These runs occurred at different times and cache
states and are not a controlled speedup benchmark.

**The inventory optimization is not loaded into the running recorder.** Its
activation belongs to the concurrent immutable release, which retains the user's
explicit maintenance-window approval gate for recorder/backend restarts. No live
Python module was hotpatched. The schema index addition is also in
`backend/app/services/market_archive.py` for future archive initialization. The
other chat owns the history-reader/index changes and coordinated release.

Validation: 20 inventory/recording tests and 47 archive/recovery/durability tests
passed. New regression tests check inventory behavior and errors, unchanged
pending-decision results, and SQLite's avoidance of confirmed-row table reads.

## Recording continuity and observations

No TopSignal backend, heatmap, frontend, or independent-recorder process was
stopped by this work. Recorder PID 4752 remained active. It reported a
`ConnectionClosedError` at **19:31:08 UTC**, followed by a new connection at
19:31:11 UTC, before the archive index and media-helper pause. This is not an
assertion of uninterrupted feed coverage.

Twenty three-second counter samples at **19:40:39–19:41:36 UTC** were taken after
the copy verification and standalone inventory benchmark had finished, with both
media helpers running:

| Measure | Earlier snapshots (19:22–19:23 UTC) | After changes |
| --- | --- | --- |
| D: active time | 94.4% mean; 73–100% range | **37.0% mean; 22–57% range** |
| D: queue length | 2–10 range | **0.35 mean; 0–2 range** |

The post-change sample means were 8.85 ms for disk read latency and 3.61 ms for
write latency, CPU usage 10.9%, and available memory 2,413 MB. Latency figures are
means of interval averages, not operation-weighted totals. The earlier eight WMI
snapshots and later continuous counter intervals differ in duration and workload;
this supports observed relief, not a controlled attribution or a guarantee that
future bursts cannot saturate the disk. The inventory candidate was not active
during this measurement, so the improvement cannot be attributed to its rollout.

Recorder durable index advanced **17,272,136 → 17,289,353** across the measurement,
with no additional errors or reconnects. All eight tracked TopSignal core and
launcher PIDs retained their creation times. At 19:42:13 UTC the recorder was still
recording (durable index 17,300,549), `/health` and `/ready` returned HTTP 200 in
70 ms and 18 ms, and C: had 78,011,539,456 bytes free. MusicDesk and StreamingDesk
tasks were Running; the obsolete feature helper was Disabled.

## Rollback and receipts

Do not simply point MusicDesk at the retained original: it becomes stale once
the SSD worker resumes. A rollback must first pause both media helpers, back up
the current SSD database, and verify a consistent current copy before changing
the junction or restoring paths. Keep the current copy and retained original
until rollback verification is complete. No rollback or deletion was performed.

The archive index is additive; removing that index alone would restore the old
query plan without deleting records, but would restore avoidable reads. The
prepared inventory change can be excluded from a release without touching data.

Local receipts and scripts: `tmp/disk-reduction-20261113/`. Host diagnostics:
`C:\ProgramData\TopSignal\runtime\diagnostics\disk-reduction-20261113\`.
No recording frequency, account setting, retention/budget guard, security setting,
durability option, or backup schedule was weakened.
