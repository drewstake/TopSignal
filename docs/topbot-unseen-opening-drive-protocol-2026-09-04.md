# Frozen opening-drive evaluation on the reserved ProjectX pool

This protocol is written before opening the reserved price files for evaluation.
Preparation reads only the pool's collection manifest, structural QA and dated
contract metadata, plus code and earlier experiment manifests. Synthetic tests
use invented candles. No new price inspection, provider request or historical
performance run occurs during preparation.

The parent task reports that the complete A07 audit passed: all registered
neighbors, entry-delay and target-fill checks were retained, and the unchanged
center reproduces A06. Actual evaluation remains separately gated until the
parent reviews the concrete frozen preparation and explicitly authorizes it.
An authorization receipt must bind the exact preparation hash, the original
opening-drive candidate and SHA-pinned A07 audit evidence. A file cannot confer
task authorization by itself.

## Candidate and source freeze

Only the **original `opening_drive` center** is evaluated. No neighbor is chosen
from A07, and no rule is changed after opening this pool. The original fixture
is `backend/tools/fixtures/topbot_research.py`, normalized source SHA-256
`d0230d261f3e5f00f6f876756086b873987eb540ae2c6a6b1798ba2b376d80e6`.
Its A06 lineage is:

```text
backend/storage/research/experiments/20260905T005448.370797Z-fee061-opening-8482f9451110
```

Preparation verifies that every A06 app source file, the original research
runner and the original fixture still match their captured bytes. It copies
the A06 manifest verbatim and records its hash and original source-bundle hash.
The current repository revision is `9616061a950c7e414f3bae45e150de79c04e6fb7`
at initial preparation. The new manifest captures the exact current app/tool
sources, standalone adapter, protocol and synthetic tests, with hashes and Git
status/diff. The new adapter changes data ingestion and report attribution;
the original engine, evaluator and research clock hook remain unchanged.

The fixed rule uses the first six complete 09:30–10:00 Eastern five-minute bars,
one decision at 10:00, direction of displacement, and displacement/range at
least 0.65. Stop distance is half the opening range, clipped to 10–100 points
and rounded upward to a quarter point; target is 2R with the original rounding.
The preserved settings are one MNQ, $50,000 starting cash, 200 signal bars,
09:30–16:00 entry session, $250 daily realized-net loss entry budget with proposed
stop-risk reservation, three permitted daily entries and 300-second cooldown.
The rule naturally produces at most one opening setup per session.

## One complete reserved period

The source is the existing verified ProjectX/TopstepX dated-contract collection:

```text
backend/storage/research/quarantine/projectx-mnqu26-20260904/complete-newer-pool
```

Collection manifest SHA-256:
`25e354280208ae795f402dd88155b3f87c1652b4b976c5b31002fc462dff4576`.
Structural QA SHA-256:
`f71d270c9ef31d56381bd8e6c6ceba1c83b4bea4458113de33c2eaf466d413ba`.
The raw file hashes already recorded during collection are copied into the
preparation without opening or rehashing raw price files. After authorization,
each byte hash must match before parsing. Any mismatch stops evaluation.

The provider contract is `CON.F.US.MNQ.U26`, named `MNQU6`, September 2026,
with tick size 0.25 and tick value $0.50 ($2 per index point). The verified
Databento definition crosswalk is `MNQU6@2026`, instrument ID `42004800`.
This numeric ID is explicitly a **Databento crosswalk**, not an ID returned
by ProjectX. Every price bar remains labeled ProjectX. The fixed outright has
no delivery switch or back adjustment; any unexpected contract identity is an
input error. No continuous-contract resolver or Databento cache is used.

The metadata identifies 55,240 closed, distinct minute bars: the 40-minute
July 10 tail plus **all 40 complete trading sessions from July 13 through
September 4, 2026**, with 1,380 minutes per complete session. The requested
evaluation interval is fixed at **July 12 22:00 UTC through September 4 21:00 UTC
exclusive**, matching those complete CME trading dates. There are no tuning
splits, optional start dates, excluded losing days or repeated candidate searches.

Warmup uses this same ProjectX pool only. The July 10 20:20–20:59 UTC tail gives
eight complete five-minute bars. Sunday July 12 22:00 UTC through Monday July 13
14:00 UTC adds 192. Exactly **200 completed signal bars** are available at the
first Monday 10:00 ET decision. Synthetic tests verify this exact boundary.
The first 200-bar-ready execution minute is 09:59 ET, whose close permits the
10:00 decision; starting execution at 10:00 would incorrectly miss that signal.
The engine's known initial warmup deferral is therefore fixed in advance at
July 13 13:59 UTC. No opening-drive setup can occur in the deferred interval.
The requested full-period bounds, actual processed bounds, deferral and all
40 trading dates are disclosed. The July 10 tail never contributes a trade or
evaluation session. No preperiod position, cash, pending signal or risk state
is carried forward.

## Execution and attribution

`backend/tools/research_projectx_unseen.py` is a standalone offline adapter.
It uses canonical `t/o/h/l/c/v` JSON envelopes already saved during collection.
After authorization it verifies file/request/contract identity, response status,
exact row schema, finite positive OHLC, quarter-point alignment, high/low bounds,
nonnegative integral volume, closed request bounds, expiry, unique minute
timestamps, known-calendar session membership and full per-date coverage.
Errors are retained and stop the run; the adapter does not repair the pool.

Five-minute signals require all five consecutive observed minute bars in a
bucket. Partial buckets are omitted, never filled or bridged. The fixed pool
must contain every expected complete bucket. Adapter lists do not set trusted
cache flags that would bypass engine validation. Source and delivery identity
remain stable across both minute execution and signal bars.

The captured original research engine supplies actual one-minute execution,
completed five-minute decisions, stop-first ambiguity, gap behavior, fill costs,
proposed-stop daily risk and the independent calendar clock. The original clock
flattens at the earlier of 15:55 ET or a known holiday close minus five minutes,
after resting gap brackets, at an observed open. It is not dependent on receiving
a completed signal candle. No additional entry delay or target-fill variant is
substituted for the original center. An unexpected forced end-of-test exit,
delivery change, residual position or shortened session coverage invalidates
the evaluation and requires an explicit audit; none is silently accepted.

Run exactly three fresh cases on the same whole pool: **$0.61 per contract per
side** and **1, 2 and 4 adverse ticks on every entry/exit fill**. The first is
base cost, the second is the required cost check, and the third is disclosed
severe stress. They reuse the same observations and are not independent samples.
Every case starts with $50,000 cash, no position, no pending order, and fresh
daily/cooldown state. Round-trip fees must be $1.22 for each one-contract trade.

The engine's generic hardcoded Databento and production-v5 metadata must not be
reported as the source/rule for this run. The standalone wrapper replaces
`historical_source`, `market_data`, source fingerprint, roll policy and strategy
revision with actual ProjectX/fixed-contract/original-candidate provenance in
both replay and summary. It records the displaced generic defaults explicitly,
preserves base engine version and economics, and retains the actual candidate
definition separately from the engine-normalized configuration snapshot.

## Predeclared interpretation

This small pool can supply **preliminary support or falsification evidence**.
It cannot satisfy the original protocol's **at least 200 trades and six months**
of independent confirmation, regardless of its observed result. All original
historical gates remain requirements, and live/replay operational parity remains
separate unresolved work. Positive short-sample outcomes do not establish
profitability or authorize live trading.

- Any source, coverage, execution, fee or accounting integrity error makes the
  evaluation invalid. Preserve the error and artifacts; do not repair inputs,
  choose new dates or retry another candidate based on results.
- Zero trades in a required case is inconclusive, with no supporting evidence.
- Net P&L must be **strictly positive at both one and two ticks** for the label
  `preliminary_support_only`. Failure of either condition is
  `fails_predeclared_preliminary_screen`; this is not a statistical proof that
  the true expectancy is nonpositive.
- Four-tick results are always disclosed as severe stress. Their interpretation
  cannot be changed after seeing the result.
- Report trade counts, net/gross P&L, fees, expectancy, profit factor, drawdown,
  exposure, long/short, sessions, blocked opportunities, concentration and
  5/20-session block-bootstrap intervals. Forty sessions provide limited block
  information; those intervals are conditional descriptions, not confirmation
  or a probability of future profit. Do not hide loss concentration behind an
  aggregate sign.
- Once viewed, this entire pool is exposed. Do not retune on it, relabel part as
  unseen, promote a neighbor or select a new date range. Any later redesign
  requires new independent observations and preserved experiment history.

## Preparation and later authorized execution

Metadata-only preparation, from the repository root:

```powershell
backend/.venv/Scripts/python.exe backend/tools/research_projectx_unseen.py prepare
```

It creates a unique ignored `backend/storage/research/unseen-preparations/`
directory. Review its exact `manifest.json` and hash. It is an immutable
preparation, **not execution authorization**. All source/data/rules/criteria
hashes precede any new outcomes. Never overwrite a preparation, start record,
failure, result or ledger.

Only after the parent explicitly authorizes evaluation, its separate receipt
must contain `permission="single_reserved_pool_evaluation"`,
`a07_audited_passed=true`, `candidate="opening_drive"`, the exact
`prepared_manifest_sha256`, and a nonempty `a07_evidence_sha256` mapping of
reviewed audit files to their byte hashes. Those evidence and source hashes
are checked before any raw price read. The later command is:

```powershell
backend/.venv/Scripts/python.exe backend/tools/research_projectx_unseen.py run --prepared-dir $reviewedPreparation --approval $rootAuthorizationReceipt
```

`run` launches the captured adapter and captured app modules in a new process.
The live working-tree adapter cannot execute an evaluation directly. Network,
dotenv and live execution are disabled. An exclusive `evaluation-started.json`
prevents rerunning the same preparation. All three cases retain full replay,
trade, session and summary JSONs. The terminal record applies the fixed
preliminary interpretation and always marks confirmation false. Separate audit
and tracked reporting follow execution; no source/provider integration is implied.
