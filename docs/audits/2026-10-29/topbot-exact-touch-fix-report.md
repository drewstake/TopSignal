# TopBot exact-touch repair and repeated v2 validation

October 29, 2026. **The rounding defect is fixed locally, and numerical validation
passes. Nothing was deployed, installed, promoted or restarted.** Existing
artifacts, prior reports and audit evidence were preserved. Entry thresholds,
uncertainty deductions, stop/target sizing, costs and risk limits are unchanged.

This report resolves the exact-touch finding in the earlier
[v2 validation report](C:/Users/drews/Development/TopSignal/docs/audits/2026-10-29/topbot-paper-v2-validation-report.md).
That report and its failing reproduction remain historical evidence.

## Correction

`price_paths` now allows only floating-point roundoff in v2 stop/target touch
comparisons, including opening gaps and target trade-through comparisons. The
allowance is eight double-precision epsilon units times the relevant entry and
bracket magnitude, with a quarter-point magnitude floor. It corrects comparison
classification without rounding prices to ticks or changing actual fills,
brackets, fees or slippage. Opening gaps retain priority; ambiguous intrabar
touches remain stop-first; adverse stop gaps still lose more than the stop and
target gaps receive no bonus.

The correction applies only to positive entry offsets used by paper v2. The
legacy three-bar path retains its original comparisons and numerical results.
The registered protocol file and all strategy/risk parameters are unchanged.

The observed MNQU6 BUY example from August 18 at 09:45 Eastern now takes the
exact 29,605 stop at its one-bar entry, returning **−$68.22**, matching the runtime
paper simulator. It previously missed that stop and returned **+$16.78**. The
original difference arose from a rescaled low only **7.105 × 10⁻¹⁵ points** above
the computed stop.

## Regression and safety results

- **58 new regression cases passed.** Before the source fix, 22 failed and 36
  passed. Coverage includes the real example and its SELL mirror, both v2 entry
  offsets, exact stops/targets, opening-gap ordering, ambiguous bars, target
  trade-through, adverse gap fills, and preservation of legacy comparisons.
  Near misses of `1e-9` points and one tick remain unfilled; the change does not
  snap prices to the tick grid.
- **592 backend safety tests passed** in 21.17 seconds: the previous 534 plus the
  58 new cases. Databases and ledgers were isolated and external network access
  was denied. No frontend code changed.
- The actual preserved legacy artifact was priced with the old and new functions
  across **262,144 path/direction/scale cases**. Net values, exit kinds and holding
  durations were **byte-for-byte identical**.
- Repeated independent absolute-OHLC and runtime-paper comparisons found **zero
  mismatches** in all panels below. Both directions and both v2 offsets are
  covered. The panels overlap; these counts are checks, not independent market
  observations.

| Panel | Paths | Side/offset checks | Mismatches |
| --- | ---: | ---: | ---: |
| Chronological validation | 4,320 | 17,280 | 0 |
| Retained candidate training paths | 4,096 | 16,384 | 0 |
| Later provider paths | 230 | 920 | 0 |
| Total checks | | **34,584** | **0** |

The largest net-price difference from the absolute scalar reference was
**5.684 × 10⁻¹⁴ USD**, with matching exit classifications and timing. The scalar
reference and runtime engine were not changed to accommodate the fix. Independent
day-cluster/HAC/Student-t checks also passed, with maximum difference
**2.274 × 10⁻¹³** across the five fixed queries and both directions.

## New fit and compatibility

A new file was produced by running the fitter again against the same frozen
source data. No earlier artifact was edited or rehash-labeled. The new artifact
is [candidate-v2.json](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/exact-touch-fix-20261029/candidate-v2.json).

| Field | Result |
| --- | --- |
| Specification | Bayesian cells, mixture 0.0, `post_record_open_fixed_deadline_v2` |
| Stop policy | Existing `observation_volatility_uncapped_v1` |
| Declared training window | 240 complete sessions, November 6, 2025–October 23, 2026 |
| Retained paths | 4,096, representing 171 dates |
| Final training label | October 23, 2026, 15:45 Eastern |
| Artifact canonical identity | `d0a8d92bbfcb0580e10e0f21a8119bc4911d5c0b3f182ef6d0aa4477e39a60e3` |
| Artifact file SHA-256 | `2bc90750bd4608e0aefe05d47e43a4ab5d2273742fd0e734acfdf666d6ec30c6` |
| Implementation binding | `59c88d82576cb1c2511daf550f50c602c3cf335f0f452eb630d0a8d52d60fd88` |
| Model payload SHA-256 | `554a3df6d3afd3842abfd3716fac0fac81a3c284e0af8a2bade92e3230521a4d` |
| Status | `unvalidated`, `routing_allowed=false`; not installed |

The regenerated model payload equals the prior candidate's payload, as expected
from identical data and fixed fitting parameters. The artifact identity and
implementation binding are new. The corrected loader accepts this new binding
and rejects the old candidate's binding. Actual-candidate scope checks, HIDDEN
account simulation eligibility, wrong-owner rejection and broker-loader rejection
all passed in isolation. The model was 5.987 days old at that check; future
training age must still be assessed at installation/use.

## Repeated statistical results

The same frozen 60-session window, daily chronological refits, costs, bootstrap
seed, day-block lengths, scoring lattice and acceptance criteria were reused.
No data, parameter or threshold was selected after viewing the prior results.

- **4,320 forecasts over July 31–October 23:** zero qualifying signals and zero
  candidate trades. Best score remains **−$4.807**, below the unchanged strict
  **$1** requirement.
- **230 exact-candidate post-training forecasts over October 26–29:** zero
  qualifying signals and zero trades. Best score remains **−$6.051**.
- The 1,440 nonoverlapping chronological probability scores are unchanged:
  candidate Brier **0.249328**, pool Brier **0.248413**; candidate CRPS
  **$30.689418**, pool CRPS **$30.712892**. The paired day-block comparison still
  favors the pool on Brier and does not establish a CRPS improvement.
- Only one probability bin has adequate observations, so the required broad
  calibration check remains undefined. With zero trades, positive expectancy,
  trade-tail loss, concentration and profitable cost-stress requirements are not
  established. Zero P&L does not demonstrate profitability.

All two-bar score reports and simulated account performance exactly match the
prior validation run. Complete metrics, including the unchanged statistical
qualification failures, are retained in
[results.json](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/exact-touch-fix-20261029/results.json).
The historical data was previously inspected. This is chronological development
validation plus a short retrospective provider check, not an untouched holdout
or prospective forward result.

## Preservation, patch and deployment boundary

All **118 protected files** and the frozen source-file hashes passed preservation
checks. The only application-source change in this follow-up is the v2 numerical
comparison correction. The prior full patch, previous v2 candidate, its report,
failing probes, original audit and existing model artifacts remain unchanged.

The unchanged limits include one MNQ, $250 daily loss admission, 30 entries/day,
300-second cooldown, 120-second market freshness, minimum 300 training paths and
20 effective days, strict $1 entry edge, $1 cost-uncertainty buffer, original
forecast multiplicity deduction, 1.5× target and unchanged transaction costs.
No historical database import, cloud write, broker mutation, production write or
service change was performed.

The [incremental repair patch](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/exact-touch-fix-20261029/exact-touch-fix.patch)
applies to the previously reviewed local workspace. The
[combined patch](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/exact-touch-fix-20261029/topbot-inactivity-fixes.patch)
contains the earlier inactivity fixes plus this correction and reports, against
the original captured HEAD `6c8d778` plus pre-existing workspace changes. These
are review artifacts, not patches directly applicable to the running release.
Both patches were applied with strict whitespace checks to isolated reconstructed
bases and compared against the current files.

The numerical blocker is resolved. **Production activation remains pending**:
reconcile and test the exact deployed-release package, verify its implementation
hash matches this artifact, review observation installation, and take fresh
verified off-device backups before an authorized rollout. Keep live-routing
gates disabled. The statistical limitations above remain; numerical correctness
does not grant live-trading qualification.

Supporting receipts: [safety suite](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/exact-touch-fix-20261029/safety.log),
[before-fix regressions](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/exact-touch-fix-20261029/boundaries-before.log),
[after-fix regressions](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/exact-touch-fix-20261029/boundaries-after.log),
[legacy parity](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/exact-touch-fix-20261029/legacy-parity.json),
[fit receipt](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/exact-touch-fix-20261029/fit-receipt.json),
[frozen plan](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/exact-touch-fix-20261029/plan.json),
and [preservation receipt](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/exact-touch-fix-20261029/preservation-after.json).
