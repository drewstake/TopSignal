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

function evaluationWithAnalysis(nextAnalysis: BotAnalysis, decisionPrice = 105): BotEvaluation {
  return {
    ...evaluation,
    decision: { ...evaluation.decision, price: decisionPrice },
    analysis: nextAnalysis,
  };
}

function insufficientAnalysis(
  closedCandleCount = 0,
  latestCandleTimestamp: string | null = null,
): BotAnalysis {
  const confidence = Math.min(35, closedCandleCount * 3);
  return {
    ...analysis,
    scenario_weights: { bullish: 33, bearish: 33, sideways: 34 },
    provenance: {
      ...analysis.provenance,
      closed_candle_count: closedCandleCount,
      partial_candle_count: 0,
      latest_candle_timestamp: latestCandleTimestamp,
      data_age_seconds: latestCandleTimestamp ? 20 : null,
    },
    data_quality: {
      status: "insufficient",
      confidence,
      missing_inputs: ["at_least_25_closed_candles", "trend_history", "atr_history"],
      warnings: [
        `Only ${closedCandleCount} closed candle(s) were available; at least 10 are needed for a reliable heuristic read.`,
      ],
    },
    market_regime: "unknown",
    features: {
      ...analysis.features,
      trend: { direction: "neutral", strength: 0, fast_ema: null, slow_ema: null, slow_ema_slope: null },
      volatility: { atr: null, atr_percent: null, percentile: null, state: "normal" },
      volume: { relative_volume: null, state: "normal" },
      vwap: { value: null, location: "unavailable" },
      multi_timeframe_alignment: {
        status: "unavailable",
        aligned_timeframes: 0,
        conflicting_timeframes: 0,
        timeframes: [],
      },
      nearby_levels: { support: null, resistance: null },
    },
    score_drivers: {
      bullish: [],
      bearish: [],
      neutral: ["Insufficient closed-candle history for a directional feature set."],
    },
    setup_quality: { score: confidence, label: "weak", drivers: [] },
    market_bias: { direction: "neutral", strength: 0, drivers: [] },
    execution_risk: { risk_score: 100 - confidence, label: "high", drivers: [] },
    data_confidence: { score: confidence, label: "insufficient", drivers: [] },
    current_price: null,
    previous_close: null,
    price_change: null,
    price_change_percent: null,
    trend: "neutral",
    trend_strength: 0,
    nearest_support: null,
    nearest_resistance: null,
    expected_move: null,
    invalidation_level: null,
    summary: "Insufficient closed-candle history for a directional read.",
    reasoning: ["Insufficient closed-candle history for a directional feature set."],
    candle_timestamp: latestCandleTimestamp,
  };
}

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

  it("renders a compact threshold state for a canonical zero-bar analysis", () => {
    const html = renderToStaticMarkup(
      <BotAnalysisPanel
        bot={bot}
        evaluation={evaluationWithAnalysis(insufficientAnalysis(), 30_275)}
        onEvaluate={() => undefined}
      />,
    );

    expect(html).toContain("Freshness unknown");
    expect(html).not.toContain(">Fresh<");
    expect(html).toContain("No directional read yet");
    expect(html).toContain("received no closed 5m candles");
    expect(html).toContain("0 / 10 closed bars");
    expect(html).toContain("10 closed bars unlock");
    expect(html).toContain("25 are needed for normal confidence");
    expect(html).toContain("Retry evaluation");
    expect(html).not.toContain("Bullish scenario weight");
    expect(html).not.toContain("Setup quality");
    expect(html).not.toContain("Execution risk");
    expect(html).not.toContain("What invalidates the setup?");
    expect(html).not.toContain("Missing · normal");
    expect(html).not.toContain("Decision/reference price");
  });

  it("switches from the compact state to the full layout at the 9-to-10-bar boundary", () => {
    const nineBarHtml = renderToStaticMarkup(
      <BotAnalysisPanel
        bot={bot}
        evaluation={evaluationWithAnalysis(insufficientAnalysis(9, "2026-07-09T14:55:00Z"))}
      />,
    );
    const tenBarHtml = renderToStaticMarkup(
      <BotAnalysisPanel
        bot={bot}
        evaluation={evaluationWithAnalysis(insufficientAnalysis(10, "2026-07-09T15:00:00Z"))}
      />,
    );

    expect(nineBarHtml).toContain("No directional read yet");
    expect(nineBarHtml).toContain("9 / 10 closed bars");
    expect(nineBarHtml).not.toContain("Bullish scenario weight");
    expect(tenBarHtml).toContain("Bullish scenario weight");
    expect(tenBarHtml).toContain("Setup quality");
    expect(tenBarHtml).not.toContain("No directional read yet");
  });

  it("does not call a timestamp-less otherwise healthy analysis fresh", () => {
    const html = renderToStaticMarkup(
      <BotAnalysisPanel
        bot={bot}
        evaluation={evaluationWithAnalysis({
          ...analysis,
          provenance: { ...analysis.provenance, latest_candle_timestamp: null, data_age_seconds: null },
        })}
      />,
    );

    expect(html).toContain("Freshness unknown");
    expect(html).not.toContain(">Fresh<");
    expect(html).toContain("Bullish scenario weight");
  });

  it("shows sufficiently populated chart bars only as separate local context", () => {
    const candles = Array.from({ length: 10 }, (_, index) =>
      chartCandle(new Date(Date.parse("2026-07-09T14:15:00Z") + index * 5 * 60_000).toISOString()),
    );
    const snapshot: BotMarketSnapshot = {
      contractKey: `${bot.contract_id}:${bot.timeframe_unit}:${bot.timeframe_unit_number}`,
      unit: bot.timeframe_unit,
      unitNumber: bot.timeframe_unit_number,
      candles,
      lastPrice: candles[candles.length - 1].close,
      updatedAt: "2026-07-09T15:05:00Z",
    };
    const html = renderToStaticMarkup(
      <BotAnalysisPanel
        bot={bot}
        evaluation={evaluationWithAnalysis(insufficientAnalysis())}
        marketSnapshot={snapshot}
      />,
    );

    expect(html).toContain("Local chart context");
    expect(html).toContain("Separate from evaluation");
    expect(html).toContain("The chart has 10 closed bars");
    expect(html).toContain("Latest chart close");
    expect(html).toContain("were not included in the canonical evaluation");
    expect(html).not.toContain("Bullish scenario weight");
  });

  it("labels a decision-price fallback instead of calling it the current price", () => {
    const html = renderToStaticMarkup(
      <BotAnalysisPanel
        bot={bot}
        evaluation={evaluationWithAnalysis({ ...analysis, current_price: null }, 30_275)}
      />,
    );

    expect(html).toContain("Decision/reference price");
    expect(html).toContain("30,275.00");
  });
});
