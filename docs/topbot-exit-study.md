# TopBot recording and exit-only comparison

This feature records the existing strategy and runs research only. It does not
select a live strategy, change the session-only fixed bracket override, alter
quantity or account limits, or submit additional broker orders.

## Prespecified study

The immutable protocol is `PROTOCOL` in `backend/app/services/topbot_study.py`.
Its identifier hashes both the protocol and the normalized source of the fill
model, evidence reader and book reconstruction. A source/model change creates a
different experiment; results from incompatible experiments are not pooled.

Each prospective entry saves entry and exit rules, quantity, contract, account,
environment, session, risk limits, initial dollar risk, parameter hash and source
fingerprint before submission. That snapshot is also copied into the append-only
broker-intent journal. Historical classification uses only saved plans and code
references. Inferred trailing plans are explicitly labeled; unsupported cases
stay unknown. Original historical execution records are not rewritten.

Nine fixed exits use a 25-point initial stop and reward:risk ratios 1, 1.25, 1.5,
1.75, 2, 2.25, 2.5, 2.75 and 3. Targets are respectively 25, 31.25, 37.5, 43.75,
50, 56.25, 62.5, 68.75 and 75 points. The trailing reference uses a 25-point
initial stop, 25-point activation and 25-point trail. Every alternative uses the
same actual entry fill and $50 initial risk for one MNQ contract.

The horizon ends at the earlier of the saved session flatten deadline or two
hours from registration at entry submission. Research feed retention survives
the actual trade's exit and a stopped/paused entry run. After a backend restart,
the reader reloads outstanding retention obligations. A restart's missing market
interval is not reconstructed: an incomplete source path stays excluded.
Outstanding retention from an earlier experiment is also preserved. Its results
must be replayed with that experiment's retained release/model; the current
dashboard does not mix old experiments into a new protocol's cohort.

This is an exit-only study. Different holding periods would change subsequent
entry eligibility. Daily account profit/loss rules are preserved and recorded for
actual execution; counterfactual portfolio risk state and complete independent
trading schedules are not simulated.

## Fill and cost model

Only chronological, hash-chain-verified worker events are replayed. Neither
candle highs/lows nor saved MFE/MAE establish simulated fills. Long exits use bid;
short exits use ask. Displayed size must cover the one-contract exit. Limits
must trade through by one tick and fill at their tick-rounded target, never on
a mere touch. Initial target activation and stop execution latency are 250 ms.
A stop trigger is latched; it fills at the subsequent executable price after
latency, with one adverse tick and adverse-direction tick rounding. Gaps through
the stop therefore do not receive the stop price.

The trailing reference observes MFE continuously, but evaluates amendments at
a nominal five-second management interval and applies them after 250 ms. It
never widens the stop. Real worker scheduling, broker queueing and acknowledgement
delays vary; this is a specified model of the rule, not a recreation of every
live request. Costs are fixed at $1.72 per round turn. Entry slippage is already
in the actual anchor fill and is not charged again. The fixed cost and latency
assumptions are not estimates of each future execution.

Provider fills and local receipt times are assumed aligned for research; their
clock offset is unverified. Recorder index gaps, invalid/crossed quotes, feed
loss and quote gaps over two seconds invalidate unresolved alternatives. A gap
before entry may be followed by a saved full book snapshot that re-establishes
the observed starting book; this never repairs missing evidence during a trade.
Missing tails are missing, and unresolved alternatives with evidence through the cutoff
are censored. Missing/ambiguous/censored outcomes have no invented exit price or
zero-profit placeholder. Provider snapshots are not certified complete exchange
history, even when the observed recorder sequence is continuous.

## Cohorts and uncertainty

All ten outcomes must resolve for an entry to enter the paired comparison.
Each ratio therefore uses exactly the same valid entries. The dashboard exposes
pending, excluded and unregistered historical entries, coverage rates, distinct
entries/sessions, wins/losses, average win/loss, expectancy and profit factor.
Undefined values are displayed as unavailable. Coverage exclusions can bias the
cohort; nine variants on 100 entries are 100 observations, not 900 independent
trades.

The first 100 registered filled entries are exploratory. The milestone counts
registrations across filters; excluded observations still consume a registration.
The filtered valid paired count is shown separately. Blocked or verified unfilled
attempts are not actual trades and do not enroll in research. The dashboard can
freeze one candidate, immutably, for this experiment/account. Validation excludes
the first 100 registrations and every entry at or before that choice was saved.
It exposes only the selected candidate and trailing reference in that cohort,
including their paired difference. No candidate is registered automatically.
Inspecting validation consumes its unseen status; repeated peeking is not a
new independent validation. A later candidate needs a new prespecified study.

Mean intervals use 1,000 deterministic session-cluster bootstrap resamples and
are unavailable below five sessions. They are pointwise exploratory intervals,
not simultaneous guarantees after selecting the best of nine candidates. A
100-entry milestone alone establishes neither profitability nor readiness for
live trading. There is no promotion action or automated routing path.

## Persistence, bounds and recovery

Worker evidence remains under
`TOPSIGNAL_DATABENTO_CACHE_DIR/topbot-worker-evidence-v1`. SQLite WAL/FULL batches
are append-only and hash chained. Schema/metadata creation is one transaction,
avoiding separate durable writes for each CREATE statement during startup.
The writer disables SQLite's synchronous automatic checkpoint at 1,000 WAL
pages. A checkpoint can spend a minute seeking/writing database pages on the
busy archive HDD while blocking the sole append writer. Commits still use FULL
WAL durability; checkpointing is deferred until capture close. The retained WAL
is included in capacity checks and online backups, and crash recovery replays
its committed prefix. This trades additional bounded WAL disk space for avoiding
checkpoint pauses in the recording path; it does not enlarge the memory queue.
Immutable metadata sidecars reduce reopening old WAL databases on subsequent
starts. The 4,096-event / 8 MiB queue is unchanged. Its producer never waits for
SQLite; overload is counted and cannot be presented as complete coverage.

Live health includes durable event/batch counts, last durable event receipt time,
last completed commit, queued/in-flight events, dropped events, last drop,
writer phase/timing and stalled progress. Only committed events are durable.
Saved status from a dead process does not certify current liveness; a forced
termination may leave an unknown uncommitted tail.

The existing 20 GiB evidence budget, 10 GiB disk-free floor and 30-day review
boundary remain. No automatic purge is added. Research results and registrations
are local, owner-scoped SQLite under `topbot-exit-study-v1`, with a 256 MiB
per-owner result-store bound and 10 GiB free-space floor. Result corrections
append a new hash/revision referring to the original; original rows are retained.
The dashboard bounds actual and research reads to 5,000 entries. Long studies
need an explicit capacity and reporting review.

The background reader is separate from trading. Expensive replay runs in a
network-disabled child process at below-normal Windows CPU priority and
background I/O priority; it cannot place orders.

### Replay scheduling, checkpoints and failures (November 11, 2026)

Every replay used to re-read and re-verify its capture from event 1, and a fixed
300-second deadline killed it. Book reconstruction runs at roughly 12,000 events
per second, so any entry more than about an hour into a busy capture could never
finish; it was retried from the beginning on a backoff while competing with the
live recorder for the archive disk. On November 11 every replay attempt timed out.

`topbot_replay_index.py` now keeps derived checkpoints of the recorded reader's
state at batch boundaries (every five minutes of receipt time). It mirrors the
hash-chain checks of `recorded_batches` and the per-row logic of
`topbot_study_store.quotes` exactly; tests replay randomized captures with index
holes, sequence gaps, stale timestamps and silences and require identical quotes
and identical `simulate()` results for every start. The mirror is only enabled while
the source fingerprints of those functions and `DepthState` match; any model
change disables it and replays use the original full scan. Each checkpoint is
anchored to its capture batch hash and metadata hash and carries its own SHA-256; a
mismatch is a checkpoint error that falls back to the full scan and is never
recorded as missing market evidence. Results carry `replay` provenance (reader,
checkpoint anchor, events read, seconds).

Each child first extends the capture's checkpoints toward the entry within a
90-second budget; if it runs out it saves its position and returns
`index_progress`, which is not a failure. Replays that keep reporting progress are
not killed for being slow. A child is ended only when it stops reporting progress
for three minutes or reaches a 30-minute safety cap. Before launching, and while
a child runs, the reader checks the worker capture for storage backpressure
(stall, retrying writer, more than 1,024 queued events, a queue older than two
seconds or a commit slower than two seconds); two consecutive observations stop
research and retry in a minute, without counting a failure.

Per-entry job state lives in `replay-jobs.sqlite3` beside the results: attempts,
consecutive failures, next retry, the first and latest sanitized failure (fixed
category, exception type, phase, source location; never messages or paths) and
the last progress. It survives restarts. Six consecutive failures mark an entry
`failed`; it still retries at the six-hour cadence and stays visible. A successful
scan does not clear an unresolved failure. The Bot page shows, per account,
entries whose observation window is open, queued, replaying, retrying, failed,
valid and excluded, with explicit denominators.

Before replaying a later entry, the child extends an older checkpoint index
through that entry in resumable slices. An existing checkpoint alone is not
enough: otherwise later trades would repeatedly scan the growing unindexed
tail. The heatmap deriver yields while a replay runs or the recorder reports
backpressure, avoiding simultaneous optional archive readers.

Releases now refuse to stop the application while post-exit observation windows
are open unless the private host configuration sets
`accept_research_window_gaps`. A restart inside a window ends that capture, and the
missing interval is excluded rather than reconstructed.

### Fixed take-profit baseline

`topbot_study_report.py` answers whether a larger fixed take-profit than the
current 25-point target would have done better with the stop fixed at 25 points.
It pairs each larger target with the 25-point variant on the same valid exploratory
entries, winners and losers alike, using the simulated chronological target/stop
order and $1.72 costs. It reports mean and total differences, entries better,
worse and unchanged, 25-point winners that a larger target turned into stops,
pointwise 95% and Bonferroni-adjusted session-cluster intervals, and a reading
that stays `insufficient_data` below 30 entries or five sessions. The trailing
reference is shown separately and is not the baseline. Excluded entries with some
resolved outcomes are listed as partial observations and never averaged. Actual
fixed-bracket broker exits are compared with the simulated 25-point outcome as a
model check. The held-out validation cohort is not used. Broker execution identifiers are stored in the normal compact account
records and deduplicated append-only fill journal. Ambiguous/partial exposure
continues to require explicit reconciliation under the existing execution gates.

The fixed-target baseline table follows the selected strategy, version and date
filters, while account-wide processing readiness retains its explicit scope.

The existing cache backup discovers the evidence and study SQLite files and uses
online snapshots, not direct copies of live WAL databases. Source archives remain
in their existing locations. These ignored local files need off-device backup;
Git and Supabase alone do not protect them. New cloud data is limited to compact
entry/configuration and execution summaries; no input paths or replay arrays are
added to cloud records.

The Windows cache-backup helper keeps bulk scans and copies at background
resource priority. Its bounded SQLite operations temporarily use normal I/O
priority and below-normal CPU priority: full background mode starved small
SQLite reads and trimmed verification caches on the production HDD. Normal
priority for the entire archive scan caused recorder stalls, so it is limited
to those SQLite steps and background priority is restored even on failure.
When system temporary storage is on a different drive, the
helper first reads a bounded source-file prefix sequentially, then takes a
consistent SQLite snapshot in its own SSD scratch directory and checksum-checks
the sequential copy to backup staging. The preliminary reads are only cache
warming; the SQLite online API supplies the backed-up data. A 5 GB scratch
free-space floor applies in addition to snapshot size. Only that invocation's
temporary scratch is removed; source databases, original archives and completed
or failed backup staging remain preserved.

Integrity verification also copies each closed backup database sequentially to
SSD scratch and matches its checksum before running SQLite's integrity check.
This avoids random integrity-scan reads on the live archive HDD. It never copies
a live database this way: live database snapshots still use the SQLite backup API.

At 100 entries/day, allowing 8 KiB per compact research result and 8 KiB per
additional entry snapshot gives approximately 1.6 MiB/day before indexes and
SQLite/WAL overhead. Actual source-stream growth must be measured separately;
the evidence budget is a stop boundary, not a guaranteed retention period.

## Verification

Relevant suites include `test_topbot_evidence.py`, `test_topbot_study.py`,
`test_topbot_study_worker.py`, `test_topbot_fixed_exits.py`, `test_topbot_dom.py`,
the runner/session/profit-guard/recovery suites and `test_market_cache_backup.py`.
Frontend tests cover the comparison, strategy panel and recording health. See
the deployment audit for measured host health, backup receipts and limitations.
