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
}

export interface StabilityMetrics {
  bestDay: MetricValue;
  worstDay: MetricValue;
  dailyPnlVolatility: MetricValue;
  bestDayPercentOfNet: MetricValue;
  worstDayPercentOfNet: MetricValue;
}

export interface DashboardDerivedMetrics {
  winLossRatio: MetricValue;
  winDurationOverLossDuration: MetricValue;
  direction: DirectionMetrics;
  stability: StabilityMetrics;
}

export interface DashboardMetricsInput {
  summary: AccountSummary;
  trades: AccountTrade[];
  dailyPnlDays: AccountPnlCalendarDay[];
  hasCompleteDirectionalHistory: boolean;
  directionDataIssue?: string | null;
}
