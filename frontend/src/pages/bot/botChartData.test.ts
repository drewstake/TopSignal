import { describe, expect, it } from "vitest";

import {
  BOT_CHART_MAX_BARS,
  BOT_CHART_MIN_BARS,
  buildBotChartQuery,
  buildBotLivePriceQuery,
  buildCandlestickData,
  buildLiquidityLevels,
  buildLiveCandleFromPriceUpdate,
  buildSignalMarkers,
  buildSmaData,
  buildVwapData,
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

  it("bridges consecutive intraday opens to the previous candle close", () => {
    const rows = buildCandlestickData([
      candle("2026-04-26T13:35:00Z", 100, { open: 96, high: 102, low: 95 }),
      candle("2026-04-26T13:40:00Z", 125, { open: 122, high: 126, low: 121 }),
    ]);

    expect(rows[1]).toMatchObject({
      open: 100,
      high: 126,
      low: 100,
      close: 125,
    });
  });

  it("leaves missing intraday intervals as real gaps", () => {
    const rows = buildCandlestickData([
      candle("2026-04-26T13:35:00Z", 100, { open: 96, high: 102, low: 95 }),
      candle("2026-04-26T13:45:00Z", 125, { open: 122, high: 126, low: 121 }),
    ]);

    expect(rows[1]).toMatchObject({
      open: 122,
      high: 126,
      low: 121,
      close: 125,
    });
  });

  it("can preserve raw opens for analytics that should not include display bridging", () => {
    const rows = buildCandlestickData(
      [
        candle("2026-04-26T13:35:00Z", 100, { open: 96, high: 102, low: 95 }),
        candle("2026-04-26T13:40:00Z", 125, { open: 122, high: 126, low: 121 }),
      ],
      { bridgeConsecutiveGaps: false },
    );

    expect(rows[1]).toMatchObject({
      open: 122,
      high: 126,
      low: 121,
      close: 125,
    });
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

  it("uses the latest partial candle close when it is included in the chart data", () => {
    const candles = buildCandlestickData([
      candle("2026-04-26T13:35:00Z", 10),
      candle("2026-04-26T13:40:00Z", 20),
      candle("2026-04-26T13:45:00Z", 50, { is_partial: true }),
    ]);

    expect(buildSmaData(candles, 2)).toEqual([
      { time: candles[1].time, value: 15 },
      { time: candles[2].time, value: 35 },
    ]);
  });

  it("returns no points when the period is not usable", () => {
    const candles = buildCandlestickData([candle("2026-04-26T13:35:00Z", 10)]);

    expect(buildSmaData(candles, 0)).toEqual([]);
    expect(buildSmaData(candles, 3)).toEqual([]);
  });
});

describe("buildVwapData", () => {
  it("builds cumulative VWAP from typical price and volume", () => {
    const rows = buildVwapData([
      candle("2026-04-26T13:35:00Z", 10, { volume: 100 }),
      candle("2026-04-26T13:40:00Z", 20, { volume: 300 }),
      candle("2026-04-26T13:45:00Z", 30, { volume: 0 }),
    ]);

    expect(rows).toHaveLength(3);
    expect(rows[0].value).toBe(10);
    expect(rows[1].value).toBe(17.5);
    expect(rows[2].value).toBe(17.5);
  });

  it("resets at the configured Eastern session start", () => {
    const rows = buildVwapData(
      [
        candle("2026-04-26T13:25:00Z", 10, { volume: 100 }),
        candle("2026-04-26T13:30:00Z", 20, { volume: 100 }),
        candle("2026-04-26T13:35:00Z", 30, { volume: 100 }),
      ],
      { sessionStartTime: "09:30", sessionTimeZone: "America/New_York" },
    );

    expect(rows.map((row) => row.value)).toEqual([10, 20, 25]);
  });
});

describe("buildLiquidityLevels", () => {
  it("finds nearest active buy-side and sell-side liquidity from confirmed swing points", () => {
    const candles = buildCandlestickData([
      candle("2026-04-26T13:30:00Z", 98, { high: 100, low: 96 }),
      candle("2026-04-26T13:35:00Z", 101, { high: 104, low: 95 }),
      candle("2026-04-26T13:40:00Z", 107, { high: 110, low: 97 }),
      candle("2026-04-26T13:45:00Z", 100, { high: 106, low: 94 }),
      candle("2026-04-26T13:50:00Z", 94, { high: 103, low: 90 }),
      candle("2026-04-26T13:55:00Z", 104, { high: 107, low: 92 }),
      candle("2026-04-26T14:00:00Z", 106, { high: 108, low: 93 }),
    ]);

    expect(buildLiquidityLevels(candles)).toEqual([
      expect.objectContaining({ side: "buy", price: 110, time: candles[2].time }),
      expect.objectContaining({ side: "sell", price: 90, time: candles[4].time }),
    ]);
  });

  it("ignores swept liquidity levels and uses the next active swing level", () => {
    const candles = buildCandlestickData([
      candle("2026-04-26T13:30:00Z", 98, { high: 100, low: 96 }),
      candle("2026-04-26T13:35:00Z", 101, { high: 104, low: 95 }),
      candle("2026-04-26T13:40:00Z", 107, { high: 110, low: 97 }),
      candle("2026-04-26T13:45:00Z", 100, { high: 106, low: 94 }),
      candle("2026-04-26T13:50:00Z", 106, { high: 112, low: 98 }),
      candle("2026-04-26T13:55:00Z", 105, { high: 108, low: 99 }),
      candle("2026-04-26T14:00:00Z", 104, { high: 107, low: 100 }),
    ]);

    expect(buildLiquidityLevels(candles).find((level) => level.side === "buy")).toMatchObject({
      side: "buy",
      price: 112,
      time: candles[4].time,
    });
  });

  it("does not promote unconfirmed highs or lows at the chart edge", () => {
    const candles = buildCandlestickData([
      candle("2026-04-26T13:30:00Z", 100, { high: 101, low: 99 }),
      candle("2026-04-26T13:35:00Z", 101, { high: 102, low: 100 }),
      candle("2026-04-26T13:40:00Z", 102, { high: 103, low: 101 }),
      candle("2026-04-26T13:45:00Z", 103, { high: 104, low: 102 }),
      candle("2026-04-26T13:50:00Z", 101, { high: 110, low: 90 }),
    ]);

    expect(buildLiquidityLevels(candles)).toEqual([]);
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

describe("buildLiveCandleFromPriceUpdate", () => {
  it("buckets streamed prices into the configured candle and accumulates high and low", () => {
    const config = botConfig({ timeframe_unit: "minute", timeframe_unit_number: 5 });
    const existingLive = buildLiveCandleFromPriceUpdate({
      config,
      price: {
        contract_id: "CON.F.US.MNQ.M26",
        symbol: "MNQ",
        price: 18450,
        timestamp: "2026-04-26T14:07:15Z",
      },
      closedCandles: [],
      currentLiveCandle: null,
      fetchedAt: new Date("2026-04-26T14:07:15Z"),
    });

    const updatedLive = buildLiveCandleFromPriceUpdate({
      config,
      price: {
        contract_id: "CON.F.US.MNQ.M26",
        symbol: "MNQ",
        price: 18449.25,
        timestamp: "2026-04-26T14:09:45Z",
      },
      closedCandles: [],
      currentLiveCandle: existingLive,
      fetchedAt: new Date("2026-04-26T14:09:45Z"),
    });

    expect(updatedLive).toMatchObject({
      timestamp: "2026-04-26T14:05:00.000Z",
      open: 18450,
      high: 18450,
      low: 18449.25,
      close: 18449.25,
      is_partial: true,
    });
  });

  it("uses the matching closed candle as the base for a streamed partial update", () => {
    const config = botConfig({ timeframe_unit: "minute", timeframe_unit_number: 5 });
    const base = candle("2026-04-26T14:05:00.000Z", 18449, {
      open: 18448,
      high: 18449.5,
      low: 18447.75,
      volume: 22,
    });

    const live = buildLiveCandleFromPriceUpdate({
      config,
      price: {
        contract_id: "CON.F.US.MNQ.M26",
        symbol: "MNQ",
        price: 18450.25,
        timestamp: "2026-04-26T14:08:00Z",
      },
      closedCandles: [base],
      currentLiveCandle: null,
      fetchedAt: new Date("2026-04-26T14:08:00Z"),
    });

    expect(live).toMatchObject({
      open: 18448,
      high: 18450.25,
      low: 18447.75,
      volume: 22,
      close: 18450.25,
    });
  });

  it("uses the previous consecutive close when a streamed candle starts a new bucket", () => {
    const config = botConfig({ timeframe_unit: "minute", timeframe_unit_number: 5 });
    const previous = candle("2026-04-26T14:00:00.000Z", 18440, {
      open: 18439,
      high: 18441,
      low: 18438,
    });

    const live = buildLiveCandleFromPriceUpdate({
      config,
      price: {
        contract_id: "CON.F.US.MNQ.M26",
        symbol: "MNQ",
        price: 18450.25,
        timestamp: "2026-04-26T14:05:10Z",
      },
      closedCandles: [previous],
      currentLiveCandle: null,
      fetchedAt: new Date("2026-04-26T14:05:10Z"),
    });

    expect(live).toMatchObject({
      timestamp: "2026-04-26T14:05:00.000Z",
      open: 18440,
      high: 18450.25,
      low: 18440,
      close: 18450.25,
    });
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

  it("places bot decisions on the selected aggregate chart candle", () => {
    const candles = buildCandlestickData([
      candle("2026-04-26T13:30:00Z", 100, { unit: "minute", unit_number: 15 }),
      candle("2026-04-26T13:45:00Z", 101, { unit: "minute", unit_number: 15 }),
    ]);

    const markers = buildSignalMarkers({
      candles,
      activityDecisions: [
        decision({ id: 9, action: "BUY", candle_timestamp: "2026-04-26T13:35:00Z" }),
      ],
      timeframeUnit: "minute",
      timeframeUnitNumber: 15,
    });

    expect(markers).toHaveLength(1);
    expect(markers[0]).toMatchObject({ id: "decision-9", text: "BUY", time: candles[0].time });
  });

  it("falls back to the loaded candle range when aggregate bars are not UTC-aligned", () => {
    const candles = buildCandlestickData([
      candle("2026-04-25T22:00:00Z", 100, { unit: "day", unit_number: 1 }),
      candle("2026-04-26T22:00:00Z", 101, { unit: "day", unit_number: 1 }),
    ]);

    const markers = buildSignalMarkers({
      candles,
      activityDecisions: [
        decision({ id: 10, action: "SELL", candle_timestamp: "2026-04-26T13:35:00Z" }),
      ],
      timeframeUnit: "day",
      timeframeUnitNumber: 1,
    });

    expect(markers).toHaveLength(1);
    expect(markers[0]).toMatchObject({ id: "decision-10", text: "SELL", time: candles[0].time });
  });
});
