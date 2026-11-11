# One-second trade progress deployment — November 11, 2026

The user authorized deployment on peteamdpc, account enrollment, commit and push.
Release `20261111-trade-progress-75` runs commit
`732bacad869e252a11763a1e7853b337052d010b`. It contains the one-second recorder
(`1ca393f`) and the Windows backup-lock correction (`732baca`). The prior release,
`20261110-daily-pnl-73`, remains available for rollback.

## Active behavior and storage

Practice account `29593203` (`PRAC-V2-192577-85156978`) is enrolled. Manual and bot
positions on that account receive signed points and unrealized-dollar samples
approximately once per second while open, with separate lifecycle observations
and incoming-price extrema. Recording runs independently of the browser and bot
arm state. The other connected account was not trade-eligible and was not enrolled.

The API's effective destination was verified as
`D:/TopSignal/data/databento/trade-progress-v1/progress.sqlite3` on peteamdpc
(`DESKTOP-6EUPDU1`), D: Storage, SATA `ST2000DM006-2DM164`. The volume is
2,000,396,742,656 bytes; free space at 06:21:55 UTC was 1,094,975,991,808 bytes.
The existing limited `TopSignalSvc` identity has the required write permission.
Enrollment, health and all observations remain in local SQLite; this release
adds no cloud tables, historical imports or migrations.

Sizing remains a planning estimate: about 86.4 MB per continuously open
position-day at 1 KB/sample, plus lifecycle metadata and WAL/backup headroom.
The measured synthetic database used 49,201,152 bytes for 86,400 periodic samples
plus entry. Flat accounts do not generate position samples.

## Validation and activation

The clean committed source passed the full release workflow: 3,274 backend tests
(20 skipped, plus 8 subtests), 1,288 frontend tests, lint, 47 deployment script
tests, dependency audits and the production build. All six jobs in the
[exact-commit CI run](https://github.com/drewstake/TopSignal/actions/runs/42967288919)
succeeded before activation.

The initial CI attempt had two CSV UI wait timeouts and a Windows request-deadline
status mismatch. The affected application/test files were unchanged from the
preceding green commit; full local validation and focused reruns (6 CSV and 28
deadline tests) passed. Failed CI jobs were rerun once with identical source and
assertions and passed. Their precise transient cause was not established.

An initial activation attempt stopped at the broker exposure gate, leaving the
running service untouched. After the account became flat, bot configuration 40,
run 17 was stopped through its normal API at 06:01:40 UTC to prevent new entries
during the switch. It remains stopped with `manual_stop`; configuration `enabled`
is false. Strategy/risk parameters were verified unchanged. No broker orders were
placed, modified or cancelled by this work.

Activation completed at 06:03:46 UTC after a graceful shutdown. Release contents,
dependency inventory, frontend bytes, API/worker readiness, broker state, sidecar
task definitions and independent depth-recorder continuity passed the standard
checks. Enrollment was enabled at 06:04:05 UTC.

The first recorder connections repeatedly closed, and health correctly remained
unavailable. Bounded read-only probes succeeded, including concurrent connections
and a probe under the service identity. A controlled restart of the unchanged
release temporarily restored the running service's connection; the precise trigger
was not established. The restart repeated broker-flat, integrity, readiness and
recorder-continuity checks, completing at 06:20:51 UTC. Application and sidecar
task definitions and the independent recorder process were unchanged. The
temporary diagnostic task was removed after completion.

Authenticated status checks at 06:20:31, 06:21:10 and 06:21:55 UTC all reported
`enrolled=true`, `healthy=true`, `error=null`, `sample_seconds=1`, and advancing
heartbeats. A broker read at 06:21:34 UTC confirmed zero positions and zero
working orders with run 17 still stopped. Enrollment survived the restart.

This verifies readiness while flat, not an observed live trade trajectory. The
database contained no position samples at verification, and no test trade was
placed. Historical trades are not retroactively recorded. Future interruptions
must continue to appear as health failures and coverage gaps, not invented prices.

## Backup verification

Connection follow-up: after the initial status observations, another disconnect
and automatic recovery appeared in the logs. The shared runner lacked SignalR
protocol pings already used by the depth recorder. The source now adds them,
handles server Close frames immediately, and logs fixed local failure reasons.
The [SignalR configuration documentation](https://learn.microsoft.com/en-us/aspnet/core/signalr/configuration)
describes client protocol keepalives and server idle timeouts. A bounded live
comparison kept both a pinging and a non-pinging diagnostic connection open for
110 seconds, so it did not establish the cause of the observed production
failure. Four concurrent connections with a fresh token also succeeded.
All 132 focused hub/progress/route/order-book tests passed; follow-up release
validation and activation remain pending.

The deployed companion `backup_market_cache.py` has SHA-256
`92d8681b0a53b2335ccc8edf374f8e6534e04c9ee21d771858134d11cad59a56`.
It omits only the ephemeral `trade-progress-v1/progress.lock`; a regression test
holds the real Windows lock while snapshotting live SQLite WAL data. The helper
was updated before enrollment, preserving its ACL and retaining the old copy.
The existing recursive off-device backup schedule includes the new database;
its schedule and destination were not changed.

A consistent online snapshot was made while the recorder was running, copied
to the development PC, checksum-verified and restored into a separate read-only
review file. SQLite `integrity_check` returned `ok`, and the restored enrollment
was enabled with a healthy heartbeat. The snapshot database was 69,632 bytes,
SHA-256 `29444c8c1a197a64d3f1d1215c772774b3f7449bfe73f806cfa73c16daaf83e1`.

Off-device snapshot:
`backend/storage/backups/peteamdpc-trade-progress-20261111-healthy/`.
This was a focused backup of the new recorder database, not a claim that a new
full-cache scheduled backup completed during this deployment.

## Retained operational evidence

Ignored local evidence is under `tmp/releases/trade-progress-75-operations/`:
deployment receipts, initial enrollment, healthy status observations, broker
state, CI failure investigation, backup manifests and a restored SQLite copy.
The full validation receipt is
`tmp/releases/20261111-trade-progress-75-validation/validation.json`.

Host deployment receipts remain under
`C:/ProgramData/TopSignal/staging/20261111-trade-progress-75/`.
Controlled-restart receipts are under
`D:/TopSignal/maintenance/trade-progress-75/service-restart/`.
Private credentials and raw production recordings are not committed to Git.
