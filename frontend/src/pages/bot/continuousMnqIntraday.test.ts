import { afterEach, expect, it, vi } from "vitest";
import * as api from "../../lib/api";
import type { HistoricalCandle } from "../backtesting/historyChartData";
import { aggregateContinuousIntraday, continuousIntradayFrame, loadContinuousMnqIntraday } from "./continuousMnqIntraday";

const epoch = Date.parse("2026-05-05T00:00:00Z") / 1000;
const bar = (index: number): HistoricalCandle => ({ time: epoch + index * 300, end_time: epoch + (index + 1) * 300,
  open: 100 + index, high: 110 + index, low: 90 + index, close: 105 + index, volume: index + 1, contract: "MNQM6", count: 1 });
const query: api.CandleQuery = { contractId: "CON.F.US.MNQ.Z26", symbol: "F.US.MNQ", unit: "hour", unitNumber: 1,
  start: "2026-05-05T00:00:00Z", end: "2026-05-06T00:00:00Z", limit: 300 };
const history = (candles: HistoricalCandle[], extra = {}) => ({ timeframe: "5m", aggregate_size: 1,
  coverage_start: "2019-08-01T00:00:00Z", coverage_end: "2026-10-23T21:00:00Z", candles, truncated: false, ...extra });
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

it.each([["minute", 1, "1m"], ["minute", 5, "5m"], ["minute", 15, "5m"], ["hour", 1, "5m"], ["hour", 4, "4h"]] as const)(
  "routes %s %i to exact %s archive bars", (unit, unitNumber, timeframe) => {
    expect(continuousIntradayFrame({ ...query, unit, unitNumber }).timeframe).toBe(timeframe);
  });

it("builds true 15-minute and hourly OHLCV without skipping extrema or inventing session-gap bars", () => {
  const rows = Array.from({ length: 12 }, (_, i) => bar(i));
  expect(aggregateContinuousIntraday(rows, 3600, 12)).toEqual([
    { ...bar(0), end_time: epoch + 3600, high: 121, low: 90, close: 116, volume: 78 },
  ]);
  expect(aggregateContinuousIntraday(rows, 900, 3)).toHaveLength(4);
  expect(aggregateContinuousIntraday([bar(0), bar(12)], 900, 3).map(row => row.time)).toEqual([epoch, epoch + 3600]);
  expect(() => aggregateContinuousIntraday([bar(0), { ...bar(1), contract: "MNQU6" }], 3600, 12)).toThrow("rollover");
  expect(() => aggregateContinuousIntraday([{ ...bar(0), count: 50 }], 3600, 12)).toThrow("individual");
});

it("loads old hourly history from local data, preserving the historical delivery and excluding incomplete buckets", async () => {
  const archive = vi.spyOn(api, "requestJson").mockResolvedValue(history(Array.from({ length: 12 }, (_, i) => bar(i))));
  const broker = vi.spyOn(api.botsApi, "getCandles").mockResolvedValue([]);
  const rows = await loadContinuousMnqIntraday("old-hour-test", { ...query, end: "2026-05-05T01:30:00Z" });
  expect(rows).toHaveLength(1);
  expect(rows[0]).toMatchObject({ contract_id: "MNQM6", unit: "hour", unit_number: 1, volume: 78 });
  expect(archive).toHaveBeenCalledTimes(2);
  expect(broker).not.toHaveBeenCalled();
});

it("pages exact source bars when hourly requests exceed the API's 10,000-row limit", async () => {
  const raw = Array.from({ length: 24012 }, (_, i) => bar(i));
  const archive = vi.spyOn(api, "requestJson")
    .mockResolvedValueOnce(history([bar(0)]))
    .mockResolvedValueOnce(history(raw.slice(-10000), { truncated: true }))
    .mockResolvedValueOnce(history(raw.slice(-20000, -10000), { truncated: true }))
    .mockResolvedValueOnce(history(raw.slice(0, -20000)));
  vi.spyOn(api.botsApi, "getCandles").mockResolvedValue([]);
  const rows = await loadContinuousMnqIntraday("pagination-test", { ...query, limit: 2000,
    end: new Date((raw.at(-1)!.end_time) * 1000).toISOString() });
  expect(rows).toHaveLength(2000);
  expect(rows[0].timestamp).toBe("2026-05-05T01:00:00.000Z");
  expect(archive).toHaveBeenCalledTimes(4);
  expect(archive.mock.calls.slice(1).every(([, options]) => Number(options?.query?.limit) <= 10000)).toBe(true);
});

it("keeps the candle immediately before a pagination boundary", async () => {
  const archive = vi.spyOn(api, "requestJson").mockResolvedValue(history(Array.from({ length: 12 }, (_, i) => bar(i))));
  vi.spyOn(api.botsApi, "getCandles").mockResolvedValue([]);
  const rows = await loadContinuousMnqIntraday("boundary-test", { ...query, end: "2026-05-05T00:59:59.999Z" });
  expect(rows).toHaveLength(1);
  expect(rows[0]).toMatchObject({ timestamp: "2026-05-05T00:00:00.000Z", volume: 78 });
  expect(archive.mock.calls.at(-1)?.[1]?.query?.end).toBe("2026-05-05T01:00:00.000Z");
});

it("joins only the recent same-delivery suffix and never asks ProjectX for old contract history", async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-31T12:00:00Z"));
  vi.spyOn(api, "requestJson").mockResolvedValue(history([{ ...bar(0), contract: "MNQZ6" }]));
  const broker = vi.spyOn(api.botsApi, "getCandles").mockResolvedValue([]);
  await loadContinuousMnqIntraday("tail-test", { ...query, start: "2026-10-24T00:00:00Z", end: "2026-10-31T00:00:00Z" });
  expect(broker).toHaveBeenCalledWith(expect.objectContaining({ start: "2026-10-24T00:00:00.000Z", contractId: query.contractId }), expect.anything());
  vi.mocked(api.requestJson).mockResolvedValue(history([{ ...bar(0), contract: "MNQU6" }]));
  await expect(loadContinuousMnqIntraday("wrong-delivery-test", { ...query, end: "2026-10-31T00:00:00Z" })).rejects.toThrow("needs an update");
  expect(broker).toHaveBeenCalledTimes(1);
});
