import type { ProjectXMarketCandle } from "../../lib/types";
import { buildVwapData, type BotChartMarket } from "./botChartData";

// TopBot's only strategy is the mathematical model; it has no moving-average
// entry filters. VWAP is descriptive context that resets at the regular and
// overnight opens (New York time).
export const TOPBOT_VWAP_RESETS = {
  regular: "09:30",
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
    const segment = time >= TOPBOT_VWAP_RESETS.regular && time < TOPBOT_VWAP_RESETS.overnight
      ? regular : overnight;
    segment.push(candle);
  }
  return [
    ...buildVwapData(regular, { sessionStartTime: TOPBOT_VWAP_RESETS.regular, sessionTimeZone: "America/New_York" }),
    ...buildVwapData(overnight, { sessionStartTime: TOPBOT_VWAP_RESETS.overnight, sessionTimeZone: "America/New_York" }),
  ].sort((a, b) => Number(a.time) - Number(b.time));
}
