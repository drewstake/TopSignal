# Session order flow — local implementation, not deployed

The TopBot dashboard now places recorded session trade volume before its smaller
price-trend strip. It shows provider-reported buy/sell contracts, their difference,
classified-volume percentages, unknown volume, rolling 5/15/60-minute readings,
and observed first-to-last trade price movement. A 6 p.m. America/New_York boundary
uses the existing DST/weekend session helper. This is a session grouping rule,
not a new holiday calendar or permission to trade.

## ProjectX verification result

The official [market-hub reference](https://gateway.docs.projectx.com/docs/realtime/)
documents `GatewayTrade.type` as `TradeLogType`, with `Buy = 0` and `Sell = 1`.
It does not explicitly guarantee that these labels identify the initiating
aggressor. Account `GatewayUserTrade.side` and resting `GatewayDepth.type` are
different fields and are never used for these totals.

On November 3, a read-only audit compared eight checksum-verified raw segments
distributed across the recorded day with preceding, same-contract, uncrossed
quotes. Receipt and provider quote ages had to be within 500 ms and trade delivery
lag within 0–2 seconds. No future quotes were used. The reproducible result is
[projectx-trade-side-verification.json](audits/2026-11-03/projectx-trade-side-verification.json).

- 15,521 market prints: 7,596 type 0; 7,925 type 1.
- 214 lacked an eligible preceding quote; 1,989 were inside the spread.
- Of 13,318 outside-spread prints, 11,242 (84.4%) agreed with the expected side
  and 2,076 (15.6%) disagreed. The volume-weighted agreement was 84.8%.
- Delayed, coalesced or asynchronously delivered quotes could explain disagreements.
  This comparison cannot establish exchange aggressor semantics, full feed
  completeness, or a reliable exchange trade identity.

The UI explicitly calls these **reported** volumes. A subsequent live
[Quantower comparison](audits/2026-11-03/quantower-comparison.md) matched 1,907
prints, 961 buy contracts, 996 sell contracts and delta -35 across a 60-second
MNQZ6 sample. All 714 unique candidate pairs agreed on direction. Quantower was
set to its Data provider aggressor source. This validates sample consistency
with that platform, not independent exchange provenance or every future trade.

The dashboard labels the observed balance **bullish/bearish reported pressure**,
explicitly distinct from a price forecast, institutional activity or a verified
exchange-aggressor verdict. This interpretation uses delta's sign, not the bot's
DOM thresholds. Unknown volume that could erase/reverse the delta, or invalid
quantities whose volume cannot be bounded, produces an uncertain reading. Zero
known delta with no unknowns is balanced; missing data remains unavailable.
Unknown labels, invalid numeric values and bad provider timestamps cannot
contribute to the buy/sell split. Third-party SDK comments were not accepted as
provider guarantees. The API's `bias` and `absorption` fields remain unavailable
for a certified verdict; the UI derives only qualified reported pressure from
the visible volume totals.

The user's Level 2 subscription is useful and is already reflected in the raw
depth recording and DOM strategy. A second audit against prior DOM
`NewBestBid`/`NewBestAsk` events found 4,915 agreeing and 858 disagreeing
outside-spread prints (85.1% agreement); 754 were inside the spread and 8,994
failed freshness/availability checks. The same eight frozen segments contained
105,813 explicit Ask/Bid depth updates. Results are retained in
[projectx-dom-side-verification.json](audits/2026-11-03/projectx-dom-side-verification.json).
Level 2 therefore provides useful resting-liquidity observations, but this check
also cannot independently certify the trade label as an exchange aggressor flag.
Both timing ambiguity and limited snapshot/sequence guarantees remain relevant.

## Measurement and storage

`backend/tools/read_session_flow.py` reads only committed raw market segments
using the existing owner-scoped checksum verifier. Scope includes owner, exact
delivery contract, subscription mode and source. It never reads candle volume,
DOM quantities, account fills, unsealed files or another delivery's trades.
It excludes overlapping segments instead of double counting them. Legitimate
identical prints remain distinct; provider redelivery cannot be exchange-deduped
without a documented identity contract.

Reliable provider timestamps assign volume to one-minute buckets. Provider lag
outside 0–2 seconds leaves the trade unclassified at its receipt time. Totals end
at the last complete minute supported by sealed evidence, with a two-second
boundary allowance. Recent windows end at that displayed cutoff, not an invented
live clock. First/last trade prices use the selected observation interval; they
are not described as the session's exact opening price.

Per-segment minute summaries are derived into
`TOPSIGNAL_DATABENTO_CACHE_DIR/session-orderflow-v1/<owner-hash>/segments.sqlite3`.
The source root is normally `TOPSIGNAL_DATABENTO_CACHE_DIR/depth-recordings-v1`.
The SQLite page cap is 64 MiB per owner, with a 1 GiB free-space reserve; no raw
arrays, historical candles, schema migrations, cloud writes or automatic deletion
are introduced. A summary is normally a few KiB per segment; the database cap
bounds growth, and hitting it requires storage review. Original segments remain
immutable and must retain their existing off-device backup coverage.

Each request processes at most 128 MiB of previously uncached raw data and stops
starting new segments after twelve seconds. Cold history builds are explicitly
partial and continue on refresh; cached segments are not counted twice or decoded
again. The cache key includes source checksums, file identity, implementation
version and scope. New/raw-changed files are reverified. A small, owner-scoped
in-memory response cache bounds duplicate refresh work.

Missing session starts, gaps, reconnects, corrupt data, timestamp exclusions,
staleness and pending history remain visible. Even a continuous observed recording
does not certify full exchange coverage. Missing data never becomes neutral bias.

## Display and strategy boundary

The old EMA 9/21 calculation is unchanged. Only presentation changes: the strip
is named **Price trend by timeframe**, labels read Upward/Downward/Flat, and a
plain-language disclosure explains the weighted spread/slope score. The former
claim that both measures agree is removed because agreement is not required.
Mixed summaries identify the majority and opposing timeframe(s), excluding stale
or missing readings. These overlapping price views are not independent signals.

Recent pressure is compared with observed first-to-last price movement over the
same selected window, describing agreement, divergence or equal endpoint prices.
Divergence alone is not presented as confirmed absorption. At the next New York
session boundary, the previous session's summary is withdrawn immediately even
if the last response is less than two minutes old. Weekend/DST grouping matches
the backend session helper.

`GET /api/accounts/{account_id}/session-order-flow` checks owned ProjectX account
access before reading, releases the database transaction before filesystem/SSH
work, and returns `Cache-Control: no-store`. Client state resets on account,
authentication scope, contract and demo changes. No strategy module, bot settings,
thresholds, order placement, position management, risk gates or recording process
was changed. No production service was restarted and no code was deployed.

## Future deployment dependency (not performed)

For a remote recording host, the server-owned `depth-history-source.json` must
eventually supply `session_flow_reader_path` pointing to an installed standalone
`read_session_flow.py`, with its existing `read_depth_history.py`,
`read_depth_research.py` and their dependency bundle. Until that reviewed release
exists, the new endpoint returns an explicit reader-not-installed response; it
does not silently switch sources, upload code, or compute a fabricated session.
Existing source configuration has deliberately not been changed.

Confirm provider aggressor semantics with authoritative evidence before adding
an aggressive-flow verdict or absorption detector. That would be a separately
versioned measurement change; it must not change the trading strategy implicitly.
