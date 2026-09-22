# MNQ Level 2 research and integration — 22 September 2026

**Research implementation only. No depth model is promoted, calibrated, or allowed
to route orders.** This extends the [existing probabilistic research](topbot-probabilistic-research.md)
and preserves its [protocol](topbot-probabilistic-protocol-v1.json), minimum data
requirements, incumbent v5 strategy, and safety boundaries. The incremental-value
experiment is specified in [depth protocol v1](topbot-depth-protocol-v1.json).
[Results and capability assessment](topbot-depth-results-2026-09-22.md) distinguish
observed market data from synthetic engineering evidence.

## Findings from primary sources

The following findings motivate hypotheses, not claims that MNQ offers a net
trading edge. MNQ has a $2 point value and a 0.25-point/$0.50 tick; the collector
checks those values against the active contract metadata.
[CME contract specifications](https://www.cmegroup.com/markets/equities/nasdaq/micro-e-mini-nasdaq-100.contractSpecs.html).

**Best-level imbalance and short-term price formation.** With best bid/ask sizes
q_b,q_a, normalized imbalance is I=(q_b−q_a)/(q_b+q_a). Gould and Bonart fit
logistic regressions to the next mid-price movement on ten Nasdaq **equities**;
their evidence concerns one tick, with stronger results for large-tick stocks.
It does not establish profitable forecasts at a fixed 15-minute MNQ horizon.
Required observations are ordered BBO prices and sizes. Feature computation is
O(1); a d-feature Newton logistic fit costs approximately O(n d²+d³) per iteration.
Stable conditional probabilities, faithful quote updates, and sufficiently
similar tick/spread regimes are assumptions. Transfer to MNQ is plausible as a
mechanical hypothesis and requires new tests.
[Original paper](https://arxiv.org/abs/1512.03492).

**Order-flow imbalance (OFI).** For consecutive BBO observations,
e_n=1[b_n≥b_(n−1)]q_bn−1[b_n≤b_(n−1)]q_b(n−1)
−1[a_n≤a_(n−1)]q_an+1[a_n≥a_(n−1)]q_a(n−1).
OFI over a window sums e_n. Cont, Kukanov and Stoikov document short-interval
price-impact relationships for 50 US **stocks**. Contemporaneous explanation is
not automatically a causal, executable prediction. Computing the sum is O(events);
it needs ordered, complete quote changes, including size decreases. It cannot
identify whether a decrease was a cancellation, execution, or combined update
without additional event semantics. Conflation or loss changes the statistic.
[Original paper](https://arxiv.org/abs/1011.6402).

**Multi-level imbalance, distance weighting and shape.** A simple observable
extension sums displayed bid and ask quantities across K levels; the weighted
version discounts levels by tick distance. Xu, Gould and Howison study multi-level
OFI and contemporaneous price changes for six Nasdaq **stocks**, using regression
and regularization. Their research motivates testing deeper information, not
claiming that our chosen distance weights or 30-second window are optimal.
O(K) static features and O(K × events) window summaries need complete multi-level
updates. Correlated depths, moving level ranks, spread changes and selection of K
can cause unstable estimates. We predeclare K=5 and sensitivity at 3 and 10.
[Original paper](https://arxiv.org/abs/1907.06230).

**Microprice.** The implemented weighted midpoint is
w=(a q_b+b q_a)/(q_b+q_a). Its displacement from the midpoint is a transparent
O(1) feature. It is **not** Stoikov's fitted microprice. That paper defines a limit
of conditional expected future mid-prices and estimates a Markov adjustment for
spread and imbalance; a discretized version solves for future state transitions.
This requires high-frequency transition data, stable conditional dynamics and
substantially more estimation than w. Evidence concerns short-term prices in an
equity-market setting, not MNQ 15-minute outcomes. We retain the simpler proxy and
do not label it an efficient price or a calibrated return forecast.
[Original paper and author's code link](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2970694).

**Replenishment, depletion and resilience.** Displayed increases/decreases at the
same price are observable; trader intent and hidden liquidity are not. A theoretical
resilience model takes price displacement D after a trade to decay as
dD_t=−ρD_t dt between further impacts. Obizhaeva and Wang analyze optimal execution
under dynamic supply/demand, rather than demonstrate a directional MNQ strategy.
Estimating ρ needs identified disturbances and subsequent recovery with controls
for new flow; attributing every size increase to replenishment after our own trade
would be wrong. Fitting a scalar exponential can be O(n), but estimating a reliable
state-dependent execution model is much harder. Our O(window events × K) features
are explicitly *displayed-liquidity change proxies*, not a fitted resilience law.
[Original paper](https://web.mit.edu/wangj/www/pap/ObizhaevaWang13.pdf).

**Queue-reactive models.** Huang, Lehalle and Rosenbaum model a book state Q as a
Markov jump process, with generator
Lf(q)=Σ_e λ_e(q)[f(q+Δ_e)−f(q)]. Rates depend on the current queue state while a
reference price is fixed, with additional reference-price transitions. Their
empirical examples are France Telecom and Alcatel-Lucent **equities**; futures
applicability is proposed, not established for MNQ. Simulation cost scales with
event count and event classes; fitting state bins can suffer severe sparsity.
Reliable additions, removals, executions and queue-state observations are required.
ProjectX's public aggregate schema does not provide individual queue position or
unambiguous cancellation events. This model is therefore deferred, not approximated
by invented order events.
[Original paper](https://arxiv.org/pdf/1312.0563).

**Point processes.** A multivariate Hawkes model uses
λ_i(t)=μ_i+Σ_j∫φ_ij(t−s)dN_j(s). Positive kernels represent excitation; stationary
linear models require suitable stability conditions on integrated kernels.
Exponential kernels permit recursive O(number of event types) updates, whereas a
naive full-history fit can be quadratic in event count. Identified event types,
fine timestamps and known losses/conflation are essential. Hawkes's original
result is mathematical, not instrument-specific trading evidence; financial
applications span markets and do not validate MNQ alpha. We defer fitting because
an aggregate, unsequenced feed cannot safely supply the required event taxonomy.
[Original theory](https://academic.oup.com/jrsssb/article/33/3/438/7027167),
[finance survey](https://arxiv.org/abs/1502.04592).

**Bayesian inference and regime changes.** A Beta-Bernoulli cell model updates
P(up|cell)=(α+w)/(α+β+w+l), assuming exchangeable labels within a sufficiently stable
cell. Its O(1) updates are attractive, but sparse regimes and dependent outcomes
make naive posterior intervals overconfident. Bayesian online change-point methods
maintain a run-length posterior with hazard H(r), prediction and reset terms;
exact history grows with time and a truncated implementation costs O(R) per update.
The change-point result is generic inference, not equities/futures profitability.
We keep the existing empirical-Bayes path candidate and use fixed, train-defined
regime diagnostics. Neither a new change-point model nor additional tuning is
justified by zero usable Level 2 sessions.
[Adams and MacKay](https://arxiv.org/abs/0710.3742),
[existing Bayesian path specification](topbot-probabilistic-research.md).

**Execution and adverse selection.** For a marketable BUY of Q contracts, allocate
x_k=min(q_ak,Q−Σ_(j<k)x_j) to ascending ask prices and compute
VWAP=Σ a_k x_k/Q; reverse sides for SELL. Insufficient displayed quantity returns
unknown, never an extrapolated fill. Cost relative to decision midpoint is
s(VWAP−m)×$2×Q. A later signed markout can diagnose favorable/adverse movement, but
does not establish the counterfactual fill of a passive order. O(K) sweeps assume
displayed liquidity survives until arrival; latency, hidden liquidity and races
can invalidate that assumption. CME distinguishes aggregate MBP from individual
MBO orders: MBP does not establish exact queue position. ProjectX exposes fewer
fields than exchange-native MBO. Our scenarios are lower-bound book observations
plus explicit friction, not realistic limit fills.
[CME MBP/MBO specification overview](https://www.cmegroup.com/articles/faqs/market-by-order-mbo.html).

## Implemented observations and formulas

All prices are points; δ=.25 points/tick; q is displayed contracts. Features use
only events whose **local receive time** is at or before the decision. Provider
event time is retained separately. Neither clock difference nor a local index is
presented as exchange latency or an exchange sequence. A 30-second window must
be fully warm, the latest valid book/provider observation at most 2 seconds old,
and at least K=5 levels present on both sides. Any synchronization break resets
the window. Missing inputs return `None`, not zeros.

Let B=Σq_bk, A=Σq_ak, B0+A0 be total depth at the last observed state at or before
window start, and m=(b1+a1)/2. The following formulas are design choices to test.

| Group / feature | Formula and units |
| --- | --- |
| Level 1 spread | (a1−b1)/δ, ticks |
| Level 1 imbalance | (q_b1−q_a1)/(q_b1+q_a1), dimensionless |
| Weighted midpoint displacement | [(a1 q_b1+b1 q_a1)/(q_b1+q_a1)−m]/δ, ticks; not fitted microprice |
| Level 1 OFI, 30 seconds | Σe_n/(q_b1+q_a1), dimensionless; event changes are ordered by receipt |
| Multi-level imbalance | (B−A)/(B+A) |
| Distance-weighted imbalance | (W_b−W_a)/(W_b+W_a), W_b=Σq_bk/[1+(b1−b_k)/δ], symmetric asks |
| Shape asymmetry | (d_b−d_a)/(1+d_b+d_a), d_b=Σq_bk(b1−b_k)/(δ B), symmetric asks; weighted distance, not an order-level slope estimate |
| Concentration asymmetry | Σ(q_bk/B)²−Σ(q_ak/A)² |
| Total depth | log(1+B+A) |
| Window depth change | [(B+A)−(B0+A0)]/(B0+A0) |
| Displayed replenishment / depletion proxies | Sum positive / negative size changes at prices in both consecutive observed top-K sets, divided by B0+A0; rank entry/exit is excluded |
| Persistence | Receive-time-weighted mean of multi-level imbalance over 30 seconds |

TradeLogType Buy/Sell is retained raw. Public documentation does not explicitly
define it as aggressor side. No signed trade-flow feature, cancellation identity,
queue position, hidden liquidity, spoofing or absorption label is fabricated.
The best-level and deeper features are collinear in some regimes; ridge
regularization and prespecified ablations address this imperfectly. K sensitivities
retain the interface names ending in `_5`, but recompute using the stated K.

## Three separate experiments

Five matched feature sets use the same observations and labels: four existing
candle features; those plus Level 1; those plus Level 2; Level 2 without shape;
and Level 2 without flow. Level 1 BBO features are extracted from the same verified
book stream. Whether a standalone Level 1 subscription has identical update
cadence remains an additional provider question, not an assumed guarantee.

**A, trade-quality filtering:** preserve BUY/SELL/NO TRADE proposals from both
existing path candidates (fixed, untuned pool mixture zero). Compare unfiltered,
Level 1 filtered and Level 2 filtered versions on the same eligible decisions.
A filter may only reject the proposed side when its lower net utility fails; it
cannot create a trade merely because depth exists. Report rejection effects and
paired daily P&L intervals. The parent's separate calibration/acceptance experiment
is still required before any eventual promotion.

**B, execution modeling:** compare a regularized linear prediction of the
one-second-ahead displayed marketable sweep cost relative to decision midpoint.
Report absolute error and paired daily error improvement against Level 1. This
target includes signed price movement during the delay, so it is an execution
*proxy*, not a pure spread estimate or an observed fill. No passive fill probability
is asserted. For one MNQ contract, deeper levels may add little direct sweep value
when the best quote already has sufficient size. Actual fills and latency remain
separate missing evidence.

**C, directional/payoff forecasting:** fit one fixed regularized logistic model
per action, p_s=σ(β_sᵀz), to whether its scenario net payoff is positive. Day weights
sum to one per session. Minimize weighted binary log loss plus
(λ/2n)||β_nonintercept||², λ=1; the intercept has a small numerical penalty.
Training-only weighted means/scales standardize features. Newton steps use
backtracking, at most 80 iterations; failure or inputs beyond eight training
standard deviations cause abstention. No preprocessing or calibration learns
from validation data. Probabilities remain labeled **uncalibrated estimates**;
there is no temperature/isotonic tuning or complex challenger in this version.

Expected payoff is p_s μ_positive,s+(1−p_s)μ_nonpositive,s, where conditional payoff
means are fitted only on training data. This assumes within-class magnitudes are
adequately represented by those pooled means; probability accuracy alone is not
payoff accuracy. Uncertainty combines day-cluster coefficient covariance with the
larger of daily and four-lag HAC payoff standard errors. Lower utility subtracts
2.576 times that combined SE plus a $1 cost buffer. Choose the highest side only
if lower utility exceeds $1, with at least 300 nonoverlapping training paths and
20 equally weighted session clusters; otherwise NO TRADE. This is a conservative
engineering statistic, not a distribution-free confidence guarantee.

Primary outcomes use a decision made after a completed five-minute candle is
actually received, a one-second entry delay, a one-contract marketable quote
scenario, the inherited 4–25 point volatility stop and 1.5× target, and exit by
900 seconds after the decision. Add one adverse tick each side and $0.61 commission
each side. Stops can gap; targets get no favorable overshoot and are treated as
marketable exits, not queue fills. The one-second sampled book can miss subsecond
extrema. Closed-equity drawdown and these quote scenarios **do not establish actual
intratrade drawdown or broker stop/time-exit parity**. Short 1/5/30-second net
markouts are diagnostics only and never authorize a 15-minute trading claim.

## Validation and acceptance

Minimum 200 paired complete sessions is unchanged: 60 train / 20 reserved
calibration / 20 validation, advance 20 sessions for at least three folds, plus an
untouched 60-session tail. All 75 regular-session candles must exist; depth must
cover at least 99% of the 22,500 regular-session seconds. A day that mixes delivery
contracts is excluded. Sparse or invalid outcome windows are counted and excluded
from all baselines together. Futures rolls change contracts, never splice a book
or label across deliveries; normalized completed examples may pool across
different delivery sessions within the same owner/data subscription.

The final tail is removed before creating development labels. There is no final
test CLI shortcut. The calibration block remains reserved; the current fixed
depth model has no calibration fit. Training uses nonoverlapping 900-second
windows; labels must end before the next partition. Predeclared ablations share
exactly the same eligible observations. Too many unsupported inputs therefore
cannot make a challenger look good by quietly changing its test sample.

Report Brier/log scores and fixed-bin reliability; expectancy after costs;
closed-equity drawdown, tail expected shortfall, turnover, exposure and trade count;
early/late session and training-median volatility regimes; and day/trade profit
concentration. Five- and ten-day circular block bootstrap intervals use 2,000
draws and fixed seeds. Incremental score/error/P&L intervals are paired by day;
the 20-comparison family adjustment is specified prospectively. Millions of
updates are not millions of independent samples. Weak serial-dependence assumptions
and small session counts remain limitations.

Raw-event stresses delay receipt by 250/1,000/3,000 ms, drop every twentieth event,
and change K to 3/10. Three seconds also tests staleness. Fitted-model stress holds
decision times and realized base outcomes fixed, changes only information available
then, and abstains on missing features. Cost stresses add $1/$3 per round trip;
ridge sensitivities are .5/2. All attempt/coverage losses are disclosed. Zero
forecasts do not prove economic robustness. Parent stop/latency/candle stresses
remain separate required gates.

Acceptance requires positive adjusted lower bounds versus Level 1 for relevant
scores, execution proxy error or filtered net payoff; log loss no worse; calibration
error ≤.05 in populated bins with ≥30 observations; positive expectancy lower
bounds; ≥200 validation and ≥200 final trades; drawdown ≤$1,000; 95% tail loss
≤$100; outlier-removal/concentration/regime/cost-stress checks; all parent criteria;
verified unseen data and ≥20 forward Dry Run sessions. Missing metrics fail closed.
Even successful offline checks cannot enable routing: actual execution and
intratrade-risk verification, feed semantics, final provenance, and a separately
reviewed execution integration remain necessary.

## Local collection, reconstruction and replay

`research_depth_topbot.py` opens a dedicated market-only socket. Its HTTP client
allowlists authentication, contract lookup and bars; account/order/position
mutations are denied. No server, bot worker, existing run, database session or
existing DOM viewer is started or changed. Credentials are read into process
memory only; tokens, account balances and provider exception text are not written.
Probe scope is a hash of the credential profile, separate from any application user.

Each raw event preserves its payload, provider time, UTC receive time, monotonic
receive clock, local order index, capture ID, owner hash, contract and sim/live data
subscription. Receive order is deterministic; event-time sorting would introduce
hindsight and is not used. Bar opening timestamps are not treated as depth event
timestamps. The first received completed value is retained for candle features;
later revisions cannot rewrite past information. Bars are fetched asynchronously
once per minute with at most 36 five-minute bars, stored locally with their actual
receive times. Historical SQLite rows with unknown original receipt times are
audited only, not silently joined to new depth.

Explicit complete snapshot fixtures and the versioned `explicit_snapshot_v1`
event adapter synchronize a bounded aggregate book. Absolute size replaces prior
size, zero deletes a level, and identical absolute updates are idempotent.
Late provider updates, clock reversal, missing local indices, declared sequence
gaps, malformed payloads, stale or crossed books, reconnects and resets invalidate
state. A complete snapshot plus fresh window is needed to recover. Window and book
memory are bounded. Rank changes are not inferred to be individual cancellations.

**ProjectX's current raw adapter intentionally cannot assert synchronization.**
The published schema provides no complete-snapshot boundary or sequence guarantee.
Counting five received prices or a successful subscription is insufficient. A
documented/verified mapping of the provider's reset, snapshot completion and
incremental semantics is required before adding a ProjectX synchronization adapter.
This is an explicit external evidence requirement; the generic adapter and replay
are implemented and tested with labeled fixtures. Never relabel live raw updates
as complete snapshots just to pass a gate.

The active contract is chosen only from a unique `activeContract=true` MNQ with
expected tick metadata. Recheck every minute. A roll records the transition,
closes the old socket/book, and subscribes anew; ambiguity stops collection.
SignalR pings, resubscription, bounded backoff and at most three failed reconnects
are implemented. Authentication/rate-limit rejection stops instead of retrying
indefinitely. Real probes did not exercise an actual roll or outage. Contract
selection and book invalidation have deterministic tests; a complete production
roll/reconnect cycle remains field-verification work.

PowerShell from the repository root:

```powershell
$env:TOPSIGNAL_DATABENTO_CACHE_DIR = 'backend/storage/probabilistic-mnq'
# Read-only, at most 60 seconds. Uses the already-configured local credentials.
backend/.venv/Scripts/python backend/tools/research_depth_topbot.py probe --env-file backend/.env --seconds 30 --max-mib 32
# Correctness fixture, explicitly synthetic; output includes its capture directory.
backend/.venv/Scripts/python backend/tools/research_depth_topbot.py fixture
# Substitute the actual capture_path returned by the command.
backend/.venv/Scripts/python backend/tools/research_depth_topbot.py replay --capture '<capture_path>'
backend/.venv/Scripts/python backend/tools/research_depth_topbot.py validate --capture '<capture_path>' --sqlite backend/storage/offline/topsignal.sqlite3 --summary docs/topbot-depth-results-2026-09-22.json
```

Supply repeated `--capture` arguments for multiple captures. Mixed owner/subscription
data is rejected. `--sqlite` is optional and only audits the existing U26 snapshot;
new model inputs come from received bars and verified replay observations.
For a bounded full-session collection, run from the personal device during the
relevant session, after entitlement/semantics and capacity are established:

```powershell
backend/.venv/Scripts/python backend/tools/research_depth_topbot.py collect --env-file backend/.env --owner-hash '<SHA256-of-TopSignal-user-id>' --seconds 23400 --max-mib 2048 --max-events 4000000
```

The owner hash must belong to the application user whose credentials are used;
do not bind a probe to another tenant. The CLI does not infer this association.
For the Dry Run panel to read the collector's latest state, the backend process
and collector must resolve `TOPSIGNAL_DATABENTO_CACHE_DIR` to the same absolute
directory and use the same owner, contract and data-subscription scope. The probes
in this task use a separate credential-profile namespace and do not install a
model or change backend configuration.
Duration, event and byte bounds all stop collection at the first limit. Blocking
HTTP/close operations have their own bounded timeouts. This task does not schedule
or start ongoing capture. No subscription or historical archive is purchased.

Raw append-only NDJSON segments, SHA-256 manifests, derived observations and
immutable experiments live under `TOPSIGNAL_DATABENTO_CACHE_DIR/depth-v1/`.
Replay verifies hashes and scope before reconstruction; unfinished captures with
no manifest require explicit recovery and are not silently accepted. A collector
lock prevents concurrent writers. For a stale lock, verify the recorded process
is terminal and inspect the unfinished capture before manual recovery. Never
restart solely because a status read timed out. Repeated replay/validation of the
same source and code is deterministic; code, protocol, source hashes and runtime
versions identify experiments. Training arrays do not enter API results or cloud
tables, and no retired dataset is restored.

## Storage and backup requirements

The [storage policy](supabase-storage-policy.md) applies. No Supabase tables,
migrations, bulk writes or cloud retention changes are introduced. Databento
imports still use the existing cache builder and SQLite-only guard.

Defaults: 64 MiB per CLI capture, 250,000 events, 60 seconds; hard bounds 2 GiB,
5 million events and 8 hours. Rotate raw files at 16 MiB. The depth directory has
a 5 GiB allocation budget and requires another 1 GiB free-space reserve before
growth; derived datasets and experiment writes also check capacity. Stop when a
completed capture is older than 30 days pending reviewed archival. **No automatic
deletion occurs.** These are engineering limits, not exchange retention rights.

At 550 bytes/record and 22,500 seconds/session, 50/100/1,000 events per second imply
about 0.62/1.24/12.38 GB of uncompressed raw data per session, before derived files
and backups. The observed Level 1 probe rate is not an estimate of future Level 2
traffic. A full-depth feed may exhaust a capture bound early; the tool must stop,
not silently raise it. Plan storage for 200 independent paired sessions and
separate archives before attempting a large validation run.

Preserve original segments and manifests. Verify SHA-256 after copying to an
authorized off-device destination, record backup verification, and keep at least
one recoverable copy before any user-authorized removal. Git and Supabase backups
exclude these files. An off-device destination has not been configured or verified
by this task. Local retention review does not authorize deleting user/audit data.

## Dry Run integration

After the existing completed Dry Run decision, the service reads a bounded latest
state and optional paired model artifact from the local owner/contract/subscription
scope. It does not open a feed. Scope, provenance, size, timestamps, model cutoff,
seven-day model age, numerical domain and registered protocol are checked.
Missing/stale Level 2 abstains; there is no silently validated fallback. The
existing candle strategy remains separately authoritative.

The UI shows observed Level 1 versus verified multi-level capability, synchronization,
age and level counts; model version/horizon; uncalibrated probabilities, expected
net payoff, costs and uncertainty; paired forecast differences versus Level 1;
and specific abstention/proposal reasons. A difference between fitted models is
not labeled a causal contribution. All responses retain `action=NO_TRADE` and
`routing_allowed=false`. Live evaluations do not call the loader. Fitted experiment
files are not automatically installed. No current dataset can supply an eligible
artifact. Account classification, risk, reconciliation and emergency controls are
unchanged.
