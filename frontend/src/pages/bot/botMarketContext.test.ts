import { describe, expect, it } from "vitest";

import {
  aggregateCandles,
  averageTrueRange,
  buildMarketContext,
  buildTimeframeTrends,
  classifyTrend,
  computeRelativeVolume,
  computeAtrPercentile,
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
    expect(trend?.direction).toBe("bullish");
    expect(trend?.strength).toBeGreaterThan(0.4);
  });

  it("detects a steady downtrend", () => {
    const closes = Array.from({ length: 40 }, (_, index) => 100 - index * 0.4);
    expect(classifyTrend(closes)?.direction).toBe("bearish");
  });

  it("reports sideways for flat closes and null for short series", () => {
    const closes = Array.from({ length: 40 }, (_, index) => 100 + (index % 2 === 0 ? 0.01 : -0.01));
    expect(classifyTrend(closes)?.direction).toBe("neutral");
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

  it("ranks the latest ATR against closed trailing observations", () => {
    const candles = seriesFiveMinute(50, (index) => 100 + index * 0.1).map((row, index) => ({
      ...row,
      high: row.close + 0.5 + index * 0.02,
      low: row.close - 0.5 - index * 0.02,
    }));
    expect(computeAtrPercentile(candles, 14, 40)).toBeGreaterThan(80);
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
    expect(trends.every((trend) => trend.direction === "bullish")).toBe(true);
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
    // Analysis features anchor to the latest closed bar, not the live quote.
    expect(context!.lastPrice).toBe(candles[candles.length - 1].close);
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
    expect(context!.trends[0].direction).toBe("bullish");
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

  it("never lets a partial bar contaminate closed-bar features", () => {
    const closed = seriesFiveMinute(60, (index) => 100 + index * 0.1, "2026-06-09T13:30:00Z");
    const partial = candle("2026-06-09T18:30:00Z", 9_999, {
      open: 9_000,
      high: 10_000,
      low: 8_000,
      volume: 99_999,
      is_partial: true,
    });
    const nowMs = Date.parse("2026-06-09T18:35:00Z");
    const baseline = buildMarketContext(snapshot(closed, 9_999), nowMs);
    const withPartial = buildMarketContext(snapshot([...closed, partial], 9_999), nowMs);

    expect(withPartial).not.toBeNull();
    expect(withPartial!.lastPrice).toBe(baseline!.lastPrice);
    expect(withPartial!.atr).toBe(baseline!.atr);
    expect(withPartial!.atrPercentile).toBe(baseline!.atrPercentile);
    expect(withPartial!.vwap).toBe(baseline!.vwap);
    expect(withPartial!.trends).toEqual(baseline!.trends);
    expect(withPartial!.provenance.closedCandleCount).toBe(60);
    expect(withPartial!.provenance.partialCandleCount).toBe(1);
  });

  it("reports insufficient and stale data explicitly", () => {
    const shortHistory = seriesFiveMinute(10, (index) => 100 + index * 0.1);
    const latestMs = Date.parse(shortHistory[shortHistory.length - 1].timestamp);
    const insufficient = buildMarketContext(snapshot(shortHistory), latestMs + 5 * 60_000);
    const stale = buildMarketContext(
      snapshot(seriesFiveMinute(40, (index) => 100 + index * 0.1)),
      Date.parse("2026-06-10T00:00:00Z"),
    );

    expect(insufficient!.dataQuality.status).toBe("insufficient");
    expect(insufficient!.dataQuality.missingInputs).toContain("At least 25 closed candles for trend");
    expect(stale!.provenance.isStale).toBe(true);
    expect(stale!.dataQuality.status).toBe("stale");
  });

  it("classifies clear trend and range regime boundaries deterministically", () => {
    // 90 base bars provide at least 25 completed 15m bars, so this assertion
    // exercises actual multi-timeframe alignment instead of the unavailable path.
    const trending = seriesFiveMinute(90, (index) => 100 + index * 0.25);
    const ranging = seriesFiveMinute(90, (index) => 100 + (index % 2 === 0 ? 0.01 : -0.01));
    const trendContext = buildMarketContext(snapshot(trending), Date.parse(trending[trending.length - 1].timestamp) + 5 * 60_000);
    const rangeContext = buildMarketContext(snapshot(ranging), Date.parse(ranging[ranging.length - 1].timestamp) + 5 * 60_000);

    expect(trendContext!.marketRegime).toBe("trend");
    expect(trendContext!.multiTimeframeAlignment.status).toBe("bullish");
    expect(rangeContext!.marketRegime).toBe("range");
  });

  it("includes in-session holes in provenance and lowers data quality", () => {
    const complete = seriesFiveMinute(40, (index) => 100 + index * 0.1);
    const withGap = complete.filter((_, index) => index !== 12);
    const nowMs = Date.parse(withGap[withGap.length - 1].timestamp) + 5 * 60_000;
    const context = buildMarketContext(snapshot(withGap), nowMs);

    expect(context!.provenance.detectedGapCount).toBe(1);
    expect(context!.provenance.detectedGaps[0].missingBars).toBe(1);
    expect(context!.dataQuality.status).toBe("limited");
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
