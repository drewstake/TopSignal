# September 26 trading-bot audit remediation

The supplied audit was treated as findings to investigate, not as authorization
to trade, restore retired history, publish a model or change cloud billing.
Code remediation is implemented locally. Data-dependent acceptance and broker
observations remain open; this is not a validated trading strategy.

## Confirmed findings

| Finding | Implemented change | Verification / remaining evidence |
| --- | --- | --- |
| C1 | Regular-session population only; 15-minute labels end by 15:45 ET | Session and adapter tests at overnight and late times; hourly forward counters added. Actual forward counts unavailable. |
| C2 | Shared exchange/Topstep flat deadline, two-minute close buffer and clamped exit | Thursday/Friday 16:50 and Thanksgiving 12:50 tests. Actual-candle boundary replay: 67/67 HOLDs across daily halt, Friday and early close. Topstep published terms checked; actual broker halt behavior remains unmeasured. |
| C3 | Close with protection intact, then cancel residual orders and verify both flat and no orders | Failed-close, partial-cancel, missing-stop and worker recovery tests. Actual provider behavior still needs observation. |
| C4 | Pending exit skips evaluation with HOLD and preserves enabled/running state | Integration test confirms one entry and no run shutdown. |
| C5 | Frozen refit, immutable experiment/evidence references, required acceptance keys, implementation/spec hashes and horizon embargo | Loader rejects missing checks, changed code, future labels, missing evidence, changed pool mix and tampered evidence. Synthetic full-binding test passes. No real passed artifact exists. |
| C6 | Explicit contract_rolled stop and root-plus-causal-roll model scope | Run/exit integration regression and root-selector tests. |
| C7 | Only signal rows draw arrows; visible hollow blocked labels; duplicates and blocked overlays excluded | Marker and overlay regressions; fills and verified-flat observations have separate markers. |
| C8 | Frontend historical halt cutoff matches backend | Shared calendar fixture covers June 25/28 boundary and modern sessions. |
| C9 | Saturday New Year no longer closes the previous Friday | Shared calendar test plus 1,020 actual one-minute records on 2021-12-31, 00:00–17:00 ET. Rebuild adds one 4h and one 1d bar, with no changed OHLCV on shared timestamps. |
| C10 | Cancelled/Expired reconcile to cancelled | Status 2/3/4 regression. A cancelled remainder with a verified fill retains its timed-exit obligation. |
| C11 | Sub-timeframe marker uses the final constituent chart bar | Five-minute decision at 13:35 plots at 13:39 on a one-minute chart. |
| C12 | One 18:00 ET VWAP definition, labelled context only | Shared backend/frontend VWAP fixture; live-tail calculation agrees with full recomputation. |
| C13 | volatility_above_risk_cap with sigma and implied stop | Typed cap exception, API fields and high-volatility regression. |

## Research findings

| Finding | Implemented change | Verification / remaining evidence |
| --- | --- | --- |
| H1 | Local causal Databento pooling by delivery, with ProjectX only for parity | Authorized source restoration/rebuild provides 1,622 complete sessions. History ends July 10; the ProjectX July probe returned no bars. No overlap, parity unverified. |
| H2 | Explicit volatility-conditioned population; rejection counts by session/fold | Actual development population: 55,898 of 115,049 candidate windows rejected by the cap (48.59%); 58,865 accepted and 286 other rejections. No candidate produced a trade. |
| H3 | Effective-day Student t plus eight-test Bonferroni bound | Numerical implementation tests and 100 zero-drift synthetic refits (0 refits proposed a trade). This does not verify market calibration. |
| H4 | Mixture chosen by calibration net rule P&L; flat/long/short baselines | Actual 74-fold simulation: candidates 0 trades; always-long 20,860 trades / -$70,154.20; always-short 20,225 trades / -$77,593.50. Flat benchmark $0. |
| H5 | Unrounded volatility path normalization; independent bracket stress; net R | Pricing/regression tests cover scale and bracket separation. |
| H6 | One-tick target trade-through stress | Touch-without-through stays open in the synthetic regression. Real queue/non-fill rate unknown. |
| H7 | BUY-only probability scoring on a non-overlapping fifteen-minute lattice | Scoring tests count independent decisions; execution simulation still evaluates every five-minute close. |
| H8 | Pooled disjoint OOS ledger, >=3 folds, no negative fold, 20,000 non-circular replicates, paired score bounds and seeds 1–20 | 74 actual folds completed. Candidates had no trade ledger, so per-trade lower bounds and seed sensitivity are undefined (zero usable seeds), not passing evidence. |
| H9 | SHA-pinned protocol; runtime/research read costs, horizon, support, risk and cooldown constants | Modified-spec rejection test; code changes invalidate installed bindings. |
| H10 | Immutable registration ledger plus reviewed historical ledger; family correction uses the configuration count | 256 manifest-verified archived configuration runs now contribute to the correction. This partial inventory cannot authorize live promotion; complete history remains unverified. |
| H11 | Observed-minute five-minute aggregation, no fabricated candles; local minute-count quality sidecar | Actual rebuild: 3,011 added five-minute bars, no changed shared OHLCV; 4,676 unobserved minutes flagged inside retained buckets. Calendar gap audit: 406 missing open five-minute intervals across 1,858 sessions, 7 within regular sessions. |
| H12 | Decision-anchored exits, common cooldown/limits/budget; signed decision-to-fill difference and measured close-verification drift | Frozen-clock/cooldown/exit tests. Current account records supply execution prices/timestamps, but none matches a strategy decision or timed-exit plan. Model-specific fill differences and exit drift remain unavailable. |
| H13 | Direct conditional depth net-payoff regression and clustered covariance | Existing depth fitting/round-trip tests. Depth remains research-only. |

## Execution hypotheses

| Finding | Implemented change | Verification / remaining evidence |
| --- | --- | --- |
| E1 | Worker checks correct stop side, size, type, parent and working state; missing stop triggers critical flatten | Provider-fake tests and one historical manual order with correctly linked, sized stop/target children at the requested 55/40 ticks. Continuous protection, account bracket mode and delivery timing remain unverified. |
| E2 | API start cannot route a mathematical entry; live worker lease required | Start-without-lease integration test; mutation fencing retained. |
| E3 | Manual test orders blocked while timed exits remain; observed post-entry flat completes obligation | Stopped-run/manual-test regression; no external manual orders were placed. |
| E4 | Stop risk includes fees and two stop-slippage ticks, with stable boundary arithmetic | Exact-budget replay/live tests; manual admission uses the same additions. Current records show two ten-contract MNQ stop exits six ticks beyond their stop prices, exceeding the two-tick allowance. This does not establish costs for the frozen one-contract rule; execution parity remains failed/unverified. |
| E5 | Explicit micro-contract list replaces the broad M prefix | Classification regression. Fresh provider records reconcile all observed charges across three accounts and 18 closing fills, including 15 MNQ closing fills at $1.22 round trip per contract. Risk and metrics calculations agree with both execution sides; no double count found. A formal statement was not used. |
| E6 | Injected risk/session/cooldown clock; close-verification elapsed time included in drift | Clock and exit tests; flat observations are not labelled as exact fill timestamps. |
| E7 | One critical operator alert after three unresolved cycles; no resend | Escalation regression and manual resolution procedure in the strategy contract. |

## Simplification and maintenance

- New configurations default to TopBot Mathematical. Legacy creation and live
  execution require `TOPSIGNAL_ENABLE_LEGACY_STRATEGIES=true`; historical backtest
  engines remain reproducible under explicit legacy scope.
- Removed unused technical_indicators module and unused Backtest panel/tests.
- Recorded forecast leads analysis; heuristic indicator context is collapsed.
  Missing local scenario weights are unavailable, not invented probabilities.
- Relational volume-roll code is labelled legacy/retrospective. Causal local
  replay is the research source.
- Chart signal queries use loaded date range, run and execution mode, with a
  visible 2,000-result cap. Recent activity still includes HOLDs and errors.
- Live candle/VWAP tails reuse prepared history. Synthetic OHLC continuity
  alteration was removed. Demo preset matches the mathematical configuration.
- Added nullable first_fetched_at and revision_hash metadata, preserving unknown
  historical first receipt times. No backfill or historical row expansion.
- Contract selectors require one active matching root. Singleflight normalizes
  both provider window and key to seconds, preserving explicit window identity.
- Decision research uses model P(net profit) buckets and a fifteen-minute horizon;
  barrier proxies are explicitly distinguished from executed returns/calibration.
- Profit factor is net. Drawdown percent uses an explicitly supplied starting
  balance; it is unavailable when that basis is unknown, including aggregates.
- Current strategy/research docs describe v3; v1/v2 results are historical records.

## Evidence and deployment status

The user initially had no active Databento cache, MNQ fee statement or saved
forward observations, then explicitly authorized restoring source archives and
rebuilding local history. Read-only inspection found no installed model JSON
files. The local SQLite has 3,128 U26 five-minute rows (Aug 24–Sep 9)
and 3,750 Z26 rows (Sep 8–25), before quality/session filtering. The
[read-only coverage audit](topbot-projectx-coverage-2026-09-26.json) found 10
complete U26 sessions and 14 Z26 sessions. These short fragments are parity
inputs only and cannot substitute for the registered history.

The [cache rebuild audit](topbot-cache-rebuild-audit-2026-09-26.json) establishes
historical coverage and the calendar/resampling corrections. The registered
development run completed all 74 chronological folds with `offline_failed`.
The following remain unproven: actual halt
cancel/close behavior and general zero-volume semantics,
Databento/ProjectX parity, broker bracket mode, model-specific forward slippage/target
non-fills/exit drift and calibration, intratrade drawdown, untouched post-freeze
outcomes, and the complete historical experiment count. None is marked passed.

A [read-only ProjectX probe](topbot-projectx-readonly-probe-2026-09-26.json)
used the existing configured connection for market-data requests only. On
September 25, 13:30–15:30 UTC, `limit=2` returned the newest two of 24 five-minute
bars. All 24 exactly matched OHLCV aggregated from 120 one-minute bars, supporting
UTC bar-open timestamps for this window. No zero-volume bar occurred in the
sample; it cannot establish general filler-bar behavior. The July 9 U26 request
returned no bars, so provider-to-Databento parity remains unavailable. No accounts,
orders, positions or database records were modified. The API's documented request
fields are at [ProjectX Retrieve Bars](https://gateway.docs.projectx.com/docs/api-reference/market-data/retrieve-bars/).
The [calendar gap summary](topbot-gap-audit-2026-09-26.json) has yearly counts;
per-session detail remains in the local cache audit directory.

The [MNQ fee audit](topbot-mnq-fee-audit-2026-09-26.json) inspected 125 cached,
non-voided provider events from August 12 through September 25. Their separate
fields contain $0.36 fees and $0.25 commission per side per contract. All 63 closed
events match both application fee calculations, with no detected double counting.
The resulting $1.22 round trip agrees with the current
[TopstepX fee table](https://help.topstep.com/en/articles/8284213-topstepx-commissions-and-fees).
This establishes cached sample consistency, not a guarantee about future provider
fields. The cached bot decisions
belong to the retired strategy and cannot supply this model's forward evidence.

Following the user's request to use current account trades, the
[current-account audit](topbot-current-account-audit-2026-09-26.json) fetched a
30-day window from all three active accounts. ProjectX classifies all three as
simulated. Returned activity spans September 21–25: 35 fill records, 18 closing
fills and 60 orders. All fills match 31 distinct orders by account, contract,
side and order ID; volume-weighted fill prices match their order records.
Four filled orders on one account nevertheless report `fillVolume=0`. Execution
records, rather than that field alone, establish the observed quantities.

For each account/contract, the observed positions match from flat back to flat.
The sum of actual per-execution fees and commissions equals both application
calculations, with zero discrepancy. This supplies current broker-API fee
reconciliation; it does not depend on obtaining a separate statement.
The historical manual-order test also has correctly sized, opposite-side stop
and target children, with the requested 55/40-tick distances. Its stop's final
update is 42.549724 seconds after the target's final update. Those final-state
timestamps cannot establish automatic OCO timing or continuous protection.

Five MNQ stop orders have matched closing fills. Two ten-contract orders filled
six ticks beyond their stop price, exceeding the two-tick risk-budget allowance.
The strategy's frozen quantity is one contract, so this sample cannot establish
its cost distribution; the observed costs do not justify passing execution
parity. No strategy decisions or timed-exit plans match these current fills.
No research parameters were changed in response to these observations.

Partial `fill_exit_parity` evidence is archived under SHA
`af05605c34247074aa5158b74a3851afa1c2797bd76511e0028baeafc3cb2ae1` in the local
`probabilistic-v3/evidence` directory. Fee reconciliation and the previously
observed retrieve-bars limit behavior are marked true; the remaining checks
are false. This record cannot authorize promotion. Raw account records remain
in the ignored local cache; no orders or database rows were changed.

The [boundary replay](topbot-boundary-replay-2026-09-26.json) used actual closed
Databento windows and a frozen clock to exercise the mathematical adapter across
a Thursday halt, Friday close and Thanksgiving early close. All 67 decisions
were HOLD with routing disabled. This is an offline adapter observation; it does
not establish actual broker cancel/close behavior during a halt.

The migrations `db/migrations/20260926_add_candle_provenance.sql` and
`db/migrations/20260926_allow_cancelled_order_attempts.sql` are prepared but have
not been applied to a cloud database. Run the storage report and normal migration
process before deployment. Candle metadata adds roughly 80 bytes per new/updated
row; the status constraint expansion adds no rows or indexes.
Decision provenance also adds up to 21 SHA-256 references (about 1.4 KB plus JSON
keys per recorded forecast); include evaluation cadence and retention when
estimating deployment growth. No credentials,
orders, active runs, subscriptions, billing,
retention or cloud data were changed.

After user authorization, two original Databento ZIPs and their small metadata
files (67,898,148 bytes total) were recovered through an empty staging directory.
The retirement archive and each extracted file matched their recorded SHA-256.
`build_databento_cache.py` rebuilt 1m, 5m, 4h and 1d series under the configured
filesystem boundary; existing experiment registrations were preserved. The old
research directory was not restored. Its archived summaries were read directly
for the [partial experiment inventory](topbot-known-experiment-ledger-2026-09-26.json).
The source archives and retirement backup remain local; an off-device backup
destination has not been configured.

## Registered development result

Experiment `2bfc29c748473b55578a75c9502c04e295a93fa99d768b9741345639fdb79ad5`
used 1,622 complete sessions, 74 validation folds and a conservative multiplicity
count of 292 (256 known archived runs plus 36 registered v3 configurations). The
complete historical count remains unverified. Both candidate models and the
empirical pool generated zero admissible trades. Expectancy, per-trade confidence
bounds and bootstrap-seed sensitivity are undefined; minimum trade count,
calibration and profitability checks fail. This result supplies no evidence of
a tradable edge and cannot support a model installation or routing.

The [result summary](topbot-probabilistic-v3-result-2026-09-26.json) links the
native immutable report generated by the final implementation. Baseline ledgers
are stored as hashed local companion files; the report is 22,869,025 bytes, within
the loader's 25 MB limit. All 3,111 ledger references resolve to 149 verified
files, with matching content hashes and row counts. Registration, protocol and
current implementation hashes also match. The loader rejects both candidates
because the development experiment failed, not because of a packaging error.
Earlier reports remain preserved. The final run used unchanged registered
parameters; the additional registration is conservatively included in the
multiplicity correction.

Topstep's published terms require flat before 15:10 Central / 16:10 Eastern or
the market close, whichever is earlier. The implementation applies this
conservative deadline to automated Practice entries as well. Sources checked:
[Topstep terms](https://www.topstep.com/terms-of-use),
[ProjectX real-time enums](https://gateway.docs.projectx.com/docs/realtime/),
[CME halt removal](https://www.cmegroup.com/notices/electronic-trading/2021/06/20210621.html),
[SciPy Student t](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.t.html).

## Verification

- Backend: `cd backend; .venv/Scripts/python.exe -m pytest tests -q` — 2,132 passed,
  9 skipped. Includes database-persisted cancelled-status reconciliation,
  partial-fill exit recovery and preserved first-receipt/revision metadata.
- Frontend: `cd frontend; npm test -- --run` — 116 files, 981 tests passed.
- Frontend production build and ESLint passed with no lint warnings.
- `git diff --check` passed.
- Actual-candle boundary replay: 67 HOLDs, no signals, across three close/halt
  windows. Cached MNQ fees: 63 closed events reconciled with no calculation
  mismatches. Fresh current-account data: 35 fills / 60 orders; fees reconciled
  across all observed execution sides and 18 closing fills.
- Registered development: 74 folds completed, `offline_failed`, zero candidate
  trades. No ProjectX/Databento overlap or model promotion. No financial
  validation is inferred from unit tests.
- Native final report: current implementation and registration hashes verified;
  3,111 ledger references / 149 distinct files checked; both failed candidates
  rejected by the loader at the development-acceptance gate.
