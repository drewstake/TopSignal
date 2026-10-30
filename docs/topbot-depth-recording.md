# Continuous TopBot depth recording

Deployed on peteamdpc (`DESKTOP-6EUPDU1`) on October 30, 2026. The independent
`TopSignal Depth Recorder` Windows scheduled task records the active MNQ delivery
contract from the existing owner's ProjectX credentials. TopBot remains in paper
mode. This recorder does not supply model inputs or place orders.

The [deployment audit](audits/2026-10-30/topbot-level2-recording.md) contains the
observed Level 2 verification, preservation checks and backup evidence.

## Host layout

| Purpose | Host path |
| --- | --- |
| Recorder bundle and config | `C:/ProgramData/TopSignal/tools/depth-recorder-v1/` |
| Status, process lock and optional STOP file | `C:/ProgramData/TopSignal/runtime/depth-recorder/` |
| Raw compressed depth, trades and quotes | `D:/TopSignal/data/databento/depth-recordings-v1/` |
| Pinned backend dependencies and environment | `C:/ProgramData/TopSignal/releases/20261030-dashboard-18/backend/` |

Storage follows [the Supabase storage policy](supabase-storage-policy.md). The
recording root must match `TOPSIGNAL_DATABENTO_CACHE_DIR/depth-recordings-v1`.
There are no new Supabase tables, historical cloud writes, or result arrays.
Credentials are read in a PostgreSQL read-only transaction for bot configuration
32. Both live-routing environment flags must be exactly `false`, and the bot
configuration must be `dry_run` for MNQ. The market client rejects order routes.

The task uses the existing limited `TopSignalSvc` account, runs without a browser
or interactive login, starts at boot, and has a one-minute restart-on-failure
setting and five-minute recovery trigger. An OS-owned lock and `IgnoreNew` task
policy prevent overlapping recorders. The service has read/execute access to its
code and modify access to its own recording/runtime directories. The main TopBot
task is unchanged. Keep the pinned backend release available; a later dependency
upgrade must update and verify the recorder's config and scheduled Python path.

## Recording contract and limits

Each process creates a UTC-date/run-ID directory. Segments rotate after 60 seconds
or 16 MiB of uncompressed events. Active files end in `.ndjson.gz.partial`.
Completed gzip files have immutable `.segment.json` commit markers with both
compressed and uncompressed SHA-256 checksums, receipt index bounds, counts and
owner/stream scope. Flush/fsync runs about once per second while connected; sealing
finishes the gzip footer and fsyncs before publishing the marker.

Every recognized quote, trade and depth payload is preserved with its original
fields, provider timestamp, local UTC receipt timestamp and monotonic receipt
index. `seq` and `epoch` are preserved when supplied. No deliberate event sampling
is applied. Disconnects, reconnects, malformed envelopes and delivery rolls leave
explicit markers. Contract selection is rechecked every five minutes; each record
retains its actual delivery. This does not roll TopBot's trading configuration.

The initial recording budget is **20 GiB**, with **10 GiB free disk space reserved**
and **review required after 30 days** from the oldest retained recording file.
A 32 MiB safety margin is checked before opening each segment; inventory is also
refreshed at least every 30 seconds. Reaching a boundary stops recording and sets
`state=storage_blocked`. It never deletes evidence or increases budgets itself.
Review capacity and arrange authorized archival/retention before that point.

Network reconnect delays are bounded at 60 seconds. Unacknowledged subscriptions
time out after 15 seconds, and absent server frames after 45 seconds; server
heartbeats permit quiet or closed markets without requiring price events.
Authentication/permission failures exit for a supervised fresh credential read.
Status reports exception types/codes without bearer tokens or exception strings.

Capture is not guaranteed lossless: provider/network omissions, bounded socket
buffers, process crashes and storage failures can leave gaps. An interrupted
partial file remains on the host for inspection and is excluded from verified
backups. No automatic truncation, repair or deletion is performed. A reconnect
does not backfill missed depth.

## Health and operation

### Contract totals and imbalance history

The recorder's `record_contract_totals: true` option adds an independent compact
stream under `depth-recordings-v1/totals/<UTC date>/<run ID>/`. It runs without a
browser and uses the same `MarketByPriceBook` aggregate `volume` semantics as the
display ladder. It does not count orders, traders, executed volume, or
`currentVolume` as resting contracts. The totals cover **all observed levels**,
independent of the browser's selected level count. Separately recorded nearest
10/20/50/100-level totals and actual level counts allow comparable depth scopes.

Each `kind=book_totals` record contains UTC receipt time, monotonic time, raw
capture/receipt references, bid/ask contract sums and level counts, best bid/ask,
mid-price, spread, last trade price and age, and normalized imbalance:
`(bid_contracts - ask_contracts) / (bid_contracts + ask_contracts)`. Positive
values mean more observed bid contracts; an empty book has null imbalance.
Price outcomes can be joined later at 5/15/60-second horizons. No future prices
are used to construct measurements, and no predictive model is enabled.

All received depth changes update the in-memory book. Measurements are emitted
at most once per 250 ms during event activity, plus immediate boundary records.
Quiet intervals are not filled with fabricated observations. Reconnects, gaps,
contract changes, resets, and observed epoch changes clear prior book/trade state
and increment a generation. Missing sides, crossed/locked books, side updates
older than two seconds, and unreliable provider times are flagged. A fresh side
does not prove every resting level is fresh. Snapshot completeness and provider
sequence guarantees remain explicitly **unverified**; totals are observed-book
measurements, not a certified full exchange book or a proven trading signal.

Summary files use the existing compressed event envelope, `.segment.json` commit
markers, verification, and backup inclusion. They share the existing 20 GiB
recording budget, free-space reserve, and review boundary; raw capture is
unchanged. The summary stream has its own receipt index and `capture_id`; each
payload's `source_capture_id`/`source_index` identifies the latest raw input.
Backups snapshot committed files independently, so a referenced raw segment may
be sealed and backed up later. Summary measurements are self-contained.

`status.json` exposes `recording.contract_totals` with sample count, sealed byte
count, run ID, and the latest measurement. To verify only this compact history:

```powershell
& 'C:/ProgramData/TopSignal/releases/20261030-dashboard-18/backend/.venv/Scripts/python.exe' -B 'C:/ProgramData/TopSignal/tools/depth-recorder-v1/record_market_depth.py' --verify 'D:/TopSignal/data/databento/depth-recordings-v1/totals'
```

Read `status.json` for `state`, `at`, `last_frame_at`, acknowledgements, latest
depth receipt, event counts, durable index, reconnect/errors and storage bounds.
Counts are per process; segment manifests provide the retained history. A stale
status timestamp is not proof of a running recorder. Compare latest depth with
market hours and current quote/trade activity before diagnosing a feed outage.

`level2_observed=true` means fresh explicit ask/bid updates at multiple prices
were observed in a two-second receipt window. The price counts describe updates,
not a complete reconstructed order book. Old/invalid provider timestamps remain
recorded and are counted separately. `data_live=false` identifies the requested
ProjectX subscription environment; it is not a claim that prices are delayed.

`snapshot_completion_verified`, `provider_sequence_guarantee` and
`model_inputs_enabled` remain **false**. Sequence/reset/snapshot semantics and
paper-v2 feature timing must be validated before training or using this data for
decisions. Buying Level 2 or successfully recording it does not establish a
profitable model.

Run on the host to inspect status and verify completed segments:

```powershell
Get-ScheduledTask -TaskName 'TopSignal Depth Recorder'
Get-Content -Raw 'C:/ProgramData/TopSignal/runtime/depth-recorder/status.json'
& 'C:/ProgramData/TopSignal/releases/20261030-dashboard-18/backend/.venv/Scripts/python.exe' -B 'C:/ProgramData/TopSignal/tools/depth-recorder-v1/record_market_depth.py' --verify 'D:/TopSignal/data/databento/depth-recordings-v1'
```

For a graceful recorder-only stop, create its private STOP file and wait for
`state=stopped`, `reason=stop_file`, and the task to become Ready. Leave the STOP
file present to prevent the recovery trigger from restarting collection. To
resume, remove that exact STOP file and start `TopSignal Depth Recorder`.
These controls do not stop TopBot or alter its paper mode. Do not use the main
TopSignal runtime's STOP file for recorder maintenance.

## Off-device backup

The existing `TopSignal Off-device Archive Backup` task now includes only completed
depth segments present at its snapshot boundary. It still runs at 18:15 Eastern
on the development PC, requiring that PC to be on, the user signed in, and private
SSH available. Recording itself has no development-PC dependency.

The host helper is the recorder bundle's `backup_market_cache.py`. The installed
development tools remain under
`C:/Users/drews/.ssh/topbot-archive-20261028/scheduled-tools-v17/`, with the existing
private config and receipts beside that directory. Immutable snapshots are packed
into a single `.tar` for transfer, hashed before and after transfer, extracted
with path/type/size restrictions, and checked against their manifest. Python uses
Windows extended paths for deeply nested evidence; no registry policy changed.
Restore checks continue to verify the archived decision inputs and models.

The existing 250 GB budgets and 5 GB free-space floors remain. Capacity checks now
include the transport archive: up to two new copies on host staging and three on
the development PC for archive, extracted snapshot and restore. Transport files,
failed attempts and source evidence are retained. Daily full backups can reach
their separate budget sooner than the recording budget; review growth during
regular trading hours. Neither Git nor Supabase backs up these ignored files.
