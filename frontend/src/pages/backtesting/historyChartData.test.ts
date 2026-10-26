import { expect, it } from "vitest";
import { candleRequestWindow, lastCandleInRange, historyWindow, mergeHistoryDetail, logicalToTime, timeToLogical, type ChartWindow, type HistoricalCandle } from "./historyChartData";

const bar = (time: number, end: number): HistoricalCandle => ({ time, end_time: end, count: 1, open: 1, high: 2, low: 1, close: 2, volume: 1, contract: "MNQU6" });

it("replaces overlapping overview bars, retaining full timeline and exact detail", () => {
  const overview = [bar(100, 200), bar(200, 300), bar(300, 400), bar(400, 500)];
  const detail = { start: new Date(220000).toISOString(), end: new Date(380000).toISOString(), candles: [bar(220, 230), bar(230, 240), bar(360, 370)] } as ChartWindow;
  const merged = mergeHistoryDetail(overview, detail);
  expect(merged.map((row) => row.time)).toEqual([100, 220, 230, 360, 400]);
  expect(new Set(merged.map((row) => row.time)).size).toBe(merged.length);
});

it("preloads beside the visible window without leaving history bounds", () => {
  expect(historyWindow({ from: 200, to: 600 }, { from: 0, to: 1000 })).toEqual({ from: 100, to: 700 });
  expect(historyWindow({ from: 0, to: 1000 }, { from: 0, to: 1000 })).toEqual({ from: 0, to: 1000 });
});


it("round-trips fractional positions and whitespace across irregular time spacing", () => {
  const candles = [bar(100, 200), bar(200, 210), bar(500, 600)];
  for (const index of [-5, -0.25, 0, 0.5, 1.25, 2, 4.75]) {
    expect(timeToLogical(candles, logicalToTime(candles, index))).toBeCloseTo(index);
  }
  const single = [bar(100, 200)];
  expect(logicalToTime(single, -2)).toBe(-100);
  expect(timeToLogical(single, 350)).toBe(2.5);
});


it("selects only candles that intersect the visible window", () => {
  const candles = [bar(100, 160), bar(160, 220), bar(500, 560)];
  expect(lastCandleInRange(candles, { from: 100, to: 180 })).toBe(candles[1]);
  expect(lastCandleInRange(candles, { from: 220, to: 400 })).toBeUndefined();
  expect(lastCandleInRange(candles, { from: 0, to: 90 })).toBeUndefined();
});

it("never requests future history or more than the exact candle window", () => {
  const bounds = { from: 0, to: 1000000 };
  expect(candleRequestWindow({ from: 1100000, to: 1200000 }, bounds, 60, false)).toBeNull();
  const window = candleRequestWindow({ from: -10000, to: 2000000 }, bounds, 60, false)!;
  expect(window.to - window.from).toBe(6000 * 60);
  expect(window.from).toBeGreaterThanOrEqual(0);
  expect(window.to).toBeLessThanOrEqual(bounds.to);
});
