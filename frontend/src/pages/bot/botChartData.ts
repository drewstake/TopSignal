import type { CandlestickData, LineData, SeriesMarker, UTCTimestamp } from "lightweight-charts";
import { BOT_CHART_BUY_COLOR, BOT_CHART_SELL_COLOR } from "./botChartTheme";

import type { BotConfig, BotDecision, BotEvaluation, BotOrderAttempt, BotTimeframeUnit, ProjectXMarketCandle, ProjectXMarketPrice } from "../../lib/types";

// Charting needs a market and timeframe, not a persisted bot or trading account.
export type BotChartMarket = Pick<BotConfig,
  "contract_id" | "symbol" | "timeframe_unit" | "timeframe_unit_number" | "lookback_bars"
> & Partial<Pick<BotConfig, "id" | "strategy_type" | "strategy_params" | "fast_period" | "slow_period">>;

export const BOT_CHART_MAX_BARS = 2_000;
export const BOT_CHART_MIN_BARS = 300;
export const BOT_CHART_INITIAL_BARS = BOT_CHART_MIN_BARS;
export const BOT_CHART_TIMEFRAMES = [
  { id: "1m", label: "1m", unit: "minute", unitNumber: 1 },
  { id: "5m", label: "5m", unit: "minute", unitNumber: 5 },
  { id: "15m", label: "15m", unit: "minute", unitNumber: 15 },
  { id: "1h", label: "1H", unit: "hour", unitNumber: 1 },
  { id: "4h", label: "4H", unit: "hour", unitNumber: 4 },
  { id: "1d", label: "1D", unit: "day", unitNumber: 1 },
] as const satisfies readonly {
  id: string;
  label: string;
  unit: BotTimeframeUnit;
  unitNumber: number;
}[];
export type BotChartTimeframe = (typeof BOT_CHART_TIMEFRAMES)[number];
export type BotChartTimeframeId = BotChartTimeframe["id"];
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
  config: BotChartMarket;
  price: ProjectXMarketPrice;
  closedCandles: ProjectXMarketCandle[];
  currentLiveCandle: ProjectXMarketCandle | null;
  fetchedAt?: Date;
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
    const existing = byTime.get(timestampSeconds);
    if (existing && !existing.candle.is_partial && candle.is_partial) {
      continue;
    }
    byTime.set(timestampSeconds, {
      time,
      timestampSeconds,
      candle,
    });
  }

  return Array.from(byTime.values()).sort((a, b) => a.timestampSeconds - b.timestampSeconds)
    .map(buildCanonicalCandlestick);
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

export function buildEmaData(candles: CandlestickData<UTCTimestamp>[], period: number): LineData<UTCTimestamp>[] {
  const normalizedPeriod = Math.trunc(period);
  if (normalizedPeriod <= 0 || candles.length < normalizedPeriod) {
    return [];
  }

  const seed = candles.slice(0, normalizedPeriod).reduce((sum, candle) => sum + candle.close, 0) / normalizedPeriod;
  const multiplier = 2 / (normalizedPeriod + 1);
  const output: LineData<UTCTimestamp>[] = [{ time: candles[normalizedPeriod - 1].time, value: seed }];
  let current = seed;

  for (let index = normalizedPeriod; index < candles.length; index += 1) {
    current = ((candles[index].close - current) * multiplier) + current;
    output.push({
      time: candles[index].time,
      value: current,
    });
  }

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
  const sessionStartMinutes = parseSessionStartMinutes(options.sessionStartTime ?? "18:00");
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

/** Prepare history once per provider refresh; quote ticks only reprice the tail. */
export function prepareLiveVwap(candles: ProjectXMarketCandle[]) {
  const rows = buildSortedVwapCandles(candles);
  const formatter = new Intl.DateTimeFormat("en-US", { timeZone: DEFAULT_VWAP_SESSION_TIME_ZONE,
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  type State = { session: string | null; volume: number; priceVolume: number };
  const empty: State = { session: null, volume: 0, priceVolume: 0 };
  const add = (state: State, row: ValidMarketCandle): State => {
    const session = buildVwapSessionKey(row.candle.timestamp, formatter, 18 * 60);
    const prior = session === state.session ? state : empty;
    const volume = Number.isFinite(row.candle.volume) && row.candle.volume > 0 ? row.candle.volume : 0;
    return { session, volume: prior.volume + volume,
      priceVolume: prior.priceVolume + volume * (row.candle.high + row.candle.low + row.candle.close) / 3 };
  };
  let state = empty, beforeLast = empty, prefixLength = 0;
  const output: LineData<UTCTimestamp>[] = [];
  for (const row of rows) {
    beforeLast = state; prefixLength = output.length;
    state = add(state, row);
    if (state.volume > 0) output.push({ time: row.time, value: state.priceVolume / state.volume });
  }
  const last = rows.at(-1);
  return (live: ProjectXMarketCandle | null): LineData<UTCTimestamp>[] => {
    if (!live) return output;
    const row = buildSortedVwapCandles([live])[0];
    if (!row) return output;
    if (last && row.time < last.time) return buildVwapData([...candles, live]);
    const replacing = last && row.time === last.time;
    if (replacing && !last.candle.is_partial && live.is_partial) return output;
    const next = add(replacing ? beforeLast : state, row);
    const prefix = replacing ? output.slice(0, prefixLength) : output;
    return next.volume > 0 ? [...prefix, { time: row.time, value: next.priceVolume / next.volume }] : prefix;
  };
}

export function prepareLiveCandlesticks(candles: ProjectXMarketCandle[]) {
  const base = buildCandlestickData(candles);
  const last = base.at(-1);
  const closedTimes = new Set(candles.filter(c => !c.is_partial).map(c => toUtcTimestamp(c.timestamp)));
  return (live: ProjectXMarketCandle | null) => {
    if (!live) return base;
    const tail = buildCandlestickData([live])[0];
    if (!tail || (live.is_partial && closedTimes.has(tail.time))) return base;
    if (!last || tail.time > last.time) return [...base, tail];
    if (tail.time === last.time) return [...base.slice(0, -1), tail];
    return buildCandlestickData([...candles, live]);
  };
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

export function buildBotChartQuery(config: BotChartMarket, now: Date = new Date()): BotChartQueryWindow {
  const lookbackBars = Math.trunc(config.lookback_bars);
  const limit = Math.min(BOT_CHART_MAX_BARS, Math.max(BOT_CHART_MIN_BARS, lookbackBars * 4));
  return buildBotChartQueryForLimit(config, limit, now);
}

/** Shared computed levels for chart lines and analysis; input is closed candles. */
export function buildMarketLevels(candles: CandlestickData<UTCTimestamp>[]) {
  const history = candles.filter(isFiniteCandlestick).slice().sort((a, b) => Number(a.time) - Number(b.time))
    .slice(-BOT_CHART_MAX_BARS);
  const liquidity = buildLiquidityLevels(history);
  return {
    liquidity,
    support: liquidity.find(level => level.side === "sell")?.price ?? null,
    resistance: liquidity.find(level => level.side === "buy")?.price ?? null,
  };
}

export function buildInitialBotChartQuery(config: BotChartMarket, now: Date = new Date()): BotChartQueryWindow {
  return buildBotChartQueryForLimit(config, BOT_CHART_INITIAL_BARS, now);
}

function buildBotChartQueryForLimit(config: BotChartMarket, limit: number, now: Date): BotChartQueryWindow {
  const normalizedLimit = Math.min(BOT_CHART_MAX_BARS, Math.max(1, Math.trunc(limit)));
  const timeframeSeconds =
    UNIT_SECONDS_BY_NAME[config.timeframe_unit] * Math.max(1, Math.trunc(config.timeframe_unit_number));
  const end = Number.isFinite(now.getTime()) ? now : new Date();
  const start = new Date(end.getTime() - timeframeSeconds * normalizedLimit * CHART_LOOKBACK_MULTIPLIER * 1000);

  return {
    start: start.toISOString(),
    end: end.toISOString(),
    limit: normalizedLimit,
  };
}

export const BOT_CHART_HISTORY_PAGE_BARS = 500;

/**
 * Query window for loading candles older than the earliest loaded bar, used by
 * on-demand history expansion. The span is padded (x3) so session closures do
 * not starve the page of bars.
 */
export function buildOlderCandlesQuery(
  config: BotChartMarket,
  earliestLoadedTimestamp: string,
  pageBars: number = BOT_CHART_HISTORY_PAGE_BARS,
): BotChartQueryWindow | null {
  const earliestMs = Date.parse(earliestLoadedTimestamp);
  if (!Number.isFinite(earliestMs)) {
    return null;
  }

  const limit = Math.max(1, Math.trunc(pageBars));
  const timeframeSeconds =
    UNIT_SECONDS_BY_NAME[config.timeframe_unit] * Math.max(1, Math.trunc(config.timeframe_unit_number));
  const end = new Date(earliestMs - 1000);
  const start = new Date(end.getTime() - timeframeSeconds * limit * CHART_LOOKBACK_MULTIPLIER * 1000);
  if (start.getTime() >= end.getTime()) {
    return null;
  }

  return {
    start: start.toISOString(),
    end: end.toISOString(),
    limit,
  };
}

export function buildBotLivePriceQuery(config: BotChartMarket, now: Date = new Date()): BotChartQueryWindow {
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
  const fetchedBase = closedCandles.find((candle) => Date.parse(candle.timestamp) === bucketTimestampMs) ?? null;
  const liveBase = currentLiveCandle && Date.parse(currentLiveCandle.timestamp) === bucketTimestampMs ? currentLiveCandle : null;
  const closedBase = [fetchedBase, liveBase].find((candle) => candle !== null && !candle.is_partial) ?? null;
  if (closedBase) {
    return closedBase;
  }

  const base = liveBase ?? fetchedBase;
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
    id: fetchedBase?.id ?? null,
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

function buildCanonicalCandlestick(row: ValidMarketCandle): CandlestickData<UTCTimestamp> {
  const { candle } = row;
  return {
    time: row.time,
    open: candle.open,
    high: candle.high,
    low: candle.low,
    close: candle.close,
  };
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
    const existing = byTime.get(timestampSeconds);
    if (existing && !existing.candle.is_partial && candle.is_partial) {
      continue;
    }
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

// Session keys are pure functions of (timezone, session start, timestamp); the
// Intl conversion dominates VWAP cost, so memoize it. Live ticks recompute the
// VWAP series several times per second over the same historical timestamps.
const vwapSessionKeyCache = new Map<string, string>();
const VWAP_SESSION_KEY_CACHE_LIMIT = 60_000;

function buildVwapSessionKey(
  timestamp: string,
  formatter: Intl.DateTimeFormat,
  sessionStartMinutes: number,
): string {
  const cacheKey = `${formatter.resolvedOptions().timeZone}|${sessionStartMinutes}|${timestamp}`;
  const cached = vwapSessionKeyCache.get(cacheKey);
  if (cached !== undefined) {
    return cached;
  }

  const key = computeVwapSessionKey(timestamp, formatter, sessionStartMinutes);
  if (vwapSessionKeyCache.size >= VWAP_SESSION_KEY_CACHE_LIMIT) {
    vwapSessionKeyCache.clear();
  }
  vwapSessionKeyCache.set(cacheKey, key);
  return key;
}

function computeVwapSessionKey(
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
  orderAttempts?: BotOrderAttempt[];
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

  for (const attempt of input.orderAttempts ?? []) {
    if (attempt.execution_mode !== "live") continue;
    for (const event of attempt.execution_observations ?? []) {
      const time = toDecisionMarkerTimestamp(event.timestamp, candleTimes, sortedCandleTimes, input.timeframeUnit, input.timeframeUnitNumber);
      if (time === null || !candleTimes.has(Number(time))) continue;
      const id = event.kind === "fill" ? `fill-${event.id}` : `flat-${attempt.id}`;
      markersByKey.set(id, { id, time, position: "inBar", shape: "square", size: .65,
        color: "rgb(56,189,248)", text: event.kind === "fill" ? `FILL ${attempt.side} @ ${event.price}` : "FLAT VERIFIED" });
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
  if (decision.decision_type !== "signal" && decision.decision_type !== "risk_reject") return null;

  const chartSeconds = timeframeUnit ? UNIT_SECONDS_BY_NAME[timeframeUnit] * (timeframeUnitNumber ?? 1) : 300;
  const payload = decision.raw_payload ?? {};
  const decisionSeconds = typeof payload.decision_interval_seconds === "number" ? payload.decision_interval_seconds : 300;
  const stamp = decision.candle_timestamp ? Date.parse(decision.candle_timestamp) : NaN;
  const alignedStamp = Number.isFinite(stamp) && Number.isFinite(decisionSeconds)
    ? new Date(stamp + Math.max(0, decisionSeconds - chartSeconds) * 1000).toISOString() : null;
  const time = toDecisionMarkerTimestamp(alignedStamp, candleTimes, sortedCandleTimes, timeframeUnit, timeframeUnitNumber);
  if (time === null || !candleTimes.has(Number(time))) {
    return null;
  }

  if (decision.decision_type === "risk_reject") return {
    id: markerId(decision), time, position: "aboveBar", shape: "circle",
    color: "rgb(148,163,184)", text: `○ BLOCKED ${decision.action}`, size: 0,
  };

  if (decision.action === "BUY") {
    return {
      id: markerId(decision),
      time,
      position: "belowBar",
      shape: "arrowUp",
      color: BOT_CHART_BUY_COLOR,
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
      color: BOT_CHART_SELL_COLOR,
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
