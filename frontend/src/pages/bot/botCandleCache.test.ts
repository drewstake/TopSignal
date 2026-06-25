import { describe, expect, it } from "vitest";

import { buildBotCandleCacheKey, filterMarketCandlesForWindow, mergeMarketCandles, upsertMarketCandles } from "./botCandleCache";
import type { ProjectXMarketCandle } from "../../lib/types";

function candle(timestamp: string, close: number, overrides: Partial<ProjectXMarketCandle> = {}): ProjectXMarketCandle {
  return {
    id: null,
    contract_id: "CON.F.US.MNQ.M26",
    symbol: "MNQ",
    live: false,
    unit: "minute",
    unit_number: 5,
    timestamp,
    open: close - 0.25,
    high: close + 1,
    low: close - 1,
    close,
    volume: 100,
    is_partial: false,
    fetched_at: null,
    ...overrides,
  };
}

describe("buildBotCandleCacheKey", () => {
  it("normalizes equivalent bot market inputs to the same key", () => {
    expect(
      buildBotCandleCacheKey({
        contractId: " con.f.us.mnq.m26 ",
        symbol: " mnq ",
        live: false,
        unit: "minute",
        unitNumber: 5,
      }),
    ).toBe(
      buildBotCandleCacheKey({
        contractId: "CON.F.US.MNQ.M26",
        symbol: "MNQ",
        live: false,
        unit: "minute",
        unitNumber: 5,
      }),
    );
  });
});

describe("mergeMarketCandles", () => {
  it("keeps closed candles sorted, replaces duplicate timestamps, and trims to the limit", () => {
    const rows = mergeMarketCandles(
      [
        candle("2026-04-26T13:35:00Z", 100),
        candle("2026-04-26T13:40:00Z", 101, { is_partial: true }),
        candle("2026-04-26T13:45:00Z", 102),
      ],
      [
        candle("2026-04-26T13:35:00Z", 103),
        candle("2026-04-26T13:50:00Z", 104),
      ],
      3,
    );

    expect(rows.map((row) => row.timestamp)).toEqual([
      "2026-04-26T13:35:00Z",
      "2026-04-26T13:45:00Z",
      "2026-04-26T13:50:00Z",
    ]);
    expect(rows.map((row) => row.close)).toEqual([103, 102, 104]);
  });
});

describe("filterMarketCandlesForWindow", () => {
  it("drops cached candles outside the current chart query window", () => {
    const rows = filterMarketCandlesForWindow(
      [
        candle("2026-06-10T14:00:00Z", 100),
        candle("2026-06-25T14:00:00Z", 101),
        candle("2026-06-25T14:05:00Z", 102),
      ],
      {
        start: "2026-06-25T13:55:00.000Z",
        end: "2026-06-25T14:05:00.000Z",
      },
    );

    expect(rows.map((row) => row.timestamp)).toEqual(["2026-06-25T14:00:00Z", "2026-06-25T14:05:00Z"]);
  });

  it("returns no cached candles for an invalid query window", () => {
    const rows = filterMarketCandlesForWindow([candle("2026-06-25T14:00:00Z", 101)], {
      start: "2026-06-25T14:05:00.000Z",
      end: "2026-06-25T14:00:00.000Z",
    });

    expect(rows).toEqual([]);
  });
});

describe("upsertMarketCandles", () => {
  it("keeps partial candles and sorts by timestamp", () => {
    const rows = upsertMarketCandles(
      [candle("2026-04-26T13:40:00Z", 101)],
      [candle("2026-04-26T13:45:00Z", 102, { is_partial: true }), candle("2026-04-26T13:35:00Z", 100)],
    );

    expect(rows.map((row) => row.timestamp)).toEqual([
      "2026-04-26T13:35:00Z",
      "2026-04-26T13:40:00Z",
      "2026-04-26T13:45:00Z",
    ]);
    expect(rows[2].is_partial).toBe(true);
  });

  it("never replaces a closed candle with a partial one at the same timestamp", () => {
    const rows = upsertMarketCandles(
      [candle("2026-04-26T13:40:00Z", 101)],
      [candle("2026-04-26T13:40:00Z", 999, { is_partial: true })],
    );

    expect(rows).toHaveLength(1);
    expect(rows[0].close).toBe(101);
    expect(rows[0].is_partial).toBe(false);
  });

  it("replaces a partial candle with a closed one and respects the limit from the newest side", () => {
    const rows = upsertMarketCandles(
      [candle("2026-04-26T13:40:00Z", 101, { is_partial: true }), candle("2026-04-26T13:35:00Z", 100)],
      [candle("2026-04-26T13:40:00Z", 102), candle("2026-04-26T13:45:00Z", 103)],
      2,
    );

    expect(rows.map((row) => row.timestamp)).toEqual(["2026-04-26T13:40:00Z", "2026-04-26T13:45:00Z"]);
    expect(rows[0].close).toBe(102);
    expect(rows[0].is_partial).toBe(false);
  });
});
