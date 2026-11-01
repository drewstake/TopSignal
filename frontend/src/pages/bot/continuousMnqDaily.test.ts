import { expect, it } from "vitest";
import type { ProjectXMarketCandle } from "../../lib/types";
import type { ChartWindow } from "../backtesting/historyChartData";
import { mergeContinuousDaily, sameMnqDelivery } from "./continuousMnqDaily";

const history: ChartWindow = { symbol: "MNQ", source: "Databento", timeframe: "1d", coverage_start: "2025-09-01T22:00:00Z",
  coverage_end: "2026-10-23T22:00:00Z", start: "2025-09-01T00:00:00Z", end: "2026-10-31T00:00:00Z", total: 2, aggregate_size: 1, truncated: false,
  candles: [
    { time: Date.parse("2025-09-01T22:00:00Z") / 1000, end_time: Date.parse("2025-09-02T22:00:00Z") / 1000, open: 100, high: 105, low: 99, close: 104, volume: 1000, contract: "MNQU5", count: 1 },
    { time: Date.parse("2026-10-22T22:00:00Z") / 1000, end_time: Date.parse("2026-10-23T22:00:00Z") / 1000, open: 110, high: 115, low: 109, close: 114, volume: 2000, contract: "MNQZ6", count: 1 },
  ] };
const contract = "CON.F.US.MNQ.Z26";
const candle = (date: string, overrides: Partial<ProjectXMarketCandle> = {}): ProjectXMarketCandle => ({ id: null, contract_id: contract, symbol: "MNQ", unit: "day", unit_number: 1,
  timestamp: `${date}T00:00:00Z`, open: 120, high: 125, low: 119, close: 124, volume: 2500, live: false, is_partial: false, fetched_at: "2026-10-31T12:00:00Z", ...overrides });

it("aligns both sources by trading date and preserves archived delivery identity", () => {
  const rows = mergeContinuousDaily(history, [candle("2026-10-23"), candle("2026-10-26")], contract, Date.parse("2026-10-31T12:00:00Z"));
  expect(rows.map(row => [row.date, row.contract, row.source])).toEqual([
    ["2025-09-02", "MNQU5", "Databento"], ["2026-10-23", "MNQZ6", "Databento"], ["2026-10-26", "MNQZ26", "ProjectX"],
  ]);
  expect(rows[1].close).toBe(114);
});

it("rejects partial candles, different contracts, and unsupported rollover splices", () => {
  const rows = mergeContinuousDaily(history, [candle("2026-10-26", { is_partial: true }), candle("2026-10-27", { contract_id: "CON.F.US.MNQ.H27" }), candle("2026-10-31")], contract, Date.parse("2026-10-31T12:00:00Z"));
  expect(rows).toHaveLength(2);
  expect(mergeContinuousDaily(history, [candle("2026-10-26")], "CON.F.US.MNQ.H27")).toHaveLength(2);
  expect(sameMnqDelivery("MNQZ5", contract)).toBe(false);
  expect(() => mergeContinuousDaily({ ...history, aggregate_size: 2 }, [], contract)).toThrow("individual daily");
});
