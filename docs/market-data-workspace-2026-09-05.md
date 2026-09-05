# Market-data workspace

The `/data` page brings stored market data, coverage checks, source collection,
economic events, recorded market flow, and decision research into one workspace.
The Bot evaluation panel includes the collected context available at its decision
time. Trades now show observed MAE/MFE where a reliable lifecycle association exists.

## Collection and coverage

- **Application history:** instrument, delivery contract, source, provider mode,
  timeframe, counts, first/last observation and collection timestamps. A bounded
  recent one-minute check compares observed minutes with the app's exchange
  calendar; a missing minute is not automatically a feed failure.
- **Local archive:** displays the existing Databento continuous series separately
  from ProjectX delivery contracts. It does not splice incompatible sources or
  imply that the archive was extended.
- **Captured MNQ history:** verifies the pinned manifest, every file hash and all
  55,240 captured minute bars before importing. Complete 5-minute, 15-minute and
  hourly buckets are derived from those minutes. Existing conflicting prices are
  preserved. This history has already been used for strategy research.
- **Related futures:** explicitly collects the last three days of MNQ, MES, NQ and
  ES through the connected ProjectX account. Native ENQ/EP symbols are recognized
  as NQ/ES while their delivery contract IDs remain intact. Provider mode is
  explicit, and missing instruments remain missing.
- **Public references:** manual daily US 10-year constant-maturity yield collection
  from Federal Reserve H.15. Yield levels are percent per year and changes are
  basis points. See [public reference setup](public-daily-market-context-2026-09-05.md)
  for provenance and the optional Cboe VIX adapter.
- **Economic calendar/news:** official Federal Reserve press releases and FOMC
  dates, a BLS calendar adapter, and an optional Trading Economics US calendar.
  Date-only events retain that precision. Actual, consensus, previous and revision
  fields stay absent unless supplied by a source. Failed or incomplete sources
  cannot produce a falsely complete low-risk calendar.

Collection buttons are manual and bounded. Merely opening Data does not start a
broker history download, paid feed, background scheduler, or trading run.

## Recorded decisions and market flow

The existing Bot order-book connection now records real delivered quotes, depth
events, resets, connection gaps and separately subscribed trade prints. Collection
is viewer-driven. The recorder exposes its queue, dropped-event count, write
errors, timestamps, spread samples and partial executed-volume profile. It does
not claim complete depth entitlement or infer aggressor direction from an
ambiguous provider field.

Each new bot decision stages an immutable snapshot before routing. Only committed
decisions are published to the independent bounded writer. A separate routing
record can be attached without rewriting the decision-time context. Nested
order-claim rollbacks preserve valid outer decision snapshots.

The snapshot includes stored related markets, economic event context, available
headlines, the latest valid recorded bid/ask, and the partial current-session
trade profile with POC, 70% value area and VWAP. An intervening reset, connection
gap or unusable book invalidates the earlier quote. Delta is unavailable unless
all recorded trade volume has supported aggressor classification.

Decision outcomes use up to 60 minutes of later closed one-minute candles for
the same owner, contract, source and mode. Signal-minute ambiguity, missing
candles, same-bar target/stop ambiguity, expiry and missing geometry are separate
outcomes. These are hypothetical barrier observations, not simulated fills or
realized P&L. The score table reports its resolved sample size; heuristic scores
are not calibrated win probabilities.

Execution observations match stored order attempts to exact account/contract/order
fill records. The reported signed decision-to-fill difference is not an arrival
quote slippage estimate. Acknowledgment latency remains unavailable without
measured timestamps. Position-wide MAE/MFE are labeled separately from a uniquely
matched whole-trade excursion, and initialized zero values are not mistaken for
observed excursions.

## Time and ownership guarantees

All new APIs require the application's authentication context and scope stored
data to its owner. Decision queries additionally validate account ownership.
Economic event versions preserve both event/publication times and local receipt
times. Historical revisions cannot appear in earlier decision snapshots.
Decision context excludes candles collected after its cutoff. Public daily
observations always enforce first-collection availability; their date alone does
not establish historical publication time.

No external feed is fetched in an order-routing transaction. Optional context
reads use savepoints, and capture failures are surfaced in recorder telemetry.
The existing bot execution controls and order gates remain unchanged.

## Configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| `TOPSIGNAL_MARKET_CAPTURE_ENABLED` | `true` | Capture observations from an open Bot feed |
| `TOPSIGNAL_MARKET_CAPTURE_QUEUE` | `4096` | Bounded asynchronous queue |
| `TOPSIGNAL_MARKET_CAPTURE_RETENTION_DAYS` | `3` | Market-observation retention, bounded 1–30 days |
| `TOPSIGNAL_MARKET_CAPTURE_RECORD_CAP` | `250000` | Maximum retained observations per owner, bounded at 2 million |
| `TOPSIGNAL_DECISION_RETENTION_DAYS` | `3650` | Decision-snapshot retention |
| `TOPSIGNAL_DECISION_RECORD_CAP` | `1000000` | Maximum retained decision snapshots per owner |
| `TRADING_ECONOMICS_API_KEY` | Unconfigured | Optional calendar actuals and consensus, subject to account coverage |
| `CBOE_VIX_ENABLED` | `false` | Optional daily VIX collection after appropriate data-use rights are established |

Retention is enforced on writer batches. This is a bounded local research recorder,
not a durable complete exchange tick archive. Full-session tick/depth archives,
equity/ETF context, broad news, consensus data and settlement/open-interest feeds
still require suitable sources and access.

## Database and verification

Migration `20260905_add_market_data_workspace.sql` adds four tenant-owned tables
for event versions/source status and market observations/decision snapshots. It
revokes public/client-role access and publishes schema baseline
`schema-20260905-v7`. Use the checksummed migration runner; do not edit an applied
migration or rerun the full schema against a populated database.

The running app was checked with its authenticated session on September 5, 2026:

- Application price/reference rows increased from 95,257 to 166,573: 59,809 rows
  from the verified capture, 3,755 MES minute bars, 3,752 NQ minute bars, 3,750 ES
  minute bars and 250 daily US10Y observations. Existing MNQ history already
  covered the recent refresh window. Counts include multiple timeframes.
- The separate Databento continuous one-minute archive remains 2,532,300 bars.
- Federal Reserve sources stored 20 news releases and 57 meeting entries. The
  calendar shows the September 15–16 meeting as a date range with no assumed
  announcement time.
- BLS returned HTTP 403. Trading Economics was not configured; Cboe collection
  remained disabled. The UI reports these limits and macro coverage as unknown.
- No new live observations or decisions were manufactured during the closed
  market. Recorder and decision screens correctly showed zero new records.
- The database check confirmed all 53 migrations current.

The full offline backend suite passed 1,839 tests with zero external connection
attempts. Eight disposable-PostgreSQL integration tests were skipped because
that separate test database was not configured. The frontend suite passed 853
tests; lint and production build passed. Real collection checks and current
source availability are visible in Data.
