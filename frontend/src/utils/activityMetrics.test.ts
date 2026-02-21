import { describe, expect, it } from "vitest";

import { computeActivityMetrics } from "./activityMetrics";

describe("computeActivityMetrics", () => {
  it("computes median daily trades across active days", () => {
    const result = computeActivityMetrics({
      totalTrades: 20,
      activeDays: 5,
      dailyPnlDays: [
        { date: "2026-01-02", trade_count: 8, gross_pnl: 0, fees: 0, net_pnl: 0 },
        { date: "2026-01-03", trade_count: 2, gross_pnl: 0, fees: 0, net_pnl: 0 },
        { date: "2026-01-04", trade_count: 6, gross_pnl: 0, fees: 0, net_pnl: 0 },
        { date: "2026-01-05", trade_count: 4, gross_pnl: 0, fees: 0, net_pnl: 0 },
      ],
      rangeStart: "2026-01-01T00:00:00.000Z",
      rangeEnd: "2026-01-07T23:59:59.999Z",
    });

    expect(result.medianTradesPerDay).toBe(5);
  });

  it("computes max daily trades from active day counts", () => {
    const result = computeActivityMetrics({
      totalTrades: 16,
      activeDays: 4,
      dailyPnlDays: [
        { date: "2026-01-02", trade_count: 3, gross_pnl: 0, fees: 0, net_pnl: 0 },
        { date: "2026-01-03", trade_count: 11, gross_pnl: 0, fees: 0, net_pnl: 0 },
        { date: "2026-01-04", trade_count: 1, gross_pnl: 0, fees: 0, net_pnl: 0 },
        { date: "2026-01-05", trade_count: 1, gross_pnl: 0, fees: 0, net_pnl: 0 },
      ],
      rangeStart: "2026-01-01T00:00:00.000Z",
      rangeEnd: "2026-01-07T23:59:59.999Z",
    });

    expect(result.maxTradesInDay).toBe(11);
  });

  it("computes weekly pacing metrics from a known date range", () => {
    const result = computeActivityMetrics({
      totalTrades: 140,
      activeDays: 10,
      dailyPnlDays: [
        { date: "2026-01-01", trade_count: 12, gross_pnl: 0, fees: 0, net_pnl: 0 },
        { date: "2026-01-14", trade_count: 14, gross_pnl: 0, fees: 0, net_pnl: 0 },
      ],
      rangeStart: "2026-01-01T00:00:00.000Z",
      rangeEnd: "2026-01-14T23:59:59.999Z",
    });

    expect(result.rangeDays).toBe(14);
    expect(result.tradesPerWeek).toBeCloseTo(70, 5);
    expect(result.activeDaysPerWeek).toBeCloseTo(5, 5);
  });
});

