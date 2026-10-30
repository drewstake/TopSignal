# Microstructure workspace verification — October 30, 2026

The six requested research capabilities are implemented in the development app:
causal measurements, episode studies, aligned trade flow and bookmarks,
observed-depth replay, execution/cost simulation, and an immutable experiment
notebook with chronological validation and frozen forward observation.

## Open and use

- [Observe & replay](http://localhost:5173/microstructure?account=28278873)
- [Final verification scorecard](http://localhost:5173/event-study?account=28278873&job=851d0f9620044b38b9f17da3cec9347c)
- [Experiments & bookmarks](http://localhost:5173/research-notebook?account=28278873)

These example links use the verification account. The implementation takes the
selected owned account from authenticated context; it does not hardcode it.
Load a completed short interval, inspect activity before an observation, bookmark
it, then run explicit conditions/costs. Inspect failures and exclusions before
saving a hypothesis. Fork variations and reserve later sessions for validation.
The [operating guide](../../microstructure-research.md) specifies formulas,
execution rules, limits and recovery.

## Enabled and preserved

The local backend and frontend expose the three pages and account-scoped API.
On peteamdpc, additive read-only tools are installed under
`C:/ProgramData/TopSignal/tools/depth-research-v1/`, with the existing private SSH
identity, strict host-key verification and pinned Python interpreter. The ignored
development reader configuration points to this helper. Its original configuration
was preserved under the local cache's audit directory.

The production dashboard release remains `20261030-history-23`. No main-service
or independent-recorder restart was performed. Final host inspection at about
18:31 UTC found both scheduled services Running, the recorder reporting recording,
one connection, zero reconnects/errors and zero broker mutation calls. The recorder
remains on its existing v1 summary code. Enhanced features are reconstructed on
demand and can be built into immutable compact files separately.

No database migration, historical cloud import, billing change, budget increase,
strategy change, order submission or deletion was performed. Source archives and
earlier development artifacts remain intact.

## Actual history and bounded backfill

Authenticated inventory showed **one UTC day / one futures session**. Raw history
begins **2026-10-30 12:26:58.492796 UTC (08:26:58 Eastern)**. The last refreshed
inventory reached **18:31:04 UTC (14:31:04 Eastern)**, with 449 committed raw
segments. This is an availability envelope, not a claim of gap-free coverage.
Existing compact summaries begin around **17:04:50 UTC (13:04:50 Eastern)**.
Active/unsealed segments are excluded until committed.

The recorder reports `paper_only=true` and `data_live=false`. These are actual
received ProjectX MNQ records, not synthetic fixtures; they are not certified
live exchange-depth evidence. Snapshot completion, sequence scope/guarantees and
aggressor-side trade semantics remain unverified. Directional flow is disabled.

The 17:30–17:33 UTC replay reconstructed 680 points and 298 heatmap frames from
158,636 incoming events, reading 89,712,083 uncompressed bytes including pre-roll.
No corrupt segments or sampled quality flags were found in this window. Raw
intra-sample validity still excluded some subsequent outcomes/execution paths.
Observed sequence jumps/reversals are diagnostics, not proof of packet loss.

Historical backfill was deliberately limited to **12:30–12:35 UTC (08:30–08:35
Eastern)**: 946 points from 83,221 incoming events, no skipped segments. The final
all-scope compressed feature file is 971,854 bytes; file plus manifest adds
974,599 bytes. Repeating the identical host command returned `created=false`
and **zero incremental bytes**. Originals were not rewritten. Other intervals
remain available for bounded reconstruction; no full-day/month backfill was run.

Final backfill data ID:
`bab1d34347d38911d45c71f8044dfb61fe66260aa02f7ad2a7fc5f32077c9216`.
Measurement version `microstructure-v2.1`, measurement-code SHA-256
`8830195abcf2c047edac5ec2ac521fb808860ddc48510678856d2832f7ebd7e7`.

## Recorded verification study — a negative result

The final study used October 30, 13:30–13:33 Eastern, nearest ten observed levels,
imbalance ≤0 for one second, 30-second episode cooldown and 5/15/60-second horizons.
This threshold was a functional verification choice, not a profitability search.
Simulation: short one MNQ, market BBO, eight-tick equal stop/target, 30-second hold,
250ms entry/exit latency, one tick adverse slippage per side, $1.24 illustrative
round-trip fees. Harsher settings add one tick per side, 500ms each way and 50% fees.

| Evidence | Result |
| --- | --- |
| Matching samples / independent episodes | 334 / 6 |
| Sessions / days | 1 / 1 |
| Valid / missing outcomes, 5s and 15s | 4 / 2 at each horizon |
| Valid / missing outcomes, 60s | 2 / 4 |
| Baseline filled simulations / excluded | 5 / 1 missing exit |
| Baseline gross / net expectancy | −$1.50 / **−$2.74 per trade** |
| Baseline net win rate / profit factor | 40% / 0.2872 |
| Baseline net total / maximum drawdown | −$13.70 / $13.70 |
| Harsher filled simulations / net expectancy | 4 / **−$4.735 per trade** |
| Harsher exclusions | 1 invalid intra-sample execution path, 1 missing exit |
| Ambiguous filled cases | 0 in this particular window |
| Session-cluster uncertainty | Unavailable: one session, minimum three |

The workspace reports **insufficient data for repeatability**. This tiny inspected
sample cannot establish or rule out a general edge. Missing controls, invalid
intra-sample paths and incomplete horizons remain visible. Failed episodes are
listed alongside favorable ones. No tick-wise confidence interval was substituted.

Final result job: `851d0f9620044b38b9f17da3cec9347c`.
Engine: `microstructure-study-v1.2` (interpolated return percentiles).
Code SHA-256: `f27e7aa5f0d2d8581c246302eeebdb48a8877f4b939272b12e1e5440af8943a7`.
Compressed result SHA-256: `0511e1dca33ee2158b61f29f93100cd433c63376f8df40dbc056c8069c1d7cf6`.
Final frozen experiment: `0c02e3d5b64f4d07bfacab41beb1499c`; version digest
`a77a5506488be4706f7310e6516065888ef111f108ca6c5e0bc9d8ddbdcd9d8d`.
Forward windows must begin after **18:32:17.517223 UTC**. Earlier versions/runs
remain in the registry and do not count as independent confirmation.

## Tests and actual browser checks

- Backend: **136 passed**, covering microstructure, depth totals/history/recording,
  book semantics and archive durability. New microstructure tests use explicitly
  labeled synthetic fixtures for causal timing, invalid paths, execution ambiguity,
  ownership, bounds/corruption, cancellation, immutability and backup restoration.
- Frontend: **46 passed across nine files**, including stale responses, account
  changes, cancellation, causal replay selection, bounded forms and existing history.
- Production frontend build: TypeScript and Vite passed.
- `git diff --check`: passed (only repository LF/CRLF normalization notices).
- Authenticated Chrome against localhost:5173 loaded real host data, played/paused
  replay, stepped and zoomed, inspected preceding depth and five-second activity,
  saved/reopened a bookmark, ran the study, reviewed costs/exclusions, froze and
  reopened an experiment, and downloaded CSV. Episode 1 was explicitly inspected
  despite its unfavorable five-second outcome. The episode link loaded two minutes
  of preceding context. Bookmark reopening preserves its original data version.
- Final CSV: 24 rows (18 episode/horizon outcomes and six simulation attempts),
  including missing outcomes and the excluded attempt, with job/code/data provenance.
  Download size 5,856 bytes, SHA-256
  `e34d00a9867aa71c50d52791ded417e4f556fa42954abb2b5e0a536c874e3b97`.

## Measured storage and backups

All working market/research artifacts are below the configured filesystem cache.
At inspection, host raw recordings occupied 363,043,404 bytes; the new derived
tree occupied **2,897,866 bytes across seven files**, including two retained earlier
feature versions. Local research jobs/results, two experiments and a bookmark
occupied **647,667 bytes across 13 files**.

The final five-minute feature file suggests roughly 11.7 MB/hour or 281 MB per
continuous 24 hours. This is a single observed window, not a guaranteed rate.
The 512 MiB derived ceiling would allow roughly 1.9 continuous days at that rate;
it is intentionally bounded and requires review, not automatic expansion.
Derived data must also fit inside the original combined 20 GiB raw+derived budget,
10 GiB free-space reserve and 30-day review. The development notebook has its own
512 MiB / 5,000-file cap, 5 GiB reserve and 30-day review. Jobs remain bounded to
two per process, one per user/account; raw windows ≤10 minutes / 256 MiB /
600,000 events, and browser results ≤3,000 points / 1,200 frames per window.

The existing **TopSignal Off-device Archive Backup** ran successfully. Its
18:25:46 UTC receipt verified **1,094 files / 424,145,228 bytes**, including all
seven derived files (2,897,866 bytes), on the development PC. Its existing daily
18:15 Eastern schedule, capacity and restore-check behavior were not changed.

The development notebook also has a verified separate-device snapshot on peteamdpc:
`D:/TopSignal/archive-backup-staging/development-research-20261030T1834Z`.
All 13 notebook files / 647,667 bytes verified after extraction. The 686,080-byte
transport tar has SHA-256
`42f107b6cf958487d77797473834b3c2ecf98907fe0ce07d3336b909bd9b3c68`.
Capacity was checked first: staging about 2.30 GB against its existing 250 GB
ceiling; host free space about 1.23 TB. This is a one-time notebook backup;
future local notebook edits need subsequent snapshots. Host backup pulls do not
automatically include development-only files.

Local receipts, downloaded CSVs, verified notebook snapshot and screenshots are
under `backend/storage/databento/audits/microstructure-20261030/` (ignored by Git).
Restore by verifying the snapshot manifest, preserving the owner/account hierarchy,
and copying into a separate research cache root. Never overwrite current evidence
automatically. Original recording archives remain necessary for new reconstructions.

## Concrete operational limitation

**Resolved later on October 30:** automatic collection is now enabled and verified.
See the [activation receipt](microstructure-automatic-collection.md). The paragraph
below preserves the original delivery's status.

Installing the optional five-minute **TopSignal Microstructure Features** task
under the existing limited service identity failed with
`Register-ScheduledTask: Access is denied (0x80070005)`. No task was created and
permissions were not weakened. The reviewed installer is available at
`scripts/install-microstructure-task.ps1` and on the host. An administrator can
register it using the existing explicit tool/cache/Python/owner arguments.
On-demand research, manual idempotent backfill and the original independent raw
recorder work without that optional task. Future evidence continues accumulating
in raw recordings; continuous precomputation is not enabled.

Snapshot completeness, aggressor direction, exchange queue fills and within-sample
barrier ordering remain feed/model limitations, not silently assumed facts.
The workspace remains research-only and is enabled locally; the production
dashboard was not replaced.
