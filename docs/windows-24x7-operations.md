# TopSignal on a personal Windows laptop

This is the supported unattended runtime path for TopSignal. It uses one
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
Build in the development checkout, but never point Task Scheduler at that Git
working tree. A source checkout is intentionally mutable and therefore is not a
safe execution directory for a long-running trading process.

From an elevated Windows PowerShell, create an immutable release copy. Adapt
the timestamp and source path, but keep the release and writable runtime roots
separate:

```powershell
$sourceRoot = (Resolve-Path C:\Users\you\Development\TopSignal).Path
$releaseRoot = 'C:\ProgramData\TopSignal\releases\2026-09-03-01'
$runtimeRoot = 'C:\ProgramData\TopSignal\runtime'

New-Item -ItemType Directory -Path $releaseRoot, $runtimeRoot -Force | Out-Null
robocopy $sourceRoot $releaseRoot /E /XD .git node_modules .venv storage /XF .env .env.*
if ($LASTEXITCODE -gt 7) { throw "Release copy failed: robocopy exit $LASTEXITCODE" }

py -3 -m venv "$releaseRoot\backend\.venv"
& "$releaseRoot\backend\.venv\Scripts\python.exe" -m pip install -r "$releaseRoot\backend\requirements.txt"
npm --prefix "$releaseRoot\frontend" ci
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
Remove-Item -LiteralPath "$releaseRoot\frontend\node_modules" -Recurse -Force
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
ALLOWED_ORIGINS=http://127.0.0.1:4173
TOPSIGNAL_DB_SCHEMA_INIT=skip
TOPSIGNAL_BOT_WORKER_ENABLED=true
TOPSIGNAL_LIVE_EXECUTION_ENABLED=false
TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION=false
PROJECTX_USER_HUB_URL=https://rtc.topstepx.com/hubs/user
TOPSIGNAL_DATABENTO_CACHE_DIR=C:\ProgramData\TopSignal\runtime\data\databento
JOURNAL_IMAGE_STORAGE_DIR=C:\ProgramData\TopSignal\runtime\data\journal_images
```

Never set `AUTH_REQUIRED=false` for this control path. The two live flags are
independent and default to false. A live configuration and a separately
confirmed continuous start are also required; environment flags alone do not
create or resume a run.

Before moving machines, verify that this user already has decryptable ProjectX
credentials in the shared database. If you intentionally use legacy environment
credentials, keep the deployment local-only and set
`ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS=true`; the backend rejects that fallback
with a cloud database or cloud Supabase configuration.

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
service-owned release item, reparse point escaping the release, or unprotected
root, environment file, or runtime root.

If an ACL command fails, do not improvise additional broad grants. The safest
recovery for a versioned release is to delete and recreate that release as an
administrator. To restore the saved ACLs instead, keep the backup outside the
target and use the parent directory expected by `icacls /restore`:

```powershell
icacls.exe (Split-Path $releaseRoot -Parent) /restore "$aclBackupRoot\release.acl" /C
icacls.exe (Split-Path $runtimeRoot -Parent) /restore "$aclBackupRoot\runtime.acl" /C
```

## 4. Migrate and prove the release

Run database checks as an administrator before the code becomes unattended:

```powershell
Set-Location $releaseRoot
npm run db:migrate
npm run db:check
```

For a manual smoke test, supply the separate runtime root explicitly and stop
the supervisor with `Ctrl+C` afterward:

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

Stop the manual supervisor. In elevated PowerShell, run the installer from the
hardened release. It prompts for the dedicated local account password:

```powershell
powershell -NoProfile -File "$releaseRoot\scripts\install-windows-startup-task.ps1" -RepoRoot $releaseRoot -RuntimeRoot $runtimeRoot -ServiceUser "$env:COMPUTERNAME\TopSignalSvc" -StartNow
```

The installer rejects Git checkouts, administrator/built-in identities,
service-writable release content, missing frontend builds, and unsafe ACLs. It
uses the actual Windows PowerShell 5.1 or PowerShell 7 executable that invoked
it, creates a password-logon task at `Limited` run level, and refuses to replace
an existing task unless `-Force` is explicit. It never uses `SYSTEM` or
`Highest`.

The supervisor first runs a production-security preflight and rejects
`AUTH_REQUIRED=false` even for an otherwise local-only configuration. It then
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
use explicit Windows tree termination. Before each cycle, occupied loopback
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

- `logs\topsignal.log`: 14 UTC-daily application logs
- `logs\access.log`: 7 UTC-daily API access logs
- `logs\frontend-access.log`: five 10 MiB local UI access logs
- `logs\supervisor.log`: five 10 MiB supervisor logs
- `supervisor.lock`: cross-session single-supervisor lock

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

Back up PostgreSQL and the Fernet key separately. For a direct or session-mode
PostgreSQL URL, a typical encrypted backup uses:

```powershell
pg_dump --format=custom --file D:\EncryptedBackups\topsignal.dump "$env:MIGRATION_DATABASE_URL"
```

Never use the Supabase transaction-pooler port for backup or migrations. Test
`pg_restore --list` after every backup and restore into a separate database
before relying on it. A database backup without the exact Fernet key cannot
recover stored provider credentials.

After network loss or restart, only a still-`running`, explicitly started,
continuous `BotRun` with the same execution mode is eligible for adoption.
Stopped, blocked, and errored runs never resurrect. Unresolved submissions are
reconciled before another evaluation, and only one deployment-wide worker can
hold the database lease.
