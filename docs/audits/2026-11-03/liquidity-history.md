# Liquidity recording and chart correction

## Evidence and root causes

The verified comparison covers CON.F.US.MNQ.Z26 from November 2, 2026 18:00
through November 3 00:56 Eastern (23:00–05:56 UTC). All 70,748 stored observations
were read from checksum-verified, committed filesystem segments. No segments
were skipped. Raw archives and existing summary recordings were not rewritten.

The exact 5,037-point / 1,113-exclusion browser buffer was not persisted. The
session overview mixes compacted history, a rolling 15-minute detailed archive
tail and provisional browser observations, so its count changes as it refreshes.
A CSV captured during investigation contained 4,930 points and 1,125 flagged
points. The frozen archival comparison contained 2,487 old overview points and
1,111 flagged points, reproducing the same failure with fixed source evidence.

| Quality reason | Raw observations | Old 10-second overview | Captured browser CSV |
| --- | ---: | ---: | ---: |
| Ask provider time unreliable | 1,277 | 1,111 | 1,125 |
| Bid provider time unreliable | 1,276 | 1,110 | 1,124 |
| Bid stale | 1,084 | 981 | 993 |
| Ask stale | 1,055 | 962 | 972 |
| Sampling gap (reader/client generated) | 0 | 1,102 | 1,065 |
| Missing side | 4 | 4 | 4 |
| Locked/crossed | 1 | 1 | 1 |
| Disconnect / connect / reset | 1 each | 1 each | 1 each |

Counts overlap. Only 1,277 raw observations had any flag; the former overview
unioned every flag within a bucket onto its final sample. A transient bad sample
therefore invalidated up to ten seconds. The chart then required every flag to
be absent and mid-price to exist before drawing **any** metric. An unavailable
price or one stale side erased otherwise usable liquidity totals. Last-only
sampling lost real extrema, and isolated valid samples had invisible SVG move
commands rather than visible markers.

The independent recorder synchronously scanned its entire archive from the
socket loop every 30 seconds and before opening both raw and totals segments.
Two read-only production profiles measured 4.026 and 4.157 seconds for a scan of
roughly 4,824 sealed segments / 1.48 GB. The fixed interval has 1,206 receipt gaps
over two seconds, median 3.877 seconds and 90th percentile 4.391 seconds, totaling
4,893.236 seconds. The next observation after every one of those gaps carries a
provider-time warning and at least one stale-side flag. This is strong evidence
of recorder-induced stalls; it does
not establish that every gap was caused by a scan. The longest gap is a separate
75.115-second disconnect/reconnect interval at 23:04:34–23:05:49 Eastern. Cumulative
process errors also caused later, isolated reconnects to use the maximum backoff
even after lengthy healthy connections.

The data path is ProjectX SignalR → independent `SegmentRecorder` gzip files →
`BookTotals` → separately sealed totals files → bounded, owner-scoped history
reader → authenticated API → live/archive merge → SVG chart. Completed segments
normally trail recording by about a minute. The API additionally shares duplicate
requests and caches bounded responses for 30 seconds; neither mechanism removes
rows. Failed refreshes retain the chart and display an error. Unsealed files and
unreadable segments are excluded explicitly; no unreadable segments occurred in
this comparison.

`sqlite_code=5` errors came from the separate market quote/audit archive, not the
depth recording files or history reader. At 01:13 Eastern its durable quote spool
reported 37,232 queued, 37,219 persisted, 1,239 recovered after retry, 13 exhausted
retries, and zero backlog. These counters span the spool's lifetime, not just the
comparison interval. Critical-event retry errors and decision archive errors
also appeared; this correction does not claim to reconstruct missing audit rows.

## Corrections

- Capacity inventory runs in one background job, with at most one scan pending.
  Capacity decisions conservatively charge all uncompressed bytes written since
  the scan started. Segment opening still checks current disk free space. Failed
  or stalled scans stop recording; limits, retention and source files remain.
  A concurrent segment rename is retried at most three times in that background
  job. Socket buffering remains bounded at 64 frames / 1 MiB per frame.
- Reconnect backoff uses consecutive failures and resets after a healthy
  subscription has run for 30 seconds. Subscription, transport and generation
  boundaries still clear the book; provider sequence guarantees remain unverified.
- Recorder health exposes durable write time, pending events, rejected envelopes,
  reconnects and status freshness through an owner-scoped filesystem sidecar.
  Unknown network/crash loss is reported as unknown, never a manufactured zero.
- SQLite initialization avoids repeated DDL for existing archives. Unchanged
  derived candles skip recompression and reinsertion while holding the writer
  lock. The isolated optional-quote consumer gets a bounded two-second SQLite
  wait; execution callers retain 250 ms. Existing 16-event batches, 4,096-entry
  queues, five attempts, 120-second expiry, disk limits and loss counters remain.
- Each line uses metric-specific validity. Receipt-fresh totals survive an
  unrelated price/provider-time warning; flags remain visible. Stale sides,
  missing sides, reconnects, source changes and unknown intervals break affected
  lines. No interpolation or estimated observations are added.
- The overview keeps actual first/last samples and each metric's extrema in
  ten-second buckets, with continuity computed before reduction. Original flags
  stay on their own sample. Bounds are 100,000 delivered points, 400,000 examined
  observations, existing decompression limits and 128 MiB remote responses.
- Hover shows observation time, totals, level counts, quality, generation and gap
  duration/reason. Isolated samples are visible. Polling preserves the chart,
  chosen inspection window and selection, and overlapping archive receipts align
  continuity across refreshes. CSV still exports observed values only.

## Validation

The same frozen interval now retains 16,760 actual samples. Of those, 517 have
quality flags; exclusions are 421 bid, 412 ask, 517 price and 497 imbalance samples.
These are chart-selection counts, not changes to the 1,277 flagged raw records.
The maximum recorded bid total, 1,507 contracts, is retained; the prior overview
only retained a maximum of 1,492. Both the full session and the same five-minute
window were rendered and visually inspected. Genuine historical gaps remain.

Final backend regression suite: **165 passed**, including a dedicated healthy-
connection backoff-reset regression. Frontend: **38 passed** across history,
live updates, Bot page integration, order-book
batching and research views. Production frontend compilation/build passed using
the deployed source and environment, with the existing lockfile and dependencies.
Tests include real SQLite lock contention beyond the execution deadline, retries
without duplicate writes, reconnects, transport/subscription timeouts, corruption,
owner isolation, stale sides, independent metrics, extrema, hidden continuity
breaks, zoom/selection retention and hover details.

Private evidence is retained under
`backend/storage/databento/audits/liquidity-20261103/` and
`C:/ProgramData/TopSignal/staging/liquidity-45/` and `liquidity-46/` on peteamdpc. This includes the
frozen observations, before/after responses and screenshots, captured CSV,
configuration fingerprints, release manifests and rollback sources. These ignored
files are filesystem evidence, not Supabase data.

## Deployment and unrecoverable intervals

Production release **`20261103-liquidity-46`** became healthy at 01:53:17 Eastern
on November 3, rebased onto `20261103-local-execution-45`. All nine intended
source changes were checked against the actual deployed baseline. The migration's
database authority, environment, operator-helper corrections, and prior frontend
assets were preserved. `/health`, `/ready`, worker health and the frontend passed.
The first attempt before the migration completed made no changes. A subsequent
activation caught a missing service read/execute permission on the new directory
and automatically restored release 45. Matching the prior release's service
permissions and adding a permission preflight resolved that deployment issue.

The independent recorder received only the corrected `record_market_depth.py`;
its pinned v1 totals implementation, configuration, recording root and scheduled
task stayed intact. A fake-socket reconnect test against those actual pinned
dependencies verified six segments / 14 events before the update. The old capture
sealed all **37,186,261** received raw events durably. Its last event at
01:53:35.335 Eastern and the new capture's first event at 01:54:05.907 leave a
**30.572-second explicit restart boundary**. No old recording was overwritten.

The first complete post-update five-minute interval, 01:54:11.227–01:59:11.227
Eastern, was read from verified sealed segments:

| Recording check | Before, 00:40–00:45 | After, 01:54:11–01:59:11 |
| --- | ---: | ---: |
| Observed measurements | 876 | 999 |
| Flagged measurements | 15 | 1 |
| Receipt gaps over two seconds | 15 | 1 |
| Total duration of those gaps | 64.685 s | 2.474 s |
| Skipped/corrupt segments | 0 | 0 |

These are different live periods; feed activity can vary. The frozen comparison
above separately verifies chart behavior on identical observations. Its 16,760
selected points match all tested values, flags and continuity metadata exactly.
The production response additionally includes side/provider ages, checked against
the frozen raw measurements. Its complete point-array SHA-256 is
`ae8916b83a3db849212be98bd3e4ed4940541301929a3157bcf7d306fa8f788b`.

At 02:01 Eastern, background capacity scans still took about 4.4 seconds, while
the feed continued recording. Recorder health showed fresh durable writes, zero
rejected envelopes, zero reconnects and no error. Unknown upstream/crash loss
remains explicitly unknown. The remaining 2.474-second receipt interval cannot
be attributed confidently to source inactivity, transport or local I/O.

SQLite contention still occurred and was recovered through retries. By 02:01,
37,549 optional quotes had been durably queued and 37,536 persisted; the lifetime
drop count remained **13**, with **zero pending**. This observation does not
guarantee that future prolonged contention cannot exhaust the bounded retries.

Production session and five-minute views were visually inspected. The later
01:56:07–02:01:07 view displayed 1,022 observations with no flags and readable
continuous lines. Live timestamps advanced from 01:59:50 to 02:00:23, then to
02:03:01 after resuming. A selected 21:44:30 observation and zoomed axis stayed
unchanged during inspection. Hover exposed time, totals, observed levels, flags
and a historical 4.59-second gap reason. Evidence includes
`production-session.png`, `production-five-minutes.png`, and both fixed-interval
comparison screenshots in the private audit directory.

The final read-only broker/configuration check at 02:02:52 Eastern confirmed the
original full configuration fingerprint, enabled state and running run 11; the
account had no open positions or orders. All scheduled task definitions were
verified unchanged by the recorder update. The main application, independent
depth recorder and microstructure task were running. Trading and stop-management
code/settings were not modified by this correction.

Network disconnects, unknown receipt intervals and process-crash omissions cannot
be repaired by drawing lines. Raw events remain available for later research,
but delayed receipt timestamps do not recover the true timing of unseen events.
The 13 expired optional quotes have only aggregate loss counters; their individual
identities are not retained in that spool. No historical values are invented to
replace them, and no recordings or trading/audit records are purged.
