# Storage maintenance — November 13, 2026

The owner authorized the prepared maintenance after the 4 p.m. New York stock
market close. MNQ futures continue through 5 p.m.; fresh broker checks, recovery
gates and existing execution safeguards still apply. This receipt records work
in progress and does not declare Supabase retired.

## Recovery and preparation

The original immutable candidate `053e0c6` passed all six CI jobs, 3,411 backend
tests, 1,291 frontend tests, 47 release-script tests, lint, dependency audits and
production build. Fourteen additional PostgreSQL integration checks passed.

At 21:02:58 UTC a new encrypted execution/auth backup was published at
`D:/TopSignal/data/databento/execution-backups-v1/20261113T210239Z-1107a76a71e6`.
Its off-device copy under
`C:/Users/drews/.ssh/topsignal-local-first-20261102/remediation-backups/`
restored at 21:05:45 UTC: 37 tables, 12,862 rows, verified hashes and credential
decryption, execution authority absent, and local authentication issuance,
restart and revocation with network calls blocked. The isolated database is
`topsignal_audit_maintenance_20261113_1`. This focused backup does not replace
the pending full-cache catch-up.

Seventeen configuration/task files, including the new dedicated recorder
credentials and prior cloud/helper environments, were encrypted into
`D:/TopSignal/data/databento/configuration-recovery-v1/20261113T210757Z`.
The off-device copy under the protected `configuration-recovery/` directory was
decrypted in memory and every original byte count/hash verified. Ciphertext
SHA-256: `892979453827d2184b837d6b3f7eaeb163b3bff05075c08c74c648fb2ce45b94`.
No old environment, recovery copy, cloud data or audit record was deleted.

The local `topsignal_depth_recorder` role was provisioned and verified: only
owned paper configuration 32 and its one encrypted ProjectX credential are
readable; foreign-owner rows are invisible; writes remain denied even with
the read-only session default disabled. All helper files are protected;
the limited service identity can read the private environment but cannot write it.

## Initial recorder switch and recovery

The 21:08:50 UTC broker gate found practice account 29593203 simulated/eligible,
zero positions/orders, zero unresolved attempts and no open research windows.
Earlier research windows were allowed to finish instead of accepting gaps.

The recorder drained cleanly, sealing capture `839cd91d7dc747228386733572a4c0e4`
at durable index 19,452,222. The new helper was started at 21:09:17 UTC but failed
before market connection. A shared hub import loaded `StreamingPnlTracker`,
which imported application models/database initialization despite the independent
recorder's correctly isolated credential lookup.

The prior recorder task action was restored without forced termination and
restarted at 21:11:36 UTC. At 21:11:55 UTC PID 18808 was recording capture
`1f67d065ffe24c98ad678db9552773ad`, advancing durable data with zero errors,
zero reconnects and zero broker mutations. Preserve this maintenance gap; do
not present it as provider coverage or backfill observations.

The correction makes the hub's tracker reference a type-checking-only import.
A fresh-process regression reproduces the failure without any application
DATABASE_URL and proves the independent recording loop does not load `app.db`
or `app.models`. The 61 targeted recorder/hub checks pass. A new immutable
candidate and exact-commit CI are required before a second switch; the failed
helper directory and its receipts remain intact.

The application remains release `20261112-auto-signin-87`. No application
restart, fill migration, mirror retirement or enrollment change has occurred
at this checkpoint. The dedicated local role is prepared, while the restored
recorder still uses its prior startup configuration.
