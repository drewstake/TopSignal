import type {
  BotDataQualityStatus,
  BotMarketBias,
  BotMarketRegime,
  BotTimeframeUnit,
  BotVwapLocation,
  ProjectXMarketCandle,
} from "../../lib/types";
import { buildCandlestickData, buildLiquidityLevels, buildVwapData } from "./botChartData";
import { findCandleGaps, intervalSecondsFor, isFuturesSessionOpen } from "./botCandleGaps";

/**
 * Live market context derived from the candles currently loaded in the chart.
 * Pure functions so the read stays testable and independent of evaluation
 * round-trips: this is the "what is the market doing right now" layer, while
 * the backend analysis is the "what did the strategy think at evaluation time"
 * layer.
 */

export type TrendDirection = BotMarketBias;
export type VolatilityState = "low" | "normal" | "elevated" | "extreme";
export type VolumeState = "low" | "normal" | "elevated";

export interface BotMarketSnapshot {
  contractKey: string;
  unit: BotTimeframeUnit;
  unitNumber: number;
  candles: ProjectXMarketCandle[];
  lastPrice: number | null;
  updatedAt: string;
}

export interface TimeframeTrend {
  label: string;
  direction: TrendDirection;
  /** 0..1 */
  strength: number;
  bars: number;
}

export interface MarketContext {
  asOfTimestamp: string | null;
  lastPrice: number | null;
  trends: TimeframeTrend[];
  trend: TimeframeTrend | null;
  marketRegime: BotMarketRegime;
  atr: number | null;
  atrPercent: number | null;
  atrPercentile: number | null;
  volatilityState: VolatilityState | null;
  relativeVolume: number | null;
  volumeState: VolumeState | null;
  vwap: number | null;
  vwapLocation: BotVwapLocation;
  vwapDistance: number | null;
  vwapDistancePercent: number | null;
  multiTimeframeAlignment: {
    status: "bullish" | "bearish" | "mixed" | "neutral" | "unavailable";
    alignedTimeframes: number;
    conflictingTimeframes: number;
  };
  sessionHigh: number | null;
  sessionLow: number | null;
  priorSessionClose: number | null;
  sessionChangePercent: number | null;
  nearestSupport: number | null;
  nearestResistance: number | null;
  provenance: {
    closedCandleCount: number;
    partialCandleCount: number;
    latestCandleTimestamp: string | null;
    dataAgeSeconds: number | null;
    isStale: boolean;
    staleAfterSeconds: number;
    timeframe: string;
    detectedGapCount: number;
    detectedGaps: Array<{
      beforeTimestamp: string;
      afterTimestamp: string;
      missingBars: number;
    }>;
    missingGapBars: number;
  };
  dataQuality: {
    status: BotDataQualityStatus;
    confidence: number;
    missingInputs: string[];
    warnings: string[];
  };
}

interface TimeframeStep {
  label: string;
  unit: BotTimeframeUnit;
  unitNumber: number;
}

const TIMEFRAME_LADDER: TimeframeStep[] = [
  { label: "1m", unit: "minute", unitNumber: 1 },
  { label: "5m", unit: "minute", unitNumber: 5 },
  { label: "15m", unit: "minute", unitNumber: 15 },
  { label: "1H", unit: "hour", unitNumber: 1 },
  { label: "4H", unit: "hour", unitNumber: 4 },
  { label: "1D", unit: "day", unitNumber: 1 },
];

const FAST_TREND_PERIOD = 9;
const SLOW_TREND_PERIOD = 21;
const MIN_TREND_BARS = SLOW_TREND_PERIOD + 4;
const ATR_PERIOD = 14;
const ATR_PERCENTILE_LOOKBACK = 100;
const VOLUME_BASELINE_BARS = 20;
const EASTERN_TIME_ZONE = "America/New_York";
const VWAP_SESSION_START_TIME = "18:00";
const MAX_HIGHER_TIMEFRAMES = 2;
/** Cap context computations; recent bars carry the read and deep history is paged in for charting, not context. */
const MAX_CONTEXT_BARS = 2_000;
const MAX_LEVEL_SCAN_BARS = 600;
const LOCAL_CONTEXT_VERSION = "local_fallback_market_analysis_v2";

export { LOCAL_CONTEXT_VERSION };

export function timeframeLabel(unit: BotTimeframeUnit, unitNumber: number): string {
  const preset = TIMEFRAME_LADDER.find((step) => step.unit === unit && step.unitNumber === unitNumber);
  if (preset) {
    return preset.label;
  }
  const suffix: Record<BotTimeframeUnit, string> = {
    second: "s",
    minute: "m",
    hour: "H",
    day: "D",
    week: "W",
    month: "M",
  };
  return `${Math.max(1, Math.trunc(unitNumber))}${suffix[unit]}`;
}

/**
 * Aggregate candles into a higher timeframe. The target interval must be a
 * multiple of the source interval; otherwise [] is returned.
 */
export function aggregateCandles(
  candles: ProjectXMarketCandle[],
  sourceUnit: BotTimeframeUnit,
  sourceUnitNumber: number,
  targetUnit: BotTimeframeUnit,
  targetUnitNumber: number,
): ProjectXMarketCandle[] {
  const sourceSeconds = intervalSecondsFor(sourceUnit, sourceUnitNumber);
  const targetSeconds = intervalSecondsFor(targetUnit, targetUnitNumber);
  if (sourceSeconds <= 0 || targetSeconds <= sourceSeconds || targetSeconds % sourceSeconds !== 0) {
    return [];
  }

  const sorted = sortedValidCandles(candles);
  if (sorted.length === 0) {
    return [];
  }

  const bucketMs = targetSeconds * 1000;
  const buckets = new Map<number, ProjectXMarketCandle>();
  for (const row of sorted) {
    const bucketStartMs = Math.floor(row.ms / bucketMs) * bucketMs;
    const existing = buckets.get(bucketStartMs);
    if (!existing) {
      buckets.set(bucketStartMs, {
        ...row.candle,
        unit: targetUnit,
        unit_number: targetUnitNumber,
        timestamp: new Date(bucketStartMs).toISOString(),
        is_partial: row.candle.is_partial,
      });
      continue;
    }

    existing.high = Math.max(existing.high, row.candle.high);
    existing.low = Math.min(existing.low, row.candle.low);
    existing.close = row.candle.close;
    existing.volume += Number.isFinite(row.candle.volume) ? row.candle.volume : 0;
    existing.is_partial = existing.is_partial || row.candle.is_partial;
  }

  const aggregated = Array.from(buckets.entries())
    .sort((left, right) => left[0] - right[0])
    .map(([, candle]) => candle);

  // The newest bucket is partial unless its source bars reach the bucket end.
  const last = aggregated[aggregated.length - 1];
  if (last) {
    const lastBucketStart = Date.parse(last.timestamp);
    const lastSourceMs = sorted[sorted.length - 1].ms;
    if (lastSourceMs + sourceSeconds * 1000 < lastBucketStart + bucketMs) {
      last.is_partial = true;
    }
  }

  return aggregated;
}

/** EMA(fast) vs EMA(slow) gap plus slow-EMA slope, normalized by price. */
export function classifyTrend(closes: number[]): { direction: TrendDirection; strength: number } | null {
  if (closes.length < MIN_TREND_BARS) {
    return null;
  }

  const fast = emaSeries(closes, FAST_TREND_PERIOD);
  const slow = emaSeries(closes, SLOW_TREND_PERIOD);
  const latestFast = fast[fast.length - 1];
  const latestSlow = slow[slow.length - 1];
  const priorSlow = slow[Math.max(0, slow.length - 4)];
  const reference = Math.abs(closes[closes.length - 1]);
  if (!Number.isFinite(latestFast) || !Number.isFinite(latestSlow) || reference <= 0) {
    return null;
  }

  const gapPercent = ((latestFast - latestSlow) / reference) * 100;
  const slopePercent = ((latestSlow - priorSlow) / reference) * 100;
  const score = gapPercent * 0.7 + slopePercent * 3 * 0.3;
  // ~0.05% EMA spread on an index future is a meaningful tilt at intraday scale.
  const strength = clamp(Math.abs(score) / 0.2, 0, 1);
  if (strength < 0.18) {
    return { direction: "neutral", strength };
  }
  return { direction: score > 0 ? "bullish" : "bearish", strength };
}

export function buildMarketContext(
  snapshot: BotMarketSnapshot | null,
  nowMs = Date.now(),
  staleAfterSecondsOverride?: number,
): MarketContext | null {
  if (!snapshot || snapshot.candles.length === 0) {
    return null;
  }

  const allSorted = sortedValidCandles(snapshot.candles);
  const partialCandleCount = allSorted.filter((row) => row.candle.is_partial).length;
  const sorted = allSorted.filter((row) => !row.candle.is_partial).slice(-MAX_CONTEXT_BARS);
  if (sorted.length < 2) {
    return null;
  }

  const candles = sorted.map((row) => row.candle);
  const latest = candles[candles.length - 1];
  // Every derived feature deliberately uses the latest closed candle. The live
  // quote and partial bar remain chart concerns, not analysis inputs.
  const lastPrice = latest.close;

  const atr = averageTrueRange(candles, ATR_PERIOD);
  const atrPercent = atr !== null && lastPrice !== 0 ? (atr / Math.abs(lastPrice)) * 100 : null;
  const atrPercentile = computeAtrPercentile(candles, ATR_PERIOD, ATR_PERCENTILE_LOOKBACK);
  const volatilityState = classifyVolatility(candles);
  const relativeVolume = computeRelativeVolume(candles);
  const volumeState = classifyVolume(relativeVolume);

  const vwapPoints = buildVwapData(candles, {
    sessionStartTime: VWAP_SESSION_START_TIME,
    sessionTimeZone: EASTERN_TIME_ZONE,
  });
  const vwap = vwapPoints.length > 0 ? vwapPoints[vwapPoints.length - 1].value : null;
  const vwapDistance = vwap !== null ? lastPrice - vwap : null;
  const vwapDistancePercent = vwap !== null && vwap !== 0 ? ((lastPrice - vwap) / Math.abs(vwap)) * 100 : null;
  const vwapLocation = classifyVwapLocation(lastPrice, vwap, atr);

  const sessionLevels = computeSessionLevels(sorted);
  const sessionChangePercent =
    sessionLevels.priorSessionClose !== null && sessionLevels.priorSessionClose !== 0
      ? ((lastPrice - sessionLevels.priorSessionClose) / Math.abs(sessionLevels.priorSessionClose)) * 100
      : null;

  const { nearestSupport, nearestResistance } = computeNearestLevels(candles, lastPrice);
  const trends = buildTimeframeTrends(candles, snapshot.unit, snapshot.unitNumber);
  const trend = trends[0] ?? null;
  const multiTimeframeAlignment = classifyTimeframeAlignment(trends);
  const marketRegime = classifyMarketRegime({ trend, volatilityState, volumeState });
  const dataGaps = findCandleGaps(candles, snapshot.unit, snapshot.unitNumber).filter((gap) => gap.kind === "data");
  const missingGapBars = dataGaps.reduce((total, gap) => total + gap.missingSessionBars, 0);
  const intervalSeconds = intervalSecondsFor(snapshot.unit, snapshot.unitNumber);
  const latestTimestampMs = Date.parse(latest.timestamp);
  const dataAgeSeconds = Number.isFinite(latestTimestampMs)
    ? Math.max(0, Math.floor((nowMs - (latestTimestampMs + intervalSeconds * 1000)) / 1000))
    : null;
  const staleAfterSeconds =
    typeof staleAfterSecondsOverride === "number" && Number.isFinite(staleAfterSecondsOverride) && staleAfterSecondsOverride > 0
      ? staleAfterSecondsOverride
      : Math.max(intervalSeconds * 2, 60);
  const isStale = dataAgeSeconds !== null && dataAgeSeconds > staleAfterSeconds;
  const missingInputs = buildMissingInputs({
    closedCandleCount: candles.length,
    trend,
    atr,
    atrPercentile,
    relativeVolume,
    vwap,
    multiTimeframeAlignment,
    nearestSupport,
    nearestResistance,
  });
  const warnings = [
    ...(isStale ? [`Latest closed candle is ${dataAgeSeconds ?? 0}s old.`] : []),
    ...(dataGaps.length > 0 ? [`Detected ${dataGaps.length} in-session candle gap${dataGaps.length === 1 ? "" : "s"}.`] : []),
    ...(partialCandleCount > 0 ? [`Excluded ${partialCandleCount} partial candle${partialCandleCount === 1 ? "" : "s"}.`] : []),
  ];
  const dataQuality = classifyDataQuality({
    closedCandleCount: candles.length,
    isStale,
    gapCount: dataGaps.length,
    missingInputCount: missingInputs.length,
  });

  return {
    asOfTimestamp: latest.timestamp,
    lastPrice,
    trends,
    trend,
    marketRegime,
    atr,
    atrPercent,
    atrPercentile,
    volatilityState,
    relativeVolume,
    volumeState,
    vwap,
    vwapLocation,
    vwapDistance,
    vwapDistancePercent,
    multiTimeframeAlignment,
    sessionHigh: sessionLevels.sessionHigh,
    sessionLow: sessionLevels.sessionLow,
    priorSessionClose: sessionLevels.priorSessionClose,
    sessionChangePercent,
    nearestSupport,
    nearestResistance,
    provenance: {
      closedCandleCount: candles.length,
      partialCandleCount,
      latestCandleTimestamp: latest.timestamp,
      dataAgeSeconds,
      isStale,
      staleAfterSeconds,
      timeframe: timeframeLabel(snapshot.unit, snapshot.unitNumber),
      detectedGapCount: dataGaps.length,
      detectedGaps: dataGaps.map((gap) => ({
        beforeTimestamp: gap.beforeTimestamp,
        afterTimestamp: gap.afterTimestamp,
        missingBars: gap.missingSessionBars,
      })),
      missingGapBars,
    },
    dataQuality: {
      ...dataQuality,
      missingInputs,
      warnings,
    },
  };
}

export function buildTimeframeTrends(
  candles: ProjectXMarketCandle[],
  unit: BotTimeframeUnit,
  unitNumber: number,
): TimeframeTrend[] {
  const trends: TimeframeTrend[] = [];
  const closedCandles = sortedValidCandles(candles)
    .filter((row) => !row.candle.is_partial)
    .map((row) => row.candle);
  const baseCloses = closedCandles.map((candle) => candle.close);
  const baseTrend = classifyTrend(baseCloses);
  if (baseTrend) {
    trends.push({
      label: timeframeLabel(unit, unitNumber),
      direction: baseTrend.direction,
      strength: baseTrend.strength,
      bars: baseCloses.length,
    });
  }

  const baseSeconds = intervalSecondsFor(unit, unitNumber);
  const higherSteps = TIMEFRAME_LADDER.filter((step) => {
    const stepSeconds = intervalSecondsFor(step.unit, step.unitNumber);
    return stepSeconds > baseSeconds && stepSeconds % baseSeconds === 0;
  }).slice(0, MAX_HIGHER_TIMEFRAMES);

  for (const step of higherSteps) {
    const aggregated = aggregateCandles(closedCandles, unit, unitNumber, step.unit, step.unitNumber);
    const closes = aggregated.filter((candle) => !candle.is_partial).map((candle) => candle.close);
    const trend = classifyTrend(closes);
    if (trend) {
      trends.push({
        label: step.label,
        direction: trend.direction,
        strength: trend.strength,
        bars: closes.length,
      });
    }
  }

  return trends;
}

export function averageTrueRange(candles: ProjectXMarketCandle[], period: number): number | null {
  if (candles.length < 2 || period <= 0) {
    return null;
  }

  const ranges: number[] = [];
  const start = Math.max(1, candles.length - period);
  for (let index = start; index < candles.length; index += 1) {
    const candle = candles[index];
    const previousClose = candles[index - 1].close;
    ranges.push(
      Math.max(
        candle.high - candle.low,
        Math.abs(candle.high - previousClose),
        Math.abs(candle.low - previousClose),
      ),
    );
  }

  const finite = ranges.filter((value) => Number.isFinite(value) && value >= 0);
  if (finite.length === 0) {
    return null;
  }
  return finite.reduce((sum, value) => sum + value, 0) / finite.length;
}

/** Percentile rank of the latest ATR against trailing rolling ATR observations. */
export function computeAtrPercentile(
  candles: ProjectXMarketCandle[],
  period = ATR_PERIOD,
  lookback = ATR_PERCENTILE_LOOKBACK,
): number | null {
  const closed = candles.filter((candle) => !candle.is_partial);
  if (period <= 0 || closed.length < period + 2) {
    return null;
  }

  const observations: number[] = [];
  const firstEnd = Math.max(period + 1, closed.length - Math.max(1, lookback) + 1);
  for (let end = firstEnd; end <= closed.length; end += 1) {
    const value = averageTrueRange(closed.slice(0, end), period);
    if (value !== null && Number.isFinite(value)) {
      observations.push(value);
    }
  }
  const latest = observations[observations.length - 1];
  if (latest === undefined || observations.length < 2) {
    return null;
  }
  const less = observations.filter((value) => value < latest && !nearlyEqual(value, latest)).length;
  const equal = observations.filter((value) => nearlyEqual(value, latest)).length;
  return clamp(((less + equal * 0.5) / observations.length) * 100, 0, 100);
}

function classifyVolatility(candles: ProjectXMarketCandle[]): VolatilityState | null {
  if (candles.length < ATR_PERIOD + 8) {
    return null;
  }

  const recent = averageTrueRange(candles, 6);
  const baseline = averageTrueRange(candles.slice(0, -6), Math.min(28, candles.length - 7));
  if (recent === null || baseline === null || baseline <= 0) {
    return null;
  }

  const ratio = recent / baseline;
  if (ratio < 0.7) {
    return "low";
  }
  if (ratio < 1.35) {
    return "normal";
  }
  if (ratio < 2) {
    return "elevated";
  }
  return "extreme";
}

/** Last closed bar volume vs the average of the prior baseline bars. */
export function computeRelativeVolume(candles: ProjectXMarketCandle[]): number | null {
  const closed = candles.filter((candle) => !candle.is_partial && Number.isFinite(candle.volume) && candle.volume > 0);
  if (closed.length < 6) {
    return null;
  }

  const lastVolume = closed[closed.length - 1].volume;
  const baselineRows = closed.slice(0, -1).slice(-VOLUME_BASELINE_BARS);
  if (baselineRows.length === 0) {
    return null;
  }
  const baseline = baselineRows.reduce((sum, candle) => sum + candle.volume, 0) / baselineRows.length;
  if (baseline <= 0) {
    return null;
  }
  return lastVolume / baseline;
}

function classifyVolume(relativeVolume: number | null): VolumeState | null {
  if (relativeVolume === null) {
    return null;
  }
  if (relativeVolume > 1.5) {
    return "elevated";
  }
  if (relativeVolume < 0.7) {
    return "low";
  }
  return "normal";
}

function classifyVwapLocation(
  price: number,
  vwap: number | null,
  atr: number | null,
): BotVwapLocation {
  if (vwap === null || !Number.isFinite(price)) {
    return "unavailable";
  }
  const tolerance = Math.max((atr ?? 0) * 0.05, Math.abs(price) * 0.00005);
  if (price > vwap + tolerance) {
    return "above";
  }
  if (price < vwap - tolerance) {
    return "below";
  }
  return "at";
}

function classifyTimeframeAlignment(trends: TimeframeTrend[]): MarketContext["multiTimeframeAlignment"] {
  if (trends.length < 2) {
    return { status: "unavailable", alignedTimeframes: 0, conflictingTimeframes: 0 };
  }
  const primaryDirection = trends[0].direction;
  if (primaryDirection === "neutral") {
    return { status: "neutral", alignedTimeframes: 0, conflictingTimeframes: 0 };
  }
  const alignedTimeframes = trends.filter((trend) => trend.direction === primaryDirection).length;
  const conflictingTimeframes = trends.filter((trend) => {
    const direction = trend.direction;
    return direction !== "neutral" && direction !== primaryDirection;
  }).length;
  return {
    status: conflictingTimeframes > 0 ? "mixed" : primaryDirection,
    alignedTimeframes,
    conflictingTimeframes,
  };
}

function nearlyEqual(left: number, right: number): boolean {
  return Math.abs(left - right) <= Number.EPSILON * Math.max(1, Math.abs(left), Math.abs(right)) * 8;
}

function classifyMarketRegime(input: {
  trend: TimeframeTrend | null;
  volatilityState: VolatilityState | null;
  volumeState: VolumeState | null;
}): BotMarketRegime {
  if (!input.trend || !input.volatilityState) {
    return "unknown";
  }
  const directional = input.trend.direction !== "neutral";
  if (
    input.volatilityState === "extreme" ||
    (input.volatilityState === "elevated" && (!directional || input.trend.strength < 0.45))
  ) {
    return "volatile";
  }
  if (input.volatilityState === "low") {
    return "quiet";
  }
  if (directional && input.trend.strength >= 0.45) {
    return "trend";
  }
  if (!directional && input.volumeState !== "elevated") {
    return "range";
  }
  return "chop";
}

function buildMissingInputs(input: {
  closedCandleCount: number;
  trend: TimeframeTrend | null;
  atr: number | null;
  atrPercentile: number | null;
  relativeVolume: number | null;
  vwap: number | null;
  multiTimeframeAlignment: MarketContext["multiTimeframeAlignment"];
  nearestSupport: number | null;
  nearestResistance: number | null;
}): string[] {
  const missing: string[] = [];
  if (input.closedCandleCount < MIN_TREND_BARS || input.trend === null) {
    missing.push(`At least ${MIN_TREND_BARS} closed candles for trend`);
  }
  if (input.atr === null) {
    missing.push("ATR history");
  }
  if (input.atrPercentile === null) {
    missing.push("ATR percentile history");
  }
  if (input.relativeVolume === null) {
    missing.push("Relative-volume baseline");
  }
  if (input.vwap === null) {
    missing.push("Session VWAP inputs");
  }
  if (input.multiTimeframeAlignment.status === "unavailable") {
    missing.push("Multi-timeframe trend history");
  }
  if (input.nearestSupport === null) {
    missing.push("Nearby support");
  }
  if (input.nearestResistance === null) {
    missing.push("Nearby resistance");
  }
  return missing;
}

function classifyDataQuality(input: {
  closedCandleCount: number;
  isStale: boolean;
  gapCount: number;
  missingInputCount: number;
}): { status: BotDataQualityStatus; confidence: number } {
  let confidence = 100;
  if (input.closedCandleCount < MIN_TREND_BARS) {
    confidence -= 45;
  } else if (input.closedCandleCount < 50) {
    confidence -= 12;
  }
  confidence -= Math.min(30, input.gapCount * 10);
  confidence -= Math.min(32, input.missingInputCount * 8);
  if (input.isStale) {
    confidence -= 35;
  }
  confidence = Math.round(clamp(confidence, 0, 100));

  if (input.isStale) {
    return { status: "stale", confidence };
  }
  if (input.closedCandleCount < MIN_TREND_BARS) {
    return { status: "insufficient", confidence };
  }
  if (confidence < 80) {
    return { status: "limited", confidence };
  }
  return { status: "good", confidence };
}

interface SessionLevels {
  sessionHigh: number | null;
  sessionLow: number | null;
  priorSessionClose: number | null;
}

/**
 * Current-session high/low and prior-session close, using the futures session
 * boundary (18:00 ET). The session of a candle is identified by the most
 * recent 18:00 ET boundary at or before its timestamp.
 */
function computeSessionLevels(sorted: { ms: number; candle: ProjectXMarketCandle }[]): SessionLevels {
  if (sorted.length === 0) {
    return { sessionHigh: null, sessionLow: null, priorSessionClose: null };
  }

  const sessionKeys = sorted.map((row) => sessionKeyFor(row.ms));
  const latestKey = sessionKeys[sessionKeys.length - 1];

  let sessionHigh: number | null = null;
  let sessionLow: number | null = null;
  let priorSessionClose: number | null = null;

  for (let index = 0; index < sorted.length; index += 1) {
    const { candle } = sorted[index];
    if (sessionKeys[index] === latestKey) {
      sessionHigh = sessionHigh === null ? candle.high : Math.max(sessionHigh, candle.high);
      sessionLow = sessionLow === null ? candle.low : Math.min(sessionLow, candle.low);
    } else {
      priorSessionClose = candle.close;
    }
  }

  return { sessionHigh, sessionLow, priorSessionClose };
}

const SESSION_BOUNDARY_PROBE_HOURS = 30;

function sessionKeyFor(timestampMs: number): number {
  // Walk back hour by hour to find the most recent 18:00 ET boundary.
  const date = new Date(timestampMs);
  const utcMinutes = date.getUTCMinutes();
  let probeMs = timestampMs - utcMinutes * 60_000 - date.getUTCSeconds() * 1000 - date.getUTCMilliseconds();
  for (let hops = 0; hops < SESSION_BOUNDARY_PROBE_HOURS; hops += 1) {
    if (easternHourOf(probeMs) === 18 && probeMs <= timestampMs) {
      return probeMs;
    }
    probeMs -= 3_600_000;
  }
  return Math.floor(timestampMs / 86_400_000) * 86_400_000;
}

const easternHourOnlyFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: EASTERN_TIME_ZONE,
  hour: "2-digit",
  hourCycle: "h23",
});
const easternHourMemo = new Map<number, number>();

function easternHourOf(timestampMs: number): number {
  const hourKey = Math.floor(timestampMs / 3_600_000);
  const cached = easternHourMemo.get(hourKey);
  if (cached !== undefined) {
    return cached;
  }
  const hour = Number(easternHourOnlyFormatter.format(new Date(hourKey * 3_600_000)));
  if (easternHourMemo.size > 20_000) {
    easternHourMemo.clear();
  }
  easternHourMemo.set(hourKey, hour);
  return hour;
}

function computeNearestLevels(
  allCandles: ProjectXMarketCandle[],
  referencePrice: number,
): { nearestSupport: number | null; nearestResistance: number | null } {
  // Liquidity scanning is quadratic in the worst case; bound the window.
  const candles = allCandles.slice(-MAX_LEVEL_SCAN_BARS);
  const closedCandles = candles.filter((candle) => !candle.is_partial);
  const chartCandles = buildCandlestickData(closedCandles.length >= 5 ? closedCandles : candles, {
    bridgeConsecutiveGaps: false,
  });
  const liquidityLevels = buildLiquidityLevels(chartCandles);
  const liquiditySupport = liquidityLevels.find((level) => level.side === "sell")?.price ?? null;
  const liquidityResistance = liquidityLevels.find((level) => level.side === "buy")?.price ?? null;

  let swingSupport: number | null = null;
  let swingResistance: number | null = null;
  for (const candle of candles) {
    if (Number.isFinite(candle.low) && candle.low < referencePrice) {
      swingSupport = swingSupport === null ? candle.low : Math.max(swingSupport, candle.low);
    }
    if (Number.isFinite(candle.high) && candle.high > referencePrice) {
      swingResistance = swingResistance === null ? candle.high : Math.min(swingResistance, candle.high);
    }
  }

  return {
    nearestSupport: liquiditySupport ?? swingSupport,
    nearestResistance: liquidityResistance ?? swingResistance,
  };
}

/** Re-exported so UI code can mark in/out-of-session timestamps consistently. */
export { isFuturesSessionOpen };

function sortedValidCandles(candles: ProjectXMarketCandle[]): { ms: number; candle: ProjectXMarketCandle }[] {
  const byMs = new Map<number, ProjectXMarketCandle>();
  for (const candle of candles) {
    const ms = Date.parse(candle.timestamp);
    if (
      Number.isFinite(ms) &&
      [candle.open, candle.high, candle.low, candle.close].every((value) => Number.isFinite(value))
    ) {
      byMs.set(ms, candle);
    }
  }
  return Array.from(byMs.entries())
    .map(([ms, candle]) => ({ ms, candle }))
    .sort((left, right) => left.ms - right.ms);
}

function emaSeries(values: number[], period: number): number[] {
  if (values.length === 0 || period <= 0) {
    return [];
  }
  const normalizedPeriod = Math.min(period, values.length);
  const seed = values.slice(0, normalizedPeriod).reduce((sum, value) => sum + value, 0) / normalizedPeriod;
  const multiplier = 2 / (normalizedPeriod + 1);
  const output: number[] = [];
  let current = seed;
  for (let index = 0; index < values.length; index += 1) {
    if (index >= normalizedPeriod) {
      current = (values[index] - current) * multiplier + current;
    }
    output.push(current);
  }
  return output;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
