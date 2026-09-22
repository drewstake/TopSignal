import type { ProjectXMarketCandle } from "../../lib/types";
import { buildVwapData, type BotChartMarket } from "./botChartData";

// Matches backend/app/services/topbot_strategy.py. The strategy replaces saved
// parameter overrides with this preset; chart overlays must do the same.
export const TOPBOT_PRESET = {
  revision: "mnq_ema_vwap_pullback_v6_all_sessions",
  emaPeriod: 20,
  shortTrendEmaPeriod: 50,
  sessionStart: "09:30",
  overnightStart: "18:00",
  positionSize: 1,
  stopPoints: 50,
  targetPoints: 50,
  timeframeUnit: "minute",
  timeframeUnitNumber: 5,
  symbol: "MNQ",
} as const;

export function resolveBotChartIndicators(market: BotChartMarket | null) {
  const topbot = market?.strategy_type === "topbot_adaptive";
  const mathematical = topbot && market?.strategy_params?.revision === "mnq_bayesian_payoff_v1";
  const ema = (topbot && !mathematical) || market?.strategy_type === "pullback_trap_reversal" ||
    market?.strategy_type === "ema_scalping" || market?.strategy_type === "ema_trend_pullback";
  const fastPeriod = topbot ? TOPBOT_PRESET.emaPeriod : market?.fast_period ?? 0;
  const slowPeriod = topbot ? TOPBOT_PRESET.shortTrendEmaPeriod : market?.slow_period ?? 0;
  const sessionStart = topbot ? TOPBOT_PRESET.sessionStart : "18:00";
  return {
    topbot,
    ema,
    showAverages: (topbot && !mathematical) || ema || market?.strategy_type === "sma_cross",
    fastPeriod,
    slowPeriod,
    fastLabel: topbot ? `EMA ${fastPeriod}` : `Fast ${ema ? "EMA" : "SMA"} ${fastPeriod}`,
    slowLabel: topbot ? `EMA ${slowPeriod} · short filter` : `Slow ${ema ? "EMA" : "SMA"} ${slowPeriod}`,
    sessionStart,
    vwapLabel: mathematical ? "VWAP · context only" : topbot ? "TopBot VWAP · resets 09:30 / 18:00 ET" : "VWAP · 18:00 ET",
  };
}

const sessionClock = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

export function buildBotChartVwap(candles: ProjectXMarketCandle[], topbot: boolean) {
  if (!topbot) return buildVwapData(candles, {
    sessionStartTime: "18:00", sessionTimeZone: "America/New_York",
  });
  const regular: ProjectXMarketCandle[] = [];
  const overnight: ProjectXMarketCandle[] = [];
  for (const candle of candles) {
    const timestamp = Date.parse(candle.timestamp);
    if (!Number.isFinite(timestamp)) continue;
    const time = sessionClock.format(timestamp);
    const segment = time >= TOPBOT_PRESET.sessionStart && time < TOPBOT_PRESET.overnightStart
      ? regular : overnight;
    segment.push(candle);
  }
  return [
    ...buildVwapData(regular, { sessionStartTime: TOPBOT_PRESET.sessionStart, sessionTimeZone: "America/New_York" }),
    ...buildVwapData(overnight, { sessionStartTime: TOPBOT_PRESET.overnightStart, sessionTimeZone: "America/New_York" }),
  ].sort((a, b) => Number(a.time) - Number(b.time));
}
