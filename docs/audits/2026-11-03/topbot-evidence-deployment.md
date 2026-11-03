# TopBot worker evidence deployment — November 3, 2026

## Installed release and scope

Release `20261103-worker-evidence-52` became healthy at **18:19:13 Eastern**
(23:19:13 UTC). It preserves the separately deployed session-pause fix from
`20261103-session-pause-50`, the telemetry and commission fixes from release 49,
and adds the recorder-buffer and provider-health corrections.

The production service identity, local PostgreSQL execution authority, cloud
mirror, configured practice account, run and saved trading settings were
preserved. The broker confirmed no positions or working orders before the
switch and again after graceful shutdown. No diagnostic order was submitted.
The independent depth recorder and its scheduled recording/backup tasks
continued unchanged.

The release contains exact normalized worker-input capture with durable batch
hashes, code fingerprints, saved decision and pre-submit references, an offline
replay/timeline CLI, and dashboard execution/coverage details. Recorded timing
distinguishes durable intent writes, broker API requests, fills, observations
and verified stop changes. Runner costs use the existing shared commission
policy without rewriting historical execution or append-only audit records.

The queue now holds up to 4,096 events, retaining its 8 MiB byte bound. The
original 512-event queue dropped 39 events during the opening burst. That older
capture retains its loss report; missing events were not reconstructed.
Read-only provider probes continue while open positions or session pauses block
new entries. Failed probes retain error status and retry backoff.

Source fingerprint:
`51d71f20470e17374525458d345c6f52e8bf1516a201a280588be52e3e00d718`.

## Observed verification

A naturally generated practice trade supplied entry, eight broker-verified
trailing-stop changes, exit and final reconciliation evidence. Its capture
contains **90,587 durable events with zero drops**. An off-device copy passed
SHA-256, SQLite integrity, complete hash-chain and continuity checks. Replay
with matching source reproduced the saved entry observation exactly.

Representative recorded latencies:

| Measurement | Observed value |
| --- | --- |
| Entry API request | 160.537 ms |
| Durable intent write before request | 544.217 ms |
| Decision to submission | 1,238.036 ms |
| Qualification to submission | 2,731.988 ms |
| Fill to first observation | 10,768.037 ms |
| Fill to protection observation | 12,009.407 ms |
| Latest stop request to verified readback | 1,184.269 ms |

The completed record separately identifies broker fees, commission, total costs,
net outcome and slippage. Coverage explicitly records an initial 11.299-second
fill-to-tracking gap. Excursions remain observed values with incomplete coverage.
Provider/local clock offsets are unverified; signed cross-clock intervals are
retained with that qualification. Exit reconciliation included the deployment
interval and is distinguished from broker order-request duration.

Two release-52 decisions also replayed exactly against an off-device capture.
A later live sample reached **28,997 durable events without drops**.
At 18:23:25, frontend, API, readiness and enabled-worker checks all returned
HTTP 200. Four periodic provider probes were recorded. A final process check
confirmed the expected release running, both application listeners present and
no deployment STOP marker. The dashboard displayed active recording and the
new execution/coverage fields.

Cloud sync was caught up, and the critical broker-event spool drained to zero.

## Remaining research-archive limitation

Existing SQLite lock contention caused both archive-copy attempts for two
postdeployment decisions to time out. Those duplicate research-archive rows
are absent. The primary PostgreSQL decisions, worker captures and action
journal are intact, and exact replay was verified. Optional sampled quote
writes also reported contention. Critical broker events have a durable retry
spool; failed decision-copy writes currently do not. Recording is therefore
verified for the core evidence path, not every duplicate archive subsystem.

## Validation and storage

The curated telemetry/commission baseline passed 542 backend tests, seven
frontend tests, scoped ESLint and the TypeScript/Vite build. Final buffer,
provider-health and session integration passed 173 targeted tests; the 39-test
worker suite passed separately. The combined invocation exposed an existing
order-dependent offline-readiness fixture failure and was not counted as a
passing combined suite. A burst regression blocks the writer, enqueues 2,500
events and verifies that all 2,500 become durable without drops.

A fresh encrypted execution backup was generated, decrypted and verified on
the host, then copied off-device. Action-journal and worker-capture snapshots
also passed off-device checksum and SQLite integrity checks.

No migration, historical cloud import, billing change or data purge occurred.
Worker evidence remains on the backend filesystem under
`TOPSIGNAL_DATABENTO_CACHE_DIR/topbot-worker-evidence-v1/`.
The short evening sample used 3.82 MB compressed / 5.49 MB SQLite storage over
approximately 16 minutes, extrapolating to about 0.34 / 0.49 GB per day. Peak
rates may be higher. The existing 20 GiB budget, 10 GiB free-space floor and
30-day review boundary remain enforced without automatic deletion.

Complete account-specific receipts, encrypted backups, source baselines and
replay outputs remain in ignored local audit storage. Preserve matching source
releases and off-device data backups. Older trades cannot acquire timings that
were never recorded. Feed gaps, uncommitted tails and provider snapshot
limitations remain explicit; saved status from an ended process is historical.
