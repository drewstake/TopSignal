# Session order-flow recovery — November 16, 2026

The owner requested restoring the session order-flow panel without interrupting
the bot or recording. Recovery used the existing authenticated reader on
`peteamdpc`, release `20261115-recorder-sidecar-101`. No deployment, application
restart, recorder restart, trading change, enrollment change, schedule change,
archive rewrite or source-data deletion was performed.

## Finding

The account `29593203` session-order-flow endpoint repeatedly returned HTTP 503
after the 18:00 New York session open. Nonblocking stack samples showed its
request thread reading historical raw recording manifests in `Inventory.scan`.
The scan had not finished building its in-memory inventory. Overlapping requests
were rejected by the service's one-reader lock; this was a slow cold scan, not
an established deadlock. Examined-manifest counts advanced from 8,875 at
23:08:48 UTC to 10,702 at 23:10:01 and 15,132 at 23:12:35.

The persisted summary file's prior modification time was November 14 04:38 UTC.
That stale derived file was not evidence that raw market recording had stopped.
The independent recorder continued committing data throughout this incident.

## Recovery

From 23:09:22 to 23:13:56 UTC, a bounded diagnostic client made serialized
requests to the existing order-flow endpoint. Each reader attempt retained the
existing 12-second work budget, 128 MiB raw-read bound, 64 MiB derived SQLite
limit, source checksum/identity validation and owner/contract checks. The client
waited five seconds between completed attempts and was limited to 120 attempts;
it completed after 21. No separate archive scanner or backup job was started.

Before each request the client checked worker health, recorder freshness,
recording state, storage refusal/stall status and unchanged recorder PID/capture.
Available D: space was 1,290,615,828,480 bytes, above the 15 GiB admission floor;
the existing derived cache was 1,531,904 bytes and grew to 1,544,192 bytes.

At 23:13:46 UTC the endpoint returned HTTP 200 with partial catch-up:
six processed segments, eight pending. At 23:13:56 UTC it returned HTTP 200
in 4.360 seconds with 14 processed segments, zero pending, no coverage reasons,
`stale=false`, and 28,713 market trades through 23:12 UTC. These are provider
recordings, not a certification of exchange completeness or aggressor sides.
The diagnostic sign-in was revoked in the client's `finally` block.

Three follow-up requests at 23:14:15, 23:14:45 and 23:15:16 UTC all returned
HTTP 200 in 0.641, 0.094 and 0.812 seconds respectively, with zero pending
segments, no coverage reasons and `stale=false`. Complete-minute coverage
advanced from 23:13 to 23:14 UTC and trade count from 29,600 to 30,932.
The verification sign-in was also revoked. No browser refresh is required for
the visible panel's normal 30-second polling to retrieve these responses.

## Continuity and limits

The API processes remained PIDs 9968/9536 with unchanged creation timestamps.
Bot run 20 remained running on practice account 29593203 with fresh heartbeats,
no run error, and a new scheduled evaluation at 23:10:05 UTC. The independent
recorder remained PID 11372, capture `9d4a32c204e547bf91f83981852b6a3a`;
its durable index advanced from 30,391,216 at recovery start to 30,452,029 at
23:14:33 UTC. Its cumulative error count stayed at 19, with its last error
predating this work (22:14:26 UTC). Practice trade recording remained enabled
with fresh heartbeats and no scope error. This is sampled continuity evidence,
not a claim of complete historical coverage.

At 23:15:16 UTC practice diagnostics reported zero commit failures, retries,
discarded events or queue backlog and `coverage_ready=true`. Cumulative snapshot
failures were five (four in the earlier 23:03 health check), so this receipt
does not claim zero transient provider snapshot failures during the broader
observation period. The current snapshot and write timestamps were fresh.

This restores the current panel by completing the existing reader's cold scan;
it does not change the cold-start architecture. A future API process restart
can require inventory warming again. A persistent-inventory or startup change
requires a tested application release and is not represented as deployed here.
Full off-device recovery and prior maintenance acceptance remain separate work.

Local diagnostic sources and outputs are retained under
`tmp/health-20261116/` (Git-ignored). No production source files were modified.
