# TopSignal on a personal Windows laptop

This is the deployment procedure for TopSignal. Passing local tests does not
certify unattended trading: complete the staged evidence gates below first.
It uses one
dedicated, non-administrator local identity to run a loopback-only API,
closed-candle worker, and prebuilt control UI. The supervisor restarts the
whole child-process tree after failure or reboot and keeps code and the Python
environment read-only to that identity.

Do not deploy this trading runtime to a VPS, route it through a VPN, or expose
either loopback port to the network. Topstep currently requires API bots to run
from a personal device and prohibits VPS/VPN/remote-server routing; see
[TopstepX API Access](https://help.topstep.com/en/articles/11187768-topstepx-api-access).
Topstep also allows only one active TopstepX device/browser session at a time,
so do not leave TopstepX signed in elsewhere while this laptop is operating;
see [TopstepX platform access](https://help.topstep.com/en/articles/14434175-topstepx).
Recheck those rules before every rollout.

This runtime refuses provider accounts classified as non-simulated. Do not use
it for a Live Funded Account: Topstep's current
[Live Funded Account parameters](https://help.topstep.com/en/articles/10657969-live-funded-account-parameters)
prohibit automated ProjectX API strategies there. Begin with the Topstep
Practice environment; API Access does not provide a separate sandbox.

## 1. Prepare and build a release

Use a dedicated personal laptop with Windows device encryption, automatic time
synchronization, stable power and networking, and current security updates.
Review and test source in the development checkout, then build the release
copy before applying its read-only ACLs. Never point Task Scheduler at the Git
working tree. Transfer the reviewed local changes: a fresh origin/main clone
does not contain the uncommitted fixes from this audit.

From an elevated Windows PowerShell, create an immutable release copy. Adapt
the timestamp and source path, but keep the release and writable runtime roots
separate:

Install Python 3.12 for all users in a machine-wide, administrator-owned path
readable by the service identity (the example uses `C:\Program Files\Python312`).
A venv created from a developer's per-user Python still depends on that user's
base interpreter/standard library and may fail under the scheduled identity.
Install Node/npm for the build; the running task itself does not need Node.

```powershell
$sourceRoot = (Resolve-Path C:\Users\you\Development\TopSignal).Path
$releaseRoot = 'C:\ProgramData\TopSignal\releases\2026-09-04-01'
$runtimeRoot = 'C:\ProgramData\TopSignal\runtime'

New-Item -ItemType Directory -Path $releaseRoot, $runtimeRoot -Force | Out-Null
robocopy $sourceRoot $releaseRoot /E /XD .git node_modules .venv storage tmp __pycache__ .pytest_cache .mypy_cache .ruff_cache /XF .env .env.*
if ($LASTEXITCODE -gt 7) { throw "Release copy failed: robocopy exit $LASTEXITCODE" }

$machinePython = (Resolve-Path -LiteralPath 'C:\Program Files\Python312\python.exe').Path
& $machinePython -m venv "$releaseRoot\backend\.venv"
if ($LASTEXITCODE -ne 0) { throw 'Release Python environment creation failed' }
& "$releaseRoot\backend\.venv\Scripts\python.exe" -m pip install -r "$releaseRoot\backend\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed' }
npm.cmd --prefix "$releaseRoot\frontend" ci
if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' }
```

Keep the machine's PowerShell execution policy enabled. If a reviewed release
arrived with Mark-of-the-Web metadata, sign its PowerShell scripts or use
`Unblock-File` on those exact reviewed files before hardening; do not solve this
by adding an `ExecutionPolicy Bypass` task action.

Create `frontend/.env.production.local` in the release with the production
`VITE_SUPABASE_URL` and publishable `VITE_SUPABASE_ANON_KEY`. Do not put a
Supabase service-role key or provider credential in a `VITE_*` variable. Then
build the UI with the checked-in wrapper, which pins its API URL to loopback:

```powershell
powershell -NoProfile -File "$releaseRoot\scripts\build-production-frontend.ps1" -RepoRoot $releaseRoot -ApiPort 8000
if ($LASTEXITCODE -ne 0) { throw 'Frontend production build failed' }
$buildDependencies = (Resolve-Path -LiteralPath "$releaseRoot\frontend\node_modules").Path
if (-not $buildDependencies.StartsWith($releaseRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Build dependency path escaped release' }
Remove-Item -LiteralPath $buildDependencies -Recurse -Force
```

The wrapper also forces `VITE_DEMO_MODE=false` for the production artifact.

The supervisor serves the resulting `frontend/dist` with the Python standard
library server in `scripts/serve-production-frontend.py`. That server refuses
non-loopback binding and non-loopback `Host` headers, supplies SPA fallback and
restrictive browser headers, does not log query strings, and is health-checked
with the backend. Vite's dev and preview servers are not used in production.
The local control URL is `http://127.0.0.1:4173/`; configure that exact URL as
an allowed Supabase auth redirect when OAuth is enabled.

Create `backend/.env` in the release. Use real database, Supabase Auth, Fernet
credential-key, and ProjectX settings. Do not move plaintext secrets by email
or cloud notes. Preserve the Fernet key in a separate password manager/backup;
losing it makes stored ProjectX credentials unreadable. At minimum, set:

```dotenv
AUTH_REQUIRED=true
TOPSIGNAL_ENV=production
DATABASE_URL=postgresql+psycopg://<app-user>:<password>@<db-host>/<database>?sslmode=verify-full&sslrootcert=C:/ProgramData/TopSignal/releases/2026-09-04-01/certs/database-ca.pem
MIGRATION_DATABASE_URL=postgresql://<migration-user>:<password>@<direct-db-host>/<database>?sslmode=verify-full&sslrootcert=C:/ProgramData/TopSignal/releases/2026-09-04-01/certs/database-ca.pem
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_JWT_AUDIENCE=authenticated
CREDENTIALS_ENCRYPTION_KEY=<your-valid-fernet-key>
PROJECTX_API_BASE_URL=https://api.topstepx.com
ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS=false
ALLOW_INSECURE_LOCAL_CREDENTIALS_KEY=false
ALLOW_QUERY_BEARER_TOKENS=false
ALLOWED_ORIGINS=http://127.0.0.1:4173
ALLOWED_ORIGIN_REGEX=
TOPSIGNAL_DB_SCHEMA_INIT=skip
TOPSIGNAL_BOT_WORKER_ENABLED=true
TOPSIGNAL_LIVE_EXECUTION_ENABLED=false
TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION=false
PROJECTX_USER_HUB_URL=https://rtc.topstepx.com/hubs/user
TOPSIGNAL_DATABENTO_CACHE_DIR=C:\ProgramData\TopSignal\runtime\data\databento
JOURNAL_IMAGE_STORAGE_DIR=C:\ProgramData\TopSignal\runtime\data\journal_images
JOURNAL_IMAGE_STORAGE_BACKEND=local
```

Obtain the database provider's current CA certificate through its trusted
dashboard/documentation, verify its provenance, and put the file at the exact
`sslrootcert` path in the immutable release before preflight. The app checks that
the file is absolute, readable and nonempty; libpq checks the certificate chain
and database hostname when connecting. A fixture path or arbitrary nonempty file
is not proof of trusted TLS. Update this path when changing release versions.
URL-encode passwords and certificate paths containing reserved characters or spaces.

For remote production PostgreSQL, both application and migration URLs must set
`sslmode=verify-full` plus explicit `sslrootcert`; `prefer`, `require`, and
`verify-ca` are rejected. This prevents plaintext fallback and verifies the
server hostname. [PostgreSQL's SSL documentation](https://www.postgresql.org/docs/current/libpq-ssl.html)
explains why the default `prefer` is unsuitable here. Connection keyword
overrides force `gssencmode=disable` so GSS negotiation cannot replace required
TLS. Exact loopback hosts alone may omit TLS. Host/hostaddr/service query
overrides, duplicated URL options, and ambient `PGHOSTADDR`, `PGSERVICE`, or
`PGSERVICEFILE` are rejected before engine creation or migrations; put the
single intended host and database in each URL. The effective scheduled-task
environment must not contain those overrides.

Never set `AUTH_REQUIRED=false` for this control path. The two live flags are
independent and default to false. A live configuration and a separately
confirmed continuous start are also required; environment flags alone do not
create or resume a run.

Before moving machines, verify that this user already has decryptable ProjectX
credentials in the shared database. Use authenticated per-user encrypted
credentials and `ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS=false` for this deployment.
The local development legacy credential escape hatch is not part of this runbook.

## 2. Create a least-privileged task identity

Create a unique local account. It must not be an Administrator and must not be
used for interactive browsing or development:

```powershell
$servicePassword = Read-Host 'Strong unique password for TopSignalSvc' -AsSecureString
New-LocalUser -Name TopSignalSvc -Password $servicePassword -AccountNeverExpires -UserMayNotChangePassword -Description 'TopSignal scheduled task only'
```

In `secpol.msc`, grant this account **Log on as a batch job** and deny it
**Log on locally** and **Log on through Remote Desktop Services**. Do not grant
it administrative membership. Task Scheduler stores the password using Windows
credential protection; when the password is rotated, rerun the installer with
`-Force` and the new credential.

## 3. Harden and verify filesystem permissions

Back up the original ACLs to an administrator-only directory before changing
them:

```powershell
$aclBackupRoot = 'C:\ProgramData\TopSignal\acl-backups'
New-Item -ItemType Directory -Path $aclBackupRoot -Force | Out-Null
icacls.exe $aclBackupRoot /grant:r 'BUILTIN\Administrators:(OI)(CI)(F)' 'NT AUTHORITY\SYSTEM:(OI)(CI)(F)'
if ($LASTEXITCODE -ne 0) { throw 'Could not grant ACL-backup recovery access' }
icacls.exe $aclBackupRoot /inheritance:r
if ($LASTEXITCODE -ne 0) { throw 'Could not protect the ACL-backup directory' }
icacls.exe $aclBackupRoot /remove:g 'BUILTIN\Users' 'NT AUTHORITY\Authenticated Users' 'Everyone'
if ($LASTEXITCODE -ne 0) { throw 'Could not remove broad ACL-backup access' }
icacls.exe $releaseRoot /save "$aclBackupRoot\release.acl" /T /C
icacls.exe $runtimeRoot /save "$aclBackupRoot\runtime.acl" /T /C
```

Use braces around the PowerShell variable below: without them, the colon after
the account name is parsed incorrectly. Every sequence grants the replacement
permissions first and only then removes inheritance, so an interrupted command
does not strand the tree without an administrative recovery ACE.

```powershell
$servicePrincipal = "$env:COMPUTERNAME\TopSignalSvc"
$administrators = 'BUILTIN\Administrators'
$system = 'NT AUTHORITY\SYSTEM'
$environmentFile = Join-Path $releaseRoot 'backend\.env'

# Every release file, including backend\.venv and frontend\dist, is read/execute
# only for the service identity. Administrators and SYSTEM retain recovery access.
icacls.exe $releaseRoot /grant:r "${servicePrincipal}:(OI)(CI)(RX)" "${administrators}:(OI)(CI)(F)" "${system}:(OI)(CI)(F)" /T /C
if ($LASTEXITCODE -ne 0) { throw 'Could not grant release ACLs' }
icacls.exe $releaseRoot /inheritance:r /T /C
if ($LASTEXITCODE -ne 0) { throw 'Could not protect release ACLs' }
icacls.exe $releaseRoot /remove:g 'BUILTIN\Users' 'NT AUTHORITY\Authenticated Users' 'Everyone' /T /C
if ($LASTEXITCODE -ne 0) { throw 'Could not remove broad release ACLs' }

# The service can read, but cannot replace or edit, the environment file.
icacls.exe $environmentFile /grant:r "${servicePrincipal}:(R)" "${administrators}:(F)" "${system}:(F)"
if ($LASTEXITCODE -ne 0) { throw 'Could not grant environment-file ACLs' }
icacls.exe $environmentFile /inheritance:r
if ($LASTEXITCODE -ne 0) { throw 'Could not protect environment-file ACLs' }

# Logs, lock/config state, Databento cache, and optional local journal images are
# the only service-writable tree. This is separate from executable code.
icacls.exe $runtimeRoot /grant:r "${servicePrincipal}:(OI)(CI)(M)" "${administrators}:(OI)(CI)(F)" "${system}:(OI)(CI)(F)" /T /C
if ($LASTEXITCODE -ne 0) { throw 'Could not grant runtime ACLs' }
icacls.exe $runtimeRoot /inheritance:r /T /C
if ($LASTEXITCODE -ne 0) { throw 'Could not protect runtime ACLs' }
icacls.exe $runtimeRoot /remove:g 'BUILTIN\Users' 'NT AUTHORITY\Authenticated Users' 'Everyone' /T /C
if ($LASTEXITCODE -ne 0) { throw 'Could not remove broad runtime ACLs' }
```

Review rather than assume the result:

```powershell
(Get-Acl $releaseRoot).AreAccessRulesProtected
(Get-Acl $environmentFile).AreAccessRulesProtected
(Get-Acl $runtimeRoot).AreAccessRulesProtected
icacls.exe $releaseRoot
icacls.exe "$releaseRoot\backend\.venv\Scripts\python.exe"
icacls.exe $environmentFile
icacls.exe $runtimeRoot
```

All three `AreAccessRulesProtected` values must be `True`. The service identity
must have only `RX` on the release and virtual environment, only `R` on `.env`,
and `M` on the runtime tree. It must not own any release content. The installer
independently walks the whole release and refuses any service/broad write grant,
service-owned release item, any release/runtime reparse point, or unprotected
root, environment file, or runtime root. The environment file and every existing
runtime item may grant access only to the service SID, Administrators, and
SYSTEM; additional per-user or broad read grants are rejected as credential
exposure. Review ACLs again after copying secrets or restoring runtime data.

If an ACL command fails, do not improvise additional broad grants. The safest
recovery for a versioned release is to delete and recreate that release as an
administrator. To restore the saved ACLs instead, keep the backup outside the
target and use the parent directory expected by `icacls /restore`:

```powershell
icacls.exe (Split-Path $releaseRoot -Parent) /restore "$aclBackupRoot\release.acl" /C
icacls.exe (Split-Path $runtimeRoot -Parent) /restore "$aclBackupRoot\runtime.acl" /C
```

## 4. Migrate and prove the release

Choose the database preparation path before any unattended launch. Keep all
workers stopped and take a verified backup before modifying an existing database:

- **Existing database with a migration ledger:** run the checked migration and
  check commands below. A checksum mismatch requires investigation, never edits
  to the ledger to silence it.
- **New, verified empty database:** apply the reviewed `db/schema.sql` once,
  then run `npm.cmd run db:baseline` and `npm.cmd run db:check`. Do not replay
  the historical incremental migrations onto an empty database without the base
  schema. Configure a private PostgreSQL service file for `psql`; confirm its
  target matches the release's `MIGRATION_DATABASE_URL`. For a remote target,
  that service must also set `sslmode=verify-full`, the reviewed `sslrootcert`,
  and `gssencmode=disable` before running:

  ```powershell
  $env:PGSERVICE = 'topsignal-release-migration'
  psql --set ON_ERROR_STOP=1 --single-transaction --file "$releaseRoot\db\schema.sql"
  if ($LASTEXITCODE -ne 0) { throw 'Fresh schema initialization failed' }
  Remove-Item -LiteralPath Env:PGSERVICE -ErrorAction SilentlyContinue
  Set-Location $releaseRoot
  $env:TOPSIGNAL_ENV = 'production'
  npm.cmd run db:baseline
  if ($LASTEXITCODE -ne 0) { throw 'Schema baseline validation failed' }
  npm.cmd run db:check
  if ($LASTEXITCODE -ne 0) { throw 'Schema check failed' }
  ```

- **Populated database without a ledger:** stop here until a backup restore has
  been verified and its schema reviewed. The explicit `db:adopt-current` command
  performs stricter populated-schema validation and security migration; it is
  not an automatic repair option. Never use `db:baseline` to bypass adoption.

For the ordinary existing-ledger update, run from an administrator shell:

```powershell
Set-Location $releaseRoot
# Ensure manual tooling enforces the same policy as the supervisor:
$env:TOPSIGNAL_ENV = 'production'
npm.cmd run db:migrate
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed' }
npm.cmd run db:check
if ($LASTEXITCODE -ne 0) { throw 'Database schema check failed' }
```

For a manual dry-run smoke test, supply the separate runtime root explicitly.
Use the persistent stop procedure in section 8 afterward. This operator-shell
smoke test is not evidence that the dedicated task identity can run the release;
the task launch in section 5 proves that separately. Do not remove the service
account's interactive-logon restrictions to perform this test:

```powershell
powershell -NoProfile -File .\scripts\run-production.ps1 -RepoRoot $releaseRoot -RuntimeRoot $runtimeRoot
```

In a second window, verify the API and UI:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
Invoke-WebRequest http://127.0.0.1:4173/ -UseBasicParsing
```

`/health` is process liveness. `/ready` also fails closed for a bad schema,
missing/stale worker lease, an unarmed enabled configuration, provider
degradation, stale/real-account classification, or an unresolved live
submission. The authenticated `GET /api/bots/runtime/status` endpoint shows
safe checks and counts without exposing credentials.

Let at least several complete trading sessions run in dry-run mode. Exercise a
network disconnect, provider rejection, laptop reboot, and database outage.
Compare every decision and attempted order with provider state before
considering either live flag.

## 5. Install automatic startup

Stop the manual supervisor with section 8's script and verify no listeners. In
elevated PowerShell, install from the hardened release without starting it yet.
The installer prompts for the dedicated local account password:

```powershell
powershell -NoProfile -File "$releaseRoot\scripts\install-windows-startup-task.ps1" -RepoRoot $releaseRoot -RuntimeRoot $runtimeRoot -ServiceUser "$env:COMPUTERNAME\TopSignalSvc"
if ($LASTEXITCODE -ne 0) { throw 'Task installation failed' }
```

The manual stop left a persistent STOP latch. Confirm both execution gates are
false and the selected bot is dry-run, then explicitly clear that maintenance
latch and start the task for its limited-identity acceptance test:

```powershell
Remove-Item -LiteralPath "$runtimeRoot\STOP" -Force -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName 'TopSignal Personal Device'
```

The installer rejects Git checkouts, administrator/built-in identities,
service-writable release content, missing frontend builds, and unsafe ACLs. It
uses the actual Windows PowerShell 5.1 or PowerShell 7 executable that invoked
it, creates a password-logon task at `Limited` run level, and refuses to replace
an existing task unless `-Force` is explicit. It never uses `SYSTEM` or
`Highest`.

The supervisor first runs a production-security preflight and rejects
`AUTH_REQUIRED=false` even for an otherwise local-only configuration. It also
requires PostgreSQL/psycopg, a valid Fernet key, an HTTPS (or explicit local)
authentication issuer and audience, disabled insecure/query-token/shared-credential
escape hatches, and schema compatibility initialization disabled. It then
applies migrations, starts one Uvicorn worker and the static UI, and probes both
every ten seconds. Three liveness failures restart both. After a two-minute
startup grace, it also calls the narrow unauthenticated
`GET /health/worker?require_enabled=true` probe: only a worker-specific failure
that persists for five minutes gets one bounded restart, and the probe must
recover before another worker-health restart is allowed. Generic `/ready`
failures are logged for the operator but never cause restarts, because states
such as an unarmed configuration, provider degradation, or account
classification need reconciliation rather than a process loop. The exclusive
runtime lock prevents a second supervisor from creating an occupied-port restart
loop. A Windows kill-on-close Job Object contains migration, API, and UI
processes so an abrupt supervisor exit cannot orphan them; orderly restarts also
request graceful Uvicorn shutdown through the private runtime file, allow up to
45 seconds for cleanup, then use Windows tree termination if needed. Security
preflight and migration subprocesses each have a five-minute deadline; failure
prevents backend launch. Process handles are disposed after each cycle. Before each cycle, occupied loopback
ports cause the supervisor to wait rather than spin a conflicting restart loop.
In addition,
`PYTHONDONTWRITEBYTECODE=1` prevents import caches from mutating the release or
virtual environment.

Verify the installed task and its least-privileged principal:

```powershell
$task = Get-ScheduledTask -TaskName 'TopSignal Personal Device'
$task.Principal | Format-List UserId, LogonType, RunLevel
Get-ScheduledTaskInfo -TaskName 'TopSignal Personal Device'
Invoke-RestMethod http://127.0.0.1:8000/ready
Invoke-WebRequest http://127.0.0.1:4173/ -UseBasicParsing
```

## 6. Power and operating controls

A sleeping laptop cannot receive market data, renew the worker lease, or
reconcile an ambiguous order. From elevated PowerShell:

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

Set closing the lid to **Do nothing** while plugged in. Turning off the display
is fine. Keep Windows Update enabled with trading-aware active hours; the startup
task restores the process after a reboot.

Application state is under `C:\ProgramData\TopSignal\runtime` by default:

- `logs\topsignal.log`: current file plus 14 rotated files, each up to 10 MiB
- `logs\access.log`: current file plus 7 rotated files, each up to 10 MiB
- `logs\frontend-access.log`: current file plus five rotated files, each up to 10 MiB
- `logs\supervisor.log`: current file plus five rotated files, each up to 10 MiB
- `supervisor.lock`: cross-session single-supervisor lock
- `STOP`: persistent operator latch, suppressing startup and restart until explicitly removed
- `shutdown.request`: transient graceful-shutdown request used by the supervisor

These four rotating streams use approximately 350 MiB maximum in total (a
single oversized log record can exceed its file threshold). Startup check and
migration stdout/stderr logs are overwritten per supervisor start. API access
logs retain paths and status codes, dropping query strings; application log
formatting redacts conventional credential fields, bearer tokens, and URL
userinfo/query strings. Never log raw provider payloads or paste credentials
into diagnostics; format redaction cannot recognize every arbitrary secret.

These files are local diagnostic indicators only. The repository does not send
email, SMS, push, or remote monitoring alerts. Repeated `bot_worker_*`,
`projectx_*`, `submission_unknown`, stale-classification, or readiness messages
will not notify you unless you separately deploy a secure external monitoring
system. Check the task and logs at the start and end of every trading session;
do not call this setup unattended-safe until an independent alert path and its
failure drill have been proven.

The ordinary **Stop** action is local-only: it prevents later evaluations but
does not claim to cancel or flatten broker state. Use the separately confirmed
emergency-flatten control when broker cancellation and verified flat state are
intended. A `409` means automation is stopped but the provider was not proven
flat and needs manual inspection.

Back up PostgreSQL and the Fernet key separately. `pg_dump` does not encrypt its
archive: the destination must be a verified encrypted volume/container with
private ACLs; a directory named `EncryptedBackups` alone provides no protection.
For a direct or session-mode PostgreSQL connection:

```powershell
# Configure private PostgreSQL service/pgpass files first. Never put a
# credential-bearing database URL on a command line or in shell history.
# For a remote database, that service must set sslmode=verify-full,
# sslrootcert to the reviewed CA file, and gssencmode=disable.
$env:PGSERVICE = 'topsignal-backup'
pg_dump --format=custom --file D:\EncryptedBackups\topsignal.dump
if ($LASTEXITCODE -ne 0) { throw 'Database backup failed' }
pg_restore --list D:\EncryptedBackups\topsignal.dump
if ($LASTEXITCODE -ne 0) { throw 'Backup archive validation failed' }
Remove-Item -LiteralPath Env:PGSERVICE -ErrorAction SilentlyContinue
```

Never use the Supabase transaction-pooler port for backup or migrations. Test
`pg_restore --list` after every backup and restore into a separate database
before relying on it. A database backup without the exact Fernet key cannot
recover stored provider credentials.

Historical Databento archives are separate local files, excluded from Git and
database backups. Transfer them with the app using the
[history transfer instructions](databento-history-transfer.md), retain the
original OHLCV and Definition ZIPs, and rebuild the cache at its final paths on
the new laptop. The saved OHLCV download can be reused without fetching it from
Databento again.

After ownership acquisition (process restart, reboot, or lease takeover), only
dry-run continuous runs may automatically resume. Every provider-routing run,
including practice execution, is disarmed with `worker_restart_requires_rearm`.
The operator must reconcile account/orders/positions and explicitly start it
again. Stopped, blocked, and errored runs never resurrect. Unresolved submissions
are reconciled before another evaluation, and only one deployment-wide worker
can hold the database lease. A process stop never proves the account is flat.

## 7. Exact monitoring and response thresholds

Keep ports 8000 and 4173 on loopback. Run read-only checks from an independently
supervised local monitor, sending a heartbeat outward to an approved monitoring
service. Set the remote missing-heartbeat alarm to 90 seconds; prove delivery
to an operator when the entire laptop or its network is off. No alert service
is installed by this repository; choosing/provisioning it remains a deployment
blocker. Do not put bearer tokens in monitoring URLs or logs.

| Check | Interval and alert threshold | Operator action |
| --- | --- | --- |
| `GET /health` on port 8000 | 10 seconds; alert after 3 failures | Inspect task/supervisor log; keep trading stopped during diagnosis. |
| `GET /health/worker?require_enabled=true` | 10 seconds after 120-second startup grace; alert after 3 failures | Inspect lease/heartbeat/database. Do not start another worker to compensate. |
| `GET /ready` | 10 seconds; alert after 3 failures whenever operation is expected | Read reason/check fields and authenticated runtime status; never bypass a failing check. |
| UI `GET /` on port 4173 | 30 seconds; alert after 2 failures | Use the local stop script and direct provider exposure controls if UI is unavailable. |
| Runtime disk free space | 60 seconds; warn below 10 GiB, stop/investigate below 5 GiB | Preserve evidence before archival. |
| Memory, CPU, OS clock sync | 60 seconds; sustained growth across equal workload or clock drift over 1 second | Stop/review resources/time sync before restarting. |
| `submission_unknown`, `bot_worker_shutdown_incomplete`, `worker_restart_requires_rearm`, critical errors | Immediate alert | Stop and reconcile provider state; never blindly retry ambiguous submissions. |

Health endpoints are read-only. Detailed per-user `GET /api/bots/runtime/status`
requires the signed-in control UI or a securely stored authenticated session.
Capture a 503's safe reason/check fields before changing anything. An
intentionally stopped/unarmed bot can keep readiness degraded: record a
maintenance window rather than restarting indefinitely.

Readiness reasons include `database_unavailable`, `schema_outdated`,
`worker_not_started`, and `bot_runtime_not_ready`, with safe `failed_checks`
codes rather than account identifiers. PostgreSQL sessions use a 10-second
connection timeout, a 10-second pool wait, 30-second statement timeout,
5-second lock timeout, 60-second idle-transaction timeout, and TCP keepalives.
These bound failures; they do not prove external database availability.
Migration connections have a 10-second connect timeout, 15-second lock timeout,
and 5-minute statement timeout, with the supervisor's overall startup deadline.
Both production database connections enforce verified remote TLS before opening
a connection. Invalid URL/TLS configuration is reported without including the
URL/password; certificate/hostname failures remain a target-database acceptance
check, never a reason to weaken `sslmode`.
`failed_checks` lists only checks required for the current workload; disabled
live gates are expected in dry-run and are not a reason to enable routing.

Configured broker endpoints require TLS and reject embedded credentials, query
strings, and fragments; authenticated HTTP/WebSocket transports reject redirects.
SignalR adds its required access token to the outgoing WebSocket query at runtime;
never log or copy that generated URL. Unexpected transport/response failures after submission
remain ambiguous until reconciliation. Streaming MAE/MFE telemetry can have gaps
after disconnects/restarts; authoritative broker fills and reconciled P&L remain
the safety input. Legacy metrics return `metrics_pnl_incomplete` if authoritative
closed-trade P&L is absent, rather than estimating futures dollars from points.

Gemini and Supabase storage also reject redirects, URL userinfo, query strings,
and fragments. Gemini requires HTTPS, limits successful responses to 4 MiB and
error bodies to 16 KiB, and caps attempts at five. Storage downloads are limited
to 10 MiB; HTTP storage is permitted only for explicit
`TOPSIGNAL_ENV=development`/`test` and an exact loopback hostname. The production
launcher forces `TOPSIGNAL_ENV=production`, so production storage must use HTTPS.
Read deadlines are checked between bounded reads; DNS/socket behavior still
requires target-machine fault testing before claiming a strict overall deadline.

Manual checks, with no provider order action:

```powershell
Get-ScheduledTaskInfo -TaskName 'TopSignal Personal Device'
Invoke-RestMethod 'http://127.0.0.1:8000/health'
Invoke-RestMethod 'http://127.0.0.1:8000/health/worker?require_enabled=true'
# PowerShell 7 retains a 503 body:
Invoke-WebRequest 'http://127.0.0.1:8000/ready' -SkipHttpErrorCheck | Select-Object StatusCode, Content
Get-Content -LiteralPath "$runtimeRoot\logs\supervisor.log" -Tail 80
Get-Content -LiteralPath "$runtimeRoot\logs\topsignal.log" -Tail 100
Get-Volume | Select-Object DriveLetter, SizeRemaining
w32tm /query /status
```

On Windows PowerShell 5.1, use `Invoke-WebRequest` inside `try/catch` and inspect
`$_.ErrorDetails.Message` for a 503 body instead of `-SkipHttpErrorCheck`.

## 8. Emergency stop and verified flatten

1. In the authenticated local UI, select the exact account/bot and click
   **Stop Automation**. This stops later evaluations; it does not cancel or flatten.
2. If exposure must be removed and provider connectivity is working, use the
   separately confirmed **Emergency: Flatten Account …** control for that account. This is
   an actual broker action requiring the operator's decision. Success requires
   verified zero open orders and positions. A `409`, timeout, or unavailable
   provider leaves flatness unverified; do not blindly retry.
3. Stop all local automation and prevent reboot recovery using elevated PowerShell:

   ```powershell
   powershell -NoProfile -File "$releaseRoot\scripts\stop-production.ps1" -RuntimeRoot $runtimeRoot
   Test-Path -LiteralPath "$runtimeRoot\STOP"
   Get-ScheduledTask -TaskName 'TopSignal Personal Device' | Select-Object State
   Get-NetTCPConnection -State Listen -LocalPort 8000,4173 -ErrorAction SilentlyContinue
   ```

   Expected: STOP is `True`, task disabled/not running, no listeners after
   cleanup. The script writes STOP before disabling the task, requests graceful
   shutdown, and forces task termination only after 60 seconds. It never routes
   broker orders. If the UI is unavailable, perform this step immediately and
   inspect exposure directly with the provider.
   If the script errors or warns, keep STOP set and verify the task, supervisor
   lock and listeners independently; a requested termination is not an exit
   acknowledgement. A hung manual supervisor requires terminating that exact
   supervisor process because it has no scheduled task to stop.
4. Independently inspect the exact account in the provider platform. Resolve
   open orders/positions there when required; contact broker support if state
   is unknown. Record IDs, timestamps, fills, and account identity. A stopped
   process, disarmed UI, timeout, or unplugged laptop does not prove flatness.
5. Restart only after resolving the cause and reconciling exposure. For a
   deliberate maintenance restart, keep both execution gates false, then:

   ```powershell
   Remove-Item -LiteralPath "$runtimeRoot\STOP" -Force
   Enable-ScheduledTask -TaskName 'TopSignal Personal Device'
   Start-ScheduledTask -TaskName 'TopSignal Personal Device'
   ```

   Startup removes the transient shutdown request. Every practice/live
   provider-routing run stays disarmed until explicitly started again.

## 9. Backup, retention, recovery, and updates

Back up daily and immediately before an update: PostgreSQL custom archive,
private journal images, release version/dependency locks, settings, and recent
logs. Keep the exact Fernet key/auth configuration separately encrypted. Keep
7 daily, 4 weekly, and 3 monthly encrypted backups with an off-device copy.
Preserve order attempts, ambiguous submissions, trades, worker events, and
reconciliation records: this release does not automatically purge them. Monitor
database growth and approve a retention/export policy before deletion.
Archive historical market-data caches only after stopping work that uses them;
journal attachments must remain consistent with the database.

`pg_restore --list` proves archive readability, not recoverability. Monthly,
restore into a separate empty database, with separate credentials, both
execution gates false, and no broker credentials available to the process.
Run schema checks; compare account/trade/order-attempt counts, daily P&L, and
sample journal attachments. Do not restore production while another worker is
running. Record recovery time/last recoverable timestamp and decide acceptable
data loss and recovery time before unattended operation.

For each update:

1. Stop runs, reconcile exposure, invoke section 8's persistent stop, and verify
   no listeners. Keep the old release intact for rollback.
2. Back up/restore-test before migrations. Build a new versioned release; never
   edit the running release. Install pinned dependencies, run all suites/lint/
   builds, securely copy secrets, and repeat the ACL procedure.
3. Run checked migrations and `db:check`. Failure means STOP stays set. Reverting
   code alone does not reverse schema changes; rollback requires a restore plan.
4. Install the new task action with its new `-RepoRoot`, unchanged `-RuntimeRoot`,
   and `-Force`, omitting `-StartNow`. Never start the old laptop concurrently
   against the same account. A shared database lease cannot protect workers
   pointed at different databases.
5. Confirm both execution gates false, explicitly clear STOP/start the task,
   prove readiness and a dry-run session, then repeat applicable routing gates.

## 10. Staged rollout and acceptance evidence

| Stage | Required evidence before advancing |
| --- | --- |
| Offline tests and bounded mock soak | Full suites/builds pass. Stale prices, provider timeouts, ambiguous submissions, DB failures, duplicate workers, and stop/restart tests fail closed. Zero real order calls. |
| Second-laptop dry-run | Worker enabled, both execution gates false, bot dry-run true. At least 48 continuous hours and two trading sessions. Prove cold boot under limited identity, sleep settings, STOP across reboot, duplicate-start refusal, network/DB outage recovery, bounded resources, and independent alerts. |
| Verified practice routing | Independently verify the exact practice account classification. Human explicitly changes gates/configuration and starts it. At least five complete sessions including restart/rearm. Check exact broker IDs, no duplicates, stop/flatten, trade/P&L synchronization, and no unresolved submissions. |
| Supervised minimum-size live routing | Only if provider policy permits that account and all safety behavior is verified. One minimum contract, one position, tight daily loss/trade limits, operator present with direct provider access. Non-simulated accounts are currently rejected: never bypass that check. |
| Unattended operation | Retain all prior evidence; prove provider permission, alert delivery/off-laptop heartbeat, backup restore, boot recovery, reconciliation, and emergency drills. Any unknown order/account/data state or missing safety evidence means NO-GO. |

Live arming never survives a restart. Automatic process recovery is supported;
automatic resumption of provider-routing orders is deliberately not supported.
If fully automatic trading after reboot is required, this deployment remains
NO-GO for that requirement.
