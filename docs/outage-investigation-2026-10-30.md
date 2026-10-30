# TopSignal outage investigation — October 30, 2026

All times below are **America/New_York (EDT, UTC−04:00)**. Collection began
around 09:42. This investigation used read-only production probes and separate
test tunnels. No production process, live tunnel, recorder, trading permission,
credential, database schema, billing setting, or historical-data policy was
changed during fault testing. After the user approved **“Activate both now,”**
the host release and viewing-PC supervisor were activated with the maintenance
interruptions recorded below. Final host verification was at 10:38:45;
final viewer verification was at 10:41:13.

## Findings

The reported Bot page incident was a **viewing-PC connection interruption**:
both local forwarded ports disappeared, and restarting `TopSignal Host
Connection` restored the existing asset and the application. The host's SSH
log narrows this interruption to **09:38:12–09:39:09**. The session established
at 08:59:07 used client source port 50541; at 09:38:12 the host logged SSH
disconnect reason 11, `disconnected by user`. The replacement connection began
at 09:39:09. This protocol message does not identify a human actor.

The supplied task result **3221225786 = 0xC000013A = STATUS_CONTROL_C_EXIT**
is Windows' control-C exit status. It does not prove a person pressed Ctrl+C,
identify the originating process, or establish that the host crashed.
[Microsoft's NTSTATUS definitions](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-erref/596a1078-e883-4972-9bbc-49e60bebca55)
support the code interpretation. Task history was disabled on both PCs, and
the original process ancestry was not retained. **The sender of the control
event remains unknown.**

The connection task already had unlimited runtime, battery allowances,
IgnoreNew, and 999 one-minute failure retries. Its only trigger was sign-in.
The manual recovery occurred less than one minute after the disconnect, so
the available evidence does **not** prove that its one-minute failure retry
malfunctioned. A periodic trigger adds independent stopped-task recovery.

Other failures are distinct:

* **Confirmed maintenance outages:** host Windows-update reboots and explicit
  operator STOP/start sequences during releases. They are not unexplained
  backend crashes.
* **Confirmed backend degradation:** local SQLAlchemy pool exhaustion (3
  persistent + 2 overflow connections, 10-second checkout timeout), API
  exceptions, and worker `lease_database_error` at approximately 09:31–09:38.
  Several liveness requests timed out, but no automatic backend restart appears
  in that interval. Health and readiness subsequently recovered on the same PID.
  The historical holders of all five connections were not captured; the
  initiating slow query, concurrency burst, or dependency failure is unresolved.
* **Verified code-level amplifier:** both async market-stream routes performed
  synchronous credential DB access on the event loop. A slow checkout could
  block unrelated async work. Isolated tests reproduce that risk and verify
  the repair. This is not proof that these routes caused the entire incident.
* **Confirmed stale-asset exposure before this repair:** the server returned 404 for
  `BotAnalysisPanel-BUG9P-09.js` and `BotAnalysisPanel-Cf3VhqGb.js`, and 200 for
  then-current `BotAnalysisPanel-Du-zkv_U.js`. Previous releases still exist on disk
  but were not served. Open tabs can therefore fail after a deployment.
  This does not explain missing local listeners in the reported incident.
* **Confirmed misleading UI:** every route failure claimed the application
  was still running without checking it, including dynamic-import failures.

## Verified architecture

| Layer | Configuration at initial collection, before approved activation |
| --- | --- |
| Viewing PC | Windows `PC`, user `drews`; last boot Oct 23 11:47:11; browser connects to `http://127.0.0.1:4173` |
| Connection task | Per-user, Limited, InteractiveToken, hidden Windows PowerShell 5.1; installed under `C:\Users\drews\.ssh` |
| Tunnel | OpenSSH `-N -T`; local loopback 4173/8000 → same host loopback ports; strict trusted-host checking, existing key, BatchMode, ExitOnForwardFailure; 15-second keepalive × 3 |
| Private network | Tailscale viewer `100.70.176.79` → host `100.99.71.73:22`; underlying network and both PCs must be available |
| Host | peteamdpc / `DESKTOP-6EUPDU1`; last boot Oct 29 15:48:52; SSH and Tailscale services Automatic/Running; auto-logon disabled |
| Production task | `TopSignal Personal Device`, dedicated non-admin `TopSignalSvc`, Password logon, boot trigger, unlimited runtime, IgnoreNew, 999 one-minute retries |
| Active release at collection | `C:\ProgramData\TopSignal\releases\20261030-logs-20`; immutable release, writable runtime separate |
| Backend | One Uvicorn worker, Python 3.12; supervisor PID 4308 → venv launcher 6540 → backend 1516, started 09:12:15 |
| Frontend | Python bounded static server, loopback-only, prebuilt Vite assets, no-store/security headers; venv launcher 14844 → frontend 7716 |
| Supervision | Exclusive runtime lock, kill-on-close Windows Job Object, bounded backoff and graceful shutdown; `/health`, `/health/worker?require_enabled=true`, and `/ready` have different meanings |
| Recorder | Independent `TopSignal Depth Recorder` task; Python launcher 10596 → recorder 16440, started 08:37:50; uses retained release `20261030-dashboard-18` runtime |
| Storage/dependencies | Supabase application DB/auth, ProjectX feeds, local runtime/filesystem data; no cloud candle cache introduced |

The current viewer supervisor's parent was Task Scheduler's `svchost.exe`
PID 2332, not the investigating shell. Python launcher/child pairs on the host
are normal Windows venv behavior, not duplicate backends. One owner held each
production port. No production occupied-port loop was found in the inspected
supervisor history.

Host AC sleep timeout is zero; battery sleep timeout is 900 seconds. At the
sample, the host had about 2.2 GiB free RAM of 7.9 GiB and 69 GiB free disk.
These are snapshots, not a resource-pressure history. No matching host sleep,
unexpected power-loss, or resource-exhaustion event was found in the inspected
Oct 28–30 window. This does not rule out unlogged network/dependency failures.

`ExitOnForwardFailure` covers establishing forwards, not reachability of the
ultimate destination. A running SSH PID/listener is insufficient evidence of
working HTTP service. [OpenSSH documentation](https://man.openbsd.org/ssh_config#ExitOnForwardFailure)

## Timeline and evidence

| Time (EDT) | Observation and evidence |
| --- | --- |
| Oct 29 15:19:34 | Host User32/System 1074: planned hotfix restart requested by `shutdown.exe`; comment references approved security update. OS booted 15:20:35. |
| Oct 29 15:45:18 | Host System 1074: another planned hotfix restart; boot 15:47:28. |
| Oct 29 15:48:25 | Host System 1074: `TrustedInstaller.exe`, planned OS upgrade restart; boot 15:48:52. |
| Oct 29 17:41:37 | Supervisor: persistent operator STOP latch suppressed startup. No evidence was retained to date the initial latch write or prove the task's state at each earlier reboot. |
| Oct 29 17:44:07 | Supervisor: backend/UI started after preflight/migrations. |
| Oct 29 17:52:16–18:01:28 | Supervisor: operator stop, then next start; release `paper-v2r1-17` created at 17:57:48. |
| Oct 29 18:56:23–Oct 30 06:43:21 | Viewing PC slept, then woke (System 42 and Power-Troubleshooter 1). Network reconnected; Tailscale changed network state again 06:45:04–06:45:05. This predates today's installed connection supervisor. |
| Oct 30 07:31:51–07:33:02 | Host supervisor operator stop/start surrounding `dashboard-18`. |
| 08:55:43 | Viewer connection log begins at its installed `.ssh` path. |
| 08:58:02–08:59:07 | Host records reset connections; viewer logs SSH exit −1 and subsequent reconnects. Earlier deployment notes describe an intentional tunnel recovery test; these records alone do not attribute each reset. |
| 08:58:34–08:59:41 | Host operator stop/start around `hosted-19`. |
| 09:10:38–09:12:15 | Liveness failure during maintenance, STOP logged 09:11:05; `logs-20` backend/UI start 09:12:15. |
| 09:31:46–09:38:31 | Readiness/worker warnings, intermittent liveness timeouts, QueuePool checkout errors and worker lease DB errors; frontend remained active. |
| 09:38:12 | Host OpenSSH event 4: connection from client port 50541 (established 08:59:07) sent disconnect reason 11. Viewer task reportedly stopped with 0xC000013A; no local listeners. |
| 09:39:08–09:39:09 | Viewer PowerShell engine start and connection-log reconnect. This restored the ports; reported browser/API/asset checks passed. |
| 09:39:16 / 09:39:38 | Host frontend log records requests for current BotAnalysisPanel chunk. Old frontend logs omit HTTP status, so these lines alone cannot establish success. |
| About 09:44 | Independent probes through the tunnel: frontend 200 (~112 ms), `/health` 200 (~130 ms), worker health 200 (~98 ms), `/ready` 200 (~1.88 s). |
| 09:54–10:07 | Isolated fault tests below; live tunnel remained PID 25192, host runtime and recorder were not restarted. |
| 10:02:35 | Viewer task gained a one-minute repeating trigger and updated next-start files; live task continued from its 09:39 start. |
| 10:24:21–10:25:26 | Approved activation began; host stopped gracefully at 10:24:27. Updating the Password-logon task action without supplying its existing credential failed with 0x8007052e. The unchanged `logs-20` action was restarted at 10:25:17; its backend/UI started 10:25:26 and health recovered. |
| 10:26–10:27 | Located the host's existing machine-protected deployment credential helper. An isolated task verified action registration with that unchanged credential; the probe task was removed. Activation was corrected to load and verify the credential before stopping production. |
| 10:27:20–10:27:34 | Approved retry: graceful stop completed 10:27:26; `recovery-21` task started immediately, with backend/UI processes at 10:27:34. Health, worker health and readiness subsequently returned 200. |
| 10:28:53–10:29:04 | New viewer supervisor started and both HTTP destinations were reachable. The old task stop had left SSH PID 25192 orphaned; its target, ports, original parent and start time were verified before terminating that exact child within the approved replacement. |
| After 10:29:04–10:29:36 | The first new supervisor PID 69264 disappeared without a logged exit reason; its SSH child was also gone. The periodic task trigger started replacement PID 89116 at 10:29:36; both probes passed at 10:29:47. The exit time/sender are unknown; 43 seconds separates the two successful observations, not a measured outage duration. |
| 10:37–10:38:45 | Signed-in Bot page rendered with live chart, connected depth and market analysis. Viewer remained PID 89116 / SSH 81484; host backend/UI remained 8012/8556. Host liveness, worker health and readiness were 200; original recorder PIDs 10596/16440 were unchanged. |
| 10:41:13 | Final viewer snapshot: same supervisor/SSH, one owner for both loopback ports, current and preceding UI chunks all 200. Replacement had survived more than 11 minutes and multiple completed launching/inspection sessions. |

Supervisor timestamps were converted from UTC to EDT. Python log times and
Windows host times were checked against `Eastern Standard Time`. Viewer
power events contain apparent resume records stamped before clock correction;
the Power-Troubleshooter event's embedded UTC sleep/wake times establish the
overnight interval, not that misleading intermediate timestamp.

Private evidence and original viewer task/script backups are under
`C:\Users\drews\AppData\Local\TopSignal\outage-investigation-20261030`.
Key sources: `connection-before.log`, `connection-task-before.xml`,
`viewer-system-events.json`, `viewer-connection-engine-events.json`,
`host-observations.txt`, `host-timeline-evidence.txt`, `test-evidence-complete`,
and the `activation` directory containing final health/process snapshots,
connection logs/status and host activation records. Host originals are
Windows System/OpenSSH logs and `C:\ProgramData\TopSignal\runtime\logs`.
Do not publish raw logs containing account details or private network names.

## Repairs and activation state

**Active on both PCs:**

* Added one-minute repeating start trigger to the existing viewer task, keeping
  InteractiveToken/Limited, IgnoreNew, no time limit, and existing failure
  retries. Sign-out still ends this user's access; sign-in starts it again.
  Sleeping PCs do not run timers; recovery resumes after wake/sign-in.
* Installed and activated the tested connection supervisor and helper. Its
  current process started from Task Scheduler at 10:29:36 and persisted after
  the launching command sessions ended, across subsequent periodic triggers.
* Enabled host Task Scheduler Operational history with its existing circular
  10 MiB limit. Viewer-side enablement returned Access denied; it remains off.

**Active connection behavior:** an SSH child without an inherited console,
kill-on-close containment, a cross-session file lock plus compatibility mutex,
PID/timestamp state logs, bounded native-stderr logs, and an atomic status file.
It checks both HTTP destinations every 10 seconds with three-second timeouts.
Three consecutive failures of both destinations recycle only its own transport;
an HTTP error or a single unreachable application does not. Reconnect waits
are 5, 10, 20, 40, then 60 seconds, resetting only after five healthy minutes.
Status `reachable` means these two HTTP probes succeeded, not trading health.
Occupied ports cause a 30-second wait without killing another process.

**Active host release:** `20261030-recovery-21`, based on the actual deployed
`logs-20`, includes only the stream credential offload, actionable route-load
errors, status-bearing frontend logs, and preceding-build public assets. It
does not include the checkout's unrelated risk-forecast, auth-development,
market-analysis, or layout changes. Backend `.env`, trading implementation,
dependencies and migrations are preserved. The frontend retains the exact
existing public Supabase configuration and loopback API URL.

The asset helper copies only the immediately preceding build's own public
assets, records a manifest, rejects conflicting names/unsafe paths, checks
space and a 128 MiB cap, and never deletes previous releases. This payload
adds 1,335,237 bytes of old assets. Tabs older than one preceding release may
need a manual reload. No automatic reload discards edits.

The route error view distinguishes an unsuccessful frontend probe from a
reachable frontend with a 404/410 chunk. It does not claim the backend or
trading is healthy. Generic render errors remain distinct.

## Validation

| Test | Result |
| --- | --- |
| Launch shell exits, scheduled isolated connection remains | Passed; healthy connection persisted across separate command sessions |
| Final SSH child killed at 09:59:06.17 | Replacement launched 09:59:11.20; both HTTP probes passed 09:59:21.39: **15.2 s** to observed recovery |
| Final isolated supervisor killed at about 09:59:52 | Its SSH child was gone within 0.5 s; watchdog launched 10:00:36.54; HTTP passed 10:00:46.80: **54.7 s** to observed recovery |
| Scheduled fixture exits with exactly 0xC000013A | Second execution at 09:55:11, about **51 s** after first exit; watchdog recovered it after launching shell ended |
| Duplicate launch while fixture owns ports/mutex | Exited 0; original supervisor retained ownership |
| Local test port occupied | `ports_occupied`, no SSH spawned; holder left alive. Released 10:04:18; connection launched 10:04:35; HTTP passed 10:04:45: **27.5 s** |
| SSH alive, forwarding destinations denied | HTTP state degraded; recycled after three probes (~30 s); observed retry progression through 60 s without parallel transports |
| SSH server refused connection | SSH exit 255; observed 5/10/20/40/60-second retry progression |
| Probe returns HTTP 503 vs no listener | Distinguished; a responding failed application is not labeled a dead transport |
| Old asset, missing asset, no-store/security behavior | Local HTTP test passed; old chunk 200 after carry-forward, missing chunk 404, no query secrets in status logs |
| Exact staged frontend on isolated host port 24177 | Index, preceding `Du-zkv_U` chunk, and new `BLDrTEgf` chunk all returned 200/no-store; isolated server then stopped |
| Native stderr flood and child containment | Log rotation bound and kill-on-close passed |
| Database wait during both stream setups | Event loop remained responsive; session opened/closed on the same worker thread before streaming |
| Checkout / exact staged release checks | 13 Windows operations/connection tests; 90 staged Python tests; 5 frontend error tests; staged production build passed |
| Activated production release | `/`, `/bot`, prior and new BotAnalysisPanel chunks, and main bundle returned 200 through the viewer tunnel; liveness, worker health and readiness returned 200 |
| Signed-in browser after activation | Bot page, chart, connected order book and market analysis rendered; no route-load error. No trading control was used. The chart also reported a candle-data gap; this is separate from successful transport/rendering and was not claimed repaired. |
| Unexpected first-launch exit after activation | Task recovered automatically at 10:29:36; contained SSH child did not remain orphaned. Replacement was healthy through 10:41, without duplicate port owners or further logged restarts |

The staged host also passed its production security preflight without starting
the application lifespan or worker. Its entire release/runtime ACL validation
passed. Hash comparisons confirmed an unchanged backend environment and only
`backend/app/main.py` changed under the deployed backend application tree.
The transferred overlay SHA-256 is
`8ce3e78aaaf8a905e3388938613344f63a6efeb9ec4ec1926e0330acf413cc0b`.

All temporary fault-test tasks were removed and their ports released. No
deliberate host reboot, sleep, or real network interruption was performed.
Live processes were replaced only during the approved activation, not for
fault injection. Timings are observations from these fixtures, not guarantees
during power loss or a real WAN outage.

## Activation outcome and uncertainties

The user approved both activations after the interruption and existing rearm
policy were explained. Host task action now targets `recovery-21`; its release
manifest records active status and the activation timestamp. The initial task
action update failed, the prior release was restored, and the approved retry
succeeded using the existing protected credential mechanism. No credential,
task identity, permissions, trading gates or strategy were changed. Production
evaluation/collection paused during those two graceful stop/start intervals;
the independent depth recorder never restarted. No provider-routing run was
rearmed. Existing restart safety behavior remains in force.

The first new viewer supervisor exited for an unidentified reason. Automatic
recovery worked in production, but this does **not** establish or remove the
source of the original control event. The replacement remained healthy across
multiple completed command sessions and one-minute triggers. Viewer Task
Scheduler history is still unavailable without administrator enablement.

Keep `logs-20` and the separate recorder's `dashboard-18` for rollback. A future
approved release switch must validate the task action, hashes, ACLs, health,
and availability of its existing protected deployment credential **before**
stopping production. Use the established helper on the host; do not print,
reset or copy its secret into command text. Password-logon task updates need
the existing credential even when the identity is unchanged. Use the graceful
stop mechanism and clear only the maintenance latch owned by that operation;
verify health, worker status, readiness and old/new chunks after startup.

Remaining unknowns: control-event sender; precise database connection holders
at 09:31–09:38; WAN/router behavior during unlogged incidents; historical CPU/
RAM pressure; browser errors without a timestamp/asset URL. No broad power,
firewall, SSH-security, DB pool-size, billing, or strategy change is justified
by the evidence collected. There is still no independent external outage
alert; a Logs page cannot report while the connection is down.

## Short recovery runbook

1. Record the time, exact browser error and asset URL before reloading. On the
   viewer, inspect task state and port ownership:

   ```powershell
   Get-ScheduledTask -TaskName 'TopSignal Host Connection'
   Get-ScheduledTaskInfo -TaskName 'TopSignal Host Connection'
   Get-NetTCPConnection -State Listen -LocalPort 4173,8000 -ErrorAction SilentlyContinue
   Get-Content "$env:USERPROFILE\.ssh\peteamdpc-host-connection.log" -Tail 30
   Get-Content "$env:USERPROFILE\.ssh\peteamdpc-host-connection.log.status.json"
   ```

   The status file is now active. Treat an
   observation older than 30 seconds as stale, not a current health claim.
   Do not judge health from `LastTaskResult` alone: after a repeating trigger
   was ignored, the task reported 2147946720 while still Running with the same
   healthy SSH PID. Check task state, timestamp, listener owner and HTTP together.
2. If the enabled connection task is stopped, the watchdog should retry within
   about one minute while signed in/awake. `Start-ScheduledTask -TaskName
   'TopSignal Host Connection'` requests immediate recovery. If ports belong to
   something else, identify that process; do not kill it blindly. To intentionally
   keep the connection stopped, **disable** the task before stopping it.
3. Probe `/` on 4173 and `/health`, `/health/worker?require_enabled=true`, and
   `/ready` on 8000 with short timeouts. A 200 from `/health` means the API can
   answer, not that the worker/database/provider is healthy. Preserve any 503
   reason. If only an old chunk is 404 and the frontend responds, reload after
   considering unsaved work.
4. If the connection cannot recover, check Tailscale and host power/network.
   Over the existing SSH connection, inspect `TopSignal Personal Device`, its
   supervisor/application logs, and the STOP latch. A host process failure is
   different from a dead viewer tunnel. Do not restart trading, clear STOP,
   rearm execution, or change permissions as a generic connectivity fix.
5. For the next incident, export the relevant Task Scheduler/System/OpenSSH
   events promptly. Viewer Task Scheduler history requires an administrator
   to run `wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true`;
   no reboot, security-policy relaxation or retention change is needed.

Rollback of the non-disruptive viewer task update uses the saved XML and
`maintain-host-connection-before.ps1`. Registering the old XML replaces its
future triggers; it need not stop the current connection. Keep the evidence
directory private. Host release rollback likewise requires an approved
restart, the existing protected service credential, and the original action saved on the host under
`C:\ProgramData\TopSignal\staging\outage-recovery-20261030\host-task-before.xml`.
