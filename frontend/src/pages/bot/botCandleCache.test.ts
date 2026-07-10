import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildBotCandleCacheKey,
  filterMarketCandlesForWindow,
  invalidateLegacyBotCandleCache,
  mergeMarketCandles,
  upsertMarketCandles,
  type BotCandleCacheKeyInput,
} from "./botCandleCache";
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
  afterEach(() => vi.unstubAllGlobals());

  it("normalizes equivalent bot market inputs to the same key", () => {
    expect(
      buildBotCandleCacheKey({
        userScope: "user:one",
        contractId: " con.f.us.mnq.m26 ",
        symbol: " mnq ",
        live: false,
        unit: "minute",
        unitNumber: 5,
      }),
    ).toBe(
      buildBotCandleCacheKey({
        userScope: "user:one",
        contractId: "CON.F.US.MNQ.M26",
        symbol: "MNQ",
        live: false,
        unit: "minute",
        unitNumber: 5,
      }),
    );
  });

  it("isolates persisted candles by authenticated user", () => {
    const input: BotCandleCacheKeyInput = {
      userScope: "user:one",
      contractId: "CON.F.US.MNQ.M26",
      symbol: "MNQ",
      live: false,
      unit: "minute",
      unitNumber: 5,
    };

    expect(buildBotCandleCacheKey({ ...input, userScope: "user:two" })).not.toBe(buildBotCandleCacheKey(input));
    expect(buildBotCandleCacheKey(input)).toContain("topsignal:bot-candles:v2:");
  });

  it("deletes the unscoped v1 entry instead of assigning it to the current user", () => {
    const removed: string[] = [];
    vi.stubGlobal("window", {
      localStorage: {
        removeItem: (key: string) => removed.push(key),
      },
    });

    invalidateLegacyBotCandleCache({
      userScope: "user:one",
      contractId: "CON.F.US.MNQ.M26",
      symbol: "MNQ",
      live: false,
      unit: "minute",
      unitNumber: 5,
    });

    expect(removed).toEqual(["topsignal:bot-candles:v1:practice%7CCON.F.US.MNQ.M26%7CMNQ%7Cminute%7C5"]);
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

  it("preserves the authoritative closed candle's raw provider fields", () => {
    const closed = candle("2026-04-26T13:40:00.000Z", 104, {
      open: 99,
      high: 108,
      low: 97,
      volume: 4321,
      fetched_at: "2026-04-26T13:45:02Z",
    });
    const rows = mergeMarketCandles(
      [candle("2026-04-26T13:40:00Z", 999, { is_partial: true })],
      [closed],
      10,
    );

    expect(rows).toHaveLength(1);
    expect(rows[0]).toBe(closed);
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

  it("deduplicates equivalent timestamps with closed-over-partial precedence", () => {
    const closed = candle("2026-06-25T14:00:00Z", 101);
    const rows = filterMarketCandlesForWindow(
      [closed, candle("2026-06-25T14:00:00.000Z", 999, { is_partial: true })],
      {
        start: "2026-06-25T13:55:00.000Z",
        end: "2026-06-25T14:05:00.000Z",
      },
    );

    expect(rows).toEqual([closed]);
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

  it.each([
    ["closed then partial", false],
    ["partial then closed", true],
  ])("keeps a closed duplicate when one input array contains %s", (_label, partialFirst) => {
    const closed = candle("2026-04-26T13:40:00Z", 101, {
      open: 97,
      high: 105,
      low: 96,
      volume: 234,
    });
    const partial = candle("2026-04-26T13:40:00.000Z", 999, {
      open: 998,
      high: 1_000,
      low: 997,
      is_partial: true,
    });

    const rows = upsertMarketCandles([], partialFirst ? [partial, closed] : [closed, partial]);

    expect(rows).toHaveLength(1);
    expect(rows[0]).toBe(closed);
  });
});
