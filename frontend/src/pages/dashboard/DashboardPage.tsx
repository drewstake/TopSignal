import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate, useOutletContext, useSearchParams } from "react-router-dom";

import type { CopyFullStatsMetrics } from "../../components/dashboard/CopyFullStatsButton";
import { DemoModeNotice } from "../../components/demo/DemoModeNotice";
import { useDemoInteractionPolicy } from "../../components/demo/useDemoInteractionPolicy";
import { Chip } from "../../components/metrics/Chip";
import { DonutRing } from "../../components/metrics/DonutRing";
import { GaugeBar } from "../../components/metrics/GaugeBar";
import { InfoPopover } from "../../components/metrics/InfoPopover";
import { MasonryGrid } from "../../components/metrics/MasonryGrid";
import { MetricCard } from "../../components/metrics/MetricCard";
import { MiniStatList } from "../../components/metrics/MiniStatList";
import { SplitBar } from "../../components/metrics/SplitBar";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { cn } from "../../components/ui/cn";
import { Input } from "../../components/ui/Input";
import { Skeleton } from "../../components/ui/Skeleton";
import { Toggle } from "../../components/ui/Toggle";
import {
  ACCOUNT_QUERY_PARAM,
  parseAccountId,
  readStoredAccountId,
  readStoredMainAccountId,
  writeStoredAccountId,
} from "../../lib/accountSelection";
import { getAccountRiskRuleForAccount } from "../../lib/accountRiskRules";
import { useAccountRequestGate } from "../../lib/accountRequestGate";
import {
  describeAccountProviderSync,
  describeProviderRefreshException,
  formatAccountBalance,
  formatProviderLastSeen,
  getAvailableAccountBalance,
  summarizeAccountProviderSync,
  useAccountProviderFreshness,
} from "../../lib/accountProviderState";
import { accountsApi } from "../../lib/api";
import { sortAccountsForActiveSelection } from "../../lib/accountOrdering";
import { isCompactModeEnabled, setCompactModeEnabled } from "../../lib/compactMode";
import { DEMO_AS_OF_ISO } from "../../lib/demoScenario";
import { getTradingDayBoundaryIso, getTradingDayRange, tradingDayKey } from "../../lib/tradingDay";
import { ACCOUNT_TRADES_SYNCED_EVENT, type AccountTradesSyncedDetail } from "../../lib/tradeSyncEvents";
import type { AccountInfo, AccountPnlCalendarDay, AccountSizingBenchmark, AccountSummary, AccountTrade } from "../../lib/types";
import { logPerfInfo } from "../../lib/perf";
import { useLatestRequestGuard } from "../../lib/latestRequest";
import { formatCurrency, formatInteger, formatMinutes, formatNumber, formatPercent, formatPnl } from "../../utils/formatters";
import { computeActivityMetrics } from "../../utils/activityMetrics";
import {
  computeDirectionPercentages,
  computeDrawdownPercentOfEquityBase,
  computeDrawdownPercentOfNetPnl,
  computeStabilityScoreFromWorstDayPercent,
} from "../../utils/metrics";
import { computeSustainability, type SustainabilityLabel } from "../../utils/sustainability";
import type { AppShellOutletContext } from "../../app/AppShell";
import { CompactDashboardSkeleton, CompactDashboardView } from "./components/CompactDashboardView";
import { CopyTradePanel } from "./components/CopyTradePanel";
import { RecentTradesCard } from "./components/RecentTradesCard";
import { TradeImportPanel } from "./components/TradeImportPanel";
import { ViewportDeferredSection } from "./components/ViewportDeferredSection";
import {
  COPY_TRADE_ENABLE_CONFIRMATION_MESSAGE,
  COPY_TRADE_MATCH_WINDOW_MINUTES,
  MAX_COPY_TRADE_FOLLOWERS,
  buildCopyTradeAccountRows,
  combineCopyTradePnlCalendarDays,
  computeCopyTradeDriftSummary,
  computeCopyTradeTotals,
  getCopyTradeDailyNetPnlForTradingDay,
  getCopyTradeRosterAccountIds,
  getCopyTradeUncopyEventsResetAt,
  updateCopyTradeFollowerAccountIds,
  prepareCopyTradeModeToggle,
  readStoredCopyTradeSettings,
  updateCopyTradeModeSetting,
  updateCopyTradeUncopyEventsResetAt,
  writeStoredCopyTradeSettings,
  type CopyTradeAccountRow,
  type CopyTradeDriftSummary,
  type CopyTradeMetricSnapshot,
  type CopyTradeSettings,
  type CopyTradeTotals,
} from "./copyTrade";
import { computeCopyTradeWhenEnabled } from "./copyTradePerformance";
import { computeDashboardDerivedMetrics } from "./metrics/calculations";
import type { MetricValue } from "./metrics/types";
import {
  RECENT_TRADES_RENDER_PAGE_SIZE,
  getNextRecentTradesVisibleCount,
} from "./recentTradesPagination";
import {
  buildCompactAccountRequestPlan,
  buildCompactDashboardScopes,
  combineCompactCalendarDays,
  combineCompactSummaries,
  combineCompactTrades,
  computeCompactCalendarMaxDrawdown,
  COMPACT_RECENT_TRADES_LIMIT,
  refreshCompactCopyThenInvalidate,
  type CompactDashboardScopes,
} from "./compactDashboardData";
import { resolveCustomDateRangeDraft } from "./customDateRange";
import {
  buildPnlCalendarRefreshWindow,
  type PnlCalendarRefreshWindow,
} from "./pnlCalendarRefresh";
import {
  resolveLiveAccountEnableDecision,
  resolveProjectXAccountId,
} from "./liveAccountMode";
import { useAccountScopedDaySelection } from "./useAccountScopedDaySelection";

const CopyFullStatsButton = lazy(() =>
  import("../../components/dashboard/CopyFullStatsButton").then((module) => ({ default: module.CopyFullStatsButton })),
);
const DailyAccountBalanceCard = lazy(() =>
  import("./components/DailyAccountBalanceCard").then((module) => ({ default: module.DailyAccountBalanceCard })),
);
const PnlCalendarCard = lazy(() =>
  import("./components/PnlCalendarCard").then((module) => ({ default: module.PnlCalendarCard })),
);

const TRADE_LIMIT = 200;
const DAY_FILTER_TRADE_LIMIT = 1000;
const METRIC_TRADE_LIMIT = 1000;
type MetricsRangePreset = "1D" | "1W" | "1M" | "6M" | "ALL" | "CUSTOM";
type PointsBasis = "auto" | "MNQ" | "MES" | "NQ" | "ES" | "MGC" | "SIL";
type ConcretePointsBasis = Exclude<PointsBasis, "auto">;
const PAYOFF_POINTS_BASES: ConcretePointsBasis[] = ["MNQ", "MES", "NQ", "ES", "MGC", "SIL"];
const DISPLAY_PAYOFF_POINTS_BASES: ConcretePointsBasis[] = ["MNQ", "MES"];
const RISK_PRESSURE_FULL_SCALE_PERCENT = 25;
const EMPTY_COPY_TRADE_ACCOUNT_IDS: number[] = [];
const EMPTY_COPY_TRADE_SNAPSHOTS: Record<number, CopyTradeMetricSnapshot | undefined> = {};
const EMPTY_COPY_TRADE_ROWS: CopyTradeAccountRow[] = [];
const EMPTY_COPY_TRADE_CALENDAR_DAYS: AccountPnlCalendarDay[] = [];
const EMPTY_COPY_TRADE_TRADES_BY_ACCOUNT_ID: Record<number, AccountTrade[] | undefined> = {};
const EMPTY_COPY_TRADE_TOTALS: CopyTradeTotals = {
  hasLeader: false,
  canCalculate: false,
  combinedNetPnl: 0,
  combinedDailyPnl: 0,
  combinedBalance: null,
  leaderNetPnl: 0,
  leaderDailyPnl: 0,
  followerContributionNetPnl: 0,
  followerContributionDailyPnl: 0,
  activeCopiedAccountCount: 0,
  followersCopyingCount: 0,
  warnings: [],
};
const EMPTY_COPY_TRADE_DRIFT_SUMMARY: CopyTradeDriftSummary = {
  likelyUncopyEventCount: 0,
  followerOnlyTradeCount: 0,
  followerOnlyNetPnl: 0,
  affectedAccountCount: 0,
  matchWindowMinutes: COPY_TRADE_MATCH_WINDOW_MINUTES,
  accounts: [],
};

const METRICS_RANGE_OPTIONS: Array<{ key: MetricsRangePreset; label: string }> = [
  { key: "1D", label: "1D" },
  { key: "1W", label: "1W" },
  { key: "1M", label: "1M" },
  { key: "6M", label: "6M" },
  { key: "ALL", label: "All" },
];

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});

const fullStatsRangeDayFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

const fullStatsRangeDayFormatterWithYear = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});

const emptySummary: AccountSummary = {
  realized_pnl: 0,
  gross_pnl: 0,
  fees: 0,
  net_pnl: 0,
  win_rate: 0,
  win_count: 0,
  loss_count: 0,
  breakeven_count: 0,
  profit_factor: 0,
  avg_win: 0,
  avg_loss: 0,
  avg_win_duration_minutes: 0,
  avg_loss_duration_minutes: 0,
  expectancy_per_trade: 0,
  tail_risk_5pct: 0,
  max_drawdown: 0,
  average_drawdown: 0,
  risk_drawdown_score: 0,
  max_drawdown_length_hours: 0,
  recovery_time_hours: 0,
  average_recovery_length_hours: 0,
  trade_count: 0,
  half_turn_count: 0,
  execution_count: 0,
  day_win_rate: 0,
  green_days: 0,
  red_days: 0,
  flat_days: 0,
  avg_trades_per_day: 0,
  active_days: 0,
  efficiency_per_hour: 0,
  profit_per_day: 0,
  averagePositionSize: 0,
  medianPositionSize: 0,
  tradeCountUsedForSizingStats: 0,
  avgPointGain: null,
  avgPointLoss: null,
  pointsBasisUsed: "auto",
  sizingBenchmark: {
    benchmarkMode: "fixed_average_size",
    benchmarkSizeUsed: 0,
    benchmarkGrossPnl: 0,
    benchmarkNetPnl: 0,
    benchmarkDiff: 0,
    benchmarkRatio: null,
    benchmarkLabel: "In Line With Benchmark",
  },
};

interface PointPayoffStats {
  avgPointGain: number | null;
  avgPointLoss: number | null;
}

type PointPayoffByBasis = Record<ConcretePointsBasis, PointPayoffStats>;

function createEmptyPointPayoffByBasis(): PointPayoffByBasis {
  return {
    MNQ: { avgPointGain: null, avgPointLoss: null },
    MES: { avgPointGain: null, avgPointLoss: null },
    NQ: { avgPointGain: null, avgPointLoss: null },
    ES: { avgPointGain: null, avgPointLoss: null },
    MGC: { avgPointGain: null, avgPointLoss: null },
    SIL: { avgPointGain: null, avgPointLoss: null },
  };
}

const sizingBenchmarkTooltipContent = (
  <div className="space-y-1.5">
    <div>
      <p className="font-semibold text-app-text">Sizing Benchmark</p>
      <p className="mt-1 text-app-text-soft">
        This compares your actual results to a fixed-size benchmark using the same trades, same entries, and same exits,
        but with a constant size equal to your average position size in the selected range.
      </p>
    </div>
    <div>
      <p className="font-semibold text-app-negative">Far Below Benchmark</p>
      <p className="text-app-text-soft">
        You made much less than the average-size benchmark, or your sizing reduced performance.
      </p>
    </div>
    <div>
      <p className="font-semibold text-app-warning">Below Benchmark</p>
      <p className="text-app-text-soft">You trailed the benchmark by a meaningful amount.</p>
    </div>
    <div>
      <p className="font-semibold text-app-text">In Line With Benchmark</p>
      <p className="text-app-text-soft">Your dynamic sizing performed about the same as a fixed average-size approach.</p>
    </div>
    <div>
      <p className="font-semibold text-app-accent">Above Benchmark</p>
      <p className="text-app-text-soft">Your sizing improved results over the benchmark.</p>
    </div>
    <div>
      <p className="font-semibold text-app-positive">Far Above Benchmark</p>
      <p className="text-app-text-soft">Your sizing clearly added strong value versus a fixed average-size approach.</p>
    </div>
    <div className="border-t border-app-border/80 pt-1.5 text-app-muted">
      <p>When the benchmark is positive, the label is based on your actual net PnL compared to benchmark net PnL.</p>
      <p className="mt-0.5">When the benchmark is flat or negative, fall back to dollar difference so the label stays meaningful.</p>
    </div>
  </div>
);

function formatDurationCompact(minutes: number) {
  const safeMinutes = Number.isFinite(minutes) ? Math.max(0, minutes) : 0;
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

function formatMicroPositionSize(size: number, tradeCountUsed: number) {
  if (tradeCountUsed <= 0 || !Number.isFinite(size) || size <= 0) {
    return "N/A";
  }
  return `${formatNumber(size, 1)} micros`;
}

function formatSizingBenchmarkSubtitle(size: number, tradeCountUsed: number) {
  const formattedSize = formatMicroPositionSize(size, tradeCountUsed);
  return formattedSize === "N/A" ? "vs Avg Size" : `vs Avg Size (${formattedSize})`;
}

function pnlClass(value: number) {
  return value >= 0 ? "text-app-positive" : "text-app-negative";
}

function formatMetricValue(metric: MetricValue, formatter: (value: number) => string) {
  if (metric.value === null) {
    return "N/A";
  }
  return formatter(metric.value);
}

function formatMetricValueWithNote(metric: MetricValue, formatter: (value: number) => string) {
  if (metric.value === null) {
    return metric.missingReason ? `N/A (${metric.missingReason})` : "N/A";
  }
  return formatter(metric.value);
}

function formatPoints(value: number) {
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${formatNumber(value, 1)} pts`;
}

function formatPointMetric(value: number | null, basis: ConcretePointsBasis) {
  if (value === null || !Number.isFinite(value)) {
    return "—";
  }
  const decimals = basis === "SIL" ? 3 : 2;
  return `${formatNumber(Math.abs(value), decimals)} pts`;
}

function metricPnlClass(metric: MetricValue) {
  if (metric.value === null) {
    return undefined;
  }
  return pnlClass(metric.value);
}

function sizingBenchmarkBadgeVariant(label: AccountSizingBenchmark["benchmarkLabel"]) {
  switch (label) {
    case "Far Below Benchmark":
      return "negative" as const;
    case "Below Benchmark":
      return "warning" as const;
    case "Above Benchmark":
      return "accent" as const;
    case "Far Above Benchmark":
      return "positive" as const;
    default:
      return "neutral" as const;
  }
}

function sizingBenchmarkDeltaLabel(diff: number) {
  if (diff > 0) {
    return "Sizing Edge";
  }
  if (diff < 0) {
    return "Sizing Drag";
  }
  return "Sizing Match";
}

function sizingBenchmarkComparisonLabel(benchmark: AccountSizingBenchmark) {
  if (benchmark.benchmarkRatio === null) {
    if (Math.abs(benchmark.benchmarkDiff) < 0.01) {
      return "In line on dollar difference";
    }
    return benchmark.benchmarkNetPnl <= 0 ? "Benchmark flat/negative, using $ diff" : "Benchmark near 0, using $ diff";
  }

  const ratioDeltaPercent = (benchmark.benchmarkRatio - 1) * 100;
  if (Math.abs(ratioDeltaPercent) < 0.1) {
    return "In line vs benchmark";
  }
  return `${ratioDeltaPercent > 0 ? "+" : ""}${formatNumber(ratioDeltaPercent, 1)}% vs benchmark`;
}

function sustainabilityBadgeVariant(label: SustainabilityLabel) {
  if (label === "Healthy") {
    return "positive" as const;
  }
  if (label === "Mostly healthy") {
    return "accent" as const;
  }
  if (label === "Unstable") {
    return "warning" as const;
  }
  return "negative" as const;
}

function sustainabilityFillClass(score: number) {
  if (score >= 80) {
    return "bg-app-positive/75";
  }
  if (score >= 60) {
    return "bg-app-accent/75";
  }
  if (score >= 40) {
    return "bg-app-warning/80";
  }
  return "bg-app-negative/80";
}

function parseIsoDay(value: string) {
  return new Date(`${value}T00:00:00.000Z`);
}

function formatFullStatsRangeLabel(startDay: string, endDay: string) {
  const startDate = parseIsoDay(startDay);
  const endDate = parseIsoDay(endDay);
  const startShort = fullStatsRangeDayFormatter.format(startDate);
  const endShort = fullStatsRangeDayFormatter.format(endDate);
  const startWithYear = fullStatsRangeDayFormatterWithYear.format(startDate);
  const endWithYear = fullStatsRangeDayFormatterWithYear.format(endDate);

  if (startDay === endDay) {
    return endWithYear;
  }

  if (startDate.getUTCFullYear() === endDate.getUTCFullYear()) {
    return `${startShort} to ${endShort}, ${endDate.getUTCFullYear()}`;
  }

  return `${startWithYear} to ${endWithYear}`;
}

function DeferredDashboardCardSkeleton({
  title,
  description,
  bodyHeightClassName,
}: {
  title: string;
  description: string;
  bodyHeightClassName: string;
}) {
  return (
    <Card role="status" aria-live="polite" aria-busy="true">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        <Skeleton className={cn("w-full", bodyHeightClassName)} />
      </CardContent>
    </Card>
  );
}

function ViewportDeferredDashboardCard({
  title,
  description,
  bodyHeightClassName,
  children,
}: {
  title: string;
  description: string;
  bodyHeightClassName: string;
  children: ReactNode;
}) {
  const fallback = (
    <DeferredDashboardCardSkeleton
      title={title}
      description={description}
      bodyHeightClassName={bodyHeightClassName}
    />
  );

  return (
    <ViewportDeferredSection fallback={fallback}>
      <Suspense fallback={fallback}>{children}</Suspense>
    </ViewportDeferredSection>
  );
}

function subtractUtcMonths(value: Date, months: number) {
  const monthIndex = value.getUTCFullYear() * 12 + value.getUTCMonth() - months;
  const targetYear = Math.floor(monthIndex / 12);
  const targetMonth = ((monthIndex % 12) + 12) % 12;
  const lastDayOfTargetMonth = new Date(Date.UTC(targetYear, targetMonth + 1, 0)).getUTCDate();
  const targetDay = Math.min(value.getUTCDate(), lastDayOfTargetMonth);

  return new Date(
    Date.UTC(
      targetYear,
      targetMonth,
      targetDay,
      value.getUTCHours(),
      value.getUTCMinutes(),
      value.getUTCSeconds(),
      value.getUTCMilliseconds(),
    ),
  );
}

interface CustomDateRange {
  startDate: string;
  endDate: string;
}

interface MetricsRangeQuery {
  start?: string;
  end?: string;
  allTime: boolean;
}

interface CopyTradeLoadedAccountData {
  summary: AccountSummary | null;
  calendarDays: AccountPnlCalendarDay[];
  trades: AccountTrade[];
  tradesError: string | null;
  error: string | null;
}

interface CompactAccountLoadState {
  summary: AccountSummary | null;
  days: AccountPnlCalendarDay[] | null;
  trades: AccountTrade[] | null;
  summaryLoading: boolean;
  daysLoading: boolean;
  tradesLoading: boolean;
  summaryError: string | null;
  daysError: string | null;
  tradesError: string | null;
}

function createCompactAccountLoadState(): CompactAccountLoadState {
  return {
    summary: null,
    days: null,
    trades: null,
    summaryLoading: true,
    daysLoading: true,
    tradesLoading: true,
    summaryError: null,
    daysError: null,
    tradesError: null,
  };
}

type CopyTradeToggleFeedbackTone = "neutral" | "success" | "error";

interface CopyTradeToggleFeedback {
  tone: CopyTradeToggleFeedbackTone;
  message: string;
}

interface CopyTradeSettingsSaveResult {
  ok: boolean;
  errorMessage?: string;
}

interface RefreshedPnlCalendarMonth {
  accountId: number;
  days: AccountPnlCalendarDay[];
}

interface PnlCalendarMonthRefreshResult {
  refreshed: RefreshedPnlCalendarMonth[];
  failedAccountIds: number[];
}

async function refreshPnlCalendarMonth(
  accountIds: readonly number[],
  window: PnlCalendarRefreshWindow,
): Promise<PnlCalendarMonthRefreshResult> {
  const results = await Promise.all(
    accountIds.map(async (accountId) => {
      try {
        const days = await accountsApi.getPnlCalendar(accountId, {
          start: window.start,
          end: window.end,
          all_time: false,
          refresh: true,
          automaticRefresh: true,
        });
        return { accountId, days };
      } catch {
        return null;
      }
    }),
  );

  return {
    refreshed: results.filter((result): result is RefreshedPnlCalendarMonth => result !== null),
    failedAccountIds: accountIds.filter(
      (accountId) => !results.some((result) => result?.accountId === accountId),
    ),
  };
}

function buildMetricsRangeQuery(
  range: MetricsRangePreset,
  customRange: CustomDateRange | null,
  currentTradingDay: string = tradingDayKey(new Date()),
): MetricsRangeQuery {
  if (range === "CUSTOM") {
    if (!customRange) {
      return { allTime: true };
    }
    const start = getTradingDayBoundaryIso(customRange.startDate, false);
    const end = getTradingDayBoundaryIso(customRange.endDate, true);
    if (!start || !end) {
      return { allTime: true };
    }
    return { start, end, allTime: false };
  }

  if (range === "ALL") {
    return { allTime: true };
  }

  const end = new Date();
  let start = new Date(end.getTime());

  switch (range) {
    case "1D":
      {
        const currentTradingDayRange = getTradingDayRange(currentTradingDay);
        if (currentTradingDayRange) {
          start = new Date(currentTradingDayRange.start);
        } else {
          start = new Date(end.getTime() - 24 * 60 * 60 * 1000);
        }
      }
      break;
    case "1W":
      start = new Date(end.getTime() - 7 * 24 * 60 * 60 * 1000);
      break;
    case "1M":
      start = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), 1, 0, 0, 0, 0));
      break;
    case "6M":
      start = subtractUtcMonths(end, 6);
      break;
    default:
      break;
  }

  return {
    start: start.toISOString(),
    end: end.toISOString(),
    allTime: false,
  };
}

function getCopyTradeSettingsSaveErrorMessage(error: unknown) {
  return error instanceof Error && error.message ? error.message : "Browser storage is unavailable.";
}

function getCurrentTradingMonthRefreshQuery(currentTradingDay: string): Pick<MetricsRangeQuery, "start" | "end"> {
  const [year, month] = currentTradingDay.split("-");
  const monthStart = year && month ? getTradingDayBoundaryIso(`${year}-${month}-01`, false) : null;
  return {
    start: monthStart ?? undefined,
    end: new Date().toISOString(),
  };
}

function getCopyTradeRefreshQuery(range: MetricsRangeQuery, currentTradingDay: string): Pick<MetricsRangeQuery, "start" | "end"> {
  if (range.allTime) {
    return getCurrentTradingMonthRefreshQuery(currentTradingDay);
  }

  return {
    start: range.start,
    end: range.end,
  };
}

async function refreshCopyTradeRange(accountIds: readonly number[], range: Pick<MetricsRangeQuery, "start" | "end">) {
  if (accountIds.length === 0) {
    return true;
  }

  const results = await Promise.all(
    accountIds.map(async (accountId) => {
      try {
        await accountsApi.refreshTrades(accountId, range, { automatic: true });
        return true;
      } catch {
        // Fall back to the existing local cache for accounts that cannot refresh.
        return false;
      }
    }),
  );
  return results.every(Boolean);
}

function DashboardLoadingState() {
  return (
    <div
      className="dashboard-surface space-y-5 pb-8"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <h1 className="sr-only">Trading Dashboard</h1>
      <div className="rounded-xl border border-app-border/80 bg-app-bg/45 p-5">
        <div className="flex items-center gap-3">
          <span className="relative flex h-3 w-3 shrink-0" aria-hidden="true">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-app-accent opacity-50" />
            <span className="relative inline-flex h-3 w-3 rounded-full bg-app-accent" />
          </span>
          <div>
            <p className="text-sm font-medium text-app-text">Loading dashboard...</p>
            <p className="mt-0.5 text-xs text-app-muted">Restoring your active account and saved trade data.</p>
          </div>
        </div>
      </div>

      <div className="space-y-5" aria-hidden="true">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-28 w-full" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, index) => (
            <Skeleton key={`dashboard-loading-card-${index}`} className="h-44 w-full" />
          ))}
        </div>
        <Skeleton className="h-72 w-full" />
      </div>
    </div>
  );
}

export function DashboardPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const shellContext = useOutletContext<AppShellOutletContext | null>();
  const standaloneCompactMode = useMemo(
    () => ({ enabled: isCompactModeEnabled(), setEnabled: setCompactModeEnabled }),
    [],
  );
  const compactMode = shellContext?.compactMode ?? standaloneCompactMode;
  const shellAccountsError = shellContext?.accountsError ?? null;
  const reloadShellAccounts = shellContext?.reloadAccounts;
  const usingShellAccounts = compactMode.enabled && shellContext != null;
  const accountFromQuery = parseAccountId(searchParams.get(ACCOUNT_QUERY_PARAM));
  const { demoModeEnabled } = useDemoInteractionPolicy();

  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [accountsLoading, setAccountsLoading] = useState(true);
  const [compactAccountsError, setCompactAccountsError] = useState<string | null>(null);
  const [providerAccountsRefreshing, setProviderAccountsRefreshing] = useState(false);
  const [providerRefreshError, setProviderRefreshError] = useState<string | null>(null);
  const [copyTradeSettings, setCopyTradeSettings] = useState<CopyTradeSettings>(() => readStoredCopyTradeSettings());
  const [copyTradeTogglePending, setCopyTradeTogglePending] = useState(false);
  const [copyTradeToggleFeedback, setCopyTradeToggleFeedback] = useState<CopyTradeToggleFeedback | null>(null);
  const [liveAccountSetupRequest, setLiveAccountSetupRequest] = useState(0);
  const [pendingLiveAccountSetup, setPendingLiveAccountSetup] = useState<{ accountId: number | null } | null>(null);
  const [tradeDataSourceFeedback, setTradeDataSourceFeedback] = useState<CopyTradeToggleFeedback | null>(null);
  const copyTradeSettingsRef = useRef(copyTradeSettings);
  const copyTradeTogglePendingRef = useRef(false);
  const copyTradeToggleTimerRef = useRef<number | null>(null);
  const lastProjectXAccountIdRef = useRef<number | null>(null);
  const beginAccountsRequest = useLatestRequestGuard();
  const beginProviderAccountsRequest = useLatestRequestGuard();
  const beginSummaryRequest = useLatestRequestGuard();
  const beginTradesRequest = useLatestRequestGuard();
  const beginJournalDaysRequest = useLatestRequestGuard();
  const beginMetricsTradesRequest = useLatestRequestGuard();
  const beginCompactAnalysisRequest = useLatestRequestGuard();
  const beginCompactCalendarRequest = useLatestRequestGuard();
  const beginCompactCopyRefreshRequest = useLatestRequestGuard();
  const beginCompactJournalDaysRequest = useLatestRequestGuard();
  const standardCalendarMonthRefreshAttemptedKeysRef = useRef(new Set<string>());
  const compactCalendarMonthRefreshAttemptedKeysRef = useRef(new Set<string>());
  const standardCalendarMonthRefreshCompletedKeysRef = useRef(new Set<string>());
  const compactCalendarMonthRefreshCompletedKeysRef = useRef(new Set<string>());
  const pendingCalendarMonthRefreshAccountIdsRef = useRef(new Set<number>());
  const calendarMonthRefreshLatestWarningGenerationRef = useRef(new Map<string, number>());
  const standardCopyTradeRangeRefreshedScopeKeyRef = useRef<string | null>(null);
  const compactCopyTradeRangeRefreshedScopeKeyRef = useRef<string | null>(null);

  const [summary, setSummary] = useState<AccountSummary>(emptySummary);
  const [pointPayoffByBasis, setPointPayoffByBasis] = useState<PointPayoffByBasis>(() => createEmptyPointPayoffByBasis());
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [metricsRange, setMetricsRange] = useState<MetricsRangePreset>("ALL");
  const [customStartDate, setCustomStartDate] = useState("");
  const [customEndDate, setCustomEndDate] = useState("");
  const [customRange, setCustomRange] = useState<CustomDateRange | null>(null);

  const [trades, setTrades] = useState<AccountTrade[]>([]);
  const [tradesLoading, setTradesLoading] = useState(false);
  const [tradesError, setTradesError] = useState<string | null>(null);
  const [recentTradesVisibleCount, setRecentTradesVisibleCount] = useState(RECENT_TRADES_RENDER_PAGE_SIZE);
  const [metricsTrades, setMetricsTrades] = useState<AccountTrade[]>([]);
  const [metricsTradesLoading, setMetricsTradesLoading] = useState(false);
  const [metricsTradesError, setMetricsTradesError] = useState<string | null>(null);

  const [pnlCalendarDays, setPnlCalendarDays] = useState<AccountPnlCalendarDay[]>([]);
  const [copyTradeAccountDataById, setCopyTradeAccountDataById] = useState<Record<number, CopyTradeLoadedAccountData>>({});
  const [pnlCalendarLoading, setPnlCalendarLoading] = useState(false);
  const [pnlCalendarError, setPnlCalendarError] = useState<string | null>(null);
  const [journalDays, setJournalDays] = useState<Set<string>>(new Set());
  const [journalDaysLoading, setJournalDaysLoading] = useState(false);
  const [journalDaysError, setJournalDaysError] = useState<string | null>(null);
  const [standardCalendarVisibleRange, setStandardCalendarVisibleRange] = useState<{
    startDate: string;
    endDate: string;
    scopeKey: string;
  } | null>(null);
  const [compactCalendarVisibleRange, setCompactCalendarVisibleRange] = useState<{
    startDate: string;
    endDate: string;
    scopeKey: string;
  } | null>(null);
  const [currentTradingDayKey, setCurrentTradingDayKey] = useState(() =>
    tradingDayKey(demoModeEnabled ? DEMO_AS_OF_ISO : new Date()),
  );
  const [compactAccountDataById, setCompactAccountDataById] = useState<Record<number, CompactAccountLoadState>>({});
  const [compactReloadNonce, setCompactReloadNonce] = useState(0);
  const [compactCopyRefreshVersion, setCompactCopyRefreshVersion] = useState(0);
  const [calendarMonthRefreshApplyVersion, setCalendarMonthRefreshApplyVersion] = useState(0);
  const [calendarMonthRefreshWarnings, setCalendarMonthRefreshWarnings] = useState<Record<string, string>>({});
  const compactAnalysisReloadHandledRef = useRef(0);
  const compactCalendarReloadHandledRef = useRef(0);
  const compactAnalysisStartedKeyRef = useRef<string | null>(null);
  const compactCalendarStartedKeyRef = useRef<string | null>(null);
  const compactCopyRefreshInFlightRef = useRef<{ key: string; promise: Promise<void> } | null>(null);
  const [compactJournalDays, setCompactJournalDays] = useState<Set<string>>(new Set());
  const [compactJournalDaysLoading, setCompactJournalDaysLoading] = useState(false);
  const [compactJournalDaysError, setCompactJournalDaysError] = useState<string | null>(null);
  const [compactJournalActionError, setCompactJournalActionError] = useState<string | null>(null);

  const setActiveAccount = useCallback(
    (accountId: number) => {
      const next = new URLSearchParams(searchParams);
      next.set(ACCOUNT_QUERY_PARAM, String(accountId));
      setSearchParams(next, { replace: true });
      writeStoredAccountId(accountId);
    },
    [searchParams, setSearchParams],
  );

  const loadAccounts = useCallback(async () => {
    const isCurrent = beginAccountsRequest();
    setAccountsLoading(true);
    setCompactAccountsError(null);
    try {
      const payload = await accountsApi.getSelectableAccountsLocalFirst();
      if (isCurrent()) {
        setAccounts(payload);
      }
    } catch (error) {
      if (isCurrent()) {
        setAccounts([]);
        setCompactAccountsError(error instanceof Error && error.message ? error.message : "Failed to load accounts");
      }
    } finally {
      if (isCurrent()) {
        setAccountsLoading(false);
      }
    }
  }, [beginAccountsRequest]);

  const handleLiveImportAccountCreated = useCallback(
    (account: AccountInfo) => {
      setPendingLiveAccountSetup(null);
      setAccounts((current) => [
        account,
        ...current.filter((candidate) => candidate.id !== account.id),
      ]);
      setActiveAccount(account.id);
      setTradeDataSourceFeedback(null);
    },
    [setActiveAccount],
  );

  useEffect(() => {
    if (usingShellAccounts) {
      return;
    }
    void loadAccounts();
  }, [loadAccounts, usingShellAccounts]);

  useEffect(() => {
    copyTradeSettingsRef.current = copyTradeSettings;
  }, [copyTradeSettings]);

  useEffect(() => {
    return () => {
      if (copyTradeToggleTimerRef.current !== null) {
        window.clearTimeout(copyTradeToggleTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    function syncCurrentTradingDayKey() {
      const nextTradingDayKey = tradingDayKey(demoModeEnabled ? DEMO_AS_OF_ISO : new Date());
      setCurrentTradingDayKey((current) => (current === nextTradingDayKey ? current : nextTradingDayKey));
    }

    syncCurrentTradingDayKey();
    if (demoModeEnabled) {
      return undefined;
    }
    const intervalId = window.setInterval(syncCurrentTradingDayKey, 60_000);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [demoModeEnabled]);

  const freshnessAwareAccounts = useAccountProviderFreshness(
    usingShellAccounts ? shellContext.accounts : accounts,
  );
  const orderedAccounts = useMemo(
    () => sortAccountsForActiveSelection(freshnessAwareAccounts),
    [freshnessAwareAccounts],
  );

  useEffect(() => {
    if (orderedAccounts.length === 0) {
      return;
    }

    if (accountFromQuery && orderedAccounts.some((account) => account.id === accountFromQuery)) {
      writeStoredAccountId(accountFromQuery);
      return;
    }

    const persistedMainAccountId = orderedAccounts.find((account) => account.is_main)?.id ?? null;
    if (persistedMainAccountId) {
      setActiveAccount(persistedMainAccountId);
      return;
    }

    const storedMainAccountId = readStoredMainAccountId();
    if (storedMainAccountId && orderedAccounts.some((account) => account.id === storedMainAccountId)) {
      setActiveAccount(storedMainAccountId);
      return;
    }

    const storedAccountId = readStoredAccountId();
    if (storedAccountId && orderedAccounts.some((account) => account.id === storedAccountId)) {
      setActiveAccount(storedAccountId);
      return;
    }

    setActiveAccount(orderedAccounts[0].id);
  }, [orderedAccounts, accountFromQuery, setActiveAccount]);

  const selectedAccount = useMemo(
    () => orderedAccounts.find((account) => account.id === accountFromQuery) ?? null,
    [orderedAccounts, accountFromQuery],
  );
  const selectedAccountId = selectedAccount?.id ?? null;
  const journalOpenRequestGate = useAccountRequestGate(selectedAccountId);
  const {
    selectedDate: activeSelectedTradeDate,
    setSelectedDate: handleSelectedTradeDateChange,
  } = useAccountScopedDaySelection(selectedAccountId);
  useEffect(() => {
    setCompactJournalActionError(null);
  }, [selectedAccountId]);
  const selectedAccountIsCsvImport = selectedAccount?.trade_data_source === "csv_import";
  const liveAccountModeEnabled = selectedAccountIsCsvImport
    || pendingLiveAccountSetup?.accountId === selectedAccountId;
  useEffect(() => {
    if (pendingLiveAccountSetup && pendingLiveAccountSetup.accountId !== selectedAccountId) {
      setPendingLiveAccountSetup(null);
    }
  }, [pendingLiveAccountSetup, selectedAccountId]);
  const liveCsvAccounts = useMemo(
    () => orderedAccounts.filter((account) => account.trade_data_source === "csv_import"),
    [orderedAccounts],
  );

  useEffect(() => {
    if (usingShellAccounts || demoModeEnabled || selectedAccount?.trade_data_source !== "projectx") {
      return;
    }

    const isCurrent = beginProviderAccountsRequest();
    setProviderAccountsRefreshing(true);
    setProviderRefreshError(null);

    void accountsApi
      .getSelectableAccounts({ refreshProvider: true })
      .then((payload) => {
        if (isCurrent()) {
          setAccounts(payload);
        }
      })
      .catch((error) => {
        if (isCurrent()) {
          setProviderRefreshError(describeProviderRefreshException(error));
        }
      })
      .finally(() => {
        if (isCurrent()) {
          setProviderAccountsRefreshing(false);
        }
      });
  }, [
    beginProviderAccountsRequest,
    demoModeEnabled,
    selectedAccount?.id,
    selectedAccount?.trade_data_source,
    usingShellAccounts,
  ]);

  const copyTradeModeConfigured =
    copyTradeSettings.modeEnabled && !selectedAccountIsCsvImport && !demoModeEnabled;
  const copyTradeModeActive = copyTradeModeConfigured && !compactMode.enabled;
  const compactCopyTradeModeActive = copyTradeModeConfigured && compactMode.enabled;
  const providerSyncNotice = useMemo(
    () => describeAccountProviderSync(summarizeAccountProviderSync(orderedAccounts)),
    [orderedAccounts],
  );
  const copyTradeRosterKey = useMemo(
    () =>
      computeCopyTradeWhenEnabled(copyTradeModeConfigured, EMPTY_COPY_TRADE_ACCOUNT_IDS, () =>
        getCopyTradeRosterAccountIds(orderedAccounts, selectedAccountId, copyTradeSettings),
      ).join(","),
    [copyTradeModeConfigured, copyTradeSettings, orderedAccounts, selectedAccountId],
  );
  const copyTradeRosterAccountIds = useMemo(
    () => copyTradeRosterKey ? copyTradeRosterKey.split(",").map(Number) : EMPTY_COPY_TRADE_ACCOUNT_IDS,
    [copyTradeRosterKey],
  );
  const copyTradeFollowerAccountIds = useMemo(
    () =>
      computeCopyTradeWhenEnabled(copyTradeModeConfigured, EMPTY_COPY_TRADE_ACCOUNT_IDS, () =>
        copyTradeRosterAccountIds.filter((accountId) => accountId !== selectedAccountId),
      ),
    [copyTradeModeConfigured, copyTradeRosterAccountIds, selectedAccountId],
  );
  const projectXAccountIds = useMemo(
    () => new Set(orderedAccounts.filter((account) => account.trade_data_source === "projectx").map((account) => account.id)),
    [orderedAccounts],
  );
  const standardCalendarRefreshAccountIds = useMemo(() => {
    if (selectedAccountId === null) {
      return [];
    }
    const requestedAccountIds = copyTradeModeActive ? copyTradeRosterAccountIds : [selectedAccountId];
    return requestedAccountIds.filter((accountId) => projectXAccountIds.has(accountId));
  }, [copyTradeModeActive, copyTradeRosterAccountIds, projectXAccountIds, selectedAccountId]);
  const standardCalendarScopeKey = useMemo(
    () =>
      [
        selectedAccountId ?? "none",
        copyTradeModeActive ? standardCalendarRefreshAccountIds.join(",") : "single",
        metricsRange,
        metricsRange === "CUSTOM" ? `${customRange?.startDate ?? ""}:${customRange?.endDate ?? ""}` : "",
      ].join("|"),
    [
      copyTradeModeActive,
      customRange?.endDate,
      customRange?.startDate,
      metricsRange,
      selectedAccountId,
      standardCalendarRefreshAccountIds,
    ],
  );
  const metricsRangeQuery = useMemo(
    () => buildMetricsRangeQuery(metricsRange, customRange, currentTradingDayKey),
    [customRange, currentTradingDayKey, metricsRange],
  );
  const selectedTradeDayRangeQuery = useMemo<MetricsRangeQuery | null>(() => {
    if (!activeSelectedTradeDate) {
      return null;
    }
    const selectedRange = getTradingDayRange(activeSelectedTradeDate);
    return selectedRange ? { ...selectedRange, allTime: false } : null;
  }, [activeSelectedTradeDate]);
  const analyticsRangeQuery = selectedTradeDayRangeQuery ?? metricsRangeQuery;
  const selectedTradeDayRefresh = selectedTradeDayRangeQuery !== null;
  const customRangeInvalid = customStartDate !== "" && customEndDate !== "" && customStartDate > customEndDate;

  useEffect(() => {
    if (selectedAccount?.trade_data_source === "projectx") {
      lastProjectXAccountIdRef.current = selectedAccount.id;
    }
  }, [selectedAccount]);

  function updateCustomRangeFromDraft(startDate: string, endDate: string) {
    const resolution = resolveCustomDateRangeDraft(startDate, endDate, metricsRange === "CUSTOM");
    setCustomRange(resolution.appliedRange);
    if (resolution.nextMode === "CUSTOM") {
      setMetricsRange("CUSTOM");
      handleSelectedTradeDateChange(null);
    } else if (resolution.nextMode === "ALL") {
      setMetricsRange("ALL");
      handleSelectedTradeDateChange(null);
    }
  }
  const dashboardLoadPerfRef = useRef<{
    accountId: number;
    startedAtMs: number;
    startedAtIso: string;
  } | null>(null);
  const dashboardWasLoadingRef = useRef(false);

  useEffect(() => {
    dashboardLoadPerfRef.current = null;
    dashboardWasLoadingRef.current = false;
  }, [activeSelectedTradeDate, analyticsRangeQuery.end, analyticsRangeQuery.start, selectedAccountId]);

  const selectedTradeDateLabel = useMemo(() => {
    if (!activeSelectedTradeDate) {
      return null;
    }
    return dateFormatter.format(parseIsoDay(activeSelectedTradeDate));
  }, [activeSelectedTradeDate]);

  const loadSummaryAndCalendar = useCallback(async (options: { skipCopyTradeRefresh?: boolean } = {}) => {
    const isCurrent = beginSummaryRequest();
    if (compactMode.enabled) {
      setSummaryLoading(false);
      setPnlCalendarLoading(false);
      setSummary(emptySummary);
      setPointPayoffByBasis(createEmptyPointPayoffByBasis());
      setSummaryError(null);
      setPnlCalendarDays([]);
      setCopyTradeAccountDataById({});
      setPnlCalendarError(null);
      return;
    }
    if (!selectedAccountId) {
      setSummaryLoading(false);
      setPnlCalendarLoading(false);
      setSummary(emptySummary);
      setPointPayoffByBasis(createEmptyPointPayoffByBasis());
      setSummaryError(null);
      setPnlCalendarDays([]);
      setCopyTradeAccountDataById({});
      setPnlCalendarError(null);
      return;
    }

    setSummaryLoading(true);
    setPnlCalendarLoading(true);
    setSummaryError(null);
    setPnlCalendarError(null);

    try {
      if (copyTradeModeActive && !options.skipCopyTradeRefresh) {
        const fullyRefreshed = await refreshCopyTradeRange(
          copyTradeRosterAccountIds,
          getCopyTradeRefreshQuery(metricsRangeQuery, currentTradingDayKey),
        );
        standardCopyTradeRangeRefreshedScopeKeyRef.current = fullyRefreshed ? standardCalendarScopeKey : null;
      }

      const summaryQuery = {
        start: analyticsRangeQuery.start,
        end: analyticsRangeQuery.end,
        refresh: selectedTradeDayRefresh,
      };
      const followerAccountIds = copyTradeModeActive
        ? copyTradeRosterAccountIds.filter((accountId) => accountId !== selectedAccountId)
        : [];
      const followerResultsRequest = Promise.all(
        followerAccountIds.map(async (accountId) => {
          const followerTradesRequest = accountsApi
            .getTrades(accountId, {
              limit: METRIC_TRADE_LIMIT,
              start: analyticsRangeQuery.start,
              end: analyticsRangeQuery.end,
              refresh: selectedTradeDayRefresh,
              includeLifecycle: false,
            })
            .then(
              (trades) => ({ trades, error: null }),
              (err) => ({
                trades: [],
                error: err instanceof Error ? err.message : "Failed to load follower trade history",
              }),
            );

          try {
            const [summaryBundle, calendarDays, followerTrades] = await Promise.all([
              accountsApi.getSummaryWithPointBases(accountId, {
                start: summaryQuery.start,
                end: summaryQuery.end,
                refresh: summaryQuery.refresh,
              }),
              accountsApi.getPnlCalendar(accountId, {
                start: metricsRangeQuery.start,
                end: metricsRangeQuery.end,
                all_time: metricsRangeQuery.allTime,
              }),
              followerTradesRequest,
            ]);

            return {
              accountId,
              data: {
                summary: summaryBundle.summary,
                calendarDays,
                trades: followerTrades.trades,
                tradesError: followerTrades.error,
                error: null,
              } satisfies CopyTradeLoadedAccountData,
            };
          } catch (err) {
            return {
              accountId,
              data: {
                summary: null,
                calendarDays: [],
                trades: [],
                tradesError: null,
                error: err instanceof Error ? err.message : "Failed to load copy-trade account data",
              } satisfies CopyTradeLoadedAccountData,
            };
          }
        }),
      );

      const [nextSummaryBundle, nextPnlCalendar, followerResults] = await Promise.all([
        accountsApi.getSummaryWithPointBases(selectedAccountId, {
          start: summaryQuery.start,
          end: summaryQuery.end,
          refresh: summaryQuery.refresh,
        }),
        accountsApi.getPnlCalendar(selectedAccountId, {
          start: metricsRangeQuery.start,
          end: metricsRangeQuery.end,
          all_time: metricsRangeQuery.allTime,
        }),
        followerResultsRequest,
      ]);

      if (!isCurrent()) {
        return;
      }

      const nextPointPayoffByBasis = createEmptyPointPayoffByBasis();
      PAYOFF_POINTS_BASES.forEach((basis) => {
        const pointPayoff = nextSummaryBundle.point_payoff_by_basis[basis];
        if (!pointPayoff) {
          return;
        }
        nextPointPayoffByBasis[basis] = {
          avgPointGain: pointPayoff.avgPointGain,
          avgPointLoss: pointPayoff.avgPointLoss,
        };
      });

      setSummary(nextSummaryBundle.summary);
      setPointPayoffByBasis(nextPointPayoffByBasis);
      setPnlCalendarDays(nextPnlCalendar);

      if (copyTradeModeActive) {
        const nextCopyTradeAccountDataById: Record<number, CopyTradeLoadedAccountData> = {
          [selectedAccountId]: {
            summary: nextSummaryBundle.summary,
            calendarDays: nextPnlCalendar,
            trades: [],
            tradesError: null,
            error: null,
          },
        };

        followerResults.forEach(({ accountId, data }) => {
          nextCopyTradeAccountDataById[accountId] = data;
        });

        setCopyTradeAccountDataById(nextCopyTradeAccountDataById);
      } else {
        setCopyTradeAccountDataById({});
      }
    } catch (err) {
      if (!isCurrent()) {
        return;
      }
      const message = err instanceof Error ? err.message : "Failed to load dashboard data";
      setSummaryError(message);
      setPnlCalendarError(message);
      setSummary(emptySummary);
      setPointPayoffByBasis(createEmptyPointPayoffByBasis());
      setPnlCalendarDays([]);
      setCopyTradeAccountDataById({});
    } finally {
      if (isCurrent()) {
        setSummaryLoading(false);
        setPnlCalendarLoading(false);
      }
    }
  }, [
    analyticsRangeQuery.end,
    analyticsRangeQuery.start,
    copyTradeRosterAccountIds,
    copyTradeModeActive,
    currentTradingDayKey,
    metricsRangeQuery,
    selectedAccountId,
    selectedTradeDayRefresh,
    standardCalendarScopeKey,
    beginSummaryRequest,
    compactMode.enabled,
  ]);

  const loadTrades = useCallback(async () => {
    const isCurrent = beginTradesRequest();
    setRecentTradesVisibleCount(RECENT_TRADES_RENDER_PAGE_SIZE);
    if (compactMode.enabled) {
      setTradesLoading(false);
      setTrades([]);
      setTradesError(null);
      return;
    }
    if (!selectedAccountId) {
      setTradesLoading(false);
      setTrades([]);
      setTradesError(null);
      return;
    }

    setTradesLoading(true);
    setTradesError(null);

    try {
      const selectedRange = activeSelectedTradeDate ? getTradingDayRange(activeSelectedTradeDate) : null;
      const query = selectedRange
        ? { limit: DAY_FILTER_TRADE_LIMIT, ...selectedRange, refresh: true }
        : { limit: TRADE_LIMIT };
      const nextTrades = await accountsApi.getTrades(selectedAccountId, query);
      if (isCurrent()) {
        setTrades(nextTrades);
      }
    } catch (err) {
      if (!isCurrent()) {
        return;
      }
      const message = err instanceof Error ? err.message : "Failed to load trade events";
      setTradesError(message);
      setTrades([]);
    } finally {
      if (isCurrent()) {
        setTradesLoading(false);
      }
    }
  }, [activeSelectedTradeDate, beginTradesRequest, compactMode.enabled, selectedAccountId]);

  const loadJournalDays = useCallback(async () => {
    const isCurrent = beginJournalDaysRequest();
    if (compactMode.enabled) {
      setJournalDaysLoading(false);
      setJournalDays(new Set());
      setJournalDaysError(null);
      return;
    }
    if (
      !selectedAccountId ||
      !standardCalendarVisibleRange ||
      standardCalendarVisibleRange.scopeKey !== standardCalendarScopeKey
    ) {
      setJournalDaysLoading(false);
      setJournalDays(new Set());
      setJournalDaysError(null);
      return;
    }

    setJournalDaysLoading(true);
    setJournalDaysError(null);
    try {
      const payload = await accountsApi.getJournalDays(selectedAccountId, {
        start_date: standardCalendarVisibleRange.startDate,
        end_date: standardCalendarVisibleRange.endDate,
      });
      if (isCurrent()) {
        setJournalDays(new Set(payload.days));
      }
    } catch (err) {
      if (isCurrent()) {
        setJournalDays(new Set());
        setJournalDaysError(err instanceof Error ? err.message : "Failed to load journal markers");
      }
    } finally {
      if (isCurrent()) {
        setJournalDaysLoading(false);
      }
    }
  }, [
    beginJournalDaysRequest,
    compactMode.enabled,
    selectedAccountId,
    standardCalendarScopeKey,
    standardCalendarVisibleRange,
  ]);

  const loadMetricsTrades = useCallback(async () => {
    const isCurrent = beginMetricsTradesRequest();
    if (compactMode.enabled || !selectedAccountId) {
      setMetricsTrades([]);
      setMetricsTradesError(null);
      setMetricsTradesLoading(false);
      return;
    }

    setMetricsTradesLoading(true);
    setMetricsTradesError(null);

    try {
      const nextTrades = await accountsApi.getTrades(selectedAccountId, {
        limit: METRIC_TRADE_LIMIT,
        start: analyticsRangeQuery.start,
        end: analyticsRangeQuery.end,
        refresh: selectedTradeDayRefresh,
        includeLifecycle: false,
      });
      if (isCurrent()) {
        setMetricsTrades(nextTrades);
      }
    } catch (err) {
      if (!isCurrent()) {
        return;
      }
      const message = err instanceof Error ? err.message : "Failed to load directional trade history";
      setMetricsTradesError(message);
      setMetricsTrades([]);
    } finally {
      if (isCurrent()) {
        setMetricsTradesLoading(false);
      }
    }
  }, [analyticsRangeQuery.end, analyticsRangeQuery.start, beginMetricsTradesRequest, compactMode.enabled, selectedAccountId, selectedTradeDayRefresh]);

  const reloadDashboard = useCallback(async () => {
    setCompactReloadNonce((current) => current + 1);
    if (compactMode.enabled) {
      return;
    }
    await Promise.all([loadSummaryAndCalendar(), loadTrades(), loadMetricsTrades()]);
  }, [compactMode.enabled, loadMetricsTrades, loadSummaryAndCalendar, loadTrades]);

  useEffect(() => {
    void loadSummaryAndCalendar();
  }, [loadSummaryAndCalendar]);

  useEffect(() => {
    void loadTrades();
  }, [loadTrades]);

  useEffect(() => {
    void loadMetricsTrades();
  }, [loadMetricsTrades]);

  useEffect(() => {
    void loadJournalDays();
  }, [loadJournalDays]);

  useEffect(() => {
    const isDashboardLoading =
      summaryLoading || pnlCalendarLoading || tradesLoading || (!compactMode.enabled && metricsTradesLoading) || journalDaysLoading;
    if (!selectedAccountId) {
      dashboardWasLoadingRef.current = false;
      dashboardLoadPerfRef.current = null;
      return;
    }

    if (isDashboardLoading && !dashboardWasLoadingRef.current) {
      const startedAtIso = new Date().toISOString();
      dashboardLoadPerfRef.current = {
        accountId: selectedAccountId,
        startedAtMs: performance.now(),
        startedAtIso,
      };
      dashboardWasLoadingRef.current = true;
      logPerfInfo("[perf][dashboard] load-start", {
        account_id: selectedAccountId,
        started_at: startedAtIso,
      });
      return;
    }

    if (isDashboardLoading || !dashboardWasLoadingRef.current) {
      return;
    }

    dashboardWasLoadingRef.current = false;
    const loadPerf = dashboardLoadPerfRef.current;
    if (!loadPerf || loadPerf.accountId !== selectedAccountId) {
      return;
    }
    const totalMs = Math.max(performance.now() - loadPerf.startedAtMs, 0);
    logPerfInfo("[perf][dashboard] load-end", {
      account_id: selectedAccountId,
      started_at: loadPerf.startedAtIso,
      finished_at: new Date().toISOString(),
      total_ms: Number(totalMs.toFixed(2)),
      summary_error: summaryError,
      pnl_calendar_error: pnlCalendarError,
      trades_error: tradesError,
      metrics_trades_error: metricsTradesError,
      journal_days_error: journalDaysError,
      trades_count: trades.length,
      metrics_trades_count: metricsTrades.length,
      pnl_calendar_day_count: pnlCalendarDays.length,
      journal_day_count: journalDays.size,
    });
    dashboardLoadPerfRef.current = null;
  }, [
    compactMode.enabled,
    journalDays.size,
    journalDaysError,
    journalDaysLoading,
    metricsTrades.length,
    metricsTradesError,
    metricsTradesLoading,
    pnlCalendarDays.length,
    pnlCalendarError,
    pnlCalendarLoading,
    selectedAccountId,
    summaryError,
    summaryLoading,
    trades.length,
    tradesError,
    tradesLoading,
  ]);

  useEffect(() => {
    handleSelectedTradeDateChange(null);
  }, [handleSelectedTradeDateChange, metricsRange]);

  useEffect(() => {
    function handleAccountTradesSynced(event: Event) {
      const detail = (event as CustomEvent<AccountTradesSyncedDetail>).detail;
      const matchesVisibleAccount =
        detail.accountId === selectedAccountId ||
        (compactCopyTradeModeActive && copyTradeRosterAccountIds.includes(detail.accountId));
      if (!selectedAccountId || !matchesVisibleAccount || detail.error) {
        return;
      }
      void reloadDashboard();
    }

    window.addEventListener(ACCOUNT_TRADES_SYNCED_EVENT, handleAccountTradesSynced as EventListener);
    return () => {
      window.removeEventListener(ACCOUNT_TRADES_SYNCED_EVENT, handleAccountTradesSynced as EventListener);
    };
  }, [compactCopyTradeModeActive, copyTradeRosterAccountIds, reloadDashboard, selectedAccountId]);

  const copyTradeSnapshotsByAccountId = useMemo<Record<number, CopyTradeMetricSnapshot | undefined>>(
    () =>
      computeCopyTradeWhenEnabled(copyTradeModeActive, EMPTY_COPY_TRADE_SNAPSHOTS, () => {
        const snapshots: Record<number, CopyTradeMetricSnapshot | undefined> = {};
        const accountById = new Map(orderedAccounts.map((account) => [account.id, account]));

        Object.entries(copyTradeAccountDataById).forEach(([accountId, data]) => {
          const account = accountById.get(Number(accountId));
          snapshots[Number(accountId)] = {
            netPnl: data.summary?.net_pnl ?? 0,
            dailyPnl: getCopyTradeDailyNetPnlForTradingDay(
              data.calendarDays,
              currentTradingDayKey,
              data.summary,
              account?.last_trade_at,
              account?.balance,
            ),
            openPositions: 0,
            loadError: data.error,
          };
        });

        return snapshots;
      }),
    [copyTradeAccountDataById, copyTradeModeActive, currentTradingDayKey, orderedAccounts],
  );

  const copyTradeRows = useMemo(
    () =>
      computeCopyTradeWhenEnabled(copyTradeModeActive, EMPTY_COPY_TRADE_ROWS, () =>
        buildCopyTradeAccountRows({
          accounts: orderedAccounts,
          leaderAccountId: selectedAccountId,
          followerAccountIds: copyTradeFollowerAccountIds,
          snapshotsByAccountId: copyTradeSnapshotsByAccountId,
        }),
      ),
    [copyTradeFollowerAccountIds, copyTradeModeActive, copyTradeSnapshotsByAccountId, orderedAccounts, selectedAccountId],
  );

  const copyTradeTotals = useMemo(
    () =>
      computeCopyTradeWhenEnabled(copyTradeModeActive, EMPTY_COPY_TRADE_TOTALS, () =>
        computeCopyTradeTotals(copyTradeRows),
      ),
    [copyTradeModeActive, copyTradeRows],
  );
  const copyTradeCalendarDays = useMemo(
    () =>
      computeCopyTradeWhenEnabled(copyTradeModeActive, EMPTY_COPY_TRADE_CALENDAR_DAYS, () => {
        const calendarDaysByAccountId: Record<number, AccountPnlCalendarDay[] | undefined> = {};
        Object.entries(copyTradeAccountDataById).forEach(([accountId, data]) => {
          calendarDaysByAccountId[Number(accountId)] = data.calendarDays;
        });
        return combineCopyTradePnlCalendarDays(copyTradeRows, calendarDaysByAccountId);
      }),
    [copyTradeAccountDataById, copyTradeModeActive, copyTradeRows],
  );
  const copyTradeTradesByAccountId = useMemo(
    () =>
      computeCopyTradeWhenEnabled(copyTradeModeActive, EMPTY_COPY_TRADE_TRADES_BY_ACCOUNT_ID, () => {
        const tradesByAccountId: Record<number, AccountTrade[] | undefined> = {};
        Object.entries(copyTradeAccountDataById).forEach(([accountId, data]) => {
          tradesByAccountId[Number(accountId)] = data.trades;
        });
        if (selectedAccountId !== null) {
          tradesByAccountId[selectedAccountId] = metricsTrades;
        }
        return tradesByAccountId;
      }),
    [copyTradeAccountDataById, copyTradeModeActive, metricsTrades, selectedAccountId],
  );
  const copyTradeDriftResetAt = useMemo(
    () =>
      computeCopyTradeWhenEnabled(copyTradeModeActive, null, () =>
        getCopyTradeUncopyEventsResetAt(copyTradeSettings, selectedAccountId),
      ),
    [copyTradeModeActive, copyTradeSettings, selectedAccountId],
  );
  const copyTradeDriftSummary = useMemo(
    () =>
      computeCopyTradeWhenEnabled(copyTradeModeActive, EMPTY_COPY_TRADE_DRIFT_SUMMARY, () =>
        computeCopyTradeDriftSummary(copyTradeRows, copyTradeTradesByAccountId, { resetAt: copyTradeDriftResetAt }),
      ),
    [copyTradeDriftResetAt, copyTradeModeActive, copyTradeRows, copyTradeTradesByAccountId],
  );
  const copyTradeStatsActive = copyTradeModeActive && copyTradeTotals.canCalculate;
  const compactRequestedAccountIds = useMemo(() => {
    if (!compactMode.enabled || selectedAccountId === null) {
      return [];
    }
    return compactCopyTradeModeActive ? copyTradeRosterAccountIds : [selectedAccountId];
  }, [compactCopyTradeModeActive, compactMode.enabled, copyTradeRosterAccountIds, selectedAccountId]);
  const compactCalendarRefreshAccountIds = useMemo(
    () => compactRequestedAccountIds.filter((accountId) => projectXAccountIds.has(accountId)),
    [compactRequestedAccountIds, projectXAccountIds],
  );
  const compactCalendarScopeKey = useMemo(
    () =>
      [
        selectedAccountId ?? "none",
        compactCopyTradeModeActive ? compactRequestedAccountIds.join(",") : "single",
        metricsRange,
        metricsRange === "CUSTOM" ? `${customRange?.startDate ?? ""}:${customRange?.endDate ?? ""}` : "",
      ].join("|"),
    [
      compactCopyTradeModeActive,
      compactRequestedAccountIds,
      customRange?.endDate,
      customRange?.startDate,
      metricsRange,
      selectedAccountId,
    ],
  );
  const compactContextAsOfKey = [
    compactMode.enabled ? "compact" : "standard",
    selectedAccountId ?? "none",
    metricsRange,
    customRange?.startDate ?? "",
    customRange?.endDate ?? "",
    currentTradingDayKey,
    compactReloadNonce,
  ].join("|");
  const compactContextAsOf = useMemo(() => {
    void compactContextAsOfKey;
    return new Date(demoModeEnabled ? DEMO_AS_OF_ISO : Date.now());
  }, [compactContextAsOfKey, demoModeEnabled]);
  const compactCalendarContextScope = useMemo(() => {
    if (!compactMode.enabled || selectedAccountId === null) {
      return null;
    }
    return buildCompactDashboardScopes({
      range: metricsRange,
      customRange,
      currentTradingDay: currentTradingDayKey,
      selectedDate: null,
      asOf: compactContextAsOf,
    }).calendarContextScope;
  }, [
    compactContextAsOf,
    compactMode.enabled,
    currentTradingDayKey,
    customRange,
    metricsRange,
    selectedAccountId,
  ]);
  const compactAnalysisScope = useMemo(() => {
    if (compactCalendarContextScope === null) {
      return null;
    }
    if (activeSelectedTradeDate === null) {
      return compactCalendarContextScope;
    }
    return buildCompactDashboardScopes({
      range: metricsRange,
      customRange,
      currentTradingDay: currentTradingDayKey,
      selectedDate: activeSelectedTradeDate,
      asOf: compactContextAsOf,
    }).analysisScope;
  }, [
    activeSelectedTradeDate,
    compactCalendarContextScope,
    compactContextAsOf,
    currentTradingDayKey,
    customRange,
    metricsRange,
  ]);
  const compactScopes = useMemo<CompactDashboardScopes | null>(
    () =>
      compactAnalysisScope && compactCalendarContextScope
        ? {
            analysisScope: compactAnalysisScope,
            calendarContextScope: compactCalendarContextScope,
          }
        : null,
    [compactAnalysisScope, compactCalendarContextScope],
  );

  useEffect(() => {
    if (!compactCopyTradeModeActive || compactCalendarContextScope === null) {
      beginCompactCopyRefreshRequest();
      compactCopyRefreshInFlightRef.current = null;
      compactCopyTradeRangeRefreshedScopeKeyRef.current = null;
      return;
    }
    const refreshKey = [
      selectedAccountId ?? "none",
      compactRequestedAccountIds.join(","),
      compactCalendarContextScope.key,
      compactReloadNonce,
    ].join("|");
    const coverageKey = [
      selectedAccountId ?? "none",
      compactRequestedAccountIds.join(","),
      compactCalendarContextScope.key,
    ].join("|");
    if (compactCopyRefreshInFlightRef.current?.key === refreshKey) {
      return;
    }
    const isCurrent = beginCompactCopyRefreshRequest();
    const promise = refreshCompactCopyThenInvalidate(
      async () => {
        const fullyRefreshed = await refreshCopyTradeRange(compactRequestedAccountIds, {
          start: compactCalendarContextScope.start,
          end: compactCalendarContextScope.end,
        });
        if (isCurrent()) {
          compactCopyTradeRangeRefreshedScopeKeyRef.current = fullyRefreshed ? coverageKey : null;
        }
      },
      () => {
        if (isCurrent()) {
          setCompactCopyRefreshVersion((current) => current + 1);
        }
      },
    );
    compactCopyRefreshInFlightRef.current = { key: refreshKey, promise };
    void promise.finally(() => {
      if (compactCopyRefreshInFlightRef.current?.promise === promise) {
        compactCopyRefreshInFlightRef.current = null;
      }
    });
  }, [
    beginCompactCopyRefreshRequest,
    compactCalendarContextScope,
    compactCopyTradeModeActive,
    compactReloadNonce,
    compactRequestedAccountIds,
    selectedAccountId,
  ]);

  const loadCompactAnalysisData = useCallback(async () => {
    if (!compactMode.enabled || selectedAccountId === null || compactScopes === null) {
      compactAnalysisStartedKeyRef.current = null;
      beginCompactAnalysisRequest();
      setCompactAccountDataById({});
      return;
    }
    const loadKey = [
      selectedAccountId,
      compactRequestedAccountIds.join(","),
      compactScopes.analysisScope.key,
      compactReloadNonce,
      compactCopyRefreshVersion,
    ].join("|");
    if (compactAnalysisStartedKeyRef.current === loadKey) {
      return;
    }
    compactAnalysisStartedKeyRef.current = loadKey;
    const isCurrent = beginCompactAnalysisRequest();

    // A copied-account sync has already invalidated changed local reads. Loading
    // its results must not launch another provider sync through each GET route.
    const forceRefresh = compactReloadNonce > compactAnalysisReloadHandledRef.current;
    const requestPlan = buildCompactAccountRequestPlan(compactScopes, { forceRefresh });
    setCompactAccountDataById((current) =>
      Object.fromEntries(
        compactRequestedAccountIds.map((accountId) => {
          const state = current[accountId] ?? createCompactAccountLoadState();
          return [
            accountId,
            {
              ...state,
              summary: null,
              trades: null,
              summaryLoading: true,
              tradesLoading: true,
              summaryError: null,
              tradesError: null,
            },
          ];
        }),
      ),
    );

    const updateAccountState = (accountId: number, patch: Partial<CompactAccountLoadState>) => {
      if (!isCurrent()) {
        return;
      }
      setCompactAccountDataById((current) => ({
        ...current,
        [accountId]: {
          ...(current[accountId] ?? createCompactAccountLoadState()),
          ...patch,
        },
      }));
    };
    const errorMessage = (error: unknown, fallback: string) =>
      error instanceof Error && error.message ? error.message : fallback;

    await Promise.all(
      compactRequestedAccountIds.flatMap((accountId) => [
        accountsApi.getSummaryWithPointBases(accountId, requestPlan.summary).then(
          (bundle) => updateAccountState(accountId, { summary: bundle.summary, summaryLoading: false }),
          (error) =>
            updateAccountState(accountId, {
              summary: null,
              summaryLoading: false,
              summaryError: errorMessage(error, "Failed to load Compact summary"),
            }),
        ),
        accountsApi.getTrades(accountId, requestPlan.trades).then(
          (nextTrades) => updateAccountState(accountId, { trades: nextTrades, tradesLoading: false }),
          (error) =>
            updateAccountState(accountId, {
              trades: null,
              tradesLoading: false,
              tradesError: errorMessage(error, "Failed to load Compact trades"),
            }),
        ),
      ]),
    );
    if (isCurrent()) {
      compactAnalysisReloadHandledRef.current = compactReloadNonce;
    }
  }, [
    beginCompactAnalysisRequest,
    compactMode.enabled,
    compactCopyRefreshVersion,
    compactReloadNonce,
    compactRequestedAccountIds,
    compactScopes,
    selectedAccountId,
  ]);

  const loadCompactCalendarData = useCallback(async () => {
    const calendarScope = compactCalendarContextScope;
    if (!compactMode.enabled || selectedAccountId === null || calendarScope === null) {
      compactCalendarStartedKeyRef.current = null;
      beginCompactCalendarRequest();
      setCompactAccountDataById({});
      return;
    }
    const loadKey = [
      selectedAccountId,
      compactRequestedAccountIds.join(","),
      calendarScope.key,
      compactReloadNonce,
      compactCopyRefreshVersion,
    ].join("|");
    if (compactCalendarStartedKeyRef.current === loadKey) {
      return;
    }
    compactCalendarStartedKeyRef.current = loadKey;
    const isCurrent = beginCompactCalendarRequest();

    const forceRefresh = compactReloadNonce > compactCalendarReloadHandledRef.current;
    const contextScopes: CompactDashboardScopes = {
      analysisScope: calendarScope,
      calendarContextScope: calendarScope,
    };
    const requestPlan = buildCompactAccountRequestPlan(contextScopes, { forceRefresh });
    setCompactAccountDataById((current) =>
      Object.fromEntries(
        compactRequestedAccountIds.map((accountId) => {
          const state = current[accountId] ?? createCompactAccountLoadState();
          return [
            accountId,
            {
              ...state,
              days: null,
              daysLoading: true,
              daysError: null,
            },
          ];
        }),
      ),
    );

    const updateAccountState = (accountId: number, patch: Partial<CompactAccountLoadState>) => {
      if (!isCurrent()) {
        return;
      }
      setCompactAccountDataById((current) => ({
        ...current,
        [accountId]: {
          ...(current[accountId] ?? createCompactAccountLoadState()),
          ...patch,
        },
      }));
    };
    const errorMessage = (error: unknown) =>
      error instanceof Error && error.message ? error.message : "Failed to load Compact performance days";

    await Promise.all(
      compactRequestedAccountIds.map((accountId) =>
        accountsApi.getPnlCalendar(accountId, requestPlan.calendar).then(
          (days) => updateAccountState(accountId, { days, daysLoading: false }),
          (error) =>
            updateAccountState(accountId, {
              days: null,
              daysLoading: false,
              daysError: errorMessage(error),
            }),
        ),
      ),
    );
    if (isCurrent()) {
      compactCalendarReloadHandledRef.current = compactReloadNonce;
    }
  }, [
    beginCompactCalendarRequest,
    compactCopyRefreshVersion,
    compactMode.enabled,
    compactReloadNonce,
    compactRequestedAccountIds,
    compactCalendarContextScope,
    selectedAccountId,
  ]);

  useEffect(() => {
    void loadCompactAnalysisData();
  }, [loadCompactAnalysisData]);

  useEffect(() => {
    void loadCompactCalendarData();
  }, [loadCompactCalendarData]);

  const loadCompactJournalDays = useCallback(async () => {
    const isCurrent = beginCompactJournalDaysRequest();
    if (
      !compactMode.enabled ||
      !selectedAccountId ||
      !compactCalendarVisibleRange ||
      compactCalendarVisibleRange.scopeKey !== compactCalendarScopeKey
    ) {
      setCompactJournalDays(new Set());
      setCompactJournalDaysLoading(false);
      setCompactJournalDaysError(null);
      return;
    }

    setCompactJournalDays(new Set());
    setCompactJournalDaysLoading(true);
    setCompactJournalDaysError(null);
    try {
      const payload = await accountsApi.getJournalDays(selectedAccountId, {
        start_date: compactCalendarVisibleRange.startDate,
        end_date: compactCalendarVisibleRange.endDate,
      });
      if (isCurrent()) {
        setCompactJournalDays(new Set(payload.days));
      }
    } catch (error) {
      if (isCurrent()) {
        setCompactJournalDays(new Set());
        setCompactJournalDaysError(
          error instanceof Error && error.message ? error.message : "Failed to load Compact journal markers",
        );
      }
    } finally {
      if (isCurrent()) {
        setCompactJournalDaysLoading(false);
      }
    }
  }, [
    beginCompactJournalDaysRequest,
    compactCalendarScopeKey,
    compactCalendarVisibleRange,
    compactMode.enabled,
    selectedAccountId,
  ]);

  useEffect(() => {
    void loadCompactJournalDays();
  }, [loadCompactJournalDays]);

  const compactPrimaryState = selectedAccountId === null ? undefined : compactAccountDataById[selectedAccountId];
  const compactSummaryLoading =
    selectedAccountId !== null &&
    compactRequestedAccountIds.some((accountId) => compactAccountDataById[accountId]?.summaryLoading !== false);
  const compactDaysLoading =
    selectedAccountId !== null &&
    compactRequestedAccountIds.some((accountId) => compactAccountDataById[accountId]?.daysLoading !== false);
  const compactTradesLoading =
    selectedAccountId !== null &&
    compactRequestedAccountIds.some((accountId) => compactAccountDataById[accountId]?.tradesLoading !== false);
  const compactSummaryError = compactPrimaryState?.summaryError ?? null;
  const compactDaysError = compactPrimaryState?.daysError ?? null;
  const compactTradesError = compactPrimaryState?.tradesError ?? null;
  const compactSummary = useMemo(() => {
    if (compactSummaryError) {
      return emptySummary;
    }
    if (!compactCopyTradeModeActive) {
      return compactPrimaryState?.summary ?? emptySummary;
    }
    return (
      combineCompactSummaries(
        compactRequestedAccountIds.flatMap((accountId) => {
          const nextSummary = compactAccountDataById[accountId]?.summary;
          return nextSummary ? [nextSummary] : [];
        }),
      ) ?? emptySummary
    );
  }, [
    compactAccountDataById,
    compactCopyTradeModeActive,
    compactPrimaryState?.summary,
    compactRequestedAccountIds,
    compactSummaryError,
  ]);
  const compactCalendarDays = useMemo(() => {
    if (compactDaysError) {
      return [];
    }
    if (!compactCopyTradeModeActive) {
      return [...(compactPrimaryState?.days ?? [])].sort((left, right) => left.date.localeCompare(right.date));
    }
    return combineCompactCalendarDays(
      compactRequestedAccountIds.flatMap((accountId) => {
        const days = compactAccountDataById[accountId]?.days;
        return days ? [{ days }] : [];
      }),
    );
  }, [
    compactAccountDataById,
    compactCopyTradeModeActive,
    compactDaysError,
    compactPrimaryState?.days,
    compactRequestedAccountIds,
  ]);
  const compactDays = useMemo(
    () =>
      activeSelectedTradeDate
        ? compactCalendarDays.filter((day) => day.date === activeSelectedTradeDate)
        : compactCalendarDays,
    [activeSelectedTradeDate, compactCalendarDays],
  );
  const compactTrades = useMemo(() => {
    if (compactTradesError) {
      return [];
    }
    const groups = compactCopyTradeModeActive
      ? compactRequestedAccountIds.flatMap((accountId) => {
          const nextTrades = compactAccountDataById[accountId]?.trades;
          return nextTrades ? [nextTrades] : [];
        })
      : [compactPrimaryState?.trades ?? []];
    return combineCompactTrades(groups, COMPACT_RECENT_TRADES_LIMIT);
  }, [
    compactAccountDataById,
    compactCopyTradeModeActive,
    compactPrimaryState?.trades,
    compactRequestedAccountIds,
    compactTradesError,
  ]);
  const compactAccountNameById = useMemo<Readonly<Record<number, string>> | undefined>(() => {
    if (!compactCopyTradeModeActive || compactRequestedAccountIds.length < 2) {
      return undefined;
    }
    const requested = new Set(compactRequestedAccountIds);
    return Object.fromEntries(
      orderedAccounts
        .filter((account) => requested.has(account.id))
        .map((account) => [account.id, account.name || account.provider_name || `Account ${account.id}`]),
    );
  }, [compactCopyTradeModeActive, compactRequestedAccountIds, orderedAccounts]);
  const compactDataWarnings = useMemo(() => {
    if (!compactCopyTradeModeActive || selectedAccountId === null) {
      return [];
    }
    const accountById = new Map(orderedAccounts.map((account) => [account.id, account]));
    return compactRequestedAccountIds.flatMap((accountId) => {
      if (accountId === selectedAccountId) {
        return [];
      }
      const state = compactAccountDataById[accountId];
      if (!state || state.summaryLoading || state.daysLoading || state.tradesLoading) {
        return [];
      }
      const unavailable: string[] = [];
      if (state.summaryError) unavailable.push("KPIs");
      if (state.daysError) unavailable.push("charts and calendar");
      if (state.tradesError) unavailable.push("Recent Trades");
      if (unavailable.length === 0) {
        return [];
      }
      const account = accountById.get(accountId);
      const name = account?.name || account?.provider_name || `Account ${accountId}`;
      return [`${name} could not contribute to ${unavailable.join(", ")}; other Compact regions remain available.`];
    });
  }, [
    compactAccountDataById,
    compactCopyTradeModeActive,
    compactRequestedAccountIds,
    orderedAccounts,
    selectedAccountId,
  ]);
  const compactViewWarnings = useMemo(
    () =>
      compactJournalActionError
        ? [...compactDataWarnings, `Journal entry was not opened: ${compactJournalActionError}`]
        : compactDataWarnings,
    [compactDataWarnings, compactJournalActionError],
  );
  const compactAccountName = compactCopyTradeModeActive
    ? `${selectedAccount?.name ?? "Active account"} copy group (${1 + copyTradeFollowerAccountIds.length} accounts)`
    : selectedAccount?.name ?? "Active account";
  const compactScoreAccountIds = useMemo(
    () =>
      compactRequestedAccountIds.filter((accountId) => {
        const state = compactAccountDataById[accountId];
        return state?.days !== null && state?.days !== undefined && !state.daysError;
      }),
    [compactAccountDataById, compactRequestedAccountIds],
  );
  const compactRiskBase = useMemo(() => {
    if (!compactMode.enabled) {
      return null;
    }
    if (!compactCopyTradeModeActive) {
      if (!selectedAccount) {
        return null;
      }
      const riskRule = getAccountRiskRuleForAccount(selectedAccount);
      if (riskRule) {
        return {
          value: riskRule.maxLossLimit,
          label: `${riskRule.provider} ${riskRule.planSize.toUpperCase()} max loss limit`,
        };
      }
      const balance = getAvailableAccountBalance(selectedAccount.balance);
      return balance !== null && balance > 0 ? { value: balance, label: "Current balance" } : null;
    }

    const scoreAccountSet = new Set(compactScoreAccountIds);
    const scoreAccounts = orderedAccounts.filter((account) => scoreAccountSet.has(account.id));
    const bases = scoreAccounts.map((account) => {
      const riskRule = getAccountRiskRuleForAccount(account);
      if (riskRule) {
        return { value: riskRule.maxLossLimit, kind: "max-loss" as const };
      }
      const balance = getAvailableAccountBalance(account.balance);
      return balance !== null && balance > 0 ? { value: balance, kind: "balance" as const } : null;
    });
    if (scoreAccounts.length === 0 || bases.some((base) => base === null)) {
      return null;
    }
    const kinds = new Set(bases.map((base) => base?.kind));
    return {
      value: bases.reduce<number>((total, base) => total + (base?.value ?? 0), 0),
      label:
        kinds.size === 1 && kinds.has("max-loss")
          ? `Combined max-loss risk bases (${scoreAccounts.length} accounts)`
          : kinds.size === 1 && kinds.has("balance")
            ? `Combined balances (${scoreAccounts.length} accounts)`
            : `Combined max-loss and balance risk bases (${scoreAccounts.length} accounts)`,
    };
  }, [compactCopyTradeModeActive, compactMode.enabled, compactScoreAccountIds, orderedAccounts, selectedAccount]);
  const compactMaxDrawdown = useMemo(() => computeCompactCalendarMaxDrawdown(compactDays), [compactDays]);
  const compactSustainability = useMemo(
    () =>
      computeSustainability({
        dailyNetPnl: compactDays.map((day) => day.net_pnl).filter((value) => Number.isFinite(value)),
        maxDrawdown: compactMaxDrawdown,
        equityBase: compactRiskBase?.value ?? null,
      }),
    [compactDays, compactMaxDrawdown, compactRiskBase?.value],
  );
  useEffect(() => {
    if (
      demoModeEnabled ||
      compactMode.enabled ||
      pnlCalendarLoading ||
      standardCalendarVisibleRange === null ||
      standardCalendarVisibleRange.scopeKey !== standardCalendarScopeKey ||
      standardCalendarRefreshAccountIds.length === 0
    ) {
      return;
    }

    const window = buildPnlCalendarRefreshWindow(standardCalendarVisibleRange);
    if (window === null) {
      return;
    }
    const warningKey = `standard|${standardCalendarScopeKey}|${window.startDate}|${window.endDate}`;
    const refreshGeneration = compactReloadNonce;
    const refreshEntries = standardCalendarRefreshAccountIds.map((accountId) => ({
      accountId,
      key: [refreshGeneration, standardCalendarScopeKey, accountId, window.startDate, window.endDate].join("|"),
    }));
    const coveredByCopyTradePreload =
      copyTradeModeActive &&
      standardCopyTradeRangeRefreshedScopeKeyRef.current === standardCalendarScopeKey &&
      (!metricsRangeQuery.allTime ||
        (window.startDate <= currentTradingDayKey && window.endDate >= currentTradingDayKey));
    if (coveredByCopyTradePreload) {
      calendarMonthRefreshLatestWarningGenerationRef.current.set(warningKey, refreshGeneration);
      refreshEntries.forEach(({ key }) => {
        standardCalendarMonthRefreshAttemptedKeysRef.current.add(key);
        standardCalendarMonthRefreshCompletedKeysRef.current.add(key);
      });
      setCalendarMonthRefreshWarnings((current) => {
        if (!(warningKey in current)) {
          return current;
        }
        const next = { ...current };
        delete next[warningKey];
        return next;
      });
      return;
    }
    const pendingEntries = refreshEntries.filter(
      ({ key }) => !standardCalendarMonthRefreshAttemptedKeysRef.current.has(key),
    );
    if (pendingEntries.length === 0) {
      return;
    }
    pendingEntries.forEach(({ key }) => standardCalendarMonthRefreshAttemptedKeysRef.current.add(key));
    calendarMonthRefreshLatestWarningGenerationRef.current.set(warningKey, refreshGeneration);
    setCalendarMonthRefreshWarnings((current) => {
      if (!(warningKey in current)) {
        return current;
      }
      const next = { ...current };
      delete next[warningKey];
      return next;
    });

    void refreshPnlCalendarMonth(
      pendingEntries.map(({ accountId }) => accountId),
      window,
    ).then(({ refreshed, failedAccountIds }) => {
      // One automatic attempt per visible month prevents a failed provider from
      // turning state changes into an unbounded retry loop. Sync Latest Trades
      // increments the reload generation and supplies a bounded retry path.
      const refreshedAccountIds = new Set(refreshed.map(({ accountId }) => accountId));
      pendingEntries.forEach(({ accountId, key }) => {
        if (refreshedAccountIds.has(accountId)) {
          standardCalendarMonthRefreshCompletedKeysRef.current.add(key);
        }
      });
      if (calendarMonthRefreshLatestWarningGenerationRef.current.get(warningKey) === refreshGeneration) {
        setCalendarMonthRefreshWarnings((current) => {
          const next = { ...current };
          if (failedAccountIds.length === 0) {
            delete next[warningKey];
          } else {
            const accountLabel = failedAccountIds.length === 1 ? "account" : "accounts";
            next[warningKey] = `ProjectX could not refresh this calendar month for ${accountLabel} ${failedAccountIds.join(", ")}. Saved P&L is still shown. Use Sync Latest Trades to retry.`;
          }
          return next;
        });
      }
      if (refreshed.length > 0) {
        refreshed.forEach(({ accountId }) => pendingCalendarMonthRefreshAccountIdsRef.current.add(accountId));
        setCalendarMonthRefreshApplyVersion((current) => current + 1);
      }
    });
  }, [
    compactReloadNonce,
    compactMode.enabled,
    copyTradeModeActive,
    currentTradingDayKey,
    demoModeEnabled,
    metricsRangeQuery.allTime,
    pnlCalendarLoading,
    selectedAccountId,
    standardCalendarRefreshAccountIds,
    standardCalendarScopeKey,
    standardCalendarVisibleRange,
  ]);

  useEffect(() => {
    if (
      demoModeEnabled ||
      !compactMode.enabled ||
      compactDaysLoading ||
      compactCalendarVisibleRange === null ||
      compactCalendarVisibleRange.scopeKey !== compactCalendarScopeKey ||
      compactCalendarRefreshAccountIds.length === 0
    ) {
      return;
    }

    const window = buildPnlCalendarRefreshWindow(compactCalendarVisibleRange);
    if (window === null) {
      return;
    }
    const warningKey = `compact|${compactCalendarScopeKey}|${window.startDate}|${window.endDate}`;
    const refreshGeneration = compactReloadNonce;
    const refreshEntries = compactCalendarRefreshAccountIds.map((accountId) => ({
      accountId,
      key: [refreshGeneration, compactCalendarScopeKey, accountId, window.startDate, window.endDate].join("|"),
    }));
    const compactCopyCoverageKey = compactCalendarContextScope
      ? [
          selectedAccountId ?? "none",
          compactRequestedAccountIds.join(","),
          compactCalendarContextScope.key,
        ].join("|")
      : null;
    if (
      compactCopyTradeModeActive &&
      compactCopyCoverageKey !== null &&
      compactCopyRefreshInFlightRef.current?.key.startsWith(`${compactCopyCoverageKey}|`)
    ) {
      return;
    }
    const coveredByCopyTradePreload =
      compactCopyTradeModeActive &&
      compactCopyCoverageKey !== null &&
      compactCopyTradeRangeRefreshedScopeKeyRef.current === compactCopyCoverageKey &&
      (!compactCalendarContextScope?.allTime ||
        (window.startDate <= currentTradingDayKey && window.endDate >= currentTradingDayKey));
    if (coveredByCopyTradePreload) {
      calendarMonthRefreshLatestWarningGenerationRef.current.set(warningKey, refreshGeneration);
      refreshEntries.forEach(({ key }) => {
        compactCalendarMonthRefreshAttemptedKeysRef.current.add(key);
        compactCalendarMonthRefreshCompletedKeysRef.current.add(key);
      });
      setCalendarMonthRefreshWarnings((current) => {
        if (!(warningKey in current)) {
          return current;
        }
        const next = { ...current };
        delete next[warningKey];
        return next;
      });
      return;
    }
    const pendingEntries = refreshEntries.filter(
      ({ key }) => !compactCalendarMonthRefreshAttemptedKeysRef.current.has(key),
    );
    if (pendingEntries.length === 0) {
      return;
    }
    pendingEntries.forEach(({ key }) => compactCalendarMonthRefreshAttemptedKeysRef.current.add(key));
    calendarMonthRefreshLatestWarningGenerationRef.current.set(warningKey, refreshGeneration);
    setCalendarMonthRefreshWarnings((current) => {
      if (!(warningKey in current)) {
        return current;
      }
      const next = { ...current };
      delete next[warningKey];
      return next;
    });

    void refreshPnlCalendarMonth(
      pendingEntries.map(({ accountId }) => accountId),
      window,
    ).then(({ refreshed, failedAccountIds }) => {
      const refreshedAccountIds = new Set(refreshed.map(({ accountId }) => accountId));
      pendingEntries.forEach(({ accountId, key }) => {
        if (refreshedAccountIds.has(accountId)) {
          compactCalendarMonthRefreshCompletedKeysRef.current.add(key);
        }
      });
      if (calendarMonthRefreshLatestWarningGenerationRef.current.get(warningKey) === refreshGeneration) {
        setCalendarMonthRefreshWarnings((current) => {
          const next = { ...current };
          if (failedAccountIds.length === 0) {
            delete next[warningKey];
          } else {
            const accountLabel = failedAccountIds.length === 1 ? "account" : "accounts";
            next[warningKey] = `ProjectX could not refresh this calendar month for ${accountLabel} ${failedAccountIds.join(", ")}. Saved P&L is still shown. Use Sync Latest Trades to retry.`;
          }
          return next;
        });
      }
      if (refreshed.length > 0) {
        refreshed.forEach(({ accountId }) => pendingCalendarMonthRefreshAccountIdsRef.current.add(accountId));
        setCalendarMonthRefreshApplyVersion((current) => current + 1);
      }
    });
  }, [
    compactCalendarRefreshAccountIds,
    compactCalendarContextScope,
    compactCalendarScopeKey,
    compactCalendarVisibleRange,
    compactCopyTradeModeActive,
    compactDaysLoading,
    compactMode.enabled,
    compactReloadNonce,
    compactRequestedAccountIds,
    currentTradingDayKey,
    demoModeEnabled,
    selectedAccountId,
  ]);

  useEffect(() => {
    if (demoModeEnabled || selectedAccountId === null) {
      return;
    }

    const displayedAccountIds = compactMode.enabled
      ? compactRequestedAccountIds
      : copyTradeModeActive
        ? copyTradeRosterAccountIds
        : [selectedAccountId];
    const refreshedDisplayedAccountIds = displayedAccountIds.filter((accountId) =>
      pendingCalendarMonthRefreshAccountIdsRef.current.has(accountId),
    );
    if (refreshedDisplayedAccountIds.length === 0) {
      return;
    }

    // Consume provider-sync completions from the currently rendered scope. This
    // state-driven handoff cannot miss a completion between render and effects,
    // and the latest request guards prevent an older scope from winning a race.
    refreshedDisplayedAccountIds.forEach((accountId) =>
      pendingCalendarMonthRefreshAccountIdsRef.current.delete(accountId),
    );
    if (compactMode.enabled) {
      compactAnalysisStartedKeyRef.current = null;
      compactCalendarStartedKeyRef.current = null;
      void Promise.all([loadCompactAnalysisData(), loadCompactCalendarData()]);
      return;
    }

    void Promise.all([
      loadSummaryAndCalendar({ skipCopyTradeRefresh: true }),
      loadTrades(),
      loadMetricsTrades(),
    ]);
  }, [
    calendarMonthRefreshApplyVersion,
    compactMode.enabled,
    compactRequestedAccountIds,
    copyTradeModeActive,
    copyTradeRosterAccountIds,
    demoModeEnabled,
    loadCompactAnalysisData,
    loadCompactCalendarData,
    loadMetricsTrades,
    loadSummaryAndCalendar,
    loadTrades,
    selectedAccountId,
  ]);
  const activeCalendarMonthRefreshWarning = useMemo(() => {
    const mode = compactMode.enabled ? "compact" : "standard";
    const scopeKey = compactMode.enabled ? compactCalendarScopeKey : standardCalendarScopeKey;
    const visibleRange = compactMode.enabled ? compactCalendarVisibleRange : standardCalendarVisibleRange;
    if (visibleRange === null || visibleRange.scopeKey !== scopeKey) {
      return null;
    }
    return (
      calendarMonthRefreshWarnings[
        `${mode}|${scopeKey}|${visibleRange.startDate}|${visibleRange.endDate}`
      ] ?? null
    );
  }, [
    calendarMonthRefreshWarnings,
    compactCalendarScopeKey,
    compactCalendarVisibleRange,
    compactMode.enabled,
    standardCalendarScopeKey,
    standardCalendarVisibleRange,
  ]);
  const rawDashboardPnlCalendarDays = copyTradeStatsActive ? copyTradeCalendarDays : pnlCalendarDays;
  const dashboardSummary = useMemo(
    () =>
      copyTradeStatsActive
        ? {
            ...summary,
            net_pnl: copyTradeTotals.combinedNetPnl,
            profit_per_day: copyTradeTotals.combinedDailyPnl,
          }
        : summary,
    [copyTradeStatsActive, copyTradeTotals.combinedDailyPnl, copyTradeTotals.combinedNetPnl, summary],
  );
  const dashboardCurrentBalance = copyTradeStatsActive
    ? copyTradeTotals.combinedBalance
    : getAvailableAccountBalance(selectedAccount?.balance ?? null);
  const selectedDayLoadedTradeCount = !copyTradeStatsActive && activeSelectedTradeDate && !tradesLoading && !tradesError ? trades.length : null;
  const displayTradeCount = selectedDayLoadedTradeCount ?? summary.trade_count;
  const displayActiveDays = selectedDayLoadedTradeCount !== null ? (selectedDayLoadedTradeCount > 0 ? 1 : 0) : summary.active_days;
  const displayAvgTradesPerDay = selectedDayLoadedTradeCount !== null ? selectedDayLoadedTradeCount : summary.avg_trades_per_day;
  const dashboardPnlCalendarDays = useMemo(
    () =>
      selectedDayLoadedTradeCount === null || !activeSelectedTradeDate
        ? rawDashboardPnlCalendarDays
        : rawDashboardPnlCalendarDays.map((day) =>
            day.date === activeSelectedTradeDate
              ? {
                  ...day,
                  trade_count: selectedDayLoadedTradeCount,
                }
              : day,
          ),
    [activeSelectedTradeDate, rawDashboardPnlCalendarDays, selectedDayLoadedTradeCount],
  );
  const analyticsPnlCalendarDays = useMemo(
    () =>
      activeSelectedTradeDate
        ? dashboardPnlCalendarDays.filter((day) => day.date === activeSelectedTradeDate)
        : dashboardPnlCalendarDays,
    [activeSelectedTradeDate, dashboardPnlCalendarDays],
  );
  const analyticsSummary = useMemo(
    () =>
      selectedDayLoadedTradeCount === null
        ? dashboardSummary
        : {
            ...dashboardSummary,
            trade_count: displayTradeCount,
            active_days: displayActiveDays,
            avg_trades_per_day: displayAvgTradesPerDay,
          },
    [dashboardSummary, displayActiveDays, displayAvgTradesPerDay, displayTradeCount, selectedDayLoadedTradeCount],
  );

  const directionDataIssue = metricsTradesLoading
    ? "Loading directional trade history."
    : metricsTradesError
      ? "Needs directional trade history for this range."
      : null;

  const hasCompleteDirectionalHistory =
    !metricsTradesLoading && !metricsTradesError && displayTradeCount <= metricsTrades.length;

  const derivedMetrics = useMemo(
    () =>
      computeDashboardDerivedMetrics({
        summary: analyticsSummary,
        trades: metricsTrades,
        dailyPnlDays: analyticsPnlCalendarDays,
        hasCompleteDirectionalHistory,
        directionDataIssue,
      }),
    [analyticsPnlCalendarDays, analyticsSummary, directionDataIssue, hasCompleteDirectionalHistory, metricsTrades],
  );

  const netPnlMetric = useMemo<MetricValue>(() => ({ value: dashboardSummary.net_pnl }), [dashboardSummary.net_pnl]);
  const profitPerDayMetric = useMemo<MetricValue>(() => ({ value: dashboardSummary.profit_per_day }), [dashboardSummary.profit_per_day]);
  const efficiencyPerHourMetric = useMemo<MetricValue>(() => ({ value: summary.efficiency_per_hour }), [summary.efficiency_per_hour]);
  const expectancyPerTradeMetric = useMemo<MetricValue>(() => ({ value: summary.expectancy_per_trade }), [summary.expectancy_per_trade]);
  const maxDrawdownMetric = useMemo<MetricValue>(() => ({ value: summary.max_drawdown }), [summary.max_drawdown]);
  const performanceSignalVariant = dashboardSummary.net_pnl > 0 ? "positive" : dashboardSummary.net_pnl < 0 ? "negative" : "neutral";
  const performanceSignalLabel = copyTradeStatsActive
    ? dashboardSummary.net_pnl > 0
      ? "Copy Net Positive"
      : dashboardSummary.net_pnl < 0
        ? "Copy Net Negative"
        : "Copy Net Flat"
    : dashboardSummary.net_pnl > 0
      ? "Positive Flow"
      : dashboardSummary.net_pnl < 0
        ? "Negative Drift"
        : "Flat Session";
  const performanceAccentClassName =
    dashboardSummary.net_pnl > 0
      ? "bg-gradient-to-r from-app-positive/80 via-app-accent/30 to-transparent"
      : dashboardSummary.net_pnl < 0
        ? "bg-gradient-to-r from-app-negative/80 via-app-warning/35 to-transparent"
        : "bg-gradient-to-r from-app-accent/70 via-app-accent/25 to-transparent";
  const performancePrimaryClassName =
    dashboardSummary.net_pnl > 0
      ? "bg-gradient-to-r from-app-positive via-app-accent to-app-positive bg-clip-text text-transparent"
      : dashboardSummary.net_pnl < 0
        ? "bg-gradient-to-r from-app-negative via-app-warning to-app-negative bg-clip-text text-transparent"
        : "text-app-accent";
  const performanceCardClassName =
    dashboardSummary.net_pnl > 0
      ? "border-app-positive/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgb(var(--theme-positive)/0.24),rgb(var(--theme-surface)/0.58)_44%,rgb(var(--theme-surface)/0.9)_100%)]"
      : dashboardSummary.net_pnl < 0
        ? "border-app-negative/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgb(var(--theme-negative)/0.22),rgb(var(--theme-surface)/0.58)_44%,rgb(var(--theme-surface)/0.9)_100%)]"
        : "border-app-accent/25 bg-[radial-gradient(150%_120%_at_0%_0%,rgb(var(--theme-accent)/0.2),rgb(var(--theme-surface)/0.58)_44%,rgb(var(--theme-surface)/0.9)_100%)]";
  const performanceGlowClassName =
    dashboardSummary.net_pnl > 0 ? "bg-app-positive/25" : dashboardSummary.net_pnl < 0 ? "bg-app-negative/25" : "bg-app-accent/20";
  const edgeSignalVariant = summary.expectancy_per_trade > 0 ? "positive" : summary.expectancy_per_trade < 0 ? "negative" : "neutral";
  const edgeSignalLabel = summary.expectancy_per_trade > 0 ? "Positive Expectancy" : summary.expectancy_per_trade < 0 ? "Negative Expectancy" : "Flat Expectancy";
  const edgeAccentClassName =
    summary.expectancy_per_trade > 0
      ? "bg-gradient-to-r from-app-accent/80 via-app-positive/25 to-transparent"
      : summary.expectancy_per_trade < 0
        ? "bg-gradient-to-r from-app-negative/80 via-app-warning/30 to-transparent"
        : "bg-gradient-to-r from-app-accent/75 via-app-accent/20 to-transparent";
  const edgePrimaryClassName =
    summary.expectancy_per_trade > 0
      ? "bg-gradient-to-r from-app-accent via-app-positive to-app-accent bg-clip-text text-transparent"
      : summary.expectancy_per_trade < 0
        ? "bg-gradient-to-r from-app-negative via-app-warning to-app-negative bg-clip-text text-transparent"
        : "text-app-accent";
  const edgeCardClassName =
    summary.expectancy_per_trade > 0
      ? "border-app-accent/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgb(var(--theme-accent)/0.22),rgb(var(--theme-surface)/0.58)_45%,rgb(var(--theme-surface)/0.9)_100%)]"
      : summary.expectancy_per_trade < 0
        ? "border-app-negative/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgb(var(--theme-negative)/0.2),rgb(var(--theme-surface)/0.58)_45%,rgb(var(--theme-surface)/0.9)_100%)]"
        : "border-app-accent/25 bg-[radial-gradient(150%_120%_at_0%_0%,rgb(var(--theme-accent)/0.18),rgb(var(--theme-surface)/0.58)_45%,rgb(var(--theme-surface)/0.9)_100%)]";
  const edgeGlowClassName =
    summary.expectancy_per_trade > 0 ? "bg-app-accent/20" : summary.expectancy_per_trade < 0 ? "bg-app-negative/20" : "bg-app-accent/20";
  const edgeOutcomeTotal = summary.win_count + summary.loss_count + summary.breakeven_count;
  const edgeWinShare = edgeOutcomeTotal > 0 ? (summary.win_count / edgeOutcomeTotal) * 100 : 0;
  const edgeLossShare = edgeOutcomeTotal > 0 ? (summary.loss_count / edgeOutcomeTotal) * 100 : 0;
  const edgeBreakevenShare = edgeOutcomeTotal > 0 ? (summary.breakeven_count / edgeOutcomeTotal) * 100 : 0;
  const payoffSignalVariant =
    derivedMetrics.winLossRatio.value === null
      ? "neutral"
      : derivedMetrics.winLossRatio.value >= 1.5
        ? "positive"
        : derivedMetrics.winLossRatio.value >= 1
          ? "accent"
          : "negative";
  const payoffSignalLabel =
    derivedMetrics.winLossRatio.value === null
      ? "Awaiting Payoff Data"
      : derivedMetrics.winLossRatio.value >= 1.5
        ? "Strong Asymmetry"
        : derivedMetrics.winLossRatio.value >= 1
          ? "Balanced Asymmetry"
          : "Weak Asymmetry";
  const payoffAccentClassName =
    derivedMetrics.winLossRatio.value === null || derivedMetrics.winLossRatio.value >= 1
      ? "bg-gradient-to-r from-app-positive/70 via-app-accent/20 to-transparent"
      : "bg-gradient-to-r from-app-negative/75 via-app-warning/20 to-transparent";
  const payoffPrimaryClassName =
    derivedMetrics.winLossRatio.value === null || derivedMetrics.winLossRatio.value >= 1
      ? "bg-gradient-to-r from-app-positive via-app-accent to-app-positive bg-clip-text text-transparent"
      : "bg-gradient-to-r from-app-negative via-app-warning to-app-negative bg-clip-text text-transparent";
  const payoffCardClassName =
    derivedMetrics.winLossRatio.value === null || derivedMetrics.winLossRatio.value >= 1
      ? "border-app-positive/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgb(var(--theme-positive)/0.18),rgb(var(--theme-surface)/0.58)_46%,rgb(var(--theme-surface)/0.9)_100%)]"
      : "border-app-negative/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgb(var(--theme-negative)/0.2),rgb(var(--theme-surface)/0.58)_46%,rgb(var(--theme-surface)/0.9)_100%)]";
  const holdTimeSignalVariant =
    derivedMetrics.winDurationOverLossDuration.value === null
      ? "neutral"
      : derivedMetrics.winDurationOverLossDuration.value >= 1.2
        ? "positive"
        : derivedMetrics.winDurationOverLossDuration.value >= 0.9
          ? "warning"
          : "negative";
  const holdTimeSignalLabel =
    derivedMetrics.winDurationOverLossDuration.value === null
      ? "Awaiting Duration Data"
      : derivedMetrics.winDurationOverLossDuration.value >= 1.2
        ? "Winners Breathe"
        : derivedMetrics.winDurationOverLossDuration.value >= 0.9
          ? "Balanced Holds"
          : "Cut Winners Early";
  const holdTimeAccentClassName =
    derivedMetrics.winDurationOverLossDuration.value === null || derivedMetrics.winDurationOverLossDuration.value >= 1
      ? "bg-gradient-to-r from-app-accent/70 via-app-warning/25 to-transparent"
      : "bg-gradient-to-r from-app-warning/70 via-app-negative/20 to-transparent";
  const holdTimePrimaryClassName =
    derivedMetrics.winDurationOverLossDuration.value === null || derivedMetrics.winDurationOverLossDuration.value >= 1
      ? "bg-gradient-to-r from-app-accent via-app-warning to-app-accent bg-clip-text text-transparent"
      : "bg-gradient-to-r from-app-warning via-app-negative to-app-warning bg-clip-text text-transparent";
  const holdTimeCardClassName =
    derivedMetrics.winDurationOverLossDuration.value === null || derivedMetrics.winDurationOverLossDuration.value >= 1
      ? "border-app-accent/25 bg-[radial-gradient(150%_120%_at_0%_0%,rgb(var(--theme-accent)/0.18),rgb(var(--theme-surface)/0.58)_46%,rgb(var(--theme-surface)/0.9)_100%)]"
      : "border-app-warning/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgb(var(--theme-warning)/0.18),rgb(var(--theme-surface)/0.58)_46%,rgb(var(--theme-surface)/0.9)_100%)]";

  const drawdownPercentOfNet = useMemo(
    () => computeDrawdownPercentOfNetPnl(summary.max_drawdown, dashboardSummary.net_pnl),
    [dashboardSummary.net_pnl, summary.max_drawdown],
  );

  const directionSplit = useMemo(() => {
    const longTrades = derivedMetrics.direction.longTrades.value;
    const shortTrades = derivedMetrics.direction.shortTrades.value;
    if (longTrades === null || shortTrades === null) {
      const reason =
        derivedMetrics.direction.longTrades.missingReason ??
        derivedMetrics.direction.shortTrades.missingReason ??
        "Needs directional trades.";
      return {
        longPercent: { value: null, missingReason: reason },
        shortPercent: { value: null, missingReason: reason },
      };
    }
    return computeDirectionPercentages(longTrades, shortTrades);
  }, [
    derivedMetrics.direction.longTrades.missingReason,
    derivedMetrics.direction.longTrades.value,
    derivedMetrics.direction.shortTrades.missingReason,
    derivedMetrics.direction.shortTrades.value,
  ]);

  const stabilityScore = useMemo(
    () => computeStabilityScoreFromWorstDayPercent(derivedMetrics.stability.worstDayPercentOfNet.value),
    [derivedMetrics.stability.worstDayPercentOfNet.value],
  );
  const sustainabilityDailyNetPnl = useMemo(
    () =>
      analyticsPnlCalendarDays
        .map((day) => day.net_pnl)
        .filter((value) => Number.isFinite(value)),
    [analyticsPnlCalendarDays],
  );
  const accountRiskRule = useMemo(() => {
    if (!selectedAccount) {
      return null;
    }
    return getAccountRiskRuleForAccount(selectedAccount);
  }, [selectedAccount]);
  const accountRiskBase = useMemo(() => {
    if (accountRiskRule !== null) {
      return {
        value: accountRiskRule.maxLossLimit,
        label: `${accountRiskRule.provider} ${accountRiskRule.planSize.toUpperCase()} max loss limit`,
        detail: `Risk uses the ${formatCurrency(accountRiskRule.maxLossLimit)} Maximum Loss Limit, not ${formatCurrency(
          accountRiskRule.nominalBuyingPower,
        )} buying power or current balance.`,
      };
    }

    const currentBalance = getAvailableAccountBalance(selectedAccount?.balance ?? null);
    if (currentBalance !== null && currentBalance > 0) {
      return {
        value: currentBalance,
        label: "Current balance",
        detail: "Account risk uses the current account balance as the risk base.",
      };
    }

    return null;
  }, [accountRiskRule, selectedAccount?.balance]);
  const sustainability = useMemo(
    () =>
      computeSustainability({
        dailyNetPnl: sustainabilityDailyNetPnl,
        maxDrawdown: summary.max_drawdown,
        equityBase: accountRiskBase?.value ?? null,
      }),
    [
      accountRiskBase?.value,
      sustainabilityDailyNetPnl,
      summary.max_drawdown,
    ],
  );
  const sustainabilityAccentClassName =
    sustainability.score >= 80
      ? "bg-gradient-to-r from-app-positive/75 via-app-accent/20 to-transparent"
      : sustainability.score >= 60
        ? "bg-gradient-to-r from-app-accent/75 via-app-secondary/20 to-transparent"
        : sustainability.score >= 40
          ? "bg-gradient-to-r from-app-warning/75 via-app-warning/25 to-transparent"
          : "bg-gradient-to-r from-app-negative/80 via-app-warning/25 to-transparent";
  const sustainabilityPrimaryClassName =
    sustainability.score >= 80
      ? "bg-gradient-to-r from-app-positive via-app-accent to-app-positive bg-clip-text text-transparent"
      : sustainability.score >= 60
        ? "bg-gradient-to-r from-app-accent via-app-secondary to-app-accent bg-clip-text text-transparent"
        : sustainability.score >= 40
          ? "bg-gradient-to-r from-app-warning via-app-warning to-app-warning bg-clip-text text-transparent"
          : "bg-gradient-to-r from-app-negative via-app-warning to-app-negative bg-clip-text text-transparent";
  const sustainabilityCardClassName =
    sustainability.score >= 80
      ? "border-app-positive/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgb(var(--theme-positive)/0.2),rgb(var(--theme-surface)/0.58)_46%,rgb(var(--theme-surface)/0.9)_100%)]"
      : sustainability.score >= 60
        ? "border-app-accent/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgb(var(--theme-accent)/0.2),rgb(var(--theme-surface)/0.58)_46%,rgb(var(--theme-surface)/0.9)_100%)]"
        : sustainability.score >= 40
          ? "border-app-warning/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgb(var(--theme-warning)/0.18),rgb(var(--theme-surface)/0.58)_46%,rgb(var(--theme-surface)/0.9)_100%)]"
          : "border-app-negative/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgb(var(--theme-negative)/0.2),rgb(var(--theme-surface)/0.58)_46%,rgb(var(--theme-surface)/0.9)_100%)]";
  const drawdownEquityBase = useMemo(() => {
    if (accountRiskBase !== null) {
      return accountRiskBase;
    }

    if (sustainability.debug.peakEquityFallback > 0) {
      return {
        value: sustainability.debug.peakEquityFallback,
        label: "Peak equity fallback",
        detail: "Account risk base is unavailable, so risk uses peak equity inferred from daily PnL in this range.",
      };
    }

    return {
      value: null,
      label: "Unavailable",
      detail: "Need account risk rules, account balance, or equity history to grade drawdown control.",
    };
  }, [accountRiskBase, sustainability.debug.peakEquityFallback]);
  const drawdownPercentOfEquityBase = useMemo(
    () => computeDrawdownPercentOfEquityBase(summary.max_drawdown, drawdownEquityBase.value),
    [summary.max_drawdown, drawdownEquityBase.value],
  );
  const riskSignalVariant =
    drawdownPercentOfEquityBase.value === null
      ? "neutral"
      : drawdownPercentOfEquityBase.value <= 5
        ? "positive"
        : drawdownPercentOfEquityBase.value <= 10
          ? "accent"
          : drawdownPercentOfEquityBase.value <= 15
            ? "warning"
            : "negative";
  const riskSignalLabel =
    drawdownPercentOfEquityBase.value === null
      ? "Awaiting Risk Base"
      : drawdownPercentOfEquityBase.value <= 5
        ? "Very Controlled"
        : drawdownPercentOfEquityBase.value <= 10
          ? "Controlled"
          : drawdownPercentOfEquityBase.value <= 15
            ? "Moderate Risk"
            : drawdownPercentOfEquityBase.value <= 25
              ? "High Risk"
              : "Uncontrolled";
  const riskAccentClassName =
    drawdownPercentOfEquityBase.value === null || drawdownPercentOfEquityBase.value <= 10
      ? "bg-gradient-to-r from-app-accent/70 via-app-positive/20 to-transparent"
      : drawdownPercentOfEquityBase.value <= 15
        ? "bg-gradient-to-r from-app-warning/70 via-app-warning/20 to-transparent"
        : "bg-gradient-to-r from-app-negative/75 via-app-warning/25 to-transparent";
  const riskPrimaryClassName =
    drawdownPercentOfEquityBase.value === null || drawdownPercentOfEquityBase.value <= 10
      ? "bg-gradient-to-r from-app-accent via-app-positive to-app-accent bg-clip-text text-transparent"
      : drawdownPercentOfEquityBase.value <= 15
        ? "bg-gradient-to-r from-app-warning via-app-warning to-app-warning bg-clip-text text-transparent"
        : "bg-gradient-to-r from-app-negative via-app-warning to-app-negative bg-clip-text text-transparent";
  const riskCardClassName =
    drawdownPercentOfEquityBase.value === null || drawdownPercentOfEquityBase.value <= 10
      ? "border-app-accent/25 bg-[radial-gradient(150%_120%_at_0%_0%,rgb(var(--theme-accent)/0.16),rgb(var(--theme-surface)/0.6)_46%,rgb(var(--theme-surface)/0.9)_100%)]"
      : drawdownPercentOfEquityBase.value <= 15
        ? "border-app-warning/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgb(var(--theme-warning)/0.18),rgb(var(--theme-surface)/0.6)_46%,rgb(var(--theme-surface)/0.9)_100%)]"
        : "border-app-negative/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgb(var(--theme-negative)/0.2),rgb(var(--theme-surface)/0.6)_46%,rgb(var(--theme-surface)/0.9)_100%)]";
  const riskPressurePercent =
    drawdownPercentOfEquityBase.value === null
      ? 0
      : Math.min(100, Math.max(0, (drawdownPercentOfEquityBase.value / RISK_PRESSURE_FULL_SCALE_PERCENT) * 100));

  const directionPrimaryValue =
    directionSplit.longPercent.value === null ? "N/A" : `${formatPercent(directionSplit.longPercent.value, 0)} Long`;
  const longPnlShareMagnitude = Math.abs(derivedMetrics.direction.longPnlShare.value ?? 0);
  const shortPnlShareMagnitude = Math.abs(derivedMetrics.direction.shortPnlShare.value ?? 0);
  const totalPnlShareMagnitude = longPnlShareMagnitude + shortPnlShareMagnitude;
  const longPnlShareWidth = totalPnlShareMagnitude > 0 ? (longPnlShareMagnitude / totalPnlShareMagnitude) * 100 : 50;
  const shortPnlShareWidth = totalPnlShareMagnitude > 0 ? (shortPnlShareMagnitude / totalPnlShareMagnitude) * 100 : 50;
  const activityMetrics = useMemo(
    () =>
      computeActivityMetrics({
        totalTrades: displayTradeCount,
        activeDays: displayActiveDays,
        dailyPnlDays: analyticsPnlCalendarDays,
        rangeStart: analyticsRangeQuery.start,
        rangeEnd: analyticsRangeQuery.end,
      }),
    [analyticsPnlCalendarDays, analyticsRangeQuery.end, analyticsRangeQuery.start, displayActiveDays, displayTradeCount],
  );
  const activityWeeklyPace = activeSelectedTradeDate ? null : activityMetrics.tradesPerWeek;
  const activitySignalVariant =
    activeSelectedTradeDate
      ? "accent"
      : activityWeeklyPace === null
      ? "neutral"
      : activityWeeklyPace >= 30
        ? "warning"
        : activityWeeklyPace >= 15
          ? "accent"
          : "positive";
  const activitySignalLabel =
    activeSelectedTradeDate
      ? "Day Filter"
      : activityWeeklyPace === null
      ? "Awaiting Pace Data"
      : activityWeeklyPace >= 30
        ? "High Tempo"
        : activityWeeklyPace >= 15
          ? "Balanced Tempo"
          : "Selective Tempo";
  const activityAccentClassName =
    activityWeeklyPace === null || activityWeeklyPace < 30
      ? "bg-gradient-to-r from-app-muted/60 via-app-accent/20 to-transparent"
      : "bg-gradient-to-r from-app-warning/70 via-app-warning/20 to-transparent";
  const activityPrimaryClassName =
    activityWeeklyPace === null || activityWeeklyPace < 30
      ? "bg-gradient-to-r from-app-accent via-app-text to-app-accent bg-clip-text text-transparent"
      : "bg-gradient-to-r from-app-warning via-app-warning to-app-warning bg-clip-text text-transparent";
  const activityCardClassName =
    activityWeeklyPace === null || activityWeeklyPace < 30
      ? "border-app-border-strong/60 bg-[radial-gradient(150%_120%_at_0%_0%,rgb(var(--theme-muted)/0.16),rgb(var(--theme-surface)/0.58)_46%,rgb(var(--theme-surface)/0.9)_100%)]"
      : "border-app-warning/25 bg-[radial-gradient(150%_120%_at_0%_0%,rgb(var(--theme-warning)/0.15),rgb(var(--theme-surface)/0.58)_46%,rgb(var(--theme-surface)/0.9)_100%)]";
  const activityPacePercent = activityWeeklyPace === null ? 0 : Math.min(100, Math.max(0, (activityWeeklyPace / 30) * 100));
  const fullStatsRangeLabel = useMemo(() => {
    if (analyticsRangeQuery.start && analyticsRangeQuery.end) {
      return formatFullStatsRangeLabel(tradingDayKey(analyticsRangeQuery.start), tradingDayKey(analyticsRangeQuery.end));
    }

    if (analyticsPnlCalendarDays.length > 0) {
      const orderedDays = [...analyticsPnlCalendarDays].sort((left, right) => left.date.localeCompare(right.date));
      return formatFullStatsRangeLabel(orderedDays[0].date, orderedDays[orderedDays.length - 1].date);
    }

    return "selected range";
  }, [analyticsPnlCalendarDays, analyticsRangeQuery.end, analyticsRangeQuery.start]);
  const compactRangeBounds = useMemo(() => {
    const calendarScope = compactScopes?.calendarContextScope;
    if (calendarScope?.startDate && calendarScope.endDate) {
      return {
        startDate: calendarScope.startDate,
        endDate: calendarScope.endDate,
      };
    }

    if (compactCalendarDays.length > 0) {
      const orderedDays = [...compactCalendarDays].sort((left, right) => left.date.localeCompare(right.date));
      return {
        startDate: orderedDays[0].date,
        endDate: orderedDays[orderedDays.length - 1].date,
      };
    }

    return { startDate: undefined, endDate: undefined };
  }, [compactCalendarDays, compactScopes?.calendarContextScope]);
  const compactRangeLabel =
    activeSelectedTradeDate && selectedTradeDateLabel
      ? selectedTradeDateLabel
      : compactRangeBounds.startDate && compactRangeBounds.endDate
      ? formatFullStatsRangeLabel(compactRangeBounds.startDate, compactRangeBounds.endDate)
      : "All available history";
  const recentTrades = trades;
  const recentTradesLoading = tradesLoading;
  const recentTradesError = tradesError;
  const clearSelectedTradeDate = useCallback(() => handleSelectedTradeDateChange(null), [handleSelectedTradeDateChange]);
  const showMoreRecentTrades = useCallback(
    () =>
      setRecentTradesVisibleCount((current) =>
        getNextRecentTradesVisibleCount(current, recentTrades.length),
      ),
    [recentTrades.length],
  );
  const copyFullStatsMetrics = useMemo<CopyFullStatsMetrics>(
    () => ({
      summary: analyticsSummary,
      performance: {
        netPnl: netPnlMetric,
        profitPerDay: profitPerDayMetric,
        efficiencyPerHour: efficiencyPerHourMetric,
        expectancyPerTrade: expectancyPerTradeMetric,
      },
      consistency: {
        dailyPnlVolatility: derivedMetrics.stability.dailyPnlVolatility,
        bestDay: derivedMetrics.stability.bestDay,
        worstDay: derivedMetrics.stability.worstDay,
        bestDayPercentOfNet: derivedMetrics.stability.bestDayPercentOfNet,
        worstDayPercentOfNet: derivedMetrics.stability.worstDayPercentOfNet,
        medianDayPnl: derivedMetrics.stability.medianDayPnl,
        avgGreenDay: derivedMetrics.stability.avgGreenDay,
        avgRedDay: derivedMetrics.stability.avgRedDay,
        redDayPercent: derivedMetrics.stability.redDayPercent,
        worstDayImpact: derivedMetrics.stability.nukeRatio,
        greenRedDaySizeRatio: derivedMetrics.stability.greenRedDaySizeRatio,
        stabilityScore,
        insight: derivedMetrics.stability.insight,
      },
      risk: {
        maxDrawdown: maxDrawdownMetric,
        drawdownPercentOfNet,
        drawdownPercentOfEquityBase,
        equityBase: drawdownEquityBase,
        averageDrawdown: { value: summary.average_drawdown },
        maxDrawdownLengthHours: summary.max_drawdown_length_hours,
        recoveryTimeHours: summary.recovery_time_hours,
      },
      direction: {
        longPercent: directionSplit.longPercent,
        shortPercent: directionSplit.shortPercent,
        longTrades: derivedMetrics.direction.longTrades,
        shortTrades: derivedMetrics.direction.shortTrades,
        longPnl: derivedMetrics.direction.longPnl,
        shortPnl: derivedMetrics.direction.shortPnl,
        longPnlShare: derivedMetrics.direction.longPnlShare,
        shortPnlShare: derivedMetrics.direction.shortPnlShare,
        longWinRate: derivedMetrics.direction.longWinRate,
        shortWinRate: derivedMetrics.direction.shortWinRate,
        longExpectancy: derivedMetrics.direction.longExpectancy,
        shortExpectancy: derivedMetrics.direction.shortExpectancy,
        longProfitFactor: derivedMetrics.direction.longProfitFactor,
        shortProfitFactor: derivedMetrics.direction.shortProfitFactor,
        longAvgWin: derivedMetrics.direction.longAvgWin,
        longAvgLoss: derivedMetrics.direction.longAvgLoss,
        shortAvgWin: derivedMetrics.direction.shortAvgWin,
        shortAvgLoss: derivedMetrics.direction.shortAvgLoss,
        longLargeLossRate: derivedMetrics.direction.longLargeLossRate,
        shortLargeLossRate: derivedMetrics.direction.shortLargeLossRate,
        insight: derivedMetrics.direction.insight,
      },
      payoff: {
        winLossRatio: derivedMetrics.winLossRatio,
        averageWin: derivedMetrics.payoff.averageWin,
        averageLoss: derivedMetrics.payoff.averageLoss,
        breakevenWinRate: derivedMetrics.payoff.breakevenWinRate,
        currentWinRate: derivedMetrics.payoff.currentWinRate,
        wrCushion: derivedMetrics.payoff.wrCushion,
        largeLossThreshold: derivedMetrics.payoff.largeLossThreshold,
        largeLossRate: derivedMetrics.payoff.largeLossRate,
        p95Loss: derivedMetrics.payoff.p95Loss,
        capture: derivedMetrics.payoff.capture,
        pointPayoffByBasis,
        insight: derivedMetrics.payoff.insight,
      },
      activity: activityMetrics,
      sustainability,
      holdTime: {
        ratio: derivedMetrics.winDurationOverLossDuration,
        averageWinDurationMinutes: summary.avg_win_duration_minutes,
        averageLossDurationMinutes: summary.avg_loss_duration_minutes,
      },
      balance: {
        currentBalance: dashboardCurrentBalance,
      },
    }),
    [
      activityMetrics,
      derivedMetrics.direction.insight,
      derivedMetrics.direction.longAvgLoss,
      derivedMetrics.direction.longAvgWin,
      derivedMetrics.direction.longExpectancy,
      derivedMetrics.direction.longLargeLossRate,
      derivedMetrics.direction.longPnl,
      derivedMetrics.direction.longPnlShare,
      derivedMetrics.direction.longProfitFactor,
      derivedMetrics.direction.longTrades,
      derivedMetrics.direction.longWinRate,
      derivedMetrics.direction.shortAvgLoss,
      derivedMetrics.direction.shortAvgWin,
      derivedMetrics.direction.shortExpectancy,
      derivedMetrics.direction.shortLargeLossRate,
      derivedMetrics.direction.shortPnl,
      derivedMetrics.direction.shortPnlShare,
      derivedMetrics.direction.shortProfitFactor,
      derivedMetrics.direction.shortTrades,
      derivedMetrics.direction.shortWinRate,
      derivedMetrics.payoff.averageLoss,
      derivedMetrics.payoff.averageWin,
      derivedMetrics.payoff.breakevenWinRate,
      derivedMetrics.payoff.capture,
      derivedMetrics.payoff.currentWinRate,
      derivedMetrics.payoff.insight,
      derivedMetrics.payoff.largeLossRate,
      derivedMetrics.payoff.largeLossThreshold,
      derivedMetrics.payoff.p95Loss,
      derivedMetrics.payoff.wrCushion,
      derivedMetrics.stability.avgGreenDay,
      derivedMetrics.stability.avgRedDay,
      derivedMetrics.stability.bestDay,
      derivedMetrics.stability.bestDayPercentOfNet,
      derivedMetrics.stability.dailyPnlVolatility,
      derivedMetrics.stability.greenRedDaySizeRatio,
      derivedMetrics.stability.insight,
      derivedMetrics.stability.medianDayPnl,
      derivedMetrics.stability.nukeRatio,
      derivedMetrics.stability.redDayPercent,
      derivedMetrics.stability.worstDay,
      derivedMetrics.stability.worstDayPercentOfNet,
      derivedMetrics.winDurationOverLossDuration,
      derivedMetrics.winLossRatio,
      directionSplit.longPercent,
      directionSplit.shortPercent,
      drawdownEquityBase,
      drawdownPercentOfEquityBase,
      drawdownPercentOfNet,
      efficiencyPerHourMetric,
      expectancyPerTradeMetric,
      maxDrawdownMetric,
      netPnlMetric,
      pointPayoffByBasis,
      profitPerDayMetric,
      dashboardCurrentBalance,
      analyticsSummary,
      stabilityScore,
      summary,
      sustainability,
    ],
  );

  const openJournalForDate = useCallback(
    async (date: string) => {
      if (!selectedAccountId) {
        return;
      }

      const requestAccountId = selectedAccountId;

      setCompactJournalActionError(null);
      const next = new URLSearchParams();
      next.set(ACCOUNT_QUERY_PARAM, String(requestAccountId));
      next.set("date", date);

      if (demoModeEnabled) {
        navigate(`/journal?${next.toString()}`);
        return;
      }

      const requestToken = journalOpenRequestGate.begin(requestAccountId, "open-journal");
      try {
        await accountsApi.createJournalEntry(requestAccountId, {
          entry_date: date,
          title: "New Entry",
          mood: "Neutral",
          tags: [],
          body: "",
        });
        if (!journalOpenRequestGate.isCurrent(requestToken)) {
          return;
        }
        setJournalDays((current) => {
          const next = new Set(current);
          next.add(date);
          return next;
        });
        setCompactJournalDays((current) => {
          const next = new Set(current);
          next.add(date);
          return next;
        });

        navigate(`/journal?${next.toString()}`);
      } catch (err) {
        if (!journalOpenRequestGate.isCurrent(requestToken)) {
          return;
        }
        const message = err instanceof Error ? err.message : "Failed to open journal entry";
        if (compactMode.enabled) {
          setCompactJournalActionError(message);
        } else {
          setTradesError(message);
        }
      }
    },
    [compactMode.enabled, demoModeEnabled, journalOpenRequestGate, navigate, selectedAccountId],
  );

  const handleStandardCalendarVisibleRangeChange = useCallback(
    (startDate: string, endDate: string) => {
      setStandardCalendarVisibleRange((current) => {
        if (
          current &&
          current.startDate === startDate &&
          current.endDate === endDate &&
          current.scopeKey === standardCalendarScopeKey
        ) {
          return current;
        }
        return { startDate, endDate, scopeKey: standardCalendarScopeKey };
      });
    },
    [standardCalendarScopeKey],
  );

  const handleCompactCalendarVisibleRangeChange = useCallback(
    (startDate: string, endDate: string) => {
      setCompactCalendarVisibleRange((current) => {
        if (
          current &&
          current.startDate === startDate &&
          current.endDate === endDate &&
          current.scopeKey === compactCalendarScopeKey
        ) {
          return current;
        }
        return { startDate, endDate, scopeKey: compactCalendarScopeKey };
      });
    },
    [compactCalendarScopeKey],
  );

  const saveCopyTradeSettings = useCallback((updater: (current: CopyTradeSettings) => CopyTradeSettings): CopyTradeSettingsSaveResult => {
    const current = copyTradeSettingsRef.current;
    const next = updater(current);

    try {
      writeStoredCopyTradeSettings(next);
      copyTradeSettingsRef.current = next;
      setCopyTradeSettings(next);
      return { ok: true };
    } catch (err) {
      return {
        ok: false,
        errorMessage: getCopyTradeSettingsSaveErrorMessage(err),
      };
    }
  }, []);

  useEffect(() => {
    if (!selectedAccountIsCsvImport || !copyTradeSettings.modeEnabled) {
      return;
    }

    const saveResult = saveCopyTradeSettings((current) => updateCopyTradeModeSetting(current, false));
    setCopyTradeToggleFeedback(
      saveResult.ok
        ? { tone: "success", message: "Copy Trade Mode disabled for Live CSV imports." }
        : {
            tone: "error",
            message: `Live CSV mode is active, but the old Copy Trade preference could not be cleared: ${saveResult.errorMessage}`,
          },
    );
  }, [copyTradeSettings.modeEnabled, saveCopyTradeSettings, selectedAccountIsCsvImport]);

  const handleLiveAccountModeChange = useCallback(
    (liveAccountEnabled: boolean) => {
      if (liveAccountEnabled) {
        const decision = resolveLiveAccountEnableDecision(orderedAccounts);
        if (decision.kind === "select") {
          setPendingLiveAccountSetup(null);
          setActiveAccount(decision.accountId);
          setTradeDataSourceFeedback(null);
          return;
        }

        setPendingLiveAccountSetup({ accountId: selectedAccountId });
        setLiveAccountSetupRequest((current) => current + 1);
        setTradeDataSourceFeedback(
          decision.liveAccountCount === 0
            ? null
            : {
                tone: "neutral",
                message: `Choose one of ${decision.liveAccountCount} Live CSV accounts below. Your current Express account will stay unchanged.`,
              },
        );
        return;
      }

      setPendingLiveAccountSetup(null);
      setTradeDataSourceFeedback(null);
      if (!selectedAccountIsCsvImport) {
        return;
      }

      const projectXAccountId = resolveProjectXAccountId(
        orderedAccounts,
        lastProjectXAccountIdRef.current,
      );
      if (projectXAccountId === null) {
        setTradeDataSourceFeedback({
          tone: "error",
          message: "No ProjectX account is available to switch to. The Live CSV account remains selected.",
        });
        return;
      }

      setActiveAccount(projectXAccountId);
      setTradeDataSourceFeedback({
        tone: "success",
        message: "Switched back to your ProjectX account. The separate Live CSV account was not changed.",
      });
    },
    [orderedAccounts, selectedAccountId, selectedAccountIsCsvImport, setActiveAccount],
  );

  const handleCopyTradeModeChange = useCallback(
    (enabled: boolean) => {
      if (copyTradeTogglePendingRef.current) {
        return;
      }
      if (enabled && selectedAccountIsCsvImport) {
        setCopyTradeToggleFeedback({
          tone: "error",
          message: "Copy Trade Mode is unavailable while this account uses Live CSV imports.",
        });
        return;
      }

      const currentSettings = copyTradeSettingsRef.current;
      const enableConfirmed =
        enabled && !currentSettings.modeEnabled && selectedAccountId !== null
          ? window.confirm(COPY_TRADE_ENABLE_CONFIRMATION_MESSAGE)
          : undefined;
      const decision = prepareCopyTradeModeToggle(currentSettings, enabled, {
        selectedAccountId,
        enableConfirmed,
      });

      if (decision.status !== "ready") {
        setCopyTradeToggleFeedback({
          tone: decision.status === "blocked" ? "error" : "neutral",
          message: decision.message,
        });
        return;
      }

      copyTradeTogglePendingRef.current = true;
      setCopyTradeTogglePending(true);
      setCopyTradeToggleFeedback({
        tone: "neutral",
        message: enabled ? "Enabling Copy Trade Mode..." : "Disabling Copy Trade Mode...",
      });

      const saveResult = saveCopyTradeSettings((current) => updateCopyTradeModeSetting(current, enabled));
      if (!saveResult.ok) {
        setCopyTradeToggleFeedback({
          tone: "error",
          message: `Copy Trade Mode was not changed: ${saveResult.errorMessage}`,
        });
        copyTradeTogglePendingRef.current = false;
        setCopyTradeTogglePending(false);
        return;
      }

      copyTradeToggleTimerRef.current = window.setTimeout(() => {
        setCopyTradeToggleFeedback({ tone: "success", message: decision.message });
        copyTradeTogglePendingRef.current = false;
        copyTradeToggleTimerRef.current = null;
        setCopyTradeTogglePending(false);
      }, 0);
    },
    [saveCopyTradeSettings, selectedAccountId, selectedAccountIsCsvImport],
  );

  const handleResetUncopyEvents = useCallback(() => {
    const confirmed = window.confirm(
      "Reset uncopy event tracking for this leader account? This only hides currently detected follower-only trades from Copy Trade Mode. It does not change trades or account P&L.",
    );
    if (!confirmed) {
      return;
    }

    const saveResult = saveCopyTradeSettings((current) => updateCopyTradeUncopyEventsResetAt(current, selectedAccountId, new Date().toISOString()));
    setCopyTradeToggleFeedback(
      saveResult.ok
        ? { tone: "success", message: "Uncopy event tracking reset for this leader account." }
        : { tone: "error", message: `Copy Trade settings were not saved: ${saveResult.errorMessage}` },
    );
  }, [saveCopyTradeSettings, selectedAccountId]);

  const handleCopyTradeFollowerSelectionChange = useCallback(
    (accountId: number, selected: boolean) => {
      if (selectedAccountId === null) {
        return;
      }

      const currentFollowerIds = getCopyTradeRosterAccountIds(orderedAccounts, selectedAccountId, copyTradeSettingsRef.current).filter(
        (candidateAccountId) => candidateAccountId !== selectedAccountId,
      );
      const alreadySelected = currentFollowerIds.includes(accountId);
      if (selected && !alreadySelected && currentFollowerIds.length >= MAX_COPY_TRADE_FOLLOWERS) {
        setCopyTradeToggleFeedback({
          tone: "error",
          message: `Copy Trade Mode supports up to ${MAX_COPY_TRADE_FOLLOWERS} follower accounts.`,
        });
        return;
      }

      const nextFollowerIds = selected
        ? alreadySelected
          ? currentFollowerIds
          : [...currentFollowerIds, accountId]
        : currentFollowerIds.filter((candidateAccountId) => candidateAccountId !== accountId);
      const saveResult = saveCopyTradeSettings((current) => updateCopyTradeFollowerAccountIds(current, selectedAccountId, nextFollowerIds));
      setCopyTradeToggleFeedback(
        saveResult.ok
          ? { tone: "success", message: "Copy Trade followers updated." }
          : { tone: "error", message: `Copy Trade followers were not saved: ${saveResult.errorMessage}` },
      );
    },
    [orderedAccounts, saveCopyTradeSettings, selectedAccountId],
  );

  const copyTradeToggleDisabled =
    copyTradeTogglePending ||
    selectedAccountIsCsvImport ||
    (selectedAccountId === null && !copyTradeSettings.modeEnabled);
  const copyTradeModeStatusMessage = copyTradeToggleFeedback?.message ?? null;
  const copyTradeModeStatusTone: CopyTradeToggleFeedbackTone = copyTradeToggleFeedback?.tone ?? "neutral";
  const copyTradeModeStatusClassName =
    copyTradeModeStatusTone === "error"
      ? "text-app-negative"
      : copyTradeModeStatusTone === "success"
        ? "text-app-accent"
        : "text-app-muted";
  const tradeDataSourceStatusMessage = tradeDataSourceFeedback?.message ?? null;
  const tradeDataSourceStatusClassName =
    tradeDataSourceFeedback?.tone === "error"
      ? "text-app-negative"
      : tradeDataSourceFeedback?.tone === "success"
        ? "text-app-accent"
        : "text-app-muted";
  const compactCopyTradeModeStatusClassName =
    copyTradeModeStatusTone === "error"
      ? "text-app-negative-text"
      : copyTradeModeStatusTone === "success"
        ? "text-app-accent-text"
        : "text-app-muted-text";
  const compactCopyFollowerCandidates = useMemo(
    () => compactMode.enabled
      ? orderedAccounts.filter(
          (account) =>
            account.id !== selectedAccountId &&
            account.trade_data_source === "projectx" &&
            (account.account_state === "ACTIVE" || account.account_state === "LOCKED_OUT"),
        )
      : [],
    [compactMode.enabled, orderedAccounts, selectedAccountId],
  );
  const activeAccountsLoading = usingShellAccounts ? shellContext.accountsLoading : accountsLoading;

  if (activeAccountsLoading || (compactMode.enabled && orderedAccounts.length > 0 && selectedAccountId === null)) {
    return compactMode.enabled ? <CompactDashboardSkeleton /> : <DashboardLoadingState />;
  }

  if (compactMode.enabled && orderedAccounts.length === 0) {
    const accountsUnavailableMessage = compactAccountsError ?? shellAccountsError;
    return (
      <section className="dashboard-surface pb-8" aria-label="Compact dashboard" data-dashboard-view="compact">
        <Card className="mx-auto max-w-xl p-6 text-center">
          <p className="text-base font-semibold text-app-text">
            {accountsUnavailableMessage ? "Accounts unavailable" : "No accounts available"}
          </p>
          <p className="mt-2 text-sm text-app-muted-text" role={accountsUnavailableMessage ? "alert" : "status"}>
            {accountsUnavailableMessage
              ? `Compact Mode could not load an account: ${accountsUnavailableMessage}`
              : "Add or connect an account before opening Compact Mode."}
          </p>
          {accountsUnavailableMessage && (!usingShellAccounts || reloadShellAccounts) ? (
            <Button
              className="mt-4 min-h-11"
              variant="secondary"
              onClick={() => {
                if (usingShellAccounts) {
                  reloadShellAccounts?.();
                  return;
                }
                void loadAccounts();
              }}
            >
              Retry accounts
            </Button>
          ) : null}
        </Card>
      </section>
    );
  }

  return (
    <div className="dashboard-surface space-y-5 pb-8">
      <h1 className="sr-only">Trading Dashboard</h1>
      <DemoModeNotice compact>
        <p>
          Dashboard filters, account selection, calendar drill-down, and copy-to-clipboard tools explore the fixed sample timeline. Live CSV imports, provider refresh, and copy-trade settings are unavailable.
        </p>
      </DemoModeNotice>
      {providerRefreshError || (shellContext === null && (providerAccountsRefreshing || providerSyncNotice)) ? (
        <Card
          className={cn(
            "p-3 text-xs",
            providerRefreshError || providerSyncNotice?.tone === "error"
              ? "border-app-negative/40 bg-app-negative/10 text-app-negative"
              : providerSyncNotice?.tone === "warning"
                ? "border-app-warning/40 bg-app-warning/10 text-app-warning"
                : "border-app-border bg-app-surface/60 text-app-muted",
          )}
          role={providerRefreshError || providerSyncNotice?.tone === "error" ? "alert" : "status"}
          aria-live="polite"
        >
          {providerRefreshError
            ? `${providerRefreshError} Saved account data remains available.`
            : providerAccountsRefreshing
              ? "Refreshing ProjectX accounts… Saved accounts remain available while this finishes."
              : providerSyncNotice?.message}
        </Card>
      ) : null}
      {activeCalendarMonthRefreshWarning ? (
        <Card
          className="border-app-warning/40 bg-app-warning/10 p-3 text-xs text-app-warning"
          role="alert"
          aria-live="polite"
        >
          {activeCalendarMonthRefreshWarning}
        </Card>
      ) : null}
      <div className="space-y-2">
        <div className="space-y-1.5">
          <div className="max-w-full pb-1">
            <div className="flex flex-col gap-1 rounded-xl border border-app-border/80 bg-app-bg/45 p-1 shadow-none sm:flex-row sm:flex-wrap sm:items-center">
            <div className="flex flex-col gap-1 sm:flex-row">
              <Input
                type="date"
                value={customStartDate}
                max={customEndDate || undefined}
                onChange={(event) => {
                  const nextStartDate = event.target.value;
                  setCustomStartDate(nextStartDate);
                  updateCustomRangeFromDraft(nextStartDate, customEndDate);
                }}
                className={cn(
                  "h-11 w-full min-w-0 rounded-lg border-app-border/80 bg-app-surface/60 px-2 sm:w-[140px]",
                  compactMode.enabled ? "text-xs sm:h-11" : "text-[11px] sm:h-8",
                )}
                aria-label="Custom start date"
              />
              <Input
                type="date"
                value={customEndDate}
                min={customStartDate || undefined}
                onChange={(event) => {
                  const nextEndDate = event.target.value;
                  setCustomEndDate(nextEndDate);
                  updateCustomRangeFromDraft(customStartDate, nextEndDate);
                }}
                className={cn(
                  "h-11 w-full min-w-0 rounded-lg border-app-border/80 bg-app-surface/60 px-2 sm:w-[140px]",
                  compactMode.enabled ? "text-xs sm:h-11" : "text-[11px] sm:h-8",
                )}
                aria-label="Custom end date"
              />
            </div>
            <div className="flex flex-wrap items-center gap-1">
              {METRICS_RANGE_OPTIONS.map((option) => {
                const active = option.key === metricsRange;
                return (
                  <Button
                    key={option.key}
                    variant={active ? "secondary" : "ghost"}
                    size="sm"
                    aria-pressed={active}
                    onClick={() => setMetricsRange(option.key)}
                    className={cn(
                      "min-w-[44px] flex-1 rounded-lg border border-app-border/80 px-2.5 sm:flex-none",
                      compactMode.enabled ? "!h-11 text-xs" : "text-[11px]",
                      active ? "border-app-accent/40 ring-1 ring-app-accent/60" : undefined,
                    )}
                  >
                    {option.label}
                  </Button>
                );
              })}
            </div>
            {!compactMode.enabled && !demoModeEnabled ? (
              <>
            <div className="flex w-full min-w-0 flex-col gap-1 sm:w-auto sm:min-w-[230px]">
              <div className="flex items-center gap-1.5">
                <Toggle
                  checked={liveAccountModeEnabled}
                  onChange={handleLiveAccountModeChange}
                  label="Live Account (CSV)"
                  aria-label={liveAccountModeEnabled ? "Live Account CSV mode is on" : "Live Account CSV mode is off"}
                  aria-describedby={tradeDataSourceStatusMessage ? "trade-data-source-toggle-status" : undefined}
                  disabled={copyTradeTogglePending}
                  className={cn(
                    "h-11 flex-1 justify-center rounded-lg px-2.5 text-[11px] sm:h-8 sm:flex-none",
                    liveAccountModeEnabled ? "border-app-accent/50 bg-app-accent/15 ring-1 ring-app-accent/50" : undefined,
                  )}
                />
                <Badge
                  variant={selectedAccountIsCsvImport ? "positive" : "neutral"}
                  className="h-8 rounded-lg"
                >
                  {selectedAccountIsCsvImport ? "CSV" : "API"}
                </Badge>
              </div>
              {tradeDataSourceStatusMessage ? (
                <p
                  id="trade-data-source-toggle-status"
                  role="status"
                  aria-live={tradeDataSourceFeedback?.tone === "error" ? "assertive" : "polite"}
                  className={cn("text-[10px] leading-snug", tradeDataSourceStatusClassName)}
                >
                  {tradeDataSourceStatusMessage}
                </p>
              ) : null}
            </div>
            <div className="flex w-full min-w-0 flex-col gap-1 sm:w-auto sm:min-w-[230px]">
              <div className="flex items-center gap-1.5">
                <Toggle
                  checked={copyTradeModeConfigured}
                  onChange={handleCopyTradeModeChange}
                  label={
                    copyTradeTogglePending
                      ? "Updating..."
                      : selectedAccountIsCsvImport
                        ? "Copy Trade unavailable"
                        : "Copy Trade Mode"
                  }
                  aria-label={
                    selectedAccountIsCsvImport
                      ? "Copy Trade Mode is unavailable for Live CSV accounts"
                      : copyTradeModeConfigured
                      ? "Copy Trade Mode is on"
                      : copyTradeToggleDisabled
                        ? "Copy Trade Mode unavailable"
                        : "Copy Trade Mode is off"
                  }
                  aria-describedby={copyTradeModeStatusMessage ? "copy-trade-toggle-status" : undefined}
                  disabled={copyTradeToggleDisabled}
                  className={cn(
                    "h-11 flex-1 justify-center rounded-lg px-2.5 text-[11px] sm:h-8 sm:flex-none",
                    copyTradeModeConfigured ? "border-app-accent/50 bg-app-accent/15 ring-1 ring-app-accent/50" : undefined,
                  )}
                />
                <Badge variant={copyTradeTogglePending ? "accent" : copyTradeModeConfigured ? "positive" : "neutral"} className="h-8 rounded-lg">
                  {copyTradeTogglePending ? "Saving" : copyTradeModeConfigured ? "On" : "Off"}
                </Badge>
              </div>
              {copyTradeModeStatusMessage ? (
                <p
                  id="copy-trade-toggle-status"
                  role="status"
                  aria-live={copyTradeModeStatusTone === "error" ? "assertive" : "polite"}
                  className={cn("text-[10px] leading-snug", copyTradeModeStatusClassName)}
                >
                  {copyTradeModeStatusMessage}
                </p>
              ) : null}
            </div>
              </>
            ) : null}
            {!compactMode.enabled ? (
            <Suspense fallback={<Skeleton className="h-8 w-full rounded-lg sm:w-32" />}>
              <CopyFullStatsButton
                metrics={copyFullStatsMetrics}
                rangeLabel={fullStatsRangeLabel}
                calendarDays={analyticsPnlCalendarDays}
                disabled={selectedAccountId === null || summaryLoading || pnlCalendarLoading || metricsTradesLoading || summaryError !== null || pnlCalendarError !== null}
                className="h-11 w-full rounded-lg px-2.5 text-[11px] sm:h-8 sm:w-auto"
              />
            </Suspense>
            ) : null}
            </div>
          </div>
          {customRangeInvalid ? (
            <p className={cn("w-full text-xs", compactMode.enabled ? "text-app-negative-text" : "text-app-negative")}>
              End date must be on or after start date.
            </p>
          ) : null}
        </div>

        {!compactMode.enabled && !demoModeEnabled && liveAccountModeEnabled ? <TradeImportPanel
          accountId={selectedAccountId}
          tradeDataSource={selectedAccount?.trade_data_source ?? null}
          liveAccounts={liveCsvAccounts}
          accountsLoading={accountsLoading}
          accountSetupRequest={liveAccountSetupRequest}
          onImportComplete={reloadDashboard}
          onAccountCreated={handleLiveImportAccountCreated}
          onAccountSelected={handleLiveImportAccountCreated}
        /> : null}

        {!selectedAccountIsCsvImport && selectedAccount?.account_state === "MISSING" ? (
          <Card className="border-app-warning/40 bg-app-warning/10 p-4">
            <p className={cn("text-sm", compactMode.enabled ? "text-app-text" : "text-app-warning")}>
              This account is missing from ProjectX. Metrics and trade history are being served from locally stored data.
            </p>
          </Card>
        ) : null}
      </div>

      {compactMode.enabled && !demoModeEnabled ? (
        <Card className="border-app-border/80 p-3 sm:p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-sm font-semibold text-app-text">Copy Trade accounts</h2>
                <Badge
                  variant={copyTradeModeConfigured ? "accent" : "neutral"}
                  className={cn(
                    "!text-xs",
                    copyTradeModeConfigured ? "!text-app-accent-text" : "!text-app-text",
                  )}
                >
                  {copyTradeModeConfigured
                    ? `${1 + copyTradeFollowerAccountIds.length} included`
                    : "Leader only"}
                </Badge>
              </div>
              <p className="mt-1 text-xs text-app-muted-text">
                Compact KPIs, charts, score, calendar, and Recent Trades use the same selected account roster.
              </p>
            </div>
            <Toggle
              checked={copyTradeModeConfigured}
              onChange={handleCopyTradeModeChange}
              label={copyTradeTogglePending ? "Updating..." : "Copy Trade Mode"}
              aria-label={copyTradeModeConfigured ? "Copy Trade Mode is on" : "Copy Trade Mode is off"}
              aria-describedby={copyTradeModeStatusMessage ? "compact-copy-trade-status" : undefined}
              disabled={copyTradeToggleDisabled}
              className={cn(
                "h-11 shrink-0 justify-center rounded-lg px-3 text-xs",
                copyTradeModeConfigured ? "border-app-accent/50 bg-app-accent/15 ring-1 ring-app-accent/50" : undefined,
              )}
            />
          </div>
          {copyTradeModeConfigured ? (
            <div className="mt-3 border-t border-app-border/70 pt-3">
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-app-muted-text">
                Included followers
              </p>
              {compactCopyFollowerCandidates.length > 0 ? (
                <div className="mt-2 flex flex-wrap gap-2">
                  {compactCopyFollowerCandidates.map((account) => {
                    const included = copyTradeFollowerAccountIds.includes(account.id);
                    const atLimit = copyTradeFollowerAccountIds.length >= MAX_COPY_TRADE_FOLLOWERS;
                    return (
                      <Button
                        key={account.id}
                        type="button"
                        variant={included ? "secondary" : "ghost"}
                        size="sm"
                        aria-pressed={included}
                        disabled={copyTradeTogglePending || (!included && atLimit)}
                        onClick={() => handleCopyTradeFollowerSelectionChange(account.id, !included)}
                        className={cn(
                          "min-h-11 rounded-xl border px-3 text-xs",
                          included ? "border-app-accent/50 bg-app-accent/10" : "border-app-border/80",
                        )}
                      >
                        {included ? "Included: " : "Add: "}{account.name || account.provider_name || `Account ${account.id}`}
                      </Button>
                    );
                  })}
                </div>
              ) : (
                <p className="mt-2 text-xs text-app-muted-text">No eligible ProjectX follower accounts are available.</p>
              )}
            </div>
          ) : null}
          {copyTradeModeStatusMessage ? (
            <p
              id="compact-copy-trade-status"
              role="status"
              aria-live={copyTradeModeStatusTone === "error" ? "assertive" : "polite"}
              className={cn("mt-2 text-xs", compactCopyTradeModeStatusClassName)}
            >
              {copyTradeModeStatusMessage}
            </p>
          ) : null}
        </Card>
      ) : null}

      {copyTradeModeActive ? (
        <CopyTradePanel
          rows={copyTradeRows}
          totals={copyTradeTotals}
          driftSummary={copyTradeDriftSummary}
          driftResetAt={copyTradeDriftResetAt}
          accounts={orderedAccounts}
          leaderAccountId={selectedAccountId}
          selectedFollowerAccountIds={copyTradeFollowerAccountIds}
          maxFollowers={MAX_COPY_TRADE_FOLLOWERS}
          loading={summaryLoading || pnlCalendarLoading || copyTradeTogglePending}
          onFollowerSelectionChange={handleCopyTradeFollowerSelectionChange}
          onResetUncopyEvents={handleResetUncopyEvents}
        />
      ) : null}

      {!selectedAccountIsCsvImport && selectedAccount?.provider_data_stale ? (
        <Card className="border-app-warning/40 bg-app-warning/10 p-4">
          <p className={cn("text-sm font-medium", compactMode.enabled ? "text-app-text" : "text-app-warning")}>
            Provider account data is stale.
          </p>
          <p className={cn("mt-1 text-xs", compactMode.enabled ? "text-app-muted-text" : "text-app-warning/90")}>
            {`${formatProviderLastSeen(selectedAccount.last_seen_at)}. ${
              getAvailableAccountBalance(selectedAccount.balance) === null
                ? "The current balance is unavailable, so balance-based charts and risk metrics remain unanchored."
                : "Balance and trading status may be out of date."
            }`}
          </p>
        </Card>
      ) : null}

      {compactMode.enabled ? (
        <CompactDashboardView
          accountName={compactAccountName}
          rangeLabel={compactRangeLabel}
          rangeStartDate={compactRangeBounds.startDate}
          rangeEndDate={compactRangeBounds.endDate}
          summary={compactSummary}
          score={compactSustainability.score}
          scoreBreakdown={{
            label: compactSustainability.label,
            riskScore: compactSustainability.riskScore,
            consistencyScore: compactSustainability.consistencyScore,
            edgeScore: compactSustainability.edgeScore,
            sampleSize: compactSustainability.debug.nDays,
            sampleConfidence: compactSustainability.debug.confidence,
          }}
          performanceContext={{
            tradingDayCount: compactDays.length,
            maxDrawdown: compactMaxDrawdown,
            riskBase: compactRiskBase?.value ?? null,
            riskBaseLabel: compactRiskBase?.label ?? "Risk base unavailable",
          }}
          days={compactDays}
          calendarDays={compactCalendarDays}
          trades={compactTrades}
          summaryLoading={compactSummaryLoading}
          summaryError={compactSummaryError}
          daysLoading={compactDaysLoading}
          daysError={compactDaysError}
          tradesLoading={compactTradesLoading}
          tradesError={compactTradesError}
          journalDays={compactJournalDays}
          journalDaysLoading={compactJournalDaysLoading}
          journalDaysError={compactJournalDaysError}
          selectedDate={activeSelectedTradeDate}
          selectedDateLabel={selectedTradeDateLabel}
          calendarScopeKey={compactCalendarScopeKey}
          dataWarnings={compactViewWarnings}
          accountNameById={compactAccountNameById}
          onDaySelect={handleSelectedTradeDateChange}
          onClearDayFilter={clearSelectedTradeDate}
          onJournalDayOpen={openJournalForDate}
          onCalendarVisibleRangeChange={handleCompactCalendarVisibleRangeChange}
        />
      ) : (
        <>
      <MasonryGrid>
        {summaryLoading ? (
          Array.from({ length: 8 }).map((_, index) => (
            <MetricCard
              key={`summary-loading-${index}`}
              title="Loading"
              primaryValue="..."
              className="animate-pulse sm:col-span-2 md:col-span-3 lg:col-span-4"
            />
          ))
        ) : summaryError ? (
          <Card className="col-span-full p-4">
            <p className="text-sm text-app-negative">{summaryError}</p>
          </Card>
        ) : (
          <>
            <MetricCard
              title={copyTradeStatsActive ? "Copy Trade Net" : "Performance"}
              primaryValue={formatMetricValue(netPnlMetric, formatPnl)}
              primaryClassName={cn("tracking-tight drop-shadow-[0_1px_12px_rgb(var(--theme-bg)/0.5)]", performancePrimaryClassName)}
              subtitle={copyTradeStatsActive ? "Combined copied-account net P&L." : "Net realized PnL after fees."}
              info={
                copyTradeStatsActive
                  ? "Copy Trade Net adds the active leader result plus enabled active follower account results."
                  : "Realized net profit and loss after fees in the selected range."
              }
              accentClassName={performanceAccentClassName}
              className={cn(
                "z-20 isolate overflow-visible sm:col-span-2 md:col-span-2 md:row-start-1 md:col-start-1 lg:col-span-3 lg:row-start-1 lg:col-start-1",
                performanceCardClassName,
              )}
              contentClassName="relative mt-2.5 space-y-2.5"
            >
              <div aria-hidden="true" className={cn("pointer-events-none absolute -right-8 -top-10 h-24 w-24 rounded-full blur-2xl", performanceGlowClassName)} />
              <div className="relative rounded-xl border border-app-text/10 bg-app-bg/35 p-2.5 backdrop-blur-sm">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant={performanceSignalVariant}>{performanceSignalLabel}</Badge>
                  <Badge variant="accent">
                    {copyTradeStatsActive ? `${formatInteger(copyTradeTotals.activeCopiedAccountCount)} Accounts` : `${formatInteger(displayTradeCount)} Trades`}
                  </Badge>
                  <span className="ml-auto text-[10px] uppercase tracking-[0.12em] text-app-muted">
                    {copyTradeStatsActive ? `Leader ${formatPnl(copyTradeTotals.leaderNetPnl)}` : `Win ${formatPercent(summary.win_rate, 1)}`}
                  </span>
                </div>
                {copyTradeStatsActive ? (
                  <div className="mt-2.5 grid gap-1.5 sm:grid-cols-2">
                    <div className="rounded-lg border border-app-border/75 bg-app-surface/55 px-2 py-1.5">
                      <p className="text-[10px] uppercase tracking-[0.12em] text-app-muted">Combined Daily P&L</p>
                      <p className={cn("mt-1 text-sm font-semibold", pnlClass(copyTradeTotals.combinedDailyPnl))}>
                        {formatPnl(copyTradeTotals.combinedDailyPnl)}
                      </p>
                    </div>
                    <div className="rounded-lg border border-app-border/75 bg-app-surface/55 px-2 py-1.5">
                      <p className="text-[10px] uppercase tracking-[0.12em] text-app-muted">Combined Balance</p>
                      <p className="mt-1 text-sm font-semibold text-app-text">
                        {formatAccountBalance(copyTradeTotals.combinedBalance)}
                      </p>
                    </div>
                    <div className="rounded-lg border border-app-border/75 bg-app-surface/55 px-2 py-1.5">
                      <p className="text-[10px] uppercase tracking-[0.12em] text-app-muted">Leader P&L</p>
                      <p className={cn("mt-1 text-sm font-semibold", pnlClass(copyTradeTotals.leaderNetPnl))}>
                        {formatPnl(copyTradeTotals.leaderNetPnl)}
                      </p>
                    </div>
                    <div className="rounded-lg border border-app-border/75 bg-app-surface/55 px-2 py-1.5">
                      <p className="text-[10px] uppercase tracking-[0.12em] text-app-muted">Follower P&L</p>
                      <p className={cn("mt-1 text-sm font-semibold", pnlClass(copyTradeTotals.followerContributionNetPnl))}>
                        {formatPnl(copyTradeTotals.followerContributionNetPnl)}
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="mt-2.5 grid gap-1.5 sm:grid-cols-2">
                    <div className="rounded-lg border border-app-border/75 bg-app-surface/55 px-2 py-1.5">
                      <p className="text-[10px] uppercase tracking-[0.12em] text-app-muted">Profit / Day</p>
                      <p className={cn("mt-1 text-sm font-semibold", pnlClass(summary.profit_per_day))}>
                        {formatMetricValue(profitPerDayMetric, formatPnl)}
                      </p>
                    </div>
                    <div className="rounded-lg border border-app-border/75 bg-app-surface/55 px-2 py-1.5">
                      <p className="text-[10px] uppercase tracking-[0.12em] text-app-muted">Efficiency / Hour</p>
                      <p className={cn("mt-1 text-sm font-semibold", pnlClass(summary.efficiency_per_hour))}>
                        {formatMetricValue(efficiencyPerHourMetric, formatPnl)}
                      </p>
                    </div>
                  </div>
                )}
                <div className="mt-2.5 rounded-lg border border-app-border/75 bg-app-surface/55 px-2.5 py-2">
                  <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.12em] text-app-muted">
                    <span>Sizing Benchmark</span>
                    <InfoPopover
                      content={sizingBenchmarkTooltipContent}
                      label="Sizing Benchmark"
                      panelClassName="left-0 right-auto ml-1 w-64 max-w-[calc(100vw-2.5rem)] text-[10px] leading-snug"
                    />
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-2">
                    <Badge
                      variant={sizingBenchmarkBadgeVariant(summary.sizingBenchmark.benchmarkLabel)}
                      className="max-w-full normal-case tracking-normal"
                    >
                      {summary.sizingBenchmark.benchmarkLabel}
                    </Badge>
                    <span className="text-[10px] text-app-muted">
                      {formatSizingBenchmarkSubtitle(
                        summary.sizingBenchmark.benchmarkSizeUsed,
                        summary.tradeCountUsedForSizingStats,
                      )}
                    </span>
                  </div>
                  {formatMicroPositionSize(summary.sizingBenchmark.benchmarkSizeUsed, summary.tradeCountUsedForSizingStats) !== "N/A" ? (
                    <p className="mt-1 text-[10px] text-app-muted-strong">
                      Benchmark Size:{" "}
                      {formatMicroPositionSize(summary.sizingBenchmark.benchmarkSizeUsed, summary.tradeCountUsedForSizingStats)}
                    </p>
                  ) : null}
                  <div className="mt-3 flex flex-wrap items-end justify-between gap-x-3 gap-y-2 border-t border-app-border/70 pt-2.5">
                    <div className="min-w-0">
                      <p className="text-[9px] uppercase tracking-[0.12em] text-app-muted-strong">
                        {sizingBenchmarkDeltaLabel(summary.sizingBenchmark.benchmarkDiff)}
                      </p>
                      <div className="mt-1 flex flex-wrap items-end gap-x-3 gap-y-1">
                        <p className={cn("text-lg font-semibold tracking-tight", pnlClass(summary.sizingBenchmark.benchmarkDiff))}>
                          {formatPnl(summary.sizingBenchmark.benchmarkDiff)}
                        </p>
                        <p
                          className={cn(
                            "pb-0.5 text-[11px] font-medium",
                            summary.sizingBenchmark.benchmarkDiff > 0
                              ? "text-app-positive"
                              : summary.sizingBenchmark.benchmarkDiff < 0
                                ? "text-app-negative"
                                : "text-app-muted",
                          )}
                        >
                          {sizingBenchmarkComparisonLabel(summary.sizingBenchmark)}
                        </p>
                      </div>
                    </div>
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-3 border-t border-app-border/70 pt-2.5">
                    <div className="min-w-0">
                      <p className="text-[9px] uppercase tracking-[0.12em] text-app-muted-strong">Benchmark Net</p>
                      <p className={cn("mt-1 text-sm font-semibold", pnlClass(summary.sizingBenchmark.benchmarkNetPnl))}>
                        {formatPnl(summary.sizingBenchmark.benchmarkNetPnl)}
                      </p>
                    </div>
                    <div className="min-w-0 text-right">
                      <p className="text-[9px] uppercase tracking-[0.12em] text-app-muted-strong">Actual Net</p>
                      <p className={cn("mt-1 text-sm font-semibold", pnlClass(summary.net_pnl))}>
                        {formatPnl(summary.net_pnl)}
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </MetricCard>

            <MetricCard
              title="Edge"
              primaryValue={formatMetricValue(expectancyPerTradeMetric, formatPnl)}
              primaryClassName={cn("tracking-tight drop-shadow-[0_1px_12px_rgb(var(--theme-bg)/0.5)]", edgePrimaryClassName)}
              subtitle="Expected net result per trade."
              info="Expectancy combines your win rate and payoff profile into average dollars per trade."
              accentClassName={edgeAccentClassName}
              className={cn(
                "relative isolate sm:col-span-2 md:col-span-2 md:row-start-1 md:col-start-3 lg:col-span-6 lg:row-start-1 lg:col-start-4",
                edgeCardClassName,
              )}
              contentClassName="relative mt-2.5 flex flex-col gap-2.5 space-y-0"
            >
              <div
                aria-hidden="true"
                className={cn("pointer-events-none absolute -right-14 -top-12 h-28 w-28 rounded-full blur-3xl", edgeGlowClassName)}
              />
              <div className="relative rounded-xl border border-app-text/10 bg-app-bg/35 p-2.5 backdrop-blur-sm">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant={edgeSignalVariant}>{edgeSignalLabel}</Badge>
                  <Badge variant="accent">{`PF ${formatNumber(summary.profit_factor)}`}</Badge>
                  <span className="ml-auto text-[10px] uppercase tracking-[0.12em] text-app-muted">{`WR ${formatPercent(summary.win_rate, 1)}`}</span>
                </div>
                <div className="mt-2.5 grid gap-1.5 sm:grid-cols-3">
                  <div className="rounded-lg border border-app-border/75 bg-app-surface/55 px-2 py-1.5">
                    <p className="text-[10px] uppercase tracking-[0.12em] text-app-muted">Profit Factor</p>
                    <p className="mt-1 text-sm font-semibold text-app-accent">{formatNumber(summary.profit_factor)}</p>
                  </div>
                  <div className="rounded-lg border border-app-border/75 bg-app-surface/55 px-2 py-1.5">
                    <p className="text-[10px] uppercase tracking-[0.12em] text-app-muted">Win Rate</p>
                    <p className="mt-1 text-sm font-semibold text-app-text">{formatPercent(summary.win_rate, 1)}</p>
                  </div>
                  <div className="rounded-lg border border-app-border/75 bg-app-surface/55 px-2 py-1.5">
                    <p className="text-[10px] uppercase tracking-[0.12em] text-app-muted">W/L Ratio</p>
                    <p className="mt-1 text-sm font-semibold text-app-text">
                      {formatMetricValue(derivedMetrics.winLossRatio, (value) => `${formatNumber(value)}x`)}
                    </p>
                  </div>
                </div>
                <div className="mt-2.5 space-y-1">
                  <div className="flex items-center justify-between text-[10px] uppercase tracking-[0.12em] text-app-muted">
                    <span>Outcome Mix</span>
                    <span>{`${summary.win_count}W / ${summary.loss_count}L / ${summary.breakeven_count} BE`}</span>
                  </div>
                  <div className="relative h-2 overflow-hidden rounded-full border border-app-border/80 bg-app-surface/85">
                    <div className="absolute inset-y-0 left-0 bg-gradient-to-r from-app-positive/95 to-app-positive/85" style={{ width: `${edgeWinShare}%` }} />
                    <div
                      className="absolute inset-y-0 bg-gradient-to-r from-app-warning/85 to-app-muted/75"
                      style={{ left: `${edgeWinShare}%`, width: `${edgeBreakevenShare}%` }}
                    />
                    <div className="absolute inset-y-0 right-0 bg-gradient-to-l from-app-negative/95 to-app-negative/85" style={{ width: `${edgeLossShare}%` }} />
                  </div>
                </div>
              </div>
            </MetricCard>

            <MetricCard
              title="Swing"
              primaryValue={formatMetricValue(derivedMetrics.stability.dailyPnlVolatility, formatCurrency)}
              primaryClassName="bg-gradient-to-r from-app-secondary via-app-accent to-app-secondary bg-clip-text text-transparent"
              subtitle="Daily PnL volatility ($)."
              info="Stability uses worst-day % of net PnL; lower worst-day concentration implies higher stability."
              accentClassName="bg-gradient-to-r from-app-secondary/80 via-app-accent/30 to-app-secondary/80"
              className="relative overflow-hidden p-3 sm:col-span-2 md:col-span-2 md:row-start-2 md:col-start-1 lg:col-span-3 lg:col-start-1 lg:row-start-2"
              contentClassName="relative mt-2.5 flex flex-col gap-2.5 space-y-0"
            >
              <div className="relative overflow-hidden rounded-xl border border-app-accent/20 bg-[radial-gradient(120%_130%_at_8%_0%,rgb(var(--theme-accent-secondary)/0.16),rgb(var(--theme-surface)/0.28)_42%,rgb(var(--theme-surface)/0.78)_100%)] p-2.5">
                <div aria-hidden="true" className="pointer-events-none absolute -left-8 top-0 h-20 w-20 rounded-full bg-app-secondary/20 blur-2xl" />
                <div aria-hidden="true" className="pointer-events-none absolute -right-6 bottom-1 h-24 w-24 rounded-full bg-app-accent/15 blur-2xl" />
                <div className="relative space-y-2.5">
                  <SplitBar
                    className="rounded-md border border-app-border/70 bg-app-bg/35 p-2"
                    leftLabel="Best Day"
                    rightLabel="Worst Day"
                    leftValue={formatMetricValue(derivedMetrics.stability.bestDay, formatPnl)}
                    rightValue={formatMetricValue(derivedMetrics.stability.worstDay, formatPnl)}
                    leftMagnitude={Math.abs(derivedMetrics.stability.bestDay.value ?? 0)}
                    rightMagnitude={Math.abs(derivedMetrics.stability.worstDay.value ?? 0)}
                    leftBarClassName="bg-gradient-to-r from-app-positive/95 to-app-accent/80"
                    rightBarClassName="bg-gradient-to-l from-app-negative/90 to-app-warning/75"
                  />
                  <MiniStatList
                    className="gap-1.5"
                    items={[
                      {
                        label: "Best Day",
                        value: `${formatMetricValue(derivedMetrics.stability.bestDay, formatPnl)} (${formatMetricValue(
                          derivedMetrics.stability.bestDayPercentOfNet,
                          (value) => formatPercent(value, 1),
                        )})`,
                        valueClassName: metricPnlClass(derivedMetrics.stability.bestDay),
                      },
                      {
                        label: "Worst Day",
                        value: `${formatMetricValue(derivedMetrics.stability.worstDay, formatPnl)} (${formatMetricValue(
                          derivedMetrics.stability.worstDayPercentOfNet,
                          (value) => formatPercent(value, 1),
                        )})`,
                        valueClassName: metricPnlClass(derivedMetrics.stability.worstDay),
                      },
                      { label: "Median Day", value: formatMetricValue(derivedMetrics.stability.medianDayPnl, formatPnl) },
                      { label: "Avg Green", value: formatMetricValue(derivedMetrics.stability.avgGreenDay, formatPnl) },
                      { label: "Avg Red", value: formatMetricValue(derivedMetrics.stability.avgRedDay, formatPnl) },
                      {
                        label: "Red Day %",
                        value: formatMetricValue(derivedMetrics.stability.redDayPercent, (value) => formatPercent(value, 1)),
                      },
                    ]}
                  />
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5">
                <Chip
                  label="Worst Day Impact"
                  value={
                    derivedMetrics.stability.nukeRatio.value === null
                      ? formatMetricValueWithNote(derivedMetrics.stability.nukeRatio, (value) => formatNumber(value, 1))
                      : `Worst Day = ${formatNumber(derivedMetrics.stability.nukeRatio.value, 1)} days of avg profit`
                  }
                  className={
                    derivedMetrics.stability.nukeRatio.value !== null && derivedMetrics.stability.nukeRatio.value >= 10
                      ? "border-app-negative/40 bg-app-negative/15 text-app-negative"
                      : "border-app-accent/40 bg-app-accent/15 text-app-accent"
                  }
                />
                <Chip
                  label="G/R Size Ratio"
                  value={formatMetricValueWithNote(derivedMetrics.stability.greenRedDaySizeRatio, (value) => `${formatNumber(value)}x`)}
                  className="border-app-secondary/30 bg-app-secondary/10 text-app-secondary"
                />
              </div>
              <GaugeBar
                label="Stability"
                value={stabilityScore.value}
                valueLabel={formatMetricValue(stabilityScore, (value) => `${formatNumber(value, 0)}%`)}
                className="space-y-1.5 rounded-md border border-app-border/70 bg-app-bg/35 p-2"
                fillClassName="bg-gradient-to-r from-app-accent/85 via-app-secondary/80 to-app-secondary/80"
              />
              <p className="rounded-md border border-app-secondary/30 bg-[linear-gradient(120deg,rgb(var(--theme-accent-secondary)/0.15),rgb(var(--theme-surface)/0.48)_45%,rgb(var(--theme-accent)/0.12)_100%)] px-2 py-1 text-[11px] text-app-text-soft">
                <span className="font-semibold text-app-accent">Insight:</span> {derivedMetrics.stability.insight}
              </p>
            </MetricCard>

            <MetricCard
              title="Risk Control"
              primaryValue={formatMetricValue(maxDrawdownMetric, formatPnl)}
              primaryClassName={cn("tracking-tight drop-shadow-[0_1px_12px_rgb(var(--theme-bg)/0.5)]", riskPrimaryClassName)}
              subtitle="Peak-to-trough drawdown with account-risk context."
              info="Drawdown control is graded from max drawdown as a share of risk base. Known Topstep combines use Maximum Loss Limit as risk capital; other accounts use current balance when available and then fall back to peak equity inferred from daily PnL. Profit giveback is shown separately and does not determine control."
              accentClassName={riskAccentClassName}
              className={cn(
                "self-start sm:col-span-2 md:col-span-2 md:row-start-1 md:col-start-5 lg:col-span-3 lg:row-start-1 lg:col-start-10",
                riskCardClassName,
              )}
              contentClassName="mt-2.5 space-y-2.5"
            >
              <div className="rounded-xl border border-app-text/10 bg-app-bg/35 p-2.5 backdrop-blur-sm">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant={riskSignalVariant}>{riskSignalLabel}</Badge>
                  <span className="ml-auto text-[10px] uppercase tracking-[0.12em] text-app-muted">Account Risk</span>
                </div>
                <div className="mt-2.5 space-y-1.5">
                  <div className="flex items-center justify-between text-[10px] uppercase tracking-[0.12em] text-app-muted">
                    <span>Max DD % of Risk Base</span>
                    <span className="font-semibold text-app-text-soft">{formatMetricValue(drawdownPercentOfEquityBase, formatPercent)}</span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full border border-app-border/80 bg-app-surface/85">
                    <div
                      aria-hidden="true"
                      className={cn(
                        "h-full transition-all duration-500",
                        drawdownPercentOfEquityBase.value === null || drawdownPercentOfEquityBase.value <= 10
                          ? "bg-gradient-to-r from-app-accent/90 to-app-positive/80"
                          : drawdownPercentOfEquityBase.value <= 15
                            ? "bg-gradient-to-r from-app-warning/90 to-app-warning/80"
                            : "bg-gradient-to-r from-app-negative/95 to-app-warning/85",
                      )}
                      style={{ width: `${riskPressurePercent}%` }}
                    />
                  </div>
                  <div className="flex items-center justify-between text-[9px] uppercase tracking-[0.12em] text-app-muted">
                    <span>Risk pressure scale</span>
                    <span>{formatNumber(RISK_PRESSURE_FULL_SCALE_PERCENT, 0)}% = full bar</span>
                  </div>
                  <p className="text-[10px] text-app-muted">{drawdownEquityBase.detail}</p>
                </div>
              </div>
              <MiniStatList
                items={[
                  { label: "Max DD % of Risk Base", value: formatMetricValue(drawdownPercentOfEquityBase, formatPercent) },
                  { label: "Profit Giveback", value: formatMetricValue(drawdownPercentOfNet, formatPercent) },
                  { label: "Risk Base", value: drawdownEquityBase.value === null ? "N/A" : formatCurrency(drawdownEquityBase.value) },
                  { label: "Basis", value: drawdownEquityBase.label },
                  {
                    label: "Avg Size Used",
                    value: formatMicroPositionSize(summary.averagePositionSize, summary.tradeCountUsedForSizingStats),
                  },
                  {
                    label: "Median Size Used",
                    value: formatMicroPositionSize(summary.medianPositionSize, summary.tradeCountUsedForSizingStats),
                  },
                  { label: "Avg Drawdown", value: formatPnl(summary.average_drawdown), valueClassName: metricPnlClass({ value: summary.average_drawdown }) },
                  { label: "DD Length", value: `${formatNumber(summary.max_drawdown_length_hours, 1)} h` },
                ]}
              />
            </MetricCard>

            <MetricCard
              title="Direction"
              primaryValue={directionPrimaryValue}
              primaryClassName="bg-gradient-to-r from-app-positive via-app-accent to-app-negative bg-clip-text text-transparent"
              subtitle={
                directionSplit.longPercent.value === null
                  ? directionSplit.longPercent.missingReason ?? "Needs directional trade history."
                  : "Long vs short trade mix."
              }
              info="Long % is long trades divided by total directional trades for this range."
              accentClassName="bg-gradient-to-r from-app-positive/80 via-app-accent/25 to-app-negative/80"
              className="relative flex flex-col overflow-hidden md:col-span-2 md:row-start-2 md:col-start-3 lg:col-span-6 lg:col-start-4 lg:row-start-2"
              contentClassName="relative mt-2.5 flex flex-col gap-2.5 space-y-0"
            >
              <div className="relative overflow-hidden rounded-xl border border-app-accent/20 bg-[radial-gradient(120%_130%_at_6%_0%,rgb(var(--theme-positive)/0.16),rgb(var(--theme-surface)/0.25)_42%,rgb(var(--theme-surface)/0.75)_100%)] p-2.5">
                <div aria-hidden="true" className="pointer-events-none absolute -left-8 top-0 h-20 w-20 rounded-full bg-app-positive/20 blur-2xl" />
                <div aria-hidden="true" className="pointer-events-none absolute -right-6 bottom-1 h-24 w-24 rounded-full bg-app-negative/15 blur-2xl" />
                <div className="relative grid gap-2.5 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
                  <DonutRing
                    className="lg:items-start"
                    segments={[
                      {
                        label: "Long",
                        value: directionSplit.longPercent.value,
                        valueLabel: formatMetricValue(directionSplit.longPercent, (value) => formatPercent(value, 0)),
                        color: "rgb(var(--theme-positive)/0.95)",
                      },
                      {
                        label: "Short",
                        value: directionSplit.shortPercent.value,
                        valueLabel: formatMetricValue(directionSplit.shortPercent, (value) => formatPercent(value, 0)),
                        color: "rgb(var(--theme-negative)/0.95)",
                      },
                    ]}
                    centerLabel={directionPrimaryValue}
                    centerSubLabel="Direction"
                  />
                    <div className="space-y-1.5">
                      <div className="overflow-hidden rounded-lg border border-app-border/70 bg-app-bg/45 shadow-[inset_0_1px_0_rgb(var(--theme-muted)/0.07)]">
                        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,0.9fr)_minmax(0,0.9fr)] border-b border-app-border/65 bg-app-surface/90 px-2 py-1 text-[9px] uppercase tracking-[0.12em] text-app-muted">
                          <span>Side Comparison</span>
                          <span className="text-right text-app-positive">Long</span>
                          <span className="text-right text-app-negative">Short</span>
                      </div>
                      {[
                        {
                          label: "Trades",
                          long: formatMetricValue(derivedMetrics.direction.longTrades, formatInteger),
                          short: formatMetricValue(derivedMetrics.direction.shortTrades, formatInteger),
                        },
                        {
                          label: "WR",
                          long: formatMetricValue(derivedMetrics.direction.longWinRate, (value) => formatPercent(value, 1)),
                          short: formatMetricValue(derivedMetrics.direction.shortWinRate, (value) => formatPercent(value, 1)),
                        },
                        {
                          label: "Expectancy",
                          long: formatMetricValue(derivedMetrics.direction.longExpectancy, formatPnl),
                          short: formatMetricValue(derivedMetrics.direction.shortExpectancy, formatPnl),
                        },
                        {
                          label: "PF",
                          long: formatMetricValue(derivedMetrics.direction.longProfitFactor, (value) => `${formatNumber(value)}x`),
                          short: formatMetricValue(derivedMetrics.direction.shortProfitFactor, (value) => `${formatNumber(value)}x`),
                        },
                        {
                          label: "Avg Win / Loss",
                          long: `${formatMetricValue(derivedMetrics.direction.longAvgWin, formatPnl)} / ${formatMetricValue(
                            derivedMetrics.direction.longAvgLoss,
                            formatPnl,
                          )}`,
                          short: `${formatMetricValue(derivedMetrics.direction.shortAvgWin, formatPnl)} / ${formatMetricValue(
                            derivedMetrics.direction.shortAvgLoss,
                            formatPnl,
                          )}`,
                        },
                        {
                          label: "Large Loss %",
                          long: formatMetricValueWithNote(derivedMetrics.direction.longLargeLossRate, (value) => formatPercent(value, 1)),
                          short: formatMetricValueWithNote(derivedMetrics.direction.shortLargeLossRate, (value) => formatPercent(value, 1)),
                        },
                        {
                          label: "PnL Share",
                          long: `${formatMetricValue(derivedMetrics.direction.longPnl, formatPnl)} (${formatMetricValueWithNote(
                            derivedMetrics.direction.longPnlShare,
                            (value) => formatPercent(value, 1),
                          )})`,
                          short: `${formatMetricValue(derivedMetrics.direction.shortPnl, formatPnl)} (${formatMetricValueWithNote(
                            derivedMetrics.direction.shortPnlShare,
                            (value) => formatPercent(value, 1),
                          )})`,
                        },
                      ].map((row, rowIndex) => (
                        <div
                          key={row.label}
                          className={`grid grid-cols-[minmax(0,1fr)_minmax(0,0.9fr)_minmax(0,0.9fr)] border-t border-app-border/65 px-2 py-1 text-[10px] ${
                            rowIndex % 2 === 0 ? "bg-app-bg/30" : "bg-app-surface/45"
                          }`}
                        >
                          <span className="text-app-muted">{row.label}</span>
                          <span className="text-right font-medium text-app-positive">{row.long}</span>
                          <span className="text-right font-medium text-app-negative">{row.short}</span>
                        </div>
                      ))}
                    </div>
                    <div className="space-y-1 rounded-md border border-app-border/70 bg-app-bg/45 p-1.5">
                      <div className="flex items-center justify-between text-[10px] uppercase tracking-[0.12em] text-app-muted">
                        <span>PnL Share Split</span>
                        <span className="text-app-muted">
                          <span className="text-app-positive">
                            {formatMetricValueWithNote(derivedMetrics.direction.longPnlShare, (value) => `Long ${formatPercent(value, 1)}`)}
                          </span>{" "}
                          /{" "}
                          <span className="text-app-negative">
                            {formatMetricValueWithNote(derivedMetrics.direction.shortPnlShare, (value) => `Short ${formatPercent(value, 1)}`)}
                          </span>
                        </span>
                      </div>
                      <div className="relative h-2 overflow-hidden rounded-full border border-app-border/80 bg-app-surface/90">
                        <div className="absolute inset-0 bg-[linear-gradient(90deg,rgb(var(--theme-positive)/0.18)_0%,rgb(var(--theme-surface)/0)_50%,rgb(var(--theme-negative)/0.18)_100%)]" />
                        <div
                          className="relative h-full bg-gradient-to-r from-app-positive/90 to-app-positive/80"
                          style={{ width: `${longPnlShareWidth}%` }}
                          aria-hidden="true"
                        />
                        <div
                          className="absolute right-0 top-0 h-full bg-gradient-to-l from-app-negative/85 to-app-negative/75"
                          style={{ width: `${shortPnlShareWidth}%` }}
                          aria-hidden="true"
                        />
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <SplitBar
                className="rounded-md border border-app-border/70 bg-app-bg/35 p-1.5"
                leftLabel="Long PnL"
                rightLabel="Short PnL"
                leftValue={formatMetricValue(derivedMetrics.direction.longPnl, formatPnl)}
                rightValue={formatMetricValue(derivedMetrics.direction.shortPnl, formatPnl)}
                leftMagnitude={Math.abs(derivedMetrics.direction.longPnl.value ?? 0)}
                rightMagnitude={Math.abs(derivedMetrics.direction.shortPnl.value ?? 0)}
                leftBarClassName="bg-gradient-to-r from-app-positive/95 to-app-positive/80"
                rightBarClassName="bg-gradient-to-l from-app-negative/90 to-app-negative/80"
              />
              <p className="rounded-md border border-app-accent/20 bg-[linear-gradient(120deg,rgb(var(--theme-positive)/0.12),rgb(var(--theme-surface)/0.48)_48%,rgb(var(--theme-negative)/0.1)_100%)] px-2 py-1 text-[10px] text-app-text-soft">
                <span className="font-semibold text-app-accent">Insight:</span> {derivedMetrics.direction.insight}
              </p>
            </MetricCard>

            <MetricCard
              title="Payoff"
              primaryValue={formatMetricValue(derivedMetrics.winLossRatio, (value) => `${formatNumber(value)}x`)}
              primaryClassName={cn("tracking-tight drop-shadow-[0_1px_10px_rgb(var(--theme-bg)/0.45)]", payoffPrimaryClassName)}
              subtitle="Average win versus average loss."
              info="Breakeven win rate = abs(avg loss) / (avg win + abs(avg loss))."
              accentClassName={payoffAccentClassName}
              className={cn("relative isolate md:col-span-2 md:row-start-2 md:col-start-5 lg:col-span-3 lg:col-start-10 lg:row-start-2", payoffCardClassName)}
              contentClassName="relative mt-2.5 flex flex-col gap-2.5 space-y-0"
            >
              <div
                aria-hidden="true"
                className={cn(
                  "pointer-events-none absolute -right-10 -top-10 h-24 w-24 rounded-full blur-3xl",
                  derivedMetrics.winLossRatio.value !== null && derivedMetrics.winLossRatio.value < 1 ? "bg-app-negative/20" : "bg-app-positive/20",
                )}
              />
              <div className="relative rounded-xl border border-app-text/10 bg-app-bg/35 p-2.5 backdrop-blur-sm">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant={payoffSignalVariant}>{payoffSignalLabel}</Badge>
                  <Badge variant="accent">{`W/L ${formatMetricValue(derivedMetrics.winLossRatio, (value) => `${formatNumber(value)}x`)}`}</Badge>
                  <span className="ml-auto text-[10px] uppercase tracking-[0.12em] text-app-muted">
                    {`Capture ${formatMetricValueWithNote(derivedMetrics.payoff.capture, (value) => formatPercent(value * 100, 1))}`}
                  </span>
                </div>
                <SplitBar
                  className="mt-2.5 rounded-md border border-app-border/70 bg-app-bg/45 p-1.5"
                  leftLabel="Avg Win"
                  rightLabel="Avg Loss"
                  leftValue={formatMetricValue(derivedMetrics.payoff.averageWin, formatPnl)}
                  rightValue={formatMetricValue(derivedMetrics.payoff.averageLoss, formatPnl)}
                  leftMagnitude={Math.abs(derivedMetrics.payoff.averageWin.value ?? 0)}
                  rightMagnitude={Math.abs(derivedMetrics.payoff.averageLoss.value ?? 0)}
                  leftBarClassName="bg-gradient-to-r from-app-positive/95 to-app-accent/80"
                  rightBarClassName="bg-gradient-to-l from-app-negative/90 to-app-warning/80"
                />
                <MiniStatList
                  className="mt-2.5 gap-1.5"
                  items={[
                    {
                      label: "Avg Win",
                      value: formatMetricValue(derivedMetrics.payoff.averageWin, formatPnl),
                      valueClassName: metricPnlClass(derivedMetrics.payoff.averageWin),
                    },
                    {
                      label: "Avg Loss",
                      value: formatMetricValue(derivedMetrics.payoff.averageLoss, formatPnl),
                      valueClassName: metricPnlClass(derivedMetrics.payoff.averageLoss),
                    },
                    { label: "Breakeven WR", value: formatMetricValue(derivedMetrics.payoff.breakevenWinRate, (value) => formatPercent(value, 1)) },
                    { label: "Current WR", value: formatMetricValue(derivedMetrics.payoff.currentWinRate, (value) => formatPercent(value, 1)) },
                    { label: "WR Cushion", value: formatMetricValueWithNote(derivedMetrics.payoff.wrCushion, formatPoints) },
                    {
                      label: "Large Loss Rate",
                      value:
                        derivedMetrics.payoff.largeLossRate.value === null
                          ? formatMetricValueWithNote(derivedMetrics.payoff.largeLossRate, (value) => formatPercent(value, 1))
                          : `${formatPercent(derivedMetrics.payoff.largeLossRate.value, 1)} (<= ${formatMetricValue(
                              {
                                value:
                                  derivedMetrics.payoff.largeLossThreshold.value === null
                                    ? null
                                    : -Math.abs(derivedMetrics.payoff.largeLossThreshold.value),
                                missingReason: derivedMetrics.payoff.largeLossThreshold.missingReason,
                              },
                              formatPnl,
                            )})`,
                    },
                    { label: "P95 Loss", value: formatMetricValueWithNote(derivedMetrics.payoff.p95Loss, formatPnl) },
                    {
                      label: "Capture",
                      value: formatMetricValueWithNote(derivedMetrics.payoff.capture, (value) => formatPercent(value * 100, 1)),
                    },
                  ]}
                />
              </div>
              <div className="space-y-1 rounded-xl border border-app-border/70 bg-app-bg/35 p-2">
                <p className="text-[10px] uppercase tracking-[0.12em] text-app-muted-strong">Points Payoff By Basis</p>
                <div className="overflow-hidden rounded-md border border-app-border/70 bg-app-bg/25">
                  <div className="grid grid-cols-[minmax(0,0.7fr)_minmax(0,1fr)_minmax(0,1fr)] px-2 py-1 text-[9px] uppercase tracking-[0.12em] text-app-muted-strong">
                    <span>Basis</span>
                    <span className="text-right">Avg Point Gain</span>
                    <span className="text-right">Avg Point Loss</span>
                  </div>
                  {DISPLAY_PAYOFF_POINTS_BASES.map((basis) => (
                    <div
                      key={basis}
                      className="grid grid-cols-[minmax(0,0.7fr)_minmax(0,1fr)_minmax(0,1fr)] border-t border-app-border/65 px-2 py-1 text-[10px]"
                    >
                      <span className="font-semibold text-app-text-soft">{basis}</span>
                      <span className="text-right text-app-positive">{formatPointMetric(pointPayoffByBasis[basis].avgPointGain, basis)}</span>
                      <span className="text-right text-app-negative">{formatPointMetric(pointPayoffByBasis[basis].avgPointLoss, basis)}</span>
                    </div>
                  ))}
                </div>
              </div>
            </MetricCard>

            <MetricCard
              title="Activity"
              primaryValue={formatInteger(displayTradeCount)}
              primaryClassName={cn("tracking-tight drop-shadow-[0_1px_10px_rgb(var(--theme-bg)/0.45)]", activityPrimaryClassName)}
              subtitle="Closed trades in this range."
              info="Activity normalizes execution count by active trading days."
              accentClassName={activityAccentClassName}
              className={cn(
                "relative isolate md:col-span-2 md:row-start-3 md:col-start-1 lg:col-span-3 lg:col-start-1 lg:row-start-3 lg:p-3",
                activityCardClassName,
              )}
              contentClassName="relative mt-2.5 flex flex-col gap-2.5 space-y-0"
            >
              <div
                aria-hidden="true"
                className={cn(
                  "pointer-events-none absolute -right-10 -top-8 h-24 w-24 rounded-full blur-3xl",
                  activityWeeklyPace !== null && activityWeeklyPace >= 30 ? "bg-app-warning/20" : "bg-app-accent/20",
                )}
              />
              <div className="relative rounded-xl border border-app-text/10 bg-app-bg/35 p-2.5 backdrop-blur-sm">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant={activitySignalVariant}>{activitySignalLabel}</Badge>
                  <Badge variant="accent">{`${formatInteger(displayActiveDays)} Active Days`}</Badge>
                  <span className="ml-auto text-[10px] uppercase tracking-[0.12em] text-app-muted">
                    {`Avg ${formatNumber(displayAvgTradesPerDay, 1)} / day`}
                  </span>
                </div>
                <div className="mt-2.5 space-y-1.5">
                  <div className="flex items-center justify-between text-[10px] uppercase tracking-[0.12em] text-app-muted">
                    <span>{activeSelectedTradeDate ? "Selected Day Count" : "Weekly Pacing"}</span>
                    <span className="font-semibold text-app-text-soft">
                      {activeSelectedTradeDate ? formatInteger(displayTradeCount) : activityWeeklyPace === null ? "N/A" : formatNumber(activityWeeklyPace, 1)}
                    </span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full border border-app-border/80 bg-app-surface/85">
                    <div
                      aria-hidden="true"
                      className={cn(
                        "h-full transition-all duration-500",
                        activityMetrics.tradesPerWeek !== null && activityMetrics.tradesPerWeek >= 30
                          ? "bg-gradient-to-r from-app-warning/90 to-app-warning/80"
                          : "bg-gradient-to-r from-app-accent/90 to-app-positive/80",
                      )}
                      style={{ width: `${activityPacePercent}%` }}
                    />
                  </div>
                </div>
              </div>
              <MiniStatList
                className="gap-1.5"
                items={[
                  {
                    label: "Median/day",
                    value: activityMetrics.medianTradesPerDay === null ? "N/A" : formatNumber(activityMetrics.medianTradesPerDay, 1),
                  },
                  {
                    label: "Max/day",
                    value: activityMetrics.maxTradesInDay === null ? "N/A" : formatInteger(activityMetrics.maxTradesInDay),
                  },
                  {
                    label: "Trades/week",
                    value: activityWeeklyPace === null ? "N/A" : formatNumber(activityWeeklyPace, 1),
                  },
                  {
                    label: "Days/week",
                    value: activeSelectedTradeDate || activityMetrics.activeDaysPerWeek === null ? "N/A" : formatNumber(activityMetrics.activeDaysPerWeek, 1),
                  },
                  {
                    label: "Trades/active hr",
                    value: activityMetrics.tradesPerActiveHour === null ? "N/A" : formatNumber(activityMetrics.tradesPerActiveHour, 2),
                  },
                ]}
              />
            </MetricCard>

            <MetricCard
              title="Sustainability"
              primaryValue={`${formatInteger(sustainability.score)}/100`}
              primaryClassName={cn("tracking-tight drop-shadow-[0_1px_10px_rgb(var(--theme-bg)/0.45)]", sustainabilityPrimaryClassName)}
              subtitle="Composite score from Risk, Consistency, and Edge."
              info="Sustainability blends drawdown control, day-to-day consistency, and profit factor with a confidence adjustment for small samples."
              accentClassName={sustainabilityAccentClassName}
              className={cn(
                "relative isolate self-start p-3 sm:col-span-2 md:col-span-2 md:row-start-3 md:col-start-3 lg:col-span-6 lg:col-start-4 lg:row-start-3",
                sustainabilityCardClassName,
              )}
              contentClassName="relative mt-2.5 flex flex-col gap-2.5 space-y-0"
            >
              <div
                aria-hidden="true"
                className={cn(
                  "pointer-events-none absolute -right-14 -top-10 h-28 w-28 rounded-full blur-3xl",
                  sustainability.score >= 70 ? "bg-app-positive/20" : sustainability.score >= 45 ? "bg-app-accent/20" : "bg-app-negative/20",
                )}
              />
              <div className="relative rounded-xl border border-app-text/10 bg-app-bg/35 p-2.5 backdrop-blur-sm">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-[11px] uppercase tracking-[0.14em] text-app-muted-strong">Score 0-100</p>
                  <Badge variant={sustainabilityBadgeVariant(sustainability.label)}>{sustainability.label}</Badge>
                </div>

                <div className="mt-2.5 grid gap-1.5 sm:grid-cols-3">
                  {[
                    {
                      label: "Risk",
                      value: sustainability.riskScore,
                      fillClassName: "bg-gradient-to-r from-app-negative/90 to-app-warning/75",
                    },
                    {
                      label: "Consistency",
                      value: sustainability.consistencyScore,
                      fillClassName: "bg-gradient-to-r from-app-accent/90 to-app-secondary/80",
                    },
                    {
                      label: "Edge",
                      value: sustainability.edgeScore,
                      fillClassName: "bg-gradient-to-r from-app-positive/90 to-app-accent/80",
                    },
                  ].map((item) => (
                    <div key={item.label} className="space-y-1 rounded-md border border-app-border/70 bg-app-bg/45 px-2 py-1.5">
                      <div className="flex items-center justify-between text-[10px]">
                        <span className="text-app-muted">{item.label}</span>
                        <span className="font-semibold text-app-text">{formatNumber(item.value, 1)}/100</span>
                      </div>
                      <div className="h-1.5 overflow-hidden rounded-full border border-app-border/80 bg-app-surface/85">
                        <div className={cn("h-full", item.fillClassName)} style={{ width: `${Math.max(0, Math.min(100, item.value))}%` }} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="space-y-1 rounded-xl border border-app-border/70 bg-app-bg/35 p-2.5">
                <div className="flex items-center justify-between text-[10px] uppercase tracking-[0.12em] text-app-muted-strong">
                  <span>Score Gauge</span>
                  <span className="font-semibold text-app-muted">{formatInteger(sustainability.score)}/100</span>
                </div>
                <div className="relative pt-3.5">
                  <div className="h-2 overflow-hidden rounded-full border border-app-border/80 bg-app-surface/85">
                    <div
                      aria-hidden="true"
                      className={`h-full transition-all duration-500 ${sustainabilityFillClass(sustainability.score)}`}
                      style={{ width: `${sustainability.score}%` }}
                    />
                  </div>
                  {[40, 60, 80].map((tick) => (
                    <div key={tick} className="pointer-events-none absolute top-0 -translate-x-1/2" style={{ left: `${tick}%` }}>
                      <span className="block text-[10px] text-app-muted-strong">{tick}</span>
                      <span className="mx-auto mt-0.5 block h-2 w-px bg-app-muted-strong/70" />
                    </div>
                  ))}
                </div>
              </div>
            </MetricCard>

            <MetricCard
              title="Hold Time"
              primaryValue={formatMetricValue(derivedMetrics.winDurationOverLossDuration, (value) => `${formatNumber(value)}x`)}
              primaryClassName={cn("tracking-tight drop-shadow-[0_1px_10px_rgb(var(--theme-bg)/0.45)]", holdTimePrimaryClassName)}
              subtitle="Win duration divided by loss duration."
              info="Win Duration / Loss Duration = avg win hold minutes / avg loss hold minutes."
              accentClassName={holdTimeAccentClassName}
              className={cn("relative isolate md:col-span-2 md:row-start-3 md:col-start-5 lg:col-span-3 lg:col-start-10 lg:row-start-3", holdTimeCardClassName)}
              contentClassName="relative mt-2.5 flex flex-col gap-2.5 space-y-0"
            >
              <div
                aria-hidden="true"
                className={cn(
                  "pointer-events-none absolute -right-10 -top-8 h-24 w-24 rounded-full blur-3xl",
                  derivedMetrics.winDurationOverLossDuration.value !== null && derivedMetrics.winDurationOverLossDuration.value < 1
                    ? "bg-app-warning/20"
                    : "bg-app-accent/20",
                )}
              />
              <div className="relative rounded-xl border border-app-text/10 bg-app-bg/35 p-2.5 backdrop-blur-sm">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant={holdTimeSignalVariant}>{holdTimeSignalLabel}</Badge>
                  <span className="ml-auto text-[10px] uppercase tracking-[0.12em] text-app-muted">
                    {`Ratio ${formatMetricValue(derivedMetrics.winDurationOverLossDuration, (value) => `${formatNumber(value)}x`)}`}
                  </span>
                </div>
                <SplitBar
                  className="mt-2.5 rounded-md border border-app-border/70 bg-app-bg/45 p-1.5"
                  leftLabel="Avg Win Duration"
                  rightLabel="Avg Loss Duration"
                  leftValue={formatDurationCompact(summary.avg_win_duration_minutes)}
                  rightValue={formatDurationCompact(summary.avg_loss_duration_minutes)}
                  leftMagnitude={summary.avg_win_duration_minutes}
                  rightMagnitude={summary.avg_loss_duration_minutes}
                  leftBarClassName="bg-gradient-to-r from-app-accent/90 to-app-positive/80"
                  rightBarClassName="bg-gradient-to-l from-app-warning/90 to-app-warning/80"
                />
              </div>
              <MiniStatList
                className="gap-1.5"
                columns={1}
                items={[
                  { label: "Avg Win Duration", value: formatMinutes(summary.avg_win_duration_minutes) },
                  { label: "Avg Loss Duration", value: formatMinutes(summary.avg_loss_duration_minutes) },
                ]}
              />
            </MetricCard>
          </>
        )}
      </MasonryGrid>

      <ViewportDeferredDashboardCard
        title="Account Balance"
        description="Loading the daily balance view."
        bodyHeightClassName="h-[360px]"
      >
        <DailyAccountBalanceCard
          days={dashboardPnlCalendarDays}
          loading={pnlCalendarLoading}
          error={pnlCalendarError}
          currentBalance={dashboardCurrentBalance}
        />
      </ViewportDeferredDashboardCard>

      <ViewportDeferredDashboardCard
        title="PnL Calendar"
        description="Loading calendar performance and journal markers."
        bodyHeightClassName="h-[520px]"
      >
        <PnlCalendarCard
          days={dashboardPnlCalendarDays}
          loading={pnlCalendarLoading}
          error={pnlCalendarError}
          scopeKey={standardCalendarScopeKey}
          journalDays={journalDays}
          journalDaysLoading={journalDaysLoading}
          selectedDate={activeSelectedTradeDate}
          onDaySelect={handleSelectedTradeDateChange}
          onJournalDayOpen={openJournalForDate}
          onAddJournalForSelectedDay={openJournalForDate}
          onVisibleRangeChange={handleStandardCalendarVisibleRangeChange}
        />
      </ViewportDeferredDashboardCard>

      <RecentTradesCard
        trades={recentTrades}
        loading={recentTradesLoading}
        error={recentTradesError}
        selectedTradeDate={activeSelectedTradeDate}
        selectedTradeDateLabel={selectedTradeDateLabel}
        visibleCount={recentTradesVisibleCount}
        recentTradeLimit={TRADE_LIMIT}
        dayFilterTradeLimit={DAY_FILTER_TRADE_LIMIT}
        onClearDayFilter={clearSelectedTradeDate}
        onShowMore={showMoreRecentTrades}
      />
        </>
      )}
    </div>
  );
}
