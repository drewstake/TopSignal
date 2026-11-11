import { expect, it } from "vitest";
import { HeatmapTradeBuffer, MAX_TRADE_BUBBLES } from "./heatmapTrades";
import { HEATMAP_HISTORY_MS } from "./liquidityHeatmap";
import type { MarketTradePrint } from "../../lib/marketTradeTypes";

const now = Date.parse("2026-11-03T15:00:00Z"), contract = "MNQ";
const trade = (id: string, values: Partial<MarketTradePrint> = {}): MarketTradePrint => ({
  id, timestamp: new Date(now).toISOString(), price: 20000, volume: 5, side: "buy", ...values,
});
const batch = (trades: MarketTradePrint[], contract_id = contract, dropped = 0) => ({ contract_id, trades, dropped });

it("groups by tick, side and 250ms; preserves identical prints with distinct IDs", () => {
  const buffer = new HeatmapTradeBuffer(contract, .25);
  buffer.add(batch([trade("1"), trade("2"), trade("2"), trade("3", { side: "sell" }), trade("4", { side: "unknown" }),
    trade("5", { timestamp: new Date(now + 250).toISOString() }), trade("6", { price: 20000.25 })]), now);
  const result = buffer.view(now);
  expect(result.bubbles).toHaveLength(5);
  expect(result.bubbles[0]).toMatchObject({ volume: 10, prints: 2, side: "buy" });
  expect(result.gaps).toEqual([]);
  expect(result.omitted).toBe(0);
});

it("keeps paused snapshots immutable while collecting more prints", () => {
  const buffer = new HeatmapTradeBuffer(contract, .25);
  buffer.add(batch([trade("1")]), now);
  const frozen = buffer.view(now);
  buffer.add(batch([trade("2")]), now);
  expect(frozen.bubbles[0].volume).toBe(5);
  expect(buffer.view(now).bubbles[0].volume).toBe(10);
});

it("rejects cross-contract, off-grid, expired and future prints; counts what was omitted", () => {
  const buffer = new HeatmapTradeBuffer(contract, .25);
  buffer.add(batch([trade("other")], "MES"), now);
  buffer.add(batch([trade("grid", { price: 20000.1 }), trade("future", { timestamp: new Date(now + 2000).toISOString() }),
    trade("old", { timestamp: new Date(now - HEATMAP_HISTORY_MS - 1).toISOString() }), trade("ok")]), now);
  expect(buffer.view(now)).toMatchObject({ omitted: 3, gaps: [], bubbles: [{ volume: 5 }] });
});

it("expires history without filling gaps and reports server loss with a timestamp", () => {
  const buffer = new HeatmapTradeBuffer(contract, .25);
  buffer.add(batch([trade("1")], contract, 3), now);
  expect(buffer.view(now).gaps).toEqual([{ start: now, end: now, reason: "3 prints dropped by the server queue" }]);
  expect(buffer.view(now + HEATMAP_HISTORY_MS + 1).bubbles).toHaveLength(0);
  // Old interruptions age out with the data instead of latching a permanent warning.
  expect(buffer.view(now + HEATMAP_HISTORY_MS + 1).gaps).toEqual([]);
});

it("records disconnect intervals and closes them when prints resume", () => {
  const buffer = new HeatmapTradeBuffer(contract, .25);
  buffer.markGap(now, "Trade feed disconnected");
  buffer.markGap(now + 500, "Trade feed disconnected");
  expect(buffer.view(now + 1000).gaps).toEqual([{ start: now, end: null, reason: "Trade feed disconnected" }]);
  buffer.add(batch([trade("1", { timestamp: new Date(now + 4000).toISOString() })]), now + 4000);
  expect(buffer.view(now + 4000).gaps).toEqual([{ start: now, end: now + 4000, reason: "Trade feed disconnected" }]);
});

it("caps memory and flags eviction under sustained high volume", () => {
  const buffer = new HeatmapTradeBuffer(contract, .25);
  buffer.add(batch(Array.from({ length: MAX_TRADE_BUBBLES + 2 }, (_, i) => trade(String(i), { price: 20000 + i * .25 }))), now);
  expect(buffer.view(now).bubbles).toHaveLength(MAX_TRADE_BUBBLES);
  expect(buffer.view(now).gaps.at(-1)).toMatchObject({ reason: "Oldest live prints evicted at the memory limit", start: now });
});
