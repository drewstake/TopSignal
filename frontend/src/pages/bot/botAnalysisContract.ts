import type {
  BotAnalysis,
  BotAnalysisDataQuality,
  BotAnalysisDimension,
  BotAnalysisExecutionRisk,
  BotAnalysisMarketBiasDimension,
  BotAnalysisProvenance,
  BotAnalysisScoreDrivers,
  BotDirectionalProbabilities,
  BotEvaluation,
  BotMarketBias,
  BotMarketRegime,
  BotProbabilityMethod,
  BotVwapLocation,
  TradeEvaluationResult,
} from "../../lib/types";
import {
  buildMarketContext,
  LOCAL_CONTEXT_VERSION,
  timeframeLabel,
  type BotMarketSnapshot,
  type MarketContext,
} from "./botMarketContext";

export const CANONICAL_ANALYSIS_VERSION = "market_analysis_v2";
export const HEURISTIC_SCENARIO_WEIGHT_METHOD: BotProbabilityMethod = "heuristic_scenario_weight";
export const SCENARIO_WEIGHT_LABELS = {
  bullish: "Bullish scenario weight",
  bearish: "Bearish scenario weight",
  sideways: "Sideways scenario weight",
} as const;
export const SCENARIO_WEIGHT_DISCLAIMER =
  "Scenario weights are deterministic heuristic allocations, not calibrated probabilities or guaranteed outcomes.";

export type AnalysisSource = "backend" | "local_fallback";
export type AnalysisPriceSource = "closed_bar" | "decision" | "none";

export interface DisplayAnalysis {
  source: AnalysisSource;
  analysisVersion: string;
  probabilityMethod: BotProbabilityMethod;
  scenarioWeights: BotDirectionalProbabilities;
  marketBias: BotMarketBias;
  trendStrength: number;
  marketRegime: BotMarketRegime;
  currentPrice: number | null;
  priceSource: AnalysisPriceSource;
  priceChange: number | null;
  priceChangePercent: number | null;
  expectedMove: number | null;
  expectedMovePercent: number | null;
  nearestSupport: number | null;
  nearestResistance: number | null;
  volatilityState: string | null;
  atrPercentile: number | null;
  volumeState: string | null;
  relativeVolume: number | null;
  vwap: number | null;
  vwapLocation: BotVwapLocation;
  multiTimeframeStatus: string;
  summary: string;
  reasoning: string[];
  riskNotes: string[];
  invalidationLevel: number | null;
  invalidationReason: string;
  generatedAt: string | null;
  tradeEvaluation: TradeEvaluationResult | null;
  provenance: BotAnalysisProvenance;
  dataQuality: BotAnalysisDataQuality;
  scoreDrivers: BotAnalysisScoreDrivers;
  setupQuality: BotAnalysisDimension | null;
  marketBiasDimension: BotAnalysisMarketBiasDimension;
  executionRisk: BotAnalysisExecutionRisk | null;
  dataConfidence: BotAnalysisDimension;
}

const NEUTRAL_WEIGHTS: BotDirectionalProbabilities = { bullish: 33, bearish: 33, sideways: 34 };

export function normalizeScenarioWeights(
  values: Partial<BotDirectionalProbabilities> | null | undefined,
): BotDirectionalProbabilities {
  if (!values) {
    return { ...NEUTRAL_WEIGHTS };
  }
  const raw = {
    bullish: finiteNonNegative(values.bullish),
    bearish: finiteNonNegative(values.bearish),
    sideways: finiteNonNegative(values.sideways),
  };
  const rawTotal = raw.bullish + raw.bearish + raw.sideways;
  if (rawTotal <= 0) {
    return { ...NEUTRAL_WEIGHTS };
  }

  // Compatibility for the short-lived 0..1 representation. A single `1`
  // alongside percent values remains one percent, not one hundred percent.
  const multiplier = rawTotal <= 1.000001 && Object.values(raw).every((value) => value <= 1) ? 100 : 1;
  const scaled = {
    bullish: raw.bullish * multiplier,
    bearish: raw.bearish * multiplier,
    sideways: raw.sideways * multiplier,
  };
  const total = scaled.bullish + scaled.bearish + scaled.sideways;
  const exact = {
    bullish: (scaled.bullish / total) * 100,
    bearish: (scaled.bearish / total) * 100,
    sideways: (scaled.sideways / total) * 100,
  };
  const output: BotDirectionalProbabilities = {
    bullish: Math.floor(exact.bullish),
    bearish: Math.floor(exact.bearish),
    sideways: Math.floor(exact.sideways),
  };
  const keys: Array<keyof BotDirectionalProbabilities> = ["sideways", "bullish", "bearish"];
  keys.sort((left, right) => exact[right] - output[right] - (exact[left] - output[left]));
  let remainder = 100 - output.bullish - output.bearish - output.sideways;
  for (let index = 0; remainder > 0; index += 1, remainder -= 1) {
    output[keys[index % keys.length]] += 1;
  }
  return output;
}

export function buildDisplayAnalysis(evaluation: BotEvaluation | null, nowMs = Date.now()): DisplayAnalysis | null {
  if (!evaluation) {
    return null;
  }
  const localContext = buildEvaluationMarketContext(evaluation, nowMs);
  if (!evaluation.analysis) {
    return localContext ? buildLocalFallback(evaluation, localContext) : null;
  }
  return normalizeBackendAnalysis(evaluation.analysis, evaluation, localContext);
}

function buildEvaluationMarketContext(evaluation: BotEvaluation, nowMs: number): MarketContext | null {
  const snapshot: BotMarketSnapshot = {
    contractKey: `${evaluation.config.contract_id}:${evaluation.config.timeframe_unit}:${evaluation.config.timeframe_unit_number}`,
    unit: evaluation.config.timeframe_unit,
    unitNumber: evaluation.config.timeframe_unit_number,
    candles: evaluation.candles,
    lastPrice: null,
    updatedAt: new Date(nowMs).toISOString(),
  };
  return buildMarketContext(snapshot, nowMs, evaluation.config.max_data_staleness_seconds);
}

function normalizeBackendAnalysis(
  analysis: BotAnalysis,
  evaluation: BotEvaluation,
  localContext: MarketContext | null,
): DisplayAnalysis {
  const weights = normalizeScenarioWeights(
    analysis.scenario_weights ?? {
      bullish: analysis.bullish_probability,
      bearish: analysis.bearish_probability,
      sideways: analysis.sideways_probability,
    },
  );
  const marketBias = normalizeBias(analysis.market_bias?.direction ?? analysis.trend, weights);
  const derivedProvenance = provenanceFromContext(localContext, evaluation);
  const provenance = analysis.provenance ?? derivedProvenance;
  const compatibilityWarnings = [
    ...(analysis.analysis_version ? [] : ["Backend returned an unversioned legacy analysis contract."]),
    ...(analysis.provenance ? [] : ["Candle provenance was derived locally from the evaluation payload."]),
  ];
  const dataQuality: BotAnalysisDataQuality = analysis.data_quality ?? {
    status: localContext?.dataQuality.status ?? "insufficient",
    confidence: localContext?.dataQuality.confidence ?? 0,
    missing_inputs: localContext?.dataQuality.missingInputs ?? ["Canonical backend data-quality assessment"],
    warnings: localContext?.dataQuality.warnings ?? [],
  };
  const dataConfidence = analysis.data_confidence ?? {
    score: dataQuality.confidence,
    label: dataQuality.status,
    drivers: [...dataQuality.warnings, ...dataQuality.missing_inputs.map((item) => `Missing: ${item}`)],
  };
  const trendStrength = finiteNumber(analysis.features?.trend.strength) ?? finiteNumber(analysis.trend_strength) ?? 0;
  const nearestSupport = finiteNumber(analysis.features?.nearby_levels.support) ?? finiteNumber(analysis.nearest_support);
  const nearestResistance =
    finiteNumber(analysis.features?.nearby_levels.resistance) ?? finiteNumber(analysis.nearest_resistance);
  const scoreDrivers = analysis.score_drivers ?? legacyScoreDrivers(marketBias, analysis.reasoning);
  const analyzedPrice = finiteNumber(analysis.current_price);
  const decisionPrice = finiteNumber(evaluation.decision.price);

  return {
    source: "backend",
    analysisVersion: analysis.analysis_version ?? "legacy_unversioned",
    probabilityMethod: analysis.probability_method ?? HEURISTIC_SCENARIO_WEIGHT_METHOD,
    scenarioWeights: weights,
    marketBias,
    trendStrength,
    marketRegime: analysis.market_regime ?? localContext?.marketRegime ?? "unknown",
    currentPrice: analyzedPrice ?? decisionPrice,
    priceSource: analyzedPrice !== null ? "closed_bar" : decisionPrice !== null ? "decision" : "none",
    priceChange: finiteNumber(analysis.price_change),
    priceChangePercent: finiteNumber(analysis.price_change_percent),
    expectedMove: finiteNumber(analysis.expected_move),
    expectedMovePercent: finiteNumber(analysis.expected_move_percent),
    nearestSupport,
    nearestResistance,
    volatilityState: cleanLabel(analysis.features?.volatility.state ?? analysis.volatility_state),
    atrPercentile: finiteNumber(analysis.features?.volatility.percentile),
    volumeState: cleanLabel(analysis.features?.volume.state ?? analysis.volume_state),
    relativeVolume: finiteNumber(analysis.features?.volume.relative_volume),
    vwap: finiteNumber(analysis.features?.vwap.value),
    vwapLocation: analysis.features?.vwap.location ?? "unavailable",
    multiTimeframeStatus: analysis.features?.multi_timeframe_alignment.status ?? "unavailable",
    summary: cleanText(analysis.summary) ?? "No analysis summary was returned.",
    reasoning: cleanList(analysis.reasoning),
    riskNotes: cleanList(analysis.risk_notes),
    invalidationLevel: finiteNumber(analysis.invalidation_level),
    invalidationReason: invalidationText(marketBias, finiteNumber(analysis.invalidation_level), nearestSupport, nearestResistance),
    generatedAt: analysis.generated_at ?? null,
    tradeEvaluation: analysis.trade_evaluation ?? null,
    provenance,
    dataQuality: {
      ...dataQuality,
      warnings: [...compatibilityWarnings, ...dataQuality.warnings],
    },
    scoreDrivers,
    setupQuality: analysis.setup_quality ?? null,
    marketBiasDimension: analysis.market_bias ?? {
      direction: marketBias,
      strength: trendStrength,
      drivers: scoreDrivers[marketBias === "neutral" ? "neutral" : marketBias],
    },
    executionRisk: analysis.execution_risk ?? null,
    dataConfidence,
  };
}

function buildLocalFallback(evaluation: BotEvaluation, context: MarketContext): DisplayAnalysis {
  const scoreDrivers = localScoreDrivers(context);
  const weights = buildLocalScenarioWeights(context);
  const marketBias = normalizeBias(context.trend?.direction ?? "neutral", weights);
  const closes = evaluation.candles
    .filter((candle) => !candle.is_partial && Number.isFinite(candle.close) && Number.isFinite(Date.parse(candle.timestamp)))
    .sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp));
  const previousClose = closes.length >= 2 ? closes[closes.length - 2].close : null;
  const currentPrice = context.lastPrice;
  const priceChange = currentPrice !== null && previousClose !== null ? currentPrice - previousClose : null;
  const priceChangePercent =
    priceChange !== null && previousClose !== null && previousClose !== 0
      ? (priceChange / Math.abs(previousClose)) * 100
      : null;
  const setupScore = localSetupQualityScore(context, marketBias);
  const executionRiskScore = Math.round(
    clamp(
      (context.volatilityState === "extreme" ? 70 : context.volatilityState === "elevated" ? 45 : context.volatilityState === "low" ? 10 : 20) +
        (context.volumeState === "low" ? 15 : 0) +
        (context.marketRegime === "chop" || context.marketRegime === "volatile" ? 15 : 0) +
        ((context.trend?.strength ?? 0) * 100 < 25 ? 10 : (context.trend?.strength ?? 0) * 100 < 45 ? 5 : 0) +
        (context.multiTimeframeAlignment.status === "mixed" ? 15 : context.multiTimeframeAlignment.status === "unavailable" ? 10 : 0) +
        (context.provenance.isStale ? 35 : 0) +
        Math.min(30, context.provenance.detectedGapCount * 10),
      0,
      100,
    ),
  );
  const executionRiskDrivers = Array.from(new Set([
    ...context.dataQuality.warnings,
    ...(context.volumeState === "low" ? ["Low relative volume reduces directional follow-through confidence."] : []),
    ...(context.marketRegime === "chop" ? ["Choppy price action increases false-break and whipsaw risk."] : []),
    ...((context.trend?.strength ?? 0) * 100 < 25 ? ["Directional trend strength is weak."] : []),
    ...(context.multiTimeframeAlignment.status === "mixed" ? ["Timeframe trends conflict."] : []),
  ]));
  const invalidationLevel = marketBias === "bullish" ? context.nearestSupport : marketBias === "bearish" ? context.nearestResistance : null;

  return {
    source: "local_fallback",
    analysisVersion: LOCAL_CONTEXT_VERSION,
    probabilityMethod: HEURISTIC_SCENARIO_WEIGHT_METHOD,
    scenarioWeights: weights,
    marketBias,
    trendStrength: Math.round((context.trend?.strength ?? 0) * 100),
    marketRegime: context.marketRegime,
    currentPrice,
    priceSource: currentPrice !== null ? "closed_bar" : "none",
    priceChange,
    priceChangePercent,
    expectedMove: context.atr,
    expectedMovePercent: context.atrPercent,
    nearestSupport: context.nearestSupport,
    nearestResistance: context.nearestResistance,
    volatilityState: cleanLabel(context.volatilityState),
    atrPercentile: context.atrPercentile,
    volumeState: cleanLabel(context.volumeState),
    relativeVolume: context.relativeVolume,
    vwap: context.vwap,
    vwapLocation: context.vwapLocation,
    multiTimeframeStatus: context.multiTimeframeAlignment.status,
    summary: `Local closed-bar fallback is ${marketBias} in a ${context.marketRegime} regime. It is chart context only; rerun Evaluate for canonical backend analysis.`,
    reasoning: [...scoreDrivers.bullish, ...scoreDrivers.bearish, ...scoreDrivers.neutral].slice(0, 6),
    riskNotes: [
      "Backend analysis was unavailable; this read was computed locally from closed chart candles.",
      ...context.dataQuality.warnings,
    ],
    invalidationLevel,
    invalidationReason: invalidationText(marketBias, invalidationLevel, context.nearestSupport, context.nearestResistance),
    generatedAt: null,
    tradeEvaluation: null,
    provenance: provenanceFromContext(context, evaluation),
    dataQuality: {
      status: context.dataQuality.status,
      confidence: context.dataQuality.confidence,
      missing_inputs: context.dataQuality.missingInputs,
      warnings: ["Local fallback analysis; canonical backend analysis was not returned.", ...context.dataQuality.warnings],
    },
    scoreDrivers,
    setupQuality: { score: setupScore, label: scoreLabel(setupScore), drivers: scoreDrivers.neutral },
    marketBiasDimension: {
      direction: marketBias,
      strength: Math.round((context.trend?.strength ?? 0) * 100),
      drivers: scoreDrivers[marketBias === "neutral" ? "neutral" : marketBias],
    },
    executionRisk: {
      risk_score: executionRiskScore,
      label: executionRiskScore >= 60 ? "high" : executionRiskScore >= 30 ? "moderate" : "low",
      drivers: executionRiskDrivers,
    },
    dataConfidence: {
      score: context.dataQuality.confidence,
      label: context.dataQuality.status,
      drivers: [...context.dataQuality.warnings, ...context.dataQuality.missingInputs.map((item) => `Missing: ${item}`)],
    },
  };
}

function buildLocalScenarioWeights(context: MarketContext): BotDirectionalProbabilities {
  let bullish = 33;
  let bearish = 33;
  let sideways = 34;
  const trendBias = (context.trend?.strength ?? 0) * 28;
  if (context.trend?.direction === "bullish") {
    bullish += trendBias;
    bearish -= trendBias * 0.55;
    sideways -= trendBias * 0.45;
  } else if (context.trend?.direction === "bearish") {
    bearish += trendBias;
    bullish -= trendBias * 0.55;
    sideways -= trendBias * 0.45;
  } else {
    sideways += 6;
    bullish -= 3;
    bearish -= 3;
  }
  if (context.marketRegime === "range" || context.marketRegime === "quiet") {
    sideways += 6;
    bullish -= 3;
    bearish -= 3;
  }
  if (context.vwapLocation === "above") {
    bullish += 3;
    bearish -= 3;
  } else if (context.vwapLocation === "below") {
    bearish += 3;
    bullish -= 3;
  }
  return normalizeScenarioWeights({ bullish, bearish, sideways });
}

function localSetupQualityScore(context: MarketContext, marketBias: BotMarketBias): number {
  const trendStrength = (context.trend?.strength ?? 0) * 100;
  let score = context.dataQuality.confidence * 0.3 + trendStrength * 0.3;
  if (marketBias !== "neutral" && context.multiTimeframeAlignment.status === marketBias) score += 15;
  else if (context.multiTimeframeAlignment.status === "neutral") score += 7;
  else if (context.multiTimeframeAlignment.status === "mixed") score += 2;

  if (context.marketRegime === "trend" && marketBias !== "neutral") score += 15;
  else if ((context.marketRegime === "range" || context.marketRegime === "quiet") && marketBias === "neutral") score += 12;
  else if (context.marketRegime === "range" || context.marketRegime === "quiet") score += 4;
  else if (context.marketRegime === "chop") score += 2;
  else if (context.marketRegime === "volatile") score += 3;

  score += context.volumeState === "elevated" ? 10 : context.volumeState === "normal" ? 7 : context.volumeState === "low" ? 1 : 0;
  if ((marketBias === "bullish" && context.vwapLocation === "above") || (marketBias === "bearish" && context.vwapLocation === "below")) score += 8;
  else if ((marketBias === "bullish" && context.vwapLocation === "below") || (marketBias === "bearish" && context.vwapLocation === "above")) score -= 4;
  else if (marketBias === "neutral" && context.vwapLocation === "at") score += 5;
  if (context.nearestSupport !== null && context.nearestResistance !== null) score += 7;
  return Math.round(clamp(score, 0, 100));
}

function localScoreDrivers(context: MarketContext): BotAnalysisScoreDrivers {
  const bullish: string[] = [];
  const bearish: string[] = [];
  const neutral: string[] = [];
  if (context.trend?.direction === "bullish") bullish.push(`Closed-bar trend is bullish (${Math.round(context.trend.strength * 100)}/100 strength).`);
  if (context.trend?.direction === "bearish") bearish.push(`Closed-bar trend is bearish (${Math.round(context.trend.strength * 100)}/100 strength).`);
  if (context.trend?.direction === "neutral") neutral.push("Closed-bar trend is neutral.");
  if (context.vwapLocation === "above") bullish.push("Latest closed price is above session VWAP.");
  if (context.vwapLocation === "below") bearish.push("Latest closed price is below session VWAP.");
  if (context.vwapLocation === "at") neutral.push("Latest closed price is at session VWAP.");
  if (context.marketRegime === "range" || context.marketRegime === "quiet") neutral.push(`Market regime is ${context.marketRegime}.`);
  if (context.multiTimeframeAlignment.status === "mixed") neutral.push("Timeframe trends conflict.");
  if (context.relativeVolume !== null && context.relativeVolume < 0.7) neutral.push("Relative volume is below average.");
  return { bullish, bearish, neutral };
}

function provenanceFromContext(context: MarketContext | null, evaluation: BotEvaluation): BotAnalysisProvenance {
  if (context) {
    return {
      closed_candle_count: context.provenance.closedCandleCount,
      partial_candle_count: context.provenance.partialCandleCount,
      latest_candle_timestamp: context.provenance.latestCandleTimestamp,
      data_age_seconds: context.provenance.dataAgeSeconds,
      is_stale: context.provenance.isStale,
      stale_after_seconds: context.provenance.staleAfterSeconds,
      timeframe: {
        unit: evaluation.config.timeframe_unit,
        unit_number: evaluation.config.timeframe_unit_number,
        label: context.provenance.timeframe,
      },
      detected_gaps: context.provenance.detectedGaps.map((gap) => ({
        before_timestamp: gap.beforeTimestamp,
        after_timestamp: gap.afterTimestamp,
        missing_bars: gap.missingBars,
      })),
      gap_count: context.provenance.detectedGapCount,
    };
  }
  const closed = evaluation.candles.filter((candle) => !candle.is_partial);
  const partial = evaluation.candles.length - closed.length;
  return {
    closed_candle_count: closed.length,
    partial_candle_count: partial,
    latest_candle_timestamp: null,
    data_age_seconds: null,
    is_stale: false,
    stale_after_seconds: evaluation.config.max_data_staleness_seconds,
    timeframe: {
      unit: evaluation.config.timeframe_unit,
      unit_number: evaluation.config.timeframe_unit_number,
      label: timeframeLabel(evaluation.config.timeframe_unit, evaluation.config.timeframe_unit_number),
    },
    detected_gaps: [],
    gap_count: 0,
  };
}

function legacyScoreDrivers(bias: BotMarketBias, reasoning: string[]): BotAnalysisScoreDrivers {
  const drivers: BotAnalysisScoreDrivers = { bullish: [], bearish: [], neutral: [] };
  drivers[bias].push(...cleanList(reasoning).slice(0, 3));
  return drivers;
}

function invalidationText(
  bias: BotMarketBias,
  invalidation: number | null,
  support: number | null,
  resistance: number | null,
): string {
  if (bias === "bullish") return invalidation !== null ? `Bullish setup invalidates below ${invalidation}.` : "Bullish invalidation level is unavailable.";
  if (bias === "bearish") return invalidation !== null ? `Bearish setup invalidates above ${invalidation}.` : "Bearish invalidation level is unavailable.";
  if (support !== null && resistance !== null) return `Neutral read changes outside ${support} support or ${resistance} resistance.`;
  return "A deterministic invalidation level is unavailable.";
}

function normalizeBias(value: unknown, weights: BotDirectionalProbabilities): BotMarketBias {
  if (value === "bullish" || value === "bearish" || value === "neutral") return value;
  if (weights.sideways >= weights.bullish && weights.sideways >= weights.bearish) return "neutral";
  return weights.bullish > weights.bearish ? "bullish" : "bearish";
}

function scoreLabel(score: number): string {
  return score >= 80 ? "strong" : score >= 60 ? "acceptable" : score >= 40 ? "limited" : "weak";
}

function cleanText(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function cleanList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(cleanText).filter((item): item is string => item !== null) : [];
}

function cleanLabel(value: unknown): string | null {
  const text = cleanText(value);
  return text ? text.replace(/[_-]+/g, " ") : null;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function finiteNonNegative(value: unknown): number {
  const number = finiteNumber(value);
  return number === null ? 0 : Math.max(0, number);
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}
