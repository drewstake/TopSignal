import { describe, expect, it } from "vitest";
import { buildMarketExplanation } from "./botMarketExplanation";
import { buildMarketContext, type MarketContext } from "./botMarketContext";
import type { ProjectXMarketCandle } from "../../lib/types";

function context(overrides: Partial<MarketContext> = {}): MarketContext {
  const rows: ProjectXMarketCandle[] = Array.from({ length: 60 }, (_, index) => ({
    id: null, contract_id: "MNQ-test", symbol: "MNQ", live: false, unit: "minute", unit_number: 5,
    timestamp: new Date(Date.parse("2026-09-08T10:00:00Z") + index * 300_000).toISOString(),
    open: 100 + index, high: 102 + index, low: 99 + index, close: 101 + index,
    volume: 100, is_partial: false, fetched_at: null,
  }));
  const value = buildMarketContext({ contractKey: "MNQ-test:minute:5", unit: "minute", unitNumber: 5,
    candles: rows, lastPrice: 999, updatedAt: "2026-09-08T15:00:00Z" }, Date.parse("2026-09-08T15:00:00Z"))!;
  return { ...value, ...overrides };
}

describe("buildMarketExplanation", () => {
  it("explains bullish evidence and explicitly calls out opposing VWAP and higher timeframe direction", () => {
    const read = buildMarketExplanation(context({
      trend: { label: "5m", direction: "bullish", strength: 0.6, bars: 60 },
      trends: [{ label: "5m", direction: "bullish", strength: 0.6, bars: 60 },
        { label: "1H", direction: "bearish", strength: 0.5, bars: 40 }],
      vwap: 170, vwapLocation: "below", nearestSupport: 140, nearestResistance: 180,
    }));
    expect(read.headline).toBe("5m trend leans bullish");
    expect(read.supporting.join(" ")).toContain("moving-average");
    expect(read.conflicting.join(" ")).toContain("below VWAP (170.00)");
    expect(read.conflicting.join(" ")).toContain("1H is bearish");
    expect(read.changes.join(" ")).toContain("above 180.00");
    expect(read.changes.join(" ")).toContain("below 140.00");
  });

  it("does not manufacture a directional call or change level for neutral, missing inputs", () => {
    const read = buildMarketExplanation(context({ trend: { label: "5m", direction: "neutral", strength: 0.1, bars: 60 },
      trends: [], vwap: null, vwapLocation: "unavailable", relativeVolume: null, nearestSupport: null, nearestResistance: null }));
    expect(read.headline).toBe("No clear 5m direction");
    expect(read.changes).toEqual([]);
    expect(read.summary).toContain("volume comparison unavailable");
    expect(JSON.stringify(read)).not.toMatch(/NaN|0\.00|probability|buy now|sell now/i);
  });

  it("prioritizes stale data and gaps over indicator agreement", () => {
    const value = context();
    const read = buildMarketExplanation({ ...value, provenance: { ...value.provenance, isStale: true, detectedGapCount: 2 } });
    expect(read.headline).toContain("stale data");
    expect(read.conflicting[0]).toContain("stale");
    expect(read.conflicting[1]).toContain("Missing candles");
  });

  it("describes bearish agreement without treating low volume as bullish evidence", () => {
    const read = buildMarketExplanation(context({ trend: { label: "5m", direction: "bearish", strength: 0.6, bars: 60 },
      trends: [], vwap: 170, vwapLocation: "below", relativeVolume: 0.5 }));
    expect(read.headline).toBe("5m trend leans bearish");
    expect(read.supporting.join(" ")).toContain("below VWAP");
    expect(read.conflicting.join(" ")).toContain("participation is subdued");
  });

  it("identifies choppy conditions and directional evidence conflicting with a neutral read", () => {
    const read = buildMarketExplanation(context({ marketRegime: "chop",
      trend: { label: "5m", direction: "neutral", strength: 0.1, bars: 60 },
      trends: [{ label: "5m", direction: "neutral", strength: 0.1, bars: 60 },
        { label: "1H", direction: "bearish", strength: 0.5, bars: 40 }],
      vwap: 170, vwapLocation: "below" }));
    expect(read.summary).toContain("Choppy conditions");
    expect(read.conflicting.join(" ")).toContain("below VWAP");
    expect(read.conflicting.join(" ")).toContain("1H bearish");
  });
});
