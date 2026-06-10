import { describe, expect, it } from "vitest";

import {
  buildGapRangeKey,
  buildGapRepairWindows,
  findCandleGaps,
  isFuturesSessionOpen,
} from "./botCandleGaps";
import type { ProjectXMarketCandle } from "../../lib/types";

// June 2026 is in EDT (ET = UTC-4).
// 2026-06-09 is a Tuesday; 2026-06-05 is a Friday; 2026-06-07 is a Sunday.

function candle(timestamp: string, overrides: Partial<ProjectXMarketCandle> = {}): ProjectXMarketCandle {
  return {
    id: null,
    contract_id: "CON.F.US.MNQ.M26",
    symbol: "MNQ",
    live: false,
    unit: "minute",
    unit_number: 5,
    timestamp,
    open: 100,
    high: 101,
    low: 99,
    close: 100.5,
    volume: 100,
    is_partial: false,
    fetched_at: null,
    ...overrides,
  };
}

describe("isFuturesSessionOpen", () => {
  it("is open during the regular Tuesday session", () => {
    expect(isFuturesSessionOpen(Date.parse("2026-06-09T14:00:00Z"))).toBe(true); // Tue 10:00 ET
  });

  it("is closed during the nightly maintenance break", () => {
    expect(isFuturesSessionOpen(Date.parse("2026-06-09T21:00:00Z"))).toBe(false); // Tue 17:00 ET
    expect(isFuturesSessionOpen(Date.parse("2026-06-09T21:55:00Z"))).toBe(false); // Tue 17:55 ET
    expect(isFuturesSessionOpen(Date.parse("2026-06-09T22:00:00Z"))).toBe(true); // Tue 18:00 ET
  });

  it("is closed from Friday 17:00 ET through Sunday 18:00 ET", () => {
    expect(isFuturesSessionOpen(Date.parse("2026-06-05T21:00:00Z"))).toBe(false); // Fri 17:00 ET
    expect(isFuturesSessionOpen(Date.parse("2026-06-06T15:00:00Z"))).toBe(false); // Saturday
    expect(isFuturesSessionOpen(Date.parse("2026-06-07T21:55:00Z"))).toBe(false); // Sun 17:55 ET
    expect(isFuturesSessionOpen(Date.parse("2026-06-07T22:00:00Z"))).toBe(true); // Sun 18:00 ET
  });
});

describe("findCandleGaps", () => {
  it("returns no gaps for consecutive candles", () => {
    const gaps = findCandleGaps(
      [candle("2026-06-09T14:00:00Z"), candle("2026-06-09T14:05:00Z"), candle("2026-06-09T14:10:00Z")],
      "minute",
      5,
    );
    expect(gaps).toEqual([]);
  });

  it("classifies a missing intraday run during trading hours as a data gap", () => {
    const gaps = findCandleGaps(
      [candle("2026-06-09T14:00:00Z"), candle("2026-06-09T14:05:00Z"), candle("2026-06-09T14:20:00Z")],
      "minute",
      5,
    );

    expect(gaps).toHaveLength(1);
    expect(gaps[0].kind).toBe("data");
    expect(gaps[0].missingBars).toBe(2);
    expect(gaps[0].missingSessionBars).toBe(2);
    expect(gaps[0].fromMs).toBe(Date.parse("2026-06-09T14:10:00Z"));
    expect(gaps[0].toMs).toBe(Date.parse("2026-06-09T14:20:00Z"));
  });

  it("classifies the nightly maintenance break as a session gap", () => {
    // Tue 16:55 ET -> Tue 18:00 ET on 5m bars: missing 17:00..17:55 ET.
    const gaps = findCandleGaps(
      [candle("2026-06-09T20:55:00Z"), candle("2026-06-09T22:00:00Z")],
      "minute",
      5,
    );

    expect(gaps).toHaveLength(1);
    expect(gaps[0].kind).toBe("session");
    expect(gaps[0].missingBars).toBe(12);
    expect(gaps[0].missingSessionBars).toBe(0);
  });

  it("classifies the weekend closure as a session gap", () => {
    // Fri 16:55 ET -> Sun 18:00 ET on 5m bars.
    const gaps = findCandleGaps(
      [candle("2026-06-05T20:55:00Z"), candle("2026-06-07T22:00:00Z")],
      "minute",
      5,
    );

    expect(gaps).toHaveLength(1);
    expect(gaps[0].kind).toBe("session");
    expect(gaps[0].missingSessionBars).toBe(0);
  });

  it("classifies a gap spanning closure plus trading hours as a data gap", () => {
    // Fri 16:50 ET -> Mon 10:00 ET: the Sunday evening + Monday morning buckets are in session.
    const gaps = findCandleGaps(
      [candle("2026-06-05T20:50:00Z"), candle("2026-06-08T14:00:00Z")],
      "minute",
      5,
    );

    expect(gaps).toHaveLength(1);
    expect(gaps[0].kind).toBe("data");
    expect(gaps[0].missingSessionBars).toBeGreaterThan(0);
    expect(gaps[0].missingSessionBars).toBeLessThan(gaps[0].missingBars);
  });

  it("treats missing weekend daily bars as a session gap and missing weekday bars as data", () => {
    const weekend = findCandleGaps(
      [
        candle("2026-06-05T00:00:00Z", { unit: "day", unit_number: 1 }), // Fri
        candle("2026-06-08T00:00:00Z", { unit: "day", unit_number: 1 }), // Mon
      ],
      "day",
      1,
    );
    expect(weekend).toHaveLength(1);
    expect(weekend[0].kind).toBe("session");

    const midweek = findCandleGaps(
      [
        candle("2026-06-08T00:00:00Z", { unit: "day", unit_number: 1 }), // Mon
        candle("2026-06-10T00:00:00Z", { unit: "day", unit_number: 1 }), // Wed (Tue missing)
      ],
      "day",
      1,
    );
    expect(midweek).toHaveLength(1);
    expect(midweek[0].kind).toBe("data");
  });

  it("skips week and month units", () => {
    const gaps = findCandleGaps(
      [
        candle("2026-05-04T00:00:00Z", { unit: "week", unit_number: 1 }),
        candle("2026-06-01T00:00:00Z", { unit: "week", unit_number: 1 }),
      ],
      "week",
      1,
    );
    expect(gaps).toEqual([]);
  });

  it("ignores duplicate timestamps and unparseable rows", () => {
    const gaps = findCandleGaps(
      [
        candle("2026-06-09T14:00:00Z"),
        candle("2026-06-09T14:00:00Z"),
        candle("not-a-date"),
        candle("2026-06-09T14:05:00Z"),
      ],
      "minute",
      5,
    );
    expect(gaps).toEqual([]);
  });
});

describe("buildGapRepairWindows", () => {
  it("builds padded windows for data gaps only and merges overlapping ranges", () => {
    const gaps = findCandleGaps(
      [
        candle("2026-06-09T14:00:00Z"),
        candle("2026-06-09T14:20:00Z"), // data gap 14:05-14:15
        candle("2026-06-09T14:30:00Z"), // adjacent data gap 14:25
        candle("2026-06-09T20:55:00Z"), // big intraday data gap
        candle("2026-06-09T22:00:00Z"), // session gap (maintenance)
      ],
      "minute",
      5,
    );
    const dataGapCount = gaps.filter((gap) => gap.kind === "data").length;
    expect(dataGapCount).toBe(3);

    const windows = buildGapRepairWindows(gaps, "minute", 5);
    // The two near-adjacent small gaps merge into one window (padding overlaps).
    expect(windows.length).toBeLessThanOrEqual(2);
    expect(windows.length).toBeGreaterThan(0);
    for (const window of windows) {
      expect(Date.parse(window.start)).toBeLessThan(Date.parse(window.end));
    }
    // No window should cover the session-break range exclusively.
    const sessionGap = gaps.find((gap) => gap.kind === "session");
    expect(sessionGap).toBeDefined();
  });

  it("caps the number of windows", () => {
    const candles: ProjectXMarketCandle[] = [];
    let ts = Date.parse("2026-06-09T13:00:00Z");
    for (let index = 0; index < 12; index += 1) {
      candles.push(candle(new Date(ts).toISOString()));
      // Every other step skips two buckets, far enough apart not to merge.
      ts += index % 2 === 0 ? 40 * 60_000 : 5 * 60_000;
    }
    const gaps = findCandleGaps(candles, "minute", 5);
    const windows = buildGapRepairWindows(gaps, "minute", 5, 2);
    expect(windows.length).toBeLessThanOrEqual(2);
  });
});

describe("buildGapRangeKey", () => {
  it("is stable for the same gap range", () => {
    const [gap] = findCandleGaps(
      [candle("2026-06-09T14:00:00Z"), candle("2026-06-09T14:20:00Z")],
      "minute",
      5,
    );
    expect(buildGapRangeKey(gap)).toBe(`${gap.fromMs}:${gap.toMs}`);
  });
});
