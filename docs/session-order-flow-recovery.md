# Automatic session order-flow recovery

The `TopSignal Session Order Flow` Windows task maintains the existing API's
session-order-flow cache on the recording host. It is independent of the browser
and viewing PC, uses the existing local trusted-device sign-in, and never arms,
stops or changes a bot. It needs no database credentials, broker credentials,
application imports or direct raw-archive access.

This helper was installed on peteamdpc on November 16, 2026 without restarting
application release 101 or the independent recorder. See the
[installation receipt](audits/2026-11-16/session-order-flow-maintainer.md).

## Behavior

- Starts one minute after host boot. Task Scheduler retries helper failure after
  one minute and has a five-minute repeating recovery trigger with `IgnoreNew`.
- Runs as the existing limited service user at scheduler priority 7. API and
  independent-recorder tasks remain at priority 4.
- Waits for API readiness, worker health, and a fresh independent-recorder status.
  It defers if recording is stopped, its capacity scan is stalled, storage is
  blocked, status is missing/malformed/stale, or free space is below 15 GiB.
- Reads the configured bot's contract regardless of its enabled/armed state.
  The configured account must match exactly. Missing/changed scope is explicit;
  there is no account fallback and no enrollment change.
- Uses a single sequential request to the existing session-flow endpoint.
  The existing reader retains its 12-second work budget, checksum checks,
  owner/contract boundaries and bounded local derived cache.
- Retries cold preparation every five seconds after a completed request, with
  a five-minute cooldown after 120 warming attempts. Healthy results refresh
  every minute; stale/closed-market results do not trigger rapid retries.
- Recovers from API unavailability and expired/revoked auth automatically.
  The token stays in memory, is reused until renewal is needed, and is revoked
  on graceful helper exit. Abrupt termination relies on normal server expiry.
  Proxy use and HTTP redirects are disabled; the API origin is fixed to loopback.
- Publishes one bounded status JSON, never access tokens or response dumps.
  `current` means a fresh response with no pending derivation; partial source
  coverage reasons are retained and are not upgraded to complete coverage.

This automates cold recovery rather than eliminating cold scanning. A restart
can still require several minutes of warming. Request work already admitted to
the API can run for its existing bound if recorder pressure begins afterward.
The helper does not guarantee exchange completeness or zero provider gaps.

## Files and installation

Source: `backend/tools/maintain_session_flow.py` (Python standard library only).
Installer: `scripts/install-session-flow-task.ps1`.

Prepare a new immutable helper directory containing the source, installer and
`config.json`, with explicit positive account/config IDs, API port 8000, the
protected recorder status path, the dedicated helper status path, and the
tested source SHA-256. For the current deployment:

```json
{
  "account_id": 29593203,
  "bot_config_id": 40,
  "api_port": 8000,
  "recorder_status": "C:/ProgramData/TopSignal/runtime/depth-recorder/status.json",
  "status_path": "C:/ProgramData/TopSignal/runtime/session-flow-maintainer/status.json",
  "source_sha256": "<SHA-256 of the tested maintain_session_flow.py>"
}
```

Invoke the installer with the helper directory, protected machine Python,
`RuntimeRoot`, existing service `Credential`, service identity and `StartNow`.
The runtime directory's leaf name must be `session-flow-maintainer` and must not
already exist. The installer refuses task replacement, mismatched source bytes,
reparse paths and administrator service identities. Only the new helper's ACLs
are set: executable/config files are read-only to the service user, while its
separate runtime directory is writable. Existing releases, task definitions and
recording permissions are untouched. The installer is not an application-release
activation command.

For future helper updates, use a reviewed new directory, verify its source/tests
and deliberately change only this helper's task after a graceful helper stop.
Do not overwrite the running helper or restart the application to update it.

## Inspection and stopping

Read `C:/ProgramData/TopSignal/runtime/session-flow-maintainer/status.json` and
the task state. Inspect both `at` and `last_success_at`; a preserved status file
from a stopped helper is not proof of a current process. `retry_seconds`,
`reason`, `coverage`, `through`, counters and helper PID explain its state.

To stop optional warming, disable only `TopSignal Session Order Flow` and create
`STOP` in that helper's runtime directory. It exits gracefully after the current
bounded HTTP call and revokes its own session. The STOP file prevents a scheduler
retry from doing work. For an authorized resume, remove that exact STOP file,
enable and start only this helper task. No bot or recorder action is involved.

Validation uses offline tests in `backend/tests/test_session_flow_maintainer.py`
plus the existing order-flow and local-auth suites. Restart/outage tests use an
isolated loopback server. Production acceptance must not reboot the host or
restart the API merely to exercise this optional helper during trading.
