# MNQ probabilistic TopBot research, 21 September 2026

**Update, 26 September 2026:** The EMA/VWAP pullback strategy was removed from
TopSignal. New experiments use the
[registered protocol v2](topbot-probabilistic-protocol-v2.json), identical to v1
except that the paired daily benchmark is flat (no trading, $0 per session)
instead of the v5 replay comparator described below. Protocol v1 and its
21 September results remain historical records.

Status: **research implementation, not a validated replacement**. Mathematics
specifies assumptions and tests; it does not guarantee profitability. The v5
EMA/VWAP strategy remains the default and rollback comparator. No active run,
account limit, broker setting or live-routing permission is changed by this work.

The [registered protocol](topbot-probabilistic-protocol-v1.json) was written after
the coverage audit and before examining candidate payoffs. Six candidate/mixture
configurations are budgeted. Further changes constitute new experiments. The
small existing sample is development data; it cannot supply the final holdout.

## What the repository can observe

Read-only audit of `backend/storage/offline/topsignal.sqlite3` found 18,433 candle
rows, 250,000 bounded observations and 61 decision snapshots. This is a local
snapshot, not a claim about current cloud coverage or subscription entitlement.
The configured application database is PostgreSQL; this task does not connect to
it. SQLite is opened with `mode=ro`; only an explicitly scoped MNQ stream is used.

| Input | Observed availability | Consequence |
| --- | --- | --- |
| Five-minute MNQ OHLCV | 3,128 cached rows before partial exclusion, August 24–September 9, with discontinuous coverage | Can exercise the research pipeline; far short of 200 eligible sessions |
| One-minute MNQ OHLCV | 2,262 cached rows before partial exclusion, September 6–8 | Cannot reconstruct long-term intrabar execution; never upsample coarser candles |
| Hourly/daily candles | Longer history, different resolution | Cannot substitute for five-minute features or stop/target labels |
| Trade prints | 121,976 captured prints, September 8–9 | All have unknown aggressor side in this snapshot; no signed order flow |
| Quotes | 127,956 quote records; 126,606 with both bid and ask | Viewer-dependent capture; event-weighted spreads are not an unbiased time-weighted cost estimate |
| Depth | 68 resets, no depth-update rows | No historical depth or reconstructable queue positions |
| Timestamps | Provider and receive times retained for observations; candle open time, partial flag and fetched time | Test ordering, completion, staleness and gaps; receive time is not exchange latency or a fill timestamp |
| Costs | Repository replay default is $0.61 per side for MNQ | Explicit scenario assumption, not a verified fee for every account/date |
| Historical Databento | Active filesystem cache absent; retired by user on September 8 | No restoration, purchase, or cloud historical import |

The ProjectX API exposes OHLCV and a partial-bar switch; its `live` flag chooses
the data subscription and does not describe bot execution mode.
[ProjectX bars](https://gateway.docs.projectx.com/docs/api-reference/market-data/retrieve-bars/).
Its real-time API defines quotes, trades and depth subscriptions; API capability
does not establish that an account has complete historical data. The repository
only records an explicit aggressor field, not inferred candle direction or the
trade-log enum, as classified flow.
[ProjectX real-time documentation](https://gateway.docs.projectx.com/docs/realtime/).
An uninterrupted, licensed capture with reconnect/sequence diagnostics and
measured fees/fills would be needed for microstructure research. Nothing here
buys data, opens a feed, expands retention or requests new subscriptions.

## Research and candidate choice

These are methodological results, not demonstrations of profitable intraday MNQ
trading. The transfer to this instrument and horizon is a hypothesis to test.

| Approach and primary source | Assumptions, evidence, limitations | Data, computation, decision |
| --- | --- | --- |
| Conjugate Bayesian inference; [Murphy (2007)](https://www.cs.ubc.ca/~murphyk/Papers/bayesGauss.pdf) | Closed-form posterior inference under specified Gaussian sampling assumptions; posterior predictive uncertainty includes parameter uncertainty. Conditional independence and stable parameters are strong market assumptions; heavy tails and changing drift can dominate. This is derivation, not a trading performance study. | Candles suffice for return models; O(n) sufficient-statistic fit and O(1) update. Prefer a simpler empirical payoff distribution with explicit shrinkage over an unjustified Gaussian drift claim. |
| Sequential state estimation; [Kalman (1960), original paper](https://skoge.folk.ntnu.no/puublications_others/1960_Kalman%20-%20A%20new%20approach%20to%20linear%20filtering%20and%20prediction%20problems%20-%20Orinal%20version%20with%20comments.pdf) | Linear dynamics and known noise covariance support optimal linear estimation (full distributional interpretation additionally needs distribution assumptions). A filtered trend is not evidence of predictable net returns. Noise choices, jumps and hindsight smoothing can introduce failure or leakage. | Candles; scalar O(1) filter. Reject for this first experiment: limited data cannot justify process-noise tuning in addition to trading thresholds. |
| Regime/change-point detection; [Adams & MacKay (2007)](https://arxiv.org/abs/0710.3742) | Bayesian run-length inference assumes a hazard and independent segment parameters; real-data demonstrations support detection, not MNQ profitability. Volatility bursts can be mistaken for new drift regimes; an incorrect likelihood can produce confident errors. | Sequential returns; O(R) per update with truncated run-length R (unbounded exact implementation grows with history). Keep regime diagnostics and missing-data abstention; defer a learned regime model until enough independent sessions exist. |
| First passage and optimal stopping; [Carr & López de Prado (2014)](https://arxiv.org/abs/1408.1159) | Numerical results for an assumed discrete Ornstein–Uhlenbeck process; the paper explicitly presents a conjecture, not a general closed-form trading theorem. Estimating stable mean reversion and treating intrabar paths as known are major risks. | Candles can support conservative path labels; tick/quote histories are needed for accurate fill ordering. Monte Carlo or grid stopping solvers add substantial work and tuning. Use directly observed candle paths with stop-first ambiguity handling, a finite horizon and gap losses instead of claiming an optimal stopping solution. |
| Order-flow imbalance; [Cont, Kukanov & Stoikov (2014)](https://arxiv.org/abs/1011.6402) | Empirical short-interval price-impact relationships on 50 US stocks; this is not an out-of-sample MNQ strategy. Requires best-book changes, cancellations and reliable ordering; contemporaneous impact need not be a tradable forecast. | Event/quote/depth capture and coverage checks; O(events) aggregation. Excluded: the available snapshot has no depth history and no explicit aggressor side. Candle volume is not order flow. |
| Distributional prediction; [Gneiting & Raftery (2007)](https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf) | Proper scores reward honest distributions in expectation; calibration and sharpness must be assessed together. A good score does not establish positive trading utility. | Use the empirical distribution of exit payoffs; Brier/log scores for net-positive outcomes, CRPS for the full payoff distribution and reliability bins on later dates. O(n log n) weighted CRPS, no new numerical dependency. |
| Robust decisions; [Mohajerin Esfahani & Kuhn (2018)](https://arxiv.org/abs/1505.05116) | Worst-case expected loss in a Wasserstein ambiguity set can have tractable reformulations and finite-sample guarantees under its assumptions. Market dependence and shifts do not inherit those guarantees automatically; selecting the ambiguity radius creates another tuning problem. | Full robust optimization is unnecessary here. Use a transparent net-mean minus uncertainty penalty, a separate cost buffer and strict position constraints. This is an engineering lower-bound estimate, not a certified Wasserstein guarantee. |

Two candidates share the same conservative execution scenarios and have no EMA
touch, VWAP crossing or other indicator-pattern entry requirements:

1. **Bayesian cells v1**: condition on the sign of the three-bar normalized return
   and whether recent RMS volatility exceeds the preceding 20-return RMS. Give
   observations in that cell unit weight and add 20 pooled pseudo-observations.
   This is empirical-Bayes shrinkage of a discrete path distribution. Pool prior
   and observations overlap, so it is a regularized empirical estimate, not an
   exact independent-prior posterior or a guarantee of calibration.
2. **Kernel paths v1**: Gaussian distance weights over four causal features, with
   a 5% pooled floor. Standardization uses training means/scales only. Fixed
   bandwidth one; distance is clipped for numerical stability. Assumes smooth
   conditional path distributions. Sparse support and the curse of dimensionality
   are reasons to prefer the cell model if evidence is comparable.

The **empirical pool v1 baseline** uses uniform weights on exactly the same
training paths. A separate **v5 replay comparator** calls the unchanged production
entry evaluator and retains its 50/50 bracket and bracket-only exit policy.
Probabilistic candidates use at most 25-point stops and one contract; no exposure
increase compensates for weak results. The incumbent has no probability forecast,
so probability scores are undefined for it, not fabricated from its indicators.

The kernel and cell calculations are experimental hypotheses chosen for modest
data and auditability, not because the cited literature establishes these exact
features. Feature usefulness is tested against the pooled baseline. At most
three pool-mixture weights (0, 0.5, 1) per candidate are selected using calibration
CRPS; no feature search is permitted by this protocol. If both pass, choose cells
unless the paired validation evidence supports the more complex kernel model.

For n training paths, both prototypes store O(nH) values with H=3; cell weights
and kernel distances require O(n) and O(4n) work per forecast. Scenario repricing
is O(nH); this implementation groups residuals with O(nD) work for D training
dates. Calibration CRPS costs O(n log n) per action/observation. Artifacts are
limited to 4,096 paths and 4 MB for the Dry Run reader. Full-size latency must
still be measured before any execution integration; the small diagnostic is
not a representative latency benchmark.

## Mathematical contract

MNQ is $2 per index point, with a 0.25-point tick worth $0.50.
[CME contract specifications](https://www.cmegroup.com/markets/equities/nasdaq/micro-e-mini-nasdaq-100.contractSpecs.html).
Let t be the close of a completed five-minute candle. The information set contains
only this contract/owner/subscription's completed candles through t, a model
trained on labels completed strictly before t, and explicit cost assumptions.
Partial, nonfinite, duplicate, out-of-order or missing required bars are rejected.
No last-traded price from the chart or subsequent candle enters the features.

From the last 21 closes let r be the 20 point changes and
s = sqrt(mean(r²)). Features are last return/s, three-bar return/(s sqrt(3)),
log(RMS(last 5 returns)/s), and log(last volume/mean(previous 20 volumes)).
Missing or nonpositive volume blocks inference rather than receiving a zero
feature. This uses unsigned activity, not signed flow. Conditioning features are
experimental; volatility scaling is a risk normalization assumption.

Set D = max(4, ceil(s sqrt(3)/0.25) × 0.25) points. Abstain when D > 25.
Enter at the next observed bar open with adverse entry execution costs; stop
distance D and target distance 1.5D are tick-aligned and anchored to that modeled
fill. Exit at the first stop/target touch or the third bar close, at most 15
minutes after the decision. Gaps through stops fill at the worse open; if both
boundaries occur inside the same candle, stop wins. Targets never receive
beneficial gap improvement. No new entry after 15:30 ET; the candidate horizon
ends by 15:45. A missing bar in the required path invalidates the label rather
than inventing an interpolated fill. Such exclusions are counted and prevent
promotion when coverage is inadequate.

Each training scenario stores the next three OHLC bars relative to its first
open, divided by its contemporaneous D. For a new D, rescale and reprice each
scenario with the identical bracket/cost engine. This assumes normalized paths
are locally transferable; it preserves empirical asymmetric tails but cannot
represent unobserved crashes. An observed positive outcome is not clipped to a
win/loss label for payoff estimation.

For action a in {BUY, SELL}, weights w_j sum to one and modeled net payoff is
Y_a,j = 2 × signed(exit_j − entry_j) − 2 × commission_per_side.
The conditional empirical distribution is F_a(y) = sum_j w_j 1(Y_a,j ≤ y).
Display P(net>0), P(net≤0), stop/target/time probabilities and E[Y_a] separately.
NO TRADE has incremental payoff and exposure zero. This is not an instruction to
flatten an existing position.

Costs: assume one tick bid/ask spread, one tick slippage per side, half a tick
latency allowance per side, and $0.61 fees per side. Adverse entry and exit
friction are ceil((spread/2 + slippage + latency) ticks), two ticks per side in
the base case. The cash-equivalent round trip is $3.22. Spread is an assumption
when synchronized quotes are missing, never a measured zero. Fees, spread,
slippage and latency are separately disclosed. Five-minute data cannot identify
sub-bar latency, queues, rejects or fill probabilities. Stress one full bar of
entry delay and larger frictions; no claim of fill accuracy follows from passing.

For each action, aggregate weighted residual payoff contributions by trading
date. Estimate uncertainty as the larger of independent-day and Bartlett/HAC
standard errors with four daily lags. Effective days = 1/sum(day_weight²).
The research utility is L_a = E[Y_a] − 2.576 SE_a − $1 cost uncertainty.
Require at least 300 training examples, 20 effective days, and L_a > $1; choose
the action with greatest L_a, otherwise NO TRADE. This conservative estimate is
conditional on the empirical model and weak temporal dependence, not a universal
confidence guarantee. Strong serial dependence or distribution shift invalidates
it. Research proposals remain separate from execution: this revision always
returns **NO TRADE for routing**, even when a shadow proposal is BUY or SELL.

Fit on 60 chronological eligible sessions, tune distribution shrinkage on the
following 20 sessions, evaluate on the next 20 and advance 20 sessions. Training
labels use a fixed 15-minute lattice to avoid overlapping future paths; validation
evaluates each new five-minute close. Enforce label-end < split boundary, never
shuffle dates, never normalize on calibration/test, and do not cross contracts
or disconnected runs of candles. Fit/update only on completed historical labels;
no posterior update uses an unresolved outcome.

## Validation and promotion boundary

Reserve the final 60 eligible sessions before fitting anything. Three walk-forward
folds require 140 earlier sessions; minimum total is 200. A development command
must not inspect holdout payoffs. Source fingerprints, exact date partitions,
protocol/source code hashes, parameters, dependency versions and every attempted
configuration belong in local immutable experiment records. Results using
already-examined 2019–July 2026 history are development comparisons, not untouched
validation. Repeated experimentation can invalidate a nominal holdout even when
each individual test is chronological.
[Bailey et al. (2015)](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).

Report Brier, log loss, CRPS, fixed probability-bin reliability, net expectancy,
daily equity/drawdown, worst trade and 95% expected shortfall, entries, turnover,
holding exposure, early/late session and train-defined volatility regimes.
Resample contiguous day blocks (5 and 10 days), including zero-trade days, with
2,000 deterministic draws; compare models using paired day samples. Dependence
within days is never treated as independent trades. Weak stationarity is still
an assumption; regime reporting and stress tests do not remove it.
[Politis & Romano (1994)](https://www.tandfonline.com/doi/abs/10.1080/01621459.1994.10476870).

Acceptance requires all machine-readable thresholds, including positive lower
bounds after multiplicity adjustment (six configurations), at least 200
validation and 200 final trades, calibration error ≤5 percentage points in
adequately populated bins, scores no worse than pool, drawdown ≤$1,000 and tail
expected shortfall ≤$100/contract. Profits must survive removing the best five
days and trades, and the best day cannot supply >20% of positive daily P&L.
Require regime support and all predeclared cost/latency/parameter/missing-data
stresses. Sparse bins, undefined metrics and missing evidence fail closed.
These thresholds are preregistered engineering criteria, not financial laws.

The final holdout can be evaluated only after freezing the selected model and
recording unseen provenance; any later modification requires new unseen data.
At least 20 forward Dry Run sessions and verified actual-fill bracket/time-exit
parity are additional requirements. The current research interface has no broker
adapter, promotion switch or path to Live Run. Even passing future statistical
checks would require a separate reviewed execution integration. Protective stops,
authoritative positions, tenant checks, reconciliation, emergency controls,
worker leases and request-level Live Run confirmation remain in the shared engine.

The [storage policy](supabase-storage-policy.md) remains authoritative. Scenario
arrays, any approved future candle imports and fitted research artifacts belong
under `TOPSIGNAL_DATABENTO_CACHE_DIR`; Databento imports use the existing cache
builder. Public reports contain bounded aggregate results, never raw candles,
user IDs or replay arrays. No cloud tables, migrations or retention rules change.
