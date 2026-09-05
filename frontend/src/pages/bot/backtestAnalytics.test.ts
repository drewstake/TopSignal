import { describe, expect, it } from "vitest";
import type { BotBacktestTrade } from "../../lib/types";
import { analyzeTrades, buildTradeAnalysis, formatHold, groupTrades, holdingMinutes, summarizeTrades, tradesToCsv } from "./backtestAnalytics";

function trade(overrides: Partial<BotBacktestTrade> = {}): BotBacktestTrade {
  return {
    id: 1, side: "long", quantity: 1, signal_timestamp: "2026-01-05T14:25:00Z",
    entry_timestamp: "2026-01-05T14:30:00Z", exit_timestamp: "2026-01-05T14:40:00Z",
    entry_price: 25000, exit_price: 25050, exit_reason: "take_profit",
    gross_pnl: 100, commission: 2.4, net_pnl: 97.6, mae: 10, mfe: 100, bars_held: 2,
    ...overrides,
  };
}

const sample = [
  trade(),
  trade({ id: 2, side: "short", gross_pnl: -50, net_pnl: -52.4, exit_reason: "stop_loss", exit_timestamp: "2026-01-05T14:50:00Z", bars_held: 4 }),
  trade({ id: 3, gross_pnl: 20, net_pnl: 17.6, exit_timestamp: "2026-01-05T15:00:00Z", bars_held: 6 }),
  trade({ id: 4, side: "short", gross_pnl: 2.4, net_pnl: 0, exit_timestamp: "2026-01-05T14:30:00Z", bars_held: 1 }),
];

describe("backtest trade analysis", () => {
  it("reconciles sides, net outcomes, costs and duration percentiles", () => {
    const result = buildTradeAnalysis(analyzeTrades(sample));
    expect(result.overall).toMatchObject({ count: 4, longs: 2, shorts: 2, winners: 2, losers: 1, breakevens: 1, winRate: 50, averageHold: 15, medianHold: 15, p90Hold: 27, averageBars: 3.25, feeErasedWinners: 1 });
    expect(result.overall.netPnl).toBeCloseTo(62.8);
    expect(result.overall.commission).toBeCloseTo(9.6);
    expect(result.overall.profitFactor).toBeCloseTo(115.2 / 52.4);
    expect(result.overall.expectancy).toBeCloseTo(15.7);
    expect(result.outcomes[0]).toMatchObject({ count: 2, averageHold: 20, medianHold: 20, p90Hold: 28 });
    expect(result.outcomes[1]).toMatchObject({ count: 1, averageHold: 20, medianHold: 20, p90Hold: 20 });
    expect(result.outcomes[2].averageHold).toBe(0);
    for (const groups of [result.directions, result.outcomes, result.byHour, result.byWeekday, result.byYear, result.byExit, result.byDuration]) {
      expect(groups.reduce((sum, row) => sum + row.count, 0)).toBe(4);
      expect(groups.reduce((sum, row) => sum + row.netPnl, 0)).toBeCloseTo(62.8);
    }
  });

  it("classifies a gross winner as a net loser after fees", () => {
    const analyzed = analyzeTrades([trade({ gross_pnl: 1, net_pnl: -1.4 })]);
    expect(analyzed[0].outcome).toBe("loser");
    expect(summarizeTrades(analyzed, "sample")).toMatchObject({ winners: 0, losers: 1, profitFactor: 0, feeErasedWinners: 1 });
  });

  it("uses ET entry hours in winter and summer, rather than UTC or exit hours", () => {
    const analyzed = analyzeTrades([
      trade(),
      trade({ id: 2, entry_timestamp: "2026-07-06T13:30:00Z", exit_timestamp: "2026-07-07T01:00:00Z" }),
    ]);
    expect(groupTrades(analyzed, "hour")).toHaveLength(1);
    expect(groupTrades(analyzed, "hour")[0]).toMatchObject({ label: "09:00–09:59", count: 2 });
    expect(groupTrades(analyzed, "weekday")[0].label).toBe("Monday");
    expect(groupTrades(analyzed, "year")[0].label).toBe("2026");
  });

  it("measures elapsed time across DST, weekends and same-candle exits", () => {
    expect(holdingMinutes(trade({ entry_timestamp: "2026-03-08T06:30:00Z", exit_timestamp: "2026-03-08T07:30:00Z" }))).toBe(60);
    expect(holdingMinutes(trade({ entry_timestamp: "2026-01-02T21:00:00Z", exit_timestamp: "2026-01-05T14:30:00Z", bars_held: 2 }))).toBe(3930);
    expect(holdingMinutes(sample[3])).toBe(0);
  });

  it("keeps invalid timestamps in counts and P&L but excludes them from timing", () => {
    const rows = analyzeTrades([trade({ entry_timestamp: "invalid" }), trade({ id: 2, exit_timestamp: "2026-01-05T14:29:00Z" })]);
    expect(summarizeTrades(rows, "bad times")).toMatchObject({ count: 2, timedCount: 0, averageHold: null, medianHold: null, p90Hold: null });
    expect(groupTrades(rows, "duration")[0]).toMatchObject({ label: "Unknown", count: 2 });
    expect(groupTrades(rows, "hour").some((row) => row.label === "Unknown")).toBe(true);
  });

  it("uses non-overlapping duration bins at their exact boundaries", () => {
    const rows = analyzeTrades([0, 5, 15, 30, 60, 120].map((minutes, id) => trade({ id, exit_timestamp: new Date(Date.parse("2026-01-05T14:30:00Z") + minutes * 60_000).toISOString() })));
    expect(groupTrades(rows, "duration").map((row) => [row.label, row.count])).toEqual([["Under 5m", 1], ["5–<15m", 1], ["15–<30m", 1], ["30–<60m", 1], ["1–<2h", 1], ["2h+", 1]]);
  });

  it("reports missing statistics as null, including profit factor without losses", () => {
    expect(summarizeTrades([], "empty")).toMatchObject({ count: 0, winRate: null, profitFactor: null, averageHold: null, averageMae: null, expectancy: null, largestWin: null, largestLoss: null });
    expect(summarizeTrades(analyzeTrades([trade()]), "winner").profitFactor).toBeNull();
    expect(summarizeTrades(analyzeTrades([trade({ mae: -10 })]), "legacy excursion").averageMae).toBe(10);
  });

  it("exports all trades with unrounded duration, costs, escaped cells and price points", () => {
    const rows = analyzeTrades([trade({ side: "short", entry_price: 25000, exit_price: 24950, exit_reason: 'take, "profit"' }), trade({ id: 2, exit_reason: "=formula()", net_pnl: -1 })]);
    const csv = tradesToCsv(rows);
    expect(csv.split("\r\n")).toHaveLength(3);
    expect(csv).toContain('"hold_minutes_approx"');
    expect(csv).toContain('"take, ""profit"""');
    expect(csv).toContain('"\'=formula()"');
    expect(csv).toContain('"-1"');
    expect(rows[0].points).toBe(50);
  });

  it.each([[0, "0m"], [25, "25m"], [90, "1.5h"], [2880, "2d"], [null, "—"]] as const)("formats %s minutes as %s", (value, expected) => {
    expect(formatHold(value)).toBe(expected);
  });
});
