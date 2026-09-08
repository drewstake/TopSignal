# Evaluation and market analysis

The panel answers five questions: what closed candles show, the supporting
evidence, contrary evidence, levels that merit another evaluation, and why the
recorded strategy/routing outcome holds, permits, or rejects an entry.
It does not estimate a probability of profit. No performance improvement has
been established by these explanation changes.

## Market description versus execution

The first view separates descriptive market evidence from the recorded bot
decision. Calculation definitions, source coverage, observation windows, and
timestamps are expandable. Scenario percentages, conviction labels, overlapping
quality/risk score cards, and the repeated collected-context summary are removed
from that view. Legacy numeric fields remain in the API for compatibility;
`score_definitions` explains their inputs, scale, window, and missing-data rules.

TopBot v5 still uses the preset in `backend/app/services/topbot_strategy.py`:
200 closed MNQ five-minute candles for warmup; EMA20 and its three-bar change;
a previous-candle EMA touch; a confirming close beyond that candle and on the
appropriate side of EMA20 and regular-session VWAP. Shorts additionally require
EMA20 below a falling EMA50. Its entry window is 09:30–15:45 New York time at
candle close. Regular-session VWAP requires uninterrupted candles from 09:30.
The one-contract target and fixed 50-point stop/target are unchanged.

`bot_decision` is built from the strategy payload and **final** routing outcome,
including duplicate suppression, risk blocks, dry-run attempts, and provider
errors. It never derives permission from market bias or an advisory score.
A HOLD does not mean account checks passed: entry risk checks are explicitly
not evaluated when no order is considered. A dry-run permission does not mean
live provider preflight occurred. Submission does not mean a fill.

## Defects corrected

* Candle quality was labeled overall data quality. A value of 100 ignored news,
  macro, Level 1 observations, and cross-market inputs. The display now names
  candle quality separately and describes context coverage without another score.
* ATR percentile and the old `low` label measured different things. Percentile
  ranks rolling ATR(14); `low` means six recent true ranges average less than
  70% of the preceding baseline. High historical ATR and cooling current ranges
  can coexist. Both windows and the ratio are now explicit.
* Trend strength sums **absolute** movements. Opposing components can therefore
  produce 50/100 with no net direction. Direction, movement strength, and
  component agreement are separate; there is no "moderate-conviction neutral."
* Relative volume filtered out nonpositive observations and could silently use
  an older positive-volume candle. It now compares the latest actual reading
  with its preceding baseline, preserving real zero and missing as distinct.
* Higher-timeframe agreement used hardcoded base EMAs different from the selected
  periods. The base now uses the same periods as the displayed trend. The
  descriptive read also requires directional agreement from every included
  higher timeframe; a flat higher timeframe no longer confirms a directional
  base read. Outdated aggregate windows are excluded. The
  separate advisory trade evaluator also used sampled base closes as purported
  higher-timeframe observations and credited unknown trends as neutral votes.
  It now requires complete aggregated candles and does not award unknown trends
  neutral-vote credit (`trade_plan_v2.1.0`).
* The closed flag alone could admit a candle whose interval had not ended.
  Analysis verifies the close time and keeps contracts separate. A snapshot
  fetched before its interval ended is also excluded when fetch metadata exists;
  merely waiting does not make that recorded snapshot complete. The execution
  path selects an eligible closed candle for its contract and freshness checks.
* Candle-only analysis was assembled before a separate "collected" bundle read
  at wall-clock evaluation time. Optional context now has a closed-candle cutoff,
  separate capture time, and explicit eligibility. Later observations are not
  retroactively attributed to that decision.

## Time, sessions, and identity

Provider candle timestamps are interval **open** times. The API also exposes the
end timestamp. Features and decisions exclude partial/unclosed candles. When
the data resolves a configured contract to a new active delivery, indicators use
only that delivery; provenance identifies both contracts and excluded rows.

Freshness distinguishes wall-clock age from scheduled-open age after candle
end. A new closed candle is expected after one full timeframe interval;
`max_data_staleness_seconds` remains the configured delivery grace beyond that
interval. A quiet, still-forming five-minute candle is not a missing update.
Maintenance, weekends, and closures pause expected updates; an already stale
snapshot is not made fresh by market closure. The independent exchange/session
routing gate still blocks entries during closure.

The schedule comes from `trading_day.py`: recurring CME rules plus bounded dated
exceptions. Its documented product/holiday limitations still apply; it is not
a newly purchased or comprehensive live exchange calendar. Frontend freshness
ages while the panel remains open; live chart quotes and newer closed bars are
separate from the saved evaluation.

Analysis and execution share calendar-month close arithmetic: monthly provider
bars end on the first day of the following configured month, preserving UTC
time of day. Monthly freshness waits for the next actual calendar boundary,
using that interval's scheduled-open duration on the same clock as data age.
Leap years and year rollover therefore do not introduce a 31-day approximation.

Optional Level 1 records require matching owner/contract, valid best bid/ask,
provider and receipt timestamps no later than candle close, no intervening
reset/gap, and age at the cutoff at most 10 seconds. Sizes are shown only if
actually available for that best side. Spread describes quoted transaction
cost. It cannot establish deeper liquidity, queue position, full-book imbalance,
or order-flow delta.

Viewer-driven trade-print profiles require eligible prints at or before that
cutoff. They always show actual first/last observation times, remain partial,
and are excluded from current evidence when the last print is over five minutes
old. POC is the highest recorded volume price. The 70% value area starts at POC
and adds the larger adjacent recorded-volume price until reaching 70% of
recorded volume. Unobserved prices, time, and gaps are unknown. Neither a profile
nor candle VWAP is presented as a complete session unless coverage supports it.
Descriptive VWAP uses observed candles since 18:00 New York; it is separate from
TopBot's complete regular-session VWAP. Its `complete_session` flag means
uninterrupted coverage from that 18:00 start through the latest supplied close,
not observations for the remainder of an unfinished session.

News, calendar, and related-market records retain their own availability and
reference windows. A missing feed does not establish low event risk or directional
confirmation. Fresh related observations are context, not a cross-market vote.

## Retained calculation definitions

All market metrics use the supplied eligible closed-candle history, with contract,
timeframe, count, and latest close time supplied in the response. ATR and VWAP
also expose their window bounds. These windows can be shorter than a session or
trading day.
`trend_strength`, `features.trend.strength`, and `market_bias.strength` are aliases
of the same magnitude calculation; they are not three independent votes.

| Metric | Inputs and scale | Reference and missing behavior |
| --- | --- | --- |
| Trend magnitude | `min(100, round(30*abs(gap/ATR) + 25*abs(slope/ATR) + 35*abs(delta/(ATR*n))))`. Gap = fast minus slow EMA; slope = slow EMA change over five bars; delta = close change over `n` bars (up to five). | EMAs start at first supplied close; requested periods shorten to available history. ATR normalizer uses up to 14 true ranges, or `max(abs(close)*0.001, 1e-9)` for zero range. Fewer than 10 closed bars: unavailable; legacy 0 is a placeholder. Weak <30, moderate <65, strong otherwise; these are descriptive magnitude bins. |
| Trend direction/agreement | Component signs have deadbands 0.05, 0.03, 0.05 respectively. Direction needs magnitude >=15 and absolute net sign sum >=2; otherwise neutral means no clear direction. Agreement independently reports aligned, mixed, or flat. | Same closed history. Missing history is unavailable, not a neutral observation. |
| ATR / ATR percentile | True range = max(high-low, abs(high-previous close), abs(low-previous close)); first bar uses high-low. ATR is the simple mean of 14 TRs. Percentile = 100*(count below + half count tied)/count. | Latest up to 100 rolling ATR observations, including latest; minimum 14 bars for ATR, 15 for percentile. Missing values are null. Actual reference count and window are returned. No fixed long-term or predictive interpretation. |
| Recent range change | Mean latest six TRs / mean preceding up to 28. Cooling <0.7; stable <1.35; expanding <2; sharply expanding otherwise. Old low/normal/elevated/extreme aliases are retained. | At least 22 bars (six recent, at least 16 baseline); missing/zero baseline is unavailable. Independent of ATR percentile. |
| Relative volume | Latest volume / mean preceding up to 20 volumes, including reported zeros. Low <0.7, elevated >1.5, normal otherwise. | At least five prior bars; missing latest/baseline volume or zero baseline is unavailable. Not adjusted for time of day. |
| Descriptive timeframe agreement | Selected-timeframe trend plus the next two divisible higher timeframes in the 1m/5m/15m/1h/4h/1d ladder. The base uses selected EMA periods; higher reads use EMA9/21 and the same magnitude/direction formula. | Each higher read needs 25 complete aggregates and its latest close must lag the base close by less than one higher interval. Fewer than two available reads: unavailable. All matching directional reads: aligned; flat higher reads do not confirm direction. Counts and close times are returned per read. |
| Candle-quality score | Start 100; deduct 70 for <10 bars, 35 for <25, 10 for <50; gap penalty min(30, 10*count); partial penalty min(10, 2*count); missing RVOL/VWAP/MTF 5 each; stale 35. Clip 0–100. | Insufficient payload instead uses min(35, 3*count). `data_confidence.score` aliases it. News/macro/cross-market/quotes never enter it. The numeric badge is retired. |
| Setup-quality score | 0.30*candle quality + 0.30*trend magnitude plus bonuses below, rounded/clipped 0–100. | Missing features add no bonus. <10 bars uses the insufficient candle placeholder. Deprecated; does not implement the strategy. |
| Execution-risk score | Additive candle heuristic below, clipped 0–100. | Missing range state starts at 30; missing MTF adds 10; insufficient payload uses 100 minus candle placeholder. Deprecated; does not implement routing risk gates. |
| Scenario weights | Start bullish/bearish/sideways at 33/33/34, adjustments below, clip each >=0 and normalize to integer total 100 by largest remainder. | Missing inputs add no adjustment; <10 bars gives 33/33/34 compatibility placeholders. `*_probability` are deprecated aliases. No bot signal contribution; no probability or forecast meaning. |

ATR reference-window bounds are the close times when the first and latest
rolling ATR observations became available, matching the analysis close-time
basis. They are not candle open times or the beginning of all underlying TR data.

Setup bonuses: aligned directional MTF +15, neutral +7, mixed +2; trend regime
with direction +15, range/quiet with neutral +12 (otherwise +4), chop +2,
volatile +3; elevated/normal/low volume +10/+7/+1; VWAP agrees +8, opposes -4,
neutral at VWAP +5; both nearby support and resistance +7. Labels: strong >=80,
acceptable >=60, limited >=40, weak otherwise.

Execution-risk additions: range low/normal/elevated/extreme starts 10/20/45/70;
low volume +15; chop/volatile +15; magnitude <25 +10, otherwise <45 +5;
mixed MTF +15, unavailable +10; stale +35; gaps min(30,10*count). Labels:
high >=60, moderate >=30, low otherwise. Unknown context does not prove low risk.

Scenario adjustments (B=bullish, R=bearish, S=sideways): directional trend tilt
`t=min(32,0.4*strength)` adds t to that side, subtracts .55t from the opposite
and .45t from S. Neutral changes B/R/S by -3/-3/+6. Range/quiet adds -3/-3/+6;
volatile +2.5/+2.5/-5. Elevated volume with a direction adds +5 to that side,
-2 to opposite, -3 to S; low volume -1.5/-1.5/+3. Above/below observed VWAP
transfers three points from opposite to matching side. Aligned directional MTF
adds +4/-2/-2; mixed -2/-2/+4. Resistance within one ATR adds -4/+2/+2;
support within one ATR +2/-4/+2. These adjustments have no calibration evidence.

## Separate advisory trade-plan scores

The existing `trade_evaluation` API remains advisory and cannot authorize or
block a TopBot order. It is omitted without a directional signal and valid entry,
stop, and target. Its fields are retained for existing consumers; the evaluation
panel prioritizes recorded strategy and routing checks instead.

`trade_plan_v2.1.0` sums category points (maximums below), clips to 0–100, then
applies the lowest triggered cap. `score` aliases `total_score`; category percent
and dimension percent are awarded/max*100, not success probabilities.
Dimension groups are setup quality = risk/reward + stop/target quality;
market direction bias = VWAP/location + timeframe trend + time/regime;
execution risk = ATR fit + account/news. The last dimension awards more points
for fewer identified penalties; its name does not mean a higher number is
higher actual execution risk.

| Category maximum | Inputs / missing behavior |
| --- | --- |
| Risk/reward 20 | Reward/risk bands <1, <1.5, <2, <3 award 15%,45%,70%,85% of maximum. >=3 awards full maximum if target realistic, otherwise 75%. Invalid geometry awards no points. |
| VWAP/location 20 | Start 6 (legacy missing-VWAP baseline 8); direction support +8, opposing with reversal +4; no chasing +4; target beyond day extreme -4; bad location -3. Missing support remains null, not true. |
| Timeframe trend 20 | Direction agreement at actual 5m/15m/1h/4h contributes 15/30/35/20 to the alignment index; observed neutral contributes 45% of its weight, unknown contributes no vote. Category = round(index*0.2), capped at12 with higher-timeframe conflict and <=2 aligned. |
| Stop/target quality 15 | Tight stop -5; wide -3; unrealistic target -5; not behind structure -3; no room -4. Invalid directional geometry awards no points. |
| ATR fit 10 | Tight stop -4, otherwise below reasonable minimum -2; wide -3; unrealistic target -3. Missing multiples retain legacy 55% baseline, expressly not evidence. |
| Time/regime 10 | Chop/trend-or-breakout/reversal/range/unknown starts2/9/7/6/5; lunch -3; open/NY morning/power hour +1; overnight/premarket -1. |
| Account/news 5 | Unknown news -1; unknown account -1; high/medium news -5/-2; daily-loss danger -4, else drawdown danger -4, else reduce-size condition -2. |

Each category clips to its own range. Existing geometry thresholds: stop <0.5 ATR
tight, <0.8 below reasonable minimum, >2.5 wide; target >4 ATR unrealistic;
chasing >2 ATR from VWAP. Caps: invalid geometry39, reward/risk<1 caps54,
low data confidence69, chop69, high news60, daily-loss/drawdown danger54.
Grade bands are A>=85, B>=70, C>=55, D>=40, F otherwise. Take/wait/avoid and
confidence are advisory labels, not the actual execution outcome.

The advisory completeness score starts100 and subtracts missing VWAP12, ATR12,
high/low of day4 each, each timeframe trend5, regime8, news5, tick size4,
point value8, account balance5, day P&L5, daily-loss limit5, trailing drawdown4.
Clip at0; high>=90, medium>=65, low otherwise. It is not overall source coverage.

Advisory ATR uses 14 actual complete 5m bars; each timeframe trend requires14
complete bars of that timeframe, comparing closes over12 bars to
`max(0.6*ATR, abs(close)*0.0005)`. EMA21 and MA200 require21/200 actual complete
bars, respectively. Partial higher-timeframe buckets cannot supply evidence.
Its day extremes/VWAP normally use observed New York calendar-day candles;
the legacy fallback uses all supplied rows if that day has none. VWAP is
unavailable if any selected row lacks volume or the selected volume total is zero;
missing volume is not converted to zero or replaced with another day's VWAP.
These are not guaranteed complete session values. Account values are the provided snapshot; missing
balance/drawdown are not fabricated. The remaining legacy baseline awards above
are documented rather than presented as evidence or a bot decision.

## Operational boundary and verification

No data subscription, live-trading toggle, strategy preset, Supabase billing,
database migration, retention policy, or Databento filesystem policy is changed.
Tests use generated candles and isolated SQLite fixtures; browser previews use
synthetic API-shaped snapshots and cannot route orders. Focused tests cover
conflicting labels, missing inputs, closed-time/contract mismatches, quiet and
closed markets, Level 1 eligibility, partial profiles, and actual routing outcome
consistency. See the delivery report for commands and results.

### Verification on September 7, 2026

* Backend: **598 passed** with `backend/.venv/Scripts/python.exe -m pytest`,
  covering `test_bot_market_analysis`, `test_bot_decision_explanation`,
  `test_market_context_bundle`, `test_market_observations`, `test_bot_service`,
  `test_bot_execution_safety`, `test_bot_risk_hardening`,
  `test_bot_evaluation_trade_levels`, `test_bot_evaluation_candle_acquisition`,
  `test_topbot`, `test_topbot_strategy`, `test_bot_backtesting`,
  `test_trading_day`, `test_trade_plan_evaluator`,
  `test_trade_plan_observation_coverage`, `test_databento_import_storage_policy`,
  `test_databento_local_cache`, and `test_local_backtest_support`.
* Frontend: **870 passed across 107 files** using `npm run test -- --reporter=dot`.
  `npm run build` passed TypeScript checking and the Vite production build.
* Browser: rendered the actual component with four schema-validated synthetic
  API snapshots: populated HOLD, missing candle history, risk rejection, and
  dry-run permission. Inspected desktop layout and expanded strategy/risk,
  quote/profile, and calculation details. This exposed and fixed a risk-limit
  object/list mismatch before delivery. Temporary preview routes were removed.

Tests do not establish trading performance or live-provider availability.
Topstep routing/connectivity checks used mocks; no real order was sent. No new
news, calendar, or cross-market feed was acquired. Level 2, queue position,
full-book imbalance, quote-derived delta, and complete viewer-driven session
profiles remain unavailable. Existing source freshness and coverage are reported
per evaluation rather than assumed.
