# Microstructure research workspace

The local workspace has three connected pages. Select an owned ProjectX account;
the account in the URL is an access context, never a hardcoded recording owner.

- `/microstructure`: observed-depth heatmap, synchronized price/depth/flow,
  receipt-time playback, stepping, zoom and annotated bookmarks.
- `/event-study`: causal conditions, episode scorecards, earlier matched controls,
  cost-aware setup simulation and chronological validation.
- `/research-notebook`: all runs, immutable experiment versions, frozen rules and
  bookmarks that reopen the original recorded window.

These tools do not call order APIs, change strategy configuration, train/promote
TopBot models, or write broker/paper results. They are exploratory research.

## Workflow

1. Load a completed five-minute interval in Observe & replay. Raw reconstruction
   accepts at most ten minutes, 256 MiB of raw segments and 600,000 received events.
   Busy periods may need shorter intervals. The existing compact history viewer
   still accepts an hour. Only committed files are eligible.
2. Inspect a moment, play/step through its preceding observations and compare
   trailing activity. Blank heatmap columns remain unavailable. Showing future
   context is an explicit retrospective viewing option, off by default.
3. Bookmark the observation with a falsifiable hypothesis. The server attaches
   the actual timestamp, contract, source capture/index, generation, scope,
   feature/code/data versions and settings. A client cannot invent source paths.
4. Define one to four conditions in Event study. All/any combinations, sample
   persistence, cooldown and 1–300 second horizons are explicit. Add up to twelve
   nonoverlapping windows, including different sessions. With no added list,
   the current window is used. Every submitted analysis is retained, including
   zero-episode searches, failures, cancellations and execution exclusions.
5. Inspect distributions, missing outcomes, controls and breakdowns before
   looking at the setup tester. An episode link loads two minutes before and
   ninety seconds after its trigger. This retrospective inspection does not
   retrospectively change the prospective trigger or choose successful examples.
6. Record how thresholds were chosen, costs and chronological session boundaries.
   Save an immutable experiment, optionally freeze its rules, and later choose
   **Observe forward**. New windows must begin after the freeze and previously
   examined window ends. Changed code or feature implementations require a fork
   and revalidation. Export CSV for tabular results and JSON for full provenance.

Fees are user assumptions: the $1.24 round-trip input is an illustrative starting
value, not a fetched brokerage fee schedule. MNQ outright tick metadata is fixed
to 0.25 index point / $0.50 per contract; other instruments are deliberately
rejected until their metadata and recording semantics are implemented.
[CME contract specifications](https://www.cmegroup.com/markets/equities/nasdaq/micro-e-mini-nasdaq-100.contractSpecs.html).

## Measurement contract

`mnq-book-totals-v2` contains `microstructure-v2.1` features. Old v1 summary files
remain readable and are never rewritten. New measurements can be derived from
raw recordings without restarting the recorder. `BookTotals` also supports v2
collection in a future recorder deployment. The currently running host recorder
was left on its existing v1 collection code.

`depth_book.py` is a standalone copy of the display book kernel; an AST parity
test prevents semantic drift. Explicit Ask/Bid quantities replace the aggregate
`volume` at a price; zero deletes that observed level. `currentVolume` is not
added to resting quantity. A best-quote update removes impossible better prices;
an old quote-only price is not retained as explicit depth. Duplicate/stale
per-level updates are rejected according to the existing kernel. Lifecycle,
delivery, reset and observed epoch boundaries clear state. This remains an
**observed reconstruction**, not certified complete exchange depth.

| Measurement | Formula / units / validity |
| --- | --- |
| Resting depth | Sum of accepted displayed `volume`, in contracts, at all observed prices or nearest 1/5/10/20/50/100 levels per side. Actual level counts accompany each scope. |
| Fixed tick scope | Prices whose distance from that side's current best is **≤ N ticks**, including the best. Defaults 1/5/10/20; up to eight integer distances 1–100. Holes are not invented. |
| Imbalance | `(B-A)/(B+A)` at each scope; null when denominator is zero. |
| BBO / mid / spread | Observed best prices, their displayed quantities; mid `(bid+ask)/2`, spread `ask-bid`, in index points. Missing sides are null. |
| Depletion / refill | Sum of negative / positive accepted explicit price-level size changes over the previous 1/5/15 seconds, divided by window seconds. All observed explicit prices, independent of chosen depth scope. Quote pruning is excluded. These are observed reductions, not proven cancellations. |
| Activity | Count of received `GatewayTrade` prints and sum of valid positive trade `volume` over `(t-W,t]`, for W=1/5/15 seconds. Rates divide by W. Invalid price/volume/provider time makes volume unavailable for that window; the invalid count is retained. Repeated prints cannot be exchange-deduplicated without a reliable trade identity. |
| Baseline ratio / z | Current depth divided by the arithmetic mean of **prior** valid sampled depths in the trailing 60 seconds; z uses population standard deviation. Requires at least 40 prior observations spanning ≥30 seconds. Null for zero denominator/deviation or invalid current book. No centered window or future session normalization. |
| Persistence | Elapsed monotonic seconds over contiguous **sampled** observations with imbalance ≤−0.3 / ≥+0.3, or depth ratio <0.5. Sample gaps >2 seconds or invalid observations reset persistence. Brief between-sample reversals can be missed. Combined-condition persistence is separately specified in the study. |
| Trailing volatility | `sqrt(sum((mid[i]-mid[i-1])²)/(last_time-first_time))` over the same prior valid 60-second sample history, in points/√second. Same baseline warmup. |
| Session context | America/New_York, session date rolls at 18:00 ET; RTH label 09:30–16:00 ET, otherwise overnight. These are grouping labels, not a verified exchange holiday/open-hours calendar. |
| Execution interval | Bid/ask low/high across incoming observed book states after the previous summary and through the current one, with the exact preceding raw index and a path-validity bit. Any missing/stale/crossed state invalidates that interval for execution. |

All raw events update activity before at-most-250ms summary sampling. No rows are
invented during silence. Monotonic receipt time drives rolling windows; UTC receipt
and provider timestamps remain separate. An event's provider time does not imply
that every untouched book level is fresh. Side receipt ages above two seconds,
missing/future/old provider times, locked/crossed books and missing sides remain
explicit. Raw extrema preserve brief price changes the final sampled state misses.

The [ProjectX documentation](https://gateway.docs.projectx.com/docs/realtime/)
labels trade types 0/1 Buy/Sell and depth `volume` as the aggregate at a level.
Real recordings contain both trade labels, but aggressor-side interpretation,
sequence scope, epoch guarantees and snapshot completion remain unverified.
Directional flow fields are therefore null and unavailable as signal inputs.
Sequence diagnostics count observed jumps/reversals without reordering events or
asserting packet loss. No queue position, orders, identity, hidden liquidity or
spoofing inference is made.

## Event-study and inference rules

Conditions use only the trigger's measured features. One trigger is accepted per
continuous qualifying episode after its persistence time; cooldown is measured
from the last accepted trigger. Invalid samples and stream boundaries break
persistence. Occurrence counts are descriptive sample counts, not sample size.

Controls are earlier non-trigger anchors, spaced by at least the larger of the
maximum horizon and cooldown. They match contract, session, partition, ET
half-hour, fixed spread bucket (≤1 tick / ≤2 / wider) and trailing volatility
bucket (<0.5 / 0.5–2 / ≥2 points/√s, or unavailable). They are used without
replacement; their longest outcome plus cooldown must end before the trigger.
Neither control eligibility nor matching scores inspect future returns. Missing
controls/outcomes remain in exclusion counts; controls are not silently replaced
with periods having favorable or complete outcomes.

Returns and excursions are direction-adjusted mid-price **points**, not executable
P&L. A horizon uses the first observation at or after the target time, with ≤1s
tolerance. Invalid sampled or intra-sample books, >2s gaps, delivery/capture/
generation/reconstruction changes and session/split boundaries invalidate it.
Return percentiles linearly interpolate the sorted observations (engine v1.2).
“Nothing meaningful” means the maximum absolute observed excursion is below the
configured tick threshold, not merely a small ending return.

Session splits are chronological: training precedes validation, which precedes
test. No fitted model or threshold optimizer is included. Manual thresholds and
their selection rationale are persisted. Fixed-rule walk-forward rows evaluate
each session with the same settings. Independent window reconstructions are not
stitched into fabricated continuity. All runs are visible, and declaring a test
date does not erase earlier inspection of it.

Intervals use a deterministic session-cluster bootstrap (1,000 resamples, seed
1729, percentile 95%). Whole session observations move together. Fewer than three
represented sessions returns **insufficient sessions**, not a tick-wise interval.
Three sessions is a computational minimum, not compelling statistical evidence.
Paired signal/control difference intervals and net expectancy intervals do not
correct for repeated testing or selection; no automatic claim of an edge is made.

## Execution model

Entry uses the first fresh valid observed executable BBO after signal time plus
entry delay and latency, within one second. Even zero latency uses a subsequent
receipt. Long entry buys the ask and exits at the bid; short reverses these.
Position quantity must fit the displayed top quantity; no depth sweep or queue
fill is guessed. A touched limit order is **unresolved**, never automatically
filled. Fees are round-trip per contract, multiplied by size. Adverse slippage
is applied per side; spread is already represented by bid/ask prices.

Stops/targets are measured from the modeled entry. Raw observed BBO extrema
between samples detect crossings. A single crossed barrier is distinguishable;
both in the same interval are ambiguous. The chosen stop-first policy uses the
worse of recorded delayed exit and stop plus exit slippage; exclusion is also
available. Without raw extrema, non-stop exits remain ambiguous. Invalid interval
paths are unresolved. Detection occurs when the summary is received, then exit
latency applies: targets trigger a **market exit**, not an assumed resting limit.
Gap slippage can exceed the intended stop. This is not an exchange fill simulator.

Maximum hold, entry delay, both latencies, fees, slippage, position size, equal
reward/risk and overlapping-position policy are explicit. The harsher scenario
adds one tick of slippage per side, 500ms each way and 50% fees. In skip-overlap
mode an unresolved open position blocks new entries through its maximum hold.
Independent overlap is a hypothetical comparison, not a portfolio schedule.

Gross P&L includes executable spread and slippage; net subtracts fees. Net win
rate, average realized win/loss, profit factor, path drawdown, loss streak and
position-seconds exposure accompany expectancy in dollars and R. Break-even cost
is gross mean P&L **after modeled execution costs** and before fees; negative means
even zero fees do not break even. Sensitivity adds dollars per whole position.

## Filesystem, jobs and security

Everything is under `TOPSIGNAL_DATABENTO_CACHE_DIR`:

- `depth-recordings-v1/`: original immutable segments; preserved unchanged.
- `microstructure-derived-v2/<owner hash>/`: reproducible compressed multi-scope
  features plus SHA-256 manifests. No full raw duplication.
- `microstructure-research-v1/<owner hash>/<account>/`: jobs, bounded compressed
  replay results, bookmarks and immutable experiment versions.

No migration or cloud historical write was added; the SQLite-only Databento
import guard is unchanged. Authentication and owned-account verification run on
coverage, every job/artifact/bookmark/experiment and export. Transactions are
rolled back before filesystem or SSH work. Identity and paths are server-owned;
strict Pydantic bodies reject owner IDs, source paths, hosts and commands.

Two jobs per process, one active per user/account; twelve ≤10-minute windows per
study, ≤2,000 episodes, 3,000 chart points, 1,200 replay frames and 24 MiB result
limit. The reader has 50,000-manifest, 600,000-event and 110-second CPU/wall-work
bounds; SSH has a 120-second deadline and response-size cap. Cancellation stops
local processing/SSH and prevents result publication. A disconnected remote
read-only helper may finish within its own bound. Restarted jobs are marked
interrupted; completed results survive and verify against their SHA-256.

Local research writes stop at 512 MiB, 5 GiB free-space reserve, 5,000 files or
30-day review. Derived backfill has its own conservative 512 MiB ceiling, remains
within the existing **combined 20 GiB raw+derived recording budget**, retains the
10 GiB free-space reserve and original 30-day review boundary. No automatic
deletion, budget increase, billing change or historical cloud import occurs.
The existing recorder's budget remains unchanged. Derived writes stop before
they could consume its original allowance; the recorder does not grow this cache.

The development source file remains ignored:
`backend/storage/depth-history-source.json`. Additional server-only fields are
`research_reader_path` and `research_recording_root`. Existing SSH identity,
strict host-key checking, batch mode and disabled forwarding are reused. Only
owner hash and bounded research parameters travel to the helper; no OAuth token,
database password or brokerage credential is forwarded.

## Backfill, collection and recovery

The installed read-only helper is
`C:/ProgramData/TopSignal/tools/depth-research-v1/research_reader.py` on peteamdpc.
It uses the existing pinned Python interpreter, without importing the trading app.
Backfill accepts a server/operator-owned cache root and owner hash, not HTTP paths:

```powershell
python backend/tools/backfill_depth_features.py --cache-root <configured-cache> `
  --owner-hash <authenticated-owner-sha256> --start 2026-10-30T12:30:00Z --end 2026-10-30T12:35:00Z
```

`--latest` derives the latest fully sealed UTC five-minute interval. A successful
repeat verifies the immutable file and writes zero new feature observations.
Different code/source/settings produce a separate version; originals remain.
Exact compatible windows can reuse the derived cache. A corrupt or incompatible
cache falls back to verified raw reconstruction; it is never silently trusted.

`scripts/install-microstructure-task.ps1` installs the five-minute collector under
the existing limited service identity, using S4U (local filesystem only, no task
password stored). **Automatic collection is enabled on peteamdpc.** It also runs
one minute after startup, at below-normal priority, with a three-minute deadline
and overlapping launches ignored. Each invocation derives the latest sealed UTC
five-minute interval; it does not automatically catch up an arbitrary backlog.

The original access-denied error came from cross-account S4U registration without
credentials. Windows requires the target user's password at registration even
though S4U does not store it for execution. The fixed installer accepts a
`PSCredential`, authorizes registration through the explicit S4U COM API, and
grants the service account read/execute access only to the helper bundle. The
host's existing protected deployment credential was reused in memory, without
resetting an account, storing another password, or changing the bot/recorder tasks.
See [Microsoft's registration requirements](https://learn.microsoft.com/en-us/windows/win32/taskschd/security-contexts-for-running-tasks)
and the [activation receipt](audits/2026-10-30/microstructure-automatic-collection.md).

Inspect `Get-ScheduledTaskInfo -TaskName 'TopSignal Microstructure Features'` and
`microstructure-derived-v2/collector-status.json` for exit status, last successful
interval, observations and any storage refusal. Disable only this task with
`Disable-ScheduledTask -TaskName 'TopSignal Microstructure Features'`; raw recording,
trading and on-demand research continue. Re-enable with `Enable-ScheduledTask`.
No reboot or trading-service interruption is needed for installation or rollback.

The existing cache backup includes these directories because they are outside
the raw tree's committed-segment filter. `backup_market_cache.py` validates file
hashes and preserves experiment versions and results on restore. Back up while
research writes are idle; concurrent atomic updates can cause a safe retry rather
than an inconsistent successful receipt. Preserve source archives and commit
manifests. Git and Supabase do not contain these ignored files. The host's
scheduled off-device backup covers its derived cache; the development notebook
needs its own off-device copy, since the host pull does not include local files.

Recovery: verify the backup manifest before restoring into a new cache directory,
preserve the owner/account hierarchy, and point `TOPSIGNAL_DATABENTO_CACHE_DIR`
there. Completed results open without re-running or changing their code versions.
Reproducible new analysis still needs the original recording sources or matching
verified derived version. Never replace current user/audit records with an older
copy automatically. Keep the old source configuration receipt to roll back host
reader selection; no main-service rollback or restart is required.

See the [October 30 verification receipt](audits/2026-10-30/microstructure-workspace.md)
for actual coverage, storage measurements, tests and browser checks.
