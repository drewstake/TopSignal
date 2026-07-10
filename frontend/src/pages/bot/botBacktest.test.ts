import { describe, expect, it } from "vitest";

import type { BotBacktestDrawdownPoint, BotBacktestEquityPoint } from "../../lib/types";
import {
  BACKTEST_MAX_RENDERED_CHART_POINTS,
  buildBacktestChartPaths,
  validateBacktestForm,
  type BotBacktestFormState,
} from "./botBacktest";

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

  it("bounds SVG work for full-history results without discarding source counts or extrema", () => {
    const pointCount = 100_000;
    const equity = Array.from({ length: pointCount }, (_, index): BotBacktestEquityPoint => ({
      timestamp: new Date(Date.UTC(2020, 0, 1, 0, index)).toISOString(),
      equity: index === 54_321 ? 90_000 : 50_000 + (index % 100),
      realized_pnl: 0,
      unrealized_pnl: 0,
    }));
    const drawdown = equity.map((point, index): BotBacktestDrawdownPoint => ({
      timestamp: point.timestamp,
      equity: point.equity,
      drawdown_dollars: index === 12_345 ? 5_000 : index % 50,
      drawdown_percent: index === 12_345 ? 10 : (index % 50) / 10,
    }));

    const paths = buildBacktestChartPaths(equity, drawdown);

    expect(paths.equitySourcePointCount).toBe(pointCount);
    expect(paths.drawdownSourcePointCount).toBe(pointCount);
    expect(paths.equityRenderedPointCount).toBeLessThanOrEqual(BACKTEST_MAX_RENDERED_CHART_POINTS);
    expect(paths.drawdownRenderedPointCount).toBeLessThanOrEqual(BACKTEST_MAX_RENDERED_CHART_POINTS);
    expect(paths.equityMax).toBe(90_000);
    expect(paths.drawdownMax).toBe(10);
  });
});
