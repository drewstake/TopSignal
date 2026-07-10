import { describe, expect, it } from "vitest";

import type { BotAnalysis, BotEvaluation, ProjectXMarketCandle } from "../../lib/types";
import {
  buildDisplayAnalysis,
  CANONICAL_ANALYSIS_VERSION,
  HEURISTIC_SCENARIO_WEIGHT_METHOD,
  normalizeScenarioWeights,
  SCENARIO_WEIGHT_DISCLAIMER,
  SCENARIO_WEIGHT_LABELS,
} from "./botAnalysisContract";

function candle(index: number, overrides: Partial<ProjectXMarketCandle> = {}): ProjectXMarketCandle {
  const close = 100 + index * 0.2;
  return {
    id: null,
    contract_id: "CON.F.US.MNQ.M26",
    symbol: "MNQ",
    live: false,
    unit: "minute",
    unit_number: 5,
    timestamp: new Date(Date.parse("2026-07-09T13:30:00Z") + index * 5 * 60_000).toISOString(),
    open: close - 0.1,
    high: close + 0.5,
    low: close - 0.5,
    close,
    volume: 100 + index,
    is_partial: false,
    fetched_at: null,
    ...overrides,
  };
}

function legacyAnalysis(overrides: Partial<BotAnalysis> = {}): BotAnalysis {
  return {
    current_price: 105,
    previous_close: 104,
    price_change: 1,
    price_change_percent: 0.96,
    trend: "bullish",
    trend_strength: 70,
    volatility_state: "normal",
    volume_state: "normal",
    support_levels: [102],
    resistance_levels: [108],
    nearest_support: 102,
    nearest_resistance: 108,
    bullish_probability: 55,
    bearish_probability: 20,
    sideways_probability: 25,
    expected_move: 2,
    invalidation_level: 102,
    summary: "Legacy summary",
    reasoning: ["Trend supports the read."],
    risk_notes: [],
    ...overrides,
  };
}

function evaluation(analysis: BotAnalysis | null, candles: ProjectXMarketCandle[] = []): BotEvaluation {
  return {
    status: "evaluated",
    correlation_id: null,
    idempotency_key: null,
    duplicate_of_order_attempt_id: null,
    config: {
      id: 1,
      name: "Test bot",
      account_id: 1,
      contract_id: "CON.F.US.MNQ.M26",
      symbol: "MNQ",
      enabled: true,
      provider: "projectx",
      execution_mode: "dry_run",
      strategy_type: "sma_cross",
      strategy_params: {},
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
      allowed_contracts: [],
      trading_start_time: "09:30",
      trading_end_time: "15:45",
      cooldown_seconds: 300,
      max_data_staleness_seconds: 600,
      allow_market_depth: false,
      created_at: "2026-07-09T13:00:00Z",
      updated_at: "2026-07-09T13:00:00Z",
    },
    run: null,
    decision: {
      id: 1,
      bot_config_id: 1,
      bot_run_id: null,
      account_id: 1,
      contract_id: "CON.F.US.MNQ.M26",
      symbol: "MNQ",
      decision_type: "evaluation",
      action: "HOLD",
      reason: "test",
      candle_timestamp: null,
      price: 105,
      quantity: null,
      created_at: "2026-07-09T13:00:00Z",
    },
    order_attempt: null,
    risk_events: [],
    analysis,
    candles,
  };
}

describe("canonical scenario-weight contract", () => {
  it.each([
    { bullish: 55, bearish: 19, sideways: 26 },
    { bullish: 0.2, bearish: 0.3, sideways: 0.5 },
    { bullish: 1, bearish: 1, sideways: 1 },
    { bullish: 500, bearish: 2, sideways: 0 },
    { bullish: Number.NaN, bearish: -10, sideways: 0 },
  ])("normalizes $bullish/$bearish/$sideways to exact integer total 100", (input) => {
    const weights = normalizeScenarioWeights(input);
    expect(Number.isInteger(weights.bullish)).toBe(true);
    expect(Number.isInteger(weights.bearish)).toBe(true);
    expect(Number.isInteger(weights.sideways)).toBe(true);
    expect(weights.bullish + weights.bearish + weights.sideways).toBe(100);
  });

  it("uses the backend method name and scenario-weight labels consistently", () => {
    expect(HEURISTIC_SCENARIO_WEIGHT_METHOD).toBe("heuristic_scenario_weight");
    expect(Object.values(SCENARIO_WEIGHT_LABELS).every((label) => label.endsWith("scenario weight"))).toBe(true);
    expect(SCENARIO_WEIGHT_DISCLAIMER).toContain("not calibrated probabilities");
  });
});

describe("buildDisplayAnalysis", () => {
  it("prefers the canonical v2 backend contract and preserves its provenance", () => {
    const analysis = legacyAnalysis({
      analysis_version: CANONICAL_ANALYSIS_VERSION,
      probability_method: HEURISTIC_SCENARIO_WEIGHT_METHOD,
      scenario_weights: { bullish: 51, bearish: 18, sideways: 31 },
      market_regime: "trend",
      provenance: {
        closed_candle_count: 80,
        partial_candle_count: 1,
        latest_candle_timestamp: "2026-07-09T15:00:00Z",
        data_age_seconds: 30,
        is_stale: false,
        stale_after_seconds: 600,
        timeframe: { unit: "minute", unit_number: 5, label: "5m" },
        detected_gaps: [],
        gap_count: 0,
      },
      data_quality: { status: "good", confidence: 94, missing_inputs: [], warnings: [] },
      market_bias: { direction: "bullish", strength: 72, drivers: ["Aligned trend"] },
      data_confidence: { score: 94, label: "good", drivers: [] },
    });
    const result = buildDisplayAnalysis(evaluation(analysis), Date.parse("2026-07-09T15:00:30Z"));

    expect(result?.source).toBe("backend");
    expect(result?.analysisVersion).toBe(CANONICAL_ANALYSIS_VERSION);
    expect(result?.scenarioWeights).toEqual({ bullish: 51, bearish: 18, sideways: 31 });
    expect(result?.provenance.closed_candle_count).toBe(80);
    expect(result?.dataQuality.status).toBe("good");
    expect(result?.marketRegime).toBe("trend");
  });

  it("labels a missing backend payload as local fallback and excludes the partial bar", () => {
    const closed = Array.from({ length: 40 }, (_, index) => candle(index));
    const partial = candle(40, { close: 9_999, high: 10_000, low: 9_000, volume: 1_000_000, is_partial: true });
    const nowMs = Date.parse(closed[closed.length - 1].timestamp) + 5 * 60_000;
    const result = buildDisplayAnalysis(evaluation(null, [...closed, partial]), nowMs);

    expect(result?.source).toBe("local_fallback");
    expect(result?.currentPrice).toBe(closed[closed.length - 1].close);
    expect(result?.provenance.closed_candle_count).toBe(40);
    expect(result?.provenance.partial_candle_count).toBe(1);
    expect(result?.provenance.stale_after_seconds).toBe(600);
    expect(result?.provenance.is_stale).toBe(false);
    expect(result!.scenarioWeights.bullish + result!.scenarioWeights.bearish + result!.scenarioWeights.sideways).toBe(100);
    expect(result?.dataQuality.warnings[0]).toContain("Local fallback analysis");
  });

  it("does not fall back to partial candles when closed history is insufficient", () => {
    const rows = [candle(0), ...Array.from({ length: 20 }, (_, index) => candle(index + 1, { is_partial: true }))];
    expect(buildDisplayAnalysis(evaluation(null, rows), Date.parse("2026-07-09T16:00:00Z"))).toBeNull();
  });

  it("keeps legacy probability aliases compatible while identifying the unversioned contract", () => {
    const result = buildDisplayAnalysis(evaluation(legacyAnalysis({
      bullish_probability: 40,
      bearish_probability: 35,
      sideways_probability: 25,
    })));
    expect(result?.scenarioWeights).toEqual({ bullish: 40, bearish: 35, sideways: 25 });
    expect(result?.analysisVersion).toBe("legacy_unversioned");
    expect(result?.dataQuality.warnings).toContain("Backend returned an unversioned legacy analysis contract.");
  });
});
