import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { BotAnalysis, BotConfig, BotEvaluation, ProjectXMarketCandle } from "../../lib/types";
import { BotAnalysisPanel } from "./BotAnalysisPanel";
import type { BotMarketSnapshot } from "./botMarketContext";

const bot = {
  id: 1,
  name: "MNQ bot",
  symbol: "MNQ",
  contract_id: "CON.F.US.MNQ.M26",
  timeframe_unit: "minute",
  timeframe_unit_number: 5,
  max_data_staleness_seconds: 600,
} as BotConfig;

const analysis = {
  analysis_version: "market_analysis_v2",
  probability_method: "heuristic_scenario_weight",
  scenario_weights: { bullish: 54, bearish: 18, sideways: 28 },
  provenance: {
    closed_candle_count: 80,
    partial_candle_count: 1,
    latest_candle_timestamp: "2026-07-09T15:00:00Z",
    data_age_seconds: 20,
    is_stale: false,
    stale_after_seconds: 600,
    timeframe: { unit: "minute", unit_number: 5, label: "5m" },
    detected_gaps: [],
    gap_count: 0,
  },
  data_quality: {
    status: "limited",
    confidence: 74,
    missing_inputs: ["News context"],
    warnings: ["News data was not provided."],
  },
  market_regime: "trend",
  features: {
    trend: { direction: "bullish", strength: 72, fast_ema: 105, slow_ema: 103, slow_ema_slope: 0.4 },
    volatility: { atr: 2, atr_percent: 1.9, percentile: 64, state: "normal" },
    volume: { relative_volume: 1.2, state: "normal" },
    vwap: { value: 104, location: "above" },
    multi_timeframe_alignment: {
      status: "bullish",
      aligned_timeframes: 2,
      conflicting_timeframes: 0,
      timeframes: [{ timeframe: "5m", direction: "bullish" }],
    },
    nearby_levels: { support: 102, resistance: 108 },
  },
  score_drivers: {
    bullish: ["Trend and VWAP align."],
    bearish: ["Resistance is nearby."],
    neutral: ["Volume is average."],
  },
  setup_quality: { score: 68, label: "acceptable", drivers: ["Structure is defined."] },
  market_bias: { direction: "bullish", strength: 72, drivers: ["Trend and VWAP align."] },
  execution_risk: { risk_score: 35, label: "moderate", drivers: ["Resistance is nearby."] },
  data_confidence: { score: 74, label: "limited", drivers: ["News context is missing."] },
  current_price: 105,
  previous_close: 104,
  price_change: 1,
  price_change_percent: 0.96,
  trend: "bullish",
  trend_strength: 72,
  volatility_state: "normal",
  volume_state: "normal",
  support_levels: [102],
  resistance_levels: [108],
  nearest_support: 102,
  nearest_resistance: 108,
  bullish_probability: 54,
  bearish_probability: 18,
  sideways_probability: 28,
  expected_move: 2,
  expected_move_percent: 1.9,
  invalidation_level: 102,
  summary: "Closed-bar trend and VWAP support a bullish bias.",
  reasoning: ["Trend is bullish."],
  risk_notes: ["Resistance remains overhead."],
} satisfies BotAnalysis;

const evaluation = {
  status: "evaluated",
  correlation_id: null,
  idempotency_key: null,
  duplicate_of_order_attempt_id: null,
  config: bot,
  decision: { action: "HOLD", price: 105 },
  analysis,
  candles: [],
  risk_events: [],
  run: null,
  order_attempt: null,
} as unknown as BotEvaluation;

function chartCandle(timestamp: string): ProjectXMarketCandle {
  return {
    id: null,
    contract_id: bot.contract_id,
    symbol: "MNQ",
    live: false,
    unit: "minute",
    unit_number: 5,
    timestamp,
    open: 105,
    high: 106,
    low: 104,
    close: 105.5,
    volume: 100,
    is_partial: false,
    fetched_at: null,
  };
}

describe("BotAnalysisPanel canonical labels", () => {
  it("renders freshness, quality, regime, invalidation, missing inputs, and scenario-weight language", () => {
    const html = renderToStaticMarkup(<BotAnalysisPanel bot={bot} evaluation={evaluation} />);

    expect(html).toContain("Canonical backend");
    expect(html).toContain("Fresh");
    expect(html).toContain("Limited data");
    expect(html).toContain("Trend regime");
    expect(html).toContain("Bullish scenario weight");
    expect(html).toContain("not calibrated probabilities");
    expect(html).toContain("Bullish setup invalidates below 102");
    expect(html).toContain("News Context");
    expect(html).not.toContain("Bullish probability");
  });

  it("marks an evaluation stale when the chart has one newer closed bar", () => {
    const snapshot: BotMarketSnapshot = {
      contractKey: "MNQ:minute:5",
      unit: "minute",
      unitNumber: 5,
      candles: [chartCandle("2026-07-09T15:05:00Z")],
      lastPrice: 105.5,
      updatedAt: "2026-07-09T15:05:00Z",
    };
    const html = renderToStaticMarkup(
      <BotAnalysisPanel bot={bot} evaluation={evaluation} marketSnapshot={snapshot} />,
    );

    expect(html).toContain("Stale");
    expect(html).toContain("the chart has 1 newer closed bar");
  });
});
