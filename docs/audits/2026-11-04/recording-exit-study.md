# TopBot recording and exit study — November 4, 2026

Production release: `20261104-exit-study-61`.
Study: `dom-rr-59f478f9a798a8480ef23c1f`.
Protocol and operating details: [TopBot exit study](../../topbot-exit-study.md).

## Scope and preserved behavior

This adds recording, execution attribution, and research. It does not select a
live strategy. The trading configuration fingerprint remained
`44d54f8947e17ed06da71c2a9b9be26a742cb11b4caae3c70f2682aa12ebbe14`
through every deployment gate. The existing one-MNQ sizing, risk gates, daily
limits, and November 4 fixed 25-point stop/target override remain in place.
The saved configuration returns to trailing exits for the next trading session.
Read-only broker checks found zero positions and zero working orders before and
after each switch. Research made no order requests and registered no candidate.
The final read-only audit at 20:49 UTC again verified that fingerprint, a running
enabled worker, today's fixed exits, next-session trailing exits, zero broker
positions and zero working orders. Submitted attempts remained 63; 29 blocked
attempts were excluded from trade and research counts.

The baseline was release `20261104-fixed-exits-56`, repository commit
`622edbc8c946ddf9eb27d9acec9ca856a4b8a5f7`. The baseline already had persistent
runner plans, broker protection/reconciliation gates, observed MFE/MAE and timing,
an append-only action journal, worker event captures and offline replay. It also
had the session-only fixed bracket override, local execution PostgreSQL on SSD,
filesystem market history, encrypted execution backups and an off-device cache
backup job. It did not have the paired exit study or its dashboard.

## Recorder investigation and correction

The original restarted capture reported 32,522 lost events. Diagnostic messages
showed loss while the recorder was still starting. It later had 95,651 durable
events at event index 128,173; the missing events were not recovered. The old
implementation did not log its internal writer phase, so the exact syscall in
that original startup incident cannot be identified retrospectively.

The archive HDD was contended: measurements showed disk time above 100%, a
queue, and low throughput while historical feature work and backup reads ran.
A warm metadata scan took about 0.2 seconds; directory scanning alone does not
explain the incident. SQLite startup used multiple independently durable DDL
transactions. Startup now commits schema/metadata once, and immutable metadata
sidecars avoid reopening legacy databases on later starts.

Production verification of that first correction exposed another bottleneck:
the writer stalled inside `batch_commit` for over a minute during backup load,
with its 4,096-event queue full. SQLite's default automatic WAL checkpoint ran
on the committing writer. Disabling that checkpoint in a controlled host test
recorded all 180,000 events at 1,000 events/second, with zero drops, while the
backup continued. The maximum observed queue was 2,536 events.

The installed writer therefore retains FULL WAL durability but defers
checkpointing until capture close. The WAL is included in the existing storage
budget and online backup. Neither the 4,096-event / 8 MiB queue nor the existing
batch limits was increased. Producers do not wait for SQLite.

Health now exposes writer state/phase/timings, last durable event receipt and
completed commit, durable events/batches, queued/in-flight events, losses and
last drop. Pending work without progress for five seconds reports stalled.
Only committed batches are described as durable.

Release 58's eight-minute live sample advanced from 17,509 to 252,228 durable
events, with no further drops after a 510-event startup gap. Its subsequent
recovery verified 277,634 committed events and the same 510 missing indices.
The supervisor exceeded its shutdown deadline and terminated the old backend;
the recovered committed prefix remained hash-chain valid. An uncommitted tail
is unknown, not certified absent.

Release 61's 25-sample window from 17:41:05 through 17:49:27 UTC advanced from
89,880 to 266,006 durable events, with zero recorder drops and a healthy worker
at every sample. The maximum sampled queue was 353 events. This is a measured
window, not a guarantee of future coverage. Its verified capture contains a
`stream_silent` observation-gap marker at 17:44:22 UTC despite contiguous event
indices. Queue loss and missing feed observations are distinct and remain
visible to research exclusions.

After isolating backup integrity reads on SSD, a further 98 samples from
19:17:22 through 20:54:17 UTC all reported a healthy worker and active recording.
Durable events increased from 1,880,025 to 3,547,001, with zero recorder drops
and a maximum sampled queue of 110 events. This window includes backup integrity
verification, packaging, transfer and restore verification; earlier
backup-related stalls are documented below.

## Implemented requirements

| Area | Result |
| --- | --- |
| Strategy identity | Immutable experiment/model hashes, strategy version and parameter hashes; entry snapshots include rules, account/environment, contract, quantity, initial risk, session, limits and code binding. Snapshot copies also enter the append-only broker-intent journal. |
| Historical separation | Saved snapshots/plans determine fixed, trailing or unknown. Older 25/25/25 plans without explicit mode are labeled inferred. Current configuration is never used to reclassify history. |
| Actual executions | Broker entry/exit order and fill IDs, fill prices/times/quantity, risk, gross/net dollars, fees/commission basis, realized R, holding time, observed excursions, timing, slippage and coverage are retained. Original legacy records remain unchanged; derived costs identify their legacy basis. |
| Exit attribution | Fast native targets are matched against filled broker child-order history. The broker adapter now retains child parent/type/side/size fields, verified using raw provider response fixtures. Stops use verified order/price evidence; session/daily closes require a matching close order ID. Unsupported causes remain unknown. |
| Reconciliation | Identical fill retries are deduplicated; conflicting/partial fills remain explicitly ambiguous. Completed finalization is idempotent. Fill-journal identities prevent retry/restart duplicates. Local result revisions preserve their previous hash. |
| Alternatives | Nine fixed ratios from 1:1 through 3:1 in 0.25 steps, all with 25-point initial risk, plus the separate 25/25/25 trailing reference. They share the actual entry fill and risk. No shadow order-routing path exists. |
| Market paths | Feed retention continues beyond actual exit through the saved session/horizon cutoff and reloads after restart, including outstanding entries from older experiments. Replay runs in a network-disabled child process. Missing files and intervals are explicit exclusions. Older experiments require their recorded release/model and are not pooled into the current study. |
| Fill model | Chronological executable BBO, displayed size, tick rounding, one-tick limit trade-through, latched gap stops, 250 ms latency, adverse stop tick and fixed round-turn costs. Actual entry slippage is not charged twice. |
| Coverage | Missing, ambiguous and censored results have no invented prices or zero-return placeholders. All ten outcomes must resolve for the paired cohort. A full snapshot before entry can re-establish the observed book after an earlier gap; it never repairs a gap during that entry's path. |
| Dashboard | Separate actual strategy tables, strategy/version/UTC-date filters, dollars/R, nine ratio points and a separate trailing reference, counts, sessions, progress, wins/losses, average win/loss, expectancy, profit factor, intervals and exclusions. Blocked/unfilled attempts are excluded from actual trades and enrollment. |
| Validation | First 100 registered filled observations are exploratory, including registrations later excluded for coverage. Valid paired n is separate. An explicitly chosen immutable candidate is compared only on later entries after its registration and the first 100 observations. No automatic choice or promotion. |

The actual view initially contained 63 completed trailing trades across three
sessions and no completed fixed-target trades. No prospective fills were
available at the 20:49 UTC final API audit, so the research dashboard showed zero valid
paired outcomes. Fast target attribution and the complete research pipeline are
tested with fixtures; this audit does not claim a naturally occurring prospective
trade or a profitable strategy.

## Storage and backups

No database migration, cloud history import, deletion, billing change or limit
increase was performed. Raw evidence and research results remain beneath
`TOPSIGNAL_DATABENTO_CACHE_DIR` on the backend filesystem. Cloud additions are
compact entry/configuration and execution summaries, without input candle arrays.
The SQLite-only historical import guard is unchanged.

The local execution database was approximately 52 MB before deployment and
52,319,923 bytes at the 20:49 UTC final API audit. Worker evidence used
1,303,185,552 bytes; the archive drive had 1,173,963,677,696 bytes free after
retaining the backup snapshot and transport archive. Evidence retains its 20 GiB
budget, 10 GiB free-space floor and 30-day review boundary. Study stores are
limited to 256 MiB per owner, including WAL, with a 10 GiB free-space floor.
These are stop/review boundaries, not permission to purge old data.

At 100 entries/day, budgeting 8 KiB each for a compact result and additional
entry snapshot is approximately 1.6 MiB/day before database/index overhead.
The initial off-device capture snapshot contained 227,008 events in 7,847,936 bytes
over about 10.5 minutes; full-session growth depends on event rate and retained
WAL. Bounded capacity is not a guaranteed number of retained trading days.

A fresh encrypted execution backup was created and copied off device. A full
restore into an isolated audit PostgreSQL database verified all 37 tables,
11,066 rows and credential decryption. The audit database was never an execution
authority; its temporary server was stopped after validation. The restored dump
SHA-256 was `5467b6fa1425ad8ad066553b9f46c456e1474f0aa008c2c2b30b60cdc4585f16`.

An online snapshot of the final worker capture was copied to this computer and
passed SQLite integrity, SHA-256 and full event hash-chain verification: 227,008
events, zero recorder-index gaps, and the feed-gap marker described above.
Its observed range was 17:36:59–17:47:32 UTC; SHA-256:
`eaaf8e6b8463c46b38a50a0d4f681a5c17b2a0feac5748bb19cd3342124a8087`.
The earlier off-device snapshot also retains and verifies its 510-event loss.
The cache-backup tests also restore enrollment, candidate selection and all
result revisions. The scheduled full-cache run failed its 15-minute SQLite
snapshot deadline on the archive HDD at 17:52 UTC. Windows background resource
priority also made small-page reads and verification slow. A controlled copy of
the same roughly 81 MB live store, using SSD scratch, sequential cache warming
and below-normal CPU priority, completed its snapshot in 28.157 seconds and
passed integrity verification. The recorder still reported zero drops at
442,514 durable events after that check. A full scan at normal I/O priority
subsequently caused a brief recorder stall (2,189 queued events at one sample,
zero dropped events); it was stopped. The final helper keeps bulk scans/copies
at background priority and raises I/O/cache priority only around bounded SQLite
operations, restoring background mode on success or failure.
That run completed the copy, but during an in-place HDD database integrity scan,
worker health briefly returned 503 and the recorder stalled at 19:09 UTC, still
with zero drops.
The verification process was stopped and worker health recovered. Integrity
checks now use checksum-identical SSD copies of closed backup databases, keeping
random database reads away from the live archive disk. The completed snapshot
was retained and verification/packaging resumed from it.
The revised standalone helper preserves every source and backup directory;
only its newly created temporary scratch is released.

The retained snapshot `20261104T180605Z-a89f27f7` completed off-device verification
and a fresh isolated restore at 20:52 UTC: **20,455 files, 5,561,615,793 bytes**.
All file checksums and 24 SQLite integrity checks passed. The restore also
verified archived decision inputs and their original model. Coverage includes
all three retained source archives, replay/cache versions, encrypted execution
backups, worker evidence, local research stores and 9,344 sealed depth segments;
active partial depth segments are explicitly excluded. Each SQLite snapshot is
internally consistent, but the collection is not one simultaneous system-wide
snapshot. The 5,603,747,840-byte transport archive was checksum-matched off device
and flushed to disk. Its SHA-256 is
`562fa151b599c42e9536a3f93e5f13ed8a464123fd28abe13933fb36891b7673`.

The full backup's current worker-capture prefix contains **1,457,458 events**
from 17:36:59 through 18:53:40 UTC, with a valid complete hash chain and no
missing recorder indices. It retains seven `stream_silent` observation-gap
markers; contiguous recorder indices do not establish complete market coverage.
The capture SHA-256 is
`856c41e7a3fb4bc8252a383921ed195cb219ab48421d8c4eef89818a7dbf9dc5`.
Events recorded after that snapshot remain on the host for subsequent backups.

Verified backup status was published to the host. The daily task remains
enabled and uses the corrected standalone helper. This run resumed a retained
snapshot; the next unattended end-to-end scheduled run has not yet been
observed. No production study database exists yet because there have been no
prospective fills; populated study-store backup/restore is covered by tests.

## Validation and limitations

470 targeted backend tests passed, including the broker client, long/short exits, limit touch versus
trade-through, stop/target ordering, latched gaps, latency/costs/tick rounding,
post-exit evidence, missing/censored paths, fast native brackets, fill conflicts
and duplicates, abrupt recorder restart, retention restart, account isolation,
immutable corrections, backup restore and existing session/risk-rule regressions.
Twelve frontend tests passed; scoped ESLint and the production TypeScript/Vite
build passed. The additional backup checks verify scratch-file isolation,
WAL-backed contents, the free-space failure boundary and scheduling restoration
after a SQLite failure, checksum-identical integrity scratch, and rejection of
corrupted scratch copies. Live API checks covered strategy/date/cohort filters and rejection
of an unowned account. Browser checks verified the deployed panel, filtering and
dollar/R display. Candidate registration was not used on production. Checksums
verified all 1,336 deployed source/static files and unchanged private configuration.
The standalone backup helper is updated separately from the running backend;
the post-deployment audit document is also newer than the immutable release.

Coverage is observed feed coverage, never certified exchange completeness.
Known prior losses and restart intervals remain limitations. The fill model
assumes provider/local clock alignment and specified latency/costs; it does not
reproduce broker queue position. An exit-only comparison does not simulate the
entry opportunities or portfolio risk state caused by different holding periods.
The 95% intervals are session-cluster bootstrap intervals, unavailable below five
sessions and not simultaneous guarantees after choosing among nine candidates.
The dashboard bounds reads to 5,000 entries; a larger study needs explicit review.

Private receipts, capture copies and deployment manifests are retained under
`backend/storage/audits/20261104-rr`, `20261104-rr58`, `20261104-rr59`,
`20261104-rr60` and `20261104-rr61` on the
development computer, and corresponding immutable staging/release directories
on the host. They are ignored by Git. This audit's verified off-device copies
are on the development computer; ongoing backups retain the same machine,
sign-in and private-SSH availability requirements as the existing daily task.
