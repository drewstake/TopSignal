# Automatic microstructure collection — activation receipt

Automatic feature collection is enabled on peteamdpc as **TopSignal Microstructure
Features**. Its first timer-driven invocation completed successfully on
October 30, 2026 at **18:58:16 UTC / 14:58:16 Eastern**, with task exit code **0**.
No manual start, main-service restart or recorder restart was used for this check.

## Cause and repair

The SSH administrator already had an elevated token. The original installer tried
to register an S4U task for the different `TopSignalSvc` account without supplying
registration credentials. Windows requires the target account's password for
that operation even though S4U does not store a password for execution.
[Microsoft's security-context documentation](https://learn.microsoft.com/en-us/windows/win32/taskschd/security-contexts-for-running-tasks).

The fixed installer accepts a `PSCredential` and calls
`TaskFolder.RegisterTaskDefinition` with explicit S4U logon and create-only flags.
It refuses to replace an existing task, rejects administrator service identities
and mismatched credentials, and verifies the installed Limited/S4U context.
The host's existing machine-DPAPI protected deployment credential was reused
entirely in host memory. Its administrators/SYSTEM-only ACL and machine/account
scope were checked first. No account password was reset or disclosed; no new
credential file was created and the task does not store its password.

The helper directory previously allowed only administrators and SYSTEM.
`TopSignalSvc` received inherited **ReadAndExecute** on exactly
`C:/ProgramData/TopSignal/tools/depth-research-v1/`. It did not receive write access
to executable helpers. Existing recording, derived-cache, Python and trading
deployment permissions were preserved. The prior installer and helper ACL were
saved in the cache's activation audit directory.

## Registered behavior

| Setting | Verified value |
| --- | --- |
| Identity / logon / privilege | TopSignalSvc / S4U / Limited |
| Repetition | Every five minutes, no end boundary |
| Reboot recovery | Startup trigger with one-minute delay |
| Process priority | BelowNormal, observed on both Python processes |
| Maximum runtime / overlaps | Three minutes / IgnoreNew |
| Login dependency | No interactive user login required |
| Action | Pinned existing Python, `backfill_depth_features.py --latest` |
| Storage | Existing cache at `D:/TopSignal/data/databento` |

The collector reads sealed recordings and produces one latest completed UTC
five-minute interval per invocation. It does not restart other services, send
orders, read broker credentials or create an unrestricted historical backlog.
Existing 512 MiB derived budget, combined 20 GiB raw+derived budget, 10 GiB free
reserve, 30-day review and per-read limits remain in force. Limits produce a
failure receipt rather than deletion or budget expansion.

## Successful scheduled output

The first automatic invocation produced **1,114 observations** from **232,088
incoming events**, including reconstruction pre-roll, for **18:50–18:55 UTC
(14:50–14:55 Eastern)**. The compressed feature file contains 1,261,190 bytes;
file plus immutable manifest added **1,263,992 bytes**. The complete derived tree
then occupied 4,161,920 bytes, including prior versions and collector status.

File: `947074abb7a1cc32cca2e5345e0a95a0.json.gz`, in the existing owner's
`microstructure-derived-v2/` namespace. Its byte length and SHA-256 were verified:
`5d20e9e7d59eec8119b0fda08f8d0d1dd26f65bb52281fa5e85a551802290571`.

## No interruption to trading or recording

Before and after the automatic run, all seven existing launcher/Python process
IDs and creation timestamps were unchanged: 17740, 9420, 3944, 9300, 17212,
12088 and 12224. The trading and recorder task definitions also retained identical
SHA-256 digests across the run. Both remained Running, and the production `/ready`
endpoint returned `ready`.

Recorder PID **12224** retained its original startup time and one connection.
Its received-event counter advanced from **3,340,040 to 3,424,150**, with a fresh
frame at **18:59:30 UTC**, **zero reconnects and zero errors**. No existing task
was stopped, re-registered, disabled or restarted.

The recorder's Task Scheduler history already reports an ignored overlapping
watchdog launch while its long-running instance remains active. This was present
before activation; process identity, continuous receipts and recorder status are
the continuity checks, not that historical launch result alone.

## Checks, evidence and operation

- All changed/staged PowerShell files passed syntax parsing.
- Three targeted backfill, storage-refusal and backup compatibility tests passed.
- Actual cross-account registration, timer launch, S4U/Limited runtime, low process
  priority, successful exit and output checksums were verified on the host.
- Private receipts, original installer/ACL, exported task XML and continuity
  snapshots are under `D:/TopSignal/data/databento/audits/microstructure-task-20261030/`,
  with an off-device copy under the development cache's corresponding audit path.
- The existing filesystem backup includes the feature tree and activation receipts;
  its schedule and storage limits were not changed.

Inspect the current task with:

```powershell
Get-ScheduledTask -TaskName 'TopSignal Microstructure Features'
Get-ScheduledTaskInfo -TaskName 'TopSignal Microstructure Features'
Get-Content -LiteralPath 'D:/TopSignal/data/databento/microstructure-derived-v2/collector-status.json'
```

To stop future automatic feature runs, disable only **TopSignal Microstructure
Features**. To resume, enable that same task. A currently running invocation can
finish within its three-minute bound. Neither action requires changing the trading
bot, recorder, source files or any account credential.
