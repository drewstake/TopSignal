import type { CandlestickData, LineData, SeriesMarker, UTCTimestamp } from "lightweight-charts";

import type { BotConfig, BotDecision, BotEvaluation, BotTimeframeUnit, ProjectXMarketCandle } from "../../lib/types";

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

export function buildSignalMarkers(input: {
  candles: CandlestickData<UTCTimestamp>[];
  activityDecisions?: BotDecision[];
  lastEvaluation?: BotEvaluation | null;
}): SeriesMarker<UTCTimestamp>[] {
  const candleTimes = new Set(input.candles.map((candle) => Number(candle.time)));
  const markersByKey = new Map<string, SeriesMarker<UTCTimestamp>>();

  for (const decision of input.activityDecisions ?? []) {
    const marker = buildDecisionMarker(decision, candleTimes);
    if (marker) {
      markersByKey.set(marker.id ?? fallbackMarkerKey(decision), marker);
    }
  }

  if (input.lastEvaluation) {
    const marker = buildDecisionMarker(input.lastEvaluation.decision, candleTimes);
    if (marker) {
      markersByKey.set(marker.id ?? fallbackMarkerKey(input.lastEvaluation.decision), marker);
    }
  }

  return Array.from(markersByKey.values()).sort((left, right) => Number(left.time) - Number(right.time));
}

function buildDecisionMarker(
  decision: BotDecision,
  candleTimes: Set<number>,
): SeriesMarker<UTCTimestamp> | null {
  if (!SIGNAL_ACTIONS.has(decision.action)) {
    return null;
  }

  const time = toUtcTimestamp(decision.candle_timestamp);
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

function markerId(decision: BotDecision): string | undefined {
  return decision.id > 0 ? `decision-${decision.id}` : undefined;
}

function fallbackMarkerKey(decision: BotDecision): string {
  return `${decision.action}:${decision.candle_timestamp ?? "none"}:${decision.reason}`;
}
