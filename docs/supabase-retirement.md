# Supabase retirement maintenance procedure

**Executed during the authorized November 13, 2026 maintenance window.**
See the [maintenance receipt](audits/2026-11-13/storage-maintenance.md) for the
actual release, mirror transition, dedicated login retirement, enrollment and
recovery evidence. Earlier audits describe the pre-maintenance baseline.
This procedure remains recovery guidance, not permission to repeat provisioning,
retirement or enrollment blindly. Remaining acceptance work is recorded separately.

## Prerequisites and review

Package the clean reviewed commit with `npm run release -- package`; require
all six exact-commit CI jobs. Use the existing immutable [release workflow](releasing.md).
No broker orders, automatic rearming, strategy edits, budget increases, retention
changes or cloud-data deletion are part of this transition.

The application host configuration selects `retire_cloud_configuration: true`.
During preparation this strips only `SUPABASE_*`, `VITE_SUPABASE_*`, `JOURNAL_*`
and `GEMINI_*` bindings from the new candidate's private environments. It requires
the existing loopback PostgreSQL authority and local trusted-device profile and
verifies every other parsed value is identical. It handles quoted/multiline
bindings. Previous releases and encrypted environment backups remain intact.
The normal source, dependency, private-file and ACL hashes bind the result.

Include changed independent modules in `companion_files`; the release refuses
activation until their separately reviewed rollout matches. In particular:

- Recorder: `record_market_depth.py`, `recorder_startup.py`, `depth_recording.py`,
  `depth_totals.py` and their existing imports. Preserve all original
  recorder bounds, contract selection, owner and bot configuration 32.
- Remote history helpers: `read_depth_history.py`, `depth_history_index.py`,
  `recording_inventory.py`, `depth_heatmap.py`, `read_session_flow.py` and existing dependencies.
- Host backup: `backup_market_cache.py`, `backup_io.py`; execution backup uses
  `run_execution_backup.py`, `runtime_configuration.py`, crypto/dump modules and
  its existing private key/configuration. Upgrade the off-device archive runner
  and matching restore helpers to the same tested source.

Use new versioned protected tool directories. Do not modify a Python module
while its existing process is using it. Export task XML/configuration and record
file hashes before changes. Keep optional microstructure generation disabled;
its derived budget is exhausted and this procedure does not raise it.

## Fresh recovery gate

In the permitted window, create a current consistent execution dump through
the reviewed backup runner. Preserve encrypted credential key, runtime env and
SQLite online snapshot of local auth. Transfer to the existing protected recovery
computer, compare every ciphertext hash and run:

```text
python backend/tools/verify_execution_recovery.py --env-file <protected-audit-env> --pg-bin <local-pg-bin> --directory <off-device-bundle> --key-file <recovery-DPAPI-key> --database topsignal_audit_<unique-id> --receipt <off-device-bundle>/off-device-verification.json
```

The administrator URL must select disposable local PostgreSQL, never production.
The restore preserves policy role references by creating missing **NOLOGIN**,
unprivileged placeholders; it never restores role passwords/memberships or
changes an existing role. This needs the recovery administrator's role-creation
permission and prevents a new recorder RLS policy from breaking a fresh-machine restore.
The tool creates a new database, checks complete row/sequence hashes, decrypts
credentials and proves authority is absent. On an isolated auth copy it proves
issuance, module restart and logout/revocation while network connections are
blocked. It retains the audit database and isolated auth file. Session expiry,
loopback/origin enforcement and owner isolation also have regression coverage.
Return the verification receipt beside the corresponding host ciphertext files.
The mirror transition rejects a mismatched bundle or a receipt older than two hours.

This focused gate does **not** cover raw recordings, research or original source
archives. Full-cache catch-up is a separately permitted low-load transfer:

- Existing source `D:/TopSignal/data/databento`; existing protected off-device
  destination and capacity limit from `scheduled-backup-config.json` remain.
- The closed-market catch-up uses `max_mib_per_second: 8`, the host recorder `recorder_status` path,
  and the existing bounded remote deadline. Pacing applies to host copy/hash,
  SQLite warm-up/online-backup steps, packing and SCP transfer. Unknown/stale/
  unhealthy recorder status or stalled inventory defers the attempt.
- Refuse an already-running snapshot before scanning capacity. Reuse immutable
  members only from the prior verified snapshot; recheck every reconstructed
  member off device. Use SQLite online backup, never raw copies of open databases.
- Run `--restore-check`; verify source archives, research, worker evidence,
  progress, raw/totals and Journal recovery coverage in the manifest. Derived
  heatmap/history caches are deliberately excluded. Preserve failed staging.
- Keep the schedule disabled until one successful full catch-up/restore and
  observation of disk/recording load. Proposed recurring full pull: Saturday
  01:00 New York, with the recovery PC powered on/signed in and private SSH
  available; retain daily focused execution backups. Any schedule enablement
  still requires the permitted action. This destination is not always available
  when the development PC is off; stale receipts must remain visible.

## Local recorder credential transition

Prepare a new protected `recorder.env` and JSON configuration on peteamdpc.
Use a fresh strong password for role `topsignal_depth_recorder`, local database
`127.0.0.1:5433/topsignal`, the existing credential encryption key and provider
base URL. Explicitly set both execution flags to `false`; include no cloud keys.
The JSON adds `credential_env_file`, `database_role` and existing owner UUID
`3af8acf1-a33e-4a0c-ae16-0b84d7c518cb`, plus the reviewed backend path. The cache
must remain `D:/TopSignal/data/databento`. Grant only the scheduled identity read
access to credentials; Administrators/SYSTEM retain management access.

```text
python backend/tools/provision_depth_recorder.py --admin-env <protected-current-runtime-env> --recorder-config <new-private-config> --receipt <new-provisioning-receipt>
```

Provisioning refuses an existing role, a nonlocal target or non-paper/nonowned
bot. It creates no role memberships, grants only required SELECT columns and
owner/provider/bot-specific row policies, sets read-only transactions and a two-
connection limit. No application schema initializer or order client is loaded
by recorder startup. Verify wrong-owner and write denial before the process switch.

Create the recorder's dedicated STOP marker only if absent, wait for graceful
exit and segment sealing, and preserve its final receipt. Do not force-kill it
or erase another operator's STOP. Change the task's versioned interpreter/script/
config paths, preserving principal, boot/retry triggers and security settings.
Remove only the marker created by this operation and start the task. Verify a
new capture, local credential reads, accepted subscriptions, sealed raw/totals,
no order-mutation calls and unchanged limits. This expected recorder restart is
completed **before** the application release's uninterrupted-recorder baseline.

## Application, migration and coherent mirror retirement

1. Recheck fresh broker positions/orders and unresolved attempts with the existing
   release gate. Stop only when all required accounts are flat and gates pass.
   Use graceful maintenance/STOP ownership. A timeout or unknown exposure stops
   the procedure; no forced stop or diagnostic trade is allowed.
2. Apply the prepared fill-leg migration through the existing migration runner.
   Preserve old rows; do not invent one-second paths. Perform migration/readiness
   checks required by the immutable release workflow.
3. Stage/activate the exact CI-approved application bundle. Verify compatibility
   of the migration ledger and retirement state before selecting any rollback.
   Release 87 rejects the new fill-leg migration even before mirror retirement;
   it is not a valid routine rollback. Verify served bytes,
   local auth and local recorder operation. Retain expected restart disarming;
   never rearm automatically. Only after that succeeds, recheck exposure/orders
   and gracefully stop candidate writers for the separate storage transition.
4. Drain the mirror while application writers are stopped. Export its task XML,
   create its dedicated configured STOP, wait for graceful exit, disable
   `TopSignal Local Cloud Sync`, and verify no consumer connection remains.
   The old release-45 consumer cannot interpret a retirement row; stopping only
   a newly built consumer is insufficient.
5. Run the following against the protected current admin environment and existing
   sync config. `check` is read-only; use a new receipt filename for `retire`:

   ```text
   python backend/tools/retire_local_mirror.py check --admin-env <runtime-env> --sync-config <private-sync-config> --backup-receipt <host-bundle>/off-device-verification.json --receipt <check.json> --reader-role topsignal_app --reader-role topsignal_backup --reader-role topsignal_sync_source
   python backend/tools/retire_local_mirror.py retire --admin-env <runtime-env> --sync-config <private-sync-config> --backup-receipt <host-bundle>/off-device-verification.json --receipt <retired.json> --reader-role topsignal_app --reader-role topsignal_backup --reader-role topsignal_sync_source
   ```

   Each command performs fresh full ordered reconciliation. The transition
   takes bounded table/advisory locks, requires the exact acknowledged sequence
   and an empty outbox, writes a durable receipt with recovery/reconciliation
   hashes, and disables only capture triggers. Local serialize/authority fences,
   cloud fences, schema, outbox history and cloud rows remain. Candidate readiness
   checks all authority fences and the expected retired capture-trigger state.
6. Verify candidate readiness against retired mirror state, then start that same
   already activated candidate using the established maintenance STOP ownership
   procedure. Do not invoke a routine rollback to the pre-retirement release.
   Replace the execution-backup task's old release-45 action/config with the
   tested helper. Replace `runtime_env_file` with `runtime_task_name: "TopSignal
   Personal Device"` and `release_root: "C:/ProgramData/TopSignal/releases"`.
   The runner resolves the actual task release before and after each backup.
7. Publish mirror status `retired` with the transition receipt reference; verify
   ordinary local writes leave the outbox empty. After restart proof, disable
   login for the **dedicated** local sync role and dedicated cloud mirror role;
   remove active task/config references to their credentials and retain encrypted
   recovery copies. Do not revoke shared project/admin/JWT keys until their other
   consumers have been excluded and replacement admin recovery is verified.
   Do not delete the Supabase project, Auth users, audit records or objects.

The two October 22 one-shot check tasks were exported and disabled. Discord v5
restarted using `discord-local-storage-20261113/config.json` and its minimal
protected environment containing only local DATABASE_URL; its read-only check
and fresh `watching` state passed without diagnostic messages. Disabled helpers
and immutable historical environments are recovery material, not active fallbacks.

Keep scheduler priority 4 on the API and independent recorder tasks. The default
priority 7 gave both background I/O/memory priority and contributed to observed
contention. Both installers now set 4; preserve their limited service identity,
boot/retry triggers, ACLs and all recording/storage limits when restoring them.

Rollback after mirror retirement is a **storage transition**, not a blind switch
to release 87. The old backend expects capture triggers enabled. Leave execution
stopped if candidate startup fails and inspect the exact retirement state; either
repair the candidate or perform a separately reviewed mirror reinitialization
from authoritative local records. Never point execution at the cloud mirror.

## Acceptance after activation

Verify the actual served HTML/assets, backend commit, task/module hashes and local
auth issuance/logout/restart with cloud configuration absent. Exercise explicit
unavailable account `28278873` and available `29593203` through
`http://127.0.0.1:4173/bot?account=29593203`. The candidate updates URL, active
selection, requests and recording status together; unavailable links do not
silently fall back. Unauthenticated API calls and foreign account scopes fail.

Enable the reviewed owner enrollment policy only as authorized: current candidates
`29593203`, `29671908`, `29671939`, `29671977`, `29672001`, `29672024`; cap eight,
fresh discovery, explicit opt-outs preserved. `29672001` can record without trading
permission. Recheck eligibility at activation; do not enroll historical accounts.

Prewarm the bounded lower-history index and verify recent/full-session reads,
quality counts and extrema. Record warm latency and recoverable 503 behavior.
Verify upper heatmap derived coverage independently from lower totals. Observe
recorder queues, durable indices, gap reasons and last successful writes over a
meaningful interval, including browser closed and recovery/development PC
disconnected. Record disconnect/coverage times explicitly. Observe manual/bot
fills only from existing evidence or naturally authorized activity. A flat
account proves readiness, not successful fill/exit capture.

No new evidence can reconstruct the four missing raw intervals in the incident
receipt. Keep them as explicit gaps. Preserve the wider 18:00–17:00 New York
collection/session convention, including the requested overnight 18:00–16:00
coverage. Do not narrow collection hours or alter market-halt/DST handling.
Only then update architecture summaries to “Supabase fully retired.”
