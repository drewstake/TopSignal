import { describe, expect, it } from "vitest";

import {
  aggregateCandles,
  averageTrueRange,
  buildMarketContext,
  buildTimeframeTrends,
  classifyTrend,
  computeRelativeVolume,
  timeframeLabel,
  type BotMarketSnapshot,
} from "./botMarketContext";
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
    open: close - 0.5,
    high: close + 1,
    low: close - 1,
    close,
    volume: 100,
    is_partial: false,
    fetched_at: null,
    ...overrides,
  };
}

function seriesFiveMinute(count: number, closeAt: (index: number) => number, startIso = "2026-06-09T13:00:00Z"): ProjectXMarketCandle[] {
  const startMs = Date.parse(startIso);
  return Array.from({ length: count }, (_, index) =>
    candle(new Date(startMs + index * 5 * 60_000).toISOString(), closeAt(index)),
  );
}

function snapshot(candles: ProjectXMarketCandle[], lastPrice: number | null = null): BotMarketSnapshot {
  return {
    contractKey: "CON.F.US.MNQ.M26:minute:5",
    unit: "minute",
    unitNumber: 5,
    candles,
    lastPrice,
    updatedAt: new Date().toISOString(),
  };
}

describe("aggregateCandles", () => {
  it("aggregates 5m candles into 15m OHLCV buckets", () => {
    const source = [
      candle("2026-06-09T14:00:00Z", 101, { open: 100, high: 102, low: 99.5, volume: 10 }),
      candle("2026-06-09T14:05:00Z", 103, { high: 104, low: 100.5, volume: 20 }),
      candle("2026-06-09T14:10:00Z", 102, { high: 103.5, low: 101, volume: 30 }),
      candle("2026-06-09T14:15:00Z", 105, { open: 102, high: 106, low: 101.5, volume: 40 }),
    ];
    const aggregated = aggregateCandles(source, "minute", 5, "minute", 15);

    expect(aggregated).toHaveLength(2);
    expect(aggregated[0].timestamp).toBe("2026-06-09T14:00:00.000Z");
    expect(aggregated[0].open).toBe(100);
    expect(aggregated[0].high).toBe(104);
    expect(aggregated[0].low).toBe(99.5);
    expect(aggregated[0].close).toBe(102);
    expect(aggregated[0].volume).toBe(60);
    expect(aggregated[0].is_partial).toBe(false);
    // Second bucket only has its first source bar -> partial.
    expect(aggregated[1].is_partial).toBe(true);
    expect(aggregated[1].unit).toBe("minute");
    expect(aggregated[1].unit_number).toBe(15);
  });

  it("returns [] when the target is not a multiple of the source", () => {
    const source = seriesFiveMinute(10, () => 100);
    expect(aggregateCandles(source, "minute", 5, "minute", 7)).toEqual([]);
    expect(aggregateCandles(source, "minute", 5, "minute", 5)).toEqual([]);
  });
});

describe("classifyTrend", () => {
  it("detects a steady uptrend", () => {
    const closes = Array.from({ length: 40 }, (_, index) => 100 + index * 0.4);
    const trend = classifyTrend(closes);
    expect(trend?.direction).toBe("up");
    expect(trend?.strength).toBeGreaterThan(0.4);
  });

  it("detects a steady downtrend", () => {
    const closes = Array.from({ length: 40 }, (_, index) => 100 - index * 0.4);
    expect(classifyTrend(closes)?.direction).toBe("down");
  });

  it("reports sideways for flat closes and null for short series", () => {
    const closes = Array.from({ length: 40 }, (_, index) => 100 + (index % 2 === 0 ? 0.01 : -0.01));
    expect(classifyTrend(closes)?.direction).toBe("sideways");
    expect(classifyTrend([100, 101, 102])).toBeNull();
  });
});

describe("averageTrueRange / computeRelativeVolume", () => {
  it("computes ATR over the trailing period", () => {
    const candles = seriesFiveMinute(20, (index) => 100 + index * 0.1);
    const atr = averageTrueRange(candles, 14);
    expect(atr).not.toBeNull();
    expect(atr!).toBeGreaterThan(0);
  });

  it("compares the last closed bar volume against the baseline", () => {
    const candles = seriesFiveMinute(20, () => 100);
    candles[candles.length - 1] = { ...candles[candles.length - 1], volume: 300 };
    const relativeVolume = computeRelativeVolume(candles);
    expect(relativeVolume).not.toBeNull();
    expect(relativeVolume!).toBeCloseTo(3, 1);
  });

  it("excludes partial bars from relative volume", () => {
    const candles = seriesFiveMinute(20, () => 100);
    candles.push({ ...candles[candles.length - 1], timestamp: "2026-06-09T16:00:00Z", volume: 9_999, is_partial: true });
    const relativeVolume = computeRelativeVolume(candles);
    expect(relativeVolume).not.toBeNull();
    expect(relativeVolume!).toBeLessThan(2);
  });
});

describe("buildTimeframeTrends", () => {
  it("includes the base timeframe and up to two divisible higher timeframes", () => {
    // 200 five-minute bars = enough for 15m (66 bars) and 1H (16 bars... below MIN bars)
    // so use 400 bars to give 1H enough history.
    const candles = seriesFiveMinute(400, (index) => 100 + index * 0.05);
    const trends = buildTimeframeTrends(candles, "minute", 5);
    const labels = trends.map((trend) => trend.label);

    expect(labels[0]).toBe("5m");
    expect(labels).toContain("15m");
    expect(labels).toContain("1H");
    expect(trends.every((trend) => trend.direction === "up")).toBe(true);
  });

  it("omits higher timeframes when there is not enough aggregated history", () => {
    const candles = seriesFiveMinute(40, (index) => 100 + index * 0.05);
    const trends = buildTimeframeTrends(candles, "minute", 5);
    expect(trends.map((trend) => trend.label)).toEqual(["5m"]);
  });
});

describe("buildMarketContext", () => {
  it("returns null without a snapshot or with too few candles", () => {
    expect(buildMarketContext(null)).toBeNull();
    expect(buildMarketContext(snapshot([candle("2026-06-09T14:00:00Z", 100)]))).toBeNull();
  });

  it("builds a full context read from session candles", () => {
    // Tuesday regular session: 09:30-16:00 ET = 13:30-20:00 UTC.
    const candles = seriesFiveMinute(60, (index) => 100 + index * 0.2, "2026-06-09T13:30:00Z");
    const context = buildMarketContext(snapshot(candles, 112.5));

    expect(context).not.toBeNull();
    expect(context!.lastPrice).toBe(112.5);
    expect(context!.asOfTimestamp).toBe(candles[candles.length - 1].timestamp);
    expect(context!.atr).not.toBeNull();
    expect(context!.vwap).not.toBeNull();
    expect(context!.vwapDistance).not.toBeNull();
    // Rising closes -> price above VWAP.
    expect(context!.vwapDistance!).toBeGreaterThan(0);
    expect(context!.sessionHigh).not.toBeNull();
    expect(context!.sessionLow).not.toBeNull();
    expect(context!.sessionHigh!).toBeGreaterThan(context!.sessionLow!);
    expect(context!.trends.length).toBeGreaterThan(0);
    expect(context!.trends[0].direction).toBe("up");
  });

  it("splits session levels on the 18:00 ET boundary and reports prior session close", () => {
    // Monday 16:00-16:55 ET (20:00-20:55 UTC) then Monday 18:00+ ET (22:00+ UTC).
    const priorSession = seriesFiveMinute(12, (index) => 100 + index * 0.1, "2026-06-08T20:00:00Z");
    const currentSession = seriesFiveMinute(12, (index) => 110 + index * 0.1, "2026-06-08T22:00:00Z");
    const context = buildMarketContext(snapshot([...priorSession, ...currentSession]));

    expect(context).not.toBeNull();
    const priorClose = priorSession[priorSession.length - 1].close;
    expect(context!.priorSessionClose).toBeCloseTo(priorClose, 6);
    // Current session low should come from the 18:00 ET+ bars only.
    expect(context!.sessionLow!).toBeGreaterThanOrEqual(109);
    expect(context!.sessionChangePercent).not.toBeNull();
    expect(context!.sessionChangePercent!).toBeGreaterThan(0);
  });
});

describe("timeframeLabel", () => {
  it("uses ladder labels and falls back to a compact form", () => {
    expect(timeframeLabel("minute", 5)).toBe("5m");
    expect(timeframeLabel("hour", 4)).toBe("4H");
    expect(timeframeLabel("minute", 3)).toBe("3m");
    expect(timeframeLabel("second", 30)).toBe("30s");
  });
});
