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

interface BuildCandlestickDataOptions {
  bridgeConsecutiveGaps?: boolean;
}

interface BuildVwapDataOptions {
  sessionStartTime?: string;
  sessionTimeZone?: string;
}

interface ValidMarketCandle {
  time: UTCTimestamp;
  timestampSeconds: number;
  candle: ProjectXMarketCandle;
}

interface VwapSessionDateTimeParts {
  year: number;
  month: number;
  day: number;
  minutesSinceMidnight: number;
}

const DEFAULT_VWAP_SESSION_TIME_ZONE = "America/New_York";

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

export function buildCandlestickData(
  candles: ProjectXMarketCandle[],
  options: BuildCandlestickDataOptions = {},
): CandlestickData<UTCTimestamp>[] {
  const byTime = new Map<number, ValidMarketCandle>();

  for (const candle of candles) {
    const time = toUtcTimestamp(candle.timestamp);
    if (time === null) {
      continue;
    }
    if (![candle.open, candle.high, candle.low, candle.close].every(Number.isFinite)) {
      continue;
    }

    const timestampSeconds = Number(time);
    byTime.set(timestampSeconds, {
      time,
      timestampSeconds,
      candle,
    });
  }

  const bridgeConsecutiveGaps = options.bridgeConsecutiveGaps ?? true;
  const sortedCandles = Array.from(byTime.values()).sort((left, right) => left.timestampSeconds - right.timestampSeconds);
  return sortedCandles.map((row, index) => {
    const previous = index > 0 ? sortedCandles[index - 1] : null;
    const open =
      bridgeConsecutiveGaps && previous && areConsecutiveIntradayCandles(previous, row)
        ? previous.candle.close
        : row.candle.open;
    return buildDisplayCandlestick(row, open);
  });
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

export function buildVwapData(
  candles: ProjectXMarketCandle[],
  options: BuildVwapDataOptions = {},
): LineData<UTCTimestamp>[] {
  const sortedCandles = buildSortedVwapCandles(candles);
  if (sortedCandles.length === 0) {
    return [];
  }

  const sessionTimeZone = options.sessionTimeZone ?? DEFAULT_VWAP_SESSION_TIME_ZONE;
  const sessionStartMinutes = parseSessionStartMinutes(options.sessionStartTime);
  const sessionFormatter = new Intl.DateTimeFormat("en-US", {
    timeZone: sessionTimeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });

  const output: LineData<UTCTimestamp>[] = [];
  let currentSessionKey: string | null = null;
  let cumulativeVolume = 0;
  let cumulativePriceVolume = 0;
  let currentValue: number | null = null;

  for (const row of sortedCandles) {
    const sessionKey = buildVwapSessionKey(row.candle.timestamp, sessionFormatter, sessionStartMinutes);
    if (sessionKey !== currentSessionKey) {
      currentSessionKey = sessionKey;
      cumulativeVolume = 0;
      cumulativePriceVolume = 0;
      currentValue = null;
    }

    if (Number.isFinite(row.candle.volume) && row.candle.volume > 0) {
      const typicalPrice = (row.candle.high + row.candle.low + row.candle.close) / 3;
      cumulativeVolume += row.candle.volume;
      cumulativePriceVolume += typicalPrice * row.candle.volume;
      currentValue = cumulativePriceVolume / cumulativeVolume;
    }

    if (currentValue !== null) {
      output.push({
        time: row.time,
        value: currentValue,
      });
    }
  }

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
  const previousClose =
    base === null
      ? findPreviousConsecutiveClose({
          bucketTimestampMs,
          closedCandles,
          currentLiveCandle,
        })
      : null;
  const open = base?.open ?? previousClose ?? price.price;
  const high = Math.max(base?.high ?? open, open, price.price);
  const low = Math.min(base?.low ?? open, open, price.price);

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

function buildDisplayCandlestick(row: ValidMarketCandle, open: number): CandlestickData<UTCTimestamp> {
  const { candle } = row;
  const high = Math.max(open, candle.open, candle.high, candle.low, candle.close);
  const low = Math.min(open, candle.open, candle.high, candle.low, candle.close);

  return {
    time: row.time,
    open,
    high,
    low,
    close: candle.close,
  };
}

function areConsecutiveIntradayCandles(previous: ValidMarketCandle, current: ValidMarketCandle): boolean {
  if (previous.candle.unit !== current.candle.unit || previous.candle.unit_number !== current.candle.unit_number) {
    return false;
  }

  const intervalSeconds = intradayIntervalSeconds(current.candle);
  return intervalSeconds !== null && current.timestampSeconds - previous.timestampSeconds === intervalSeconds;
}

function findPreviousConsecutiveClose(input: {
  bucketTimestampMs: number;
  closedCandles: ProjectXMarketCandle[];
  currentLiveCandle: ProjectXMarketCandle | null;
}): number | null {
  let selectedTimestampMs = Number.NEGATIVE_INFINITY;
  let selectedClose: number | null = null;

  for (const candle of [...input.closedCandles, input.currentLiveCandle]) {
    if (!candle || !Number.isFinite(candle.close)) {
      continue;
    }

    const timestampMs = Date.parse(candle.timestamp);
    if (!Number.isFinite(timestampMs) || timestampMs >= input.bucketTimestampMs) {
      continue;
    }

    const intervalSeconds = intradayIntervalSeconds(candle);
    const gapSeconds = (input.bucketTimestampMs - timestampMs) / 1000;
    if (intervalSeconds === null || gapSeconds !== intervalSeconds || timestampMs <= selectedTimestampMs) {
      continue;
    }

    selectedTimestampMs = timestampMs;
    selectedClose = candle.close;
  }

  return selectedClose;
}

function intradayIntervalSeconds(candle: Pick<ProjectXMarketCandle, "unit" | "unit_number">): number | null {
  if (candle.unit !== "second" && candle.unit !== "minute" && candle.unit !== "hour") {
    return null;
  }

  return UNIT_SECONDS_BY_NAME[candle.unit] * Math.max(1, Math.trunc(candle.unit_number));
}

function buildSortedVwapCandles(candles: ProjectXMarketCandle[]): ValidMarketCandle[] {
  const byTime = new Map<number, ValidMarketCandle>();

  for (const candle of candles) {
    const time = toUtcTimestamp(candle.timestamp);
    if (time === null) {
      continue;
    }
    if (![candle.high, candle.low, candle.close].every(Number.isFinite)) {
      continue;
    }

    const timestampSeconds = Number(time);
    byTime.set(timestampSeconds, {
      time,
      timestampSeconds,
      candle,
    });
  }

  return Array.from(byTime.values()).sort((left, right) => left.timestampSeconds - right.timestampSeconds);
}

function parseSessionStartMinutes(value: string | null | undefined): number {
  if (!value) {
    return 0;
  }

  const match = /^(\d{1,2}):(\d{2})/.exec(value.trim());
  if (!match) {
    return 0;
  }

  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (!Number.isInteger(hour) || !Number.isInteger(minute) || hour < 0 || hour > 23 || minute < 0 || minute > 59) {
    return 0;
  }

  return hour * 60 + minute;
}

function buildVwapSessionKey(
  timestamp: string,
  formatter: Intl.DateTimeFormat,
  sessionStartMinutes: number,
): string {
  const parts = vwapSessionDateTimeParts(timestamp, formatter);
  if (!parts) {
    return timestamp;
  }

  if (parts.minutesSinceMidnight >= sessionStartMinutes) {
    return formatVwapDateKey(parts.year, parts.month, parts.day);
  }

  const previousDay = new Date(Date.UTC(parts.year, parts.month - 1, parts.day) - 24 * 60 * 60 * 1000);
  return formatVwapDateKey(previousDay.getUTCFullYear(), previousDay.getUTCMonth() + 1, previousDay.getUTCDate());
}

function vwapSessionDateTimeParts(timestamp: string, formatter: Intl.DateTimeFormat): VwapSessionDateTimeParts | null {
  const timestampMs = Date.parse(timestamp);
  if (!Number.isFinite(timestampMs)) {
    return null;
  }

  const parsedParts: Partial<Record<Intl.DateTimeFormatPartTypes, number>> = {};
  for (const part of formatter.formatToParts(new Date(timestampMs))) {
    if (part.type === "year" || part.type === "month" || part.type === "day" || part.type === "hour" || part.type === "minute") {
      parsedParts[part.type] = Number(part.value);
    }
  }

  if (
    parsedParts.year === undefined ||
    parsedParts.month === undefined ||
    parsedParts.day === undefined ||
    parsedParts.hour === undefined ||
    parsedParts.minute === undefined
  ) {
    return null;
  }

  return {
    year: parsedParts.year,
    month: parsedParts.month,
    day: parsedParts.day,
    minutesSinceMidnight: parsedParts.hour * 60 + parsedParts.minute,
  };
}

function formatVwapDateKey(year: number, month: number, day: number): string {
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
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
