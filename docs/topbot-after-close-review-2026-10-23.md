# TopBot after-close review — October 23, 2026

The scheduled review ran after the close. TopBot collected the complete October
23 research window and remained dry-run. All 71 eligible decisions hit the
unchanged 25-point volatility cap; the 15:30 boundary added one session HOLD.
There were no numerical forecasts, simulated trades, baseline cohorts or broker
orders. This session supplies more collection evidence, but no new evidence of
model predictive skill. No strategy, model, account or risk setting was changed.

## Evidence and comparison window

Times are America/New_York (EDT, UTC−04:00). The after-close snapshot was captured
at **17:45:56 on October 23** (21:45:56 UTC), after the final normal research
outcome's missing-data deadline. The runtime and read-only broker check completed
at **17:46:45**. The hosting task identified release `20261023-forward-10`.

The comparison follows the protocol prepared before this session:
[comparison protocol](topbot-after-close-comparison-protocol-2026-10-23.md).
The frozen baseline is the [first-day review](topbot-first-day-review-2026-10-23.md)
and its October 23, 08:24:47 evidence capture. The matched decision window is
**09:35–15:30 scheduled closes**, including the boundary HOLD, on October 22 and
23. These are two observed sessions, not a large independent validation sample.

The local SQLite backup passed integrity checking. Both snapshot manifests were
verified before analysis; the downloaded archive also matched its remote SHA-256.
The SQLite backup is transactionally consistent. Database rows, logs, model and
the live report were captured separately over several seconds, so this is not a
single atomic transaction spanning every store. No new session close occurred
during that interval. All 265 evaluation payloads in the frozen morning baseline
remain unchanged in the new 369-row ledger.

## Decisions and collection

| Matched window | October 22 | October 23 |
| --- | ---: | ---: |
| Expected / recorded distinct closes | 72 / 72 | 72 / 72 |
| Missing / duplicate ledger closes | 0 / 0 | 0 / 0 |
| BUY / SELL / HOLD | 0 / 0 / 72 | 0 / 0 / 72 |
| Volatility-cap HOLDs | 68 | 71 |
| Net-edge HOLDs | 3 | 0 |
| Session-boundary HOLDs | 1 | 1 |
| Numerical forecasts | 3 | 0 |
| Recording delay: median | 17.29 seconds | 15.71 seconds |
| Recording delay: empirical 95th percentile | 29.02 seconds | 19.92 seconds |
| Recording delay: maximum | 38.84 seconds | 21.82 seconds |

All 71 October 23 in-window volatility evaluations explicitly reported fresh
input data. Their implied stops, independently recomputed from 21 retained
five-minute closes, ranged from **28.5 to 60 points**, all above 25. The October
22 range was 25.75–64. At 09:35 on October 23, sigma was 19.569587 points;
rounding `sigma × sqrt(3)` upward to a quarter point gave a 34-point stop.
The gate correctly stopped inference before a model probability or direction.
There was no support, net-edge or execution-risk rejection downstream of that
gate. The 15:30 evaluation occurred after the entry cutoff and correctly held.

The ledger covers **October 22, 09:20:34 through October 23, 17:00:17**, with
369 distinct closes. October 23 has 205 calendar-day closes from 00:00 through
17:00: 71 volatility HOLDs and 134 session HOLDs, including the boundary. The
database has 214 October 23 signal events for those same 205 closes. Ten events
reference the final 17:00 close through the 17:45 evaluation: one original plus
nine repeats after market closure. They are not ten new opportunities, and the
ledger deduplicates them. The matched 72-close window has no repeated events.

Across run 9 through the snapshot there are 593 signal events and 560 distinct
closes, including 33 repeated closure/maintenance events. The ledger started
later than the continuous run; 191 earlier distinct closes remain in the
database/log evidence rather than being backfilled. Run 9 began October 21 at
16:26:25. These totals are not a matched two-day performance comparison.

The exported 588 complete five-minute bars span October 21, 14:00 through
October 23, 16:55 (bar-open timestamps). No expected open-session bars were
missing. No open-session evaluation was missing after the ledger began, and
database signal closes matched the ledger throughout that period. The final
16:55 bar closes at 17:00. No additional Friday bars are expected at 17:35 or
17:40. The generic `data_status=missing` on session-gated records describes
skipped inference, not proof of absent market bars.

## Separate outcome categories

### Numerical model forecasts

There were **zero October 23 numerical forecasts**, so no new forecast outcome or
calibration sample exists. The three October 22 forecasts remain theoretical
same-close-entry outcomes, all observed, with no collected delayed-entry outcomes.
Their BUY payoffs were −$52.22, −$51.72 and −$51.72; the alternative SELL payoffs
were +$72.78, +$72.28 and +$72.28. All selected NO_TRADE. They overlap and cannot
be summed as selected trades. Only one falls on the nonoverlapping 15-minute
scoring lattice. The three legacy delayed-entry gaps were preserved, not filled
retrospectively. There are no pending, overdue or unknown numerical outcomes in
the collected fields; that does not erase those missing legacy fields.

### Actual simulated account

Both sessions have **zero trades and $0 realized P&L**. At the after-close
snapshot the account is flat, with no queued entry or unknown blocked position.
Zero P&L reflects the absence of trades. It is not a successful forecast result.

### Volatility-blocked hypothetical paths

All **71 October 23 scenarios completed**, versus 62 collected October 22.
The baseline rollout missed six earlier volatility rejections; October 23 has
a scenario for every volatility HOLD. All 133 retained scenarios are observed,
with zero pending, overdue, unknown or missing directional outcomes. The last
October 23 scenario resolved at **15:40:13**, well before this review.

| Collection / alternative | Scenarios | Positive / negative | Sum after assumed costs | Mean |
| --- | ---: | ---: | ---: | ---: |
| October 22 collected, BUY | 62 | 24 / 38 | −$1,029.64 | −$16.6071 |
| October 22 collected, SELL | 62 | 35 / 27 | +$970.36 | +$15.6510 |
| October 23 complete, BUY | 71 | 32 / 39 | −$274.12 | −$3.8608 |
| October 23 complete, SELL | 71 | 34 / 37 | −$310.12 | −$4.3679 |
| October 23 common 10:20–15:25 window, BUY | 62 | 28 / 34 | −$288.64 | −$4.6555 |
| October 23 common 10:20–15:25 window, SELL | 62 | 31 / 31 | −$258.64 | −$4.1716 |

The October 22 collected sample is already the common 10:20–15:25 window.
The common clock window controls the rollout period, but does not match the
days' market conditions or create tradable matched opportunities. These are
overlapping, independent BUY/SELL alternatives with no model-selected direction.
**Do not add the sides, interpret their sums as portfolio returns, or choose the
better side after seeing the results.** October 22's favorable SELL total did
not recur on October 23; neither observation establishes predictive skill.

All **142 October 23 directional paths** were independently repriced from the
retained OHLC bars. Payoffs, entry prices/times, exit types and recorded exit-bar
end times matched, with zero discrepancies. BUY exits were 14 stops, four
targets and 53 time exits; SELL exits were 12 stops, three targets and 56 time
exits. No checked path encountered an ambiguous stop/target bar or stop gap.

The calculation assumes one MNQ at $2 per point, $1.22 round-trip commissions,
and 0.5 adverse points per side: nominal cost **$3.22**. Entry is the first
five-minute open after recording; the deadline remains the original signal
close plus 15 minutes, giving at most ten minutes of exposure here. Stops use
adverse gap fills, ambiguous bars use stop-first ordering, and target fills
retain an adverse exit haircut. An `exit_at` value identifies the completed
bar's end, not a known intrabar touch time. Original input revisions and actual
quotes/fills were not frozen; recomputation verifies the retained data and the
assumed rule, not achievable execution.

### Forward baseline accounts

There are still **zero baseline cohorts and zero baseline trades**. The baseline
implementation arrived after October 22's last eligible numerical forecast,
and October 23 produced none. There is no observed TopBot versus flat,
always-long, always-short or empirical-pool account comparison. Empty cohorts
are not zero-return competing strategies. The registered comparison requires
at least 20 complete matched sessions; these two evaluation sessions do not
meet that requirement.

## Runtime health and interruptions

The backend remains PID 3044, started **08:37:46**, matching this morning's
deployment. No subsequent restart appears in the captured supervisor log.
The planned morning restart did not lose a scheduled close. Readiness briefly
reported degraded at **12:31:31**; surrounding worker heartbeats remained
healthy and `/ready` returned HTTP 200 at 12:31:16 and 12:31:43. The exact cause
of that transient readiness-probe failure is not established by the saved
evidence. The 12:30 and 12:35 evaluations are present.

Two real provider network failures occurred **after the close**, at 17:24:50
and 17:25:57, during account listing. Provider status recovered at 17:25:19 and
17:27:06 respectively. They caused brief degraded readiness and retries, but
no missing open-session bar/evaluation or unresolved research outcome. The
current worker heartbeat advanced during the safety check; its lease matched
PID 3044, provider status was healthy and `last_error` was empty.

The existing preflight returned **PREFLIGHT_OK**. Its separate collection
subsection still reports missing 17:35/17:40 closes because it applies open-market
freshness expectations after the 17:00 close. That warning was retained and is
not evidence of a stopped collector. Complete-session coverage was established
independently above; preflight alone was not used to claim completeness. No
checker or production behavior was changed during this review.

## Safety verification and retained evidence

Only **configuration 32 / continuous run 9** is active, on the same account, in
dry-run and explicitly unvalidated. The complete configuration matches the frozen
baseline. Both live-execution environment gates remain false, direct live entry
is rejected, and all 853 release-manifest hashes match. Risk/routing source files
and worker code match the protected prior release. The 25-point cap, one MNQ,
one maximum/open contract, $250 daily loss admission limit, 30 daily entries,
300-second cooldown, 600-second configuration freshness and stricter model
freshness/session/support/edge limits remain unchanged.

Read-only checks from October 21, 16:22 through October 23, 17:46:45 returned
**zero stored order attempts, broker orders, open orders or fills**. Broker
evidence is limited to the configured account and returned provider history.
The checker allowed only authentication and order/trade searches; this review
did not place, modify, cancel or force an order.

The fitted parameters remain identical, with model SHA-256
`857ac815bb56f1f992aebbf78e60319e66d7d832862b2e601340b8a35a479387`.
The model still contains 1,648 historical paths and a July 10 training cutoff,
approximately **105.08 days old** at the safety check. Fresh market collection
does not make that training current or the model validated.

Private evidence is retained beside the deployment receipt in
`C:\Users\drews\.ssh\topbot-review-20261023`:

- `after-close-evidence/` and `after-close-evidence.zip`: manifest-verified
  database export, SQLite backup, model, logs and report. Archive SHA-256:
  `b0c0574319d11c5d8bf4ba0c7731c33ab85c2f0b37d8dd9fb0993333ae27ba21`.
- `after-close-comparison.json`: reconstructed baseline and matched session.
- `after-close-independent-audit.json`: coverage, duplicates, 71 stop checks,
  142 path checks, timing, prior-row preservation and interruption evidence.
- `after-close-safety.json` and `after-close-capture-and-guards.txt`: runtime,
  configuration, source-integrity, model and read-only broker verification.
- `audit_after_close.py`: reproducible local audit. Its initial exit-time
  comparison used bar opens; that intermediate output was preserved separately
  before correcting the audit to the documented stored bar-end convention.

The remote original snapshot remains under
`C:\ProgramData\TopSignal\tools\review-after-close-20261023-1745`.
Existing preflight files were hash-verified into a new backup directory before
running the check. Database size was 216,796,307 bytes; this review made no bulk
database writes. Historical candles, evidence and replay data stayed on disk.
Existing uncommitted application changes and all prior records were preserved.

The after-close comparison and safety review are complete. The strategy remains
the unchanged control; these observations justify no refit, threshold tuning,
risk expansion or live-trading readiness claim. No ongoing monitoring schedule
was added by this one-time follow-up.
