# Worker depth scheduling clock correction

The worker captured `now` at the start of its cycle, before database queries,
commits and provider/client preparation, and passed it to `DepthFeed.snapshot`.
A tick arriving during that work was newer than `now`, so the freshness gate
rejected it with a negative age. UI telemetry used the current clock and could
show a qualifying episode while the worker skipped its event-driven evaluation.
Scheduled candle-boundary evaluations could still run.

The worker now lets `DepthFeed.snapshot` capture the current time under the feed
lock. The earlier timestamp is retained for cycle/candle scheduling. There is no
change to the 30-second persistence threshold, two-second freshness bound,
future-timestamp rejection, final pre-submission checks, risk limits, broker
routing, worker lease or episode/order deduplication.

The regression test runs the worker's `_process_run` against an isolated SQLite
database and a real `DepthFeed`. It reproduces a tick arriving after the cycle
timestamp but before observation, with candle-boundary evaluation disabled.
Before the patch, qualifying BUY, SELL and exact-30-second cases failed to
evaluate. Afterward they evaluate once, and repeating the episode does not
evaluate again. Short persistence, truly stale data and future ticks still do
not trigger evaluation. No test routes a real order.

139 tests passed across worker/depth integration, worker lifecycle/soak,
strategy/order safeguards, depth telemetry, streaming isolation, risk hardening
and account classification. Source comparison against deployed release 39 found
only the intended timing change and its explanatory comment.

Candidate `20261102-depth-clock-40` overlays only `backend/app/bot_worker.py` on
the actual deployed release. Other source, frontend, environment and saved bot
configuration are preserved. Release 36's virtual environment remains a junction
dependency. Activation uses read-only broker preflight, graceful shutdown,
configuration fingerprints and existing practice-resume authorization. The
independent depth recorder is not restarted.

Activation was initially deferred: release 39 opened practice order attempt 11
at 21:35 EST while the candidate was being prepared. The broker confirmed one
MNQ long and its protective stop. A guarded deployment waiter began at 21:38
EST; it checks for completed position management before running the full flat
account preflight and activation. It cancels if the run, configuration, operator
STOP or active release changes, and expires after two hours. No position or
trading setting was changed to permit deployment.

Position management completed at 21:54 EST. Broker preflights confirmed the
practice account was flat with no open orders, both before and after graceful
shutdown. Release 40 started at 21:55:30 EST on November 2, 2026; frontend, API,
worker and database-backed readiness passed at 21:56:08. The recorder processes
and task definitions were unchanged. A subsequent independent check confirmed
the production task running release 40 and all four health endpoints returning
HTTP 200. The timing correction is live.

Private validation and rollback records:
`C:/ProgramData/TopSignal/staging/depth-clock-40`.
