# Supabase reads and practice classification

The November 1 usage dashboard reported 6.307 GB in the October 2–November 2
billing cycle. October 31 showed 709.805 MB of Shared Pooler Egress. PostgreSQL
statistics since October 16 showed about 557,000 column-inspection queries,
returning 9.8 million schema metadata rows. These are query counts, not exact
byte attribution. The production supervisor called `/ready` about every ten
seconds; each call repeated SQLAlchemy schema reflection.

Changes:

- Reuse successful PostgreSQL schema inspection for five minutes per engine,
  with one concurrent inspection, expiry, and invalidation on database failure.
  Failed inspections are never cached. SQLite remains uncached. Connectivity,
  the migration ledger, worker state, account classification and execution
  admission remain fresh on every readiness request.
- Probe database-backed readiness every 60 seconds in the supervisor. Process
  and worker liveness retain the existing ten-second cadence. Readiness failure
  remains an operator signal and never creates a restart loop.
- Select only authorization/status fields for repeated run discovery, stream
  synchronization, armed-run counts and readiness. Avoid the duplicate global
  unresolved-order count. Do not cache live authorization or execution checks.
- Persist account classifications from the existing periodic ProjectX probe
  (normally once a minute), including while no entry signal is present. Only
  owned ProjectX-backed accounts with running live runs are refreshed. Missing,
  ambiguous or non-boolean results invalidate prior classification; an explicit
  funded result remains false. Provider failures do not renew old evidence.
  Freshness still expires after five minutes. Entry preflight remains required.
- Record classification observations and committed refreshes in the existing
  local action journal. No new cloud tables, bulk writes, raw market arrays,
  migrations, history deletion or billing changes are involved.

Validation: the complete backend suite passed 2,855 tests with nine existing
environment-dependent skips and eight subtests. The focused readiness, worker,
classification and recovery suites passed 107 tests, including concurrent
schema-cache requests, expiry/retry, database isolation, uncached live checks,
strict classification, ownership, provider failure and reduced SELECT fields.
The supervisor script parses successfully. A fixture import-order issue was
corrected by selecting the offline test database before importing the app.

Reducing reads prevents future bandwidth use; it cannot reverse egress already
counted or independently lift Supabase's current billing-cycle restrictions.
