import { computeBreakevenWinRate } from "../../../utils/metrics";
import { buildDirectionSamples, computeDirectionExtras } from "../../../utils/metrics/directionExtras";
import { computePayoffExtras } from "../../../utils/metrics/payoffExtras";
import { computeSwingExtras } from "../../../utils/metrics/swingExtras";
import type { DashboardDerivedMetrics, DashboardMetricsInput, DirectionMetrics, MetricValue, PayoffMetrics, StabilityMetrics } from "./types";

const EPSILON = 1e-9;

export function computeDashboardDerivedMetrics(input: DashboardMetricsInput): DashboardDerivedMetrics {
  const breakevenWinRate = computeBreakevenWinRate(input.summary.avg_win, input.summary.avg_loss);

  return {
    winLossRatio: computeWinLossRatio(input.summary.avg_win, input.summary.avg_loss),
    winDurationOverLossDuration: computeWinDurationRatio(
      input.summary.avg_win_duration_minutes,
      input.summary.avg_loss_duration_minutes,
    ),
    direction: computeDirectionMetrics(input),
    stability: computeStabilityMetrics(input.dailyPnlDays, input.summary.net_pnl, input.summary.profit_per_day),
    payoff: computePayoffMetrics(input, breakevenWinRate),
  };
}

function computeWinLossRatio(avgWin: number, avgLoss: number): MetricValue {
  const lossMagnitude = Math.abs(avgLoss);
  // Win/Loss Ratio = Average Win / abs(Average Loss).
  if (lossMagnitude <= EPSILON) {
    return missingMetric("needs a non-zero average loss");
  }
  return metric(avgWin / lossMagnitude);
}

function computeWinDurationRatio(avgWinMinutes: number, avgLossMinutes: number): MetricValue {
  // Win Duration / Loss Duration = Avg Win Duration / Avg Loss Duration.
  if (avgLossMinutes <= EPSILON) {
    return missingMetric("needs a non-zero average loss duration");
  }
  return metric(avgWinMinutes / avgLossMinutes);
}

function computeDirectionMetrics(input: DashboardMetricsInput): DirectionMetrics {
  if (input.directionDataIssue) {
    return missingDirectionMetrics(input.directionDataIssue);
  }

  if (input.summary.trade_count === 0) {
    return emptyDirectionMetrics("needs directional trades");
  }

  if (!input.hasCompleteDirectionalHistory) {
    return missingDirectionMetrics("needs complete closed-trade history for this range");
  }

  const directionalSamples = buildDirectionSamples(input.trades);

  if (directionalSamples.length === 0) {
    return missingDirectionMetrics("needs closed trades with BUY/SELL or LONG/SHORT side values");
  }

  const extras = computeDirectionExtras(directionalSamples, input.summary.net_pnl);
  const longTrades = extras.long.trades.value;
  const shortTrades = extras.short.trades.value;
  const totalDirectionalTrades =
    longTrades === null || shortTrades === null ? 0 : longTrades + shortTrades;

  const longPercent =
    longTrades === null || totalDirectionalTrades <= 0
      ? missingMetric("needs directional trades")
      : metric((longTrades / totalDirectionalTrades) * 100);

  return {
    longTrades: extras.long.trades,
    shortTrades: extras.short.trades,
    longPercent,
    longPnl: extras.long.pnl,
    shortPnl: extras.short.pnl,
    longWinRate: extras.long.winRate,
    shortWinRate: extras.short.winRate,
    longExpectancy: extras.long.expectancy,
    shortExpectancy: extras.short.expectancy,
    longProfitFactor: extras.long.profitFactor,
    shortProfitFactor: extras.short.profitFactor,
    longAvgWin: extras.long.avgWin,
    longAvgLoss: extras.long.avgLoss,
    shortAvgWin: extras.short.avgWin,
    shortAvgLoss: extras.short.avgLoss,
    longLargeLossRate: extras.long.largeLossRate,
    shortLargeLossRate: extras.short.largeLossRate,
    longPnlShare: extras.longPnlShare,
    shortPnlShare: extras.shortPnlShare,
    insight: extras.insight,
  };
}

function computeStabilityMetrics(
  dailyPnlDays: DashboardMetricsInput["dailyPnlDays"],
  netPnl: number,
  profitPerDay: number,
): StabilityMetrics {
  // Day-level stability metrics require a daily net PnL series that is already grouped
  // by trading day in the account timezone before it reaches this function.
  const dailyNetValues = dailyPnlDays
    .map((day) => day.net_pnl)
    .filter((value) => Number.isFinite(value));
  const swingExtras = computeSwingExtras(dailyPnlDays, profitPerDay);

  if (dailyNetValues.length === 0) {
    const reason = "needs daily net PnL values grouped by trading day";
    return {
      bestDay: missingMetric(reason),
      worstDay: missingMetric(reason),
      dailyPnlVolatility: missingMetric(reason),
      bestDayPercentOfNet: missingMetric(reason),
      worstDayPercentOfNet: missingMetric(reason),
      medianDayPnl: swingExtras.medianDayPnl,
      avgGreenDay: swingExtras.avgGreenDay,
      avgRedDay: swingExtras.avgRedDay,
      redDayPercent: swingExtras.redDayPercent,
      nukeRatio: swingExtras.nukeRatio,
      greenRedDaySizeRatio: swingExtras.greenRedDaySizeRatio,
      insight: swingExtras.insight,
    };
  }

  const bestDay = Math.max(...dailyNetValues);
  const worstDay = Math.min(...dailyNetValues);
  // Daily PnL volatility ($): population standard deviation of daily net PnL.
  const dailyPnlVolatility = populationStandardDeviation(dailyNetValues);

  if (Math.abs(netPnl) <= EPSILON) {
    const denominatorReason = "needs non-zero net PnL for percentage ratios";
    return {
      bestDay: metric(bestDay),
      worstDay: metric(worstDay),
      dailyPnlVolatility: metric(dailyPnlVolatility),
      bestDayPercentOfNet: missingMetric(denominatorReason),
      worstDayPercentOfNet: missingMetric(denominatorReason),
      medianDayPnl: swingExtras.medianDayPnl,
      avgGreenDay: swingExtras.avgGreenDay,
      avgRedDay: swingExtras.avgRedDay,
      redDayPercent: swingExtras.redDayPercent,
      nukeRatio: swingExtras.nukeRatio,
      greenRedDaySizeRatio: swingExtras.greenRedDaySizeRatio,
      insight: swingExtras.insight,
    };
  }

  // Best Day % of Net PnL = Best Day / Net PnL.
  const bestDayPercentOfNet = (bestDay / netPnl) * 100;
  // Worst Day % of Net PnL = abs(Worst Day) / Net PnL.
  const worstDayPercentOfNet = (Math.abs(worstDay) / netPnl) * 100;

  return {
    bestDay: metric(bestDay),
    worstDay: metric(worstDay),
    dailyPnlVolatility: metric(dailyPnlVolatility),
    bestDayPercentOfNet: metric(bestDayPercentOfNet),
    worstDayPercentOfNet: metric(worstDayPercentOfNet),
    medianDayPnl: swingExtras.medianDayPnl,
    avgGreenDay: swingExtras.avgGreenDay,
    avgRedDay: swingExtras.avgRedDay,
    redDayPercent: swingExtras.redDayPercent,
    nukeRatio: swingExtras.nukeRatio,
    greenRedDaySizeRatio: swingExtras.greenRedDaySizeRatio,
    insight: swingExtras.insight,
  };
}

function computePayoffMetrics(input: DashboardMetricsInput, breakevenWinRate: MetricValue): PayoffMetrics {
  const canUseTradeDistribution = !input.directionDataIssue && input.hasCompleteDirectionalHistory;
  const tradeDistributionReason =
    input.directionDataIssue ??
    (input.hasCompleteDirectionalHistory ? "needs closed trade PnL data" : "needs complete closed-trade history for this range");

  const payoffExtras = computePayoffExtras({
    trades: input.trades,
    avgWin: input.summary.avg_win,
    avgLoss: input.summary.avg_loss,
    currentWinRate: input.summary.win_rate,
    breakevenWinRate: breakevenWinRate.value,
    canUseTradeDistribution,
    tradeDistributionReason,
  });

  return {
    averageWin: metric(input.summary.avg_win),
    averageLoss: metric(input.summary.avg_loss),
    breakevenWinRate,
    currentWinRate: metric(input.summary.win_rate),
    wrCushion: payoffExtras.wrCushion,
    largeLossThreshold: payoffExtras.largeLossThreshold,
    largeLossRate: payoffExtras.largeLossRate,
    p95Loss: payoffExtras.p95Loss,
    capture: payoffExtras.capture,
    containment: payoffExtras.containment,
    insight: payoffExtras.insight,
  };
}

function sum(values: number[]): number {
  return values.reduce((total, value) => total + value, 0);
}

function populationStandardDeviation(values: number[]): number {
  if (values.length === 0) {
    return 0;
  }
  const mean = sum(values) / values.length;
  const variance = sum(values.map((value) => (value - mean) ** 2)) / values.length;
  return Math.sqrt(Math.max(variance, 0));
}

function metric(value: number): MetricValue {
  return { value };
}

function missingMetric(missingReason: string): MetricValue {
  return {
    value: null,
    missingReason,
  };
}

function emptyDirectionMetrics(reason: string): DirectionMetrics {
  return {
    longTrades: metric(0),
    shortTrades: metric(0),
    longPercent: missingMetric(reason),
    longPnl: metric(0),
    shortPnl: metric(0),
    longWinRate: missingMetric(reason),
    shortWinRate: missingMetric(reason),
    longExpectancy: missingMetric(reason),
    shortExpectancy: missingMetric(reason),
    longProfitFactor: missingMetric(reason),
    shortProfitFactor: missingMetric(reason),
    longAvgWin: missingMetric(reason),
    longAvgLoss: missingMetric(reason),
    shortAvgWin: missingMetric(reason),
    shortAvgLoss: missingMetric(reason),
    longLargeLossRate: missingMetric(reason),
    shortLargeLossRate: missingMetric(reason),
    longPnlShare: missingMetric("needs non-zero net PnL"),
    shortPnlShare: missingMetric("needs non-zero net PnL"),
    insight: `N/A (${reason})`,
  };
}

function missingDirectionMetrics(reason: string): DirectionMetrics {
  return {
    longTrades: missingMetric(reason),
    shortTrades: missingMetric(reason),
    longPercent: missingMetric(reason),
    longPnl: missingMetric(reason),
    shortPnl: missingMetric(reason),
    longWinRate: missingMetric(reason),
    shortWinRate: missingMetric(reason),
    longExpectancy: missingMetric(reason),
    shortExpectancy: missingMetric(reason),
    longProfitFactor: missingMetric(reason),
    shortProfitFactor: missingMetric(reason),
    longAvgWin: missingMetric(reason),
    longAvgLoss: missingMetric(reason),
    shortAvgWin: missingMetric(reason),
    shortAvgLoss: missingMetric(reason),
    longLargeLossRate: missingMetric(reason),
    shortLargeLossRate: missingMetric(reason),
    longPnlShare: missingMetric(reason),
    shortPnlShare: missingMetric(reason),
    insight: `N/A (${reason})`,
  };
}
