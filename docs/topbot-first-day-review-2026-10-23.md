# TopBot first-day dry-run review — October 23, 2026

TopBot's HOLD decisions were consistent with its registered rules. This review
found no evidence supporting a strategy or risk-limit change. The implemented
improvements expose the actual evidence coverage and make report sections read
one consistent ledger snapshot. The model remains unvalidated.

## Review period and evidence

All times below are America/New_York (EDT, UTC−04:00).

- **First continuous day:** October 21, 16:26:25.112415 through October 22,
  16:26:25.112415; UTC October 21–22, 20:26:25.112415. This is run 9. The
  earlier, replaced run 8 is excluded from decision totals.
- **Extended health/evidence snapshot:** October 23, 08:24:47.509167
  (12:24:47.509167 UTC), about 39 hours 58 minutes after run 9 began. Latest
  ledger record: 08:20:16.000706, for the 08:20 closed candle. The 08:25 close
  was not yet due.
- **Local ledger:** October 22, 09:20:34.947709 through October 23,
  08:20:16.000706: 265 unique evaluations. It did not exist for the first
  approximately 16 hours 54 minutes of the run. The database and diagnostics
  retain 191 earlier distinct candle evaluations; these are not fabricated
  into the local ledger.
- **Market coverage:** one complete October 22 research window, with all 72
  scheduled closes from 09:35 through 15:30 recorded. There were 71 decisions
  inside the entry window and one 15:30 boundary HOLD, because evaluation
  occurred after 15:30:00. The collection also spans off-window evening,
  overnight, premarket, and maintenance periods. It is not 24 trading hours
  or multiple independent regular sessions.
- **Numerical forecasts:** three, at 09:35, 09:40 and 09:45 on October 22.
  All precede the version-2 future-entry comparison rollout. Their theoretical
  candle-open outcomes were collected, but their delayed-entry outcomes and
  forward baseline accounts were not collected.
- **Volatility scenarios:** collection began October 22, 10:20:27.267144.
  Sixty-two later volatility HOLDs have scenarios; the six earlier rejections
  have none. Nothing was backfilled.

The active release was discovered from the scheduled hosting task and verified
as `20261022-forward-09`, rather than inferred from older access notes. A SQLite
backup taken through its backup API passed integrity checking. Its accompanying
read-only database export, model, logs and per-file SHA-256 manifest are retained
privately on both computers. No credentials are included in this report.

## Decisions

The first 24 hours contain **289 database signal events, all HOLD**, representing
**277 distinct candle closes**. Twelve additional events reevaluate the final
candle during the scheduled maintenance break; they are not new opportunities.

| First-day reason | Signal events | Distinct candle closes |
| --- | ---: | ---: |
| Outside session / session-close restriction | 218 | 206 |
| Implied volatility stop exceeds 25 points | 68 | 68 |
| Net edge after uncertainty does not exceed $1 | 3 | 3 |
| BUY / SELL | 0 / 0 | 0 / 0 |

The extended snapshot contains 480 run-9 signal events and 456 distinct closes.
Each day's maintenance break has 13 events referencing its final candle: one
original event and 12 repeats, or 24 repeated events across both days. All are
session HOLDs, without order attempts or repeated forecast evidence. The local
ledger correctly deduplicates them. Its 265 rows comprise 194 session HOLDs,
68 volatility HOLDs, and three net-edge HOLDs; it has no duplicate identities.

| First-day close time, Eastern | Volatility HOLD | Net-edge HOLD | Session HOLD |
| --- | ---: | ---: | ---: |
| 09:35 to before 10:30 | 8 | 3 | 0 |
| 10:30 to before 14:00 | 42 | 0 | 0 |
| 14:00 through 15:30 | 18 | 0 | 1 |
| Outside that research window | 0 | 0 | 205 |

All three numerical forecasts had 1,648 training paths and approximately 90.62
effective training days. The 300-path and 20-day minimum-support gates passed;
**insufficient statistical support was not the observed reason for these HOLDs**.
Their uncertainty-adjusted net edge failed. No execution-risk rejection or
accepted simulated entry was recorded.

### Representative calculations

- At **09:35**, the implied stop was exactly 25 points, so the cap passed.
  BUY expected net payoff was $1.86375, but its uncertainty penalty was
  $9.01327, leaving lower utility **−$7.14952**, below the required value
  greater than $1. SELL lower utility was **−$15.60790**. HOLD was correct.
  The 09:40 and 09:45 BUY lower utilities were −$7.08313 and −$7.29788;
  neither cleared the threshold either.
- At **09:50**, RMS variation of the preceding 20 five-minute close changes
  was 14.8208 points. Rounding `sigma × sqrt(3)` upward to a 0.25-point tick
  gives a **25.75-point** stop, exceeding the 25-point cap. Numerical
  inference was correctly blocked before producing a probability.
- At **10:20**, sigma was 23.26545185, implying a **40.5-point** stop and
  a 60.75-point target. This produced only independent research scenarios.
  The evaluation was recorded at 10:20:27, hypothetical entry was 10:25,
  and the original deadline remained 10:35.
- At **15:30**, evaluation happened after the entry window ended. The session
  restriction correctly held. Overnight records similarly returned session
  HOLDs before model inference. Their generic `data_status=missing` field
  means inference inputs were not evaluated at that stage; it does not by
  itself establish missing market candles.

All 68 volatility stops, ranging from **25.75 to 64 points**, were independently
recomputed from the retained 21-candle windows. All three full forecasts were
reproduced using the saved fitted parameters. There were no numerical mismatches.
These checks use the retained candle cache; original per-decision input revisions
were not frozen, so they cannot prove that the provider never revised a bar.

## Separate outcome categories

### Model forecasts

| October 22 close | Theoretical BUY outcome | Theoretical SELL outcome |
| --- | ---: | ---: |
| 09:35 | −$52.22, stop | +$72.78, target |
| 09:40 | −$51.72, stop | +$72.28, target |
| 09:45 | −$51.72, stop | +$72.28, target |

All three forecasts selected **NO_TRADE**. These are overlapping alternative
payoffs, not selected trades. Recording occurred 20.05–26.78 seconds after
candle close, so a fill at that same candle-open instant is a theoretical model
label, not an achievable post-recording entry. No retroactive delayed-entry
results were added. The nonoverlapping 15-minute scoring lattice contains only
one forecast: BUY Brier 0.24655, SELL 0.37269. That is insufficient calibration
or predictive-skill evidence; the two sides are not independent observations.

### Actual simulated account

**Zero trades, $0 realized P&L, flat, no queued entry, no blocked unknown
position.** Zero account P&L reflects no trades, not successful prediction.

### Volatility-blocked alternative paths

All **62 scenarios completed**, with zero pending, overdue, or unknown outcomes
at the snapshot. Separate sides are:

| Alternative | Completed | Positive / negative | Sum after assumed costs | Mean |
| --- | ---: | ---: | ---: | ---: |
| BUY | 62 | 24 / 38 | −$1,029.64 | −$16.6071 |
| SELL | 62 | 35 / 27 | +$970.36 | +$15.6510 |

These sums include overlapping alternatives. **Do not combine them, treat either
as achievable account return, or select SELL after observing its outcome as
evidence of model skill.** No model direction was selected for these rejections.

All 124 directional outcomes were repriced independently from the retained OHLC
bars, with zero mismatches. BUY exits were 16 stops, one target and 45 time exits;
SELL exits were eight stops, ten targets and 44 time exits. No actual resolved
path encountered an ambiguous stop/target bar or a stop gap in this sample.
Existing tests cover stop-first ambiguity, gaps and missing-data handling.

For example, the 10:25 open was 29,402.50. The BUY assumed entry was 29,403.00,
its stop 29,362.50, and the bar low 29,346.50: net **−$83.22**. The SELL assumed
entry was 29,402.00, its target 29,341.25, reached in the next bar: net
**+$119.28**. These are alternative paths for the same rejected opportunity.

Pricing assumes one MNQ, $2 per point, $0.61 commission per side and 0.5 adverse
points per side for spread/slippage/latency. The nominal round-trip cost is
**$3.22**. Stops and targets are measured from the adversely adjusted entry;
stop gaps use the adverse open, favorable target gaps receive no price bonus,
and an adverse exit haircut also applies to targets. Prices enter at the first
five-minute open after recording, retaining the original close-plus-15-minute
deadline. Typical exposure is therefore at most ten minutes. OHLC cannot establish
intrabar ordering or actual fills. Missing required bars eventually become
unknown, rather than zero-return observations. Costs are assumptions, not measured
quotes or broker fills.

### Forward baseline accounts

**No cohorts or baseline trades exist.** The comparison implementation began
after the day's last eligible numerical forecast. Subsequent volatility HOLDs
did not create eligible baseline opportunities. There is no matched TopBot versus
flat/always-long/always-short/empirical-pool comparison yet. No first-day baseline
was reconstructed after the fact. The existing comparison method requires at
least 20 complete matched sessions and accounts for daily dependence; it cannot
support a conclusion from this sample.

## Completeness, timing and health

- No missing open-session five-minute bars were found in the exported
  October 21, 14:00 through October 23, 08:15 candle range. The 17:00–18:00
  maintenance intervals explain the gaps. The ledger has every expected close
  after it began, apart from periods with no new market candle. Database signal
  closes and ledger records agree throughout the ledger's collection period.
- Logs contain one provider 504 at October 22, 09:42:55; provider health returned
  to `ok` at 09:44:02. The 09:45 evaluation and its outcome were collected.
- Prior release changes caused planned worker restarts. The release-09 backend
  had remained up since October 22, 12:50:24. No further restart appears before
  this review's deployment. Log contents were current even though Windows file
  directory metadata still showed older last-write times while handles remained
  open. File metadata alone was not used to diagnose collector failure.
- Before release 08, the 23 available ledger records had median recording delay
  24.32 seconds. The following 242 records had median 15.72 seconds, approximate
  empirical 95th percentile 20.03 seconds and maximum 38.84 seconds. This is a
  descriptive timing comparison across different periods, not a strategy result.
- The July 10, 15:45 training cutoff makes the artifact about 104.69 days old at
  the snapshot. This is stale **training**, separately disclosed. The last
  numerical forecast is old because later fresh evaluations hit the volatility
  gate and then the session gate. Neither fact means the worker stopped.
- The 08:25 collection/safety check passed: advancing heartbeat, healthy worker,
  valid lease and matching backend process, fresh expected session HOLDs, both
  live gates false, and direct live-entry guard rejection. Read-only searches
  since October 21, 16:22 returned zero stored order attempts, broker orders,
  open orders and fills. Broker evidence is limited to the configured account
  and the provider's returned history.

## Changes and validation

The new coverage panel reports collection start, selected record bounds, complete
evaluation sessions, missing due closes, input failures and duplicates, with a
two-minute recording allowance. It distinguishes partial/in-progress/truncated
coverage, the three legacy theoretical-only forecasts, six volatility HOLDs
without scenarios, and the absent matched baseline cohort. Complete evaluation
coverage is explicitly separate from complete outcomes and validation.

The report now reads account state, evaluations, coverage and comparison cohorts
inside one SQLite read transaction. Previously the comparison query opened a
second connection, so a concurrent worker commit could mix report sections from
different states. A regression test commits through another connection during
report assembly and verifies that all sections retain the original snapshot.
Report queries also exclude records later than their requested observation time.

Validation: **2,246 backend tests passed, nine skipped, eight subtests passed**;
the initial full run had 14 Windows path-length fixture errors, all resolved by
rerunning those tests with a shorter temporary root. The affected forward/model
subset then passed all 61 cases. **1,200 frontend tests**, the focused final
coverage tests, frontend lint, production build and **ten deployment-script
tests** passed. No trading rules, model features, thresholds, costs or training
parameters changed. Existing uncommitted reporting edits were preserved.
The live browser opened at the sign-in page; authenticated visual inspection
was not performed. Component rendering tests and the deployed backend report
were verified separately.

## Deployment and safety confirmation

Deployed **`C:\ProgramData\TopSignal\releases\20261023-forward-10`** using
the existing immutable-release, least-privilege scheduled-task procedure. The
new backend started October 23 at **08:37:46 Eastern**. All **853** packaged
source/build hashes matched; the database schema was already current at 56
migrations, and this change added no migration or bulk cloud write.

The post-deployment safety check completed **08:40:19 Eastern, PASS**, with
healthy readiness, worker and lease; matching service/process identity; and
zero stored order attempts, broker orders, open orders or fills since October
21, 16:22. The next genuine evaluation was recorded **08:40:12.734095**,
an expected premarket session HOLD, 12.73 seconds after candle close. All 268
pre-deployment ledger rows remain byte-for-byte unchanged, with that new 269th
record added; prior trade and comparison tables also remain unchanged.

Only **TopBot configuration 32 / run 9** is active, continuous and dry-run.
Configuration 38 remains disabled; the account is unchanged. Both
`TOPSIGNAL_LIVE_EXECUTION_ENABLED` and
`TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION` remain explicitly false.
The direct live-entry guard rejected entry. Risk and execution source files,
worker behavior and all configuration values were compared with release 09.

Unchanged limits include the **25-point stop cap**, one MNQ, one maximum/open
contract, $250 daily loss admission limit, 30 daily entries, 300-second cooldown,
600-second configuration freshness limit, and existing stricter model freshness,
session, support and net-edge gates. No broker order was placed, modified,
cancelled or forced.

The fitted model SHA-256 remains
`857ac815bb56f1f992aebbf78e60319e66d7d832862b2e601340b8a35a479387`.
Only its implementation binding changed to match the reporting code; its
training data, parameters, cutoff, purpose and unvalidated status are identical.
The changed artifact digest is not a refit or new strategy.

Rollback retains release 09 plus these verified recovery files under
`C:\ProgramData\TopSignal\tools`:

- `forward-ledger-before10-20261023.sqlite3` (268 evaluations; integrity checked,
  SHA-256 `13ef308075e9414f61ae17ed8162a07d6bfd9924d95d33da74a69cf7fbb71ec7`).
- `model-before-forward10-20261023.json` and `task-before-forward10.xml`.

Rollback must pair the previous code and model, while preserving the current
shared runtime ledger rather than restoring over subsequently collected data.
The previous collection-health report was also preserved. Recovery copies and
the deployment receipt are saved privately on the viewing computer in
`C:\Users\drews\.ssh\topbot-review-20261023`.

The database measured **214,256,787 bytes** before the change, below the storage
policy's investigation threshold. Historical data and the research ledger stayed
on disk; no historical series was uploaded to Supabase, no data was purged, and
no billing or retention settings changed. The existing SSH results helper now
targets release 10. No change was made to the ongoing monitoring schedule.

## Future evidence

Keep the current strategy as control. The first hypothesis is that ordinary
future in-cap opportunities will produce forecasts and start genuinely matched
baseline cohorts; track coverage and unknown outcomes before assessing their
returns. A second question is whether the old training distribution remains
calibrated: assess future nonoverlapping forecasts, session dependence and costs
with uncertainty, without changing the decision rule on this day's results.

If a refit or feature candidate is later justified, register its training cutoff,
parameters, comparison protocol and evaluation start before collecting its test
period. Run it as a separately identified, unvalidated shadow candidate against
the unchanged control on future untouched sessions. Do not increase the stop cap,
manufacture trades, choose the winning hypothetical side, or call this reviewed
day out-of-sample validation of a change selected from it.

No new trading bot, account, research candidate or monitoring schedule was
enabled. This review does not establish cold-boot recovery, external alert
delivery, validated profitability, or live-trading readiness.

The deployment procedure's external hosting requirements were rechecked against
[Topstep's API access guidance](https://help.topstep.com/en/articles/11187768-topstepx-api-access).
All activity in this review remains dry-run or read-only; no broker mutation
endpoint was used.
