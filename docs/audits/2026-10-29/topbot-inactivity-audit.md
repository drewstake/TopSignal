# TopBot inactivity audit

**Verdict: expected strategy abstention explains the recorded absence of trades, with a likely modeling limitation. There is also a confirmed downstream account blocker, a session-boundary defect, and a mismatch between the forecast horizon and simulated execution.** No arithmetic, direction, unit-conversion, duplicate-cost, or residual 25-point-cap defect was found in the replayed current forecasts. This is not a clean bill of health for the complete trading system.

TopBot has not found an expected return large enough to enter. For the current artifact, **none of 67 forecasts had either BUY or SELL expected net above $1 even before uncertainty**. All 67 therefore fail the existing rule regardless of the uncertainty deduction. The best current entry score was **−$6.8728**, versus a strict requirement to exceed **+$1**. Separately, the saved account is **HIDDEN**: even a qualifying signal would currently fail execution-risk admission before reaching the paper account.

This investigation made no production changes, deployments, service restarts, model fits or replacements, threshold/risk changes, execution enablement, or test-order submissions. All diagnostics and datasets are local files. The report is the only addition to repository documentation; pre-existing workspace changes were preserved.

## Evidence scope and running system

Times in narrative/tables are **Eastern daylight time, UTC−04:00**, unless explicitly marked UTC. The principal evidence was acquired at approximately **October 29, 10:58**, with the ledger ending at the **10:55 candle close**. A separate read-only account/broker check completed at **11:02:03**. These are individually consistent snapshots, not a cross-file/database atomic transaction.

The supplied 10:54 context was independently reproduced by filtering the ledger: **1,389 evaluations, 84 numerical forecasts, all 84 rejected for edge**. The additional 10:55 evaluation brings the audit snapshot to **1,390 evaluations and 85 forecasts**.

| Item | Verified evidence |
| --- | --- |
| Host and active release | Scheduled task resolves to `C:/ProgramData/TopSignal/releases/20261028-archive-15` on peteamdpc |
| Service identity | `TopSignal Personal Device`, Limited principal `TopSignalSvc`; actual backend PID 18492 owned by `DESKTOP-6EUPDU1\TopSignalSvc`; one port-8000 listener |
| Process start | October 28, 13:35:05; command points to that release |
| Package integrity | All **871** manifest files match on the host; the local replay copy also matches all 871 |
| Health | `/health`, `/health/worker?require_enabled=true`, `/ready`: healthy/running/ready |
| Active bot | Configuration **32**, run **9**, enabled, continuous, `dry_run`, MNQ `CON.F.US.MNQ.Z26`, 5-minute candles, 25-bar acquisition |
| Actual strategy | `topbot_adaptive` dispatched to `mnq_bayesian_payoff_v1` / `bayesian_cells_v1` |
| Gates | Unvalidated observation and uncapped stops true; both live-execution flags false; worker/archive/independent collection enabled |
| Risk settings | One MNQ; $250 daily loss budget; 30 entries/day; 300-second cooldown; model freshness 120 seconds, separately from configuration's 600-second delivery gate |
| Heartbeat | 10:57:55 in first DB snapshot; 11:01:59 in supplementary snapshot; evaluations advanced from 10:55 to 11:00 |
| Paper account | Zero trades, zero net change, no position, no last entry, empty daily counters, no blocked-path latch |
| Application/broker records | Zero bot order attempts and risk events; zero journal trades for the correctly resolved local account; broker read-only searches: zero orders, open orders, open positions, or fills since run start |
| Saved account | Internal account 1565 / external account 26507139: **HIDDEN**, `can_trade=false`, `provider_simulated=true` |

The raw database also contains legacy `strategy_enabled=false`, `desired_state=stopped`, and old strategy-name/slug values. These are not the current worker's arming/dispatch fields. The worker uses `enabled`, run status, durable continuous/dry-run authorization and `strategy_params.revision`; the actual forecasts confirm mathematical dispatch. Those legacy fields do not explain these HOLDs. See [worker arming conditions](C:/Users/drews/.ssh/topbot-archive-20261028/release-archive15/backend/app/bot_worker.py:1432).

The current artifact is unvalidated and has no live-routing authorization:

| Identity | SHA-256 |
| --- | --- |
| Canonical artifact digest | `c3003d754ac3ffb38cdaac95bf06e652d0f7b824c8f5e21ef46828635023de3b` |
| Artifact file bytes | `ab99a8c9a0c5e3bd0e0835b9ae21b0e663c688c93e151681835274c841f789cf` |
| Fitted model payload | `aecf754bac84611afde9f332eb9a3717ab928f6f32c31e6ffbb8153e034eab67` |
| Bound implementation | `aad73ab812f4d62193acb0e6cd9bb9315e22c210de7d7389ca67431fd97c0fd4` |

It was created October 28 at 11:13:36. Metadata lists 240 complete sessions from November 6, 2025 through October 23, 2026. The last-4,096-path truncation leaves **171 represented training dates**, beginning February 20, 2026 at 11:45, with final labels through **October 23 at 15:45**. At the last audited forecast its age is 5.799 days. Declared window length, actual represented dates, and effective weighted days are different quantities.

Repository HEAD is `6c8d778` (`Allow uncapped volatility stops in TopBot dry-run observation`). The deployed package is an immutable release assembled separately, not an exact checkout of HEAD. The inspected mathematical adapter, forward ledger, uncertainty calculation logic, observation loader and session policy match the relevant workspace logic. Differences include the repository's later Databento builder/reader changes and a reconciliation-helper refactor in `bot_service.py`; two Git versions also differ in error-message characters. Diagnostics used **the manifest-verified deployed copy**, not substituted workspace code. Detailed comparison is in `extra.json`. The live model's implementation binding matches that deployed copy.

## Findings ranked by impact

1. **High explanatory impact — the fitted model has no qualifying edge in this sample.** Current BUY expected net ranges −$6.5622 to −$0.8274. SELL ranges −$7.6590 to +$0.2902. Two SELL means are positive, but neither reaches $1. All scores fail before execution/paper gates. The unchanged artifact also qualifies zero of 233 post-label forward observations and zero of 4,096 training-feature resubstitution queries. This is evidence of a weak edge under this model/payoff rule, not proof that the market has no tradable opportunities. [Forecast rule](C:/Users/drews/.ssh/topbot-archive-20261028/release-archive15/backend/app/services/probabilistic_strategy.py:358).

2. **High operational impact — a qualifying signal is blocked by the current HIDDEN account.** `evaluate_risk` requires `account_state == ACTIVE` even in dry-run. An isolated fixture using the production configuration and saved HIDDEN state produced `account_not_active`, no order attempt, no paper position, and transitioned the fixture run to blocked/configuration to disabled. This gate is not evaluated for HOLDs, so the real ledger's empty risk-code lists do not establish readiness to admit a trade. The corresponding ACTIVE-account fixture reaches paper admission. This is a confirmed configuration/workflow blocker; whether observation mode should depend on broker-account activity is a product-policy decision, not permission to bypass the gate. [Risk gate](C:/Users/drews/.ssh/topbot-archive-20261028/release-archive15/backend/app/services/bot_risk.py:62), [BUY/SELL-only risk branch](C:/Users/drews/.ssh/topbot-archive-20261028/release-archive15/backend/app/services/bot_service.py:1496).

3. **Medium modeling impact — forecast and paper execution estimate different exposures.** Training labels use the next observed open at the decision-close timestamp and three full 5-minute bars. Paper queues the first open strictly after recording, retaining the original close-plus-15-minute deadline. All 67 current forecasts were recorded 10.28–38.88 seconds after the close, so every hypothetical paper entry would occur five minutes later and have **10 minutes**, not 15, of exposure. This is explicitly documented conservative simulation behavior, but the entry score remains an estimate for the earlier 15-minute exposure. It is a confirmed target/execution mismatch, not a delayed-entry gate failure in these observations. [Training paths](C:/Users/drews/.ssh/topbot-archive-20261028/release-archive15/backend/app/services/probabilistic_strategy.py:176), [paper entry/deadline](C:/Users/drews/.ssh/topbot-archive-20261028/release-archive15/backend/app/services/forward_results.py:134).

4. **Medium evidence impact — baseline comparisons do not establish shared execution eligibility on HOLDs.** Directional baselines reuse `risk_codes`; those are empty when TopBot HOLDs because its execution-risk evaluation never ran. Baselines then synthesize `dry_run_attempt` and can admit paper trades without checking the HIDDEN account. The current cohort has 25 closed always-long and 20 closed always-short simulated trades, versus zero TopBot trades. They are separate hypothetical accounts, not missed TopBot fills. Their stated shared-risk comparability is incomplete for gates not evaluated on HOLD. Historical account-state changes were not frozen with every decision, so this audit does not retroactively assign HIDDEN to every baseline entry. [Comparison admission](C:/Users/drews/.ssh/topbot-archive-20261028/release-archive15/backend/app/services/forward_evaluation.py:175).

5. **Low impact on total inactivity — the inclusive 15:30 endpoint is effectively unreachable.** `entry_boundary_reason(15:30:00)` permits entry, while one microsecond later rejects it. The strategy checks evaluation wall time before extracting the decision-close time, and the risk adapter repeats the wall-time check. All five recorded 15:30 closes on October 22, 23, 26, 27 and 28 are `outside_research_session`. Training includes that endpoint. This removes one of 72 intended daily research opportunities. Fixed-current-model diagnostics at October 26–28 15:30 still HOLD, so it does not explain zero trades in that checked subset. [Session boundary](C:/Users/drews/.ssh/topbot-archive-20261028/release-archive15/backend/app/services/topbot_session.py:20), [adapter boundary call](C:/Users/drews/.ssh/topbot-archive-20261028/release-archive15/backend/app/services/topbot_mathematical.py:74).

6. **Low direct trading impact, material collection gap — optional quotes have been lost.** Logs from October 28 11:20 through the supplemental check contain **131** `optional_quote_batch_lost` OperationalError events. The queue removes these batches before writing and does not retry them; the exception type alone does not prove the underlying SQLite cause. There are also two collection-provider errors, one broker-recovery-provider error, and three overnight worker network failures. The worker recovered, exact decision candles remain available after archive startup, and the independent minute-coverage check found no unresolved gaps. Quote completeness and measured execution-cost/latency claims remain unsupported. [Optional quote writer](C:/Users/drews/.ssh/topbot-archive-20261028/release-archive15/backend/app/services/market_archive_events.py:132).

## End-to-end reconstruction

The archive contains **271 confirmed decisions**, each with 25 ordered inputs; no unconfirmed decision was found in this snapshot. Content hashes and original input receipts/revisions were checked. **54 of the 67 current-model forecasts** have exact archived inputs; all 54 reproduce the recorded numerical fields with maximum absolute difference **2.94 × 10⁻¹⁴**. Their original revision references also match the committed application decisions. The first 13 current forecasts predate exact-input archival.

Four decisions were additionally verified using independent scalar OHLC payoff pricing and separately calculated features, weights, clustered variance, Student-t multiplier and scores:

| Decision and candle close ET | Stop / target points | BUY net / uncertainty / score USD | SELL net / uncertainty / score USD | First failing gate |
| --- | ---: | ---: | ---: | --- |
| 2005 — Oct 28 12:50, closest current forecast | 21 / 31.5 | −2.8752 / 3.9976 / **−6.8728** | −3.5724 / 3.7914 / −7.3639 | Strict net-edge rule |
| 2268 — Oct 29 10:45 | 106 / 159 | −2.0728 / 17.2395 / −19.3122 | −7.5735 / 17.2753 / −24.8488 | Strict net-edge rule |
| 2269 — Oct 29 10:50 | 113.5 / 170.25 | −1.5600 / 17.3727 / −18.9327 | −4.1628 / 15.8469 / −20.0097 | Strict net-edge rule |
| 2270 — Oct 29 10:55 | 114.25 / 171.5 | −1.2429 / 17.4460 / −18.6889 | −4.1158 / 15.9350 / −20.0507 | Strict net-edge rule |

For each: 25 provider-native bars → final 21 valid, contiguous closed bars → four features → the preserved original model → net payoff distributions with costs → uncertainty deduction → `research_action=NO_TRADE` → strategy `HOLD` → execution branch skipped → ledger `edge_below_threshold` and identical paper disposition. No paper entry was queued. Support, model integrity, observation policy, freshness and bracket checks preceding the edge gate passed.

For decision 2269, the archived inputs run **08:45–10:45 bar opens**, with the feature window beginning 09:05. The last bar was fetched at **10:50:05.434838**, evaluated at **10:50:05.913660**, and recorded at **10:50:13.094280**. RMS price change sigma is **65.5101375743 points**. Features in original order are `[-1.5989891623, -1.3109551548, -0.1944010890, 0.1195035598]`. The hypothetical paper opportunity is 10:55, with exit deadline 11:05. Original input digest is `d7b260bb19d5e9be1f3cedb8752e1be1439995c5e069a88561f3ad3c2ca672ac`. The other three complete input digests, receipt times and scalar calculations are retained in `summary.json`.

Across **all model versions**, the closest recorded score was −$6.1533 on October 27 at 15:20 under older artifact `14efbb7a…`. That predates exact-input archival. Its ledger forecast can be inspected, but an exact-original-input replay cannot be certified. Decision 2005 is the closest forecast for the **current** artifact and was reconstructed exactly.

## Data, features and statistical support

The active delivery is consistently MNQ December 2026 (`Z26`) throughout this snapshot. No archive roll event, mixed-delivery input or `contract_rolled` rejection was found. Historical samples retain delivery identity across H6/M6/U6/Z6; contiguous-window checks reject a feature/label path spanning different deliveries. The historical roll policy is previous-completed-session volume. The current configuration is an explicit delivery; a future delivery change blocks the run rather than silently substituting history. This audit verifies observed identity consistency, not provider-wide volume leadership of every available contract.

All **6,775 archived input occurrences** matched independently aggregated native 1-minute OHLCV exactly. There were no duplicate/out-of-order input timestamps, mixed contracts or partial bars in those 271 decisions. The local archive has **9,296 native minutes**, from October 20 at 18:00 ET through October 29 at 10:55 ET. Independent expected-minute enumeration found zero missing minutes through the **10:56 ET coverage cutoff**, excluding maintenance/weekend closures. Freshness is delivery-after-bar-close, not age since bar open. Current model evaluation delays were 5.65–32.64 seconds; median recording delay was 17.60 seconds. Median evaluation duration was 7.31 seconds. All current forecasts passed the 120-second rule.

The model uses UTC interval-start candle timestamps, derives decision time by adding five minutes, and converts session boundaries to America/New_York. Training and inference call the same feature function:

1. Last close-to-close change divided by RMS of 20 close changes.
2. Sum of the last three changes divided by `sigma × sqrt(3)`.
3. Log of last-five-change RMS divided by the 20-change RMS.
4. Log of latest volume divided by mean volume of the preceding 20 bars.

The Bayesian cell model uses **only the signs of features 2 and 3** for conditioning. It is a four-cell empirical path mixture with 20 paths' worth of pooled shrinkage, not a continuously varying predictor of every feature. Features 1 and 4 are computed/validated but do not affect this model's cell weights. No inference-time scaler or feature permutation was found. Kernel-model standardization is irrelevant to this selected artifact. [Feature/weight code](C:/Users/drews/.ssh/topbot-archive-20261028/release-archive15/backend/app/services/probabilistic_strategy.py:138).

The four cells contain 780, 1,329, 1,206 and 781 paths. Current forecasts have **147.15–158.01 effective weighted days**, comfortably exceeding 20; training count 4,096 exceeds 300. These support tests are sample-count tests, not proof that the conditional mean is predictive or calibrated.

Of 54 exact current input vectors, 53 are within all four marginal training ranges. The October 29 09:35 volume feature is **2.3755**, above the training maximum **1.9424**. This unused-by-cell-weight feature does not cause the HOLD, but shows that broad count-based support is not an out-of-distribution detector. All 54 volatility levels are within the training range; current forecast stops range 20.75–114.25 points, versus training 9.25–197.75. Marginal ranges do not establish multivariate or regime support.

A deterministic sample of **65 original training windows**, including endpoints and maximum stop, was regenerated from the existing filesystem Databento cache. Features, normalized paths and stops matched exactly, with no missing minutes in those checked windows. This was dataset verification, not refitting. In the October 20–23 source/provider overlap, **818/828 five-minute bars** match OHLCV exactly; ten differ in volume, four in open, one in high and three in close. These are observed feed discrepancies, not assumed equivalence; the audit does not attribute their cause. They do not invalidate the exact forward-input replay.

`live=false` selects a subscription environment; it does not prove delayed data. ProjectX documents both that flag and `includePartialBar`. Feed latency relative to the exchange is still unverified. [ProjectX Retrieve Bars](https://gateway.docs.projectx.com/docs/api-reference/market-data/retrieve-bars/).

## Payoff, cost and uncertainty calculations

One MNQ point is **$2**, and a 0.25-point tick is **$0.50**, consistent with the exchange specification. BUY and SELL reverse signed price coordinates correctly; high/low roles reverse for SELL. [CME MNQ specifications](https://www.cmegroup.com/markets/equities/nasdaq/micro-e-mini-nasdaq-100.contractSpecs.html).

Stop distance is `max(4, ceil(sigma × sqrt(3) / 0.25) × 0.25)`. The target is `ceil(1.5 × stop / 0.25) × 0.25`, so rounding can make its ratio slightly above 1.5. Paths are scaled by **unrounded** `sigma × sqrt(3)`, not rounded stop distance. Both directions use one contract. Opening gaps are evaluated before intrabar ambiguity; stop takes precedence when a bar could hit both levels; adverse stop gaps can lose more than the bracket. A target gap earns only the target price. Time exits use the final included bar close.

Assumed commission is $0.61 per side. Per-side adverse price movement is `ceil(0.5 spread ticks + 1 slippage tick + 0.5 latency tick) × 0.25 = 0.5 points`, worth $1. Round-trip time-exit costs are therefore **$1.22 fees + $2 adverse price movement = $3.22**. The price-path engine embeds the entry/exit haircuts and subtracts fees once. The separate displayed cost breakdown is descriptive and is not subtracted again by the forecast or strategy. The paper simulator uses the same price/cost convention. Independent scalar replay and the flat-price paper fixture agree.

Stop-risk admission uses `2 × stop + $2.22`: the stop is anchored to the adversely filled entry, so this adds exit haircut and both fees; adding the entry haircut again would double count it. With a flat daily account, admissible quarter-point stops must be **below 123.89 points**, hence at most 123.75, to satisfy the strict remaining-$250 rule. None of the 67 current forecast stops breaches that dollar budget. Removing a fixed point cap correctly leaves this dynamic dollar limit intact.

The uncertainty calculation is as follows. For normalized scenario weights `w_i` and net dollar payoffs `y_i`:

```
mean = sum(w_i * y_i)
day_weight[d] = sum(w_i in day d)
effective_days = 1 / sum(day_weight[d]^2)
u[d] = sum(w_i * (y_i - mean) in day d)
V_cluster = sum(u[d]^2)
V_HAC = V_cluster + 2 * sum((1 - lag/5) * sum(u[d] * u[d-lag]), lag=1..4)
SE = sqrt(max(V_cluster, V_HAC, 0) * effective_days/(effective_days - 1))
critical = StudentT_quantile(1 - 0.01/8, df=effective_days - 1)
uncertainty = critical * SE + $1 cost-uncertainty buffer
entry_score = mean - uncertainty
enter only if supported and max(BUY score, SELL score) > $1
```

This estimates **uncertainty of a weighted mean using day clusters**, not individual-outcome standard deviation. It accounts for correlated paths within a date through aggregation, and up to four represented-day lags through Bartlett-weighted HAC terms. HAC is not allowed to lower the cluster variance. Kish day count supplies a small-cluster correction and t degrees of freedom. The code does not divide by sample size a second time because the scenario weights already carry the averaging factors.

At October 29 10:50, BUY outcome standard deviation is **$187.33**, but its mean SE is only **$5.3281**. Effective days are **158.0114**; matched paths are **1,206** and path Kish ESS is **1,234.18**. The nominal critical multiplier is **3.07289**, producing `3.07289 × 5.32810 + 1 = $17.37268`. SELL SE is **$4.83158** and its deduction is **$15.84691**. BUY cluster variance is 28.2090 and HAC variance 26.8198, so the cluster floor applies. The independent-path comparison SE is $5.3322 for BUY: the production penalty is not inflated by mistakenly treating $187 outcome dispersion as mean uncertainty.

The size is consistent with wide dollar payoffs and a roughly three-SE deduction, not automatically a bug. Nevertheless, the nominal correction is not proof of calibrated confidence: fixed empirical weights, shrinkage/model bias, regime changes, cross-feed differences, delayed-entry mismatch and dependence beyond four lags are not fully measured. Eight-test Bonferroni is the frozen specification; it does not establish familywise coverage over every repeated forward decision. The model is explicitly unvalidated. The $1 cost-uncertainty buffer and separate $1 required residual edge are two intentional buffers, not the same deduction applied twice.

## Cap removal and quantitative explanation

The current uncapped flag, artifact metadata/model policy, feature construction, forecast validation, empirical-pool shadow and paper admission all use `observation_volatility_uncapped_v1`. Training contains **3,460 of 4,096 paths above 25 points**. Current forecasts up to 114.25 points confirm the ceiling is gone from this observation route. The 25-point check remains in the registered validated/live policy and legacy volatility-counterfactual code, as intended. The observation loader rejects an old capped artifact with the uncapped setting; isolated regression tests verify that boundary.

Before the current artifact's first forecast at **October 28 11:20**, the ledger has 1,119 records. After that point it has 271: **67 edge HOLDs and 204 session HOLDs, zero volatility-cap rejections**. The older 287 cap rejections must not be attributed to the new policy. This change also updated training labels from July to October; it is not an isolated cap-only performance experiment.

The following uses **exchange trading dates**, with sessions beginning at 18:00 ET the preceding evening. October 22 begins partway through collection; October 29 is unfinished. “Edge” is also the numerical-forecast count.

| Exchange session | Evaluations | Edge / forecasts | Old cap | Session restriction | Other rejection |
| --- | ---: | ---: | ---: | ---: | ---: |
| Oct 22 | 93 | 3 | 68 | 22 | 0 |
| Oct 23 | 276 | 0 | 71 | 205 | 0 |
| Oct 26 | 267 | 0 | 71 | 196 | 0 |
| Oct 27 | 276 | 13 | 58 | 205 | 0 |
| Oct 28 | 276 | 52 | 19 | 205 | 0 |
| Oct 29 through 10:55 | 202 | 17 | 0 | 185 | 0 |
| **Total** | **1,390** | **85** | **287** | **1,018** | **0** |

There are no ledger `stale_candles`, `inputs_or_model_unavailable`, `insufficient_support`, qualified signals or execution-risk rejections in this snapshot. Provider failures before commitment are outside this denominator; their diagnostics must be retained separately. Repeated maintenance-period application evaluations do not create new ledger opportunities because the contract/close identity is deduplicated.

Artifact cohorts remain separate:

| Artifact prefix / policy | Forecast period ET | Forecasts | Paths / final label |
| --- | --- | ---: | --- |
| `6e7d81a7` / capped | Oct 22 09:35–09:45 | 3 | 1,648 / Jul 10 15:45, per committed forecast metadata |
| `14efbb7a` / capped | Oct 27 14:25–Oct 28 09:40 | 15 | 1,648 / Jul 10 15:45 |
| `c3003d75` / uncapped | Oct 28 11:20–Oct 29 10:55 | 67 | 4,096 / Oct 23 15:45 |

Each distribution below is **minimum / median / maximum**, in dollars. Full p10/p90/mean distributions and individual records are in `summary.json` and `forecast-distributions.csv`.

| Artifact | Side | Expected net before uncertainty | Uncertainty deduction | Final entry score | Net > 0 | Net > 1 | Score > 1 |
| --- | --- | --- | --- | --- | ---: | ---: | ---: |
| `6e7d81a7` | BUY | 1.652 / 1.860 / 1.864 | 8.944 / 8.950 / 9.013 | −7.298 / −7.149 / −7.083 | 3 | 3 | 0 |
| `6e7d81a7` | SELL | −6.688 / −6.615 / −6.608 | 8.850 / 8.852 / 8.920 | −15.608 / −15.465 / −15.460 | 0 | 0 | 0 |
| `14efbb7a` | BUY | −6.776 / −5.412 / 0.574 | 5.489 / 6.483 / 8.649 | −15.425 / −11.171 / −6.153 | 3 | 0 | 0 |
| `14efbb7a` | SELL | −5.700 / −2.723 / −0.921 | 5.351 / 6.375 / 8.676 | −12.537 / −8.959 / −7.118 | 0 | 0 | 0 |
| `c3003d75` | BUY | −6.562 / −2.772 / −0.827 | 3.998 / 7.156 / 23.671 | −27.272 / −11.427 / −6.873 | 0 | 0 | 0 |
| `c3003d75` | SELL | −7.659 / −3.809 / 0.290 | 3.791 / 7.259 / 22.281 | −27.398 / −10.722 / −7.364 | 2 | 0 | 0 |

There are **no qualifying opportunities** to rank. The ten closest rejected current-model opportunities are below; each is the better side for a distinct forecast, ranked by score. Every one first fails net edge, while its timing, support, bracket and empty-paper-account budget would otherwise pass. **The current HIDDEN account remains an additional execution blocker for every row if evaluated under today's saved account state.** Its exact historical state at each row is not asserted.

| Oct 28 close ET | Side | Stop | Expected net | Deduction | Score | Gap to +$1 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 12:50 | BUY | 21.00 | −2.875 | 3.998 | −6.873 | 7.873 |
| 12:25 | BUY | 23.75 | −2.824 | 4.400 | −7.224 | 8.224 |
| 12:20 | BUY | 23.75 | −2.835 | 4.399 | −7.234 | 8.234 |
| 12:15 | BUY | 25.00 | −2.783 | 4.563 | −7.346 | 8.346 |
| 12:45 | SELL | 20.75 | −2.428 | 4.947 | −7.376 | 8.376 |
| 11:55 | BUY | 25.00 | −2.782 | 4.615 | −7.397 | 8.397 |
| 12:00 | BUY | 25.25 | −2.813 | 4.609 | −7.422 | 8.422 |
| 11:50 | BUY | 25.75 | −2.752 | 4.675 | −7.427 | 8.427 |
| 12:35 | BUY | 24.00 | −2.892 | 4.656 | −7.548 | 8.548 |
| 12:30 | BUY | 24.25 | −2.902 | 4.691 | −7.594 | 8.594 |

## Isolated admission and unchanged-rule diagnostics

**66 targeted tests passed against the deployed code.** The custom fixtures deny socket connections, use an in-memory application database and disposable local paper ledgers, preserve existing risk settings, and inject an explicitly synthetic qualifying forecast. They do not alter or train an artifact.

The fixture follows the actual mathematical adapter → application execution-risk branch → dry-run attempt → committed-record preparation → paper ledger. With HIDDEN account state it produces only `account_not_active`, blocks the fixture run and records an execution-risk paper block. With a synthetic ACTIVE account it produces a dry-run attempt, queues the 10:55 open, respects the original 11:05 deadline, and records one time-exit simulated trade with **−$3.22** on a flat-price path. Repeated ingest/commit calls produce no duplicate position, entry reservation, attempt or trade. The recording client receives **zero place/cancel/close calls**. This demonstrates reachable software admission under existing conditions, not that the current fitted model supplies a qualifying forecast.

The retained regression checks cover uncapped wide stops, capped-artifact rejection, conflicting live gates, support/edge gates, cooldown, one position, daily entries, dollar budget, missing paths, ambiguous stop-before-target ordering, costs and duplicate persistence. The actual paper snapshot has no stale cooldown, accumulated entries or unknown-path latch. No evidence of a ledger-state defect causing its zero trades was found.

For an additional fixed-model diagnostic, the unchanged current artifact was applied to **233** complete, archived 1-minute-derived input windows after its final training label: 72 each on October 26–28 and 17 on October 29. These are post-training observations, but **not a certified untouched test**: some were previously observed and the artifact was installed after part of this interval. They yield **zero qualifying forecasts**, zero BUY positive means, two SELL positive means, no mean above $1, and maximum score **−$6.4705**. No input was tuned or used to select a threshold. Native decision inputs and derived diagnostic windows are explicitly distinguished.

The separate training-set diagnostic queries all **4,096 saved feature vectors** against their own fitted paths. It shifts query time metadata only to satisfy the inference cutoff check; the fitted artifact is unchanged. This is explicitly **resubstitution**, not a backtest. It yields 35 queries with some positive mean, 13 with mean above $1, **zero scores above $1**, and maximum score **−$5.4485**. This reinforces the weak-edge interpretation but supplies no independent performance estimate.

Removing uncertainty hypothetically would still produce **zero entries for the current 67 forward forecasts at the unchanged $1 threshold**. That is arithmetic on the existing sample, not a proposed setting change. No threshold search or optimization was performed. Rejected-signal outcomes, baseline accounts and fixtures are not actual TopBot trades.

## Minimal proposed changes and validation

No proposed change below has been implemented.

| Priority | Minimal proposal | Validation before any release |
| --- | --- | --- |
| 1 | Expose execution eligibility independently of signal action, including the HIDDEN-account block; explicitly decide whether this observation product is intended to require an active broker account | Read-only readiness checks for HOLD and BUY/SELL; HIDDEN/ACTIVE parity; no config mutation from a readiness query; retain live restrictions. Do not automatically unhide accounts or bypass safeguards |
| 2 | Use a shared, read-only eligibility snapshot for TopBot and comparison accounts instead of treating empty HOLD risk codes as proof of eligibility | A HOLD on HIDDEN account must not yield baselines labeled as sharing passed execution gates; explicit eligible/ineligible/unknown states; preserve all prior comparison records |
| 3 | Align the economic quantity being forecast with the actual simulated entry and original deadline, or clearly separate that forecast from executable-entry estimates | Pre-register delayed-entry semantics; verify entry offset, exposure duration, costs and deadlines with frozen data before considering any new model. Existing `delay_bars=1` research stress uses three delayed bars and is not automatically equivalent to the present two-bar paper exposure |
| 4 | Base research-window eligibility on the completed candle's close, retaining real-time freshness and exchange/flat-deadline checks | 15:30 close evaluated at +1 microsecond/+10/+119 seconds, stale +121 seconds, 15:35 close, DST and early-close cases; match training and runtime eligible closes |
| 5 | Record enough bounded error detail to diagnose optional-quote loss and consider bounded retry for transient write failures | Isolated lock/failure/queue tests; count received, lost and persisted quotes separately; demonstrate retry deduplication without trading-loop delays |
| Research only | Keep the fitted rule unchanged while documenting the four-cell model's lack of edge; any later model redesign needs a separately authorized, preregistered evaluation | Untouched provenance, chronological validation, realistic execution timing, calibration and dependence-aware uncertainty checks. Do not lower the threshold or penalty to manufacture entries |

## Evidence gaps and reproducibility

The result is firm for the observed first failing gates and arithmetic. It does not prove profitability, exchange-feed timeliness, real fills, or calibrated statistical confidence. There are no actual execution events with which to verify broker fill/fee/stop parity. The current sample spans one partial session plus part of another since uncapped installation, far below the documented forward evidence requirements. Model versions must remain separate.

Exact-input collection started after the first 13 current-model forecasts. Older capped forecasts, including the all-version closest score, lack the original archived inputs needed for exact replay. The earliest capped artifact binding was not recovered from the locations inspected; its label cutoff/path count were verified from committed decision metadata. Account eligibility history is also not captured with every HOLD. No later provider fetch is presented as an original decision input.

The minute archive is complete only within the verified time window and repository calendar. Sampled quotes are incomplete; missing quote batches cannot be reconstructed from minute bars. The audit sampled 65 training-source windows rather than certifying every source minute and every roll decision. Price/volume parity across feeds is high but not exact. Counts of committed evaluations exclude failed pre-commit provider calls.

Local private evidence is under [the audit directory](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/20261029-inactivity). It includes account identifiers and should not be committed or published. No private keys or provider credentials were exported.

| Evidence | Contents |
| --- | --- |
| [runtime.json](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/20261029-inactivity/runtime.json) and [production.json](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/20261029-inactivity/production.json) | Task/process identity, environment allowlist, health, manifest hashes, configuration/runs/decisions, current original artifact |
| [snapshot receipt](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/20261029-inactivity/snapshot-receipt.json) | SHA-256/size of online SQLite backups pulled through read-only source connections into memory, then written locally; all three integrity checks pass |
| [summary.json](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/20261029-inactivity/summary.json) and [forecast CSV](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/20261029-inactivity/forecast-distributions.csv) | Counts, cohort distributions, ten nearest forecasts, exact/scalar replays, uncertainty components, timing, archive coverage |
| [extra.json](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/20261029-inactivity/extra.json) | Source comparison, original-input/1m equality, boundary reproduction, fixed-model diagnostics, training-source and cross-feed comparisons |
| [supplement.json](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/20261029-inactivity/supplement.json) | Correctly resolved account state, advancing heartbeat, read-only broker counts, error log counts/timestamps |
| [admission fixtures](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/20261029-inactivity/test_admission_audit.py) and [test results](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/20261029-inactivity/test-output-2.txt) | Isolated HIDDEN/ACTIVE branch proof, durable exactly-once paper trade and boundary reproduction; 66 passes |

Reproduce the offline calculations from this repository root with `backend/.venv/Scripts/python.exe -B` followed by `backend/storage/databento/audits/20261029-inactivity/analyze.py`, `extra.py`, or `run_tests.py`. These read the retained snapshots and the manifest-verified local release copy at `C:/Users/drews/.ssh/topbot-archive-20261028/release-archive15`. `extra.py` also reads the existing Databento cache without rebuilding it. Test temporary roots are new short local paths under `.ssh` to avoid Windows path-length limits; the successful run's root is recorded in its output. The collection scripts are separate and should only be used to create a newly named evidence set, not overwrite this audit's original snapshots.
