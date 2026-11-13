# Storage and recording remediation — prepared release evidence

This is a preparation receipt, not a deployment or retirement declaration.
Production was reverified on `20261112-auto-signin-87` on November 13 at
19:10 UTC. No application/helper restart, enrollment, migration, trading change,
schedule enablement, heavy backup or cloud deletion has been performed by this
work. The prior maintenance restriction remains in force.

## Current evidence

Sanitized scripts and detailed receipts are in ignored `tmp/remediation-20261113/`.
The earlier [health audit](peteamdpc-health.md) and
[trade-pipeline audit](trade-pipeline.md) remain historical incident evidence.

- Local authority: PostgreSQL `127.0.0.1:5433/topsignal`, about 59.3 MB;
  authority `b15899c2-5a5b-4352-aa5d-162c600188eb`. Bot run 18 still running.
- At 19:10 UTC only practice `29593203` was enrolled. Its heartbeat advanced,
  93 lifecycles existed, zero were open at that snapshot, and the fills table
  was empty. Sampling/exit gaps remain historical evidence.
- Independent recorder code matches the repository's existing capacity-monitor
  fix. Its last inventory took 606.5 seconds, with a 900-second interval. Do not
  duplicate that prepared/deployed fix or attribute old holes solely from its
  current process. Raw/totals writes were advancing.
- Microstructure reached its **512 MiB derived budget**: 1,382 files / 511,760,195
  bytes plus the next 24 MiB allowance exceeds 536,870,912 by 55,107 bytes.
  The host still had about 1.329 TB free. This is not a volume-capacity failure.
  Its separately authorized task disablement remains in place; budgets and raw
  retention are unchanged.
- The routine backup task pins release 45's interpreter **and runtime environment**.
  A fresh one-shot backup using release 87's actual environment was verified on
  and off device at 19:51 UTC (details below). The scheduled configuration itself
  remains unchanged until maintenance.
- Off-device full-cache schedule is disabled. Its last full verified snapshot
  and restore remain November 4, 20,455 files / 5,561,615,793 bytes. Focused
  snapshots and the Journal archive below do not close this raw-history gap.

### Resource baseline

Sixty samples from 19:08:39–19:20:02 UTC (14:08:39–14:20:02 EST), before
activation of this chat's changes. The separately authorized disk-investigation
chat disabled the optional microstructure task at **19:16:58 UTC**, during this
window; this is therefore a mixed operational baseline, not a controlled
before/after comparison. That chat owns MusicDesk migration and raw inventory /
market archive optimizations. See [its report](peteamdpc-disk-contention.md).

| Measurement | Median | 95th percentile / range |
| --- | ---: | ---: |
| CPU | 10% | p95 37%, max 56% |
| Available RAM | 2,106.5 MiB | range 1,082–2,412 MiB |
| Committed memory | 10,395.5 MiB | range 10,135–11,559 MiB |
| API private memory | 1,188.5 MiB | range 1,185.6–1,246.2 MiB |
| D: read latency | 46.7 ms | p95 117.5 ms, max 1,122.6 ms |
| D: write latency | 33.6 ms | p95 111.5 ms |
| D: queue length | 6 | p95 14, max 17 |
| C: read latency | 0.32 ms | p95 5.02 ms |

These samples support persistent recording-HDD contention. They do not prove a
memory leak or assign latency to one process. There are no post-deployment
measurements yet.

A second 60-sample window at **20:20:52–20:32:15 UTC** followed the separately
authorized media-helper SSD move and optional-feature disablement. It overlapped
bounded candidate history benchmarks, so it is not a controlled attribution.
D: read latency median was **2.37 ms**, p95 34.14 ms (earlier 46.7/117.5 ms);
write latency median **2.38 ms**, p95 15.31 ms; queue median **0**, p95 4, max 6
(earlier 6/14/17). CPU median **12%**, p95 27%; available RAM median **2,430 MiB**,
minimum 2,161; committed memory median 10,172 MiB. API private memory remained
**1,186.3–1,186.4 MiB**. These measurements support disk relief and stable API
private memory during observation, not a memory-leak diagnosis or a candidate
deployment claim. See [the coordinated changes](peteamdpc-disk-reduction.md).
Baseline aggregates are retained in `trend-summary.json`; the later raw samples
and aggregate receipt are `trend-after-receipt.txt` / `trend-after-summary.json`.

### Cloud reconciliation

The existing read-only verifier passed at 19:20 UTC: 30 mirrored application
tables / 12,766 rows matched by complete ordered content hashes at acknowledged
sequence 172026. Both checkpoints later advanced to 172044 with zero pending
events. There were no logical tombstones or mismatched tables. This proves the
matched application snapshot, not object-storage completeness or cloud Auth
retirement. The restricted mirror role cannot read the storage schema.

## Journal historical recovery archive

The current local and cloud Journal tables matched in the reconciliation above.
At 19:25 UTC, a read-only consistent local export preserved **31 entries and nine
image references** belonging to one owner. Every referenced object was downloaded
and matched its recorded byte size; image/entry owner, account and date were
verified. Nine references contain **three distinct image contents**, totaling
1,500,711 referenced bytes. Deduplication changes only the archive packaging.

- Host: `D:/TopSignal/data/databento/retired-journal-v1/20261113T192453Z/journal.fernet`.
- Off-device: `C:/Users/drews/.ssh/topsignal-local-first-20261102/journal-archive/20261113T192453Z/journal.fernet`.
- Isolated restore: the off-device directory's `isolated-restore/`; all records
  and objects were decrypted, written and SHA-256 checked at 19:25:53 UTC.
- Encrypted archive: 720,588 bytes; SHA-256
  `266e8a19cd10f9067c5fe3a30b644047f69983c6ab0e23fd9be4caf30ac7d233`.
- Record-content SHA-256:
  `c180dc7660cdbf090738f91d4dedef2d48d84baba77def5352088c9e4deb0b0e`.
- Encryption uses the existing execution-backup key with separate host-machine
  and development-user DPAPI custody. No secret is included in this receipt.

This is historical recovery data. Source/cloud rows and objects were not deleted.
Candidate feature removal is complete; deployed cloud-storage retirement remains pending.
`backend/tools/archive_journal.py` verifies or restores the encrypted artifact;
its six offline regression checks passed.

A second reference scan found zero additional embedded Markdown/HTML image URLs
in archived entry bodies: all nine recorded references are accounted for. The
candidate removes Journal pages/components, route handlers, autosave, merge,
image service, Gemini recap and API/cache/types. Historical models, SQL tables
and checksummed migrations remain inert recovery compatibility; no rows were purged.

## Fresh focused execution/auth recovery

The 19:51:24 UTC snapshot contains **37 tables / 12,838 rows** and 24,020,452
encrypted bytes. Host directory:
`D:/TopSignal/data/databento/execution-backups-v1/20261113T195108Z-59035b18d72e`.
Off-device directory:
`C:/Users/drews/.ssh/topsignal-local-first-20261102/remediation-backups/20261113T195108Z-59035b18d72e`.
Dump SHA-256: `0c3aa3b900c29aca917424069be825ec9471e27a289b2930a59c66ade36327cd`.

An isolated restore to `topsignal_audit_remediation_20261113` matched every
row/sequence hash; execution authority was absent and one provider credential
row decrypted. Its separate restored auth SQLite copy issued a session, survived
module reload and revoked it on sign-out with cloud variables absent and network
connections blocked. The encrypted runtime environment and credential key are
included, with independent host/development-user DPAPI custody. Receipts:
`focused-backup-restore.json`, `off-device-restore.json`, and the host copy
`off-device-verification.json`. This restores application/auth recovery, not the
missing full-cache coverage. A new fresh backup remains required at activation.

The checked-in `verify_execution_recovery.py` subsequently repeated the isolated
restore at **20:34:32 UTC** in `topsignal_audit_remediation_20261113_2`; all database,
credential and cloud-blocked auth checks passed. Receipt:
`off-device-verification-02.json` in the same protected off-device bundle.

## Exact overnight raw gaps

First/last neighboring raw segments were checked against both compressed and
uncompressed SHA-256 receipts. Each preceding capture ended with a sealed
`end` event reporting `PermissionError`; each next capture starts with a new
connection. No unsealed tail or retained raw rows cover these intervals.

| New York time (EST) | Missing raw interval | Duration |
| --- | --- | ---: |
| November 12 | 19:25:02.473–19:29:05.181 | 242.708 s |
| November 12 | 19:50:04.227–19:53:58.987 | 234.760 s |
| November 12 | 22:00:12.223–22:03:59.716 | 227.493 s |
| November 13 | 04:20:07.886–04:24:07.111 | 239.225 s |

Complete unpublished `recording-health.json` temporary files were found 55–120 ms
before these exits. This strongly implicates Windows atomic health-file replacement
failing while a reader held the destination. The exact locking process is not
proved by retained logs. These are raw-capture holes, not timezone offsets or
missing heatmap derivation: nearby sealed segments are derived. Keep holes explicit.

The candidate retries transient permission failures for a total of 100 ms. A
sustained **status-only** replacement failure leaves a stale receipt and bounded
diagnostics while capture continues; a later success reports the failures. Raw
segment/manifest failures and disk-full errors remain fatal. Existing capacity-
monitor work is preserved; the coordinated scandir optimization is included.
Regression coverage includes sustained/transient locks, reconnects and critical
manifest/disk failure. Old temporary files were retained as incident evidence.

## History performance and delivery

The old served request failed at a shared 125-second Future wait (HTTP 500).
Candidate lower-history work admits one HDD reader, bounds local work at 20
seconds, shared waits at 27 seconds and SSH at 25 seconds. Both charts return
recoverable 503 / no-store / Retry-After on unavailable/busy storage; the browser
retains useful data during polling failures and retries without overlapping polls.
Upper heatmap rejects excess work immediately and has SQLite progress/decompression
deadlines. Cooperative deadlines cannot interrupt a blocked OS disk call; the slot
remains held until it returns. No false cancellation or unlimited replacement work.

Host candidate benchmark, below-normal priority, with the recorder continuing:

| Scope | Observations / result | Measured duration |
| --- | --- | --- |
| Initial manifest index | 13,050 receipts; bounded requests retain completed work | Several minutes of incremental cold preparation |
| Recent 15 minutes | 3,290 points, zero skipped segments | 5.969 s first completed read; **0.672 / 0.656 s warm** |
| Full session from Nov 12 23:00 UTC | 256,356 measurements → 44,896 actual extrema/boundary points; zero skipped segments | Cold retries at 20 s; first completion 21.281 s including result hashing; **13.172 / 13.719 s warm** |

Repeated recent and full-session point hashes matched. The full-session hash is
`35f34a119968f7369d7c89a99cbdca0bd4827f70908b12fabaab409bf7e24826`.
Profiling found repeated quality classification dominating warm overview work;
computing quality once per observation preserves the existing gap/extrema results.
Windows enumeration's missing file IDs are handled while retaining complete IDs
from selected-receipt checks. Raw-source identity and both checksums remain checked
on parsed-cache misses. The diagnostic cache is 44,187,648 bytes, below its
64 MiB cap, and is disposable. This is **not a served API/deployment benchmark**.
Prewarm the production cache before acceptance; browser delivery must be measured
again against the deployed candidate.

The upper heatmap candidate independently returned a fixed 30-minute tier-1
window in **1.188 s cold / 0.156 and 0.125 s warm**, and the full-session tier-2
view in **2.563 s cold / 1.187 and 1.140 s warm**. Frame/trade hashes matched
within each scope. Five coverage intervals and 356 quality-gap intervals remain
visible; fast delivery is not proof of complete raw/provider data.

At **20:34:26 UTC**, SQLite online backup prepared the lower-history cache at
`D:/TopSignal/data/databento/depth-history-cache-v1/history.sqlite3`: integrity
check passed, 234 runs / 13,067 markers / 1,268 parsed segments, 44,187,648 bytes.
No prior production cache existed and no raw file was changed. The currently
served old reader does not use this cache; candidate requests still revalidate
selected source identities and discover new segments.

At 20:22 UTC recorder PID 4752 remained recording with durable index 18,134,008,
one error and one reconnect (the known 19:31 event); this does not prove that all
provider observations were delivered. No diagnostic trades were placed.

## Supabase component checklist

| Component | Candidate / recovery evidence | Deployed status |
| --- | --- | --- |
| Execution authority | Local PostgreSQL, same active UUID; no cloud fallback | Local, unchanged |
| Auth | Cloud SDK/OAuth/JWKS/refresh removed; local restore issuance/restart/revocation verified | Release 87 still carries obsolete alternative config |
| Journal | 31 entries / nine image references archived and restored on/off device; runtime removed | Old APIs/storage adapter remain until activation |
| Independent recorder | Explicit local encrypted credential store; dedicated read-only owner RLS; real PostgreSQL tests | Still pinned to old cloud startup path until graceful helper switch |
| Mirror | Fresh 30-table content match; atomic retirement receipt disables only capture triggers; fences preserved; fault tests pass | Consumer/task and capture triggers still active |
| Execution backup | Current-environment focused restore completed; task-based release resolver prepared | Routine task still pinned to release 45 |
| Full archive backup | Incremental reuse, pacing, recorder-health deferral, overlap check before scan | Disabled; last full verified restore Nov 4 |
| Discord v5 | Active env points to local PostgreSQL | Local; verify restart after maintenance, send no test message |
| Optional feature/check helpers | Microstructure disabled; two Oct 22 one-shot checks have no next run | Keep feature task disabled; archive/disable obsolete checks in maintenance |
| Cloud research/legacy history | At 20:13 UTC three retired research tables and Databento candles each had zero rows; 41 cloud write fences enabled | Preserve definitions/fences and earlier recovery exports |
| Roles/keys/recovery | Dedicated recorder provisioning and mirror retirement tools prepared | No credentials revoked; no project/Auth/user/audit/object deletion |

Only the **mirrored** application table set is covered by the 30-table hash
comparison. Retired research data has its separate November 2 verified export;
Journal objects have the independent archive above. Cloud user/system records
remain retained and are not execution/auth fallback data. Inert migration/export/
mirror recovery code and the historical pooler compatibility setting remain;
the candidate application does not load cloud credentials to authenticate users.

## Prepared implementation

- Explicit unavailable/invalid account links stop account-page rendering and
  requests; choosing an account updates the URL. Default selections populate
  previously account-less URLs. Duplicate/malformed/unsafe numeric IDs fail
  closed. Account changes abort recorder reads and enrollment requests.
- Recorder status distinguishes not enrolled, unavailable status, stale
  heartbeat, unhealthy enrollment, active recording and recording while flat.
- Durable enrollment policy uses freshly discovered, visible, owned, unarchived
  ProjectX accounts independently of `can_trade`. Manual opt-outs are retained;
  policy-managed scopes can follow discovery. Existing manual enrollment counts
  toward the bounded capacity. State changes are audited atomically in SQLite.
  This policy is **not enabled on production**. Current candidate set is
  `29593203`, `29671908`, `29671939`, `29671977`, `29672001`, `29672024`, cap eight.
  At 3.6 MB/open-position-hour, six simultaneously open positions for eight
  hours × 252 days estimate 43.5 GB/year before other stores and backups.
- Existing case-normalization, fill-leg uniqueness, receipt-time identity and
  oversized worker-input fixes are preserved. An additional fill replay check
  prevents changed receipt-clock metadata from manufacturing a correction.
- Per-account bounded diagnostics add queue loss, discarded events, snapshot
  failures/successes, reconnects, commit latency/failures, last successful write
  and active mark availability. Historical gaps are not filled.
- Liquidity totals use an optional incremental manifest index and cached parsed
  verified segments. The disposable cache is bounded at 64 MiB with a 10 GiB
  free-space floor and excluded from backup. Selected receipts/source file
  identities are revalidated; cache misses verify both source checksums.
  Changed/corrupt sources remain explicit skipped segments. One active history
  read limits competing HDD work. Cooperative local read deadlines, bounded
  shared waits and HTTP 503 / Retry-After replace the uncaught 125-second wait.
  Individual operating-system disk calls cannot be forcibly cancelled by the
  cooperative deadline; the concurrency slot stays occupied until they return.

Full frontend verification passed **1,291 tests / 149 files**. Real local
PostgreSQL verification passed **14 tests**, including owner-restricted recorder
credentials and coherent mirror retirement. The second backend run passed 3,410
tests with one Windows cache-identity failure; that issue was corrected and all
43 related cache/backup/receipt checks plus 40 quality/continuity checks passed.
All 47 deployment-script tests passed. These remain intermediate receipts;
clean-commit release validation/package and exact-commit CI are still required.

## Work still required before a release decision

1. Complete immutable validation/package and exact-commit CI, then follow the
   concrete [retirement procedure](../../supabase-retirement.md). Do not alter
   historical migrations or replace existing incident reports.
2. Keep prepared fill reconciliation separate from live data: the prior report
   recovered 295 additional legs, but none have been merged into production.
   Migration `20261113_preserve_provider_trade_legs.sql` and a fresh scoped
   reconciliation are required before accepting persisted fill recovery.
3. Finish complete local validation, clean immutable packaging and exact-commit
   CI. Only then request the exact maintenance/backup/enrollment authorization.
   Activation requires fresh backup and broker exposure/order gates. No
   diagnostic trades or automatic rearming are authorized.
4. Verify served bytes, backend/helper versions, browser-independent recording,
   restart/cloud independence, passive activity, history latency and backups
   through a meaningful observation period. Update architecture docs to that
   verified state; do not label Supabase fully retired before this evidence.
