import type { ProjectXMarketCandle } from "../../lib/types";
import { buildVwapData, type BotChartMarket } from "./botChartData";

// TopBot's only strategy is the mathematical model; it has no moving-average
// entry filters. VWAP is descriptive context that resets at the 18:00 ET session boundary (New York time).
export const TOPBOT_VWAP_RESETS = {
  regular: "18:00",
  overnight: "18:00",
} as const;

export function resolveBotChartIndicators(market: BotChartMarket | null) {
  const topbot = market?.strategy_type === "topbot_adaptive";
  const ema = market?.strategy_type === "pullback_trap_reversal" ||
    market?.strategy_type === "ema_scalping" || market?.strategy_type === "ema_trend_pullback";
  const fastPeriod = market?.fast_period ?? 0;
  const slowPeriod = market?.slow_period ?? 0;
  return {
    topbot,
    ema,
    showAverages: !topbot && (ema || market?.strategy_type === "sma_cross"),
    fastPeriod,
    slowPeriod,
    fastLabel: `Fast ${ema ? "EMA" : "SMA"} ${fastPeriod}`,
    slowLabel: `Slow ${ema ? "EMA" : "SMA"} ${slowPeriod}`,
    sessionStart: topbot ? TOPBOT_VWAP_RESETS.regular : "18:00",
    vwapLabel: topbot ? "VWAP · context only" : "VWAP · 18:00 ET",
  };
}

export function buildBotChartVwap(candles: ProjectXMarketCandle[], _topbot: boolean) {
  void _topbot; // Kept for call-site compatibility; every chart uses one session definition.
  return buildVwapData(candles, { sessionStartTime: "18:00", sessionTimeZone: "America/New_York" });
}
