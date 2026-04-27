import { describe, expect, it } from "vitest";

import {
  BOT_CHART_MAX_BARS,
  BOT_CHART_MIN_BARS,
  buildBotChartQuery,
  buildBotLivePriceQuery,
  buildCandlestickData,
  buildSignalMarkers,
  buildSmaData,
} from "./botChartData";
import type { BotConfig, BotDecision, ProjectXMarketCandle } from "../../lib/types";

function candle(timestamp: string, close: number, overrides: Partial<ProjectXMarketCandle> = {}): ProjectXMarketCandle {
  return {
    id: null,
    contract_id: "CON.F.US.MNQ.M26",
    symbol: "MNQ",
    live: false,
    unit: "minute",
    unit_number: 5,
    timestamp,
    open: close - 0.25,
    high: close + 1,
    low: close - 1,
    close,
    volume: 100,
    is_partial: false,
    fetched_at: null,
    ...overrides,
  };
}

function botConfig(overrides: Partial<BotConfig> = {}): BotConfig {
  return {
    id: 10,
    name: "MNQ SMA Cross",
    account_id: 7001,
    provider: "projectx",
    enabled: false,
    execution_mode: "dry_run",
    strategy_type: "sma_cross",
    contract_id: "CON.F.US.MNQ.M26",
    symbol: "MNQ",
    timeframe_unit: "minute",
    timeframe_unit_number: 5,
    lookback_bars: 200,
    fast_period: 9,
    slow_period: 21,
    order_size: 1,
    max_contracts: 1,
    max_daily_loss: 250,
    max_trades_per_day: 3,
    max_open_position: 1,
    allowed_contracts: ["CON.F.US.MNQ.M26"],
    trading_start_time: "09:30",
    trading_end_time: "15:45",
    cooldown_seconds: 300,
    max_data_staleness_seconds: 600,
    allow_market_depth: false,
    created_at: "2026-04-26T12:00:00Z",
    updated_at: "2026-04-26T12:00:00Z",
    ...overrides,
  };
}

function decision(overrides: Partial<BotDecision> = {}): BotDecision {
  return {
    id: 1,
    bot_config_id: 10,
    bot_run_id: null,
    account_id: 7001,
    contract_id: "CON.F.US.MNQ.M26",
    symbol: "MNQ",
    decision_type: "signal",
    action: "BUY",
    reason: "9/21 SMA crossover generated BUY.",
    candle_timestamp: "2026-04-26T13:35:00Z",
    price: 18450.5,
    quantity: 1,
    created_at: "2026-04-26T13:35:05Z",
    ...overrides,
  };
}

describe("buildCandlestickData", () => {
  it("sorts candles, drops invalid rows, and lets the latest duplicate win", () => {
    const rows = buildCandlestickData([
      candle("2026-04-26T13:40:00Z", 102),
      candle("not-a-date", 99),
      candle("2026-04-26T13:35:00Z", 100),
      candle("2026-04-26T13:35:00Z", 101),
      candle("2026-04-26T13:45:00Z", Number.NaN),
    ]);

    expect(rows.map((row) => row.close)).toEqual([101, 102]);
    expect(rows.map((row) => Number(row.time))).toEqual([1777210500, 1777210800]);
  });
});

describe("buildSmaData", () => {
  it("builds rolling averages from the first complete window", () => {
    const candles = buildCandlestickData([
      candle("2026-04-26T13:35:00Z", 10),
      candle("2026-04-26T13:40:00Z", 20),
      candle("2026-04-26T13:45:00Z", 30),
      candle("2026-04-26T13:50:00Z", 50),
    ]);

    expect(buildSmaData(candles, 3)).toEqual([
      { time: candles[2].time, value: 20 },
      { time: candles[3].time, value: 100 / 3 },
    ]);
  });

  it("returns no points when the period is not usable", () => {
    const candles = buildCandlestickData([candle("2026-04-26T13:35:00Z", 10)]);

    expect(buildSmaData(candles, 0)).toEqual([]);
    expect(buildSmaData(candles, 3)).toEqual([]);
  });
});

describe("buildBotChartQuery", () => {
  it("uses a practical capped history window based on the bot timeframe", () => {
    const window = buildBotChartQuery(
      botConfig({
        timeframe_unit: "minute",
        timeframe_unit_number: 5,
        lookback_bars: 20_000,
      }),
      new Date("2026-04-26T14:00:00Z"),
    );

    expect(window.limit).toBe(BOT_CHART_MAX_BARS);
    expect(window.end).toBe("2026-04-26T14:00:00.000Z");
    expect(window.start).toBe("2026-04-05T18:00:00.000Z");
  });

  it("keeps small lookbacks above the minimum chart size", () => {
    expect(buildBotChartQuery(botConfig({ lookback_bars: 25 })).limit).toBe(BOT_CHART_MIN_BARS);
  });
});

describe("buildBotLivePriceQuery", () => {
  it("uses a narrow live refresh window for the bot timeframe", () => {
    const window = buildBotLivePriceQuery(
      botConfig({
        timeframe_unit: "minute",
        timeframe_unit_number: 5,
      }),
      new Date("2026-04-26T14:00:00Z"),
    );

    expect(window).toEqual({
      start: "2026-04-26T13:45:00.000Z",
      end: "2026-04-26T14:00:00.000Z",
      limit: 5,
    });
  });

  it("keeps very small timeframes large enough to catch a current bar", () => {
    const window = buildBotLivePriceQuery(
      botConfig({
        timeframe_unit: "second",
        timeframe_unit_number: 1,
      }),
      new Date("2026-04-26T14:00:00Z"),
    );

    expect(window.start).toBe("2026-04-26T13:59:00.000Z");
    expect(window.limit).toBe(5);
  });
});

describe("buildSignalMarkers", () => {
  it("maps recent signal decisions when their candle timestamps are loaded", () => {
    const candles = buildCandlestickData([
      candle("2026-04-26T13:35:00Z", 100),
      candle("2026-04-26T13:40:00Z", 101),
    ]);

    const markers = buildSignalMarkers({
      candles,
      activityDecisions: [
        decision({ id: 1, action: "BUY", candle_timestamp: "2026-04-26T13:35:00Z" }),
        decision({ id: 2, action: "SELL", candle_timestamp: "2026-04-26T13:40:00Z" }),
        decision({ id: 3, action: "HOLD", candle_timestamp: "2026-04-26T13:45:00Z" }),
        decision({ id: 4, action: "STOP", candle_timestamp: "2026-04-26T13:40:00Z" }),
      ],
    });

    expect(markers.map((marker) => marker.text)).toEqual(["BUY", "SELL"]);
    expect(markers.map((marker) => marker.id)).toEqual(["decision-1", "decision-2"]);
  });

  it("deduplicates the latest evaluation against activity by decision id", () => {
    const candles = buildCandlestickData([candle("2026-04-26T13:35:00Z", 100)]);
    const latestDecision = decision({ id: 7, action: "HOLD" });

    const markers = buildSignalMarkers({
      candles,
      activityDecisions: [latestDecision],
      lastEvaluation: {
        config: botConfig(),
        run: null,
        decision: latestDecision,
        order_attempt: null,
        risk_events: [],
        candles: [],
      },
    });

    expect(markers).toHaveLength(1);
    expect(markers[0]).toMatchObject({ id: "decision-7", text: "HOLD" });
  });
});
