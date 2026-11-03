# Recorded liquidity history timeout

The chart's timeout message appeared while peteamdpc and its independent depth
recorder were online. The message is a browser request deadline, not evidence
that the recording process stopped. Recent-window requests have a 30-second
deadline and initial session requests have a 125-second deadline.

A read-only profile on the production host measured a 15-minute history read at
21.58 seconds. It reopened 1,920 immutable segment manifests; manifest text reads
accounted for 11.06 seconds and repeated path validation for 6.62 seconds. A
following full-session read took 15.14 seconds. Disk/OS caching and profiling
overhead affect these measurements.

The reader now caches parsed manifest metadata in process, bounded to 4,096
entries and 8 MiB of source metadata. Every request still validates paths and
checks file identity, size and modification metadata. New manifests are discovered
on each inventory scan. Recording bodies are still read and checksum-verified;
raw recordings and replay arrays are not cached by this change. Cache eviction
only discards in-memory metadata, never files or database records.

The staged reader on peteamdpc took 4.34 seconds for its first recent-window read,
3.52 and 3.91 seconds for subsequent recent-window reads, and 5.52 seconds for
the current session. These measurements used the actual recordings, with zero
skipped segments and approximately 1.32 MB of cached manifest metadata.

107 tests passed across history, cache behavior, research, microstructure,
recording and totals. Regression checks cover reused manifests, discovery of new
segments, owner filtering, modified manifests, same-size recording corruption,
cache bounds and path checks on cache hits. Existing session/gap behavior remains
covered. No frontend or request-deadline change is included.

Deployment candidate: `20261102-history-cache-39`, based on actual release 38
with only `backend/tools/read_depth_history.py` changed. All 1,028 other tracked
source files and 280 frontend artifacts were compared with the running release.
The environment is unchanged. The candidate retains release 36's virtual
environment via a junction, so that dependency must remain available.

Read-only broker preflight confirmed the practice account was flat with no open
orders and that run 11/configuration were unchanged. Activation and verification
records are kept under `C:/ProgramData/TopSignal/staging/history-cache-39`.

Release 39 was activated at 21:05 EST. Frontend, API, database-backed readiness
and worker health passed at 21:05:52. The recorder retained the same processes
and task definition. Browser verification of Latest 15m returned 2,616 combined
recorded/live points, with recordings through 21:05:36 and no timeout. A separate
readiness request returned 200 in 818 ms.
Full session also loaded successfully. Its following automatic refresh advanced
the recorded-through time from 21:05:36 to 21:06:37, with no timeout banner.
Worker health remained `running`/healthy (HTTP 200). Browser verification was
read-only apart from selecting chart windows; no trading controls were used.
