# Practice recorder sidecar race — November 15, 2026

The account recorder logged intermittent `FileNotFoundError` failures without
SQLite commit failures. The November 15 inspection found six discarded queued
events across four Combine recorders; the Practice recorder's discard counter
was zero. Those counters do not prove complete observation coverage. At the
owner's request, only Practice account 29593203 remains enrolled, the five
Combines have manual opt-outs, and automatic recorder enrollment is disabled.
The independent MNQ market recorder remains enabled.

`trade_progress_store.disk_report` checked each SQLite file with `exists()` and
then a separate `stat()`. SQLite can remove its WAL/SHM sidecars between those
calls. The resulting `FileNotFoundError` escapes destination verification into
the account writer's storage-recovery path, which resets the engine and discards
queued observations rather than pretending that coverage was continuous.

A deterministic regression reproduces the same logged exception for both
sidecars and leaves the queued quote unprocessed on the original code. A bounded
1,000-call read-only host probe did not reproduce the intermittent race; the
existing host log lacks a traceback, so attribution of every historical error
to this race is not established.

The fix samples file size with a single `stat()` and reports zero for a missing
file. Destination verification still requires the main database, verifies the
registered drive and free-space floor, and opens the database read-only.
Permission and other I/O errors still propagate. No write-retry, queue, account,
strategy, risk, retention or database-schema behavior changes.

All nine added regression cases pass, including persisted queued observations
through disappearing WAL/SHM files, a missing main database, and permission/I/O
errors. The 65 focused progress, route and history-sync checks pass on Windows.
Full release validation and deployment acceptance are recorded separately when
completed. Historical gaps remain historical gaps; this fix cannot recreate
observations that were never persisted.
