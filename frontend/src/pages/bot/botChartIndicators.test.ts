import { expect, it } from "vitest";
import type { ProjectXMarketCandle } from "../../lib/types";
import { buildBotChartVwap, resolveBotChartIndicators } from "./botChartIndicators";

const market = { contract_id: "MNQ", symbol: "MNQ", timeframe_unit: "minute" as const,
  timeframe_unit_number: 5, lookback_bars: 200, fast_period: 9, slow_period: 21 };
const candle = (timestamp: string, price: number): ProjectXMarketCandle => ({
  id: null, contract_id: "MNQ", symbol: "MNQ", live: false, unit: "minute", unit_number: 5,
  timestamp, open: price, high: price, low: price, close: price, volume: 100,
  is_partial: false, fetched_at: null,
});

it("shows TopBot's actual 20/50 EMA periods despite legacy saved period settings", () => {
  const legacy = { ...market, strategy_type: "topbot_adaptive" as const,
    strategy_params: { ema_period: 9, short_trend_ema_period: 200, session_start: "18:00" } };
  expect(resolveBotChartIndicators(legacy)).toMatchObject({
    ema: true, showAverages: true, fastPeriod: 20, slowPeriod: 50, sessionStart: "09:30",
    fastLabel: "EMA 20", slowLabel: "EMA 50 · short filter",
    vwapLabel: "TopBot VWAP · 09:30–15:45 ET",
  });
  expect(resolveBotChartIndicators({ ...market, strategy_type: "sma_cross" })).toMatchObject({
    ema: false, showAverages: true, fastPeriod: 9, slowPeriod: 21, sessionStart: "18:00",
  });
});

it("keeps TopBot VWAP on its entry session and preserves the generic overnight VWAP", () => {
  const rows = [candle("2026-09-10T22:00:00Z", 90), candle("2026-09-11T13:30:00Z", 110),
    candle("2026-09-11T13:35:00Z", 105)];
  expect(buildBotChartVwap(rows, true).at(-1)?.value).toBe(107.5);
  expect(buildBotChartVwap(rows, false).at(-1)?.value).toBeCloseTo(101.6666667);
  const session = buildBotChartVwap([...rows, candle("2026-09-11T19:45:00Z", 500),
    candle("2026-09-11T22:00:00Z", 600), candle("2026-09-14T13:25:00Z", 700),
    candle("2026-09-14T13:30:00Z", 120)], true);
  expect(session.map(point => point.value)).toEqual([110, 107.5, 120]);
});
