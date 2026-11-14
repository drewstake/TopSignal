# Repeatable trade reviews

This workflow explains trading performance and the limits of its evidence. It
does not change strategies, orders, sizing, stops, targets, limits, candidates or
holdouts. The [November 13 audit](audits/2026-11-13/trade-pipeline.md) records
deployment and historical gaps. Production restarts and large archive transfers
remain prohibited by the owner's current instruction.

Local refreshed artifacts: [HTML report](../backend/storage/databento/trade-review-v1/reports/20261113-refreshed/report.html),
[summary](../backend/storage/databento/trade-review-v1/reports/20261113-refreshed/summary.md),
[coverage](../backend/storage/databento/trade-review-v1/reports/20261113-refreshed/coverage.json).
These private ignored artifacts are available on this workstation, not in a
clean Git clone; use the maintained commands below to generate another report.

Read [storage policy](supabase-storage-policy.md), [progress recording](trade-progress-recording.md),
[worker evidence](topbot-worker-evidence.md), [exit study](topbot-exit-study.md),
[depth recording](topbot-depth-recording.md), [archive](topbot-market-archive.md)
and [release procedure](releasing.md) before changing those systems.

## Sources of truth

1. Authenticated provider legs, keyed by owner + account + provider leg ID,
   establish fills and reported gross P&L. Reviewed imports retain their source
   and fee scope. Corrections supersede display heads, with immutable local
   revisions and prior heads preserving history. Missing responses never delete
   old fills. No legacy owners are merged without a reviewed ownership mapping.
2. Submitted attempts link by exact owner/account/delivery contract/order ID.
   Journals link by owned local account row and session date. Neither replaces
   broker P&L. Unmatched trades are **not-bot**, not proven manual action.
3. One-second paths establish observed valuations. Match exact owner/account/
   contract and overlapping lifecycle; multiple candidates remain ambiguous.
   Broker fill times bound metrics. Preserve post-exit observations but exclude
   them from trade metrics. Midpoint/last-price marks are not executable fills.
4. Worker-input captures and saved strategy snapshots support the existing exit
   experiment. Raw depth/totals and instrument-wide archive candles are separate
   evidence, never silently substituted for chronological worker inputs.
5. Balances and cash flows provide context. Balance changes alone cannot establish
   trades, deposits, resets, daily-limit triggers or trading P&L.

All raw/derived evidence belongs under `TOPSIGNAL_DATABENTO_CACHE_DIR` on the
backend filesystem. Never refill legacy `databento_*` PostgreSQL tables, upload
arrays to Supabase, increase budgets or remove original archives.

## Prerequisites and variables

Use Python 3.11+ with backend requirements; see [offline setup](offline-development.md).
The report itself uses the standard library. Windows needs installed `tzdata`
for `America/New_York`. No scripts or fixtures under `tmp` are required.

Capture/collection use the retained release environment and explicitly load
`--release-root/backend/.env`. Synchronization uses normal backend configuration:
run from `backend` in the authoritative environment with `DATABASE_URL`, provider
credentials and `TOPSIGNAL_DATABENTO_CACHE_DIR` configured. Never point it at a
cloud fallback or put secrets in commands, reports or Git.

Example PowerShell variables for **local offline analysis**:

```powershell
$py = 'C:/Users/drews/Development/TopSignal/backend/.venv/Scripts/python.exe'
$cache = 'C:/Users/drews/Development/TopSignal/backend/storage/databento'
$owner = '3af8acf1-a33e-4a0c-ae16-0b84d7c518cb'
$bundle = "$cache/trade-review-v1/evidence-20261113-final"
$report = "$cache/trade-review-v1/reports/review-$(Get-Date -Format yyyyMMdd-HHmmss)"
```

Use unique capture/assembly/replay/report outputs; existing directories are
rejected. Retain previous artifacts and matching source releases.

## Synchronization and coverage policy — writes state

Deploy through the normal release procedure first. Migration
`20261113_preserve_provider_trade_legs.sql` replaces the overbroad order/time
unique key with a partial fallback index; distinct provider IDs must survive.
The migration does not modify historical row values. Production activation and
this migration remain deferred until the owner permits maintenance.

After that window and destination verification, run from `backend`:

```powershell
.venv/Scripts/python.exe tools/sync_trade_history.py policy --owner $owner --start 2025-01-01 --record-active --max-accounts 8
.venv/Scripts/python.exe tools/sync_trade_history.py status --owner $owner
.venv/Scripts/python.exe tools/sync_trade_history.py sync --owner $owner --accounts 29593203 29637287 29671908 29671939 29671977 29672001 29672024 --start 2026-11-11 --end 2026-11-12 --max-windows 20
```

The persisted policy selects freshly discovered, visible, tradable, owned
ProjectX accounts with provider history enabled. Explicitly disabled recorder
scopes stay opted out. Exceeding the account cap reports `capacity_review_required`
and stops automatic enrollment/sync. Historical accounts are not all enrolled.
`policy --disable --start 2025-01-01` disables synchronization; recorder scopes
remain separate explicit settings. See `tools/trade_progress_storage.py --help`
for destination verification. Explicit enrollment uses authenticated
`PUT /api/accounts/{id}/trade-progress/enrollment?enabled=true|false`; status is
`GET /api/accounts/{id}/trade-progress/status`. Preserve drive identity and
the 10 GB free-space floor.

The API lifespan runs sync/recording with the browser closed and bot disarmed.
Backend availability, credentials, subscriptions and storage are still required.
One market stream per owner/contract serves that owner's accounts; position and
fill streams stay account scoped. Authenticated history/progress status endpoints
show policy, covered ranges, errors, heartbeat, fresh marks and gaps. A heartbeat
alone is not continuous coverage.

Sync dates are inclusive **UTC days**. Repeat to resume; `--refresh` requests
deliberate reconciliation. The worker refreshes today every five minutes, the
prior nine days every three hours, and older covered days every thirty days. It
schedules at most two windows/account/cycle and persists exponential backoff
from 60 seconds to six hours.

[Trade/search](https://gateway.docs.projectx.com/docs/api-reference/trade/trade-search/)
documents timestamp bounds, but no pagination contract, maximum date span or
retention guarantee. The worker uses at most one UTC day/request, subdivides
responses with 1,000+ legs, and leaves indivisible dense timestamps, conflicting
responses and its 32-request/window limit **partial**. Calls are spaced at least
650 ms; other app traffic shares the [provider rate limit](https://gateway.docs.projectx.com/docs/getting-started/rate-limits/).
Rate-limit failures back off. Empty responses do not prove history never existed.

`trade-history-sync-v1/state.sqlite3` stores coverage, revisions and prior heads,
with a 256 MiB cap and 10 GB free-space floor. There is no automatic deletion.
Estimate growth before new enrollment: conservatively 3.6 MB/open-position-hour
means five accounts open eight hours on 252 days produce about 36 GB/year,
before other recorders/backups. Multiple positions and actual sample sizes vary.

## Bounded recovery before migration — no execution writes

Run on the backend host with `$py`, `$release` and `$cache` set to its actual
Python, retained release and cache. The tool writes only local collection files:

```powershell
& $py backend/tools/collect_trade_history.py --release-root $release --owner $owner --start 2025-01-01T00:00:00+00:00 --end 2026-11-13T00:00:00+00:00 --all-owned --max-requests 1000 --output "$cache/trade-review-v1/provider-history-20261113"
```

Use `--accounts ID ...` instead of `--all-owned` for a smaller scope. Fresh
discovery and owned rows gate requests; hidden/locked accounts are not assumed
readable. Successful raw responses and sanitized failures persist. Repeat to
resume; `--retry-failed` explicitly retries failures. Calls are at least 2.1
seconds apart, at most 1,000 per invocation, with a 100 MB ceiling. Dense
intervals split. Broad responses do not certify provider retention completeness.

## Capture and assemble — production reads, local artifact writes

```powershell
& $py backend/tools/capture_trade_review.py --release-root $release --owner $owner --output "$cache/trade-review-v1/evidence-UNIQUE"
```

Capture uses an owner-scoped PostgreSQL repeatable-read/read-only transaction
and independent SQLite online backups including WAL contents. It records source
hashes, counts, timestamps, exclusions and database size. Bounds are 100 MB for
SQLite, 90 seconds per online backup and a 10 GB free-space floor. Sources are
not cross-database atomic. Copy the closed bundle off-device only within the
permitted load window; copying a live SQLite file without its WAL is not backup.

After the separate provider collector finishes and closes its SQLite connection,
copy the small collection directory off-device and derive a **new** bundle:

```powershell
& $py backend/tools/assemble_trade_review.py --bundle "$cache/trade-review-v1/evidence-20261113-initial-03" --collection "$cache/trade-review-v1/provider-history-20261113" --output $bundle
```

Assembly verifies hashes and owner/account identity, retains the original export
and manifest, and applies provider heads only in the new artifact. Reconciliation
records additions, changed payloads, ranges, failures and `production_merged:
false`. This recovers analysis evidence without restoring production rows.

## Generate a report — offline and read-only against the bundle

The next report command, from the repository root, is:

```powershell
& $py backend/tools/trade_review.py --bundle $bundle --owner $owner --cutoff 2026-11-12T17:00:00-05:00 --output $report
```

The generator verifies every member's SHA-256, size and SQLite integrity and
rejects missing/unmanifested files. It imports no application, contacts no
provider and changes no synchronization, enrollment or selection. Cutoff must
include an offset and cannot exceed capture completion. This is an event-time
cutoff, not a historical knowledge-time snapshot: corrections may have arrived
at the later documented capture time.

Optional filters can be combined:

```powershell
& $py backend/tools/trade_review.py --bundle $bundle --owner $owner --cutoff 2026-11-12T17:00:00-05:00 --start-session 2026-11-12 --end-session 2026-11-12 --accounts 29593203 --cohort bot --output $report
# Other choices: --cohort not-bot ; --strategy-version SAVED_VERSION_ID
```

Sessions use the 18:00 America/New_York boundary and next session date, normal
close 17:00, with timezone-aware daylight saving. Holidays/early closes are not
independently certified. Positions filter by entry session. Pre-range fills
establish inventory; `fills.csv` includes all selected-account legs through
cutoff, explicitly broader than a position cohort. `--cohort manual` fails
because current evidence cannot prove manual action; use not-bot.

Artifacts: self-contained `report.html` with charts, `summary.md`, `trades.csv`,
`fills.csv`, `positions.json`, `summary.json`, `coverage.json`, `provenance.json`.
Keep them together for relative evidence links. Provenance records code commit,
generator hash, source manifest/hashes, capture times, cutoff, filters, exclusions
and assumptions. Private account artifacts stay out of Git and public hosting.

## Definitions and interpretation

- A **leg** is a provider record; an execution may contain several leg IDs. A
  **position** is an account/exact-contract flat-to-flat cycle, retaining partial
  closes, reversals and maximum quantity. Orphan closes/unresolved inventory are
  gaps, not fabricated current exposure. Only broker snapshots establish that.
- A **decision** is an explicit strategy decision/attempt. Similar account/timing
  patterns do not establish a copied group. No copy group is inferred here.
  Per-account risk differs from aggregate exposure; pooled trades are not
  necessarily independent bets. Keep saved strategy versions separate.
- Gross uses provider closing-leg P&L. Fees and commissions are separate, counted
  once per leg; round-turn import costs are not doubled. Missing components or
  uncertain lifecycles withhold net. Known-net sums describe a subset, not total
  profit. Environment uses capture-time provider classification, never names;
  historical uncertainty and unknown classifications remain visible.
- A same-name CSV/provider account pair is an **unreviewed source overlap**,
  not an instruction to merge. Keep both scopes and withhold combined profit
  until an explicit ownership/source reconciliation establishes a mapping.
- Payouts/expenses use matching calendar dates and stay separate from trading
  P&L. Account-filtered reports exclude unallocated owner payouts. Deposits,
  resets and adjustments are not inferred from balances.
- Paths show observed extrema, first-mark delay and flags. Missing/initialized
  legacy excursions are unavailable, not zero. Sparse/split paths do not prove
  full MAE/MFE. Separate actual fills, observed valuations, candle estimates and
  counterfactual returns.
- Holding-time, direction, time-of-day and post-loss differences are retrospective
  associations. Session clustering, small samples, multiple comparisons, repeated
  peeking and selective coverage limit conclusions. A 40%-of-daily-loss-limit stop
  remains an unvalidated hypothesis, not an optimized/guaranteed cap. These data
  cannot establish that stops were or were not deliberately targeted.
- With gross win W, gross loss L and fixed round-turn cost C, break-even win
  probability is `(L+C)/(W+L)`: W=L=$50 and C=$1.72 gives 51.72%. Variable costs,
  slippage and dependence limit this assumption; it is not a validation gate.

## Exit study and copied replay

Reports filter `phase='exploratory'` in SQL **before** deserializing outcomes.
They never read holdout payloads, register candidates or promote strategies.
Show inclusion by actual winner/loser/flat/unknown, session, account, experiment
and version. Preserve all-ten-alternatives pairing, latency, costs, gaps and
horizons. Shared-stop losers can resolve quickly while winners require longer
tails, creating selection bias. Completed replay can still mean missing data;
pending jobs are not equivalent to every excluded outcome.

When the load restriction permits, copy required worker captures using online
backups for live files and verify checksums/metadata. A captures manifest has
`owner` and `files`, keyed by capture ID with relative `file` and `sha256`.
On verified off-device copies, use the matching retained experiment release:

```powershell
& $py backend/tools/replay_trade_review.py --bundle $bundle --captures-manifest "$cache/trade-review-v1/copied-captures/manifest.json" --release-root $matchingRelease --entries ENTRY_ID --output "$cache/trade-review-v1/replay-UNIQUE"
```

At most 100 explicit already-enrolled exploratory entries are accepted; model
hashes must match. Replay networking is disabled. New results/receipt append to
a unique directory; originals/production revisions are unchanged. Never invent
missing tails or consume untouched holdouts. Recorder changes can create a new
code-bound strategy version even with unchanged trading rules; retain boundaries.

## Backups, troubleshooting and completion language

In a permitted window, use the existing
`run_market_archive_backup.py --config PRIVATE_CONFIG --restore-check` system.
Preserve its 250 GB budgets/5 GB reserve, verify sealed segments, retain original
archives and receipt hashes. A feature snapshot is not full-cache protection;
a zero-sample readiness snapshot does not protect trading paths.

| Symptom | Inspect / correct |
|---|---|
| Missing fills | Owner/source, discovery, range state, retry time, receipt and pending migration; never infer fills from balance |
| New account uncovered | Persisted policy, cap, opt-out, destination verification and API health |
| Empty live fill table | Case-insensitive SignalR dispatch, account subscription, timestamp/identity flags; REST is a separate source |
| Stale/split paths | Exact contract, connections, clock skew, snapshots, restarts, queue/storage flags; never nearest-match |
| Zero legacy excursions | Whether any marks were observed; old initialized rows remain untrusted |
| Inactive archive account | Actual entitlement and advancing market cursor; market scope is not position enrollment |
| Study exclusion | Variant reasons, observation obligations, capture and replay-job state, matching code; never relax pairing |
| Worker capture drops | Fixed loss reasons, queue bounds, disk latency and invalid inputs; model still rejects books over its existing limit |
| Missed backups | Schedule, estimate timeout, overlap guard, budgets, full versus focused receipts; preserve load restriction |

Required report sections: actual closed results, environment/attribution splits,
cash flows, account/range history states, recorder/path coverage, study inclusion,
reconciliation/provenance and limits. Distinguish **not examined**, **not
synchronized**, **not enrolled**, **partially recorded**, **temporarily
inaccessible**, and observed empty responses. None proves history never existed.

**Complete within requested scope** requires explicit finite dates/accounts,
covered response intervals through cutoff, reconciled inventory, known costs
and verified coverage for requested path metrics. It does not certify unlimited
provider retention. **Provisional** means the session is unfinished or evidence
is arriving; state last evidence time and separate closed trades from unresolved
inventory. **Materially incomplete** means material fill/cost/attribution/path
gaps remain; it may coexist with provisional session state and trustworthy
individual fills. The current generator conservatively labels observed paths
partial; a heartbeat or sparse marks never certify full-path coverage.

## Reusable agent prompt

> Read docs/trade-review-workflow.md and the latest deployment/backup audit.
> Generate a trade review for OWNER, ACCOUNTS, SESSION RANGE and explicit CUTOFF
> with timezone. Respect maintenance restrictions. Establish provider coverage
> through explicit bounded collection/sync, then capture and verify a new frozen
> off-device bundle. Reporting remains offline. Use maintained tools, unique
> outputs and matching experiment code. Keep broker fills authoritative, missing
> costs/paths unknown, not-bot distinct from manual, and copied accounts distinct
> from independent decisions. Separate actual, observed, estimated and
> counterfactual evidence. Do not alter trading, consume holdouts, register
> candidates, increase budgets or delete originals. Reconcile against the prior
> report and explain changed totals/gaps. Return artifact links, recorder/backup
> scope, completeness state and exact remaining causes.
