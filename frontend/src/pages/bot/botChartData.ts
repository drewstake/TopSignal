import type { CandlestickData, LineData, SeriesMarker, UTCTimestamp } from "lightweight-charts";

import type { BotConfig, BotDecision, BotEvaluation, BotTimeframeUnit, ProjectXMarketCandle, ProjectXMarketPrice } from "../../lib/types";

export const BOT_CHART_MAX_BARS = 2_000;
export const BOT_CHART_MIN_BARS = 300;
const CHART_LOOKBACK_MULTIPLIER = 3;

const UNIT_SECONDS_BY_NAME: Record<BotTimeframeUnit, number> = {
  second: 1,
  minute: 60,
  hour: 60 * 60,
  day: 24 * 60 * 60,
  week: 7 * 24 * 60 * 60,
  month: 31 * 24 * 60 * 60,
};

const SIGNAL_ACTIONS = new Set(["BUY", "SELL", "HOLD"]);

export interface BotChartQueryWindow {
  start: string;
  end: string;
  limit: number;
}

export type LiquiditySide = "buy" | "sell";

export interface LiquidityLevel {
  side: LiquiditySide;
  price: number;
  time: UTCTimestamp;
  index: number;
}

interface BuildLiveCandleFromPriceOptions {
  config: BotConfig;
  price: ProjectXMarketPrice;
  closedCandles: ProjectXMarketCandle[];
  currentLiveCandle: ProjectXMarketCandle | null;
  fetchedAt?: Date;
}

export function toUtcTimestamp(value: string | null | undefined): UTCTimestamp | null {
  if (!value) {
    return null;
  }

  const timestampMs = Date.parse(value);
  if (!Number.isFinite(timestampMs)) {
    return null;
  }

  return Math.floor(timestampMs / 1000) as UTCTimestamp;
}

export function buildCandlestickData(candles: ProjectXMarketCandle[]): CandlestickData<UTCTimestamp>[] {
  const byTime = new Map<number, CandlestickData<UTCTimestamp>>();

  for (const candle of candles) {
    const time = toUtcTimestamp(candle.timestamp);
    if (time === null) {
      continue;
    }
    if (![candle.open, candle.high, candle.low, candle.close].every(Number.isFinite)) {
      continue;
    }

    byTime.set(Number(time), {
      time,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
    });
  }

  return Array.from(byTime.values()).sort((left, right) => Number(left.time) - Number(right.time));
}

export function buildSmaData(candles: CandlestickData<UTCTimestamp>[], period: number): LineData<UTCTimestamp>[] {
  const normalizedPeriod = Math.trunc(period);
  if (normalizedPeriod <= 0 || candles.length < normalizedPeriod) {
    return [];
  }

  const output: LineData<UTCTimestamp>[] = [];
  let rollingSum = 0;

  candles.forEach((candle, index) => {
    rollingSum += candle.close;
    if (index >= normalizedPeriod) {
      rollingSum -= candles[index - normalizedPeriod].close;
    }
    if (index >= normalizedPeriod - 1) {
      output.push({
        time: candle.time,
        value: rollingSum / normalizedPeriod,
      });
    }
  });

  return output;
}

export function buildLiquidityLevels(
  candles: CandlestickData<UTCTimestamp>[],
  swingSpan = 2,
): LiquidityLevel[] {
  const span = Math.max(1, Math.trunc(swingSpan));
  const history = candles
    .filter(isFiniteCandlestick)
    .sort((left, right) => Number(left.time) - Number(right.time));

  if (history.length < span * 2 + 1) {
    return [];
  }

  const referencePrice = history[history.length - 1].close;
  const buySide = findActiveBuySideLiquidity(history, referencePrice, span);
  const sellSide = findActiveSellSideLiquidity(history, referencePrice, span);

  return [buySide, sellSide].filter((level): level is LiquidityLevel => level !== null);
}

export function buildBotChartQuery(config: BotConfig, now: Date = new Date()): BotChartQueryWindow {
  const lookbackBars = Math.trunc(config.lookback_bars);
  const limit = Math.min(BOT_CHART_MAX_BARS, Math.max(BOT_CHART_MIN_BARS, lookbackBars * 4));
  const timeframeSeconds =
    UNIT_SECONDS_BY_NAME[config.timeframe_unit] * Math.max(1, Math.trunc(config.timeframe_unit_number));
  const end = Number.isFinite(now.getTime()) ? now : new Date();
  const start = new Date(end.getTime() - timeframeSeconds * limit * CHART_LOOKBACK_MULTIPLIER * 1000);

  return {
    start: start.toISOString(),
    end: end.toISOString(),
    limit,
  };
}

export function buildBotLivePriceQuery(config: BotConfig, now: Date = new Date()): BotChartQueryWindow {
  const timeframeSeconds =
    UNIT_SECONDS_BY_NAME[config.timeframe_unit] * Math.max(1, Math.trunc(config.timeframe_unit_number));
  const end = Number.isFinite(now.getTime()) ? now : new Date();
  const lookbackSeconds = Math.max(60, timeframeSeconds * 3);
  const start = new Date(end.getTime() - lookbackSeconds * 1000);

  return {
    start: start.toISOString(),
    end: end.toISOString(),
    limit: 5,
  };
}

export function buildLiveCandleFromPriceUpdate({
  config,
  price,
  closedCandles,
  currentLiveCandle,
  fetchedAt = new Date(),
}: BuildLiveCandleFromPriceOptions): ProjectXMarketCandle | null {
  if (!Number.isFinite(price.price)) {
    return currentLiveCandle;
  }

  const bucketTimestamp = buildTimeframeBucketTimestamp(price.timestamp, config.timeframe_unit, config.timeframe_unit_number);
  if (!bucketTimestamp) {
    return currentLiveCandle;
  }

  const bucketTimestampMs = Date.parse(bucketTimestamp);
  const closedBase = closedCandles.find((candle) => Date.parse(candle.timestamp) === bucketTimestampMs) ?? null;
  const liveBase = currentLiveCandle && Date.parse(currentLiveCandle.timestamp) === bucketTimestampMs ? currentLiveCandle : null;
  const base = liveBase ?? closedBase;
  const open = base?.open ?? price.price;
  const high = Math.max(base?.high ?? price.price, price.price);
  const low = Math.min(base?.low ?? price.price, price.price);

  return {
    id: closedBase?.id ?? null,
    contract_id: price.contract_id || config.contract_id,
    symbol: price.symbol ?? config.symbol ?? null,
    live: false,
    unit: config.timeframe_unit,
    unit_number: config.timeframe_unit_number,
    timestamp: bucketTimestamp,
    open,
    high,
    low,
    close: price.price,
    volume: base?.volume ?? 0,
    is_partial: true,
    fetched_at: fetchedAt.toISOString(),
  };
}

function buildTimeframeBucketTimestamp(timestamp: string, unit: BotTimeframeUnit, unitNumber: number): string | null {
  const timestampMs = Date.parse(timestamp);
  if (!Number.isFinite(timestampMs)) {
    return null;
  }

  const normalizedUnitNumber = Math.max(1, Math.trunc(unitNumber));
  if (unit === "month") {
    const date = new Date(timestampMs);
    const bucketMonth = Math.floor(date.getUTCMonth() / normalizedUnitNumber) * normalizedUnitNumber;
    return new Date(Date.UTC(date.getUTCFullYear(), bucketMonth, 1, 0, 0, 0, 0)).toISOString();
  }

  const unitSeconds = UNIT_SECONDS_BY_NAME[unit];
  const bucketMs = unitSeconds * normalizedUnitNumber * 1000;
  return new Date(Math.floor(timestampMs / bucketMs) * bucketMs).toISOString();
}

function findActiveBuySideLiquidity(
  candles: CandlestickData<UTCTimestamp>[],
  referencePrice: number,
  swingSpan: number,
): LiquidityLevel | null {
  let selected: LiquidityLevel | null = null;

  for (let index = swingSpan; index < candles.length - swingSpan; index += 1) {
    const candle = candles[index];
    if (!isSwingHigh(candles, index, swingSpan) || candle.high <= referencePrice) {
      continue;
    }
    if (isBuySideLevelSwept(candles, index, candle.high)) {
      continue;
    }

    if (!selected || candle.high < selected.price || (candle.high === selected.price && index > selected.index)) {
      selected = {
        side: "buy",
        price: candle.high,
        time: candle.time as UTCTimestamp,
        index,
      };
    }
  }

  return selected;
}

function findActiveSellSideLiquidity(
  candles: CandlestickData<UTCTimestamp>[],
  referencePrice: number,
  swingSpan: number,
): LiquidityLevel | null {
  let selected: LiquidityLevel | null = null;

  for (let index = swingSpan; index < candles.length - swingSpan; index += 1) {
    const candle = candles[index];
    if (!isSwingLow(candles, index, swingSpan) || candle.low >= referencePrice) {
      continue;
    }
    if (isSellSideLevelSwept(candles, index, candle.low)) {
      continue;
    }

    if (!selected || candle.low > selected.price || (candle.low === selected.price && index > selected.index)) {
      selected = {
        side: "sell",
        price: candle.low,
        time: candle.time as UTCTimestamp,
        index,
      };
    }
  }

  return selected;
}

function isSwingHigh(candles: CandlestickData<UTCTimestamp>[], index: number, swingSpan: number): boolean {
  const high = candles[index].high;
  for (let offset = 1; offset <= swingSpan; offset += 1) {
    if (candles[index - offset].high >= high || candles[index + offset].high > high) {
      return false;
    }
  }
  return true;
}

function isSwingLow(candles: CandlestickData<UTCTimestamp>[], index: number, swingSpan: number): boolean {
  const low = candles[index].low;
  for (let offset = 1; offset <= swingSpan; offset += 1) {
    if (candles[index - offset].low <= low || candles[index + offset].low < low) {
      return false;
    }
  }
  return true;
}

function isBuySideLevelSwept(candles: CandlestickData<UTCTimestamp>[], index: number, price: number): boolean {
  return candles.slice(index + 1).some((candle) => candle.high > price);
}

function isSellSideLevelSwept(candles: CandlestickData<UTCTimestamp>[], index: number, price: number): boolean {
  return candles.slice(index + 1).some((candle) => candle.low < price);
}

function isFiniteCandlestick(candle: CandlestickData<UTCTimestamp>): boolean {
  return (
    Number.isFinite(Number(candle.time)) &&
    Number.isFinite(candle.open) &&
    Number.isFinite(candle.high) &&
    Number.isFinite(candle.low) &&
    Number.isFinite(candle.close)
  );
}

export function buildSignalMarkers(input: {
  candles: CandlestickData<UTCTimestamp>[];
  activityDecisions?: BotDecision[];
  lastEvaluation?: BotEvaluation | null;
  timeframeUnit?: BotTimeframeUnit;
  timeframeUnitNumber?: number;
}): SeriesMarker<UTCTimestamp>[] {
  const sortedCandleTimes = input.candles.map((candle) => Number(candle.time)).sort((left, right) => left - right);
  const candleTimes = new Set(sortedCandleTimes);
  const markersByKey = new Map<string, SeriesMarker<UTCTimestamp>>();

  for (const decision of input.activityDecisions ?? []) {
    const marker = buildDecisionMarker(decision, candleTimes, sortedCandleTimes, input.timeframeUnit, input.timeframeUnitNumber);
    if (marker) {
      markersByKey.set(marker.id ?? fallbackMarkerKey(decision), marker);
    }
  }

  if (input.lastEvaluation) {
    const marker = buildDecisionMarker(input.lastEvaluation.decision, candleTimes, sortedCandleTimes, input.timeframeUnit, input.timeframeUnitNumber);
    if (marker) {
      markersByKey.set(marker.id ?? fallbackMarkerKey(input.lastEvaluation.decision), marker);
    }
  }

  return Array.from(markersByKey.values()).sort((left, right) => Number(left.time) - Number(right.time));
}

function buildDecisionMarker(
  decision: BotDecision,
  candleTimes: Set<number>,
  sortedCandleTimes: number[],
  timeframeUnit?: BotTimeframeUnit,
  timeframeUnitNumber?: number,
): SeriesMarker<UTCTimestamp> | null {
  if (!SIGNAL_ACTIONS.has(decision.action)) {
    return null;
  }

  const time = toDecisionMarkerTimestamp(decision.candle_timestamp, candleTimes, sortedCandleTimes, timeframeUnit, timeframeUnitNumber);
  if (time === null || !candleTimes.has(Number(time))) {
    return null;
  }

  if (decision.action === "BUY") {
    return {
      id: markerId(decision),
      time,
      position: "belowBar",
      shape: "arrowUp",
      color: "rgb(34,197,94)",
      text: "BUY",
      size: 1.15,
    };
  }

  if (decision.action === "SELL") {
    return {
      id: markerId(decision),
      time,
      position: "aboveBar",
      shape: "arrowDown",
      color: "rgb(244,63,94)",
      text: "SELL",
      size: 1.15,
    };
  }

  return {
    id: markerId(decision),
    time,
    position: "inBar",
    shape: "circle",
    color: "rgb(148,163,184)",
    text: "HOLD",
    size: 0.75,
  };
}

function toDecisionMarkerTimestamp(
  value: string | null,
  candleTimes: Set<number>,
  sortedCandleTimes: number[],
  timeframeUnit?: BotTimeframeUnit,
  timeframeUnitNumber?: number,
): UTCTimestamp | null {
  if (!value) {
    return null;
  }

  if (!timeframeUnit) {
    return toUtcTimestamp(value);
  }

  const bucketTime = toUtcTimestamp(buildTimeframeBucketTimestamp(value, timeframeUnit, timeframeUnitNumber ?? 1));
  if (bucketTime !== null && candleTimes.has(Number(bucketTime))) {
    return bucketTime;
  }

  const exactTime = toUtcTimestamp(value);
  if (exactTime === null) {
    return null;
  }

  const intervalSeconds = UNIT_SECONDS_BY_NAME[timeframeUnit] * Math.max(1, Math.trunc(timeframeUnitNumber ?? 1));
  const timestampSeconds = Number(exactTime);
  for (let index = sortedCandleTimes.length - 1; index >= 0; index -= 1) {
    const candleTime = sortedCandleTimes[index];
    if (candleTime <= timestampSeconds && timestampSeconds < candleTime + intervalSeconds) {
      return candleTime as UTCTimestamp;
    }
  }

  return bucketTime;
}

function markerId(decision: BotDecision): string | undefined {
  return decision.id > 0 ? `decision-${decision.id}` : undefined;
}

function fallbackMarkerKey(decision: BotDecision): string {
  return `${decision.action}:${decision.candle_timestamp ?? "none"}:${decision.reason}`;
}
