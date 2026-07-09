import { describe, expect, it } from "vitest";

import type { ProjectXMarketCandle } from "../../lib/types";
import { buildVolumeData } from "./botChartVolume";

function candle(overrides: Partial<ProjectXMarketCandle> = {}): ProjectXMarketCandle {
  return {
    id: null,
    contract_id: "CON.F.US.MNQ.M26",
    symbol: "MNQ",
    live: false,
    unit: "minute",
    unit_number: 5,
    timestamp: "2026-07-09T14:00:00Z",
    open: 100,
    high: 102,
    low: 99,
    close: 101,
    volume: 25,
    is_partial: false,
    fetched_at: null,
    ...overrides,
  };
}

describe("buildVolumeData", () => {
  it("sorts valid raw volume bars and colors them by their raw direction", () => {
    const data = buildVolumeData([
      candle({ timestamp: "2026-07-09T14:05:00Z", open: 105, close: 103, volume: 12 }),
      candle({ volume: 30 }),
      candle({ timestamp: "invalid" }),
      candle({ timestamp: "2026-07-09T14:10:00Z", volume: Number.NaN }),
    ]);

    expect(data.map((point) => point.value)).toEqual([30, 12]);
    expect(data[0].color).toContain("52,211,153");
    expect(data[1].color).toContain("251,113,133");
  });

  it("never lets a partial volume bar replace a closed bar at the same timestamp", () => {
    const data = buildVolumeData([
      candle({ volume: 80, is_partial: false }),
      candle({ volume: 99, is_partial: true }),
    ]);

    expect(data).toHaveLength(1);
    expect(data[0].value).toBe(80);
  });

  it("lets an authoritative closed bar replace an earlier partial bar", () => {
    const data = buildVolumeData([
      candle({ volume: 10, is_partial: true }),
      candle({ volume: 42, is_partial: false }),
    ]);

    expect(data[0].value).toBe(42);
  });
});
