# peteamdpc disk contention — November 13, 2026

Follow-up: [authorized disk reductions and MusicDesk SSD migration](peteamdpc-disk-reduction.md)
records the later live changes, backups, and post-change measurements.

## Outcome

The D: recording HDD is contended by small random database operations and
repeated filesystem traversal. There is avoidable work, but disabling one
obsolete helper did not resolve saturation. Final samples at 14:22–14:23 EST
were 73–100% active (94.4% average across eight snapshots), with queue lengths
2–10. These are short samples, not a continuous utilization average.

The **TopSignal Microstructure Features** task was disabled at
19:16:58 UTC / 14:16:58 EST. Its action was verified as the standalone
`backfill_depth_features.py --latest` helper, and its XML was exported first.
The invocation observed earlier had already finished by the pre-change snapshot
(task Ready); the stop request therefore did not demonstrate a causal immediate
drop. No feature processes remained afterward. Disabling future runs prevents
its recurring five-minute scans; it also stops that legacy derived feature
output. Existing derived output and all original recordings were preserved.

No trading/API, heatmap, or independent-recorder process was restarted, no
account was changed, and no application data, retention policy, durability setting,
security exclusion, storage budget, or backup schedule was changed.

## Evidence

A Windows DiskIO/FileIO trace contains D: completions from approximately
14:14:04–14:15:24 EST. Selected file-attributed operations:

| File/workload | Operations in trace | Mean completion latency |
| --- | ---: | ---: |
| TopSignal `market-archive/v1/<owner>/archive.sqlite3` reads | 786 | 122 ms |
| MusicDesk `musicdesk.db-wal` writes | 333 | 90 ms |
| Trade progress `progress.sqlite3-wal` writes | 185 | 128 ms |
| Active worker-evidence SQLite WAL writes | 164 | 125 ms |

These are operation counts and response latencies, **not shares of disk busy
time**; concurrent waits overlap. Some file-object associations could not be
resolved and remain explicitly unattributed. ETW file-object reuse was accounted
for by invalidating closed objects and only using unambiguous late name records.
The trace decoder reported unsupported schemas for some events, so the decoded
physical DiskIo events and directly named file opens are the primary evidence.

The trace also shows reads of Apollo application binaries by temporary
PowerShell processes, Defender activity, and routine recording/NTFS writes.
Those PowerShell processes had exited at the later inspection; their purpose
was not established and they were not stopped. MusicDesk and StreamingDesk
were left running.

The standalone feature helper opened the depth-recording root 7,743 times during
the trace; the heatmap process opened it 14,626 times. These are logical file
opens, not necessarily physical reads. Source inspection explains repeated
ancestor checks in `safe_path`, manifest inventories, and metadata traversal.
The feature helper's latest inspected failure receipt was
`Existing recording/derived storage limit or review boundary reached`.

The independent recorder reported 28,163 sealed segments at its last capacity
inventory, which took 606.5 seconds. Its next scan was still pending/stalled at
14:23, with no storage-block reason and no scan failures. The asynchronous scan
does not itself prove a recorder outage, but competes for the same disk.

## Continuity and limitations

The backend (16860), heatmap (12576), and recorder (4752), plus their launchers,
retained their PIDs and creation times before/after the task change and at the
final check. Recorder durable index advanced from 16,807,525 to 16,937,483;
current-process reconnects and errors remained zero. `/health` returned 200
in 57 ms and `/ready` returned 200 in 15 ms. This does not certify complete feed
coverage or resolve pre-existing worker drops.

WPR's initial stop hit the installed version's COM-mode error; a subsequent
stop with `-skipPdbGen` succeeded. The trace is consequently longer than the
requested 15 seconds. No WPR session or WPR/tracerpt process remained after
collection. Trace decoding was moved to the development PC; temporary decoded
XML is disposable, while the original ETL and summaries are retained. Profiling
and concurrent diagnostics can perturb performance, so this was not a controlled
benchmark and no improvement percentage is claimed.

## Further reductions without purchasing hardware

1. Reduce full-directory and repeated ancestor/manifest work through incremental
   inventories and bounded caches, preserving path checks, storage limits and
   source verification. The concurrent remediation work in this checkout is
   already changing the history reader; this investigation did not overwrite it.
2. Profile and reduce repeated archive queries/connection churn. The trace
   identifies archive reads, but does not identify the exact SQL call responsible.
3. Consider moving MusicDesk/StreamingDesk's small hot databases to the existing
   C: SSD after measuring their size and arranging a consistent backup and brief
   service stop. C: had about 80 GB free at the preceding usage check. This is a
   proposed follow-up, not an executed move or a recommendation to move all raw
   recordings there.

Do not reduce recording frequency, weaken SQLite/fsync durability, disable
Defender, delete archives, or raise storage limits to mask contention.

## Receipts and rollback

Local supporting evidence is under `tmp/disk-investigation-20261113/`:
`baseline.etl`, `baseline.summary.json`, `mitigate.out`, `final.json`, diagnostic
scripts, and the exported `microstructure-task-before.xml`. The host receipt
directory is `C:/ProgramData/TopSignal/runtime/diagnostics/disk-20261113/`.
These diagnostic files are not recording backups.

To restore the prior recurring feature behavior, enable the existing task:
`Enable-ScheduledTask -TaskName 'TopSignal Microstructure Features'`.
The old schedule/action/security context remain registered. Re-enabling will
also restore the scan load and does not fix its storage-limit failure.
