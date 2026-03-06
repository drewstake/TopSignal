/* eslint-disable react-refresh/only-export-components */
import { useEffect, useRef, useState } from "react";

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
}

interface BalanceSummary {
  startBalance: number | null;
  endingBalance: number | null;
  highBalance: number | null;
  lowBalance: number | null;
  largestDay: number | null;
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

function buildLine(label: string, value: string) {
  return `• ${label}: ${value}`;
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

  let highBalance = series[0].balance;
  let lowBalance = series[0].balance;
  let largestDay = series[0].netPnl;

  for (const point of series) {
    highBalance = Math.max(highBalance, point.balance);
    lowBalance = Math.min(lowBalance, point.balance);
    if (Math.abs(point.netPnl) > Math.abs(largestDay)) {
      largestDay = point.netPnl;
    }
  }

  return {
    startBalance: series[0].balance,
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

export function buildFullStatsText({ metrics, rangeLabel, calendarDays = [] }: BuildFullStatsTextInput) {
  if (metrics.summary.trade_count <= 0) {
    return NO_DATA_TEXT;
  }

  const safeRangeLabel = rangeLabel.trim() || "selected range";
  const orderedCalendarDays = sortCalendarDays(calendarDays);
  const balanceSummary = buildBalanceSummary(calendarDays, metrics.balance.currentBalance);
  const summary = metrics.summary;

  const sections: string[] = [
    `TopSignal Full Stats (${safeRangeLabel})`,
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
      "Worst Day",
      `${formatMetricMoney(metrics.consistency.worstDay)} (${formatMetricPercent(metrics.consistency.worstDayPercentOfNet)})`,
    ),
    buildLine("Median Day", formatMetricMoney(metrics.consistency.medianDayPnl)),
    buildLine("Avg Green", formatMetricMoney(metrics.consistency.avgGreenDay)),
    buildLine("Avg Red", formatMetricMoney(metrics.consistency.avgRedDay)),
    buildLine("Red Day %", formatMetricPercent(metrics.consistency.redDayPercent)),
    buildLine(
      "Worst Day Impact",
      metrics.consistency.worstDayImpact.value === null
        ? "N/A"
        : `Worst Day = ${formatDecimal(metrics.consistency.worstDayImpact.value, 1)} days of avg profit`,
    ),
    buildLine("G/R Size Ratio", formatMetricRatio(metrics.consistency.greenRedDaySizeRatio)),
    buildLine("Stability", formatMetricPercent(metrics.consistency.stabilityScore)),
    "",
    "RISK",
    buildLine("Max Drawdown", formatMetricMoney(metrics.risk.maxDrawdown, { forceNegative: true })),
    buildLine("DD % of Net PnL", formatMetricPercent(metrics.risk.drawdownPercentOfNet)),
    buildLine("Max DD % of Equity Base", formatMetricPercent(metrics.risk.drawdownPercentOfEquityBase)),
    buildLine("Equity Base", formatMoney(metrics.risk.equityBase.value, { showPositiveSign: false })),
    buildLine("Equity Base Basis", metrics.risk.equityBase.label || "N/A"),
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
    buildLine("Insight", metrics.direction.insight || "N/A"),
    "",
    "Direction Breakdown",
    buildLine(
      "Long",
      `Trades ${formatMetric(metrics.direction.longTrades, (value) => formatInteger(value))} | WR ${formatMetricPercent(
        metrics.direction.longWinRate,
      )} | Expectancy ${formatMetricMoney(metrics.direction.longExpectancy)} | PF ${formatMetricRatio(
        metrics.direction.longProfitFactor,
      )} | Avg W/L ${formatMetricMoney(metrics.direction.longAvgWin)} / ${formatMetricMoney(metrics.direction.longAvgLoss)} | Large Loss % ${formatMetricPercent(
        metrics.direction.longLargeLossRate,
      )}`,
    ),
    buildLine(
      "Short",
      `Trades ${formatMetric(metrics.direction.shortTrades, (value) => formatInteger(value))} | WR ${formatMetricPercent(
        metrics.direction.shortWinRate,
      )} | Expectancy ${formatMetricMoney(metrics.direction.shortExpectancy)} | PF ${formatMetricRatio(
        metrics.direction.shortProfitFactor,
      )} | Avg W/L ${formatMetricMoney(metrics.direction.shortAvgWin)} / ${formatMetricMoney(metrics.direction.shortAvgLoss)} | Large Loss % ${formatMetricPercent(
        metrics.direction.shortLargeLossRate,
      )}`,
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
    buildLine("P95 Loss", formatMetricMoney(metrics.payoff.p95Loss, { forceNegative: metrics.payoff.p95Loss.value !== null && metrics.payoff.p95Loss.value < 0 })),
    buildLine(
      "Capture",
      metrics.payoff.capture.value === null ? "N/A" : formatPercent((metrics.payoff.capture.value ?? 0) * 100),
    ),
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
    buildLine("Trades / Week", formatDecimal(metrics.activity.tradesPerWeek, 1)),
    buildLine("Days / Week", formatDecimal(metrics.activity.activeDaysPerWeek, 1)),
    buildLine("Trades / Active Hour", formatDecimal(metrics.activity.tradesPerActiveHour, 2)),
    "",
    "SUSTAINABILITY",
    buildLine("Score", `${formatInteger(metrics.sustainability.score)}/100 (${metrics.sustainability.label})`),
    buildLine("Risk", `${formatDecimal(metrics.sustainability.riskScore, 1)}/100`),
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
    sections.push("• N/A");
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

export function CopyFullStatsButton({
  metrics,
  rangeLabel,
  calendarDays,
  disabled = false,
  className,
}: CopyFullStatsButtonProps) {
  const [copied, setCopied] = useState(false);
  const timeoutRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (timeoutRef.current !== null) {
        window.clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  const handleCopy = async () => {
    const text = buildFullStatsText({ metrics, rangeLabel, calendarDays });
    const success = await copyTextToClipboard(text);
    if (!success) {
      return;
    }

    setCopied(true);
    if (timeoutRef.current !== null) {
      window.clearTimeout(timeoutRef.current);
    }
    timeoutRef.current = window.setTimeout(() => {
      setCopied(false);
      timeoutRef.current = null;
    }, COPY_FEEDBACK_MS);
  };

  return (
    <Button
      variant={copied ? "secondary" : "ghost"}
      size="sm"
      onClick={() => void handleCopy()}
      disabled={disabled}
      className={cn(
        "shrink-0 rounded-lg border px-2.5 text-[11px]",
        copied ? "border-cyan-300/40" : "border-slate-700/80 bg-transparent hover:border-slate-600 hover:bg-slate-800/70",
        className,
      )}
    >
      {copied ? "Copied" : "Copy Full Stats"}
    </Button>
  );
}

export { NO_DATA_TEXT, POINT_BASES, titleDateFormatter };
