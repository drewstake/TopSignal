/* eslint-disable react-refresh/only-export-components */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { ActivityMetrics } from "../../utils/activityMetrics";
import type { SustainabilityResult } from "../../utils/sustainability";
import type { AccountPnlCalendarDay, AccountSummary } from "../../lib/types";
import { Button } from "../ui/Button";
import { cn } from "../ui/cn";
import type { MetricValue } from "../../pages/dashboard/metrics/types";

const NO_DATA_TEXT = "No trades in this range.";
const COPY_FEEDBACK_MS = 1200;
const EPSILON = 1e-9;

const titleDateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});

const calendarDateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

const generatedAtFormatter = new Intl.DateTimeFormat("en-US", {
  month: "long",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
  timeZone: "America/New_York",
});

const integerFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 0,
});

const moneyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const decimalFormatterByDigits = new Map<number, Intl.NumberFormat>();

const POINT_BASES = ["MNQ", "MES", "MGC", "SIL"] as const;
const DISPLAY_POINT_BASES = ["MNQ", "MES"] as const;

type PointBasis = (typeof POINT_BASES)[number];

export interface PointPayoffStats {
  avgPointGain: number | null;
  avgPointLoss: number | null;
}

export type PointPayoffByBasis = Record<PointBasis, PointPayoffStats>;

interface CopyFullStatsPerformanceMetrics {
  netPnl: MetricValue;
  profitPerDay: MetricValue;
  efficiencyPerHour: MetricValue;
  expectancyPerTrade: MetricValue;
}

interface CopyFullStatsConsistencyMetrics {
  dailyPnlVolatility: MetricValue;
  bestDay: MetricValue;
  worstDay: MetricValue;
  bestDayPercentOfNet: MetricValue;
  worstDayPercentOfNet: MetricValue;
  medianDayPnl: MetricValue;
  avgGreenDay: MetricValue;
  avgRedDay: MetricValue;
  redDayPercent: MetricValue;
  worstDayImpact: MetricValue;
  greenRedDaySizeRatio: MetricValue;
  stabilityScore: MetricValue;
  insight: string;
}

interface CopyFullStatsRiskMetrics {
  maxDrawdown: MetricValue;
  drawdownPercentOfNet: MetricValue;
  drawdownPercentOfEquityBase: MetricValue;
  equityBase: {
    value: number | null;
    label: string;
    detail: string;
  };
  averageDrawdown: MetricValue;
  maxDrawdownLengthHours: number | null;
  recoveryTimeHours: number | null;
}

interface CopyFullStatsDirectionMetrics {
  longPercent: MetricValue;
  shortPercent: MetricValue;
  longTrades: MetricValue;
  shortTrades: MetricValue;
  longPnl: MetricValue;
  shortPnl: MetricValue;
  longPnlShare: MetricValue;
  shortPnlShare: MetricValue;
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
  insight: string;
}

interface CopyFullStatsPayoffMetrics {
  winLossRatio: MetricValue;
  averageWin: MetricValue;
  averageLoss: MetricValue;
  breakevenWinRate: MetricValue;
  currentWinRate: MetricValue;
  wrCushion: MetricValue;
  largeLossThreshold: MetricValue;
  largeLossRate: MetricValue;
  p95Loss: MetricValue;
  capture: MetricValue;
  pointPayoffByBasis: PointPayoffByBasis;
  insight: string;
}

interface CopyFullStatsHoldTimeMetrics {
  ratio: MetricValue;
  averageWinDurationMinutes: number | null;
  averageLossDurationMinutes: number | null;
}

interface CopyFullStatsBalanceMetrics {
  currentBalance: number | null;
}

export interface CopyFullStatsMetrics {
  summary: AccountSummary;
  performance: CopyFullStatsPerformanceMetrics;
  consistency: CopyFullStatsConsistencyMetrics;
  risk: CopyFullStatsRiskMetrics;
  direction: CopyFullStatsDirectionMetrics;
  payoff: CopyFullStatsPayoffMetrics;
  activity: ActivityMetrics;
  sustainability: SustainabilityResult;
  holdTime: CopyFullStatsHoldTimeMetrics;
  balance: CopyFullStatsBalanceMetrics;
}

interface BuildFullStatsTextInput {
  metrics: CopyFullStatsMetrics;
  rangeLabel: string;
  calendarDays?: AccountPnlCalendarDay[];
  generatedAt?: Date;
}

type SummaryTone = "positive" | "negative" | "neutral";

interface StatsCoachSummarySection {
  title: string;
  items: string[];
}

interface StatsCoachSummaryLever {
  issue: string;
  metric: string;
  action: string;
  tone: SummaryTone;
}

interface StatsCoachSummaryConfidence {
  label: string;
  detail: string;
  tone: SummaryTone;
}

export interface StatsCoachSummary {
  verdict: string;
  confidence: StatsCoachSummaryConfidence;
  keyStats: Array<{
    label: string;
    value: string;
    tone: SummaryTone;
  }>;
  topLevers: StatsCoachSummaryLever[];
  sections: StatsCoachSummarySection[];
}

export interface CopyFullStatsButtonProps {
  metrics: CopyFullStatsMetrics;
  rangeLabel: string;
  calendarDays?: AccountPnlCalendarDay[];
  disabled?: boolean;
  className?: string;
}

interface BalanceSeriesPoint {
  date: string;
  netPnl: number;
  balance: number;
  startingBalance: number;
}

interface BalanceSummary {
  startBalance: number | null;
  endingBalance: number | null;
  highBalance: number | null;
  lowBalance: number | null;
  largestDay: number | null;
}

interface SampleQuality {
  label: "High confidence sample" | "Medium confidence sample" | "Low confidence sample" | "No meaningful sample yet";
  tone: SummaryTone;
  detail: string;
  isLow: boolean;
  isNoMeaningful: boolean;
}

interface SummaryLever {
  issue: string;
  metric: string;
  action: string;
  priority: number;
}

interface DirectionConcentration {
  text: string;
  weakerSide: "long" | "short";
  otherSideHasTrades: boolean;
  otherSideHasExpectancy: boolean;
}

interface DirectionComparison {
  stronger: "Long" | "Short";
  weaker: "Long" | "Short";
  strongerExpectancy: number;
  weakerExpectancy: number;
}

function getDecimalFormatter(digits: number) {
  const existing = decimalFormatterByDigits.get(digits);
  if (existing) {
    return existing;
  }

  const formatter = new Intl.NumberFormat("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  decimalFormatterByDigits.set(digits, formatter);
  return formatter;
}

function formatInteger(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "N/A";
  }
  return integerFormatter.format(Math.round(value));
}

function formatDecimal(value: number | null | undefined, digits: number) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "N/A";
  }
  return getDecimalFormatter(digits).format(value);
}

function formatMetric(metric: MetricValue, formatter: (value: number) => string) {
  if (metric.value === null || !Number.isFinite(metric.value)) {
    return "N/A";
  }
  return formatter(metric.value);
}

function formatMoney(
  value: number | null | undefined,
  options: {
    showPositiveSign?: boolean;
    forceNegative?: boolean;
  } = {},
) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "N/A";
  }

  if (options.forceNegative) {
    if (Math.abs(value) <= EPSILON) {
      return moneyFormatter.format(0);
    }
    return `-${moneyFormatter.format(Math.abs(value))}`;
  }

  const prefix = options.showPositiveSign === false || value <= 0 ? "" : "+";
  return `${prefix}${moneyFormatter.format(value)}`;
}

function formatMetricMoney(
  metric: MetricValue,
  options: {
    showPositiveSign?: boolean;
    forceNegative?: boolean;
  } = {},
) {
  return formatMoney(metric.value, options);
}

function formatPercent(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "N/A";
  }
  return `${formatDecimal(value, 1)}%`;
}

function formatMetricPercent(metric: MetricValue) {
  return formatPercent(metric.value);
}

function formatRatio(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "N/A";
  }
  return `${formatDecimal(value, 2)}x`;
}

function formatMetricRatio(metric: MetricValue) {
  return formatRatio(metric.value);
}

function formatPoints(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "N/A";
  }
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${formatDecimal(value, 1)} pts`;
}

function formatMetricPoints(metric: MetricValue) {
  return formatPoints(metric.value);
}

function formatDurationFromMinutes(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "N/A";
  }

  const safeMinutes = Math.max(0, value);
  const totalSeconds = Math.round(safeMinutes * 60);
  const totalMinutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const hours = Math.floor(totalMinutes / 60);
  const minutesRemainder = totalMinutes % 60;

  if (hours > 0) {
    return `${hours}h ${minutesRemainder}m`;
  }

  return `${minutesRemainder}m ${seconds}s`;
}

function formatHours(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "N/A";
  }
  return `${formatDecimal(value, 1)} h`;
}

function formatPointValue(value: number | null, basis: PointBasis) {
  if (value === null || !Number.isFinite(value)) {
    return "N/A";
  }

  const digits = basis === "SIL" ? 3 : 2;
  return `${formatDecimal(Math.abs(value), digits)} pts`;
}

function formatCalendarDate(value: string) {
  return calendarDateFormatter.format(new Date(`${value}T00:00:00.000Z`));
}

function formatGeneratedAt(value: Date) {
  return `${generatedAtFormatter.format(value).replace(" at ", ", ")} ET`;
}

function buildLine(label: string, value: string) {
  return `- ${label}: ${value}`;
}

function hasFiniteValue(metric: MetricValue) {
  return metric.value !== null && Number.isFinite(metric.value);
}

function getMetricValue(metric: MetricValue) {
  return hasFiniteValue(metric) ? metric.value : null;
}

function isFiniteNumber(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && Number.isFinite(value);
}

function tradeNoun(count: number) {
  return count === 1 ? "trade" : "trades";
}

function dayNoun(count: number) {
  return count === 1 ? "active day" : "active days";
}

function basisDayNoun() {
  return "day";
}

function formatSampleLine(metrics: CopyFullStatsMetrics) {
  const tradeCount = metrics.summary.trade_count;
  const activeDays = metrics.summary.active_days;
  return `${formatInteger(tradeCount)} ${tradeNoun(tradeCount)} across ${formatInteger(activeDays)} ${dayNoun(activeDays)}`;
}

function formatTinySampleWarning(metrics: CopyFullStatsMetrics) {
  const tradeCount = metrics.summary.trade_count;
  const activeDays = metrics.summary.active_days;
  if (tradeCount >= 30 && activeDays >= 5) {
    return null;
  }

  return `Only ${formatSampleLine(
    metrics,
  )}. Win rate, profit factor, sustainability, and loss percentile metrics may be unstable.`;
}

function formatProjectedWeeklyValue(value: number | null | undefined, metrics: CopyFullStatsMetrics) {
  const formattedValue = formatDecimal(value, 1);
  if (formattedValue === "N/A") {
    return "N/A - selected range is too short for a weekly pace";
  }

  const rangeDays = metrics.activity.rangeDays;
  if (!isFiniteNumber(rangeDays) || rangeDays <= 0) {
    return `${formattedValue}, based on selected-range pace`;
  }

  return `${formattedValue}, based on ${formatInteger(rangeDays)}-${basisDayNoun()} pace`;
}

function formatTradesPerActiveHour(metrics: CopyFullStatsMetrics) {
  const formattedValue = formatDecimal(metrics.activity.tradesPerActiveHour, 2);
  return formattedValue === "N/A" ? "N/A - active trading time not available" : formattedValue;
}

function formatP95Loss(metrics: CopyFullStatsMetrics) {
  const value = metrics.payoff.p95Loss.value;
  if (value === null || !Number.isFinite(value)) {
    return `N/A - requires at least 20 losses; current losses: ${formatInteger(metrics.summary.loss_count)}`;
  }

  return formatMoney(value, { forceNegative: value < 0 });
}

function formatCapture(metrics: CopyFullStatsMetrics) {
  const value = metrics.payoff.capture.value;
  if (value === null || !Number.isFinite(value)) {
    return "N/A - capture data not available";
  }

  return formatPercent(value * 100);
}

function formatDirectionInsight(metrics: CopyFullStatsMetrics) {
  const insight = metrics.direction.insight.trim();
  if (insight) {
    return insight;
  }

  const longTrades = getMetricValue(metrics.direction.longTrades);
  const shortTrades = getMetricValue(metrics.direction.shortTrades);
  if (longTrades === 0 && shortTrades !== null && shortTrades > 0) {
    return "Only short trades taken; no long/short comparison available.";
  }
  if (shortTrades === 0 && longTrades !== null && longTrades > 0) {
    return "Only long trades taken; no long/short comparison available.";
  }

  return "N/A - needs long and short expectancy data";
}

function formatUnavailableDirectionMetric(label: string, trades: number | null) {
  if (trades === 0) {
    return `N/A - no ${label} trades in range`;
  }
  return `N/A - ${label} data not available`;
}

function formatDirectionMetric(metric: MetricValue, formatter: (value: number) => string, label: string, trades: number | null) {
  return hasFiniteValue(metric) ? formatter(metric.value ?? 0) : formatUnavailableDirectionMetric(label, trades);
}

function formatDirectionMetricPercent(metric: MetricValue, label: string, trades: number | null) {
  return formatDirectionMetric(metric, formatPercent, label, trades);
}

function formatDirectionMetricMoney(metric: MetricValue, label: string, trades: number | null) {
  return formatDirectionMetric(metric, formatMoney, label, trades);
}

function formatDirectionMetricRatio(metric: MetricValue, label: string, trades: number | null) {
  return formatDirectionMetric(metric, formatRatio, label, trades);
}

function getWorstDayLabel(metric: MetricValue) {
  const value = getMetricValue(metric);
  return value !== null && value < 0 ? "Worst Day" : "Lowest Day";
}

function getAverageWinValue(metrics: CopyFullStatsMetrics) {
  const metricValue = getMetricValue(metrics.payoff.averageWin);
  if (metricValue !== null) {
    return Math.abs(metricValue);
  }

  return isFiniteNumber(metrics.summary.avg_win) ? Math.abs(metrics.summary.avg_win) : null;
}

function getAverageLossValue(metrics: CopyFullStatsMetrics) {
  const metricValue = getMetricValue(metrics.payoff.averageLoss);
  if (metricValue !== null) {
    return Math.abs(metricValue);
  }

  return isFiniteNumber(metrics.summary.avg_loss) ? Math.abs(metrics.summary.avg_loss) : null;
}

function getMissingKeyMetricNames(metrics: CopyFullStatsMetrics) {
  const keyMetrics = [
    { name: "net PnL", value: metrics.performance.netPnl.value },
    { name: "expectancy", value: metrics.performance.expectancyPerTrade.value },
    { name: "drawdown % of net", value: metrics.risk.drawdownPercentOfNet.value },
    { name: "drawdown % of risk base", value: metrics.risk.drawdownPercentOfEquityBase.value },
    { name: "average win", value: metrics.payoff.averageWin.value },
    { name: "average loss", value: metrics.payoff.averageLoss.value },
    { name: "long expectancy", value: metrics.direction.longExpectancy.value },
    { name: "short expectancy", value: metrics.direction.shortExpectancy.value },
    { name: "stability", value: metrics.consistency.stabilityScore.value },
  ];

  return keyMetrics.filter((metric) => !isFiniteNumber(metric.value)).map((metric) => metric.name);
}

function formatMissingMetricDetail(missingNames: string[]) {
  const countText = `${formatInteger(missingNames.length)} missing key metric${missingNames.length === 1 ? "" : "s"}`;
  if (missingNames.length === 0) {
    return countText;
  }
  return `${countText}: ${missingNames.join(", ")}`;
}

function buildSampleQuality(metrics: CopyFullStatsMetrics): SampleQuality {
  const tradeCount = metrics.summary.trade_count;
  const activeDays = metrics.summary.active_days;
  const missingKeyMetrics = getMissingKeyMetricNames(metrics);
  const hasNoMeaningfulSample = tradeCount < 5 || activeDays < 2;
  const isSmallTradeSample = tradeCount < 30;
  const isThinDaySample = activeDays < 5;
  const hasManyMissingMetrics = missingKeyMetrics.length >= 3;

  const label =
    hasNoMeaningfulSample
      ? "No meaningful sample yet"
      : isSmallTradeSample || isThinDaySample || hasManyMissingMetrics
        ? "Low confidence sample"
        : tradeCount < 50 || activeDays < 10 || missingKeyMetrics.length > 0
          ? "Medium confidence sample"
          : "High confidence sample";

  return {
    label,
    tone: label === "High confidence sample" ? "positive" : label === "Medium confidence sample" ? "neutral" : "negative",
    detail: `${label} - ${formatInteger(tradeCount)} ${tradeNoun(tradeCount)} / ${formatInteger(activeDays)} ${dayNoun(activeDays)} / ${formatMissingMetricDetail(
      missingKeyMetrics,
    )}`,
    isLow: label === "Low confidence sample" || label === "No meaningful sample yet",
    isNoMeaningful: label === "No meaningful sample yet",
  };
}

function classifyInsightTone(insight: string) {
  const text = insight.toLowerCase();
  const negativePatterns = [
    "underperform",
    "outperform",
    "concentrat",
    "imbalance",
    "imbalanced",
    "skew",
    "weak",
    "weaker",
    "lag",
    "loss",
    "drawdown",
    "risk",
    "fragile",
    "unstable",
    "volatile",
  ];
  const positivePatterns = ["stable", "balanced", "strong", "controlled", "positive", "consistent", "healthy", "improving"];

  if (negativePatterns.some((pattern) => text.includes(pattern))) {
    return "negative";
  }
  if (positivePatterns.some((pattern) => text.includes(pattern))) {
    return "positive";
  }
  return "neutral";
}

function addInsightByTone(insight: string, strengths: string[], problems: string[]) {
  const trimmed = insight.trim();
  if (!trimmed) {
    return;
  }

  if (classifyInsightTone(trimmed) === "negative") {
    problems.push(trimmed);
    return;
  }

  strengths.push(trimmed);
}

function isMateriallyStronger(stronger: number, weaker: number) {
  if (!Number.isFinite(stronger) || !Number.isFinite(weaker) || stronger <= 0 || stronger <= weaker) {
    return false;
  }

  const gap = stronger - weaker;
  if (weaker <= 0) {
    return gap >= 25;
  }

  return stronger >= weaker * 1.5 && gap >= 25;
}

function getDirectionComparison(metrics: CopyFullStatsMetrics): DirectionComparison | null {
  const longExpectancy = getMetricValue(metrics.direction.longExpectancy);
  const shortExpectancy = getMetricValue(metrics.direction.shortExpectancy);
  if (longExpectancy === null || shortExpectancy === null) {
    return null;
  }

  if (isMateriallyStronger(longExpectancy, shortExpectancy)) {
    return {
      stronger: "Long",
      weaker: "Short",
      strongerExpectancy: longExpectancy,
      weakerExpectancy: shortExpectancy,
    };
  }

  if (isMateriallyStronger(shortExpectancy, longExpectancy)) {
    return {
      stronger: "Short",
      weaker: "Long",
      strongerExpectancy: shortExpectancy,
      weakerExpectancy: longExpectancy,
    };
  }

  return null;
}

function getDirectionConcentration(metrics: CopyFullStatsMetrics): DirectionConcentration | null {
  const longPnlShare = getMetricValue(metrics.direction.longPnlShare);
  const shortPnlShare = getMetricValue(metrics.direction.shortPnlShare);
  if (longPnlShare === null || shortPnlShare === null || Math.abs(shortPnlShare - longPnlShare) < 50) {
    return null;
  }

  const concentratedSide = shortPnlShare > longPnlShare ? "shorts" : "longs";
  const concentratedShare = shortPnlShare > longPnlShare ? shortPnlShare : longPnlShare;
  const otherShare = shortPnlShare > longPnlShare ? longPnlShare : shortPnlShare;
  const weakerSide = shortPnlShare > longPnlShare ? "long" : "short";
  const otherTrades = getMetricValue(shortPnlShare > longPnlShare ? metrics.direction.longTrades : metrics.direction.shortTrades);
  const otherExpectancy = getMetricValue(shortPnlShare > longPnlShare ? metrics.direction.longExpectancy : metrics.direction.shortExpectancy);
  return {
    text: `${concentratedSide} are ${formatPercent(concentratedShare)} of PnL versus ${formatPercent(otherShare)} on the other side`,
    weakerSide,
    otherSideHasTrades: otherTrades !== null && otherTrades > 0,
    otherSideHasExpectancy: otherExpectancy !== null,
  };
}

function getDirectionConcentrationText(metrics: CopyFullStatsMetrics) {
  return getDirectionConcentration(metrics)?.text ?? null;
}

function getHighFrequencyMetric(metrics: CopyFullStatsMetrics) {
  const tradesPerWeek = metrics.activity.tradesPerWeek;
  if (isFiniteNumber(tradesPerWeek) && tradesPerWeek >= 35) {
    return `current pace projects to ${formatDecimal(tradesPerWeek, 1)} trades/week based on ${formatInteger(
      metrics.summary.trade_count,
    )} trades over ${formatInteger(metrics.summary.active_days)} ${dayNoun(metrics.summary.active_days)}`;
  }

  const avgTradesPerDay = metrics.summary.avg_trades_per_day;
  if (isFiniteNumber(avgTradesPerDay) && avgTradesPerDay >= 6) {
    return `${formatDecimal(avgTradesPerDay, 1)} trades/day`;
  }

  return null;
}

function getDirectionConcentrationAction(concentration: DirectionConcentration) {
  if (!concentration.otherSideHasTrades) {
    return `do not scale ${concentration.weakerSide}s until that side has at least 10 logged trades with positive expectancy.`;
  }
  if (!concentration.otherSideHasExpectancy) {
    return `keep ${concentration.weakerSide}s at 50% size until expectancy is available and positive across at least 10 trades.`;
  }
  return `keep ${concentration.weakerSide}s at 50% size until expectancy is positive across at least 10 trades.`;
}

function getRiskBaseLabel(metrics: CopyFullStatsMetrics) {
  const label = metrics.risk.equityBase.label.trim();
  return label ? label.toLowerCase() : "defined risk base";
}

function getRiskBaseDescription(metrics: CopyFullStatsMetrics) {
  const label = metrics.risk.equityBase.label.trim();
  const detail = metrics.risk.equityBase.detail.trim();
  if (label && detail) {
    return `${label}: ${detail}`;
  }
  return label || detail || "Risk base is the account or risk budget used for drawdown percentages.";
}

function getSustainabilityDrivers(metrics: CopyFullStatsMetrics, sampleQuality: SampleQuality) {
  const drivers: string[] = [];
  const drawdownPercentOfRiskBase = getMetricValue(metrics.risk.drawdownPercentOfEquityBase);
  const stabilityScore = getMetricValue(metrics.consistency.stabilityScore);
  const directionConcentration = getDirectionConcentrationText(metrics);
  const highFrequencyMetric = getHighFrequencyMetric(metrics);

  if (sampleQuality.isLow) {
    drivers.push("small sample");
  }
  if (drawdownPercentOfRiskBase !== null && drawdownPercentOfRiskBase >= 3) {
    drivers.push(`drawdown at ${formatPercent(drawdownPercentOfRiskBase)} of risk base`);
  }
  if (stabilityScore !== null && stabilityScore < 80) {
    drivers.push(`daily stability at ${formatPercent(stabilityScore)}`);
  }
  if (directionConcentration !== null) {
    drivers.push("directional PnL concentration");
  }
  if (highFrequencyMetric !== null) {
    drivers.push("high projected trade frequency");
  }

  return drivers;
}

function formatLever(lever: SummaryLever) {
  return `${lever.issue} | Metric: ${lever.metric} | Next action: ${lever.action}`;
}

function getLeverTone(lever: SummaryLever): SummaryTone {
  if (lever.priority >= 70) {
    return "negative";
  }
  if (lever.issue === "Protect the edge") {
    return "positive";
  }
  return "neutral";
}

function getUniqueLevers(levers: SummaryLever[]) {
  const seen = new Set<string>();
  return levers.filter((lever) => {
    if (seen.has(lever.issue)) {
      return false;
    }
    seen.add(lever.issue);
    return true;
  });
}

function buildLeverCandidates(metrics: CopyFullStatsMetrics, sampleQuality: SampleQuality): SummaryLever[] {
  const levers: SummaryLever[] = [];
  const netPnl = getMetricValue(metrics.performance.netPnl);
  const expectancy = getMetricValue(metrics.performance.expectancyPerTrade);
  const profitFactor = metrics.summary.profit_factor;
  const riskScore = metrics.sustainability.riskScore;
  const sustainabilityScore = metrics.sustainability.score;
  const drawdownPercentOfNet = getMetricValue(metrics.risk.drawdownPercentOfNet);
  const drawdownPercentOfRiskBase = getMetricValue(metrics.risk.drawdownPercentOfEquityBase);
  const stabilityScore = getMetricValue(metrics.consistency.stabilityScore);
  const averageWin = getAverageWinValue(metrics);
  const averageLoss = getAverageLossValue(metrics);
  const directionComparison = getDirectionComparison(metrics);
  const directionConcentration = getDirectionConcentration(metrics);
  const highFrequencyMetric = getHighFrequencyMetric(metrics);

  if (sampleQuality.isLow) {
    levers.push({
      issue: "Sample quality",
      metric: sampleQuality.detail,
      action: "wait for at least 30 trades and 5 active days before changing size.",
      priority: sampleQuality.isNoMeaningful ? 100 : 60,
    });
  }

  if ((netPnl !== null && netPnl <= 0) || (expectancy !== null && expectancy <= 0) || (Number.isFinite(profitFactor) && profitFactor < 1)) {
    levers.push({
      issue: "Negative edge",
      metric: `net ${formatMoney(netPnl)} and expectancy ${formatMoney(expectancy)} / trade`,
      action: "cut size and trade only documented A setups until expectancy turns positive.",
      priority: 95,
    });
  }

  if (averageWin !== null && averageLoss !== null && averageLoss > averageWin) {
    const lossCap = averageWin * 0.9;
    levers.push({
      issue: "Loss sizing",
      metric: `avg loss ${formatMoney(-averageLoss)} vs avg win ${formatMoney(averageWin)}`,
      action: `cap planned loss at ${formatMoney(lossCap, { showPositiveSign: false })} or less.`,
      priority: 90,
    });
  }

  if ((drawdownPercentOfNet !== null && drawdownPercentOfNet >= 25) || (drawdownPercentOfRiskBase !== null && drawdownPercentOfRiskBase >= 3)) {
    const dailyPause = averageWin !== null ? averageWin * 2 : null;
    levers.push({
      issue: "Drawdown pressure",
      metric: `max DD ${formatPercent(drawdownPercentOfNet)} of net / ${formatPercent(drawdownPercentOfRiskBase)} of risk base (${getRiskBaseLabel(
        metrics,
      )})`,
      action:
        dailyPause !== null
          ? `stop trading for the day once realized PnL is ${formatMoney(dailyPause, { forceNegative: true })} from the day's starting balance.`
          : "reduce size until max drawdown is back under 3.0% of risk base.",
      priority: 80,
    });
  }

  if (directionComparison !== null) {
    const weaker = directionComparison.weaker.toLowerCase();
    const stronger = directionComparison.stronger.toLowerCase();
    levers.push({
      issue: "Direction imbalance",
      metric: `${weaker} expectancy ${formatMoney(directionComparison.weakerExpectancy)} vs ${stronger} ${formatMoney(directionComparison.strongerExpectancy)}`,
      action: `reduce ${weaker} size or require stricter confirmation until ${weaker} expectancy closes the gap.`,
      priority: 75,
    });
  }

  if (directionConcentration !== null) {
    levers.push({
      issue: "PnL concentration",
      metric: directionConcentration.text,
      action: getDirectionConcentrationAction(directionConcentration),
      priority: 70,
    });
  }

  if (highFrequencyMetric !== null) {
    levers.push({
      issue: "Trade frequency",
      metric: highFrequencyMetric,
      action: "require at least 4 of 5 A-setup checklist items before entry: trend alignment, key level, volume confirmation, defined stop, and minimum 2R target.",
      priority: 65,
    });
  }

  if (stabilityScore !== null && stabilityScore < 80) {
    levers.push({
      issue: "Daily consistency",
      metric: `stability ${formatPercent(stabilityScore)}`,
      action: "reduce the size of outlier days before trying to increase total PnL.",
      priority: 55,
    });
  }

  if (netPnl !== null && netPnl > 0 && expectancy !== null && expectancy > 0 && Number.isFinite(profitFactor) && profitFactor >= 1.5) {
    levers.push({
      issue: "Protect the edge",
      metric: `${formatMoney(netPnl)} net, ${formatMoney(expectancy)} expectancy, ${formatRatio(profitFactor)} PF`,
      action: "keep the current setup rules intact and scale only after risk stays controlled.",
      priority: 20,
    });
  }

  levers.push({
    issue: "Risk gate",
    metric: `risk quality ${formatDecimal(riskScore, 1)}/100 (higher is better) and sustainability ${formatInteger(sustainabilityScore)}/100`,
    action: "increase size only when both stay at 70/100 or better.",
    priority: 10,
  });

  return getUniqueLevers(levers).sort((left, right) => right.priority - left.priority);
}

function buildTopLevers(levers: SummaryLever[], metrics: CopyFullStatsMetrics) {
  const selected = levers.slice(0, 3);
  const fallbackLevers: SummaryLever[] = [
    {
      issue: "Sample depth",
      metric: `${formatInteger(metrics.summary.trade_count)} trades across ${formatInteger(metrics.summary.active_days)} active days`,
      action: "keep logging the same playbook until the next review has a deeper sample.",
      priority: 0,
    },
    {
      issue: "Execution split",
      metric: `long expectancy ${formatMetricMoney(metrics.direction.longExpectancy)} / short expectancy ${formatMetricMoney(metrics.direction.shortExpectancy)}`,
      action: "review long and short trades separately before changing shared rules.",
      priority: 0,
    },
    {
      issue: "Payoff control",
      metric: `avg win ${formatMetricMoney(metrics.payoff.averageWin)} / avg loss ${formatMetricMoney(metrics.payoff.averageLoss)}`,
      action: "keep average loss below average win before adding size.",
      priority: 0,
    },
  ];

  for (const fallback of fallbackLevers) {
    if (selected.length >= 3) {
      break;
    }
    if (!selected.some((lever) => lever.issue === fallback.issue)) {
      selected.push(fallback);
    }
  }

  return selected;
}

function buildVerdict(metrics: CopyFullStatsMetrics, rangeLabel: string, sampleQuality: SampleQuality) {
  const safeRangeLabel = rangeLabel.trim() || "selected range";
  const netPnl = getMetricValue(metrics.performance.netPnl);
  const expectancy = getMetricValue(metrics.performance.expectancyPerTrade);
  const profitFactor = metrics.summary.profit_factor;
  const riskScore = metrics.sustainability.riskScore;
  const sustainabilityScore = metrics.sustainability.score;
  const drawdownPercentOfNet = getMetricValue(metrics.risk.drawdownPercentOfNet);
  const drawdownPercentOfRiskBase = getMetricValue(metrics.risk.drawdownPercentOfEquityBase);
  const stabilityScore = getMetricValue(metrics.consistency.stabilityScore);
  const averageWin = getAverageWinValue(metrics);
  const averageLoss = getAverageLossValue(metrics);
  const hasPayoffProblem = averageWin !== null && averageLoss !== null && averageLoss > averageWin;
  const hasDirectionProblem = getDirectionComparison(metrics) !== null || getDirectionConcentrationText(metrics) !== null;
  const hasHighFrequency = getHighFrequencyMetric(metrics) !== null;
  const isLosing =
    (netPnl !== null && netPnl < 0) || (expectancy !== null && expectancy < 0) || (Number.isFinite(profitFactor) && profitFactor < 1);
  const hasPositiveEdge = netPnl !== null && netPnl > 0 && expectancy !== null && expectancy > 0 && Number.isFinite(profitFactor) && profitFactor >= 1;
  const hasStrongRisk =
    riskScore >= 70 &&
    sustainabilityScore >= 70 &&
    (stabilityScore === null || stabilityScore >= 70) &&
    (drawdownPercentOfNet === null || drawdownPercentOfNet < 20) &&
    (drawdownPercentOfRiskBase === null || drawdownPercentOfRiskBase < 2);
  const isFragile =
    riskScore < 50 ||
    sustainabilityScore < 60 ||
    (drawdownPercentOfNet !== null && drawdownPercentOfNet >= 25) ||
    (drawdownPercentOfRiskBase !== null && drawdownPercentOfRiskBase >= 3) ||
    hasPayoffProblem ||
    hasDirectionProblem ||
    hasHighFrequency;

  if (sampleQuality.isLow) {
    return `Insufficient Sample: ${safeRangeLabel} has ${sampleQuality.detail.toLowerCase()}. Keep size fixed and collect cleaner data before judging edge or changing rules.`;
  }

  if (isLosing) {
    return `Defensive Mode: ${safeRangeLabel} needs capital protection first (${formatMoney(netPnl)} net, ${formatMoney(
      expectancy,
    )} expectancy, ${formatRatio(profitFactor)} PF). Reduce risk and rebuild around the cleanest setups before increasing size.`;
  }

  if (hasPositiveEdge && isFragile) {
    return `Profitable But Fragile: ${safeRangeLabel} is positive (${formatMoney(netPnl)} net, ${formatRatio(
      profitFactor,
    )} PF), but risk, payoff, direction, or frequency needs tightening before this range deserves more size.`;
  }

  if (hasPositiveEdge && profitFactor >= 1.5 && hasStrongRisk && sampleQuality.tone === "positive") {
    return `Scaling Candidate: ${safeRangeLabel} shows profitable edge with controlled risk (${formatMoney(netPnl)} net, ${formatRatio(
      profitFactor,
    )} PF, risk quality ${formatDecimal(riskScore, 1)}/100 where higher is better). Scale only in small steps while these risk metrics hold.`;
  }

  if (hasPositiveEdge) {
    return `Profitable But Fragile: ${safeRangeLabel} is profitable (${formatMoney(netPnl)} net, ${formatRatio(
      profitFactor,
    )} PF), but keep size steady until sample quality and risk both improve.`;
  }

  return `Insufficient Sample: ${safeRangeLabel} does not have enough complete edge data to produce a trading verdict.`;
}

function sortCalendarDays(days: AccountPnlCalendarDay[]) {
  return [...days].sort((left, right) => left.date.localeCompare(right.date));
}

function buildBalanceSeries(days: AccountPnlCalendarDay[], currentBalance: number | null): BalanceSeriesPoint[] {
  const orderedDays = sortCalendarDays(days);
  if (orderedDays.length === 0) {
    return [];
  }

  const totalNetPnl = orderedDays.reduce((sum, day) => sum + day.net_pnl, 0);
  const hasCurrentBalance = currentBalance !== null && Number.isFinite(currentBalance);
  const endingBalance = hasCurrentBalance ? currentBalance : totalNetPnl;
  const startingBalance = endingBalance - totalNetPnl;

  let runningNetPnl = 0;
  return orderedDays.map((day) => {
    runningNetPnl += day.net_pnl;
    return {
      date: day.date,
      netPnl: day.net_pnl,
      balance: startingBalance + runningNetPnl,
      startingBalance,
    };
  });
}

function buildBalanceSummary(days: AccountPnlCalendarDay[], currentBalance: number | null): BalanceSummary {
  const series = buildBalanceSeries(days, currentBalance);
  if (series.length === 0) {
    return {
      startBalance: null,
      endingBalance: null,
      highBalance: null,
      lowBalance: null,
      largestDay: null,
    };
  }

  const startBalance = series[0].startingBalance;
  let highBalance = startBalance;
  let lowBalance = startBalance;
  let largestDay = series[0].netPnl;

  for (const point of series) {
    highBalance = Math.max(highBalance, point.balance);
    lowBalance = Math.min(lowBalance, point.balance);
    if (Math.abs(point.netPnl) > Math.abs(largestDay)) {
      largestDay = point.netPnl;
    }
  }

  return {
    startBalance,
    endingBalance: series[series.length - 1].balance,
    highBalance,
    lowBalance,
    largestDay,
  };
}

async function fallbackCopyText(text: string) {
  if (typeof document === "undefined") {
    return false;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  textarea.style.pointerEvents = "none";
  textarea.style.top = "0";
  textarea.style.left = "-9999px";

  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();

  const copied = document.execCommand("copy");
  document.body.removeChild(textarea);
  return copied;
}

export async function copyTextToClipboard(text: string) {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      return fallbackCopyText(text);
    }
  }

  return fallbackCopyText(text);
}

export function buildFullStatsText({ metrics, rangeLabel, calendarDays = [], generatedAt = new Date() }: BuildFullStatsTextInput) {
  if (metrics.summary.trade_count <= 0) {
    return NO_DATA_TEXT;
  }

  const safeRangeLabel = rangeLabel.trim() || "selected range";
  const orderedCalendarDays = sortCalendarDays(calendarDays);
  const balanceSummary = buildBalanceSummary(calendarDays, metrics.balance.currentBalance);
  const summary = metrics.summary;
  const lowestDayLabel = getWorstDayLabel(metrics.consistency.worstDay);
  const longTrades = getMetricValue(metrics.direction.longTrades);
  const shortTrades = getMetricValue(metrics.direction.shortTrades);
  const tinySampleWarning = formatTinySampleWarning(metrics);

  const sections: string[] = [
    `TopSignal Full Stats (${safeRangeLabel})`,
    buildLine("Generated", formatGeneratedAt(generatedAt)),
    buildLine("Range", safeRangeLabel),
    buildLine("Sample", formatSampleLine(metrics)),
    buildLine("Basis", "Net PnL after fees unless marked Gross"),
    ...(tinySampleWarning === null ? [] : [buildLine("Sample Warning", tinySampleWarning)]),
    "",
    "PERFORMANCE",
    buildLine("Net PnL (after fees)", formatMetricMoney(metrics.performance.netPnl)),
    buildLine("Gross PnL", formatMoney(summary.gross_pnl)),
    buildLine("Fees", formatMoney(summary.fees, { showPositiveSign: false })),
    buildLine("Trades", formatInteger(summary.trade_count)),
    buildLine("Win Rate", formatPercent(summary.win_rate)),
    buildLine("Profit Factor", formatRatio(summary.profit_factor)),
    buildLine("Profit / Day", formatMetricMoney(metrics.performance.profitPerDay)),
    buildLine("Efficiency / Hour", formatMetricMoney(metrics.performance.efficiencyPerHour)),
    buildLine("Edge (Expectancy)", `${formatMetricMoney(metrics.performance.expectancyPerTrade)} per trade`),
    buildLine("Outcome Mix", `${formatInteger(summary.win_count)}W / ${formatInteger(summary.loss_count)}L / ${formatInteger(summary.breakeven_count)} BE`),
    "",
    "CONSISTENCY",
    buildLine("Swing (daily PnL volatility)", formatMetricMoney(metrics.consistency.dailyPnlVolatility, { showPositiveSign: false })),
    buildLine(
      "Best Day",
      `${formatMetricMoney(metrics.consistency.bestDay)} (${formatMetricPercent(metrics.consistency.bestDayPercentOfNet)})`,
    ),
    buildLine(
      lowestDayLabel,
      `${formatMetricMoney(metrics.consistency.worstDay)} (${formatMetricPercent(metrics.consistency.worstDayPercentOfNet)})`,
    ),
    buildLine("Median Day", formatMetricMoney(metrics.consistency.medianDayPnl)),
    buildLine("Avg Green", formatMetricMoney(metrics.consistency.avgGreenDay)),
    buildLine("Avg Red", formatMetricMoney(metrics.consistency.avgRedDay)),
    buildLine("Red Day %", formatMetricPercent(metrics.consistency.redDayPercent)),
    buildLine(
      `${lowestDayLabel} Impact`,
      metrics.consistency.worstDayImpact.value === null
        ? "N/A"
        : `${lowestDayLabel} = ${formatDecimal(metrics.consistency.worstDayImpact.value, 1)} days of avg profit`,
    ),
    buildLine("G/R Size Ratio", formatMetricRatio(metrics.consistency.greenRedDaySizeRatio)),
    buildLine("Stability", formatMetricPercent(metrics.consistency.stabilityScore)),
    "",
    "RISK",
    buildLine("Max Drawdown", formatMetricMoney(metrics.risk.maxDrawdown, { forceNegative: true })),
    buildLine("DD % of Net PnL", formatMetricPercent(metrics.risk.drawdownPercentOfNet)),
    buildLine("Max DD % of Risk Base", formatMetricPercent(metrics.risk.drawdownPercentOfEquityBase)),
    buildLine("Risk Base", formatMoney(metrics.risk.equityBase.value, { showPositiveSign: false })),
    buildLine("Risk Base Basis", metrics.risk.equityBase.label || "N/A"),
    buildLine("Risk Base Definition", getRiskBaseDescription(metrics)),
    buildLine("Avg Drawdown", formatMetricMoney(metrics.risk.averageDrawdown, { forceNegative: true })),
    buildLine("DD Length", formatHours(metrics.risk.maxDrawdownLengthHours)),
    buildLine("Recovery", formatHours(metrics.risk.recoveryTimeHours)),
    "",
    "DIRECTION",
    buildLine("Long %", formatMetricPercent(metrics.direction.longPercent)),
    buildLine("Short %", formatMetricPercent(metrics.direction.shortPercent)),
    buildLine(
      "PnL Share",
      `Long ${formatMetricMoney(metrics.direction.longPnl)} (${formatMetricPercent(metrics.direction.longPnlShare)}) | Short ${formatMetricMoney(
        metrics.direction.shortPnl,
      )} (${formatMetricPercent(metrics.direction.shortPnlShare)})`,
    ),
    buildLine("Insight", formatDirectionInsight(metrics)),
    "",
    "Direction Breakdown",
    buildLine(
      "Long",
      `Trades ${formatMetric(metrics.direction.longTrades, (value) => formatInteger(value))} | WR ${formatDirectionMetricPercent(
        metrics.direction.longWinRate,
        "long",
        longTrades,
      )} | Expectancy ${formatDirectionMetricMoney(metrics.direction.longExpectancy, "long", longTrades)} | PF ${formatDirectionMetricRatio(
        metrics.direction.longProfitFactor,
        "long",
        longTrades,
      )} | Avg W/L ${formatDirectionMetricMoney(metrics.direction.longAvgWin, "long", longTrades)} / ${formatDirectionMetricMoney(
        metrics.direction.longAvgLoss,
        "long",
        longTrades,
      )} | Large Loss % ${formatDirectionMetricPercent(metrics.direction.longLargeLossRate, "long", longTrades)}`,
    ),
    buildLine(
      "Short",
      `Trades ${formatMetric(metrics.direction.shortTrades, (value) => formatInteger(value))} | WR ${formatDirectionMetricPercent(
        metrics.direction.shortWinRate,
        "short",
        shortTrades,
      )} | Expectancy ${formatDirectionMetricMoney(metrics.direction.shortExpectancy, "short", shortTrades)} | PF ${formatDirectionMetricRatio(
        metrics.direction.shortProfitFactor,
        "short",
        shortTrades,
      )} | Avg W/L ${formatDirectionMetricMoney(metrics.direction.shortAvgWin, "short", shortTrades)} / ${formatDirectionMetricMoney(
        metrics.direction.shortAvgLoss,
        "short",
        shortTrades,
      )} | Large Loss % ${formatDirectionMetricPercent(metrics.direction.shortLargeLossRate, "short", shortTrades)}`,
    ),
    "",
    "PAYOFF",
    buildLine("W/L Ratio", formatMetricRatio(metrics.payoff.winLossRatio)),
    buildLine("Avg Win", formatMetricMoney(metrics.payoff.averageWin)),
    buildLine("Avg Loss", formatMetricMoney(metrics.payoff.averageLoss)),
    buildLine("Breakeven WR", formatMetricPercent(metrics.payoff.breakevenWinRate)),
    buildLine("Current WR", formatMetricPercent(metrics.payoff.currentWinRate)),
    buildLine("WR Cushion", formatMetricPoints(metrics.payoff.wrCushion)),
    buildLine(
      "Large Loss Rate",
      metrics.payoff.largeLossRate.value === null
        ? "N/A"
        : `${formatMetricPercent(metrics.payoff.largeLossRate)} (<= ${formatMoney(metrics.payoff.largeLossThreshold.value, {
            forceNegative: true,
          })})`,
    ),
    buildLine("P95 Loss", formatP95Loss(metrics)),
    buildLine("Capture", formatCapture(metrics)),
    "",
    "Points Payoff By Basis",
    ...DISPLAY_POINT_BASES.map((basis) =>
      buildLine(
        basis,
        `Avg Point Gain ${formatPointValue(metrics.payoff.pointPayoffByBasis[basis].avgPointGain, basis)} | Avg Point Loss ${formatPointValue(
          metrics.payoff.pointPayoffByBasis[basis].avgPointLoss,
          basis,
        )}`,
      ),
    ),
    "",
    "ACTIVITY",
    buildLine("Active Days", formatInteger(summary.active_days)),
    buildLine("Avg Trades / Day", formatDecimal(summary.avg_trades_per_day, 1)),
    buildLine("Median / Day", formatDecimal(metrics.activity.medianTradesPerDay, 1)),
    buildLine("Max / Day", formatInteger(metrics.activity.maxTradesInDay)),
    buildLine("Projected Trades / Week", formatProjectedWeeklyValue(metrics.activity.tradesPerWeek, metrics)),
    buildLine("Projected Active Days / Week", formatProjectedWeeklyValue(metrics.activity.activeDaysPerWeek, metrics)),
    buildLine("Trades / Active Hour", formatTradesPerActiveHour(metrics)),
    "",
    "SUSTAINABILITY",
    buildLine("Score", `${formatInteger(metrics.sustainability.score)}/100 (${metrics.sustainability.label})`),
    buildLine("Risk Quality", `${formatDecimal(metrics.sustainability.riskScore, 1)}/100 (higher is better)`),
    buildLine("Consistency", `${formatDecimal(metrics.sustainability.consistencyScore, 1)}/100`),
    buildLine("Edge", `${formatDecimal(metrics.sustainability.edgeScore, 1)}/100`),
    "",
    "HOLD TIME",
    buildLine("Hold Time Ratio (win/loss)", formatMetricRatio(metrics.holdTime.ratio)),
    buildLine("Avg Win Duration", formatDurationFromMinutes(metrics.holdTime.averageWinDurationMinutes)),
    buildLine("Avg Loss Duration", formatDurationFromMinutes(metrics.holdTime.averageLossDurationMinutes)),
    "",
    "DAILY BALANCE",
    buildLine("Start Balance", formatMoney(balanceSummary.startBalance, { showPositiveSign: false })),
    buildLine("Ending Balance", formatMoney(balanceSummary.endingBalance, { showPositiveSign: false })),
    buildLine("High", formatMoney(balanceSummary.highBalance, { showPositiveSign: false })),
    buildLine("Low", formatMoney(balanceSummary.lowBalance, { showPositiveSign: false })),
    buildLine("Largest Day", formatMoney(balanceSummary.largestDay)),
    "",
    `PNL CALENDAR (${safeRangeLabel})`,
  ];

  if (orderedCalendarDays.length === 0) {
    sections.push("- N/A");
  } else {
    sections.push(
      ...orderedCalendarDays.map((day) =>
        buildLine(
          formatCalendarDate(day.date),
          `${formatMoney(day.net_pnl)} (${formatInteger(day.trade_count)} trade${day.trade_count === 1 ? "" : "s"})`,
        ),
      ),
    );
  }

  return sections.join("\n");
}

export function buildStatsCoachSummary({ metrics, rangeLabel }: BuildFullStatsTextInput): StatsCoachSummary {
  if (metrics.summary.trade_count <= 0) {
    return {
      verdict: NO_DATA_TEXT,
      confidence: {
        label: "No meaningful sample yet",
        detail: "No meaningful sample yet - 0 trades / 0 active days / 0 missing key metrics",
        tone: "negative",
      },
      keyStats: [],
      topLevers: [],
      sections: [],
    };
  }

  const summary = metrics.summary;
  const netPnl = getMetricValue(metrics.performance.netPnl);
  const expectancy = getMetricValue(metrics.performance.expectancyPerTrade);
  const profitFactor = summary.profit_factor;
  const winRate = summary.win_rate;
  const breakevenWinRate = getMetricValue(metrics.payoff.breakevenWinRate);
  const drawdownPercentOfNet = getMetricValue(metrics.risk.drawdownPercentOfNet);
  const drawdownPercentOfRiskBase = getMetricValue(metrics.risk.drawdownPercentOfEquityBase);
  const sustainabilityScore = metrics.sustainability.score;
  const riskScore = metrics.sustainability.riskScore;
  const redDayPercent = getMetricValue(metrics.consistency.redDayPercent);
  const winLossRatio = getMetricValue(metrics.payoff.winLossRatio);
  const largeLossRate = getMetricValue(metrics.payoff.largeLossRate);
  const tradeCount = summary.trade_count;
  const activeDays = summary.active_days;
  const activeDaysPerWeek = metrics.activity.activeDaysPerWeek;
  const sampleQuality = buildSampleQuality(metrics);
  const leverCandidates = buildLeverCandidates(metrics, sampleQuality);
  const topLevers = buildTopLevers(leverCandidates, metrics);
  const directionComparison = getDirectionComparison(metrics);
  const directionConcentration = getDirectionConcentrationText(metrics);
  const sustainabilityDrivers = getSustainabilityDrivers(metrics, sampleQuality);
  const formatStrength = (text: string) => (sampleQuality.isLow ? `Early signal: ${text}` : `${text[0].toUpperCase()}${text.slice(1)}`);

  const doingRight: string[] = [];
  const doingWrong: string[] = [];
  const improvements = leverCandidates.slice(0, 5).map((lever) => `${lever.issue}: ${lever.metric}. ${lever.action}`);
  const actionPlan = leverCandidates.slice(0, 6).map((lever) => `${lever.issue}: ${lever.action}`);

  if (netPnl !== null && netPnl > 0) {
    doingRight.push(formatStrength(`you are profitable in this range: ${formatMoney(netPnl)} net after fees.`));
  }
  if (expectancy !== null && expectancy > 0) {
    doingRight.push(formatStrength(`expectancy is positive at ${formatMoney(expectancy)} per trade.`));
  }
  if (Number.isFinite(profitFactor) && profitFactor >= 1.5) {
    doingRight.push(formatStrength(`profit factor is ${formatRatio(profitFactor)}, so winners are covering losers in this sample.`));
  }
  if (breakevenWinRate !== null && Number.isFinite(winRate) && winRate > breakevenWinRate) {
    doingRight.push(formatStrength(`your ${formatPercent(winRate)} win rate is above the ${formatPercent(breakevenWinRate)} breakeven win rate.`));
  }
  if (redDayPercent !== null && redDayPercent <= 20) {
    doingRight.push(formatStrength(`red day rate is controlled at ${formatPercent(redDayPercent)}.`));
  }
  if (largeLossRate !== null && largeLossRate <= 5) {
    doingRight.push(formatStrength(`large loss rate is ${formatPercent(largeLossRate)}.`));
  }
  addInsightByTone(metrics.consistency.insight, doingRight, doingWrong);
  addInsightByTone(metrics.payoff.insight, doingRight, doingWrong);
  addInsightByTone(metrics.direction.insight, doingRight, doingWrong);

  if (riskScore < 50) {
    doingWrong.push(`Risk quality is the biggest issue: ${formatDecimal(riskScore, 1)}/100, where higher is better.`);
  }
  if (drawdownPercentOfNet !== null && drawdownPercentOfNet >= 25) {
    doingWrong.push(`Drawdown is too large relative to profit: max drawdown equals ${formatPercent(drawdownPercentOfNet)} of net PnL.`);
  }
  if (drawdownPercentOfRiskBase !== null && drawdownPercentOfRiskBase >= 3) {
    doingWrong.push(
      `Max drawdown reached ${formatPercent(drawdownPercentOfRiskBase)} of risk base (${getRiskBaseDescription(
        metrics,
      )}), which can pressure consistency and funding rules.`,
    );
  }
  if (Number.isFinite(sustainabilityScore) && sustainabilityScore < 60) {
    const driverText = sustainabilityDrivers.length > 0 ? `, driven by ${sustainabilityDrivers.join(", ")}` : "";
    doingWrong.push(`The run is not yet sustainable: sustainability is ${formatInteger(sustainabilityScore)}/100 (${metrics.sustainability.label})${driverText}.`);
  }
  if (winLossRatio !== null && winLossRatio < 1) {
    doingWrong.push(`Average loss is slightly larger than average win: W/L ratio is ${formatRatio(winLossRatio)}.`);
  }
  if (activeDays > 0 && isFiniteNumber(activeDaysPerWeek) && activeDaysPerWeek >= 6) {
    doingWrong.push(
      `Current pace projects to ${formatDecimal(activeDaysPerWeek, 1)} active days/week based on ${formatInteger(activeDays)} ${dayNoun(
        activeDays,
      )}; burnout and forced trades are risks if that pace continues.`,
    );
  }
  if (directionComparison !== null) {
    doingWrong.push(
      `${directionComparison.weaker} side is underperforming: ${directionComparison.weaker.toLowerCase()} expectancy is ${formatMoney(
        directionComparison.weakerExpectancy,
      )} versus ${directionComparison.stronger.toLowerCase()} expectancy at ${formatMoney(directionComparison.strongerExpectancy)}.`,
    );
  }
  if (directionConcentration !== null) {
    doingWrong.push(`PnL is concentrated by direction: ${directionConcentration}.`);
  }
  if (sampleQuality.isLow) {
    doingWrong.push(`${sampleQuality.detail}. Treat positive stats as directional, not proven, until the sample reaches at least 30 trades and 5 active days.`);
  }

  if (doingRight.length === 0) {
    doingRight.push("No clear strengths stand out yet from this range. Collect more trades and focus on clean execution data.");
  }
  if (doingWrong.length === 0) {
    doingWrong.push("No major statistical weakness stands out in this selected range. Keep collecting data and watch for risk expansion.");
  }

  return {
    verdict: buildVerdict(metrics, rangeLabel, sampleQuality),
    confidence: {
      label: sampleQuality.label,
      detail: sampleQuality.detail,
      tone: sampleQuality.tone,
    },
    keyStats: [
      {
        label: "Net PnL",
        value: formatMetricMoney(metrics.performance.netPnl),
        tone: netPnl !== null && netPnl > 0 ? "positive" : netPnl !== null && netPnl < 0 ? "negative" : "neutral",
      },
      {
        label: "Expectancy",
        value: `${formatMetricMoney(metrics.performance.expectancyPerTrade)} / trade`,
        tone: expectancy !== null && expectancy > 0 ? "positive" : expectancy !== null && expectancy < 0 ? "negative" : "neutral",
      },
      {
        label: "Profit Factor",
        value: formatRatio(profitFactor),
        tone: Number.isFinite(profitFactor) && profitFactor >= 1.5 ? "positive" : Number.isFinite(profitFactor) && profitFactor < 1 ? "negative" : "neutral",
      },
      {
        label: "Risk Quality",
        value: `${formatDecimal(riskScore, 1)}/100 (higher is better)`,
        tone: riskScore >= 70 ? "positive" : riskScore < 50 ? "negative" : "neutral",
      },
      {
        label: "Trades / Days",
        value: `${formatInteger(tradeCount)} / ${formatInteger(activeDays)}`,
        tone: sampleQuality.tone,
      },
    ],
    topLevers: topLevers.map((lever) => ({
      issue: lever.issue,
      metric: lever.metric,
      action: lever.action,
      tone: getLeverTone(lever),
    })),
    sections: [
      { title: "Top 3 Levers", items: topLevers.map(formatLever) },
      { title: "What You're Doing Right", items: doingRight },
      { title: "Main Risks", items: doingWrong },
      { title: "Improvements", items: improvements.slice(0, 5) },
      { title: "Next 10 Trading Days Plan", items: actionPlan.slice(0, 5) },
    ],
  };
}

function CoachSummarySection({ section }: { section: StatsCoachSummarySection }) {
  return (
    <section className="rounded-lg border border-app-border/80 bg-app-surface/70 p-4">
      <h3 className="text-base font-semibold text-app-text">{section.title}</h3>
      <ul className="mt-3 space-y-3 text-[15px] leading-7 text-app-text-soft">
        {section.items.map((item) => (
          <li key={item} className="flex gap-3">
            <span className="mt-[10px] h-1.5 w-1.5 shrink-0 rounded-full bg-app-accent/80" />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function getSummaryToneClass(tone: SummaryTone) {
  if (tone === "positive") {
    return "border-app-positive/30 bg-app-positive/10";
  }
  if (tone === "negative") {
    return "border-app-negative/30 bg-app-negative/10";
  }
  return "border-app-border/80 bg-app-surface/70";
}

function TopLeverCard({ lever, index }: { lever: StatsCoachSummaryLever; index: number }) {
  return (
    <article className={cn("rounded-lg border p-4", getSummaryToneClass(lever.tone))}>
      <div className="flex items-start gap-3">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-app-border/80 bg-app-bg/70 text-sm font-semibold text-app-text">
          {index + 1}
        </span>
        <div className="min-w-0">
          <h4 className="text-base font-semibold text-app-text">{lever.issue}</h4>
          <dl className="mt-3 space-y-2 text-[15px] leading-6">
            <div>
              <dt className="text-[11px] font-semibold uppercase text-app-muted">Metric</dt>
              <dd className="mt-0.5 text-app-text-soft">{lever.metric}</dd>
            </div>
            <div>
              <dt className="text-[11px] font-semibold uppercase text-app-muted">Next Action</dt>
              <dd className="mt-0.5 text-app-text">{lever.action}</dd>
            </div>
          </dl>
        </div>
      </div>
    </article>
  );
}

function CoachSummaryDialog({
  open,
  onClose,
  rangeLabel,
  metrics,
  coachSummary,
}: {
  open: boolean;
  onClose: () => void;
  rangeLabel: string;
  metrics: CopyFullStatsMetrics;
  coachSummary: StatsCoachSummary;
}) {
  useEffect(() => {
    if (!open) {
      return;
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown);

    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onClose]);

  if (!open || typeof document === "undefined") {
    return null;
  }

  return createPortal(
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-app-bg/75 p-4 backdrop-blur-sm" onClick={onClose}>
      <section
        className="max-h-[86vh] w-full max-w-6xl overflow-hidden rounded-xl border border-app-border bg-app-bg/95 shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-label="Trading Summary"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 border-b border-app-border px-6 py-5">
          <div>
            <h2 className="text-xl font-semibold text-app-text">Trading Summary</h2>
            <p className="mt-1.5 text-[15px] text-app-muted">Selected range: {rangeLabel.trim() || "selected range"}</p>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close trading summary">
            Close
          </Button>
        </div>
        <div className="max-h-[calc(86vh-92px)] overflow-y-auto px-6 py-5">
          {metrics.summary.trade_count <= 0 ? (
            <p className="text-[15px] text-app-muted">{NO_DATA_TEXT}</p>
          ) : (
            <div className="space-y-6">
              <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
                <section className="rounded-lg border border-app-accent/30 bg-app-accent/10 p-4">
                  <p className="text-xs font-semibold uppercase text-app-muted">Quick Verdict</p>
                  <p className="mt-2.5 text-base leading-7 text-app-text">{coachSummary.verdict}</p>
                </section>
                <section className={cn("rounded-lg border p-4", getSummaryToneClass(coachSummary.confidence.tone))}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-xs font-semibold uppercase text-app-muted">Sample Quality</p>
                    <p className="text-sm font-semibold text-app-text">{coachSummary.confidence.label}</p>
                  </div>
                  <p className="mt-2.5 text-[15px] leading-7 text-app-text-soft">{coachSummary.confidence.detail}</p>
                </section>
              </div>

              <section>
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-base font-semibold text-app-text">Key Stats</h3>
                  <p className="text-xs font-medium uppercase text-app-muted">Snapshot</p>
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
                  {coachSummary.keyStats.map((stat) => (
                    <div key={stat.label} className={cn("rounded-lg border p-4", getSummaryToneClass(stat.tone))}>
                      <p className="text-[11px] font-medium uppercase text-app-muted">{stat.label}</p>
                      <p className="mt-1.5 text-base font-semibold leading-6 text-app-text">{stat.value}</p>
                    </div>
                  ))}
                </div>
              </section>

              <section>
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-base font-semibold text-app-text">Top 3 Levers</h3>
                  <p className="text-xs font-medium uppercase text-app-muted">Issue / Metric / Action</p>
                </div>
                <div className="grid gap-3 lg:grid-cols-3">
                  {coachSummary.topLevers.map((lever, index) => (
                    <TopLeverCard key={`${lever.issue}-${lever.metric}`} lever={lever} index={index} />
                  ))}
                </div>
              </section>

              <div className="grid gap-4 lg:grid-cols-2">
                {coachSummary.sections
                  .filter((section) => section.title !== "Top 3 Levers")
                  .map((section) => (
                    <CoachSummarySection key={section.title} section={section} />
                  ))}
              </div>
            </div>
          )}
        </div>
      </section>
    </div>,
    document.body,
  );
}

export function CopyFullStatsButton({
  metrics,
  rangeLabel,
  calendarDays,
  disabled = false,
  className,
}: CopyFullStatsButtonProps) {
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [copiedFullStats, setCopiedFullStats] = useState(false);
  const timeoutRef = useRef<number | null>(null);
  const coachSummary = buildStatsCoachSummary({ metrics, rangeLabel, calendarDays });

  useEffect(() => {
    return () => {
      if (timeoutRef.current !== null) {
        window.clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  const handleCopyFullStats = async () => {
    const text = buildFullStatsText({ metrics, rangeLabel, calendarDays });
    const success = await copyTextToClipboard(text);
    if (!success) {
      return;
    }

    setCopiedFullStats(true);
    if (timeoutRef.current !== null) {
      window.clearTimeout(timeoutRef.current);
    }
    timeoutRef.current = window.setTimeout(() => {
      setCopiedFullStats(false);
      timeoutRef.current = null;
    }, COPY_FEEDBACK_MS);
  };

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setSummaryOpen(true)}
        disabled={disabled}
        className={cn(
          "shrink-0 rounded-lg border px-2.5 text-[11px]",
          "border-app-border/80 bg-transparent hover:border-app-border-strong hover:bg-app-raised/70",
          className,
        )}
      >
        Summary
      </Button>
      <Button
        variant={copiedFullStats ? "secondary" : "ghost"}
        size="sm"
        onClick={() => void handleCopyFullStats()}
        disabled={disabled}
        className={cn(
          "shrink-0 rounded-lg border px-2.5 text-[11px]",
          copiedFullStats ? "border-app-accent/40" : "border-app-border/80 bg-transparent hover:border-app-border-strong hover:bg-app-raised/70",
          className,
        )}
      >
        {copiedFullStats ? "Copied" : "Copy Full Stats"}
      </Button>
      <CoachSummaryDialog
        open={summaryOpen}
        onClose={() => setSummaryOpen(false)}
        rangeLabel={rangeLabel}
        metrics={metrics}
        coachSummary={coachSummary}
      />
    </>
  );
}

export { NO_DATA_TEXT, POINT_BASES, titleDateFormatter };
