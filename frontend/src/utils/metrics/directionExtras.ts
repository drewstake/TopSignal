import type { AccountTrade } from "../../lib/types";
import { EPSILON, average, metric, missingMetric, sum, type DerivedMetricValue } from "./shared";

export type InferredDirection = "LONG" | "SHORT";

export interface DirectionSample {
  direction: InferredDirection;
  pnl: number;
}

export interface DirectionSideMetrics {
  trades: DerivedMetricValue;
  winRate: DerivedMetricValue;
  pnl: DerivedMetricValue;
  expectancy: DerivedMetricValue;
  profitFactor: DerivedMetricValue;
  avgWin: DerivedMetricValue;
  avgLoss: DerivedMetricValue;
  largeLossRate: DerivedMetricValue;
}

export interface DirectionExtrasMetrics {
  long: DirectionSideMetrics;
  short: DirectionSideMetrics;
  longPnlShare: DerivedMetricValue;
  shortPnlShare: DerivedMetricValue;
  insight: string;
}

export function inferDirectionFromSide(side: string): InferredDirection | null {
  const normalized = side.trim().toUpperCase();
  // Closed executions may use BUY/SELL; some feeds return LONG/SHORT directly.
  if (normalized === "SELL" || normalized === "LONG") {
    return "LONG";
  }
  if (normalized === "BUY" || normalized === "SHORT") {
    return "SHORT";
  }
  return null;
}

export function buildDirectionSamples(trades: AccountTrade[]): DirectionSample[] {
  return trades.flatMap((trade) => {
    const direction = inferDirectionFromSide(trade.side);
    if (direction === null || trade.pnl === null || !Number.isFinite(trade.pnl)) {
      return [];
    }
    return [{ direction, pnl: trade.pnl }];
  });
}

export function computeDirectionExtras(samples: DirectionSample[], netPnl: number): DirectionExtrasMetrics {
  const longSamples = samples.filter((sample) => sample.direction === "LONG");
  const shortSamples = samples.filter((sample) => sample.direction === "SHORT");

  const long = computeDirectionSideMetrics(longSamples, "long");
  const short = computeDirectionSideMetrics(shortSamples, "short");

  const pnlDenominator = Math.abs(netPnl);
  const longPnlShare =
    pnlDenominator <= EPSILON || long.pnl.value === null
      ? missingMetric("needs non-zero net PnL")
      : metric((long.pnl.value / pnlDenominator) * 100);
  const shortPnlShare =
    pnlDenominator <= EPSILON || short.pnl.value === null
      ? missingMetric("needs non-zero net PnL")
      : metric((short.pnl.value / pnlDenominator) * 100);

  return {
    long,
    short,
    longPnlShare,
    shortPnlShare,
    insight: computeDirectionInsight(long.expectancy, short.expectancy, long.largeLossRate, short.largeLossRate),
  };
}

function computeDirectionSideMetrics(samples: DirectionSample[], sideLabel: "long" | "short"): DirectionSideMetrics {
  const sideCapitalized = sideLabel === "long" ? "long" : "short";
  if (samples.length === 0) {
    const noTradesReason = `needs at least one ${sideCapitalized} trade`;
    return {
      trades: metric(0),
      winRate: missingMetric(noTradesReason),
      pnl: metric(0),
      expectancy: missingMetric(noTradesReason),
      profitFactor: missingMetric(`needs ${sideCapitalized} wins and losses`),
      avgWin: missingMetric(`needs ${sideCapitalized} winning trades`),
      avgLoss: missingMetric(`needs ${sideCapitalized} losing trades`),
      largeLossRate: missingMetric(`needs ${sideCapitalized} losses`),
    };
  }

  const pnls = samples.map((sample) => sample.pnl);
  const wins = pnls.filter((value) => value > 0);
  const losses = pnls.filter((value) => value < 0);

  const pnlTotal = sum(pnls);
  const expectancy = pnlTotal / samples.length;
  const grossWins = sum(wins);
  const grossLossAbs = Math.abs(sum(losses));
  const avgWin = average(wins);
  const avgLoss = average(losses);

  const largeLossRate = computeSideLargeLossRate(pnls, avgLoss, sideCapitalized);

  return {
    trades: metric(samples.length),
    winRate: metric((wins.length / samples.length) * 100),
    pnl: metric(pnlTotal),
    expectancy: metric(expectancy),
    profitFactor:
      grossLossAbs <= EPSILON ? missingMetric(`needs ${sideCapitalized} losing trades`) : metric(grossWins / grossLossAbs),
    avgWin: avgWin === null ? missingMetric(`needs ${sideCapitalized} winning trades`) : metric(avgWin),
    avgLoss: avgLoss === null ? missingMetric(`needs ${sideCapitalized} losing trades`) : metric(avgLoss),
    largeLossRate,
  };
}

function computeSideLargeLossRate(pnls: number[], avgLoss: number | null, sideLabel: string): DerivedMetricValue {
  if (avgLoss === null || Math.abs(avgLoss) <= EPSILON) {
    return missingMetric(`needs ${sideLabel} losses`);
  }
  const threshold = 2 * Math.abs(avgLoss);
  const largeLossCount = pnls.filter((pnl) => pnl <= -threshold).length;
  return metric((largeLossCount / pnls.length) * 100);
}

function computeDirectionInsight(
  longExpectancy: DerivedMetricValue,
  shortExpectancy: DerivedMetricValue,
  longLargeLossRate: DerivedMetricValue,
  shortLargeLossRate: DerivedMetricValue,
): string {
  let message = "Expectancy is similar, bias can be based on market regime.";

  if (longExpectancy.value !== null && shortExpectancy.value !== null) {
    if (outperforms(shortExpectancy.value, longExpectancy.value)) {
      message = "Shorts outperform longs on expectancy.";
    } else if (outperforms(longExpectancy.value, shortExpectancy.value)) {
      message = "Longs outperform shorts on expectancy.";
    }
  } else {
    message = "N/A (needs long/short expectancy data)";
  }

  if (longLargeLossRate.value !== null && shortLargeLossRate.value !== null) {
    if (longLargeLossRate.value > shortLargeLossRate.value + EPSILON) {
      return `${message} Large losses are worse on longs.`;
    }
    if (shortLargeLossRate.value > longLargeLossRate.value + EPSILON) {
      return `${message} Large losses are worse on shorts.`;
    }
  }

  return message;
}

function outperforms(candidate: number, baseline: number): boolean {
  const denominator = Math.max(Math.abs(baseline), EPSILON);
  return (candidate - baseline) / denominator >= 0.2;
}
