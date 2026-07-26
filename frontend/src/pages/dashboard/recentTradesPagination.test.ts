import { describe, expect, it } from "vitest";

import {
  RECENT_TRADES_RENDER_PAGE_SIZE,
  getNextRecentTradesVisibleCount,
  getVisibleRecentTrades,
} from "./recentTradesPagination";

describe("recent trade incremental rendering", () => {
  it("mounts only the first page from a full 200-row response", () => {
    const trades = Array.from({ length: 200 }, (_, index) => index + 1);

    expect(getVisibleRecentTrades(trades, RECENT_TRADES_RENDER_PAGE_SIZE)).toEqual(trades.slice(0, 50));
  });

  it("advances in fixed pages and caps the final page at the response size", () => {
    expect(getNextRecentTradesVisibleCount(50, 125)).toBe(100);
    expect(getNextRecentTradesVisibleCount(100, 125)).toBe(125);
    expect(getNextRecentTradesVisibleCount(125, 125)).toBe(125);
  });

  it("keeps short responses fully visible", () => {
    const trades = [1, 2, 3];

    expect(getVisibleRecentTrades(trades, RECENT_TRADES_RENDER_PAGE_SIZE)).toEqual(trades);
  });
});
