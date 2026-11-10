# Recording reliability — November 10, 2026

Release `20261110-recording-reliability-66` (base `20261110-worker-recovery-65`,
commit `6b78146` plus the working-tree patch) and an update to the independent
depth recorder under `C:/ProgramData/TopSignal/tools/depth-recorder-v1/`.
The background decision-recovery fix from release 65 is preserved unchanged.

Strategy, risk limits, credentials, account permissions, database schema,
retention, storage budgets and billing were not changed. No data was deleted.
No historical rows were written to Supabase.

## Root causes found

1. **Worker evidence stuck in "starting", then dropping events.** Every new
   capture reopened each older capture database that had no metadata sidecar to
   learn its start date. On the D: hard disk each open replays that file's WAL.
   On November 10 at 17:17:49 UTC ten such opens took 206 seconds. The capture
   stayed in `starting` for that time, its 4,096-event queue overflowed and more
   than 65,536 events were dropped (`topbot_evidence_loss`, state `starting`).
   The result was cached per capture, so every reconnect paid it again.
2. **A single SQLite error ended a capture.** At 16:52:43 UTC a lock on one of
   those older files raised `OperationalError`; the new capture went to `error`
   and refused every later event until the feed restarted. Commit errors while
   recording had the same permanent effect.
3. **Unbounded WAL files.** Automatic checkpoints were disabled to keep commits
   fast, and checkpointing happened only on a clean close. A killed process left
   capture `da176e7c…` as a 4 KiB database with a 2.79 GB WAL, which every later
   reader has to replay.
4. **Decision archive writes inside the trading transaction.** The worker wrote
   the archive copy to SQLite on D: before `db.commit()`, with the PostgreSQL
   transaction open, and wrote twice more after the commit, all on the trading
   cycle. Measured `market_archive_write` was p50 781 ms / max 1,953 ms per
   evaluation. Contention failed these writes with no retry: 78
   `decision_prepare` and 75 `decision_write` failures in the previous log file.
5. **The independent depth recorder kept stopping itself.** Its capacity check
   walked all 38,481 recording files every 30 seconds (18 s when the disk was
   quiet, 139.7 s measured today) and raised `StorageLimit` whenever one walk
   exceeded 90 seconds. The process exited and the five-minute task started a
   new one. Overnight this produced runs of about 90 seconds followed by gaps of
   5–17 minutes (for example 00:01–00:02, 00:06–00:08, 00:16–00:18 UTC). These
   are the gaps and stale ranges in recorded history and charts. The exception
   was raised again in the `finally` block, so the status said `failed` rather
   than `storage_blocked`. The continuous walking also slowed every other
   writer on that disk.
6. **Exit-study observation could not reopen a stream** (found during this
   deployment, fixed in the repository, not yet deployed). `StudyRecorder`
   called the client factory with the wrong arguments, so retention after a
   stop or restart raised `TypeError` unless the trading worker still held the
   stream.

## Changes

- `topbot_evidence.py`: storage usage is a cached, process-wide directory scan.
  Older captures are dated once by a separate thread and remembered in
  `capture-starts.json`; the writer never opens another capture's database.
  Startup and commit faults are retried with backoff instead of ending the
  capture. A retried batch is checked first, so an ambiguous commit is never
  written twice. Contention and I/O faults keep buffering in the bounded queue
  (`retrying`); storage limits refuse immediately (`storage_blocked`) and resume
  when the condition clears. Refused events remain index holes with
  `dropped_before` counts. A separate thread runs passive checkpoints once the
  WAL exceeds 64 MiB. Status now includes queue bytes, oldest queued age, last
  and maximum commit time, failure category, blocked reason, storage usage and
  the review due date.
- `market_archive_decisions.py` (new), `bot_worker.py`, `market_archive.py`: the
  evaluation freezes its inputs in memory; a bounded background writer (256
  items) stores the decision and its confirmation in one transaction after the
  execution commit, retrying contention. A failed execution commit is kept as
  explicitly unconfirmed. No archive I/O runs while the PostgreSQL transaction
  is open. Shutdown drains the queue within a bound and counts what was not
  written.
- `record_market_depth.py`, `depth_recording.py`: the capacity walk runs every
  15 minutes instead of every 30 seconds, and a slow or failed walk is reported
  and retried instead of ending the recorder. Between walks the budget is
  enforced by charging every byte written, free space is read from the volume
  on each check and review age uses the oldest file already seen. Capture stops
  only if no walk completes for six hours. The last result is cached so a
  restart does not block on a full walk.
- `recording_health.py` (new), `GET /api/recording/health`, worker log events
  `recording_health`, `recording_health_changed`, `recording_capture_health`:
  recording health is reported separately from trading health, with actionable
  problem codes. It never gates trading or readiness and runs off the trading
  thread.
- In the repository only (next release): the exit-study client fix, and the
  health report counts optional-quote losses for the current process only.

## Validation

Local: 3,228 backend tests passed, 20 skipped, before deployment. New tests in
`tests/test_recording_reliability.py` cover slow older captures, locked files,
startup faults, database locks, ambiguous commits, disk full, slow disk with
queue saturation, a stop during a fault, checkpointing, the decision writer
(slow disk, contention, failed commit, permanent failure, bounded shutdown),
the recorder's capacity monitor and the health report. Interrupted writes are
covered by the existing process-kill test.

## Deployment

- 17:47:59 UTC: depth recorder stopped with its STOP file, two files replaced
  (previous copies in `tools/depth-recorder-v1/before-recording-reliability-66/`),
  task restarted. Recording again at 17:51:18 after one 139.7 s initial walk.
- 17:55:38: run 15 stopped through `POST /api/bots/40/stop`.
- 17:55:46: provider preflight: simulated account `29593203`, 0 positions,
  0 working orders, 0 active runs.
- 17:56:02–17:57:04: release 65 stopped, task switched to release 66. The old
  backend again exceeded its 45-second graceful shutdown and the supervisor
  terminated it.
- 17:57:25: `/health`, `/health/worker?require_enabled=true` and `/ready` 200.
- 17:57:30: run 16 started through the practice start control.
- 18:03:33: provider check again: 0 positions, 0 working orders.

The stable configuration hash was identical before and after
(`c16ff51f…`): `dom_contrarian_v1`, one MNQ, 25-point stop, 25-point trail after
+25, $250 daily loss gate, four entries per day, practice auto-resume disabled.

## Measured after deployment

| Measurement | Before | After |
| --- | --- | --- |
| Capture time in `starting` | 206 s | about 3 s (2.5 s capacity, 0.2 s open, 0.2 s metadata) |
| Events dropped at capture start | more than 65,536 | 0 |
| Capture, first 5.5 minutes | — | 124,265 produced, 124,024 durable, 0 dropped, 955 batches, 0 failures |
| Capture commit time | — | last 0.297 s, maximum 0.328 s; oldest queued event 0.047 s |
| Archive work on the evaluation thread | p50 781 ms, max 1,953 ms | 0.0 ms enqueue (one evaluation) |
| Decision copy write (background) | — | 1 submitted, 1 written, 0.453 s, queue 0 |
| `market_archive_failed` since activation | — | none |
| Depth recorder | restarting every few minutes | one process since 17:48:58, 0 reconnects, status 3.6 s old |

Re-checked at 22:22 UTC, 4 hours 25 minutes after activation, same process:

- Worker capture: 3,672,500 events produced and 3,672,500 durable in 40,062
  batches; 0 dropped, 0 write failures. The slowest commit took 10.985 s and
  was absorbed by the queue. 237 background checkpoints kept the WAL bounded.
- Decision copies: 62 submitted, 62 written, none retried; slowest write
  2.813 s, off the evaluation thread.
- Broker events: 8 spool writes hit SQLite contention, went to the memory
  fallback and all 8 were recovered; backlog 0.
- Depth recorder: one process since 17:48:58 and one continuous segment run
  from 17:51:18 to 22:11:45 (earlier runs today lasted 11 to 60 minutes). Two
  reconnects at the exchange's daily close. A capacity walk took 370.8 s and
  the next one was flagged as stalled; the recorder kept recording.
- Bot: run 16 evaluated every five minutes until 21:05 UTC, the end of its
  trading window, as on the previous day. Account flat, no working orders.
  `/ready` returns 503 during the provider's daily maintenance while its hubs
  refuse connections; worker health stays 200.

These are observations from one afternoon, not a guarantee. They do not show
that any recording is complete.

## Backup coverage

- Market cache (worker evidence, depth recordings, market archive): the last
  verified off-device copy is from **November 4, 20:52 UTC**. Every attempt
  since has failed (`RuntimeError`, then `TimeoutExpired` on November 10).
  Recordings from November 4 onward exist only on the D: disk of peteamdpc.
- The scheduled attempt at 22:15 UTC today also failed, 71 seconds after it
  started. That matches the 60-second limit on the job's first step, which
  walks the whole cache directory to estimate size. Not confirmed, and not
  changed: the job runs from a private copy outside this repository.
- Execution database: the host backup ran November 9 (verified and published
  locally); its off-device status is `pending_archive_pull`, which depends on
  the same failing job.
- Failed snapshot attempts remain in `D:/TopSignal/archive-backup-staging`
  (four of about 9.4 GB each from November 10). Nothing was removed.

## Remaining limitations

- Worker evidence stops on **December 3, 2026** (30-day review boundary from the
  oldest capture, November 3) and the depth recorder on about **November 29**,
  until older recordings are reviewed and archived with a verified backup.
- A crash between the execution commit and the background archive write loses
  that archive copy. The decision row in PostgreSQL is unaffected.
- A crash or forced termination loses the queued, uncommitted evidence tail.
  The backend still exceeds its graceful shutdown deadline; cause not diagnosed.
- Depth recorder restarts are still gaps. Network omissions and events lost
  before receipt cannot be counted.
- The dashboard line for the worker feed shows "Unavailable" for the new
  `retrying` state; the full detail is in `/api/recording/health` and the log.
- The microstructure feature task and the history readers still walk every
  recording manifest on each run, which keeps load on the D: disk.
- The action journal is still written synchronously on D: before each broker
  request (about 0.5 s measured on November 3). This is the required durable
  intent write and was left as it is.
- `recording_healthy` on release 66 stays false while the quote spool's
  lifetime loss counter (409) is non-zero; corrected in the repository.

## Rollback

Backend: stop the run, confirm the account is flat with no working orders, then
point the `TopSignal Personal Device` task back at
`C:/ProgramData/TopSignal/releases/20261110-worker-recovery-65` (the saved task
definition is `staging/recording-reliability-66/host-task-before.xml`) using the
same stop/enable sequence as `activate.ps1`, and restart the run. Captures
written by release 66 stay readable by release 65; `capture-starts.json` is
ignored by it.

Recorder: create `runtime/depth-recorder/STOP`, wait for the process to exit,
copy the two files back from `before-recording-reliability-66/`, remove `STOP`
and start the `TopSignal Depth Recorder` task. `capacity-cache.json` is ignored
by the old version.
