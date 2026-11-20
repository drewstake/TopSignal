# Releasing changes to peteamdpc

Describe the change you want and keep peteamdpc open at `http://127.0.0.1:4173/bot`.
By default, the agent makes and tests the change in the local checkout, reviews
the diff, then commits, pushes and deploys it with the command below. The agent
tells you when the served page is verified and ready to refresh. Say “local only”
to keep the work in the checkout, or specify where to stop. Leave the local app closed.

```powershell
npm run release -- ship --paths frontend/src/styles/workspace.css --message "Adjust dashboard spacing"
```

Use exact tracked paths for whole-file edits. For partial files or new files,
stage only reviewed hunks, inspect `git diff --cached`, then use
`--staged (git write-tree) --message "..."`. If unrelated hunks are already
staged in the same file, apply the intended patch in a separate clean worktree,
commit there, and pass its SHA with `--commit` from the original checkout.
Never stash, reset, add all files, or include another local commit to ship.

The command pushes to main, overlaps CI with preparation, and reports the lane
and reason. The running task, verified source, served HTML and both deployed
revisions determine eligibility. Reviewed dashboard and chart components can
ship JSX text, classes and literal SVG paint changes. Existing CSS presentation
values can use the same lane. Changes to executable
expressions, selectors, API/authentication, orders, dependencies, release
tooling, backend or unrecognized files use the full lane. Frontend checks still
run on the full push CI workflow. Dashboard publication needs both frontend
jobs and the digest-verified production artifact for that push and run attempt.
The full lane requires every CI gate, isolated local validation and broker,
research, migration, recorder, ACL and integrity gates.

If you ask to prepare a change and hold it for “okay deploy”, the agent adds
`--prepare-only` to the normal ship command. This pushes, tests and stages the
exact change. On “okay deploy”, resume with `ship --commit <prepared-sha>`.
The receipt records elapsed time from both push and the deployment request.

A full release reports open research windows as soon as peteamdpc is staged.
Keep the ship command running. If you explicitly approve cutting those windows
short for this release, run `npm run release -- ship --approve-research-gaps <sha>`
in another shell. That waiting attempt uses the same prepared files and still
requires a flat broker. An ordinary deploy request waits for the windows to close.

Dashboard files use immutable commit URLs. A locked pointer switch selects
the HTML; rollback retains old asset URLs. A full release carries those URLs
forward and starts with its own dashboard. Full preparation and safe-window
polling leave trading and recording active. Research gaps always require a
fresh, separate decision. Refresh peteamdpc only after the command verifies
the page and every candidate asset through the viewing connection.

**Recovery.** Rerun `npm run release -- ship --commit <sha>`. It reuses the
verified build and matching prepared release. The failing step and phase times
are in `tmp/releases/ship/<sha>/timing.json`; full test logs sit alongside it.
An interrupted full switch requires inspection of the task, STOP ownership and
activation.log before another switch. For a dashboard rollback run
`npm run release -- ship --rollback <currently-displayed-dashboard-sha>`.
The target for the first real dashboard shipment is 2–3 minutes from push
through verified viewing bytes. Record actual wall time and CI job timestamps.

**One-time installation.** Prepare with `ship --commit <sha> --prepare-only`.
Ask before the first production switch. On this PC, port 4173 has a separate
Study View and 4176 is the SSH forward to peteamdpc. After approval, run
`npm run release -- ship --commit <sha> --install-publisher`. If needed, it
hands static requests to peteamdpc and retains old assets for existing tabs.
The viewing handoff restores the prior viewer on failure.

## Connection

Install local dependencies with `npm ci`, `npm --prefix frontend ci` and the
backend test requirements in `backend/.venv`. Use authenticated Git, gh and
OpenSSH. The private `tmp/release-host.json` needs only `host`, `key`,
`repository` and `staging_root`. The command reads the running task's
protected release profile on peteamdpc, including the complete companion list.
Historical research-gap and storage-transition approvals are never inherited.
The lower-level validate/package/stage/activate commands remain available for
diagnosis and separately reviewed transitions.

## Preserved gates and recovery

- Entire source/asset archives are SHA-256 checked before extraction. Reject
  traversal, links, Windows device names, duplicate/case-colliding paths, extra
  files, secrets and local runtime/storage directories. Candidate and previous
  release paths are distinct; existing releases are never overwritten.
- Source, assets, private environment, certificates, and installed dependency
  inventories are checked again immediately before switching and after startup.
  Each inventory uses up to eight I/O workers to read and hash all files afresh;
  no timestamp, size-only shortcut or cached hash replaces content verification.
  An unchanged requirements file allows copying the previous Python environment
  into a release-owned directory with eight bounded file-copy workers. Every
  copied file is still compared with a fresh source inventory. Copy failures or
  nested links reject the candidate. Shared environment junctions are not created
  or recursively hardened. Changed runtime dependencies get a fresh environment.
- Staging and candidate directories are private before copying configuration.
  The existing installer validates the limited service identity and every
  candidate ACL. Runtime ACL validation remains unchanged. The running release
  and its dependency environment are never modified by preparation.
  ACL checks share one fresh descriptor read per item within a validation pass,
  resolving SIDs directly. No ACL acceptance is cached across release phases.
- Database migration readiness is checked without applying migrations. Pending
  migrations, storage cutovers, changed private configuration/certificates and
  independent recorder/tool upgrades require their existing reviewed procedures
  before using this routine activation path. Follow
  [the storage policy](supabase-storage-policy.md) and
  [Windows operations](windows-24x7-operations.md); no cloud history, retention
  cleanup or backup is silently introduced by this command.
- Broker calls are restricted to login/account/position/order reads. Check all
  scoped accounts are eligible and simulated, configuration and runner identity
  are unchanged, no order attempt is pending/unknown, and positions/orders are
  flat before stopping and again before activation. The application security
  validator also checks local authority, authentication and production settings.
- A uniquely owned STOP marker and existing supervisor lock coordinate graceful
  shutdown. A pre-existing or replaced operator STOP is never removed. The task
  keeps its existing principal, triggers, settings and arguments except release
  paths. No force-kill is used to meet a deadline.
- After startup, the broker gate accepts the existing worker safeguard that
  stops a provider-routing run with `worker_restart_requires_rearm` and disables
  its configuration. It requires the same run ID and unchanged trading settings;
  only the enabled flag and update timestamp may change. This is never accepted
  before the switch. Deployment never rearms trading: explicitly restart it from
  the control UI after reconciling the account if that safeguard applies.
- Verify actual frontend bytes, API liveness/readiness, worker health, broker
  configuration, independent recorder PID, all other TopSignal task definitions,
  and three recording observations over 20 seconds. Missing recording, new
  drops, capture restarts or durability regression fail verification. The API's
  local-device authentication is required for the recording check.
  The independent recorder's fresh status, PID, capture, error counts and durable
  event index are checked from before the switch through final verification.
  Application captures are additionally required for enabled, running bots;
  deliberately disarmed bots do not create application captures.
- If candidate startup/verification fails, restore the previous task action and
  verify it. Recheck broker exposure and release integrity before stopping a
  partially started candidate. If exposure is active or the rollback gate cannot
  be verified, stop the rollback procedure and report the error, leaving that
  candidate available to manage its position. A shutdown timeout leaves STOP
  latched for operator diagnosis. Never blindly retry an interrupted switch;
  inspect task/marker ownership and `activation.log` first.

Host receipts stay in the private staging directory: `prepared.json`,
`broker-before.json`, `broker-latest.json`, `ci.json`, `recording-verification.json`,
task XML and `activation.log`. Retain the previous release for rollback; cleanup
is deliberately a separate authorized operation.

`activation.log` includes durations for dependency preparation, integrity and
broker checks, ACL hardening/validation, migrations and graceful stop. Use these
timings to diagnose a slow release without disabling a gate. The supervisor
checks STOP during its normal health wait; shutdown deadlines and the exclusive
lock remain unchanged. Archive recovery can abandon read-only coverage planning
on shutdown, retaining received bars and its old cursor for an idempotent retry.
Both archive producers are asked to drain even if either one times out; a failed
drain still prevents the worker from releasing its lease as a clean shutdown.

Both Linux and Windows offline CI suites run all tests with four isolated
workers. PostgreSQL safety acceptance remains in its separate serial lane.
Manual-order tests copy a closed, empty schema into a fresh SQLite file for each
test; real transactions, disk persistence and separate observer connections are
preserved.

## Benchmarking

```powershell
backend/.venv/Scripts/python scripts/benchmark-backend-tests.py --workers 2 4 --repeat 2 --output tmp/windows-benchmark
```

Each complete suite runs separately on the same machine. The second round
reverses worker-count order. The runner rejects failures, source drift, different
test identities/outcomes, or different passed-subtest counts; it retains logs,
JUnit reports and timings. An unsuccessful run never claims a speedup.

The manually dispatched `Benchmark Windows tests` GitHub workflow repeats this
on a hosted Windows runner with CI's Python version. Its timing is separate from
the local measurements in the [November 10 report](audits/2026-11-10/release-speed.md).
