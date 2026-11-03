# Complete TopBot release validation — November 3, 2026

This release consolidates worker-input evidence and offline replay, execution
timings and commission reporting, session-entry pauses, provider health checks,
the entry-check dashboard and account/session daily profit protection.

Daily profit protection pauses entries at +$400, or at $0 after reaching +$200,
and closes only the owned TopBot position through existing broker verification
and mutation fences. The state survives worker restarts and resets at the
existing trading-session boundary. Saved trading settings remain unchanged.
These thresholds are polling triggers, not guaranteed exit proceeds.

Validation of the complete workspace before packaging:

- Backend: **3,088 passed, 20 skipped, eight subtests passed**.
- Frontend: **1,409 passed across 169 files**, using four workers. An initial
  unrestricted run had one loading-timeout failure; the affected 35-test file
  passed alone and the full suite then passed with bounded concurrency.
- Repository-wide frontend lint and TypeScript/Vite production build passed.
- All ten Windows production startup/deployment tests passed.

The new profit-guard state is one local row per owner/account in the existing
backed-up action journal, with audit events only at state transitions. Entry
checks add bounded details to existing decision records. No migration, bulk
historical cloud write, retention deletion or billing change is required.

The secondary research-archive lock contention documented in the
[evidence deployment report](topbot-evidence-deployment.md) remains a known
limitation. This consolidation does not claim to repair missed duplicate
archive copies; primary decisions and worker evidence remain authoritative.

Deployment activation and final runtime verification are recorded after the
committed source and built frontend are installed as an immutable release.
Private configuration, account-specific receipts and backups remain outside Git.

## Deployment follow-up

Release 53 activated at 18:33:50 Eastern with all health endpoints passing.
The deployed dashboard showed entry checks and current daily profit protection.
All GitHub CI lanes passed for its source commit, including Windows and the
PostgreSQL integration checks.

A subsequent heavier burst exposed the recorder's throughput limit: batches
stopped at 128 events even when thousands were queued, causing excessive durable
disk commits and reported capture drops. The follow-up permits up to 2,048
events per batch while retaining the existing 4 MiB batch-byte bound, 250 ms
collection window, 4,096-event queue and 8 MiB queued-byte limit. FULL-sync
durability and explicit loss reporting remain unchanged. A regression verifies
that a queued 4,000-event burst is completely durable in no more than four
commits. All 116 targeted recorder, strategy, profit-guard and worker-health
tests passed after this correction. Existing lost events remain marked as lost.
