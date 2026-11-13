# Bot-page liquidity heatmap

The [November 13 audit](audits/2026-11-13/peteamdpc-health.md) verified this feature
in release `20261112-auto-signin-87`, a running deriver and current-session SQLite
data on peteamdpc. The November 11 tests below predate deployment. During the
audit the UI reported a priority pause; the deriver later reported zero pending
segments and data about 40 seconds old. Catch-up does not erase historical
source/quality gaps. The lower [Liquidity before the move chart](order-book-history.md)
uses a separate totals reader that can time out even when heatmap derivation is current.

The Liquidity heatmap on the Bot page shows observed resting depth (heat), best
bid/ask, and executed prints for the selected MNQ contract across the **current
New York trading session** (6 p.m. to 5 p.m.). It is display-only: it never
connects or disconnects the shared depth stream, changes ladder filters, or calls
bot execution APIs. Strategy, signals, risk settings, the fixed SL/TP brackets and
the one-second trade recorder are unchanged.

## Data sources

| Time range | Source |
| --- | --- |
| Session open up to about one minute ago | Derived history from the independent depth recorder's committed raw segments |
| The unsealed tail and any recorder outage | This page's live 2 Hz samples of the shared `OrderBookStore` (up to 10 minutes) |
| Prints | Recorded trade events for history; the live trade SSE stream for the uncovered tail |

The recorder (`TopSignal Depth Recorder`, see [depth recording](topbot-depth-recording.md))
collects every depth/trade event whether or not a browser is open. Where the
recorder has coverage, its history is authoritative and live samples are not
drawn on top; live samples fill only time the recorder has not sealed or did not
record. Prints follow the same rule, so nothing is double counted.

### Derivation (`backend/tools/depth_heatmap.py`)

- Reads only sealed, checksum-verified segments (`verified_rows`); active
  `.partial` files are never opened. Each capture is replayed in index order
  through the validated `MarketByPriceBook` kernel with `BookTotals` boundary
  rules (connect/disconnect/gap/roll/end, contract change, epoch change, type-6
  reset, receipt time reversal).
- A frame at slot *s* (multiple of 500 ms of receipt time) is the book after every
  event received by *s*, nearest 80 levels per side, mirroring the live sampler.
  A slot is an explicit gap — never a frame — when disconnected, just reset,
  awaiting depth, stale (provider time more than 2 s behind), future-dated,
  empty, crossed/locked or off the 0.25 tick grid. Nothing is interpolated.
  Slots in the daily 5–6 p.m. halt and weekends are not emitted.
- 5 s and 60 s tiers store per-tick sums and sample counts, so a coarse cell is the
  exact mean of the observed 500 ms samples in its bucket. Prints are grouped by
  price, ProjectX-reported side and 250 ms / 5 s / 60 s bucket and merged exactly
  across segment boundaries. Overlapping captures are excluded, not merged.
- After every segment the full engine state (book levels, timestamps, duplicate
  fingerprints) is checkpointed, so a restart resumes exactly. Without a
  checkpoint, the book is rebuilt from up to five minutes before the first pending
  segment and the session discloses `book_rebuilt_mid_capture`; levels not updated
  since then remain blank (unobserved).

Measured on the November 3–4 session from the local off-device backup
(1,355 committed segments, about 19 hours, ~1.5 GB compressed raw): a cold build
took 539–580 s on the development PC (about 0.4 s per recorded minute, roughly 1%
of one core in steady state) and produced 34 MB of derived data.

### Supervision and storage

`HeatmapDeriverSupervisor` starts `depth_heatmap.py follow` from the API lifespan
as a **separate, below-normal-priority child process**, because the API process
also hosts the trading worker. It runs only when this backend reads local
recordings (no `backend/storage/depth-history-source.json`) and
`TOPSIGNAL_DATABENTO_CACHE_DIR/depth-recordings-v1` exists;
`TOPSIGNAL_HEATMAP_DERIVER=off` disables it. The child exits when the backend
closes its stdin, is restarted with backoff if it fails, and its start/stop
cannot raise into startup or shutdown. A SQLite lease allows one active deriver
per owner without a lock file in the backed-up tree.

Derived data lives in `TOPSIGNAL_DATABENTO_CACHE_DIR/depth-heatmap-v1/<owner-hash>/`:
`state.sqlite3` (segment registry, checkpoints, status) and one
`sessions/<session-date>.sqlite3` per session (WAL, 512 MiB cap each). Bounds:
4 GiB per owner (about 115 sessions at the measured rate) and a 15 GiB free-disk
reserve, above the recorder's own 10 GiB floor so derivation always stops before
raw recording would. Reaching a bound stops derivation with status
`storage_review_required`, shown in the UI.

Retention (approved by the user on November 11, 2026): the deriver keeps the
newest **30** derived sessions, always including the current one, and every ten
minutes deletes older `sessions/YYYY-MM-DD.sqlite3` files (with their WAL/SHM)
plus their processed-segment and ended-capture bookkeeping rows. The last pass is
recorded under the `retention` status key. Only these rebuildable derived files
are eligible; raw recordings, their backups and every other store are never
touched. At about 30 MB per session this keeps the cache near 1 GB, well under
the 4 GiB bound. No Supabase table, migration or cloud write is involved.

Out-of-range or otherwise unexpected records mark that segment unreadable (an
explicit gap; the book is rebuilt afterwards) instead of crashing the deriver,
and any deriver error is recorded in its status. The inventory reads only newly
sealed manifests, so a capture running for days is not reparsed per segment, and
a retried segment replaces its earlier rows.

The derived cache is rebuilt from the raw segments, which the existing archive
backup already covers, so `backup_market_cache.py` now skips `depth-heatmap-v1`
(its live SQLite files would otherwise be re-copied in every daily backup). Until
the host's backup helper copy is updated, that helper snapshots these files with
its normal SQLite online-backup path.

### API

`GET /api/accounts/{account_id}/liquidity-heatmap?contract_id=…&start_ms=…&end_ms=…&tier=0|1|2[&summary=true]`
requires the signed-in owner of the account (checked before any file access,
then the DB transaction is released). The owner scope is derived from the
authenticated identity. Only the current session is served; windows are bounded
to 10 min (500 ms), 3 h (5 s) and 26 h (60 s). Responses carry columnar
little-endian arrays (base64), session coverage, timestamped gaps, deriver
status and `server_time_ms` (stamped when the response is sent, including for
briefly cached responses); 5 s and 60 s tiers also carry each level's observed
sample count, so a coarse cell's mean states its basis. At most 8,000 print
groups per response (largest kept, threshold reported). Large responses are
gzip-encoded. Errors return `{message, server_time_ms}`. Two reads run at a time
per backend; a short burst waits up to 15 s for a slot.

Measured reads on the derived session: a 2-minute 500 ms tile in 14 ms (53 KB
gzipped), a 30-minute 5 s tile in 50 ms (199 KB), a 4-hour 60 s tile in 123 ms,
and the whole-session navigator summary in 39 ms (12 KB).

On a development PC configured for the remote recording host, the endpoint
requires `heatmap_reader_path` (the installed `depth_heatmap.py`) and
`heatmap_cache_root` in the server-owned `depth-history-source.json` and calls
`depth_heatmap.py read` over the existing restricted SSH command. Until those are
installed the UI shows a "History unavailable" badge (the reason appears on
hover) and live depth still works. The original November 11 development check
uploaded no code; the November 13 hosted deployment reads local derived history.

## Display

- Time-follow and price-follow are independent. **Live** keeps the right edge at
  now; dragging, zooming, picking a gap or the session navigator pauses on the
  inspected window while data keeps arriving (a "Paused · time" badge, distinct
  from the feed badge). Auto-recentering moves the price range only when the mid
  leaves the middle 60% of the range; manual price pan/zoom turns it off until
  **Recenter**. Wider windows fit the observed price extent once.
- Chart height is `clamp(440px, 64vh, 780px)`; **Expand** fills the viewport
  (Esc exits). Resolution follows the window: 500 ms up to 10 min, 5 s up to
  3 h, 60 s beyond. A navigator strip shows the whole session's coverage, gaps,
  mid-price path and the visible window.
- Visible controls are only Live, Recenter, Expand and Settings. Settings holds
  time window / whole session, price range, auto-recenter, heat saturation (Auto
  or 10–500 contracts), print filters and size, the book profile, and a Help tab.
- Heat is linear in contracts up to an explicit saturation threshold shown on the
  legend (0 / half / ≥ threshold). Auto picks a rounded 97th percentile of visible
  sizes and changes only with the view or every 20 s while live. Blank = not
  observed, amber = interrupted depth, gray hatch = not recorded, blue hatch =
  recorder data not sealed yet. Quote lines have a dark halo; prints are sized by
  volume, and only the largest are shown when zoomed out.
- Crosshair with price/time tags and a tooltip; click or tap pins details
  (bid/ask size, or the bucket mean and sample count, gap reason, or print
  details). Keyboard on the focused chart: arrows move the inspection cursor,
  Shift+arrows pan, +/− zoom, Enter pins, L live, C recenter, Home session open,
  Esc clears. Ctrl/⌘ + scroll or pinch zooms time; add Shift for price.
- Coverage gaps (recorder outages, depth interruptions of 5 s or more, live trade
  feed interruptions not covered by the recorder) are listed with New York times
  and jump to the interval; brief interruptions are marked on the chart only.
  The old permanently latched "partial history" flag was removed.

### Clock alignment

Provider and recorder timestamps use server time. The page now aligns its live
sampler and freshness checks to the API's `server_time_ms` (midpoint of the
lowest-latency request; the first sample applies at once, later corrections slew
at 50 ms/s and never run backwards, except that a sample at least four times
faster replaces a first estimate that is off by more than a second). On November 11 the development PC's clock was
about 10.4 s ahead of peteamdpc, which made the previous heatmap label every live
sample "Stale depth" and draw prints ~10 s left of now.

## Isolation and lifecycle

The panel remounts on account, authentication scope, contract, tick size and demo
changes; the old history store is disposed and its requests aborted. History is
validated against the exact contract, tier and tick size before display. At the
6 p.m. rollover cached tiles are discarded and the new session loads. Demo mode
opens no stream and requests no history. Hiding the page closes the live trade
subscription (recorded history still covers that time when the recorder ran).

## Review pass

An independent review of the implementation led to these fixes: bounded ticks
and volumes plus broad error handling in the deriver; tile finality judged from
each tile's own response; live samples hidden only where a loaded tile holds
recorded data (no blank strip at the live edge); a history store lifecycle that
survives React StrictMode remounts; a 5 s refresh so paused views retry and keep
coverage current; incremental manifest inventory; no orphaned rows on retries;
per-level sample counts for coarse means; newest-first fallback mid price;
bid-and-ask levels within a bucket labelled as such; read-slot waiting and
transient "busy" handling; the deriver stop signal sent before the trading worker
stops and awaited last; and fresh server time on cached responses.

## Known limitations

- Production runs the deriver on the recording host. The supervised
  deriver uses background CPU/I/O priority on Windows and yields between segments
  while trade replay is active or the worker recorder reports backpressure.
  The page explicitly labels this pause; live depth continues. Catch-up time
  depends on archive contention and the research backlog. A development PC using
  the remote history source needs the reader installed.
- History is the current session only. Depth is an observed book (nearest 80
  levels per side); full-exchange completeness and provider sequence guarantees
  remain unverified. ProjectX buy/sell labels are not verified aggressor sides.
- Live samples are limited to this page's last 10 minutes; recorder data appears
  about a minute after it happens.

## Verification (November 11, 2026; not deployed)

- Backend: 29 heatmap tests (frame semantics vs. the live sampler, stale /
  disconnect / crossed / reset gaps, owner / contract / subscription / unsealed
  isolation, checkpoint resume identical to continuous processing, rebuilt-book
  disclosure, corrupt segments, session rollover and halt, coarse means, bounds,
  storage cap, single-deriver lease, API ownership and compression, remote-reader
  installation boundary, supervised child shutdown, out-of-range records,
  retries without orphans, incremental inventory, fresh server time on cached
  responses, keep-last-30 retention) plus the backup exclusion. Full offline backend suite: 3,322 passed,
  20 skipped.
- Frontend: Bot-page suite (566 tests) including new history/clock/viewport,
  renderer and panel tests (skewed browser clock, paused vs. disconnected, gap
  list, keyboard pinning, CSS-zoom pointer mapping, demo mode, contract/account
  isolation, StrictMode remount, retries while paused). TypeScript, ESLint on
  changed files and the production build pass.
- Browser: a temporary loopback harness (removed afterwards) served the real
  derived Nov 3–4 session through the production reader with a simulated clock,
  replayed the next recorded capture as the live feed and streamed recorded
  prints. Checked at desktop width, expanded, and 375 px phone width; reload
  restoration, disconnect/reconnect, all three tiers, whole-session view. Full
  canvas redraws took about 6–10 ms at 1,400 px (12 ms for the whole session,
  16 ms expanded at 3,150 px).
