# Worker archive recovery — November 10, 2026

The worker held an application PostgreSQL read transaction open while
`_sync_account_streams` synchronously recovered decision confirmations from the
local SQLite archive. Slow archive I/O exceeded the existing 60-second
idle-in-transaction limit. PostgreSQL logged connection terminations at 11:49:53,
11:52:02, 11:55:38 and 11:57:23 New York; the worker reported
`worker_cycle_error` / `OperationalError` when returning the expired connection.

Recovery now runs in one bounded background task per worker, with no overlapping
tasks and at most one launch per minute. It reads pending local evidence, opens
a dedicated short relational read session to verify exact decision ID and
correlation identity, closes that session, and then writes local confirmations.
The trading cycle releases its own read session before requesting recovery.
Failures remain visible and retryable. Uncommitted or mismatched decisions remain
unconfirmed. No historical rows, schemas, retention, database timeouts, trading
rules, risk limits, credentials, or account permissions changed.

Validation: 66 archive/recovery/worker tests and 35 archive durability, profit
guard, depth-clock and account-classification tests passed (101 total). The
regression tests hold recovery I/O open while verifying the caller returns,
prevent overlapping recovery, and assert that filesystem reads and writes occur
without an application database transaction.

Deployed as `20261110-worker-recovery-65`, based on
`20261110-archive-runtime-64`, with verified source hashes and the existing
deployment ACL checks. The previous release remains available. Deployment
receipts are on the host under
`C:/ProgramData/TopSignal/staging/worker-recovery-65/`.

Practice account `PRAC-V2-192577-85156978` / provider ID `29593203` was verified
simulated, active, tradable, flat and without working orders. Run 13 was stopped
through the normal application control before deployment. The old backend
exceeded its graceful-shutdown deadline; its supervisor terminated the old
process tree. Broker exposure was checked again before activating the new
release and remained flat with no working orders.

The patched service passed worker health and readiness. The normal practice
start control created run 14 / config 40 at 12:04 New York. At 12:04:09 the
worker recorded a fresh strategy evaluation with current depth and the new
release fingerprint: `HOLD — Waiting for persistent contrarian imbalance`.
The worker reported one active run and a successful evaluation. A second fresh
evaluation at 12:05:09 again had ready depth and the same normal HOLD reason.
Run heartbeats continued through 12:05:58, with no run error or stop reason; worker health and
readiness remained passing, and PostgreSQL had no new idle-transaction timeout
after deployment. This verifies running evaluations, not a broker fill.

The new account retains its current preset: one MNQ, 25-point initial stop,
25-point trail after +25, $250 daily loss gate and four entries per day.
Account-scoped practice auto-resume remains disabled; this repair did not change
that preference.
