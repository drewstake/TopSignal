# Trade progress recording idea

Recorded November 10, 2026. Implemented in the working tree November 11; not deployed or activated.
See [operation, validation and activation](trade-progress-recording.md).
Updated November 11 at the user's request to sample every second instead of every 10 seconds.

Record how many points an open trade is up or down approximately every second,
then show that history in its trade or journal review. The user suggested this
while in a trade. A timeline would help review how profit developed, how much was
given back before exit, and how long the position spent above or below entry.

## Existing coverage

The [streaming P&L tracker](../backend/app/services/streaming_pnl_tracker.py)
recalculates unrealized dollar P&L on incoming market and position updates. It
retains the most adverse and favorable observed values (MAE and MFE), with their
timestamps, in memory and emits a summary when the position closes or reverses.
It does not retain the sequence of intermediate P&L values.

The [excursion recorder](../backend/app/services/historical_trade_excursions.py)
enables this tracking for configured accounts independently of bot arming and
saves closed summaries in local SQLite. It also estimates historical excursions
from one-minute candles; those estimates cannot recover a one-second path. The
tracker's summary point values divide dollar extremes by maximum position size
and point value, so scaling can make them differ from the price excursion at the
time of each extreme.

The [market archive](../backend/app/services/market_archive_events.py) separately
samples quotes no more frequently than every five seconds for configured
contracts and retains broker events. This is potential reconstruction input,
subject to coverage and exact position history, rather than a saved per-trade
points timeline. Coverage of the user's currently open trade has not been
verified against the running recorder.

## Proposed behavior

- Save a sample every second while a position is open, plus samples at entry,
  size changes, partial exits, reversal and final exit. Run collection on the
  backend independently of whether the browser is open.
- Record timestamp, user/account and position identity, delivery contract,
  direction, current quantity, average entry price, mark price and its timestamp,
  signed points, and unrealized dollars before fees. Keep realized P&L from
  partial exits separate so remaining-position P&L is not mistaken for the whole
  trade result.
- Define points as mark minus current average entry for a long, and average entry
  minus mark for a short. Quantity changes dollar P&L, not the meaning of points.
  Preserve the entry basis and quantity at each sample when scaling.
- Continue updating observed best/worst excursions on incoming prices: a
  one-second sample alone can miss a brief spike. Mark stale prices, disconnects
  and gaps explicitly rather than inventing prices or reporting missing data as
  zero. Label any later reconstruction separately from live observations.
- Show points over elapsed time with entry, exits and observed extremes marked.

## Storage and implementation checks

At one-second intervals, one continuously open position produces 3,600 periodic
samples per hour, plus event samples. The user explicitly requires this feature's
recordings to stay on peteamdpc's local drive, which they identify as a 2 TB hard
drive. Store samples, timeline payloads and feature-specific recording metadata
in local SQLite under `TOPSIGNAL_DATABENTO_CACHE_DIR`, following the
[storage policy](supabase-storage-policy.md). Do not store or mirror these records
in Supabase tables, JSON fields or object storage, and do not use Supabase as a
fallback. Serve the chart through authenticated backend reads of the local store.

Preserve account isolation, durable restart handling and the existing off-device
backup workflow. Verify the configured path is on the intended drive and inspect
actual free space before activation; 2 TB is total capacity, not verified free
space. Estimate bytes and index growth, monitor available disk space, and surface
recording failures without disrupting trading. Do not impose automatic age-based
deletion or purge existing records without explicit authorization. Existing
authentication and unrelated account, trade and journal storage remain outside
this feature's storage change.

Before implementation, verify account enrollment and recorder health, choose the
mark-price basis, and define lifecycle matching across reconnects, scaling and
reversals. Validate that a transient move between samples still changes the
observed extremes and that gaps remain visible after restart.
