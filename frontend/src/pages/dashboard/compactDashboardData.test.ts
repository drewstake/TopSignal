import { afterEach, describe, expect, it, vi } from "vitest";

import type { AccountPnlCalendarDay, AccountSummary, AccountTrade } from "../../lib/types";
import {
  buildCompactAccountRequestPlan,
  buildCompactChartPoints,
  buildCompactDashboardScope,
  buildCompactDashboardScopes,
  combineCompactAccountDatasets,
  combineCompactSummaries,
  combineCompactTrades,
  COMPACT_RECENT_TRADES_LIMIT,
  refreshCompactCopyThenInvalidate,
} from "./compactDashboardData";

afterEach(() => {
  vi.useRealTimers();
});

function day(date: string, netPnl: number): AccountPnlCalendarDay {
  return {
    date,
    trade_count: 1,
    gross_pnl: netPnl,
    fees: 0,
    net_pnl: netPnl,
  };
}

describe("buildCompactChartPoints", () => {
  it("sorts trading days and builds a cumulative series", () => {
    expect(buildCompactChartPoints([
      day("2026-07-03", 25),
      day("2026-07-01", 100),
      day("2026-07-02", -40),
    ], 32)).toEqual([
      { date: "2026-07-01", dailyPnl: 100, cumulativePnl: 100 },
      { date: "2026-07-02", dailyPnl: -40, cumulativePnl: 60 },
      { date: "2026-07-03", dailyPnl: 25, cumulativePnl: 85 },
    ]);
  });

  it("caps visible points without resetting the cumulative total", () => {
    expect(buildCompactChartPoints([
      day("2026-07-01", 100),
      day("2026-07-02", -25),
      day("2026-07-03", 10),
    ], 2)).toEqual([
      { date: "2026-07-02", dailyPnl: -25, cumulativePnl: 75 },
      { date: "2026-07-03", dailyPnl: 10, cumulativePnl: 85 },
    ]);
  });

  it("normalizes non-finite input so chart geometry never receives NaN", () => {
    const result = buildCompactChartPoints([day("2026-07-01", Number.NaN)], 32);
    expect(result).toEqual([{ date: "2026-07-01", dailyPnl: 0, cumulativePnl: 0 }]);
  });
});

function summary(overrides: Partial<AccountSummary>): AccountSummary {
  return {
    realized_pnl: 0,
    gross_pnl: 0,
    fees: 0,
    net_pnl: 0,
    win_rate: 0,
    win_count: 0,
    loss_count: 0,
    breakeven_count: 0,
    profit_factor: 0,
    avg_win: 0,
    avg_loss: 0,
    avg_win_duration_minutes: 0,
    avg_loss_duration_minutes: 0,
    expectancy_per_trade: 0,
    tail_risk_5pct: 0,
    max_drawdown: 0,
    average_drawdown: 0,
    risk_drawdown_score: 0,
    max_drawdown_length_hours: 0,
    recovery_time_hours: 0,
    average_recovery_length_hours: 0,
    trade_count: 0,
    half_turn_count: 0,
    execution_count: 0,
    day_win_rate: 0,
    green_days: 0,
    red_days: 0,
    flat_days: 0,
    avg_trades_per_day: 0,
    active_days: 0,
    efficiency_per_hour: 0,
    profit_per_day: 0,
    averagePositionSize: 0,
    medianPositionSize: 0,
    tradeCountUsedForSizingStats: 0,
    avgPointGain: null,
    avgPointLoss: null,
    pointsBasisUsed: "auto",
    sizingBenchmark: {
      benchmarkMode: "fixed_average_size",
      benchmarkSizeUsed: 0,
      benchmarkGrossPnl: 0,
      benchmarkNetPnl: 0,
      benchmarkDiff: 0,
      benchmarkRatio: null,
      benchmarkLabel: "In Line With Benchmark",
    },
    ...overrides,
  };
}

function trade(id: number, accountId: number, timestamp: string): AccountTrade {
  return {
    id,
    account_id: accountId,
    contract_id: "MNQU6",
    symbol: "MNQ",
    side: "SELL",
    size: 1,
    price: 22000,
    timestamp,
    fees: 2,
    pnl: 50,
    order_id: `order-${id}`,
    source_trade_id: `trade-${id}`,
  };
}

describe("buildCompactDashboardScope", () => {
  const asOf = new Date("2026-07-26T16:00:00.000Z");

  it("uses an exact futures session for a selected calendar day", () => {
    const scope = buildCompactDashboardScope({
      range: "ALL",
      customRange: null,
      currentTradingDay: "2026-07-26",
      selectedDate: "2026-07-22",
      asOf,
    });

    expect(scope).toMatchObject({
      allTime: false,
      selectedDate: "2026-07-22",
      start: "2026-07-21T22:00:00.000Z",
      end: "2026-07-22T20:59:59.999999Z",
      startDate: "2026-07-22",
      endDate: "2026-07-22",
    });
  });

  it.each([
    ["1D", "2026-07-25T22:00:00.000Z"],
    ["1W", "2026-07-18T22:00:00.000Z"],
    ["1M", "2026-06-30T22:00:00.000Z"],
    ["6M", "2026-01-25T23:00:00.000Z"],
  ] as const)("aligns %s to a complete starting trading day", (range, expectedStart) => {
    const scope = buildCompactDashboardScope({
      range,
      customRange: null,
      currentTradingDay: "2026-07-26",
      selectedDate: null,
      asOf,
    });

    expect(scope.start).toBe(expectedStart);
    expect(scope.end).toBe(asOf.toISOString());
  });

  it("captures a fresh as-of boundary in the immutable scope key", () => {
    const input = {
      range: "1D" as const,
      customRange: null,
      currentTradingDay: "2026-07-26",
      selectedDate: null,
    };
    const first = buildCompactDashboardScope({ ...input, asOf });
    const second = buildCompactDashboardScope({ ...input, asOf: new Date(asOf.getTime() + 60_000) });

    expect(second.end).not.toBe(first.end);
    expect(second.key).not.toBe(first.key);
  });

  it("captures a new rolling request boundary after a reload", () => {
    vi.useFakeTimers();
    vi.setSystemTime(asOf);
    const input = {
      range: "1W" as const,
      customRange: null,
      currentTradingDay: "2026-07-26",
      selectedDate: null,
    };
    const first = buildCompactDashboardScopes({ ...input, asOf: new Date() });

    vi.advanceTimersByTime(90_000);
    const reloaded = buildCompactDashboardScopes({ ...input, asOf: new Date() });

    expect(reloaded.analysisScope.end).toBe("2026-07-26T16:01:30.000Z");
    expect(reloaded.analysisScope.key).not.toBe(first.analysisScope.key);
    expect(reloaded.calendarContextScope.end).toBe(reloaded.analysisScope.end);
  });

  it("keeps the calendar context navigable while analysis narrows to a selected day", () => {
    const scopes = buildCompactDashboardScopes({
      range: "1M",
      customRange: null,
      currentTradingDay: "2026-07-26",
      selectedDate: "2026-07-22",
      asOf,
    });

    expect(scopes.analysisScope).toMatchObject({
      selectedDate: "2026-07-22",
      startDate: "2026-07-22",
      endDate: "2026-07-22",
    });
    expect(scopes.calendarContextScope).toMatchObject({
      selectedDate: null,
      startDate: "2026-07-01",
      endDate: "2026-07-26",
    });
  });

  it("plans exactly three region-local requests and caps each account at seven recent trades", () => {
    const scopes = buildCompactDashboardScopes({
      range: "ALL",
      customRange: null,
      currentTradingDay: "2026-07-26",
      selectedDate: null,
      asOf,
    });
    const plan = buildCompactAccountRequestPlan(scopes);

    expect(Object.keys(plan)).toEqual(["summary", "calendar", "trades"]);
    expect(plan.trades).toMatchObject({
      limit: COMPACT_RECENT_TRADES_LIMIT,
      includeLifecycle: false,
    });
    expect(COMPACT_RECENT_TRADES_LIMIT).toBe(7);
  });

  it("does not refresh or narrow the calendar during ordinary day navigation", () => {
    const scopes = buildCompactDashboardScopes({
      range: "1M",
      customRange: null,
      currentTradingDay: "2026-07-26",
      selectedDate: "2026-07-22",
      asOf,
    });
    const plan = buildCompactAccountRequestPlan(scopes);

    expect(plan.summary.refresh).toBe(true);
    expect(plan.trades.refresh).toBe(true);
    expect(plan.calendar).toMatchObject({
      start: "2026-06-30T22:00:00.000Z",
      end: asOf.toISOString(),
      refresh: false,
    });
  });

  it("bypasses all endpoint caches on an explicit ALL reload", () => {
    const scopes = buildCompactDashboardScopes({
      range: "ALL",
      customRange: null,
      currentTradingDay: "2026-07-26",
      selectedDate: null,
      asOf,
    });
    const plan = buildCompactAccountRequestPlan(scopes, { forceRefresh: true });

    expect(plan.summary.refresh).toBe(true);
    expect(plan.calendar.refresh).toBe(true);
    expect(plan.trades.refresh).toBe(true);
    expect(plan.summary.start).toBeUndefined();
    expect(plan.calendar.start).toBeUndefined();
  });

  it("publishes a coordinated copy reload only after provider refresh settles", async () => {
    const events: string[] = [];
    let finishRefresh: (() => void) | undefined;
    const refresh = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finishRefresh = () => {
            events.push("refresh-complete");
            resolve();
          };
        }),
    );
    const invalidate = vi.fn(() => events.push("reload-published"));

    const pending = refreshCompactCopyThenInvalidate(refresh, invalidate);
    expect(refresh).toHaveBeenCalledOnce();
    expect(invalidate).not.toHaveBeenCalled();

    finishRefresh?.();
    await pending;
    expect(events).toEqual(["refresh-complete", "reload-published"]);
  });
});

describe("combineCompactAccountDatasets", () => {
  it("combines summary, calendar, and recent trades from the same accounts", () => {
    const result = combineCompactAccountDatasets([
      {
        summary: summary({
          gross_pnl: 150,
          realized_pnl: 150,
          fees: 10,
          net_pnl: 140,
          trade_count: 2,
          execution_count: 2,
          half_turn_count: 2,
          win_count: 1,
          loss_count: 1,
          win_rate: 50,
          avg_win: 100,
          avg_loss: -40,
          expectancy_per_trade: 70,
        }),
        days: [day("2026-07-22", 140)],
        trades: [trade(1, 10, "2026-07-22T14:00:00Z")],
      },
      {
        summary: summary({
          gross_pnl: 80,
          realized_pnl: 80,
          fees: 5,
          net_pnl: 75,
          trade_count: 1,
          execution_count: 1,
          half_turn_count: 1,
          win_count: 1,
          win_rate: 100,
          avg_win: 75,
          expectancy_per_trade: 75,
        }),
        days: [{ ...day("2026-07-22", 75), win_count: 1 }],
        trades: [trade(2, 11, "2026-07-22T15:00:00Z")],
      },
    ]);

    expect(result?.summary).toMatchObject({
      net_pnl: 215,
      trade_count: 3,
      win_count: 2,
      loss_count: 1,
      win_rate: 66.67,
      avg_win: 87.5,
      avg_loss: -40,
      expectancy_per_trade: 71.67,
      profit_factor: 4.375,
    });
    expect(result?.days).toEqual([
      expect.objectContaining({ date: "2026-07-22", trade_count: 2, net_pnl: 215 }),
    ]);
    expect(result?.trades.map((value) => value.id)).toEqual([2, 1]);
  });

  it("returns null when every copy-trade account is excluded", () => {
    expect(combineCompactAccountDatasets([])).toBeNull();
  });

  it("combines each Compact region independently after a follower endpoint fails", () => {
    const leaderSummary = summary({ net_pnl: 100, trade_count: 1, win_count: 1, avg_win: 100 });
    const followerSummary = summary({ net_pnl: 50, trade_count: 1, win_count: 1, avg_win: 50 });

    expect(combineCompactSummaries([leaderSummary, followerSummary])).toMatchObject({
      net_pnl: 150,
      trade_count: 2,
      win_count: 2,
    });
    expect(combineCompactTrades([
      [trade(1, 10, "2026-07-22T14:00:00Z")],
      // A failed follower trade endpoint contributes no group without removing its KPI summary.
    ], COMPACT_RECENT_TRADES_LIMIT).map((value) => value.id)).toEqual([1]);
  });
});
