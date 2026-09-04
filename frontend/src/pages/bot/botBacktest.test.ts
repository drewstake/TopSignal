import { describe, expect, it } from "vitest";

import type { BotBacktestDrawdownPoint, BotBacktestEquityPoint } from "../../lib/types";
import {
  BACKTEST_MAX_RENDERED_CHART_POINTS,
  buildBacktestChartPaths,
  buildBacktestPayload,
  describeBacktestError,
  describeBacktestProgress,
  validateBacktestForm,
  type BotBacktestFormState,
} from "./botBacktest";

const validForm: BotBacktestFormState = {
  strategyType: "sma_cross",
  instrument: "MNQ",
  startingBalance: "50000",
  commissionPerContract: "1.20",
  slippageTicks: "1",
};

describe("describeBacktestError", () => {
  it.each(["MNQ", "MES", "NQ", "ES"])("explains both required %s imports without treating staged candles as ready history", (root) => {
    const message = describeBacktestError(new Error(`databento_history_missing:${root}: import historical data before backtesting`));
    expect(message).toContain(`${root} replay history is not ready`);
    expect(message).toContain("both the Databento OHLCV-1m and matching Definition files");
    expect(message).toContain("OHLCV candles alone are not enough");
    expect(message).toContain("verify contract expirations and rollover");
    expect(message).not.toContain("is not installed on this computer");
    expect(message).toContain("separate ProjectX candles");
    expect(message).not.toContain("databento_history_missing");
  });

  it("distinguishes transport failures from actionable server errors", () => {
    expect(describeBacktestError(new TypeError("Failed to fetch"))).toContain("Check that the backend is running");
    expect(describeBacktestError(new Error("Historical data failed integrity validation."))).toBe("Historical data failed integrity validation.");
    expect(describeBacktestError(null)).toBe("Backtest failed.");
  });
});

describe("validateBacktestForm", () => {
  it("accepts full-history execution settings", () => {
    expect(validateBacktestForm(validForm)).toBeNull();
  });

  it("rejects malformed numeric settings", () => {
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

describe("buildBacktestPayload", () => {
  it("omits date bounds from the normal full-history request", () => {
    const payload = buildBacktestPayload(validForm);

    expect(payload).toEqual({
      strategy_type: "sma_cross",
      instrument: "MNQ",
      starting_balance: 50_000,
      commission_per_contract: 1.2,
      slippage_ticks: 1,
      force_close_at_end: true,
    });
    expect(payload).not.toHaveProperty("start");
    expect(payload).not.toHaveProperty("end");
  });
});

describe("describeBacktestProgress", () => {
  it("reports exact replay completion and remaining percentages", () => {
    expect(describeBacktestProgress({
      phase: "replaying",
      completed: 610,
      total: 1_000,
      percent: 61,
      remaining_percent: 39,
    })).toEqual({
      title: "Replaying closed candles — 61%",
      detail: "39% remaining · 610 of 1,000 candles",
      percent: 61,
    });
  });

  it("keeps provider history discovery indeterminate instead of inventing a percent", () => {
    expect(describeBacktestProgress(null).percent).toBeNull();
    expect(describeBacktestProgress({
      phase: "preparing",
      completed: null,
      total: null,
      percent: null,
      remaining_percent: null,
    }).title).toBe("Preparing candle history");
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
