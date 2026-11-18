# Automatic session-flow recovery — November 16, 2026

The owner requested a permanent way for session order flow to recover after a
restart, without interrupting the currently running bot or recording. A separate
optional Windows helper is installed and running; no application source or
running release was patched, activated or restarted.

## Installed component

| Item | Verified value |
| --- | --- |
| Host | peteamdpc / DESKTOP-6EUPDU1 |
| Task | `TopSignal Session Order Flow` |
| Bundle | `C:/ProgramData/TopSignal/tools/session-flow-maintainer-v1` |
| Runtime | `C:/ProgramData/TopSignal/runtime/session-flow-maintainer` |
| Interpreter | `C:/Program Files/TopSignalPython312/python.exe` |
| Identity | `DESKTOP-6EUPDU1/TopSignalSvc`, Limited |
| Priority | 7; API and recorder still 4 |
| Startup | Boot trigger delayed one minute; five-minute repeating recovery trigger; restart-on-failure one minute; `IgnoreNew` |
| Scope | Account 29593203, config 40; contract discovered independently of arm state |
| Source SHA-256 | `c5b40fe696b3f6e223ffb16ed2de1fa7ada1a035dd1ebdd8800b1d06347094bf` |

The installer compared both critical task definitions byte-for-byte before and
after installation; they were unchanged. Protected ACLs grant the service user
read/execute access to helper code/config and Modify access only to its dedicated
runtime directory. SYSTEM and Administrators retain FullControl; broad inherited
permissions are absent. There are no secrets in the configuration or status.

## Validation and production observation

76 focused tests passed: maintainer restart/outage/cold-cache recovery, auth
renewal/revocation, contract rolls, account mismatch, redirects, pressure deferral,
malformed/stale input, retry budgets, real helper-process startup/STOP behavior,
and existing session-order-flow/local-auth regressions. PowerShell parsing passed;
the installed task's security context, priority, triggers, singleton policy,
source hash and ACLs were read back and verified.

At 23:23:13 UTC (18:23 New York), helper PID 7812 returned a fresh response
through 23:22 UTC: 24 processed segments, zero pending, no coverage reasons.
The helper alone was then gracefully stopped with its dedicated STOP file. Its
process exited, the STOP file was removed, and only its task was started again.
At 23:24:10 UTC replacement helper PID 7840 returned fresh coverage through
23:23 UTC in 0.688 seconds, with 25 processed segments and zero pending.
Its next unattended cycle at 23:25:11 UTC also succeeded (0.500 seconds),
advancing coverage through 23:24 UTC with 26 processed segments and zero pending.
No STOP file remained. At 23:25:36 bot run 20 had evaluated again at 23:25:06;
API/recorder process identities were unchanged and the durable index was
30,551,384, with the same cumulative recorder error count.

API PIDs 9968/9536 and their creation timestamps remained unchanged across the
helper-only restart. Bot run 20 remained running with no run error and a new
23:20:07 evaluation. Independent recorder PID 11372 retained capture
`9d4a32c204e547bf91f83981852b6a3a`; its durable index advanced from 30,522,055
at the 23:21 check to 30,535,796 at 23:23. Its error counter remained 19, with
the last error still predating this work. Practice recording remained healthy.

Actual host reboot/API restart was deliberately not performed during trading.
Automatic API restart behavior was exercised using an isolated API fixture;
the production boot/recovery triggers and the helper's own restart were verified.
The helper automates bounded warm-up after a cold API start, so return of order
flow is eventual rather than instant. It preserves partial-coverage disclosure.

No cloud access, migration, backup scan, storage-policy change, bot rearm,
account enrollment, order placement or existing schedule enablement occurred.
Full off-device recovery and earlier maintenance acceptance remain separate work.
Operational details: [automatic recovery runbook](../../session-order-flow-recovery.md).
Diagnostic install/verification receipts are retained in Git-ignored
`tmp/health-20261116/`.
