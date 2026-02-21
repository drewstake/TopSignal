import type { AccountPnlCalendarDay, AccountSummary, AccountTrade } from "../../../lib/types";

export interface MetricValue {
  value: number | null;
  missingReason?: string;
}

export interface DirectionMetrics {
  longTrades: MetricValue;
  shortTrades: MetricValue;
  longPercent: MetricValue;
  longPnl: MetricValue;
  shortPnl: MetricValue;
  longWinRate: MetricValue;
  shortWinRate: MetricValue;
  longExpectancy: MetricValue;
  shortExpectancy: MetricValue;
  longProfitFactor: MetricValue;
  shortProfitFactor: MetricValue;
  longAvgWin: MetricValue;
  longAvgLoss: MetricValue;
  shortAvgWin: MetricValue;
  shortAvgLoss: MetricValue;
  longLargeLossRate: MetricValue;
  shortLargeLossRate: MetricValue;
  longPnlShare: MetricValue;
  shortPnlShare: MetricValue;
  insight: string;
}

export interface StabilityMetrics {
  bestDay: MetricValue;
  worstDay: MetricValue;
  dailyPnlVolatility: MetricValue;
  bestDayPercentOfNet: MetricValue;
  worstDayPercentOfNet: MetricValue;
  medianDayPnl: MetricValue;
  avgGreenDay: MetricValue;
  avgRedDay: MetricValue;
  redDayPercent: MetricValue;
  nukeRatio: MetricValue;
  greenRedDaySizeRatio: MetricValue;
  insight: string;
}

export interface PayoffMetrics {
  averageWin: MetricValue;
  averageLoss: MetricValue;
  breakevenWinRate: MetricValue;
  currentWinRate: MetricValue;
  wrCushion: MetricValue;
  largeLossThreshold: MetricValue;
  largeLossRate: MetricValue;
  p95Loss: MetricValue;
  capture: MetricValue;
  containment: MetricValue;
  insight: string;
}

export interface DashboardDerivedMetrics {
  winLossRatio: MetricValue;
  winDurationOverLossDuration: MetricValue;
  direction: DirectionMetrics;
  stability: StabilityMetrics;
  payoff: PayoffMetrics;
}

export interface DashboardMetricsInput {
  summary: AccountSummary;
  trades: AccountTrade[];
  dailyPnlDays: AccountPnlCalendarDay[];
  hasCompleteDirectionalHistory: boolean;
  directionDataIssue?: string | null;
}
