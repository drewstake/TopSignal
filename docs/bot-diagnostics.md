# Bot diagnostics for continuous operation

The production supervisor automatically writes `bot-diagnostics.jsonl` under
`<RuntimeRoot>\logs` (normally `C:\ProgramData\TopSignal\runtime\logs`).
The file survives process restarts and release changes because it lives outside
the release. Existing `topsignal.log`, access and supervisor logs remain useful.
No broker connection, bot start, live-routing permission or database migration is
needed to enable this logging: install the updated release and use the existing
[production launcher](windows-24x7-operations.md).

For a manual Uvicorn launch, create `backend/storage/logs` and start from the
repository root with `--app-dir backend --log-config backend/logging.production.json`.
Ordinary development launches without that config do not write the JSONL file.
Use one backend process per log directory; rotation is not multiprocess safe.

## What gets recorded

Every line is a JSON object with a UTC timestamp, severity, logger, process ID,
process-session UUID, event name and explicitly allowed context fields. Across
restarts, use `bot_run_id`, `correlation_id`, `decision_id`, `order_attempt_id` and
`provider_order_id` to connect related events. Provider order IDs are not fill
confirmations. `config_hash` identifies changes to the evaluator configuration;
it is not a source-code revision or a copy of the configuration. Keep the deployed
release and its Git revision with incident backups.

| Events | Information |
| --- | --- |
| `bot_evaluation_started`, `bot_evaluation_timing`, `bot_evaluation_failed` | Strategy, config fingerprint, account/run/correlation IDs, total evaluation time, safe failure classification |
| `bot_evaluation_stage_timing` | Stage name, inclusive duration, own time, calls/failures, SQL query count/time, new connection count/time; final execution commit and forward-results write also emit separate stage records |
| `bot_evaluation_completed` | BUY/SELL/HOLD, reason, routing outcome, risk codes, price/quantity, candle timestamp/close/age/count, linked decision and order |
| `bot_order_submission_started`, `bot_order_submission_finished` | Attempt identity, provider acknowledgment/unknown/error status and request duration |
| `bot_order_reconciled` | Changes observed when a previously ambiguous order is matched to provider state |
| `bot_run_transition` | Stop/block/error transitions and reason |
| `bot_worker_evaluation_committed` | Successful commit of a scheduled evaluation |
| `bot_worker_started`, `bot_worker_state_changed`, `bot_worker_heartbeat` | Lifecycle, ownership, provider health, retries, unresolved submissions, last cycle/success timestamps; steady-state heartbeat about once per minute |
| Existing `app.*` warnings/errors | Preserved structured extras such as provider reason/status, stream kind and retry delay |

An evaluation/order/transition record with `persistence: pending_commit` describes
what the code observed before the caller commits; a crash or rollback can follow.
Only `bot_worker_evaluation_committed` confirms the worker's subsequent commit.
Manual evaluations do not currently emit a separate commit marker. The saved
BotDecision/BotOrderAttempt records and captured broker trades remain the source
of truth. A missing submission-finished event is a reason to reconcile broker
state, never evidence that no order was sent.

Worker heartbeat counts describe the latest snapshot, not cumulative totals.
Lease activity can continue during a stalled evaluation: check
`last_cycle_completed_at` as well as heartbeat arrival. Logging is local evidence;
it does not send notifications. Use the existing independent health/readiness
monitoring procedure in the operations guide.

## Reading and summarizing

New forward records include **Evaluation stage timings** in their decision
details. Stage totals include nested stages; `self_ms` excludes them. Do not add
inclusive totals together. SQL execution and connection establishment are
subsets of the owning stage, not additional elapsed time. Pool checkout/ping,
ORM work, transaction commit/rollback and other overhead can appear in own time
without appearing in SQL counters. Final execution commit occurs after the
evaluation total; local forward-write duration is in logs only. Failed stages
retain partial durations without exception text, SQL, credentials or HTTP bodies.
The SSH summary reports each stage separately as `stage:<name>`.

Timings remain in rotating local logs and the local forward SQLite ledger;
there is no new cloud table or historical candle persistence. Allow roughly
5 KiB per evaluation for compact stage metrics in the local ledger (under
2 MiB/day for one five-minute bot); local logs retain their existing rotation cap.

For a persistent IPv4 server, the explicit Supabase **session** endpoint on
port 5432 uses a three-connection pool with at most two temporary overflow
connections per backend process, ten-second checkout timeout, five-minute
recycle, pre-ping, and rollback on return. Transaction mode on port 6543 still
uses NullPool. Both retain verified TLS and disabled prepared statements.
Statement/lock/idle-transaction timeouts are reapplied locally on every
transaction in one round trip. See [Supabase's SQLAlchemy guidance](https://supabase.com/kb/troubleshooting/using-sqlalchemy-with-supabase-FUqebT).
Never switch endpoints without testing connectivity, TLS and transaction
timeouts on the deployment host; monitor the pool budget if adding replicas.

Open **Trading Bot → Logs & summaries** to inspect recent events and summaries
without filesystem access. Choose a time range, severity, run ID or search term
(including a correlation ID), then **Apply filters**. **View event details** shows
the retained fields. Refresh manually or enable 30-second refresh while the page
is visible. Missing logging, read failures, empty results and partial coverage
are shown explicitly.

The authenticated `GET /api/accounts/{account_id}/bot-logs` endpoint verifies
account ownership and filters every record by user/account or database-owned
bot/run identity. Shared broker account numbers alone never grant log access.
Global worker events without a tenant/account link are excluded; keep using the
existing runtime status for worker health. Records referencing deleted bot/run
identities may be excluded because ownership can no longer be verified.
Responses are not cached and expose neither file paths nor raw provider payloads.

Each request scans at most the newest 8 MiB / 20,000 parsed records across the
current file and up to 30 rotations, returning the newest 100 matches (API maximum
200). Summaries cover all matching events within that scan, not just displayed
rows. A partial-coverage notice appears when the scan budget is reached. Heavy
activity can make this shorter than the requested 1–168-hour window. The server
uses the active diagnostic handler's path, with the development default as a
read-only fallback; opening this panel does not enable logging or start a bot.

Watch the file in PowerShell:

```powershell
Get-Content 'C:\ProgramData\TopSignal\runtime\logs\bot-diagnostics.jsonl' -Tail 30 -Wait
```

From the repository root, summarize current and rotated files without importing
the trading app or contacting any database/broker:

```powershell
.\backend\.venv\Scripts\python.exe backend/tools/summarize_bot_logs.py --log-dir 'C:\ProgramData\TopSignal\runtime\logs'
.\backend\.venv\Scripts\python.exe backend/tools/summarize_bot_logs.py --log-dir 'C:\ProgramData\TopSignal\runtime\logs' --run-id 123 --since '2026-10-21T00:00:00Z'
```

Use `--correlation-id` to narrow to one evaluation. The report includes observed
outcomes, frequent risk blocks, error events, mean/max evaluation and submission
latency, and the latest worker health. Run/correlation filters exclude global
worker health events. Malformed or crash-truncated lines are counted and skipped.
For a stable incident report, read a copied snapshot of the logs: rotation during
a live scan can replace files. Event counts are not trade counts or profitability.

For optimization, compare runs/config fingerprints, blocked opportunities,
delivery delays and evaluation/submission latency; join the linked decisions to
actual fills and realized results in existing records. These logs contain neither
full input candle series nor enough market data to reconstruct an exact replay.
Existing decision-research capture is a separate system with its own settings.

## Storage and privacy

The new file rotates at 10 MiB, retaining 30 backups (about 310 MiB total, plus at
most an oversized record). This is a size budget, **not 30 days of history**.
The existing four streams plus this one total about 660 MiB. At 10,000 records/day
and 1 KiB/record, this stream would retain roughly 31 days; measure your actual
rate. Copy relevant logs off-device before rollover if you need longer history.
Rotation only replaces this new diagnostic stream's oldest files; it never
deletes database trades, journals, audit rows or historical archives.

Keep the runtime folder on persistent local storage, restrict it to the service
identity and administrators, and include it in your private off-device backups.
Logs contain account/trade details even after credential redaction. They are not
stored in Supabase or included in Git. Only the scoped, redacted diagnostic view
is served by the authenticated API. Existing source archive
and database backup requirements remain in force.

New events use a field allowlist, bounded strings/lists, and credential/URL
redaction. Raw provider requests/responses, credentials and candle arrays are
excluded. Exception diagnostics can include frame locations, but omit exception
text, source lines and locals. Redaction cannot identify arbitrary secrets placed
in free text; review a diagnostic export before sharing it.

File writes are small, synchronous, and flushed per event, not fsynced. Power
loss can lose recent records. If this handler cannot write, it emits
`diagnostic_log_write_failed` to stderr at most once per minute and allows trading
control flow to continue. Check free disk space, permissions and log freshness;
logging is not a replacement for the database order audit or a trading interlock.
