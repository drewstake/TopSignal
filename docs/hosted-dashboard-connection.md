# Hosted dashboard connection

The production backend and frontend run on peteamdpc (`DESKTOP-6EUPDU1`).
The host's `TopSignal Personal Device` scheduled task starts at boot and
supervises both processes. Closing the browser or shutting down the viewing PC
does not stop the host. The host must remain powered on and connected.

On the viewing PC, open <http://127.0.0.1:4173/bot> and sign in to TopSignal.
Port 5174 is the development workspace, not the hosted dashboard.

The per-user `TopSignal Host Connection` task starts at Windows sign-in and
maintains the existing private SSH connection over Tailscale. It forwards only
local loopback ports 4173 and 8000 to the corresponding host loopback ports.
It retries after a lost connection, requires the existing trusted host key,
and uses the existing restricted SSH key. No public listener is created.
Tailscale must be connected. If a development server occupies either local
port, the connection waits for it to release the port.

The October 30 reliability update adds a one-minute repeating start trigger
with `IgnoreNew`: it recovers a stopped connection task without periodically
restarting a healthy one. Disable the task before stopping it if you want it to
remain off. Sign-out ends this per-user connection; the host keeps running.
See the [outage investigation and recovery runbook](outage-investigation-2026-10-30.md)
for evidence, limitations, activation state, and rollback.

## Install or repair the viewing-PC connection

Run as the Windows user who owns the existing SSH key:

```powershell
.\scripts\install-host-connection-task.ps1 `
  -RemoteTarget kaitl@100.99.71.73 `
  -KeyPath C:\Users\drews\.ssh\peteamdpc_ed25519
```

The installer copies the supervisor outside the checkout into the user's
`.ssh` directory. Its log is `peteamdpc-host-connection.log` there and rotates
at 1 MiB. The companion `.ssh` diagnostic log uses the same bound. The helper
`host-connection-job.ps1` must be installed beside the supervisor. A timestamped
`.log.status.json` records the latest HTTP observation; stale timestamps are
not current health. The task runs hidden, without administrator privileges or a time
limit. Disabling this connection task only stops automatic access from this
PC; it does not disable the production host task.

Installation updates the next-start files and does not stop a healthy running
instance. Use `-DoNotStart` when preparing a change without requesting a start.
The new supervisor checks both forwarded HTTP destinations, uses 5–60 second
reconnect backoff, and contains its SSH child in a Windows job. A failed
application probe is distinct from trading readiness; a single failed service
does not cause tunnel recycling. No public listener or authentication change
is introduced.

## October 30, 2026 deployment

Release `20261030-hosted-19` was staged from `20261030-dashboard-18` with only
the wider Bot layout, full-depth order-book UI, background depth connection,
bounded feed-reconnection fixes, and the existing Supabase sign-in build
configuration. Trading strategy, model, account configuration and execution
permissions were preserved. The prior release remains available for rollback
and for the separately installed depth recorder's Python runtime.

Once viewed, a contract's display book remains subscribed on the backend
after all viewers leave. Viewerless subscriptions do not write historical
observations to the database. A real provider disconnection or backend restart
still invalidates the display book and requires fresh market depth.

Validation: 84 backend feed tests and 24 order-book UI tests passed; the
production frontend build succeeded. Post-deployment checks verified the
installed model hashes, existing paper configuration, worker heartbeat,
backend readiness, frontend build identity and running depth-recorder task.
The viewing-PC connection recovered within 15 seconds after its SSH transport
was deliberately stopped. The browser reached the hosted sign-in screen;
authenticated browser validation requires the user's sign-in.

## Logs tab

The sidebar's Logs page (`/logs`) shows a bounded host event history and
market-feed connection changes for the signed-in user. The existing bot log
viewer below it remains scoped to the selected owned account. The endpoint
requires the normal API authentication and sends `Cache-Control: no-store`.
It does not require a working application database to read host history.

Host messages are mapped to fixed descriptions; raw supervisor output,
credentials, exception messages and other users' feed records are not returned.
History comes from existing rotating files on the host, with bounded reads
and no new cloud log table or retention expansion. Feed logging records state
changes only. The page labels missing sources and truncated history.

While visible, the page checks every 30 seconds. Failed checks mark the last
loaded history as stale and worker health as unknown; reauthentication clears
private history. Changing sign-in identity resets the view. If the host or
frontend is completely offline, the page may not open until connectivity
returns. This is a diagnostic viewer, not an external alerting service.

Release `20261030-logs-20` deployed this view on October 30. Validation passed
89 backend tests, three UI tests, and the production build. Post-deployment
checks confirmed actual host log sources, backend readiness, the existing
model/configuration and worker heartbeat. The signed-in Chrome session showed
host restart history, order-book and quote connection events, healthy worker
status, and correctly filtered errors. The separate recorder remained running.

## October 30 connection recovery release

`20261030-recovery-21` and the new viewing-PC supervisor were activated with
user approval at about 10:27–10:29 EDT. The host release offloads synchronous
stream-setup database work from the event loop, retains the preceding build's
public assets, records frontend HTTP status without query strings, and gives
actionable route-load errors. The independent recorder remained on its original
processes. Credentials, strategy and trading permissions were unchanged.

The first new viewer process exited without an identified reason; the periodic
trigger recovered it automatically. The replacement remained healthy through
the final checks. Signed-in Bot rendering, connected depth, both asset generations,
API health, worker health and readiness were verified. See the investigation
for the initial deployment retry, evidence, test timings and remaining unknowns.
