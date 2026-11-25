import { describe, expect, it } from "vitest";

import type { AccountTrade } from "../../lib/types";
import { computePayoffExtras } from "./payoffExtras";

function buildTrade(id: number, pnl: number): AccountTrade {
  return {
    id,
    account_id: 1,
    contract_id: `C-${id}`,
    symbol: "ES",
    side: pnl >= 0 ? "SELL" : "BUY",
    size: 1,
    price: 100,
    timestamp: "2026-01-01T00:00:00.000Z",
    fees: 0,
    pnl,
    order_id: `O-${id}`,
    source_trade_id: null,
  };
}

describe("computePayoffExtras", () => {
  it("uses net losses for tails and the same measured winners for both sides of capture", () => {
    const result = computePayoffExtras({
      trades: [
        ...Array.from({ length: 5 }, (_, id) => ({ ...buildTrade(id, 1), fees: 3, mfe: 1 })),
        { ...buildTrade(10, 22), fees: 2, mfe: 40 }, buildTrade(11, 1000),
      ],
      avgWin: 510, avgLoss: -2, currentWinRate: 0, breakevenWinRate: null,
      canUseTradeDistribution: true, tradeDistributionReason: "",
    });
    expect(result.p95Loss.value).toBe(-2);
    expect(result.capture.value).toBe(0.5);
  });

  it("computes large loss rate from a 2x avg loss threshold", () => {
    const trades = [buildTrade(1, -50), buildTrade(2, -250), buildTrade(3, -300), buildTrade(4, 100), buildTrade(5, 200)];
    const result = computePayoffExtras({
      trades,
      avgWin: 150,
      avgLoss: -100,
      currentWinRate: 40,
      breakevenWinRate: 35,
      canUseTradeDistribution: true,
      tradeDistributionReason: "",
    });

    expect(result.largeLossThreshold.value).toBeCloseTo(200);
    expect(result.largeLossRate.value).toBeCloseTo(40);
  });

  it("computes p95 loss using loss magnitudes", () => {
    const trades = [
      buildTrade(1, -50),
      buildTrade(2, -80),
      buildTrade(3, -120),
      buildTrade(4, -200),
      buildTrade(5, -250),
      buildTrade(6, -400),
      buildTrade(7, 100),
    ];
    const result = computePayoffExtras({
      trades,
      avgWin: 100,
      avgLoss: -120,
      currentWinRate: 45,
      breakevenWinRate: 40,
      canUseTradeDistribution: true,
      tradeDistributionReason: "",
    });

    expect(result.p95Loss.value).toBeCloseTo(-362.5);
  });
});
