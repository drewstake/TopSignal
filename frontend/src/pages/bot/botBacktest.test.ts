import { describe, expect, it } from "vitest";

import type { BotBacktestDrawdownPoint, BotBacktestEquityPoint } from "../../lib/types";
import { buildBacktestChartPaths, validateBacktestForm, type BotBacktestFormState } from "./botBacktest";

const validForm: BotBacktestFormState = {
  startDate: "2026-01-01",
  endDate: "2026-01-31",
  startingBalance: "50000",
  commissionPerContract: "1.20",
  slippageTicks: "1",
};

describe("validateBacktestForm", () => {
  it("accepts a bounded range and non-negative execution costs", () => {
    expect(validateBacktestForm(validForm)).toBeNull();
  });

  it("rejects reversed ranges and malformed numeric settings", () => {
    expect(validateBacktestForm({ ...validForm, startDate: "2026-02-01" })).toBe(
      "Start date must be on or before the end date.",
    );
    expect(validateBacktestForm({ ...validForm, startingBalance: "0" })).toBe(
      "Starting balance must be greater than zero.",
    );
    expect(validateBacktestForm({ ...validForm, commissionPerContract: "-1" })).toBe(
      "Commission must be zero or greater.",
    );
    expect(validateBacktestForm({ ...validForm, slippageTicks: "0.5" })).toBe(
      "Slippage must be a whole number of ticks, zero or greater.",
    );
  });
});

describe("buildBacktestChartPaths", () => {
  it("builds deterministic equity and drawdown paths", () => {
    const equity: BotBacktestEquityPoint[] = [
      { timestamp: "2026-01-01T14:30:00Z", equity: 50_000, realized_pnl: 0, unrealized_pnl: 0 },
      { timestamp: "2026-01-01T14:35:00Z", equity: 50_100, realized_pnl: 100, unrealized_pnl: 0 },
    ];
    const drawdown: BotBacktestDrawdownPoint[] = [
      { timestamp: "2026-01-01T14:30:00Z", equity: 50_000, drawdown_dollars: 0, drawdown_percent: 0 },
      { timestamp: "2026-01-01T14:35:00Z", equity: 50_100, drawdown_dollars: 1_000, drawdown_percent: 2 },
    ];

    const first = buildBacktestChartPaths(equity, drawdown);
    const second = buildBacktestChartPaths(equity, drawdown);

    expect(second).toEqual(first);
    expect(first.equity).toBe("M 0.00 146.00 L 720.00 14.00");
    expect(first.drawdown).toBe("M 0.00 184.00 L 720.00 242.00");
    expect(first.equityMin).toBe(50_000);
    expect(first.equityMax).toBe(50_100);
    expect(first.drawdownMax).toBe(2);
  });
});
