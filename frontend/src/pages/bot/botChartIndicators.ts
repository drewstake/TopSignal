import type { ProjectXMarketCandle } from "../../lib/types";
import { buildVwapData, type BotChartMarket } from "./botChartData";

// Matches backend/app/services/topbot_strategy.py. The strategy replaces saved
// parameter overrides with this preset; chart overlays must do the same.
export const TOPBOT_PRESET = {
  revision: "mnq_ema_vwap_pullback_v5_bracket_exits",
  emaPeriod: 20,
  shortTrendEmaPeriod: 50,
  sessionStart: "09:30",
  sessionEnd: "15:45",
  positionSize: 1,
  stopPoints: 50,
  targetPoints: 50,
  timeframeUnit: "minute",
  timeframeUnitNumber: 5,
  symbol: "MNQ",
} as const;

export function resolveBotChartIndicators(market: BotChartMarket | null) {
  const topbot = market?.strategy_type === "topbot_adaptive";
  const ema = topbot || market?.strategy_type === "pullback_trap_reversal" ||
    market?.strategy_type === "ema_scalping" || market?.strategy_type === "ema_trend_pullback";
  const fastPeriod = topbot ? TOPBOT_PRESET.emaPeriod : market?.fast_period ?? 0;
  const slowPeriod = topbot ? TOPBOT_PRESET.shortTrendEmaPeriod : market?.slow_period ?? 0;
  const sessionStart = topbot ? TOPBOT_PRESET.sessionStart : "18:00";
  return {
    topbot,
    ema,
    showAverages: topbot || ema || market?.strategy_type === "sma_cross",
    fastPeriod,
    slowPeriod,
    fastLabel: topbot ? `EMA ${fastPeriod}` : `Fast ${ema ? "EMA" : "SMA"} ${fastPeriod}`,
    slowLabel: topbot ? `EMA ${slowPeriod} · short filter` : `Slow ${ema ? "EMA" : "SMA"} ${slowPeriod}`,
    sessionStart,
    vwapLabel: topbot ? `TopBot VWAP · ${sessionStart}–${TOPBOT_PRESET.sessionEnd} ET` : "VWAP · 18:00 ET",
  };
}

const sessionClock = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

export function buildBotChartVwap(candles: ProjectXMarketCandle[], topbot: boolean) {
  const sessionCandles = topbot ? candles.filter(candle => {
    const timestamp = Date.parse(candle.timestamp);
    if (!Number.isFinite(timestamp)) return false;
    const time = sessionClock.format(timestamp);
    // The strategy enters only on bars closing through 15:45. Do not extend
    // its VWAP through overnight candles into the next morning's display.
    return time >= TOPBOT_PRESET.sessionStart && time < TOPBOT_PRESET.sessionEnd;
  }) : candles;
  return buildVwapData(sessionCandles, {
    sessionStartTime: topbot ? TOPBOT_PRESET.sessionStart : "18:00",
    sessionTimeZone: "America/New_York",
  });
}
