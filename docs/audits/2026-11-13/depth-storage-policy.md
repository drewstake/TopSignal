# Depth recording storage policy — November 13, 2026

The owner requested a larger heatmap recording budget and removal of the 30-day
cutoff while retaining the free-disk-space safeguard. The new protected policy
is **250 GiB** for raw depth plus totals, **10 GiB** minimum free disk space,
and `review_after_days: null`. No recordings are deleted automatically.

Before the change, retained recordings occupied 9,591,479,658 bytes (8.93 GiB)
and D: had approximately 1.19 TiB free. Recent weekday growth was approximately
0.68–0.74 GiB/day. These observations justify the larger finite budget; they
are not a guarantee of future growth or uninterrupted capture. The size limit,
32 MiB safety margin, disk-space checks, stale-inventory stop and integrity
checks remain enforced. Other evidence stores and derived-cache policies are
outside this change.

## Code and checks

`record_market_depth.py` accepts a finite budget up to 250 GiB and an explicit
JSON null to disable age review. Zero, negative, boolean and string review
values remain invalid. Integer review periods retain their existing behavior.
`depth_recording.inventory` supports that explicit disabled age policy.
Cached inventories report and enforce the current protected configuration
immediately after restart, including when their stored policy was 20 GiB/30 days.

The 82 focused recorder, inventory, totals, reliability, local-startup and policy
tests passed on Windows. They exercise preserved old evidence, the enlarged
budget, low disk space, cached policy changes and stale-inventory protection.
An isolated host fixture at **23:27:26 New York** verified local credential
decryption and startup against 90-day-old fixture evidence without broker calls
or application database initialization. Simulated low free space and excessive
growth still blocked recording, and the fixture evidence remained unchanged.

## Deployment and observed result

The independent helper is now
`C:/ProgramData/TopSignal/tools/depth-recorder-storage-v3/`. Its 251 files were
checksum-verified. Only the two recorder source files changed from the previous
helper; application modules, the registered protocol and requirements were
copied from the previous pinned helper. It still uses the immutable v2 Python
environment and protected `.env.recorder`, with the same dedicated read-only
PostgreSQL role. Administrator/SYSTEM control and service read/execute ACLs
were verified. No application release or database configuration was changed.

The old recorder PID 17264 stopped gracefully at **23:28:45.085 New York** with
348,366 received and durable events. Its final compressed and uncompressed
segment hashes matched, its last event was the explicit stop marker, and no
partial segment remained in that capture. New PID **11372**, capture
`9d4a32c204e547bf91f83981852b6a3a`, started at **23:28:51.246**, leaving a
6.161-second maintenance boundary. Activation was accepted at **23:28:59**.

At **23:30:23**, four fresh status observations showed recording, all three
subscriptions accepted, zero errors and the new policy. Health, readiness and
enabled-worker endpoints returned 200. All six account recorders were healthy,
with maximum heartbeat age 1.863 seconds. At **23:30:40**, the scheduled task
was running at priority 4; its settings, triggers and service principal matched
the previous task exactly. The application remained PID 10064 in release 99.

Protected preparation, fixture, activation, graceful-stop, source-hash, ACL,
live-health and task receipts are under
`C:/ProgramData/TopSignal/staging/depth-storage-policy-20261113/`. The patch ZIP
SHA-256 is `4958bd9d139af2e9a54bb5e206d9743e50df3ffdeb3a68c0c0cf2ff571222e6c`.
The previous helper, task XML and configuration are retained for recovery.
Full-cache transfers, backup-schedule enablement and host-reboot acceptance
remain deferred. Future recovery/configuration archives must include this new
helper and scheduled action; the earlier configuration archive predates them.
