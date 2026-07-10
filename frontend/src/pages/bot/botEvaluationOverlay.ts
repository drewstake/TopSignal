import type { BotActivity, BotConfig, BotDecision, BotEvaluation, BotTimeframeUnit } from "../../lib/types";
import { intervalSecondsFor } from "./botCandleGaps";

export const DEFAULT_EVALUATION_STALE_AFTER_BARS = 2;

export type EvaluationOverlayAction = "BUY" | "SELL";
export type EvaluationOverlayDirection = "long" | "short";
export type EvaluationOverlayLevelRole = "entry" | "stop" | "target";
export type EvaluationFreshnessStatus = "fresh" | "stale" | "unknown";

export interface EvaluationOverlayLevelPrices {
  entry: number | null;
  stop: number | null;
  target: number | null;
}

export interface EvaluationOverlayLevel {
  role: EvaluationOverlayLevelRole;
  label: "Entry" | "Stop" | "Target";
  price: number;
}

export interface EvaluationOverlayBand {
  kind: "risk" | "reward";
  fromPrice: number;
  toPrice: number;
  lowPrice: number;
  highPrice: number;
}

export interface EvaluationOverlayGeometry {
  entry: EvaluationOverlayLevel | null;
  stop: EvaluationOverlayLevel | null;
  target: EvaluationOverlayLevel | null;
  riskBand: EvaluationOverlayBand | null;
  rewardBand: EvaluationOverlayBand | null;
  /** Display-only ratio calculated from the explicit API price levels. */
  riskRewardRatio: number | null;
}

export interface EvaluationOverlayStaleness {
  status: EvaluationFreshnessStatus;
  isStale: boolean;
  barsBehind: number | null;
  staleAfterBars: number;
  latestClosedTimestamp: string | null;
}

export interface EvaluationOverlayModel {
  decisionId: number;
  action: EvaluationOverlayAction;
  direction: EvaluationOverlayDirection;
  /** Candle timestamp the backend evaluation analyzed. */
  timestamp: string | null;
  /** Wall-clock timestamp at which the backend generated/persisted the evaluation. */
  evaluatedAt: string | null;
  levels: EvaluationOverlayLevelPrices;
  geometry: EvaluationOverlayGeometry;
  staleness: EvaluationOverlayStaleness;
  /** Ratio suitable for a label, from explicit levels or the API evaluation features. */
  riskRewardRatio: number | null;
  riskRewardRatioSource: "levels" | "api" | null;
}

export interface BuildEvaluationOverlayOptions {
  /** Timestamp of the newest closed candle visible to the chart. */
  latestClosedTimestamp?: string | Date | null;
  timeframeUnit?: BotTimeframeUnit;
  timeframeUnitNumber?: number;
  staleAfterBars?: number;
}

export interface SelectLatestActionableEvaluationInput {
  bot: BotConfig | null;
  activity: BotActivity | null;
  lastEvaluation: BotEvaluation | null;
  cachedEvaluation?: BotEvaluation | null;
}

/**
 * Select the newest actionable evaluation for the bot's configured market.
 * Contract/timeframe checks prevent levels cached under a reused bot id from
 * leaking onto a chart after that bot is edited.
 */
export function selectLatestActionableEvaluation({
  bot,
  activity,
  lastEvaluation,
  cachedEvaluation = null,
}: SelectLatestActionableEvaluationInput): BotEvaluation | null {
  if (!bot) {
    return null;
  }

  const directEvaluation =
    evaluationMatchesBotMarket(lastEvaluation, bot) && isActionableEvaluation(lastEvaluation)
      ? lastEvaluation
      : evaluationMatchesBotMarket(cachedEvaluation, bot) && isActionableEvaluation(cachedEvaluation)
        ? cachedEvaluation
        : null;
  const activityDecision =
    activityConfigMatchesBotMarket(activity, bot)
      ? [...activity.decisions]
          .filter((decision) => decision.contract_id === bot.contract_id && isActionableDecision(decision))
          .sort((left, right) => sortableTimestamp(right.created_at) - sortableTimestamp(left.created_at))[0] ?? null
      : null;

  if (!activityDecision || directEvaluation?.decision.id === activityDecision.id) {
    return directEvaluation;
  }
  if (directEvaluation && sortableTimestamp(directEvaluation.decision.created_at) >= sortableTimestamp(activityDecision.created_at)) {
    return directEvaluation;
  }
  return buildActivityEvaluation(bot, activityDecision);
}

/**
 * Build a chart-ready model for the latest actionable evaluation. Strategy
 * prices come only from the standardized API trade_levels payload. The legacy
 * decision price is used only as an entry fallback; stops and targets are
 * never reconstructed from risk/reward features or strategy configuration.
 */
export function buildEvaluationOverlayModel(
  evaluation: BotEvaluation | null,
  options: BuildEvaluationOverlayOptions = {},
): EvaluationOverlayModel | null {
  if (!evaluation || !isActionableEvaluationAction(evaluation.decision.action)) {
    return null;
  }

  const action = evaluation.decision.action;
  const levels = extractEvaluationOverlayLevels(evaluation);
  const geometry = buildEvaluationOverlayGeometry(action, levels);
  const timestampMs = evaluationCandleTimestampMs(evaluation);
  const timeframeUnit = options.timeframeUnit ?? evaluation.config.timeframe_unit;
  const timeframeUnitNumber = options.timeframeUnitNumber ?? evaluation.config.timeframe_unit_number;
  const staleness = buildEvaluationOverlayStaleness(
    timestampMs,
    options.latestClosedTimestamp ?? null,
    timeframeUnit,
    timeframeUnitNumber,
    options.staleAfterBars,
  );
  const apiRatio = finiteNonNegativeNumber(evaluation.analysis?.trade_evaluation?.features.risk_reward_ratio);
  const riskRewardRatio = geometry.riskRewardRatio ?? apiRatio;

  return {
    decisionId: evaluation.decision.id,
    action,
    direction: action === "BUY" ? "long" : "short",
    timestamp: timestampMs === null ? null : new Date(timestampMs).toISOString(),
    evaluatedAt: firstValidTimestamp(evaluation.analysis?.generated_at, evaluation.decision.created_at),
    levels,
    geometry,
    staleness,
    riskRewardRatio,
    riskRewardRatioSource: geometry.riskRewardRatio !== null ? "levels" : apiRatio !== null ? "api" : null,
  };
}

export function extractEvaluationOverlayLevels(evaluation: BotEvaluation): EvaluationOverlayLevelPrices {
  const apiLevels = evaluation.trade_levels;
  return {
    entry: finiteNumber(apiLevels?.entry) ?? finiteNumber(evaluation.decision.price),
    stop: finiteNumber(apiLevels?.stop),
    target: finiteNumber(apiLevels?.target),
  };
}

/**
 * Converts explicit prices into direction-aware price-domain geometry. Invalid
 * directional relationships still keep their API line, but do not produce a
 * misleading risk/reward fill.
 */
export function buildEvaluationOverlayGeometry(
  action: EvaluationOverlayAction,
  levels: EvaluationOverlayLevelPrices,
): EvaluationOverlayGeometry {
  const entry = toLevel("entry", levels.entry);
  const stop = toLevel("stop", levels.stop);
  const target = toLevel("target", levels.target);
  const riskBand =
    entry && stop && isRiskDirectionValid(action, entry.price, stop.price)
      ? toBand("risk", entry.price, stop.price)
      : null;
  const rewardBand =
    entry && target && isRewardDirectionValid(action, entry.price, target.price)
      ? toBand("reward", entry.price, target.price)
      : null;
  const riskDistance = riskBand ? Math.abs(riskBand.toPrice - riskBand.fromPrice) : null;
  const rewardDistance = rewardBand ? Math.abs(rewardBand.toPrice - rewardBand.fromPrice) : null;
  const riskRewardRatio =
    riskDistance !== null && riskDistance > 0 && rewardDistance !== null
      ? rewardDistance / riskDistance
      : null;

  return { entry, stop, target, riskBand, rewardBand, riskRewardRatio };
}

export function evaluationCandleTimestampMs(evaluation: BotEvaluation): number | null {
  const analysisTimestamp = timestampMs(evaluation.analysis?.candle_timestamp);
  if (analysisTimestamp !== null) {
    return analysisTimestamp;
  }

  let latestClosedTimestamp: number | null = null;
  for (const candle of evaluation.candles) {
    if (candle.is_partial) {
      continue;
    }
    const candleTimestamp = timestampMs(candle.timestamp);
    if (candleTimestamp !== null && (latestClosedTimestamp === null || candleTimestamp > latestClosedTimestamp)) {
      latestClosedTimestamp = candleTimestamp;
    }
  }
  if (latestClosedTimestamp !== null) {
    return latestClosedTimestamp;
  }

  return timestampMs(evaluation.decision.candle_timestamp);
}

export function buildEvaluationOverlayStaleness(
  evaluationTimestampMs: number | null,
  latestClosedTimestamp: string | Date | null,
  timeframeUnit: BotTimeframeUnit,
  timeframeUnitNumber: number,
  staleAfterBars = DEFAULT_EVALUATION_STALE_AFTER_BARS,
): EvaluationOverlayStaleness {
  const threshold = Number.isFinite(staleAfterBars)
    ? Math.max(1, Math.trunc(staleAfterBars))
    : DEFAULT_EVALUATION_STALE_AFTER_BARS;
  const latestClosedMs = timestampMs(latestClosedTimestamp);
  const normalizedLatestTimestamp = latestClosedMs === null ? null : new Date(latestClosedMs).toISOString();
  const intervalMs = intervalSecondsFor(timeframeUnit, timeframeUnitNumber) * 1000;
  if (
    evaluationTimestampMs === null ||
    !Number.isFinite(evaluationTimestampMs) ||
    latestClosedMs === null ||
    !Number.isFinite(intervalMs) ||
    intervalMs <= 0
  ) {
    return {
      status: "unknown",
      isStale: false,
      barsBehind: null,
      staleAfterBars: threshold,
      latestClosedTimestamp: normalizedLatestTimestamp,
    };
  }

  const barsBehind = Math.max(0, Math.floor((latestClosedMs - evaluationTimestampMs) / intervalMs));
  const isStale = barsBehind >= threshold;
  return {
    status: isStale ? "stale" : "fresh",
    isStale,
    barsBehind,
    staleAfterBars: threshold,
    latestClosedTimestamp: normalizedLatestTimestamp,
  };
}

export function isActionableEvaluation(evaluation: BotEvaluation | null | undefined): evaluation is BotEvaluation {
  return evaluation !== null && evaluation !== undefined && isActionableEvaluationAction(evaluation.decision.action);
}

function isActionableEvaluationAction(action: BotEvaluation["decision"]["action"]): action is EvaluationOverlayAction {
  return action === "BUY" || action === "SELL";
}

function isActionableDecision(decision: BotDecision): boolean {
  return decision.action === "BUY" || decision.action === "SELL";
}

function evaluationMatchesBotMarket(evaluation: BotEvaluation | null | undefined, bot: BotConfig): boolean {
  return (
    evaluation?.config.id === bot.id &&
    evaluation.config.contract_id === bot.contract_id &&
    evaluation.decision.contract_id === bot.contract_id &&
    evaluation.config.timeframe_unit === bot.timeframe_unit &&
    evaluation.config.timeframe_unit_number === bot.timeframe_unit_number
  );
}

function activityConfigMatchesBotMarket(activity: BotActivity | null, bot: BotConfig): activity is BotActivity {
  return (
    activity?.config.id === bot.id &&
    activity.config.contract_id === bot.contract_id &&
    activity.config.timeframe_unit === bot.timeframe_unit &&
    activity.config.timeframe_unit_number === bot.timeframe_unit_number
  );
}

function buildActivityEvaluation(config: BotConfig, decision: BotDecision): BotEvaluation {
  return {
    status: "evaluated",
    correlation_id: null,
    idempotency_key: null,
    duplicate_of_order_attempt_id: null,
    config,
    run: null,
    decision,
    order_attempt: null,
    risk_events: [],
    trade_levels: null,
    analysis: null,
    candles: [],
  };
}

function sortableTimestamp(value: string): number {
  return timestampMs(value) ?? Number.NEGATIVE_INFINITY;
}

function toLevel(role: EvaluationOverlayLevelRole, price: number | null): EvaluationOverlayLevel | null {
  const value = finiteNumber(price);
  if (value === null) {
    return null;
  }
  const label = role === "entry" ? "Entry" : role === "stop" ? "Stop" : "Target";
  return { role, label, price: value };
}

function toBand(kind: EvaluationOverlayBand["kind"], fromPrice: number, toPrice: number): EvaluationOverlayBand {
  return {
    kind,
    fromPrice,
    toPrice,
    lowPrice: Math.min(fromPrice, toPrice),
    highPrice: Math.max(fromPrice, toPrice),
  };
}

function isRiskDirectionValid(action: EvaluationOverlayAction, entry: number, stop: number): boolean {
  return action === "BUY" ? stop < entry : stop > entry;
}

function isRewardDirectionValid(action: EvaluationOverlayAction, entry: number, target: number): boolean {
  return action === "BUY" ? target > entry : target < entry;
}

function finiteNumber(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function finiteNonNegativeNumber(value: number | null | undefined): number | null {
  const parsed = finiteNumber(value);
  return parsed !== null && parsed >= 0 ? parsed : null;
}

function timestampMs(value: string | Date | null | undefined): number | null {
  if (value instanceof Date) {
    const valueMs = value.getTime();
    return Number.isFinite(valueMs) ? valueMs : null;
  }
  if (typeof value !== "string" || value.trim() === "") {
    return null;
  }
  const valueMs = Date.parse(value);
  return Number.isFinite(valueMs) ? valueMs : null;
}

function firstValidTimestamp(...values: Array<string | null | undefined>): string | null {
  for (const value of values) {
    const valueMs = timestampMs(value);
    if (valueMs !== null) {
      return new Date(valueMs).toISOString();
    }
  }
  return null;
}
