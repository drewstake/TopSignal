# TopBot worker evidence and execution diagnostics

Implemented and deployed on November 3, 2026 as
`20261103-worker-evidence-52` on peteamdpc. See the
[deployment verification](audits/2026-11-03/topbot-evidence-deployment.md) for the
exact installed scope, backups, live checks and recording limits.

This change records the DOM contrarian worker's actual normalized inputs. It
preserves the entry threshold, qualification period, available-level behavior,
stop/trail rules, account gates and worker cadence. The independent raw recorder
remains useful for provider research; it is not substituted for worker input.

## What is recorded

- Each consumed state/snapshot/update, its local UTC receipt time, monotonic
  timestamp and capture-local index. Only fields used by the worker are copied;
  provider credentials and free-form transport messages are excluded.
- Threshold/persistence changes, episode starts, resets, and first qualification,
  including episodes that end before the next worker evaluation. These are signal
  observations, not claims that account/risk checks would permit an entry.
- Worker observations carry capture/index references and a deterministic hash of
  the book and qualification state. Decisions, final risk checks, pre-submission
  quotes and stop-change requests retain the relevant references.
- A code fingerprint binds normalized source hashes for strategy, feed adapter,
  worker, execution, risk and evidence modules. The complete file hash map is in
  each capture's metadata; compact identity accompanies decisions/actions.
  Release trees must remain immutable while running. This is a source binding,
  not a claim to fingerprint the operating system or every installed dependency.
- Broker mutation requests remain durably journaled before the call. Outcomes
  now include actual local request/response timestamps, monotonic request duration,
  and the preceding audit-write duration. Acknowledgements still require broker
  readback/fills to establish completion. Stop readbacks use the actual completion
  clock, rather than the earlier management-cycle timestamp.
- Trade summaries include pre-submit executable-price slippage, final-check and
  qualification-to-submit deltas, fill-observation delay, initial protection
  observation, stop-request-to-verification timing, and exit observation delay.
  Missing measurements are null/absent. Positive slippage is adverse. Provider
  timestamps and local clocks are not certified synchronized; signed deltas are
  retained, including negative values. Protection-observation delay is not a
  measurement of how long the broker lacked a stop.

Excursions remain explicitly incomplete. Each trade retains bounded gap counts,
closed-gap seconds, the last gap and any still-open gap. Detailed interval events
live in the local stream. Initial fill-to-tracking time, disconnects, stale data,
sequence gaps and resumed tracking are distinguished. Neither zero observed gaps
nor an apparently fresh book certifies complete exchange history. Tracking state
persisted on a previous management cycle can omit the tail before a crash.

The existing trade table exposes execution/coverage details, and the action audit
retains the underlying evidence. The feed-recording status identifies capture
loss and storage refusal. No new broker request or evaluation is triggered by
viewing this information. Terminal consumer exceptions log safe type/frame
information; exception text and credentials are excluded.

## Filesystem storage and failure behavior

Files live beneath
`TOPSIGNAL_DATABENTO_CACHE_DIR/topbot-worker-evidence-v1/<owner SHA256>/<capture ID>.sqlite3`.
Each subscription has its own capture. SQLite WAL/FULL transactions contain
compressed, hash-chained batches with scope, index and count verification. The
existing cache backup discovers these SQLite files and takes consistent online
backups. Keep the corresponding installed source release with the evidence.

The producer queue is capped at 4,096 events and 8 MiB; batches are capped at 128
events and approximately 5 MiB before compression. The writer waits at most
250 ms to batch normal input. Disk I/O runs on a separate thread outside the
market-book lock. Overload drops evidence explicitly, records index holes and
cumulative loss, and does not block trading. A missing/corrupt event prevents
exact-reproduction certification after that gap. Capture is never claimed durable
until its SQLite batch commits. Crashes can lose a queued/uncommitted tail.

The initial evidence-directory budget is **20 GiB**, with a **10 GiB free-space
floor**, conservative space for SQLite/WAL growth, and a **30-day review boundary**.
The writer checks capacity before each batch. It stops recording and reports
`storage_blocked` rather than deleting data or changing trading rules. These
bounds are for this additional stream; the independent recorder has its own
budget. A directory override exists for isolated tests/controlled local storage,
`TOPSIGNAL_TOPBOT_EVIDENCE_DIR`; production should use the cache default so backups
include it. No historical input arrays or new history tables go to Supabase.

Estimate growth from a representative capture before extending collection:
compressed bytes / consumed events × expected events per day, plus SQLite/WAL
headroom. For example, 100 compressed bytes/event at 300 events/second is roughly
2.6 GB/day before database overhead. **The initial cap is not a promise of 30 days
of capture.** Measure real sustained volume, inspect disk usage, and arrange
verified backup and reviewed retention/capacity before a boundary is reached.
No automatic deletion, billing change or historical-data restoration is included.

A local synthetic check at 300 events/second (10% 100-level snapshots) captured
all 1,500 events without loss and matched the previous book/qualification state
after every event. Consumer processing measured 0.08 ms median / 0.21 ms p95.
Compressed payload growth extrapolated to 1.58 GB/day; the small SQLite sample
included additional page overhead. This five-second synthetic run is not a
production volume or latency guarantee. The development disk had about 926 GB
free at the check; production capacity must be rechecked before rollout.

## Read-only replay and trade timelines

From the backend directory, with the matching release's Python environment:

```powershell
./.venv/Scripts/python.exe -B tools/replay_topbot_dom.py replay C:/evidence/capture.sqlite3
./.venv/Scripts/python.exe -B tools/replay_topbot_dom.py replay C:/evidence/capture.sqlite3 --observation C:/evidence/depth-entry.json
./.venv/Scripts/python.exe -B tools/replay_topbot_dom.py timeline C:/evidence/actions.sqlite3 --account 123 --entry 456
```

The observation JSON is the saved complete `depth_entry` object, including
`checked_at` and `evidence`. Replay verifies the hash chain, owner/contract capture
metadata, ordered indices, code identity, book hash and recorded observation.
The timeline reads only the requested account/entry and linked evaluations,
distinguishing measured request time from record-to-record elapsed time. Old
records remain readable with unavailable stages labeled unknown.

`--threshold` and `--persistence-seconds` replay alternative signal rules against
the same observed input. They produce episode counts/examples, not invented
broker fills or P&L. `--allow-code-mismatch` permits explicitly unverified research;
it cannot certify a historical observation. Replay uses read-only SQLite
transactions, streams batches, and the CLI disables socket networking. It does
not import the application entry point, use credentials, start a worker, fit a
model, place an order, or write to either database.

This is a DOM signal/state replay and execution-evidence tool. A validated
counterfactual execution backtest still requires an explicitly tested fill,
latency, account-risk and cost model. Existing candle/probabilistic backtests
must not be presented as validation of this strategy.

## Verification

The final development run passed 572 backend tests covering capture/replay,
missing/tampered events, bounded storage and queues, recording failures, backup
restoration, action timing, account-scoped timelines, strategy execution, runner
recovery, worker behavior, existing profit/entry checks, and archive compatibility.
Nineteen frontend tests and scoped ESLint checks passed, followed by the TypeScript
and Vite production build. The subsequent production deployment was rebuilt from
the actual running release to exclude unrelated unfinished workspace changes;
its separate validation and live results are in the deployment report above.
