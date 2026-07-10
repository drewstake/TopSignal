import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildBotCandleCacheKey,
  filterMarketCandlesForWindow,
  invalidateLegacyBotCandleCache,
  mergeMarketCandles,
  readBotCandleCache,
  resetBotCandleMemoryCacheForTests,
  upsertMarketCandles,
  writeBotCandleCache,
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

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>();

  get length(): number {
    return this.values.size;
  }

  clear(): void {
    this.values.clear();
  }

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  key(index: number): string | null {
    return Array.from(this.values.keys())[index] ?? null;
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

beforeEach(() => {
  resetBotCandleMemoryCacheForTests();
});

afterEach(() => {
  resetBotCandleMemoryCacheForTests();
  vi.unstubAllGlobals();
});

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

  it("isolates timeframe/cache-key switching", () => {
    const minuteKey = buildBotCandleCacheKey({
      userScope: "user:one",
      contractId: "CON.F.US.MNQ.M26",
      symbol: "MNQ",
      live: false,
      unit: "minute",
      unitNumber: 1,
    });
    const fiveMinuteKey = buildBotCandleCacheKey({
      userScope: "user:one",
      contractId: "CON.F.US.MNQ.M26",
      symbol: "MNQ",
      live: false,
      unit: "minute",
      unitNumber: 5,
    });

    writeBotCandleCache(minuteKey, [candle("2026-06-25T14:00:00Z", 101)], 300);
    writeBotCandleCache(fiveMinuteKey, [candle("2026-06-25T14:05:00Z", 105)], 300);

    expect(minuteKey).not.toBe(fiveMinuteKey);
    expect(readBotCandleCache(minuteKey)?.candles[0].close).toBe(101);
    expect(readBotCandleCache(fiveMinuteKey)?.candles[0].close).toBe(105);
  });
});

describe("cache persistence", () => {
  it("uses even a single valid cached candle and persists coverage metadata", () => {
    const storage = new MemoryStorage();
    vi.stubGlobal("window", { localStorage: storage });
    const key = "topsignal:bot-candles:v1:test";
    const savedAt = new Date("2026-06-25T14:06:00Z");

    writeBotCandleCache(
      key,
      [
        candle("2026-06-25T14:00:00Z", 101),
        candle("2026-06-25T14:05:00Z", 102, { is_partial: true }),
      ],
      300,
      {
        savedAt,
        coverage: {
          start: "2026-06-25T13:00:00Z",
          end: "2026-06-25T14:06:00Z",
        },
      },
    );

    resetBotCandleMemoryCacheForTests();
    const entry = readBotCandleCache(key);
    expect(entry?.candles).toHaveLength(1);
    expect(entry?.candles[0].is_partial).toBe(false);
    expect(entry?.savedAt?.toISOString()).toBe(savedAt.toISOString());
    expect(entry?.coverage).toEqual({
      start: "2026-06-25T13:00:00.000Z",
      end: "2026-06-25T14:06:00.000Z",
    });
  });

  it("reads legacy v1 payloads without coverage", () => {
    const storage = new MemoryStorage();
    vi.stubGlobal("window", { localStorage: storage });
    const key = "topsignal:bot-candles:v1:legacy";
    storage.setItem(
      key,
      JSON.stringify({
        savedAt: "2026-06-25T14:06:00Z",
        candles: [candle("2026-06-25T14:00:00Z", 101)],
      }),
    );

    expect(readBotCandleCache(key)).toMatchObject({
      coverage: null,
      candles: [{ close: 101 }],
    });
  });

  it("serves repeated reads from memory before localStorage", () => {
    const storage = new MemoryStorage();
    vi.stubGlobal("window", { localStorage: storage });
    const key = "topsignal:bot-candles:v1:memory";
    writeBotCandleCache(key, [candle("2026-06-25T14:00:00Z", 101)], 300);
    storage.setItem(key, "not-json");

    expect(readBotCandleCache(key)?.candles[0].close).toBe(101);
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

  it("returns the existing array identity for a semantic no-op merge", () => {
    const existing = [
      candle("2026-04-26T13:35:00Z", 100),
      candle("2026-04-26T13:40:00Z", 101),
    ];
    const equivalentTail = candle("2026-04-26T13:40:00.000Z", 101, {
      id: 42,
      fetched_at: "2026-04-26T13:45:02Z",
    });

    expect(mergeMarketCandles(existing, [equivalentTail], 300)).toBe(existing);
    expect(upsertMarketCandles(existing, [equivalentTail], 300)).toBe(existing);
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
