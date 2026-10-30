# Level 2 verification and continuous host recording

Verified October 30, 2026, at 08:39 Eastern. A separate host recorder is running;
TopBot configuration 32 / run 9 remains in paper mode. See the
[operating guide](../../topbot-depth-recording.md) for paths, limits and controls.

## Observed feed

A dedicated, read-only 30.219-second probe received:

| Event | Count |
| --- | ---: |
| Depth | 7,934 |
| Explicit ask updates (type 1) | 2,773 |
| Explicit bid updates (type 2) | 2,485 |
| Quotes | 419 |
| Trades | 410 |

The probe observed positive explicit updates at 137 distinct ask prices and 134
bid prices across the window. Of those explicit updates, 2,228 asks and 1,940 bids
had provider timestamps between zero and two seconds old at receipt. All three
market subscriptions were accepted. This establishes observed multi-level data
for `CON.F.US.MNQ.Z26`, rather than inferring entitlement from the purchase or a
successful subscription acknowledgement alone.

The probe wrote 4,848,298 raw bytes. Old/default timestamps and timestamp-order
problems were retained, including reset data; the research book was not marked
synchronized or feature-ready. Actual raw payloads contain `seq` and `epoch`, but
their continuity/snapshot guarantees have not been established. No depth model
was trained, promoted or connected to TopBot's decisions.

## Deployment and checks

The recorder is a separate limited `TopSignalSvc` scheduled task, with startup,
one-minute failure recovery and five-minute recovery triggers. It writes compressed
segments under the configured D: cache, uses a 20 GiB budget/10 GiB free-space
floor and stops for storage review after 30 days. No data is automatically deleted.

A controlled recorder-only stop sealed all four initial segments: 36,599 records,
1,150,473 compressed bytes, and zero unfinished segments. Restart succeeded. Two
temporary `InvalidStatus` connection failures on the first restart were recorded
and recovered automatically. The final process, after adding subscription and
transport timeout safeguards, had zero errors and 20,664 received events at
12:39:24 UTC, with fresh explicit Level 2 present and all subscriptions accepted.

The boot trigger and scheduler recovery settings were inspected; the PC was not
rebooted. Code ACLs give the service read/execute access only. The running process
belongs to `DESKTOP-6EUPDU1\TopSignalSvc`. No browser is required.

At 12:38:37 UTC, read-only preservation checks confirmed:

- The complete bot configuration matches the pre-deployment snapshot.
- All installed unvalidated model JSON hashes match the snapshot.
- Trading implementation SHA remains
  `856cee44fec03ded157e8d64423a19573027417f13a4dcb07d541f2b091a6da5`.
- Both live-routing flags are `false`; run 9 is `dry_run`, running, with no error.
- Bot order attempts remain zero. No test order was submitted.
- The main `TopSignal Personal Device` task XML is unchanged and its task is running.

The deployment adds no historical cloud writer and makes no change to the
Databento import guards, model/risk settings or paper execution behavior.

## Backup and validation

An initial backup attempt exposed Windows' path-length limit in recursive scp
for the probe's nested evidence filename. The backup path now transfers a hashed
tar archive, uses extended filesystem paths for extraction, and validates member
paths/types/sizes. Failed copies remain available; no originals were deleted.
The off-device schedule and destination were retained.

The successful off-device snapshot at **12:38:06 UTC** verified **70 files /
64,448,828 bytes**. The restore check passed at **12:38:07 UTC**. Both the downloaded
and restored depth trees independently passed gzip, compressed/raw SHA-256,
receipt-order and scope checks for **13 segments / 137,353 events / 4,361,948
compressed bytes**, with zero partial files. The active segment stayed on the
host for a later snapshot.

The existing Windows backup task was then invoked through Task Scheduler at
08:42:42 Eastern. It finished with exit code 0, verified 80 files / 66,108,224
bytes at 12:42:59 UTC, and published its success receipt to the host. Its next
scheduled run remains 18:15 Eastern. The final backup capacity-scan adjustment
also passed all eight backup tests.

The first short recording compressed to about 6.5 kB/second over its roughly
three-minute window. This pre-market observation is not a full-session storage
forecast. D: had approximately 1.23 TB free; configured budgets remain the actual
bounds. Full daily backups retain multiple copies and have their own 250 GB cap.

Validation: **58 tests passed** across depth recording, market-cache backup and
existing depth research. Coverage includes disconnect/resubscribe, silent
subscriptions/transports, raw field preservation, storage refusal without
deletion, locking, tampering, partial-segment exclusion, live-mode rejection,
long Windows paths, and unsafe archive-member rejection. The PowerShell installer
parsed successfully and its actual registered settings were inspected.

Private local receipts and scripts are under
`backend/storage/databento/audits/depth-recording-20261030/` (ignored by Git).
The host probe evidence is under
`D:/TopSignal/data/databento/audits/depth-verification-20261030T121319Z/`.
The verified off-device snapshot is
`C:/Users/drews/.ssh/topbot-archive-20261028/backups/20261030T123751Z-291ac99f/`.
The recorder bundle/config manifest is under
`C:/ProgramData/TopSignal/tools/depth-recorder-v1/` on the host.
