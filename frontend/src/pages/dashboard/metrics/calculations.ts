import type { DashboardDerivedMetrics, DashboardMetricsInput, DirectionMetrics, MetricValue, StabilityMetrics } from "./types";

const EPSILON = 1e-9;

type InferredDirection = "LONG" | "SHORT";

interface DirectionSample {
  direction: InferredDirection;
  pnl: number;
}

export function computeDashboardDerivedMetrics(input: DashboardMetricsInput): DashboardDerivedMetrics {
  return {
    winLossRatio: computeWinLossRatio(input.summary.avg_win, input.summary.avg_loss),
    winDurationOverLossDuration: computeWinDurationRatio(
      input.summary.avg_win_duration_minutes,
      input.summary.avg_loss_duration_minutes,
    ),
    direction: computeDirectionMetrics(input),
    stability: computeStabilityMetrics(input.dailyPnlDays, input.summary.net_pnl),
  };
}

function computeWinLossRatio(avgWin: number, avgLoss: number): MetricValue {
  const lossMagnitude = Math.abs(avgLoss);
  // Win/Loss Ratio = Average Win / abs(Average Loss).
  if (lossMagnitude <= EPSILON) {
    return missingMetric("Needs a non-zero average loss.");
  }
  return metric(avgWin / lossMagnitude);
}

function computeWinDurationRatio(avgWinMinutes: number, avgLossMinutes: number): MetricValue {
  // Win Duration / Loss Duration = Avg Win Duration / Avg Loss Duration.
  if (avgLossMinutes <= EPSILON) {
    return missingMetric("Needs a non-zero average loss duration.");
  }
  return metric(avgWinMinutes / avgLossMinutes);
}

function computeDirectionMetrics(input: DashboardMetricsInput): DirectionMetrics {
  if (input.directionDataIssue) {
    return missingDirectionMetrics(input.directionDataIssue);
  }

  if (input.summary.trade_count === 0) {
    return {
      longTrades: metric(0),
      shortTrades: metric(0),
      longPercent: missingMetric("Needs at least one directional trade."),
      longPnl: metric(0),
      shortPnl: metric(0),
      longWinRate: missingMetric("Needs at least one long trade."),
      shortWinRate: missingMetric("Needs at least one short trade."),
    };
  }

  if (!input.hasCompleteDirectionalHistory) {
    return missingDirectionMetrics("Needs complete closed-trade history for this range.");
  }

  const directionalSamples: DirectionSample[] = input.trades.flatMap((trade) => {
    const direction = inferDirectionFromCloseSide(trade.side);
    if (direction === null || trade.pnl === null) {
      return [];
    }
    return [{ direction, pnl: trade.pnl }];
  });

  if (directionalSamples.length === 0) {
    return missingDirectionMetrics("Needs closed trades with BUY/SELL side values.");
  }

  const longSamples = directionalSamples.filter((sample) => sample.direction === "LONG");
  const shortSamples = directionalSamples.filter((sample) => sample.direction === "SHORT");

  const longTrades = longSamples.length;
  const shortTrades = shortSamples.length;
  const totalDirectionalTrades = longTrades + shortTrades;

  // Long % = Long Trades / Total Directional Trades.
  const longPercent = totalDirectionalTrades > 0 ? (longTrades / totalDirectionalTrades) * 100 : null;
  // Long/Short PnL = Sum of net PnL for trades in that direction.
  const longPnl = sum(longSamples.map((sample) => sample.pnl));
  const shortPnl = sum(shortSamples.map((sample) => sample.pnl));

  const longWins = longSamples.filter((sample) => sample.pnl > 0).length;
  const shortWins = shortSamples.filter((sample) => sample.pnl > 0).length;

  // Optional directional win rates: wins / trades per direction.
  const longWinRate =
    longTrades > 0 ? metric((longWins / longTrades) * 100) : missingMetric("Needs at least one long trade.");
  const shortWinRate =
    shortTrades > 0 ? metric((shortWins / shortTrades) * 100) : missingMetric("Needs at least one short trade.");

  return {
    longTrades: metric(longTrades),
    shortTrades: metric(shortTrades),
    longPercent: longPercent === null ? missingMetric("Needs directional trades.") : metric(longPercent),
    longPnl: metric(longPnl),
    shortPnl: metric(shortPnl),
    longWinRate,
    shortWinRate,
  };
}

function computeStabilityMetrics(dailyPnlDays: DashboardMetricsInput["dailyPnlDays"], netPnl: number): StabilityMetrics {
  // Day-level stability metrics require a daily net PnL series that is already grouped
  // by trading day in the account timezone before it reaches this function.
  const dailyNetValues = dailyPnlDays
    .map((day) => day.net_pnl)
    .filter((value) => Number.isFinite(value));

  if (dailyNetValues.length === 0) {
    const reason = "Needs daily net PnL values grouped by trading day.";
    return {
      bestDay: missingMetric(reason),
      worstDay: missingMetric(reason),
      dailyPnlVolatility: missingMetric(reason),
      bestDayPercentOfNet: missingMetric(reason),
      worstDayPercentOfNet: missingMetric(reason),
    };
  }

  const bestDay = Math.max(...dailyNetValues);
  const worstDay = Math.min(...dailyNetValues);
  // Daily PnL volatility ($): population standard deviation of daily net PnL.
  const dailyPnlVolatility = populationStandardDeviation(dailyNetValues);

  if (Math.abs(netPnl) <= EPSILON) {
    const denominatorReason = "Needs non-zero net PnL for percentage ratios.";
    return {
      bestDay: metric(bestDay),
      worstDay: metric(worstDay),
      dailyPnlVolatility: metric(dailyPnlVolatility),
      bestDayPercentOfNet: missingMetric(denominatorReason),
      worstDayPercentOfNet: missingMetric(denominatorReason),
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
  };
}

function inferDirectionFromCloseSide(side: string): InferredDirection | null {
  const normalized = side.trim().toUpperCase();
  // Closed rows use execution side; SELL generally closes longs, BUY closes shorts.
  if (normalized === "SELL") {
    return "LONG";
  }
  if (normalized === "BUY") {
    return "SHORT";
  }
  return null;
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

function missingDirectionMetrics(reason: string): DirectionMetrics {
  return {
    longTrades: missingMetric(reason),
    shortTrades: missingMetric(reason),
    longPercent: missingMetric(reason),
    longPnl: missingMetric(reason),
    shortPnl: missingMetric(reason),
    longWinRate: missingMetric(reason),
    shortWinRate: missingMetric(reason),
  };
}
