# TopBot paper v2 deployment report

**Completed October 29, 2026, 22:06 UTC / 18:06 Eastern on peteamdpc.**
Release `20261029-paper-v2r1-17` is running under the limited `TopSignalSvc`
identity with the corrected patch and a separately fitted observation artifact.
Both live-routing gates are false; all existing artifacts and ledgers are
preserved. The approved credential repair and final verification are complete.
The first rollout's recording defect was fixed before completion. No live
promotion is authorized.

## Final verification

- Backend health, worker health, readiness and the exact built frontend passed.
  The sole active worker lease belongs to the release 17 backend on peteamdpc.
- The normal 18:05 decision was a HOLD outside the research session. Its new
  ledger record was **confirmed at 18:05:21.419 Eastern**, and the corresponding
  execution/archive commits succeeded without a forward-recording exception.
  There are 1,444 evaluations, 76 comparison trades, no main paper position and
  zero main paper trades. The one incomplete earlier HOLD remains `unknown`.
- All 1,443 stopped-baseline evaluations and 76 comparison fills passed
  preservation checks. Both preceding model files and the original observation
  state retained their hashes; every one of the 883 release files matched.
- At 22:05:45 UTC, quote counters showed 50 queued and persisted, zero pending.
  At 22:06:22, they had advanced to 58 queued, 57 persisted and one in flight,
  with zero retries. The critical backlog was zero. Collection resumed with
  its existing `feed_status=unverified` classification; no feed-quality gate
  was waived. This after-hours HOLD does not establish eligible paper admission.
- The final allowlisted broker check at 22:06:17 UTC found **zero orders, open
  orders, positions and fills**, and zero broker mutation calls.
- The post-record backup at **22:06:13 UTC** verified **31 files / 45,029,905
  bytes** with independent restore checks. It contains the confirmed HOLD,
  the optional quote spool and all three model namespaces. The updated scheduled
  task also completed successfully (exit 0); its next run remains 18:15 Eastern.

[Final operational receipts](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/deployment-paper-v2r1-20261029/final-receipts)
and the backup receipt are retained alongside the patch and safety results.

## Credential repair and operational recovery

At 21:40:30 UTC, the explicitly approved repair reset only `TopSignalSvc` and
updated its three existing scheduled-task credentials. The account SID and
Limited privilege remained unchanged. The original encrypted credential and
task definitions were retained, with recovery evidence copied off device.
The replacement is machine-DPAPI encrypted and restricted to administrators
and SYSTEM; fresh SSH and limited-account startup checks passed. Kaitl's password
and broker credentials were not changed. The original pre-repair startup exit
code alone did not establish its precise cause.

A development copy on this PC had acquired the shared evaluator lease while
the production host was offline. Its process was stopped and its worker and
two live-routing flags were set false, preserving the original private `.env`.
Production acquired the lease normally, without editing the lease or bot run.
No local development server was restarted.

## Defects caught during activation

Release 16 initially passed HTTP health and preservation checks. Its first
scheduled evaluation at 21:50 UTC exposed `AttributeError` in the recording
completion receipt: resolving an older forecast reused `path` for a NumPy
payoff array, overwriting the SQLite ledger path. This occurred only when
maturing an earlier outcome and confirming a new recording in the same call.
The new HOLD remained durably pending; no paper entry or broker order resulted.
The service was stopped at **21:52:19 UTC** with STOP latched.

The fix names the payoff array separately. Six regressions reproduce the failure
before the fix and pass afterward, covering prior legacy/v2 forecasts and new
HOLD/BUY/SELL records, confirmed receipts and retry deduplication. An unconfirmed
historical record must retain uncertainty; it must not receive a fabricated
completion time or retroactive fill.

The fresh backup also exposed a restore-check assumption: the newest committed
HOLD legitimately had no model. The checker now verifies that decision's exact
inputs and also the most recent model-bearing decision's inputs and artifact.
Three regressions cover the model-free HOLD, a missing model identity and a
missing referenced artifact. Corrupt model references still fail closed.

The unchanged model source guard rejects the previous artifact after this code
repair. A new fit was therefore generated from the same frozen inputs and
parameters. Its model payload is identical. It uses the separate
`probabilistic-v3/unvalidated-v2.1/<owner-hash>/<stream-hash>.json` namespace;
the installed `unvalidated-paper-v2` artifact remains intact.

## Exact release, tests and validation

The release is assembled from verified release 16 plus six reviewed source/test/
documentation files. The [updated source patch](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/deployment-paper-v2r1-20261029/release-source.patch)
contains 46 changed source files relative to original release 15. The
[additional repair patch](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/deployment-paper-v2r1-20261029/recording-repair.patch)
records the six-file delta from release 16. Existing workspace edits and previous
patches are preserved. Dependency manifests and database migrations are unchanged.

- **601 backend safety tests passed** on the final assembled code (20.59 seconds),
  using isolated SQLite and blocked external network access.
- The earlier **46 cache/storage-guard tests** remain applicable to unchanged
  cache dependencies. They passed with one pre-existing docstring warning.
- The previously tested frontend is byte-identical to release 16: **385 tests**,
  production build and changed-file lint passed for those assets.
- All **883 release file hashes** are verified on the host. Protected release
  ACLs retain administrator/SYSTEM control and limited service read/execute.
- The new local fit and full validation preserve data, folds, costs, thresholds,
  risk limits, training cutoff and payoff semantics. It contains 4,096 sampled
  paths, trained through October 23 at 19:45 UTC.
- All **4,320 chronological forecasts** and **230 post-training forecasts** remain
  NO_TRADE. No qualifying trades or positive expectancy were demonstrated.
  Promotion gates remain unmet; this is unvalidated dry-run observation only.

The additional results were reported before activating the corrected release.
[Fit, validation and regression evidence](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/recording-path-fix-20261029)
includes unchanged-model and prior-artifact checks. The original exact-stop
validation report and candidate are unchanged.

| Identity | SHA-256 |
| --- | --- |
| Final release ZIP | `0312b2ce70006353151fe40c9b9efc69600a4aaa1ad8f7db664ac1bbe9fad350` |
| Final release manifest | `fba5264153957d4fc875d277b414f7b28d6276f54df983fdbaa06617c922eb1d` |
| New candidate file | `c33c7ae9a57286f4ba854e97cf2e1bbf6ef568331f589bbe9c4666443aa548a7` |
| New canonical artifact | `7e4e044e74c255603b19f31fb966f336d786bdfccfb97231e2ceb60d9eafbf94` |
| Implementation binding | `856cee44fec03ded157e8d64423a19573027417f13a4dcb07d541f2b091a6da5` |

## Preservation and backup

Bot configuration 32, dry-run authorization 9, account classification, entry
thresholds and risk limits remain unchanged. The broker account is still HIDDEN,
nontradable and invisible. Read-only allowlisted broker checks confirmed zero
orders, open orders, positions and fills; verification makes no broker mutations.

The initial 1,442 evaluations, 75 comparison trades, two comparison cohorts,
original model and observation-state file were retained. The initial v2 rollout
added one HOLD and resolved one existing comparison trade normally. Its stopped
baseline contains 1,443 evaluations and 76 comparison trades, with zero main
paper trades. No old ledger is restored over current evidence. Both earlier
release directories and model artifacts are preserved in place.

Before the corrected activation, an off-device backup at **21:58:03 UTC** verified
**27 files / 42,931,597 bytes**, with checksum, SQLite integrity and independent
restore checks. Restoration verified both the latest model-free HOLD (2338) and
the earlier model-bearing decision (2322), including each exact input list.
Earlier snapshots and failed restore attempts are retained. One manual restore
attempt exceeded Windows path length; a shorter destination passed.

The existing daily 18:15 Eastern backup task keeps its user and schedule. Its
new `scheduled-tools-v17` runner contains the corrected restore checker and uses
the quote-aware remote helper. Backups require this development PC to be on,
drews signed in and private SSH connected. All three original Databento source
ZIPs also have independently verified off-device copies. There were no bulk
historical imports or changes to Supabase billing, retention or legacy guards.

## Operational limits and rollback

The deployed candidate remains `unvalidated`, `dry_run_observation_only` and
`routing_allowed=false`. Freshness and model-age limits remain unchanged.
Activation outside the entry window does not demonstrate prospective eligible
paper admission or profitability. Quote persistence is bounded, not lossless.
During release 16 startup, one critical SQLite contention error was retried
successfully; its diagnostic was retained and the critical backlog returned to
zero. The first optional quote was durably queued and persisted. Under release 17,
the incomplete HOLD was conservatively marked `unknown` and no retroactive
entry or completion timestamp was created.

The prepared rollback returns to release 15 using the repaired credential and
current D: storage, taking a new snapshot first. It preserves the new artifacts,
quote spool and all ledgers; it does not select the defective release 16 or
restore an old ledger. Rollback has not been run.

The earlier preparation/credential-blocker report is preserved verbatim as
[historical deployment evidence](C:/Users/drews/Development/TopSignal/backend/storage/databento/audits/deployment-paper-v2-20261029/deployment-report-before-activation.md).
Private operational receipts remain in the `.ssh/topbot-paper-v2-20261029` and
`.ssh/topbot-paper-v2r1-20261029` directories. No credentials are included in the
source package or this report.
