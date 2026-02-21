import type { AccountTrade } from "../../lib/types";
import { EPSILON, average, metric, missingMetric, percentile, type DerivedMetricValue } from "./shared";

export interface PayoffExtrasInput {
  trades: AccountTrade[];
  avgWin: number;
  avgLoss: number;
  currentWinRate: number;
  breakevenWinRate: number | null;
  canUseTradeDistribution: boolean;
  tradeDistributionReason: string;
}

export interface PayoffExtrasMetrics {
  wrCushion: DerivedMetricValue;
  largeLossThreshold: DerivedMetricValue;
  largeLossRate: DerivedMetricValue;
  p95Loss: DerivedMetricValue;
  capture: DerivedMetricValue;
  containment: DerivedMetricValue;
  insight: string;
}

export function computePayoffExtras(input: PayoffExtrasInput): PayoffExtrasMetrics {
  const wrCushion =
    input.breakevenWinRate === null
      ? missingMetric("needs breakeven win rate")
      : metric(input.currentWinRate - input.breakevenWinRate);

  if (!input.canUseTradeDistribution) {
    const missing = missingMetric(input.tradeDistributionReason);
    return {
      wrCushion,
      largeLossThreshold: missing,
      largeLossRate: missing,
      p95Loss: missing,
      capture: missingMetric("needs MFE data"),
      containment: missingMetric("needs MAE data"),
      insight: computePayoffInsight({
        currentWinRate: input.currentWinRate,
        breakevenWinRate: input.breakevenWinRate,
        largeLossRate: missing,
        capture: missing,
      }),
    };
  }

  const pnlValues = input.trades
    .map((trade) => trade.pnl)
    .filter((value): value is number => value !== null && Number.isFinite(value));
  const lossPnls = pnlValues.filter((value) => value < 0);

  const largeLossThresholdValue = 2 * Math.abs(input.avgLoss);
  const largeLossThreshold =
    largeLossThresholdValue <= EPSILON ? missingMetric("needs non-zero average loss") : metric(largeLossThresholdValue);
  const largeLossRate = computeLargeLossRate(pnlValues, largeLossThreshold);

  const p95LossMagnitude =
    lossPnls.length < 5 ? null : percentile(lossPnls.map((loss) => Math.abs(loss)), 95);
  const p95Loss =
    p95LossMagnitude === null ? missingMetric("needs at least 5 losing trades") : metric(-p95LossMagnitude);

  const capture = computeCapture(input.trades, input.avgWin);
  const containment = computeContainment(input.trades, input.avgLoss);

  return {
    wrCushion,
    largeLossThreshold,
    largeLossRate,
    p95Loss,
    capture,
    containment,
    insight: computePayoffInsight({
      currentWinRate: input.currentWinRate,
      breakevenWinRate: input.breakevenWinRate,
      largeLossRate,
      capture,
    }),
  };
}

function computeLargeLossRate(pnlValues: number[], largeLossThreshold: DerivedMetricValue): DerivedMetricValue {
  if (largeLossThreshold.value === null) {
    return missingMetric("needs non-zero average loss");
  }
  if (pnlValues.length === 0) {
    return missingMetric("needs closed trade PnL data");
  }

  const threshold = largeLossThreshold.value;
  return metric((pnlValues.filter((pnl) => pnl <= -threshold).length / pnlValues.length) * 100);
}

function computeCapture(trades: AccountTrade[], avgWin: number): DerivedMetricValue {
  const winningMfeValues = trades
    .filter((trade) => trade.pnl !== null && trade.pnl > 0)
    .map((trade) => trade.mfe)
    .filter((value): value is number => value !== null && value !== undefined && Number.isFinite(value))
    .map((value) => Math.abs(value))
    .filter((value) => value > EPSILON);

  if (winningMfeValues.length === 0) {
    return missingMetric("needs MFE data");
  }

  const avgMfeWins = average(winningMfeValues);
  if (avgMfeWins === null || avgMfeWins <= EPSILON) {
    return missingMetric("needs MFE data");
  }

  return metric(avgWin / avgMfeWins);
}

function computeContainment(trades: AccountTrade[], avgLoss: number): DerivedMetricValue {
  const losingMaeValues = trades
    .filter((trade) => trade.pnl !== null && trade.pnl < 0)
    .map((trade) => trade.mae)
    .filter((value): value is number => value !== null && value !== undefined && Number.isFinite(value))
    .map((value) => Math.abs(value))
    .filter((value) => value > EPSILON);

  if (losingMaeValues.length === 0) {
    return missingMetric("needs MAE data");
  }

  const avgMaeLosses = average(losingMaeValues);
  if (avgMaeLosses === null || avgMaeLosses <= EPSILON) {
    return missingMetric("needs MAE data");
  }

  return metric(Math.abs(avgLoss) / avgMaeLosses);
}

function computePayoffInsight(args: {
  currentWinRate: number;
  breakevenWinRate: number | null;
  largeLossRate: DerivedMetricValue;
  capture: DerivedMetricValue;
}): string {
  if (args.breakevenWinRate !== null && args.currentWinRate < args.breakevenWinRate) {
    return "Your win rate is below breakeven for this payoff.";
  }
  if (args.largeLossRate.value !== null && args.largeLossRate.value > 5) {
    return "Large losses happen often, tighten stops or reduce size.";
  }
  if (args.capture.value !== null && args.capture.value < 0.35) {
    return "You're capturing a small share of MFE, review exits.";
  }
  if (args.breakevenWinRate === null) {
    return "N/A (needs breakeven win rate)";
  }
  return "Payoff supports your current win rate.";
}
