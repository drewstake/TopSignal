# Bot-page live liquidity heatmap

The Liquidity Heatmap is an observation-only consumer of the Bot page's existing
`OrderBookStore`. `OrderBookPanel` continues to own the single depth subscription.
Heatmap controls never connect, disconnect, change ladder filters, or call bot
execution APIs. The strategy, signals, risk settings and broker connection are
unchanged.

## Data and rendering

- Sample current displayed resting bid/ask quantities twice per second, using
  local observation time. These are sampled book states, not executed trades or
  interval volume. Short-lived changes between samples may not appear.
- Keep a fixed 1,200-frame ring (up to ten minutes) with at most 80 nearest
  observed levels per side. Integer tick / quantity pairs use typed arrays;
  numeric level payload is bounded to about 3.1 MB per full ring. A paused view
  can retain one additional bounded set of frames.
- Three minutes and 80 ticks are the compact defaults (320px tall; 520px expanded). Canvas renders only
  visible levels, at the sample cadence, with pixel ratio capped at two.
  Logarithmic intensity uses a fixed quantity scale so a quantity's brightness
  does not change merely because an older frame expires.
- Both sides use a shared blue/cyan/cream/yellow/orange/red quantity gradient.
  Green best-bid and pink best-ask step traces stop at gaps or epoch changes.
  Prices are on the right; at canvas widths of at least 640px a separate book
  profile shows quantities from the sample covering the displayed right edge.
  It does not reuse stale depth across a gap. The profile uses a linear scale
  relative to that sample's largest level; heat colors retain their fixed scale.
- Tick size comes from the exact ProjectX contract record. A resolved chart
  contract's metadata is reused; otherwise a read-only contract lookup resolves
  it. Missing/invalid metadata does not fall back to an assumed MNQ tick size.
- Pause freezes the display while the same ring keeps collecting. Resume returns
  to current time. Follow-live also resets price centering; time and price zoom,
  earlier/later history, price pan, intensity and expand/collapse are local UI state.
- Changing contract, authenticated scope or demo mode remounts the view and clears
  history. Reloading or leaving the page loses this in-memory history.

## Integrity boundaries

The existing store applies provider snapshots and deltas, zero-size removals,
sequence validation and out-of-order rules. Heatmap-only continuity metadata
preserves resets, sequence gaps, coalesced snapshots, backwards snapshot epochs
and brief disconnections even when they occur between sampling callbacks.

Depth older than two seconds, future-dated depth beyond the existing one-second
tolerance, invalid price grids and crossed/locked books are unavailable frames.
A stalled sampling timer never backfills history. A cell spans at most 500 ms;
missing intervals are not interpolated. Timer jitter is handled with time buckets
to avoid dropping a normal 498–499 ms callback.

Blank cells mean unobserved, never an inferred zero. Amber bands mark invalid or
interrupted samples. One-sided depth remains explicitly incomplete, with no
invented midpoint. ProjectX does not provide a complete-depth certification in
this stream, so the panel always labels the ladder as observed/incomplete.
Trade bubbles use a separate executed-print stream described below. Resting
liquidity stays separate from executed volume, session delta and strategy decisions.

The page's existing visibility/subscription behavior is preserved. Hidden-tab
disconnects or timer suspension create gaps; collection is not guaranteed while
the browser suspends the page.

## Storage and verification

No persistence, database migration, Supabase writes, historical replay loading,
or deployment was added. The existing storage policy remains in force.

Local validation on November 3, 2026:

- Frontend production build and ESLint on changed source/test files.
- Bot-page regression suite, including focused heatmap tests for resets, gaps,
  coalescing, reconnects, contract changes, stale/future timestamps, removals,
  one-sided books, actual tick sizes, bounded retention, timer jitter, CSS-zoom
  hover coordinates, and pause while the shared ladder continues updating.
- Browser inspection of the actual Bot page at 1440, 768 and 390 pixel widths,
  using an isolated synthetic depth/candle fixture with account/network access
  disabled. Checked compact and expanded layouts, pause, follow-live and visible
  gaps. The temporary fixture is not shipped.
- Live depth on the existing release was inspected, but this development build
  was not authenticated against it: the worker restricts local sign-in to its
  existing preview origin. Connection/security settings were not changed.

Quantower-style rendering update: all 20 focused buffer, component and renderer
tests passed, along with TypeScript, the production build and targeted ESLint.
The actual panel was checked in an isolated synthetic-depth preview at desktop
and 390px phone widths. The preview was clearly labeled synthetic and removed
after verification; it was not connected to a broker. The depth sampling rate is unchanged.

## Executed-trade bubbles

- `/api/projectx/market-trades/stream` uses the existing authenticated user and
  credential resolution, then a separate reference-counted, user-scoped market
  hub session subscribing only to `SubscribeContractTrades`. It adds one display
  connection per viewing user, shared across that user's trade-overlay viewers.
  The depth and coalesced-price streams and all bot execution paths are unchanged.
  Provider connection limits still apply; subscription failure is shown in the UI.
- Only `GatewayTrade` price, timestamp, volume and type are accepted. Numeric
  ProjectX type 0 is displayed as provider-reported buy, 1 as sell, other/missing
  values as unknown. No quote, DOM change or account fill is converted to a print.
  This does not establish exchange-native aggressor provenance.
- The server batches up to 256 prints per SSE event at a 100ms cadence, with a
  512-event subscriber queue. Overflow reports lost prints. Trade states and
  gaps are separate from depth states; no trades are interpolated or backfilled.
  Research capture/persistence is disabled for this display session.
- Provider `tradeId`, when present, is deduplicated with a bounded 20,000-ID
  cache. Without a provider ID, every delivery is retained, even when identical
  to another print. Replayed provider deliveries without IDs remain ambiguous.
- In the browser, prints sharing a price tick, provider side and 250ms time bucket
  form one bubble; opposite sides are never netted. Volume is summed, print count
  is retained, and position uses the earliest provider timestamp in that group.
  Repeated transport IDs are ignored. Invalid/off-grid, expired or >1s future
  prints are omitted and flag partial history.
- Bubble radius scales with square-root volume, with minimum/maximum sizes for
  readability. Green = buy, pink = sell, gray = unknown. Large bubbles show
  quantity; hover shows exact quantity, print count, side, price and event time.
  Controls hide bubbles, filter minimum group volume and adjust bubble size.
- Browser history is capped at ten minutes / 20,000 groups, with at most 80,000
  delivery IDs. Capacity loss and interrupted feeds flag partial history. Pausing
  freezes copied depth and trade views while collection continues. Hiding the
  browser page closes the trade subscription; returning reconnects without
  filling the gap. Contract/account/authentication-scope changes discard the
  old view. Demo mode never opens a trade stream.

Bubble validation: focused frontend tests cover wire parsing/reconnect/demo
shutdown, contract isolation, grouping, identical prints, unknown side, memory
limits, filtering, hover geometry, pause/resume and hidden-page cleanup. Backend
tests cover tenant isolation, stale connection generations, subscription targets,
queue loss, batch limits, cancellation and authenticated route cleanup. Browser
visual checks use clearly labeled synthetic data with broker connections off;
live provider delivery has not been verified on this undeployed build.
