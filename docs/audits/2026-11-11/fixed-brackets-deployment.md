# Persistent fixed TopBot exits — November 11, 2026

The user authorized fixed 25-point stop loss and 25-point take profit on
peteamdpc, keeping one-second trade progress recording enabled and starting
the bot. Release `20261111-fixed-brackets-79` runs commit
`a59c134bc4084955c52f4fe2e772c662cef49a03`. Activation completed at
07:15:14 UTC. Practice bot configuration 40, account 29593203, started run 18
at 07:15:48 UTC (02:15:48 New York).

## Configuration and verification

The existing dated fixed-bracket override would expire at the next trading
session. The new `strategy_params.exit_mode = fixed` preference persists through
Stop / Start, session rollover, worker restart and contract rolls. The strategy
panel reflects this policy. New fixed entries use native 100-tick stop and
100-tick target brackets, equivalent to 25 MNQ points each. Their saved exit
plans do not trail. Other configurations retain the trailing default.

An authenticated provider refresh immediately before starting confirmed account
`PRAC-V2-192577-85156978` was simulated, eligible to trade and fresh. The normal
continuous practice-routing start API was used. No test order was forced.
The configuration readback differed only in exit mode and update timestamp
before starting; starting also enabled the configuration as intended.
One MNQ, contract `CON.F.US.MNQ.Z26`, the +$500 daily profit / −$500 daily loss
guards, session cutoff, entry rule and existing auto-resume/entry-limit
preferences were preserved. Daily guards and the cutoff can still close a
position before its fixed bracket fills.

Thirteen authenticated observations from 07:16:00 through 07:17:02 UTC confirmed
run 18 remained running with no error and an advancing worker heartbeat. Depth
was ready with fresh quotes and durable evidence recording. No new order had
been submitted during that interval; the imbalance was below the entry threshold.
All thirteen progress-recorder observations were enrolled, healthy, error-free
and configured for one-second samples, with strictly advancing heartbeats.
Progress storage remains local SQLite on D:, with 1,094,852,308,992 bytes free
at the final observation. Recording starts sampling when a position is open.

## Release checks

The full local release suite passed: 3,293 backend tests, 20 skipped, 8 subtests;
1,290 frontend tests; 47 deployment-script tests; lint, dependency audits and
the production build. Focused coverage verifies persistent preference across
restart/session rollover/contract changes, long/short native 25-point brackets,
saved fixed exits without trailing and rejection of mismatched protection.

All six jobs in the [exact-commit CI run](https://github.com/drewstake/TopSignal/actions/runs/42976980745)
passed before activation. Its initial Windows attempt failed the unchanged
shutdown test's 20 ms completion assertion after releasing the blocked worker.
The complete 42-test worker file passed locally; failed CI jobs were rerun once
without changing source, assertions or timeouts and passed. Candidate 78 was
never activated: its build caught a missing frontend type, corrected in 79.

Normal release gates verified broker flatness before and after the graceful
switch, immutable files and ACLs, frontend/API/worker readiness, companion tools
and recorder continuity. Release 77 remains available for rollback. No migration,
historical import, deletion, backup-policy change or recording reenrollment was
needed. Private receipts are retained under
`tmp/releases/fixed-brackets-79-operations` and host staging
`C:/ProgramData/TopSignal/staging/20261111-fixed-brackets-79`.
