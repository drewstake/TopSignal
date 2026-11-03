# Bot page database-pool recovery

Activated `20261102-candle-snapshots-38` on peteamdpc on November 2, 2026.
Frontend, API, worker and database-backed readiness passed at **10:45:12 EST**.

## Observed failure

The host and SSH tunnel were reachable, but authenticated bot requests timed out
waiting for the application's five available database connections (pool size 3,
overflow 2, ten-second checkout timeout). Queries also hit the thirty-second
statement timeout. Worker lease renewals failed intermittently. `/health` alone
did not expose this failure; it only establishes process liveness.

A read-only database snapshot showed the optional raw market-observation writer
holding a transaction for about fifty seconds while inserting individual feed
events. Its batch loop issued up to 256 individual inserts and performed market
and decision retention queries on every batch. This competed with application
and worker requests. Database size was **448,760,979 bytes**, above the storage
policy's 400 MB threshold for pausing optional bulk writes. This evidence
identifies a removable source of pressure, not a complete attribution of all
Supabase latency. Disabling that optional writer in release 37 improved initial
behavior but did not resolve the incident: pool timeouts recurred at 10:33–10:34.

Live Python stack profiling then identified repeated ORM expiration loads inside
`next_market_candle_fetch_start` and `market_candle_rows_are_stale`. The chart
endpoint loaded its candle cache, rolled back its read transaction while resolving
provider credentials, then used the now-expired rows. Each timestamp access issued
another database query. The captured profile spent about 32 aggregate thread-seconds
in these reloads. Several simultaneous chart timeframes occupied the small pool.

## Changes

- Detach the fully loaded, read-only chart candle snapshots before subsequent
  transaction rollbacks. Timestamp calculations and provider-failure fallbacks
  use those in-memory values without per-candle SELECTs. This applies both to
  exact delivery-contract requests and symbol resolution. Other ORM sessions,
  bot execution reads, cache writes and transaction settings are unchanged.
- Raw relational feed capture now requires the separate explicit opt-in
  `TOPSIGNAL_RAW_MARKET_CAPTURE_ENABLED=true`; its default is false. The existing
  master switch still controls decision snapshots. Optional trade-print
  subscriptions use the raw-capture flag too.
- Decision-only writer batches no longer scan or delete raw market history.
  Existing observations remain readable. No manual cleanup or bulk write ran.
- Database pool timeouts and operational failures return a sanitized HTTP 503
  with CORS headers and a readable message. Errors contain no SQL or credentials.
  The server does not automatically retry failed writes.

The independent filesystem depth recorder remained on the same processes and
task definition. Saved accounts, trades, credentials, bot configuration, decision
capture and the existing practice auto-resume behavior were preserved. No
billing, execution-gate, migration, historical-import or database-retention-limit
changes were made.

## Validation and deployment

325 focused tests passed together across chart/candle behavior, capture, error
responses, authentication, order-book behavior, worker behavior, streaming
isolation and worker soak. A regression test first reproduced 40–80 expired-row
SELECTs from forty cached candles; the corrected endpoint performs zero. It
covers both exact contracts and resolved symbols, provider success/failure,
preserved response contents, and released connections during provider I/O.
Regression tests cover raw capture being off while decisions persist, avoiding
market-history SQL on decision-only writes, readable CORS errors, sensitive-data
redaction, trusted origins and no automatic operation retry.

The initial four-file backend overlay was applied to a copy of the actual deployed
release, not the dirty development checkout. The old release manifest predated
fourteen existing source edits; candidate files were verified against the actual
deployed files and those edits were preserved. All 280 frontend files were
unchanged. The candidate uses the prior release's virtual environment through a
local junction; retain release 36 while releases 37/38 depend on that environment.
The final release 38 adds only the chart snapshot correction to release 37 and
matches the tested workspace `main.py` exactly. Existing source and frontend
hashes were rechecked before activation.

Read-only broker preflight verified the practice account was flat with no open
orders both before and after graceful shutdown. Saved configuration fingerprints
and run 11 matched. The first start lacked the service account's directory
permissions; applying the same restricted ACLs as release 36 resolved startup.
The second deployment copied those ACLs before starting. Both previous releases
remain available for rollback. No restart or rearming was
requested through the bot controls.

Release 37 had transient startup errors and briefly recovered, then failed again;
that brief success was not accepted as completion. Release 38 restarted cleanly,
passed broker preflight again with run 11/configuration preserved, and reported
worker `running` and database-backed readiness 200 at 10:45 EST. The extended
verification failed: readiness timed out from 10:47 onward, with worker lease
loss/error followed by recovery/standby. The endpoint is not confirmed restored.
A second live profile verified zero expired-row reloads, but ordinary SQL reads,
candle upserts and lease renewals still stalled. Independent connections also
failed: session mode timed out on connection/queries; transaction mode reported
database authentication did not complete within 15000 ms. No connection-mode
or pool-size change was applied based on these diagnostics.

## Remaining external blocker

The authenticated Supabase organization Billing and Usage pages explicitly show
`Services restricted` / `Egress Exceeded`. Usage already shows the new November
2–December 2 cycle and 0.515 / 5 GB, while the restriction remains. The November
1 audit recorded 6.307 GB in the preceding cycle. The dashboard says restrictions
may take time to clear after reset; this is not evidence of a guaranteed release
time. This restriction is confirmed independently of the database stalls; its
exact relationship to PostgreSQL query latency has not been established.

Billing: https://supabase.com/dashboard/org/kjenrqzbuuzzxsvvmqsh/billing

Suggested support request (not submitted): The TopSignal organization remains
restricted for Egress Exceeded after its billing cycle reset to November
2–December 2, with current usage 0.515 / 5 GB. Please confirm the triggering
metric, measured value, limit, and release condition, and clear a stale
restriction if appropriate. The project is also experiencing session-pooler
connection/query timeouts and transaction-pooler authentication timeouts.

No support message was sent and no paid plan or spend-cap change was made.
Release 38 remains deployed with the tested query and error-handling corrections.
Full end-to-end recovery still requires database availability and a successful
follow-up soak; this report does not claim the production outage is fixed.

Private package hashes, configuration fingerprints, task snapshots and activation
logs are on the host under `C:/ProgramData/TopSignal/staging/db-recovery-37` and
`C:/ProgramData/TopSignal/staging/candle-snapshots-38`. A temporary sampling tool
was installed only under staging, without changing production dependencies.
Existing database usage still warrants capacity review; this fix does not purge
stored records or increase the plan allowance.
