import { describe, expect, it } from "vitest";

import { getTradeNetPnl } from "./tradePnl";

describe("getTradeNetPnl", () => {
  it("subtracts positive all-in fees from gross PnL", () => {
    expect(getTradeNetPnl({ pnl: 125, fees: 4.5 })).toBe(120.5);
    expect(getTradeNetPnl({ pnl: -125, fees: 4.5 })).toBe(-129.5);
  });

  it("treats missing, non-finite, and non-positive fees as zero", () => {
    expect(getTradeNetPnl({ pnl: 125, fees: null })).toBe(125);
    expect(getTradeNetPnl({ pnl: 125, fees: Number.NaN })).toBe(125);
    expect(getTradeNetPnl({ pnl: 125, fees: Number.POSITIVE_INFINITY })).toBe(125);
    expect(getTradeNetPnl({ pnl: 125, fees: -4.5 })).toBe(125);
  });

  it("returns null when gross or computed net PnL is not finite", () => {
    expect(getTradeNetPnl({ pnl: null, fees: 4.5 })).toBeNull();
    expect(getTradeNetPnl({ pnl: Number.NaN, fees: 4.5 })).toBeNull();
    expect(getTradeNetPnl({ pnl: Number.POSITIVE_INFINITY, fees: 4.5 })).toBeNull();
    expect(getTradeNetPnl({ pnl: -Number.MAX_VALUE, fees: Number.MAX_VALUE })).toBeNull();
  });
});
