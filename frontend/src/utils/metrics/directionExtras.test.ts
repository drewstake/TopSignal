import { describe, expect, it } from "vitest";

import type { AccountTrade } from "../../lib/types";
import { buildDirectionSamples, computeDirectionExtras } from "./directionExtras";

function buildTrade(id: number, side: "BUY" | "SELL", pnl: number): AccountTrade {
  return {
    id,
    account_id: 1,
    contract_id: `C-${id}`,
    symbol: "NQ",
    side,
    size: 1,
    price: 100,
    timestamp: "2026-01-01T00:00:00.000Z",
    fees: 0,
    pnl,
    order_id: `O-${id}`,
    source_trade_id: null,
  };
}

describe("computeDirectionExtras", () => {
  it("computes expectancy and profit factor by side", () => {
    const trades: AccountTrade[] = [
      buildTrade(1, "SELL", 100),
      buildTrade(2, "SELL", -50),
      buildTrade(3, "SELL", 150),
      buildTrade(4, "BUY", 80),
      buildTrade(5, "BUY", -40),
      buildTrade(6, "BUY", -20),
    ];
    const samples = buildDirectionSamples(trades);
    const result = computeDirectionExtras(samples, 220);

    expect(result.long.expectancy.value).toBeCloseTo(66.666, 2);
    expect(result.short.expectancy.value).toBeCloseTo(6.666, 2);
    expect(result.long.profitFactor.value).toBeCloseTo(5);
    expect(result.short.profitFactor.value).toBeCloseTo(1.333, 2);
  });
});
