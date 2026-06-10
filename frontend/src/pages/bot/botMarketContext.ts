import type { BotTimeframeUnit, ProjectXMarketCandle } from "../../lib/types";
import { buildCandlestickData, buildLiquidityLevels, buildVwapData } from "./botChartData";
import { intervalSecondsFor, isFuturesSessionOpen } from "./botCandleGaps";

/**
 * Live market context derived from the candles currently loaded in the chart.
 * Pure functions so the read stays testable and independent of evaluation
 * round-trips: this is the "what is the market doing right now" layer, while
 * the backend analysis is the "what did the strategy think at evaluation time"
 * layer.
 */

export type TrendDirection = "up" | "down" | "sideways";
export type VolatilityState = "compressed" | "normal" | "expanding";
export type VolumeState = "below average" | "normal" | "above average";

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
  atr: number | null;
  atrPercent: number | null;
  volatilityState: VolatilityState | null;
  relativeVolume: number | null;
  volumeState: VolumeState | null;
  vwap: number | null;
  vwapDistance: number | null;
  vwapDistancePercent: number | null;
  sessionHigh: number | null;
  sessionLow: number | null;
  priorSessionClose: number | null;
  sessionChangePercent: number | null;
  nearestSupport: number | null;
  nearestResistance: number | null;
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
const VOLUME_BASELINE_BARS = 20;
const EASTERN_TIME_ZONE = "America/New_York";
const VWAP_SESSION_START_TIME = "18:00";
const MAX_HIGHER_TIMEFRAMES = 2;
/** Cap context computations; recent bars carry the read and deep history is paged in for charting, not context. */
const MAX_CONTEXT_BARS = 2_000;
const MAX_LEVEL_SCAN_BARS = 600;

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
    return { direction: "sideways", strength };
  }
  return { direction: score > 0 ? "up" : "down", strength };
}

export function buildMarketContext(snapshot: BotMarketSnapshot | null): MarketContext | null {
  if (!snapshot || snapshot.candles.length === 0) {
    return null;
  }

  const sorted = sortedValidCandles(snapshot.candles).slice(-MAX_CONTEXT_BARS);
  if (sorted.length < 2) {
    return null;
  }

  const candles = sorted.map((row) => row.candle);
  const latest = candles[candles.length - 1];
  const lastPrice = snapshot.lastPrice ?? latest.close;

  const atr = averageTrueRange(candles, ATR_PERIOD);
  const atrPercent = atr !== null && lastPrice !== 0 ? (atr / Math.abs(lastPrice)) * 100 : null;
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

  const sessionLevels = computeSessionLevels(sorted);
  const sessionChangePercent =
    sessionLevels.priorSessionClose !== null && sessionLevels.priorSessionClose !== 0
      ? ((lastPrice - sessionLevels.priorSessionClose) / Math.abs(sessionLevels.priorSessionClose)) * 100
      : null;

  const { nearestSupport, nearestResistance } = computeNearestLevels(candles, lastPrice);

  return {
    asOfTimestamp: latest.timestamp,
    lastPrice,
    trends: buildTimeframeTrends(candles, snapshot.unit, snapshot.unitNumber),
    atr,
    atrPercent,
    volatilityState,
    relativeVolume,
    volumeState,
    vwap,
    vwapDistance,
    vwapDistancePercent,
    sessionHigh: sessionLevels.sessionHigh,
    sessionLow: sessionLevels.sessionLow,
    priorSessionClose: sessionLevels.priorSessionClose,
    sessionChangePercent,
    nearestSupport,
    nearestResistance,
  };
}

export function buildTimeframeTrends(
  candles: ProjectXMarketCandle[],
  unit: BotTimeframeUnit,
  unitNumber: number,
): TimeframeTrend[] {
  const trends: TimeframeTrend[] = [];
  const baseCloses = sortedValidCandles(candles).map((row) => row.candle.close);
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
    const aggregated = aggregateCandles(candles, unit, unitNumber, step.unit, step.unitNumber);
    const closes = aggregated.map((candle) => candle.close);
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
  if (ratio >= 1.3) {
    return "expanding";
  }
  if (ratio <= 0.72) {
    return "compressed";
  }
  return "normal";
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
  if (relativeVolume >= 1.3) {
    return "above average";
  }
  if (relativeVolume <= 0.7) {
    return "below average";
  }
  return "normal";
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
