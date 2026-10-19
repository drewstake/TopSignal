# TopSignal Get-Funded exchange

The dashboard opens at `/` and `/dashboard`. The playable local exchange is
available at `/simulation` inside the shared workspace navigation. All these
routes use `AccountAccess`, including client-side navigation and sign-in returns.
`npm run dev` starts the cloud backend and frontend together at `http://localhost:5173`.
The simulation engine remains local and
does not contact a broker or write historical data to Supabase. The explicit
account-workspace launch commands remain available.

## Operating configuration

`frontend/src/pages/simulation/funded/model.ts` contains the fixed operating
settings. `config-reference.json` translates both complete tables from the
supplied specification, including inactive legacy/progression metadata.
Configuration tests compare active values directly against that reference.
Only BX, BT, and BG are created. No preset selector, environment overrides,
speed selector, or structural-trader cohorts are active.

There are exactly 130 bots per market: 20 Maker, 24 Liquidity, 12 Momentum,
20 Value, 12 Reversion, 8 Protective, 24 Scalper, 4 Whale, and 6 Reserve.
Starting capital, size normalization, funding caps, observation policies,
maker quote retention, reserve depth, scalper exits, and whale parent/child
scheduling are translated from the current local source. Original initialization
draws are retained even when the funding policy overrides their values.

The clock runs quarter-second steps at 18 simulated seconds per wall second,
with 100 ms publications, at most 32 steps per pass, and an 8 ms frame budget.
Unprocessed time remains in the backlog. A Web Worker owns the state and runs
the finite opening auction plus all 7,200 warmup steps before enabling trading.
History is generated from these executions. Publications use `postMessage`
structured cloning; rendering, chart tools, and selection never mutate the
exchange or consume random draws. The worker also makes trading controls usable
while the engine catches up. Browser suspension can delay processing; backlog
is preserved rather than silently converted into invented candles.

## Trading and accounting

The player starts with $10,000 and a ten-contract account-wide capacity. Closed
balance determines capacity using cent rounding; open P&L does not raise it,
and the bots' $750 margin does not impose a second player gate. Positions,
working limits, and armed stops reserve worst-case directional exposure across
all three instruments. Reduce-only exits do not reserve new exposure.

Market orders consume actual eligible opposing quotes and cancel any unfilled
remainder. Limits either pass safeguard preflight and match/rest, or reject
before execution. Stop entries reserve capacity off-book and activate only
when a trade reaches the trigger. The book uses price/time priority, resting
execution prices, partial fills, cancellations, expiry, self-trade prevention,
funding checks, and position-linked reductions. Book display aggregates at most
five actual price levels on each side without adding liquidity.

Player aggressive execution uses the specified fixed 18-second window,
5,000-contract slices, 20,000-contract owner/direction allowance across markets,
100,000-contract market/direction cap, and 10% eligible non-player depth limit.
Depth is rechecked before execution. Every aggressor observes the directional
fair-value collar (20/60/20 ticks for BX/BT/BG). Cancelling and splitting orders
do not reset allowances. Queued owners receive rotating turns, up to 32 per
step, with reduce-only priority within an owner's queue.

Stop loss and take profit default to $500/$1,500 **for the requested total
quantity**. The ticket shows effective rounded ticks and dollars. Position
protection can be edited or disabled. Player targets are genuine resting or
queued reduce-only limits; valid engine-created protection bypasses player
volume allowances, but never the actual book or execution-price collar. Stops,
depletion, voluntary Close, Flatten, and reset all execute through this same
book. A pending exit cannot attach to a later position. Bot retries retain the
four-second deadline and can respond sooner to relevant liquidity/fair-value
changes.

Both sides settle the same quantity. Entries use weighted average prices;
opposing orders reduce before reversing. Realized P&L changes balance, and the
latest execution marks unrealized P&L and equity. No fees or commissions are
deducted. Equity depletion disables exposure and retains unfinished liquidation.
Restart cancels entries and waits for real closing fills before replacing the
world/account, then repeats the auction and full warmup. It never manufactures
a reset settlement.

## Retention and lifecycle

Each timeframe retains up to 1,200 execution-bearing candles, the public tape
and order archive retain 4,000 records, player fills retain 4,000, and each
account's closed-quantity history retains 60. Lifetime completed/win counters
and P&L are independent of display trimming. The UI shows recent order/fill
rows and the bounded closed trade history.

Only the seed is saved, under `topsignal.get-funded.v3.seed`. This is an
in-memory trading session: reloading starts a new account and world with that
seed; each tab has its own world. This limitation is stated in the interface.
Old v1/v2 simulation checkpoints are neither read nor overwritten. There are no
cloud simulation arrays, database migrations, Databento imports, or storage
cleanup. Saved cloud accounts, journals, trades, credentials, and results are
unmodified. The former simulator modules and tests remain as reference code,
but the routed trading page and worker have no active dependency on them.
The existing chart and its ongoing interaction/layout improvements are reused
through a generic read-only publication interface.

## Source provenance and scope

The source was available at the adjacent `Get-Funded/src` working directory.
`source-manifest.json` records SHA-256 hashes for the eleven inspected modules:
MarketConfig, MarketEngine, Participants, BotPopulation, PlayerExecution,
ProtectiveOrders, BotExitRetry, TradeProtection, ContractLimits,
ProgressionConfig, and TradeStats. These identify local working-copy inputs,
not a published Roblox game release.

No bot category is approximated by a random buy/sell placeholder. There are
three deliberate implementation differences from the source's surrounding
platform/execution bookkeeping:

- A triggered player stop retains its order ID and receipt instead of creating
  a separate child order. Its real fills, remainder, cancellation and capacity
  checks are preserved. Native player-order ID/hash parity is not claimed.
- Queue rotation advances after a blocked owner's turn as well as a fill,
  preventing 32 blocked owners from indefinitely starving another market.
- An extreme dollar target for a short can lie below zero. That engine-created
  target remains a genuine dormant limit (still subject to the collar), rather
  than failing target creation after accepting the ticket. Entry orders still
  require positive, tick-aligned prices within the 10% band.

Roblox networking, administrative synthetic settlement, old-world migrations,
multiplayer persistence, badge rendering, ranks, and milestone bonuses are not
part of this single-account implementation. Progression was optional in the
request; its exact supplied metadata is retained but grants are not applied.
Every new start therefore uses $10,000. No trading profit is attributed to
capital grants.

## Validation

The isolated native Luau comparison used the already-installed local Lune
0.10.5 runner. Only module imports were rewritten to relative file paths in
ignored `tmp/get-funded-reference`; no Studio world, DataStore, or original
source was modified. The checked-in fixture records seed 73521 at steps 0, 1,
320, and 7200. At **all four checkpoints**, TopSignal matches native RNG state,
order/trade counts, volume, trade hash, last prices, candle counts and rounded
fair values, with explicit tolerances for unrounded anchor/fair values.
At warmup completion both have 51,523 orders, 19,897 trades, 61,443 contracts of
volume and trade hash 1,244,650,447. This verifies that seed/horizon, not universal
cross-language or published-game parity.

Engine tests cover configuration and population, capital/normalization,
deterministic input replay, read-only publication, actual warmup, execution-only
candles, price/time priority, self-trades, expiry, partial fills, capacity,
safeguards, directional collars, P&L conservation, dollar protection, linked
targets, stop activation, queue fairness, close/flatten, depletion/reset, and
retention. UI tests cover buy/sell, tick normalization, validation, protection
editing, order cancellation, selection independence and operation controls.

The final broader frontend run covered 1,159 tests across 135 files: 1,155
passed immediately, with four failures confined to concurrently introduced
speed-selector expectations. After the user confirmed fixed 18× playback,
those expectations were corrected; the focused rerun passed all 14 tests
(trading UI, chart/clock independence, authentication). The account-workspace
rerun passed all 23 tests. The development/operations scripts passed all 40
tests. Production TypeScript/Vite build and scoped lint pass.

Browser checks used the actual local worker and book: a two-contract buy,
reducing sell, protection edit, close, working limit, armed stop, short entry,
Flatten, pause/step and same-seed restart. The first long closed in two fills
for a combined -$25; the short then flattened for -$12.50, leaving $9,962.50
and nine available contracts, with no remaining positions or orders. Restart
restored $10,000 and repeated the full warmup. The 390px responsive layout was
also inspected; document scroll width matched the viewport, while wide account
tables retained their own horizontal scrolling.

Reproduce with:

```powershell
npm --prefix frontend test -- src/pages/simulation/funded --maxWorkers=1
npm --prefix frontend run build
Push-Location frontend
npx eslint src/pages/simulation/funded
Pop-Location
npm run test:dev-scripts
```
