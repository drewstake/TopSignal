> Historical result: the EMA/VWAP TopBot v5 incumbent has since been removed. TopBot Mathematical is the current preset; these diagnostic results do not validate it.

# MNQ Level 2 result and API capability assessment — 22 September 2026

**Decision: do not promote a depth model.** The engineering integration is
implemented, but neither Level 2 entitlement nor predictive incremental value has
been established. The v5 default has since been removed; current TopBot Mathematical remains subject to protocol v3 evidence gates. Depth research cannot route
orders; no Live Run, active run, account risk setting or emergency control was
changed. No subscription, billing upgrade or historical data was purchased.

The [research and mathematical specification](topbot-depth-research.md) covers the
primary literature, assumptions, feature formulas, candidate comparison, commands,
storage and backup requirements. The [registered protocol](topbot-depth-protocol-v1.json)
preserves the original 200-session minimum. Machine-readable evidence is in the
[API assessment](topbot-depth-capabilities-2026-09-22.json) and
[deterministic validation report](topbot-depth-results-2026-09-22.json).

## Actual observations

Two dedicated read-only market probes used the already-configured environment
credential profile and simulated **data** subscription; that flag is not a bot
execution mode. Authentication and all three quote/trade/depth subscription
acknowledgements succeeded. Contract lookup selected `CON.F.US.MNQ.Z26`, with
the expected 0.25-point/$0.50 tick. No account/order/position endpoints were used.

The probes requested 45 and 30 seconds and took **75.64 seconds combined** including
setup/shutdown. They wrote **5,650 local records / 3,047,678 bytes**:

| Recorded kind | Count | Interpretation |
| --- | ---: | --- |
| GatewayQuote | 896 | Level 1 price observations |
| GatewayTrade | 1,207 | Prints; not guaranteed distinct from DOM Trade messages |
| GatewayDepth | 3,349 | Breakdown below; target name alone does not prove Level 2 |
| Received closed five-minute bars | 36 | Local point-in-time warmup, not a historical depth archive |
| Unrecognized/null payload markers | 156 | Invalid periods recorded; second probe preserves the raw null |
| Connect/disconnect/end controls | 6 | Local lifecycle events |

Depth types were **830 BestAsk, 820 BestBid, 1,693 Trade, two Reset, two Low and
two High**. There were **zero explicit Ask/Bid depth updates** (types 1/2), zero
reconstructable synchronized Level 2 seconds and zero paired Level 2 sessions.
The capture also contains 1,129 provider timestamps with years before 2000; these
are not accepted as fresh observations. Timestamp differences are local clock
observations, not exchange-to-device latency measurements.

This verifies **Level 1 delivery during the probes**. It does not prove that the
user has or lacks a paid subscription: billing was not inspected, and a successful
depth subscription acknowledgement did not result in explicit multi-level updates.
The exact next entitlement requirement is a bounded probe on these credentials
that receives valid explicit MNQ depth on both sides at multiple simultaneous
prices. A visible DOM in another interface is insufficient evidence.

The previous offline SQLite snapshot was read again with `mode=ro`. It still has
3,128 U26 five-minute rows, one partial row, and only **ten complete regular
sessions**, with the same candle fingerprint
`b5170166741d2d48ca91753af6c39aef2318e3692affef00a09df3130a07dfc8`.
At the initial audit, its observation groups were 127,956 quotes, 121,976 prints
with no explicit aggressor classification, and 68 resets, with no depth-update
history. During this task the file changed: a later read contained newer Z26
quotes/prints and no U26 observation rows. The final machine-readable report
reflects that later U26 audit, while the original audit remains in the earlier
immutable local experiment. This file is evidently being used as runtime storage,
not an immutable historical archive. Research reads use a read-only transaction
for consistency and do not perform that retention or insert observations. Neither
audit establishes cloud coverage. U26 candles were not joined to Z26 depth or
used to invent historical receipt times. Retired data was not restored.

## What documentation and probes establish

| Question | Evidence and remaining uncertainty |
| --- | --- |
| Subscription/API entitlement | Topstep distinguishes Level 1 from its paid multi-level DOM bundle. This credential profile delivered only BBO/prints in our probes. Paid billing status and full-depth API entitlement remain unverified. |
| MBP or MBO | ProjectX documents price-level aggregate volume, not individual order IDs/priority. Treat it as MBP-style data. CME-native MBO availability does not imply ProjectX provides it. |
| Levels, quantities, counts | Topstep describes 5–10 best levels for Level 2. We did not receive explicit multi-level data. `volume` and `currentVolume` are present; no order-count or individual-order field was observed. Current-volume differences are not assumed to be order events. |
| Snapshot/incremental semantics | Reset and side/type enums are documented. No complete-snapshot marker or authoritative completion rule is specified in the reviewed public API documentation. The live adapter remains unsynchronized. |
| Time and sequencing | Provider timestamp plus separately recorded local receive/monotonic times. No provider sequence appeared. Local record indices detect local capture loss only. |
| Frequency/conflation | Arrival counts are measured for two short overnight intervals. No published guaranteed update rate or nonconflation contract was established. Undetectable upstream loss remains possible. |
| Trade prints | GatewayTrade was received with Buy/Sell TradeLogType. Public wording does not explicitly establish aggressor-side semantics. Signed flow is therefore unavailable. DOM Trade events are not added to GatewayTrade volume. |
| Reconnect/resubscription | Official example resubscribes after reconnect. Dedicated collector does likewise with fresh state, bounded retries and periodic active-contract lookup. Real probes observed no outage or roll. |
| Rate limits | Official REST limits: 50 bar requests/30 seconds and 200 requests/60 seconds for other endpoints. The collector uses one bar and one contract lookup per minute while connected. Rate-limit rejection stops capture. No WebSocket throughput guarantee was found. |
| Historical depth | Documented historical retrieval supplies OHLCV bars, with a 20,000-bar request maximum. No supported historical depth replay endpoint was established in the reviewed API pages. A new subscription must not be assumed to backfill a book archive. |

Sources: [ProjectX real-time schema and subscriptions](https://gateway.docs.projectx.com/docs/realtime/),
[rate limits](https://gateway.docs.projectx.com/docs/getting-started/rate-limits/),
[historical bars](https://gateway.docs.projectx.com/docs/api-reference/market-data/retrieve-bars/),
[Topstep data comparison](https://help.topstep.com/en/articles/8284120-level-1-and-level-2-market-data),
[CME MBP/MBO](https://www.cmegroup.com/articles/faqs/market-by-order-mbo.html).
Unknown entries above are explicitly gaps in evidence, not invented provider guarantees.

Topstep documents API access separately from market-data upgrades and allows
supporting historical storage/research while distinguishing it from order
transmission. The public terms describe personal, noncommercial, nonpublic use.
This task keeps captured data private and local. A specific exchange-agreement
retention period, redistribution license or commercial research permission was
not verified; none is inferred from the 30-day engineering archive-review rule.
Before expanded use or redistribution, check the user's signed data agreement
and current provider terms. No agreement was accepted on the user's behalf.
[API access](https://help.topstep.com/en/articles/11187768-topstepx-api-access),
[current terms](https://www.topstep.com/terms-of-use).

## Implemented comparison and current result

| Use | Implemented comparison | Real market finding |
| --- | --- | --- |
| A. Trade quality | Existing Bayesian/kernel proposals, unfiltered vs Level 1 filter vs Level 2 filter; expected-net-utility gate and paired daily P&L | Not estimable: no synchronized paired Level 2 history |
| B. Execution | Regularized sweep-cost proxy models across candle/Level 1/Level 2 inputs; error and paired daily improvement | Not estimable; actual fill/queue uncertainty additionally unverified |
| C. Forecasting | Fixed ridge logistic model across three baselines and two feature-group ablations; Brier, log loss, reliability, expected payoff and utility | Not estimable; no evidence of calibrated probabilities or 15-minute alpha |

The development command refuses to fit with insufficient paired sessions. It
does not lower thresholds, inspect an untouched holdout, assign zero error to
missing data, or count unavailable forecasts as safe zero-loss trades. All
promotion gates remain false. The existing probabilistic models' prior small
diagnostic is still unvalidated; nothing in these probes changes that conclusion.

A separate 120-second **synthetic complete-book fixture** produces 119 sampled
observations, 90 with a full warmed feature window. Repeated replay is identical.
Three-second delay or dropping every twentieth event prevents usable fixture
features. Other fixture tests exercise model fitting, matched baselines,
ablations, filters, uncertainty, negative expectancy despite high win probability,
and known displayed-book sweep arithmetic. These are correctness checks, not
evidence of market profitability, calibrated confidence, realistic fills or a
verified ProjectX snapshot boundary.

## Verification and completion boundary

The offline backend suite passed **2,006 tests, with nine skips**. The skips are eight
disposable-PostgreSQL safety/concurrency checks and one optional workspace capture.
The offline guard reported zero external connection attempts with dotenv, workers
and live gates disabled. All **945 frontend tests** passed, as did the production
build and lint on the affected frontend files. Focused depth/API tests passed
**52 checks**. These are the recorded suite results during this task; unrelated
work continued in the shared workspace. The final frontend build also passed.

Two consecutive validations with identical code and inputs produced byte-identical
summary files. The delivered summary identifies experiment
`2daaa5aa0b2f0013324fa6e9a8573ad397a05d4215dd2bb529f7866bf2ecf25e`
and records code, protocol and source fingerprints. Later code or source changes
produce a different experiment identity; existing local experiment files are not
overwritten.

The tests cover exact feature/sweep values; zero deletion and absolute updates;
duplicates, late/out-of-order events, clock/future-time faults, stale/crossed books;
reset/disconnect/roll invalidation; tenant/data scope and model cutoff; corruption
and segment hashes; bounded capture and retention without deletion; null payload
capture and subscription acknowledgements; missing-feature paired cohorts;
unavailable confidence intervals; model serialization; synthetic-data rejection;
and API/UI serialization. A mocked SELL depth proposal and BUY candle proposal
leave the actual HOLD decision and broker call counts unchanged. Live mode does
not call either research loader.

The actual provider probe is the separate, authorized read-only integration
evidence. No active app run was started to obtain it. All raw/derived data is under
the approved local cache root, ignored by Git. The public results contain bounded
aggregates, no raw replay arrays, credentials, usernames or order records. No
cloud retention or database schema was changed. An off-device backup destination
has not been configured; the collector does not claim the captures are backed up.

| Requested deliverable | Implementation / evidence |
| --- | --- |
| Primary-source research, mathematical specification and candidate comparison | `topbot-depth-research.md`; five registered feature sets and three separate uses |
| Account-specific capability assessment | Two bounded real probes; `topbot-depth-capabilities-2026-09-22.json`; Level 1 observed, Level 2 entitlement and snapshot boundary unverified |
| Local collection and deterministic reconstruction/replay | `depth_capture.py`, `depth_research.py`, `research_depth_topbot.py`; raw manifests, corruption checks, bounded storage and explicit invalid periods |
| Causal features and model interface | `depth_research.py`, `depth_model.py`; finite, complete, fresh observations required; no invented queue or aggressor fields |
| Incremental-value validation | Registered protocol and `depth_validation.py`; paired baselines, ablations, chronological splits, serial-dependence intervals and stress tests; real-data fit blocked by insufficient coverage |
| Research / Dry Run integration | `depth_shadow.py`, typed API output and `BotDepthResearchPanel.tsx`; account-scoped, routing disabled, explicit missing/stale reasons |
| Verification and reproducibility | `test_depth_research.py`, bot explanation and UI tests; deterministic fixture replay and real-data validation reports |
| Storage, promotion and remaining evidence | Collection/backup instructions in the research report; no promotion, with requirements below |

Before depth can influence any trading model in production, the remaining evidence is:

1. Explicit multi-level MNQ delivery on the correct credential profile, plus
   verified snapshot completion, updates/deletions and resynchronization semantics.
   Clarify `volume`/`currentVolume`, sequencing/conflation, timestamp sentinel rules,
   aggressor semantics, and applicable retention rights with the provider.
2. At least **200 complete paired sessions**, including 60 truly unseen final
   sessions, sufficient eligible nonoverlapping paths and diverse regimes. Collect
   on the actual active delivery contract, with causal bar receipt times. No
   additional historical access or restoration is assumed.
3. Execute the registered chronological comparisons and all parent gates. An
   improvement in seconds-scale diagnostics cannot substitute for 15-minute
   probability/payoff evidence. It is acceptable for Level 2 to add no useful value.
4. Verify actual execution/slippage and intratrade risk, complete a frozen final
   test, and observe at least **20 forward Dry Run sessions**. A separately reviewed
   routing integration would still be required. Current implementation never
   promotes itself or changes live order behavior.
