import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import type { CopyFullStatsMetrics } from "../../components/dashboard/CopyFullStatsButton";
import { Chip } from "../../components/metrics/Chip";
import { DonutRing } from "../../components/metrics/DonutRing";
import { GaugeBar } from "../../components/metrics/GaugeBar";
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
import {
  ACCOUNT_QUERY_PARAM,
  parseAccountId,
  readStoredAccountId,
  readStoredMainAccountId,
  writeStoredAccountId,
} from "../../lib/accountSelection";
import { accountsApi } from "../../lib/api";
import { sortAccountsForSelection } from "../../lib/accountOrdering";
import { getTradingDayBoundaryIso, getTradingDayRange, tradingDayKey } from "../../lib/tradingDay";
import { formatTradeDirection, tradeDirectionBadgeVariant } from "../../lib/tradeDirection";
import { getDisplayTradeSymbol } from "../../lib/tradeSymbol";
import { ACCOUNT_TRADES_SYNCED_EVENT, type AccountTradesSyncedDetail } from "../../lib/tradeSyncEvents";
import type { AccountInfo, AccountPnlCalendarDay, AccountSummary, AccountTrade } from "../../lib/types";
import { logPerfInfo } from "../../lib/perf";
import { formatCurrency, formatInteger, formatMinutes, formatNumber, formatPercent, formatPnl } from "../../utils/formatters";
import { computeActivityMetrics } from "../../utils/activityMetrics";
import {
  computeDirectionPercentages,
  computeDrawdownPercentOfEquityBase,
  computeDrawdownPercentOfNetPnl,
  computeStabilityScoreFromWorstDayPercent,
} from "../../utils/metrics";
import { computeSustainability, type SustainabilityLabel } from "../../utils/sustainability";
import { computeDashboardDerivedMetrics } from "./metrics/calculations";
import type { MetricValue } from "./metrics/types";

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
type PointsBasis = "auto" | "MNQ" | "MES" | "MGC" | "SIL";
type ConcretePointsBasis = Exclude<PointsBasis, "auto">;
const PAYOFF_POINTS_BASES: ConcretePointsBasis[] = ["MNQ", "MES", "MGC", "SIL"];
const DISPLAY_PAYOFF_POINTS_BASES: ConcretePointsBasis[] = ["MNQ", "MES"];

const METRICS_RANGE_OPTIONS: Array<{ key: MetricsRangePreset; label: string }> = [
  { key: "1D", label: "1D" },
  { key: "1W", label: "1W" },
  { key: "1M", label: "1M" },
  { key: "6M", label: "6M" },
  { key: "ALL", label: "All" },
];

const timestampFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: true,
  timeZone: "America/New_York",
});

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
  avgPointGain: null,
  avgPointLoss: null,
  pointsBasisUsed: "auto",
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
    MGC: { avgPointGain: null, avgPointLoss: null },
    SIL: { avgPointGain: null, avgPointLoss: null },
  };
}

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

function formatTradeDuration(minutes: number | null | undefined) {
  if (minutes === null || minutes === undefined || !Number.isFinite(minutes)) {
    return "-";
  }
  return formatDurationCompact(minutes);
}

function pnlClass(value: number) {
  return value >= 0 ? "text-emerald-300" : "text-rose-300";
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
    return "bg-emerald-300/75";
  }
  if (score >= 60) {
    return "bg-cyan-300/75";
  }
  if (score >= 40) {
    return "bg-amber-300/80";
  }
  return "bg-rose-300/80";
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
    <Card>
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

function buildMetricsRangeQuery(
  range: MetricsRangePreset,
  customRange: CustomDateRange | null,
): { start?: string; end?: string; allTime: boolean } {
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
        const currentTradingDay = tradingDayKey(end);
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

export function DashboardPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const accountFromQuery = parseAccountId(searchParams.get(ACCOUNT_QUERY_PARAM));

  const [accounts, setAccounts] = useState<AccountInfo[]>([]);

  const [summary, setSummary] = useState<AccountSummary>(emptySummary);
  const [pointPayoffByBasis, setPointPayoffByBasis] = useState<PointPayoffByBasis>(() => createEmptyPointPayoffByBasis());
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [metricsRange, setMetricsRange] = useState<MetricsRangePreset>("ALL");
  const [customStartDate, setCustomStartDate] = useState("");
  const [customEndDate, setCustomEndDate] = useState("");
  const [customRange, setCustomRange] = useState<CustomDateRange | null>(null);

  const [selectedTradeDate, setSelectedTradeDate] = useState<string | null>(null);

  const [trades, setTrades] = useState<AccountTrade[]>([]);
  const [tradesLoading, setTradesLoading] = useState(false);
  const [tradesError, setTradesError] = useState<string | null>(null);
  const [metricsTrades, setMetricsTrades] = useState<AccountTrade[]>([]);
  const [metricsTradesLoading, setMetricsTradesLoading] = useState(false);
  const [metricsTradesError, setMetricsTradesError] = useState<string | null>(null);

  const [pnlCalendarDays, setPnlCalendarDays] = useState<AccountPnlCalendarDay[]>([]);
  const [pnlCalendarLoading, setPnlCalendarLoading] = useState(false);
  const [pnlCalendarError, setPnlCalendarError] = useState<string | null>(null);
  const [journalDays, setJournalDays] = useState<Set<string>>(new Set());
  const [journalDaysLoading, setJournalDaysLoading] = useState(false);
  const [calendarVisibleRange, setCalendarVisibleRange] = useState<{ startDate: string; endDate: string } | null>(null);

  const setActiveAccount = useCallback(
    (accountId: number) => {
      const next = new URLSearchParams(searchParams);
      next.set(ACCOUNT_QUERY_PARAM, String(accountId));
      setSearchParams(next, { replace: true });
      writeStoredAccountId(accountId);
      setSelectedTradeDate(null);
    },
    [searchParams, setSearchParams],
  );

  const loadAccounts = useCallback(async () => {
    try {
      const payload = await accountsApi.getSelectableAccounts();
      setAccounts(payload);
    } catch {
      setAccounts([]);
    }
  }, []);

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

  const orderedAccounts = useMemo(() => sortAccountsForSelection(accounts), [accounts]);

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
  const metricsRangeQuery = useMemo(() => buildMetricsRangeQuery(metricsRange, customRange), [customRange, metricsRange]);
  const canReuseMetricsTradesForRecentTrades = selectedTradeDate === null && metricsRangeQuery.allTime;
  const customRangeInvalid = customStartDate !== "" && customEndDate !== "" && customStartDate > customEndDate;
  const dashboardLoadPerfRef = useRef<{
    accountId: number;
    startedAtMs: number;
    startedAtIso: string;
  } | null>(null);
  const dashboardWasLoadingRef = useRef(false);

  useEffect(() => {
    dashboardLoadPerfRef.current = null;
    dashboardWasLoadingRef.current = false;
  }, [selectedAccountId, metricsRangeQuery.end, metricsRangeQuery.start, selectedTradeDate]);

  const selectedTradeDateLabel = useMemo(() => {
    if (!selectedTradeDate) {
      return null;
    }
    return dateFormatter.format(parseIsoDay(selectedTradeDate));
  }, [selectedTradeDate]);

  const loadSummaryAndCalendar = useCallback(async () => {
    if (!selectedAccountId) {
      setSummary(emptySummary);
      setPointPayoffByBasis(createEmptyPointPayoffByBasis());
      setSummaryError(null);
      setPnlCalendarDays([]);
      setPnlCalendarError(null);
      return;
    }

    setSummaryLoading(true);
    setPnlCalendarLoading(true);
    setSummaryError(null);
    setPnlCalendarError(null);

    try {
      const summaryQuery = {
        start: metricsRangeQuery.start,
        end: metricsRangeQuery.end,
      };

      const [nextSummaryBundle, nextPnlCalendar] = await Promise.all([
        accountsApi.getSummaryWithPointBases(selectedAccountId, {
          start: summaryQuery.start,
          end: summaryQuery.end,
        }),
        accountsApi.getPnlCalendar(selectedAccountId, {
          start: metricsRangeQuery.start,
          end: metricsRangeQuery.end,
          all_time: metricsRangeQuery.allTime,
        }),
      ]);

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
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load dashboard data";
      setSummaryError(message);
      setPnlCalendarError(message);
      setSummary(emptySummary);
      setPointPayoffByBasis(createEmptyPointPayoffByBasis());
      setPnlCalendarDays([]);
    } finally {
      setSummaryLoading(false);
      setPnlCalendarLoading(false);
    }
  }, [metricsRangeQuery, selectedAccountId]);

  const loadTrades = useCallback(async () => {
    if (!selectedAccountId) {
      setTrades([]);
      setTradesError(null);
      return;
    }

    if (canReuseMetricsTradesForRecentTrades) {
      setTrades([]);
      setTradesError(null);
      setTradesLoading(false);
      return;
    }

    setTradesLoading(true);
    setTradesError(null);

    try {
      const selectedRange = selectedTradeDate ? getTradingDayRange(selectedTradeDate) : null;
      const query = selectedRange ? { limit: DAY_FILTER_TRADE_LIMIT, ...selectedRange } : { limit: TRADE_LIMIT };
      const nextTrades = await accountsApi.getTrades(selectedAccountId, query);
      setTrades(nextTrades);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load trade events";
      setTradesError(message);
      setTrades([]);
    } finally {
      setTradesLoading(false);
    }
  }, [canReuseMetricsTradesForRecentTrades, selectedAccountId, selectedTradeDate]);

  const loadJournalDays = useCallback(async () => {
    if (!selectedAccountId || !calendarVisibleRange) {
      setJournalDays(new Set());
      return;
    }

    setJournalDaysLoading(true);
    try {
      const payload = await accountsApi.getJournalDays(selectedAccountId, {
        start_date: calendarVisibleRange.startDate,
        end_date: calendarVisibleRange.endDate,
      });
      setJournalDays(new Set(payload.days));
    } catch {
      setJournalDays(new Set());
    } finally {
      setJournalDaysLoading(false);
    }
  }, [calendarVisibleRange, selectedAccountId]);

  const loadMetricsTrades = useCallback(async () => {
    if (!selectedAccountId) {
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
        start: metricsRangeQuery.start,
        end: metricsRangeQuery.end,
      });
      setMetricsTrades(nextTrades);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load directional trade history";
      setMetricsTradesError(message);
      setMetricsTrades([]);
    } finally {
      setMetricsTradesLoading(false);
    }
  }, [metricsRangeQuery.end, metricsRangeQuery.start, selectedAccountId]);

  const reloadDashboard = useCallback(async () => {
    await Promise.all([loadSummaryAndCalendar(), loadTrades(), loadMetricsTrades()]);
  }, [loadMetricsTrades, loadSummaryAndCalendar, loadTrades]);

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
    const isDashboardLoading = summaryLoading || pnlCalendarLoading || tradesLoading || metricsTradesLoading || journalDaysLoading;
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
      trades_count: trades.length,
      metrics_trades_count: metricsTrades.length,
      pnl_calendar_day_count: pnlCalendarDays.length,
      journal_day_count: journalDays.size,
    });
    dashboardLoadPerfRef.current = null;
  }, [
    journalDays.size,
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
    setSelectedTradeDate(null);
  }, [metricsRange]);

  useEffect(() => {
    function handleAccountTradesSynced(event: Event) {
      const detail = (event as CustomEvent<AccountTradesSyncedDetail>).detail;
      if (!selectedAccountId || detail.accountId !== selectedAccountId || detail.error) {
        return;
      }
      void reloadDashboard();
    }

    window.addEventListener(ACCOUNT_TRADES_SYNCED_EVENT, handleAccountTradesSynced as EventListener);
    return () => {
      window.removeEventListener(ACCOUNT_TRADES_SYNCED_EVENT, handleAccountTradesSynced as EventListener);
    };
  }, [reloadDashboard, selectedAccountId]);

  const directionDataIssue = metricsTradesLoading
    ? "Loading directional trade history."
    : metricsTradesError
      ? "Needs directional trade history for this range."
      : null;

  const hasCompleteDirectionalHistory =
    !metricsTradesLoading && !metricsTradesError && summary.trade_count <= metricsTrades.length;

  const derivedMetrics = useMemo(
    () =>
      computeDashboardDerivedMetrics({
        summary,
        trades: metricsTrades,
        dailyPnlDays: pnlCalendarDays,
        hasCompleteDirectionalHistory,
        directionDataIssue,
      }),
    [directionDataIssue, hasCompleteDirectionalHistory, metricsTrades, pnlCalendarDays, summary],
  );

  const netPnlMetric = useMemo<MetricValue>(() => ({ value: summary.net_pnl }), [summary.net_pnl]);
  const profitPerDayMetric = useMemo<MetricValue>(() => ({ value: summary.profit_per_day }), [summary.profit_per_day]);
  const efficiencyPerHourMetric = useMemo<MetricValue>(() => ({ value: summary.efficiency_per_hour }), [summary.efficiency_per_hour]);
  const expectancyPerTradeMetric = useMemo<MetricValue>(() => ({ value: summary.expectancy_per_trade }), [summary.expectancy_per_trade]);
  const maxDrawdownMetric = useMemo<MetricValue>(() => ({ value: summary.max_drawdown }), [summary.max_drawdown]);
  const performanceSignalVariant = summary.net_pnl > 0 ? "positive" : summary.net_pnl < 0 ? "negative" : "neutral";
  const performanceSignalLabel = summary.net_pnl > 0 ? "Positive Flow" : summary.net_pnl < 0 ? "Negative Drift" : "Flat Session";
  const performanceAccentClassName =
    summary.net_pnl > 0
      ? "bg-gradient-to-r from-emerald-300/80 via-cyan-200/30 to-transparent"
      : summary.net_pnl < 0
        ? "bg-gradient-to-r from-rose-300/80 via-orange-200/35 to-transparent"
        : "bg-gradient-to-r from-cyan-300/70 via-sky-200/25 to-transparent";
  const performancePrimaryClassName =
    summary.net_pnl > 0
      ? "bg-gradient-to-r from-emerald-100 via-cyan-100 to-emerald-200 bg-clip-text text-transparent"
      : summary.net_pnl < 0
        ? "bg-gradient-to-r from-rose-100 via-orange-100 to-rose-200 bg-clip-text text-transparent"
        : "text-cyan-100";
  const performanceCardClassName =
    summary.net_pnl > 0
      ? "border-emerald-400/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgba(16,185,129,0.24),rgba(15,23,42,0.58)_44%,rgba(15,23,42,0.9)_100%)]"
      : summary.net_pnl < 0
        ? "border-rose-400/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgba(244,63,94,0.22),rgba(15,23,42,0.58)_44%,rgba(15,23,42,0.9)_100%)]"
        : "border-cyan-400/25 bg-[radial-gradient(150%_120%_at_0%_0%,rgba(56,189,248,0.2),rgba(15,23,42,0.58)_44%,rgba(15,23,42,0.9)_100%)]";
  const performanceGlowClassName =
    summary.net_pnl > 0 ? "bg-emerald-300/24" : summary.net_pnl < 0 ? "bg-rose-300/24" : "bg-cyan-300/22";
  const edgeSignalVariant = summary.expectancy_per_trade > 0 ? "positive" : summary.expectancy_per_trade < 0 ? "negative" : "neutral";
  const edgeSignalLabel = summary.expectancy_per_trade > 0 ? "Positive Expectancy" : summary.expectancy_per_trade < 0 ? "Negative Expectancy" : "Flat Expectancy";
  const edgeAccentClassName =
    summary.expectancy_per_trade > 0
      ? "bg-gradient-to-r from-cyan-300/80 via-emerald-200/25 to-transparent"
      : summary.expectancy_per_trade < 0
        ? "bg-gradient-to-r from-rose-300/80 via-orange-200/28 to-transparent"
        : "bg-gradient-to-r from-sky-300/75 via-cyan-200/20 to-transparent";
  const edgePrimaryClassName =
    summary.expectancy_per_trade > 0
      ? "bg-gradient-to-r from-cyan-100 via-emerald-100 to-cyan-200 bg-clip-text text-transparent"
      : summary.expectancy_per_trade < 0
        ? "bg-gradient-to-r from-rose-100 via-orange-100 to-rose-200 bg-clip-text text-transparent"
        : "text-cyan-100";
  const edgeCardClassName =
    summary.expectancy_per_trade > 0
      ? "border-cyan-400/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgba(56,189,248,0.22),rgba(15,23,42,0.58)_45%,rgba(15,23,42,0.9)_100%)]"
      : summary.expectancy_per_trade < 0
        ? "border-rose-400/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgba(244,63,94,0.2),rgba(15,23,42,0.58)_45%,rgba(15,23,42,0.9)_100%)]"
        : "border-sky-400/25 bg-[radial-gradient(150%_120%_at_0%_0%,rgba(56,189,248,0.18),rgba(15,23,42,0.58)_45%,rgba(15,23,42,0.9)_100%)]";
  const edgeGlowClassName =
    summary.expectancy_per_trade > 0 ? "bg-cyan-300/20" : summary.expectancy_per_trade < 0 ? "bg-rose-300/20" : "bg-sky-300/20";
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
      ? "bg-gradient-to-r from-emerald-300/70 via-cyan-200/18 to-transparent"
      : "bg-gradient-to-r from-rose-300/75 via-orange-200/20 to-transparent";
  const payoffPrimaryClassName =
    derivedMetrics.winLossRatio.value === null || derivedMetrics.winLossRatio.value >= 1
      ? "bg-gradient-to-r from-emerald-100 via-cyan-100 to-emerald-200 bg-clip-text text-transparent"
      : "bg-gradient-to-r from-rose-100 via-orange-100 to-rose-200 bg-clip-text text-transparent";
  const payoffCardClassName =
    derivedMetrics.winLossRatio.value === null || derivedMetrics.winLossRatio.value >= 1
      ? "border-emerald-400/28 bg-[radial-gradient(150%_120%_at_0%_0%,rgba(16,185,129,0.18),rgba(15,23,42,0.58)_46%,rgba(15,23,42,0.9)_100%)]"
      : "border-rose-400/28 bg-[radial-gradient(150%_120%_at_0%_0%,rgba(244,63,94,0.2),rgba(15,23,42,0.58)_46%,rgba(15,23,42,0.9)_100%)]";
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
      ? "bg-gradient-to-r from-cyan-300/70 via-amber-200/25 to-transparent"
      : "bg-gradient-to-r from-amber-300/70 via-rose-200/22 to-transparent";
  const holdTimePrimaryClassName =
    derivedMetrics.winDurationOverLossDuration.value === null || derivedMetrics.winDurationOverLossDuration.value >= 1
      ? "bg-gradient-to-r from-cyan-100 via-amber-100 to-cyan-200 bg-clip-text text-transparent"
      : "bg-gradient-to-r from-amber-100 via-rose-100 to-orange-200 bg-clip-text text-transparent";
  const holdTimeCardClassName =
    derivedMetrics.winDurationOverLossDuration.value === null || derivedMetrics.winDurationOverLossDuration.value >= 1
      ? "border-cyan-400/25 bg-[radial-gradient(150%_120%_at_0%_0%,rgba(56,189,248,0.18),rgba(15,23,42,0.58)_46%,rgba(15,23,42,0.9)_100%)]"
      : "border-amber-400/28 bg-[radial-gradient(150%_120%_at_0%_0%,rgba(251,191,36,0.18),rgba(15,23,42,0.58)_46%,rgba(15,23,42,0.9)_100%)]";

  const drawdownPercentOfNet = useMemo(
    () => computeDrawdownPercentOfNetPnl(summary.max_drawdown, summary.net_pnl),
    [summary.max_drawdown, summary.net_pnl],
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
      pnlCalendarDays
        .map((day) => day.net_pnl)
        .filter((value) => Number.isFinite(value)),
    [pnlCalendarDays],
  );
  const sustainability = useMemo(
    () =>
      computeSustainability({
        dailyNetPnl: sustainabilityDailyNetPnl,
        maxDrawdown: summary.max_drawdown,
        equityBase: selectedAccount?.balance ?? null,
      }),
    [
      selectedAccount?.balance,
      sustainabilityDailyNetPnl,
      summary.max_drawdown,
    ],
  );
  const sustainabilityAccentClassName =
    sustainability.score >= 80
      ? "bg-gradient-to-r from-emerald-300/75 via-cyan-200/22 to-transparent"
      : sustainability.score >= 60
        ? "bg-gradient-to-r from-cyan-300/75 via-indigo-200/22 to-transparent"
        : sustainability.score >= 40
          ? "bg-gradient-to-r from-amber-300/75 via-orange-200/25 to-transparent"
          : "bg-gradient-to-r from-rose-300/80 via-orange-200/25 to-transparent";
  const sustainabilityPrimaryClassName =
    sustainability.score >= 80
      ? "bg-gradient-to-r from-emerald-100 via-cyan-100 to-emerald-200 bg-clip-text text-transparent"
      : sustainability.score >= 60
        ? "bg-gradient-to-r from-cyan-100 via-indigo-100 to-cyan-200 bg-clip-text text-transparent"
        : sustainability.score >= 40
          ? "bg-gradient-to-r from-amber-100 via-orange-100 to-amber-200 bg-clip-text text-transparent"
          : "bg-gradient-to-r from-rose-100 via-orange-100 to-rose-200 bg-clip-text text-transparent";
  const sustainabilityCardClassName =
    sustainability.score >= 80
      ? "border-emerald-400/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgba(16,185,129,0.2),rgba(15,23,42,0.58)_46%,rgba(15,23,42,0.9)_100%)]"
      : sustainability.score >= 60
        ? "border-cyan-400/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgba(56,189,248,0.2),rgba(15,23,42,0.58)_46%,rgba(15,23,42,0.9)_100%)]"
        : sustainability.score >= 40
          ? "border-amber-400/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgba(251,191,36,0.18),rgba(15,23,42,0.58)_46%,rgba(15,23,42,0.9)_100%)]"
          : "border-rose-400/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgba(244,63,94,0.2),rgba(15,23,42,0.58)_46%,rgba(15,23,42,0.9)_100%)]";
  const drawdownEquityBase = useMemo(() => {
    const currentBalance = selectedAccount?.balance ?? null;
    if (currentBalance !== null && Number.isFinite(currentBalance) && currentBalance > 0) {
      return {
        value: currentBalance,
        label: "Current balance",
        detail: "Account risk uses the current account balance as the equity base.",
      };
    }

    if (sustainability.debug.peakEquityFallback > 0) {
      return {
        value: sustainability.debug.peakEquityFallback,
        label: "Peak equity fallback",
        detail: "Account balance is unavailable, so risk uses peak equity inferred from daily PnL in this range.",
      };
    }

    return {
      value: null,
      label: "Unavailable",
      detail: "Need account balance or equity history to grade drawdown control.",
    };
  }, [selectedAccount?.balance, sustainability.debug.peakEquityFallback]);
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
      ? "Awaiting Equity Base"
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
      ? "bg-gradient-to-r from-cyan-300/70 via-emerald-200/22 to-transparent"
      : drawdownPercentOfEquityBase.value <= 15
        ? "bg-gradient-to-r from-amber-300/70 via-orange-200/22 to-transparent"
        : "bg-gradient-to-r from-rose-300/75 via-orange-200/25 to-transparent";
  const riskPrimaryClassName =
    drawdownPercentOfEquityBase.value === null || drawdownPercentOfEquityBase.value <= 10
      ? "bg-gradient-to-r from-cyan-100 via-emerald-100 to-cyan-200 bg-clip-text text-transparent"
      : drawdownPercentOfEquityBase.value <= 15
        ? "bg-gradient-to-r from-amber-100 via-orange-100 to-amber-200 bg-clip-text text-transparent"
        : "bg-gradient-to-r from-rose-100 via-orange-100 to-rose-200 bg-clip-text text-transparent";
  const riskCardClassName =
    drawdownPercentOfEquityBase.value === null || drawdownPercentOfEquityBase.value <= 10
      ? "border-cyan-400/25 bg-[radial-gradient(150%_120%_at_0%_0%,rgba(56,189,248,0.16),rgba(15,23,42,0.6)_46%,rgba(15,23,42,0.9)_100%)]"
      : drawdownPercentOfEquityBase.value <= 15
        ? "border-amber-400/28 bg-[radial-gradient(150%_120%_at_0%_0%,rgba(251,191,36,0.18),rgba(15,23,42,0.6)_46%,rgba(15,23,42,0.9)_100%)]"
        : "border-rose-400/30 bg-[radial-gradient(150%_120%_at_0%_0%,rgba(244,63,94,0.2),rgba(15,23,42,0.6)_46%,rgba(15,23,42,0.9)_100%)]";
  const riskPressurePercent =
    drawdownPercentOfEquityBase.value === null
      ? 0
      : Math.min(100, Math.max(0, (drawdownPercentOfEquityBase.value / 25) * 100));

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
        totalTrades: summary.trade_count,
        activeDays: summary.active_days,
        dailyPnlDays: pnlCalendarDays,
        rangeStart: metricsRangeQuery.start,
        rangeEnd: metricsRangeQuery.end,
      }),
    [metricsRangeQuery.end, metricsRangeQuery.start, pnlCalendarDays, summary.active_days, summary.trade_count],
  );
  const activitySignalVariant =
    activityMetrics.tradesPerWeek === null
      ? "neutral"
      : activityMetrics.tradesPerWeek >= 30
        ? "warning"
        : activityMetrics.tradesPerWeek >= 15
          ? "accent"
          : "positive";
  const activitySignalLabel =
    activityMetrics.tradesPerWeek === null
      ? "Awaiting Pace Data"
      : activityMetrics.tradesPerWeek >= 30
        ? "High Tempo"
        : activityMetrics.tradesPerWeek >= 15
          ? "Balanced Tempo"
          : "Selective Tempo";
  const activityAccentClassName =
    activityMetrics.tradesPerWeek === null || activityMetrics.tradesPerWeek < 30
      ? "bg-gradient-to-r from-slate-300/58 via-cyan-200/20 to-transparent"
      : "bg-gradient-to-r from-amber-300/70 via-orange-200/22 to-transparent";
  const activityPrimaryClassName =
    activityMetrics.tradesPerWeek === null || activityMetrics.tradesPerWeek < 30
      ? "bg-gradient-to-r from-cyan-100 via-slate-100 to-cyan-200 bg-clip-text text-transparent"
      : "bg-gradient-to-r from-amber-100 via-orange-100 to-amber-200 bg-clip-text text-transparent";
  const activityCardClassName =
    activityMetrics.tradesPerWeek === null || activityMetrics.tradesPerWeek < 30
      ? "border-slate-600/60 bg-[radial-gradient(150%_120%_at_0%_0%,rgba(148,163,184,0.16),rgba(15,23,42,0.58)_46%,rgba(15,23,42,0.9)_100%)]"
      : "border-amber-400/25 bg-[radial-gradient(150%_120%_at_0%_0%,rgba(251,191,36,0.15),rgba(15,23,42,0.58)_46%,rgba(15,23,42,0.9)_100%)]";
  const activityPacePercent =
    activityMetrics.tradesPerWeek === null ? 0 : Math.min(100, Math.max(0, (activityMetrics.tradesPerWeek / 30) * 100));
  const fullStatsRangeLabel = useMemo(() => {
    if (metricsRangeQuery.start && metricsRangeQuery.end) {
      return formatFullStatsRangeLabel(tradingDayKey(metricsRangeQuery.start), tradingDayKey(metricsRangeQuery.end));
    }

    if (pnlCalendarDays.length > 0) {
      const orderedDays = [...pnlCalendarDays].sort((left, right) => left.date.localeCompare(right.date));
      return formatFullStatsRangeLabel(orderedDays[0].date, orderedDays[orderedDays.length - 1].date);
    }

    return "selected range";
  }, [metricsRangeQuery.end, metricsRangeQuery.start, pnlCalendarDays]);
  const recentTrades = canReuseMetricsTradesForRecentTrades ? metricsTrades.slice(0, TRADE_LIMIT) : trades;
  const recentTradesLoading = canReuseMetricsTradesForRecentTrades ? metricsTradesLoading : tradesLoading;
  const recentTradesError =
    canReuseMetricsTradesForRecentTrades && metricsTradesError !== null ? "Failed to load recent trade events." : tradesError;
  const copyFullStatsMetrics = useMemo<CopyFullStatsMetrics>(
    () => ({
      summary,
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
        currentBalance: selectedAccount?.balance ?? null,
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
      selectedAccount?.balance,
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

      try {
        await accountsApi.createJournalEntry(selectedAccountId, {
          entry_date: date,
          title: "New Entry",
          mood: "Neutral",
          tags: [],
          body: "",
        });
        setJournalDays((current) => {
          const next = new Set(current);
          next.add(date);
          return next;
        });

        const next = new URLSearchParams();
        next.set(ACCOUNT_QUERY_PARAM, String(selectedAccountId));
        next.set("date", date);
        navigate(`/journal?${next.toString()}`);
      } catch (err) {
        setTradesError(err instanceof Error ? err.message : "Failed to open journal entry");
      }
    },
    [navigate, selectedAccountId],
  );

  const handleCalendarVisibleRangeChange = useCallback((startDate: string, endDate: string) => {
    setCalendarVisibleRange((current) => {
      if (current && current.startDate === startDate && current.endDate === endDate) {
        return current;
      }
      return { startDate, endDate };
    });
  }, []);

  return (
    <div className="space-y-5 pb-8">
      <div className="space-y-1.5">
        <div className="max-w-full overflow-x-auto pb-1">
          <div className="inline-flex min-w-max items-center gap-1 rounded-xl border border-slate-800/80 bg-slate-950/45 p-1 shadow-none">
            <div className="flex items-center gap-1">
              <Input
                type="date"
                value={customStartDate}
                max={customEndDate || undefined}
                onChange={(event) => setCustomStartDate(event.target.value)}
                className="h-8 w-[124px] shrink-0 rounded-lg border-slate-700/80 bg-slate-900/60 px-2 text-[11px]"
                aria-label="Custom start date"
              />
              <Input
                type="date"
                value={customEndDate}
                min={customStartDate || undefined}
                onChange={(event) => {
                  const nextEndDate = event.target.value;
                  setCustomEndDate(nextEndDate);
                  if (customStartDate !== "" && nextEndDate !== "" && customStartDate <= nextEndDate) {
                    setCustomRange({ startDate: customStartDate, endDate: nextEndDate });
                    setMetricsRange("CUSTOM");
                    setSelectedTradeDate(null);
                  }
                }}
                className="h-8 w-[124px] shrink-0 rounded-lg border-slate-700/80 bg-slate-900/60 px-2 text-[11px]"
                aria-label="Custom end date"
              />
            </div>
            <div className="flex items-center gap-1">
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
                      "shrink-0 rounded-lg border border-slate-700/80 px-2.5 text-[11px]",
                      active ? "border-cyan-300/40 ring-1 ring-cyan-300/60" : undefined,
                    )}
                  >
                    {option.label}
                  </Button>
                );
              })}
            </div>
            <Suspense fallback={<Skeleton className="h-8 w-32 rounded-lg" />}>
              <CopyFullStatsButton
                metrics={copyFullStatsMetrics}
                rangeLabel={fullStatsRangeLabel}
                calendarDays={pnlCalendarDays}
                disabled={selectedAccountId === null || summaryLoading || pnlCalendarLoading || metricsTradesLoading || summaryError !== null || pnlCalendarError !== null}
                className="h-8 rounded-lg px-2.5 text-[11px]"
              />
            </Suspense>
          </div>
        </div>
        {customRangeInvalid ? <p className="w-full text-xs text-rose-300">End date must be on or after start date.</p> : null}
      </div>

      {selectedAccount?.account_state === "MISSING" ? (
        <Card className="border-amber-400/40 bg-amber-500/10 p-4">
          <p className="text-sm text-amber-100">
            This account is missing from ProjectX. Metrics and trade history are being served from locally stored data.
          </p>
        </Card>
      ) : null}

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
            <p className="text-sm text-rose-300">{summaryError}</p>
          </Card>
        ) : (
          <>
            <MetricCard
              title="Performance"
              primaryValue={formatMetricValue(netPnlMetric, formatPnl)}
              primaryClassName={cn("tracking-tight drop-shadow-[0_1px_12px_rgba(15,23,42,0.5)]", performancePrimaryClassName)}
              subtitle="Net realized PnL after fees."
              info="Realized net profit and loss after fees in the selected range."
              accentClassName={performanceAccentClassName}
              className={cn(
                "isolate sm:col-span-2 md:col-span-2 md:row-start-1 md:col-start-1 lg:col-span-3 lg:row-start-1 lg:col-start-1",
                performanceCardClassName,
              )}
              contentClassName="relative mt-2.5 space-y-2.5"
            >
              <div aria-hidden="true" className={cn("pointer-events-none absolute -right-8 -top-10 h-24 w-24 rounded-full blur-2xl", performanceGlowClassName)} />
              <div className="relative rounded-xl border border-white/10 bg-slate-950/35 p-2.5 backdrop-blur-sm">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant={performanceSignalVariant}>{performanceSignalLabel}</Badge>
                  <Badge variant="accent">{`${formatInteger(summary.trade_count)} Trades`}</Badge>
                  <span className="ml-auto text-[10px] uppercase tracking-[0.12em] text-slate-400">
                    {`Win ${formatPercent(summary.win_rate, 1)}`}
                  </span>
                </div>
                <div className="mt-2.5 grid gap-1.5 sm:grid-cols-2">
                  <div className="rounded-lg border border-slate-700/75 bg-slate-900/55 px-2 py-1.5">
                    <p className="text-[10px] uppercase tracking-[0.12em] text-slate-400">Profit / Day</p>
                    <p className={cn("mt-1 text-sm font-semibold", pnlClass(summary.profit_per_day))}>
                      {formatMetricValue(profitPerDayMetric, formatPnl)}
                    </p>
                  </div>
                  <div className="rounded-lg border border-slate-700/75 bg-slate-900/55 px-2 py-1.5">
                    <p className="text-[10px] uppercase tracking-[0.12em] text-slate-400">Efficiency / Hour</p>
                    <p className={cn("mt-1 text-sm font-semibold", pnlClass(summary.efficiency_per_hour))}>
                      {formatMetricValue(efficiencyPerHourMetric, formatPnl)}
                    </p>
                  </div>
                </div>
                <div className="mt-2.5 flex items-center justify-between rounded-lg border border-slate-700/75 bg-slate-900/55 px-2 py-1 text-[10px] text-slate-300">
                  <span>{`Gross ${formatPnl(summary.gross_pnl)}`}</span>
                  <span>{`Fees ${formatCurrency(summary.fees)}`}</span>
                </div>
              </div>
            </MetricCard>

            <MetricCard
              title="Edge"
              primaryValue={formatMetricValue(expectancyPerTradeMetric, formatPnl)}
              primaryClassName={cn("tracking-tight drop-shadow-[0_1px_12px_rgba(15,23,42,0.5)]", edgePrimaryClassName)}
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
              <div className="relative rounded-xl border border-white/10 bg-slate-950/35 p-2.5 backdrop-blur-sm">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant={edgeSignalVariant}>{edgeSignalLabel}</Badge>
                  <Badge variant="accent">{`PF ${formatNumber(summary.profit_factor)}`}</Badge>
                  <span className="ml-auto text-[10px] uppercase tracking-[0.12em] text-slate-400">{`WR ${formatPercent(summary.win_rate, 1)}`}</span>
                </div>
                <div className="mt-2.5 grid gap-1.5 sm:grid-cols-3">
                  <div className="rounded-lg border border-slate-700/75 bg-slate-900/55 px-2 py-1.5">
                    <p className="text-[10px] uppercase tracking-[0.12em] text-slate-400">Profit Factor</p>
                    <p className="mt-1 text-sm font-semibold text-cyan-100">{formatNumber(summary.profit_factor)}</p>
                  </div>
                  <div className="rounded-lg border border-slate-700/75 bg-slate-900/55 px-2 py-1.5">
                    <p className="text-[10px] uppercase tracking-[0.12em] text-slate-400">Win Rate</p>
                    <p className="mt-1 text-sm font-semibold text-slate-100">{formatPercent(summary.win_rate, 1)}</p>
                  </div>
                  <div className="rounded-lg border border-slate-700/75 bg-slate-900/55 px-2 py-1.5">
                    <p className="text-[10px] uppercase tracking-[0.12em] text-slate-400">W/L Ratio</p>
                    <p className="mt-1 text-sm font-semibold text-slate-100">
                      {formatMetricValue(derivedMetrics.winLossRatio, (value) => `${formatNumber(value)}x`)}
                    </p>
                  </div>
                </div>
                <div className="mt-2.5 space-y-1">
                  <div className="flex items-center justify-between text-[10px] uppercase tracking-[0.12em] text-slate-400">
                    <span>Outcome Mix</span>
                    <span>{`${summary.win_count}W / ${summary.loss_count}L / ${summary.breakeven_count} BE`}</span>
                  </div>
                  <div className="relative h-2 overflow-hidden rounded-full border border-slate-700/80 bg-slate-900/85">
                    <div className="absolute inset-y-0 left-0 bg-gradient-to-r from-emerald-300/95 to-emerald-400/85" style={{ width: `${edgeWinShare}%` }} />
                    <div
                      className="absolute inset-y-0 bg-gradient-to-r from-amber-300/85 to-slate-300/75"
                      style={{ left: `${edgeWinShare}%`, width: `${edgeBreakevenShare}%` }}
                    />
                    <div className="absolute inset-y-0 right-0 bg-gradient-to-l from-rose-300/95 to-rose-400/85" style={{ width: `${edgeLossShare}%` }} />
                  </div>
                </div>
              </div>
            </MetricCard>

            <MetricCard
              title="Swing"
              primaryValue={formatMetricValue(derivedMetrics.stability.dailyPnlVolatility, formatCurrency)}
              primaryClassName="bg-gradient-to-r from-violet-200 via-cyan-100 to-indigo-200 bg-clip-text text-transparent"
              subtitle="Daily PnL volatility ($)."
              info="Stability uses worst-day % of net PnL; lower worst-day concentration implies higher stability."
              accentClassName="bg-gradient-to-r from-violet-300/80 via-cyan-200/30 to-indigo-300/80"
              className="relative overflow-hidden p-3 sm:col-span-2 md:col-span-2 md:row-start-2 md:col-start-1 lg:col-span-3 lg:col-start-1 lg:row-start-2"
              contentClassName="relative mt-2.5 flex flex-col gap-2.5 space-y-0"
            >
              <div className="relative overflow-hidden rounded-xl border border-cyan-500/20 bg-[radial-gradient(120%_130%_at_8%_0%,rgba(129,140,248,0.16),rgba(15,23,42,0.28)_42%,rgba(15,23,42,0.78)_100%)] p-2.5">
                <div aria-hidden="true" className="pointer-events-none absolute -left-8 top-0 h-20 w-20 rounded-full bg-violet-300/20 blur-2xl" />
                <div aria-hidden="true" className="pointer-events-none absolute -right-6 bottom-1 h-24 w-24 rounded-full bg-cyan-300/14 blur-2xl" />
                <div className="relative space-y-2.5">
                  <SplitBar
                    className="rounded-md border border-slate-700/70 bg-slate-950/35 p-2"
                    leftLabel="Best Day"
                    rightLabel="Worst Day"
                    leftValue={formatMetricValue(derivedMetrics.stability.bestDay, formatPnl)}
                    rightValue={formatMetricValue(derivedMetrics.stability.worstDay, formatPnl)}
                    leftMagnitude={Math.abs(derivedMetrics.stability.bestDay.value ?? 0)}
                    rightMagnitude={Math.abs(derivedMetrics.stability.worstDay.value ?? 0)}
                    leftBarClassName="bg-gradient-to-r from-emerald-300/95 to-cyan-300/80"
                    rightBarClassName="bg-gradient-to-l from-rose-300/90 to-amber-300/75"
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
                      ? "border-rose-300/40 bg-rose-500/15 text-rose-100"
                      : "border-cyan-300/40 bg-cyan-500/15 text-cyan-100"
                  }
                />
                <Chip
                  label="G/R Size Ratio"
                  value={formatMetricValueWithNote(derivedMetrics.stability.greenRedDaySizeRatio, (value) => `${formatNumber(value)}x`)}
                  className="border-violet-400/30 bg-violet-500/10 text-violet-100"
                />
              </div>
              <GaugeBar
                label="Stability"
                value={stabilityScore.value}
                valueLabel={formatMetricValue(stabilityScore, (value) => `${formatNumber(value, 0)}%`)}
                className="space-y-1.5 rounded-md border border-slate-700/70 bg-slate-950/35 p-2"
                fillClassName="bg-gradient-to-r from-cyan-300/85 via-indigo-300/80 to-violet-300/80"
              />
              <p className="rounded-md border border-indigo-400/30 bg-[linear-gradient(120deg,rgba(129,140,248,0.15),rgba(15,23,42,0.48)_45%,rgba(56,189,248,0.12)_100%)] px-2 py-1 text-[11px] text-slate-200">
                <span className="font-semibold text-cyan-100">Insight:</span> {derivedMetrics.stability.insight}
              </p>
            </MetricCard>

            <MetricCard
              title="Risk Control"
              primaryValue={formatMetricValue(maxDrawdownMetric, formatPnl)}
              primaryClassName={cn("tracking-tight drop-shadow-[0_1px_12px_rgba(15,23,42,0.5)]", riskPrimaryClassName)}
              subtitle="Peak-to-trough drawdown with account-risk context."
              info="Drawdown control is graded from max drawdown as a share of equity base. The dashboard uses current account balance when available and falls back to peak equity inferred from daily PnL. Profit giveback is shown separately and does not determine control."
              accentClassName={riskAccentClassName}
              className={cn(
                "self-start sm:col-span-2 md:col-span-2 md:row-start-1 md:col-start-5 lg:col-span-3 lg:row-start-1 lg:col-start-10",
                riskCardClassName,
              )}
              contentClassName="mt-2.5 space-y-2.5"
            >
              <div className="rounded-xl border border-white/10 bg-slate-950/35 p-2.5 backdrop-blur-sm">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant={riskSignalVariant}>{riskSignalLabel}</Badge>
                  <span className="ml-auto text-[10px] uppercase tracking-[0.12em] text-slate-400">Account Risk</span>
                </div>
                <div className="mt-2.5 space-y-1.5">
                  <div className="flex items-center justify-between text-[10px] uppercase tracking-[0.12em] text-slate-400">
                    <span>Max DD % of Equity Base</span>
                    <span className="font-semibold text-slate-200">{formatMetricValue(drawdownPercentOfEquityBase, formatPercent)}</span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full border border-slate-700/80 bg-slate-900/85">
                    <div
                      aria-hidden="true"
                      className={cn(
                        "h-full transition-all duration-500",
                        drawdownPercentOfEquityBase.value === null || drawdownPercentOfEquityBase.value <= 10
                          ? "bg-gradient-to-r from-cyan-300/90 to-emerald-300/80"
                          : drawdownPercentOfEquityBase.value <= 15
                            ? "bg-gradient-to-r from-amber-300/90 to-orange-300/80"
                            : "bg-gradient-to-r from-rose-300/95 to-orange-300/85",
                      )}
                      style={{ width: `${riskPressurePercent}%` }}
                    />
                  </div>
                  <p className="text-[10px] text-slate-400">{drawdownEquityBase.detail}</p>
                </div>
              </div>
              <MiniStatList
                items={[
                  { label: "Max DD % of Equity Base", value: formatMetricValue(drawdownPercentOfEquityBase, formatPercent) },
                  { label: "Profit Giveback", value: formatMetricValue(drawdownPercentOfNet, formatPercent) },
                  { label: "Equity Base", value: drawdownEquityBase.value === null ? "N/A" : formatCurrency(drawdownEquityBase.value) },
                  { label: "Basis", value: drawdownEquityBase.label },
                  { label: "Avg Drawdown", value: formatPnl(summary.average_drawdown), valueClassName: metricPnlClass({ value: summary.average_drawdown }) },
                  { label: "DD Length", value: `${formatNumber(summary.max_drawdown_length_hours, 1)} h` },
                ]}
              />
            </MetricCard>

            <MetricCard
              title="Direction"
              primaryValue={directionPrimaryValue}
              primaryClassName="bg-gradient-to-r from-emerald-200 via-cyan-100 to-rose-200 bg-clip-text text-transparent"
              subtitle={
                directionSplit.longPercent.value === null
                  ? directionSplit.longPercent.missingReason ?? "Needs directional trade history."
                  : "Long vs short trade mix."
              }
              info="Long % is long trades divided by total directional trades for this range."
              accentClassName="bg-gradient-to-r from-emerald-300/80 via-cyan-200/25 to-rose-300/80"
              className="relative flex flex-col overflow-hidden md:col-span-2 md:row-start-2 md:col-start-3 lg:col-span-6 lg:col-start-4 lg:row-start-2"
              contentClassName="relative mt-2.5 flex flex-col gap-2.5 space-y-0"
            >
              <div className="relative overflow-hidden rounded-xl border border-cyan-500/20 bg-[radial-gradient(120%_130%_at_6%_0%,rgba(16,185,129,0.16),rgba(15,23,42,0.25)_42%,rgba(15,23,42,0.75)_100%)] p-2.5">
                <div aria-hidden="true" className="pointer-events-none absolute -left-8 top-0 h-20 w-20 rounded-full bg-emerald-300/18 blur-2xl" />
                <div aria-hidden="true" className="pointer-events-none absolute -right-6 bottom-1 h-24 w-24 rounded-full bg-rose-300/14 blur-2xl" />
                <div className="relative grid gap-2.5 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
                  <DonutRing
                    className="lg:items-start"
                    segments={[
                      {
                        label: "Long",
                        value: directionSplit.longPercent.value,
                        valueLabel: formatMetricValue(directionSplit.longPercent, (value) => formatPercent(value, 0)),
                        color: "rgba(16,185,129,0.95)",
                      },
                      {
                        label: "Short",
                        value: directionSplit.shortPercent.value,
                        valueLabel: formatMetricValue(directionSplit.shortPercent, (value) => formatPercent(value, 0)),
                        color: "rgba(248,113,113,0.95)",
                      },
                    ]}
                    centerLabel={directionPrimaryValue}
                    centerSubLabel="Direction"
                  />
                    <div className="space-y-1.5">
                      <div className="overflow-hidden rounded-lg border border-slate-700/70 bg-slate-950/45 shadow-[inset_0_1px_0_rgba(148,163,184,0.07)]">
                        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,0.9fr)_minmax(0,0.9fr)] border-b border-slate-700/65 bg-slate-900/90 px-2 py-1 text-[9px] uppercase tracking-[0.12em] text-slate-400">
                          <span>Side Comparison</span>
                          <span className="text-right text-emerald-300">Long</span>
                          <span className="text-right text-rose-300">Short</span>
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
                          className={`grid grid-cols-[minmax(0,1fr)_minmax(0,0.9fr)_minmax(0,0.9fr)] border-t border-slate-800/65 px-2 py-1 text-[10px] ${
                            rowIndex % 2 === 0 ? "bg-slate-950/30" : "bg-slate-900/45"
                          }`}
                        >
                          <span className="text-slate-300">{row.label}</span>
                          <span className="text-right font-medium text-emerald-100">{row.long}</span>
                          <span className="text-right font-medium text-rose-100">{row.short}</span>
                        </div>
                      ))}
                    </div>
                    <div className="space-y-1 rounded-md border border-slate-700/70 bg-slate-950/45 p-1.5">
                      <div className="flex items-center justify-between text-[10px] uppercase tracking-[0.12em] text-slate-400">
                        <span>PnL Share Split</span>
                        <span className="text-slate-300">
                          <span className="text-emerald-200">
                            {formatMetricValueWithNote(derivedMetrics.direction.longPnlShare, (value) => `Long ${formatPercent(value, 1)}`)}
                          </span>{" "}
                          /{" "}
                          <span className="text-rose-200">
                            {formatMetricValueWithNote(derivedMetrics.direction.shortPnlShare, (value) => `Short ${formatPercent(value, 1)}`)}
                          </span>
                        </span>
                      </div>
                      <div className="relative h-2 overflow-hidden rounded-full border border-slate-700/80 bg-slate-900/90">
                        <div className="absolute inset-0 bg-[linear-gradient(90deg,rgba(16,185,129,0.18)_0%,rgba(15,23,42,0)_50%,rgba(248,113,113,0.18)_100%)]" />
                        <div
                          className="relative h-full bg-gradient-to-r from-emerald-300/90 to-emerald-400/80"
                          style={{ width: `${longPnlShareWidth}%` }}
                          aria-hidden="true"
                        />
                        <div
                          className="absolute right-0 top-0 h-full bg-gradient-to-l from-rose-300/85 to-rose-400/75"
                          style={{ width: `${shortPnlShareWidth}%` }}
                          aria-hidden="true"
                        />
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <SplitBar
                className="rounded-md border border-slate-700/70 bg-slate-950/35 p-1.5"
                leftLabel="Long PnL"
                rightLabel="Short PnL"
                leftValue={formatMetricValue(derivedMetrics.direction.longPnl, formatPnl)}
                rightValue={formatMetricValue(derivedMetrics.direction.shortPnl, formatPnl)}
                leftMagnitude={Math.abs(derivedMetrics.direction.longPnl.value ?? 0)}
                rightMagnitude={Math.abs(derivedMetrics.direction.shortPnl.value ?? 0)}
                leftBarClassName="bg-gradient-to-r from-emerald-300/95 to-emerald-400/80"
                rightBarClassName="bg-gradient-to-l from-rose-300/90 to-rose-400/80"
              />
              <p className="rounded-md border border-cyan-500/20 bg-[linear-gradient(120deg,rgba(16,185,129,0.12),rgba(15,23,42,0.48)_48%,rgba(248,113,113,0.1)_100%)] px-2 py-1 text-[10px] text-slate-200">
                <span className="font-semibold text-cyan-100">Insight:</span> {derivedMetrics.direction.insight}
              </p>
            </MetricCard>

            <MetricCard
              title="Payoff"
              primaryValue={formatMetricValue(derivedMetrics.winLossRatio, (value) => `${formatNumber(value)}x`)}
              primaryClassName={cn("tracking-tight drop-shadow-[0_1px_10px_rgba(15,23,42,0.45)]", payoffPrimaryClassName)}
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
                  derivedMetrics.winLossRatio.value !== null && derivedMetrics.winLossRatio.value < 1 ? "bg-rose-300/22" : "bg-emerald-300/20",
                )}
              />
              <div className="relative rounded-xl border border-white/10 bg-slate-950/35 p-2.5 backdrop-blur-sm">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant={payoffSignalVariant}>{payoffSignalLabel}</Badge>
                  <Badge variant="accent">{`W/L ${formatMetricValue(derivedMetrics.winLossRatio, (value) => `${formatNumber(value)}x`)}`}</Badge>
                  <span className="ml-auto text-[10px] uppercase tracking-[0.12em] text-slate-400">
                    {`Capture ${formatMetricValueWithNote(derivedMetrics.payoff.capture, (value) => formatPercent(value * 100, 1))}`}
                  </span>
                </div>
                <SplitBar
                  className="mt-2.5 rounded-md border border-slate-700/70 bg-slate-950/45 p-1.5"
                  leftLabel="Avg Win"
                  rightLabel="Avg Loss"
                  leftValue={formatMetricValue(derivedMetrics.payoff.averageWin, formatPnl)}
                  rightValue={formatMetricValue(derivedMetrics.payoff.averageLoss, formatPnl)}
                  leftMagnitude={Math.abs(derivedMetrics.payoff.averageWin.value ?? 0)}
                  rightMagnitude={Math.abs(derivedMetrics.payoff.averageLoss.value ?? 0)}
                  leftBarClassName="bg-gradient-to-r from-emerald-300/95 to-cyan-300/80"
                  rightBarClassName="bg-gradient-to-l from-rose-300/90 to-orange-300/80"
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
              <div className="space-y-1 rounded-xl border border-slate-700/70 bg-slate-950/35 p-2">
                <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Points Payoff By Basis</p>
                <div className="overflow-hidden rounded-md border border-slate-800/70 bg-slate-950/25">
                  <div className="grid grid-cols-[minmax(0,0.7fr)_minmax(0,1fr)_minmax(0,1fr)] px-2 py-1 text-[9px] uppercase tracking-[0.12em] text-slate-500">
                    <span>Basis</span>
                    <span className="text-right">Avg Point Gain</span>
                    <span className="text-right">Avg Point Loss</span>
                  </div>
                  {DISPLAY_PAYOFF_POINTS_BASES.map((basis) => (
                    <div
                      key={basis}
                      className="grid grid-cols-[minmax(0,0.7fr)_minmax(0,1fr)_minmax(0,1fr)] border-t border-slate-800/65 px-2 py-1 text-[10px]"
                    >
                      <span className="font-semibold text-slate-200">{basis}</span>
                      <span className="text-right text-emerald-200">{formatPointMetric(pointPayoffByBasis[basis].avgPointGain, basis)}</span>
                      <span className="text-right text-rose-200">{formatPointMetric(pointPayoffByBasis[basis].avgPointLoss, basis)}</span>
                    </div>
                  ))}
                </div>
              </div>
            </MetricCard>

            <MetricCard
              title="Activity"
              primaryValue={formatInteger(summary.trade_count)}
              primaryClassName={cn("tracking-tight drop-shadow-[0_1px_10px_rgba(15,23,42,0.45)]", activityPrimaryClassName)}
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
                  activityMetrics.tradesPerWeek !== null && activityMetrics.tradesPerWeek >= 30 ? "bg-amber-300/20" : "bg-cyan-300/18",
                )}
              />
              <div className="relative rounded-xl border border-white/10 bg-slate-950/35 p-2.5 backdrop-blur-sm">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant={activitySignalVariant}>{activitySignalLabel}</Badge>
                  <Badge variant="accent">{`${formatInteger(summary.active_days)} Active Days`}</Badge>
                  <span className="ml-auto text-[10px] uppercase tracking-[0.12em] text-slate-400">
                    {`Avg ${formatNumber(summary.avg_trades_per_day, 1)} / day`}
                  </span>
                </div>
                <div className="mt-2.5 space-y-1.5">
                  <div className="flex items-center justify-between text-[10px] uppercase tracking-[0.12em] text-slate-400">
                    <span>Pacing vs 30 Trades/Week</span>
                    <span className="font-semibold text-slate-200">
                      {activityMetrics.tradesPerWeek === null ? "N/A" : formatNumber(activityMetrics.tradesPerWeek, 1)}
                    </span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full border border-slate-700/80 bg-slate-900/85">
                    <div
                      aria-hidden="true"
                      className={cn(
                        "h-full transition-all duration-500",
                        activityMetrics.tradesPerWeek !== null && activityMetrics.tradesPerWeek >= 30
                          ? "bg-gradient-to-r from-amber-300/90 to-orange-300/80"
                          : "bg-gradient-to-r from-cyan-300/90 to-emerald-300/80",
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
                    value: activityMetrics.tradesPerWeek === null ? "N/A" : formatNumber(activityMetrics.tradesPerWeek, 1),
                  },
                  {
                    label: "Days/week",
                    value: activityMetrics.activeDaysPerWeek === null ? "N/A" : formatNumber(activityMetrics.activeDaysPerWeek, 1),
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
              primaryClassName={cn("tracking-tight drop-shadow-[0_1px_10px_rgba(15,23,42,0.45)]", sustainabilityPrimaryClassName)}
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
                  sustainability.score >= 70 ? "bg-emerald-300/18" : sustainability.score >= 45 ? "bg-cyan-300/18" : "bg-rose-300/20",
                )}
              />
              <div className="relative rounded-xl border border-white/10 bg-slate-950/35 p-2.5 backdrop-blur-sm">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-[11px] uppercase tracking-[0.14em] text-slate-500">Score 0-100</p>
                  <Badge variant={sustainabilityBadgeVariant(sustainability.label)}>{sustainability.label}</Badge>
                </div>

                <div className="mt-2.5 grid gap-1.5 sm:grid-cols-3">
                  {[
                    {
                      label: "Risk",
                      value: sustainability.riskScore,
                      fillClassName: "bg-gradient-to-r from-rose-300/90 to-amber-300/75",
                    },
                    {
                      label: "Consistency",
                      value: sustainability.consistencyScore,
                      fillClassName: "bg-gradient-to-r from-cyan-300/90 to-indigo-300/80",
                    },
                    {
                      label: "Edge",
                      value: sustainability.edgeScore,
                      fillClassName: "bg-gradient-to-r from-emerald-300/90 to-cyan-300/80",
                    },
                  ].map((item) => (
                    <div key={item.label} className="space-y-1 rounded-md border border-slate-700/70 bg-slate-950/45 px-2 py-1.5">
                      <div className="flex items-center justify-between text-[10px]">
                        <span className="text-slate-300">{item.label}</span>
                        <span className="font-semibold text-slate-100">{formatNumber(item.value, 1)}/100</span>
                      </div>
                      <div className="h-1.5 overflow-hidden rounded-full border border-slate-700/80 bg-slate-900/85">
                        <div className={cn("h-full", item.fillClassName)} style={{ width: `${Math.max(0, Math.min(100, item.value))}%` }} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="space-y-1 rounded-xl border border-slate-700/70 bg-slate-950/35 p-2.5">
                <div className="flex items-center justify-between text-[10px] uppercase tracking-[0.12em] text-slate-500">
                  <span>Score Gauge</span>
                  <span className="font-semibold text-slate-300">{formatInteger(sustainability.score)}/100</span>
                </div>
                <div className="relative pt-3.5">
                  <div className="h-2 overflow-hidden rounded-full border border-slate-700/80 bg-slate-900/85">
                    <div
                      aria-hidden="true"
                      className={`h-full transition-all duration-500 ${sustainabilityFillClass(sustainability.score)}`}
                      style={{ width: `${sustainability.score}%` }}
                    />
                  </div>
                  {[40, 60, 80].map((tick) => (
                    <div key={tick} className="pointer-events-none absolute top-0 -translate-x-1/2" style={{ left: `${tick}%` }}>
                      <span className="block text-[10px] text-slate-500">{tick}</span>
                      <span className="mx-auto mt-0.5 block h-2 w-px bg-slate-500/70" />
                    </div>
                  ))}
                </div>
              </div>
            </MetricCard>

            <MetricCard
              title="Hold Time"
              primaryValue={formatMetricValue(derivedMetrics.winDurationOverLossDuration, (value) => `${formatNumber(value)}x`)}
              primaryClassName={cn("tracking-tight drop-shadow-[0_1px_10px_rgba(15,23,42,0.45)]", holdTimePrimaryClassName)}
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
                    ? "bg-amber-300/20"
                    : "bg-cyan-300/20",
                )}
              />
              <div className="relative rounded-xl border border-white/10 bg-slate-950/35 p-2.5 backdrop-blur-sm">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant={holdTimeSignalVariant}>{holdTimeSignalLabel}</Badge>
                  <span className="ml-auto text-[10px] uppercase tracking-[0.12em] text-slate-400">
                    {`Ratio ${formatMetricValue(derivedMetrics.winDurationOverLossDuration, (value) => `${formatNumber(value)}x`)}`}
                  </span>
                </div>
                <SplitBar
                  className="mt-2.5 rounded-md border border-slate-700/70 bg-slate-950/45 p-1.5"
                  leftLabel="Avg Win Duration"
                  rightLabel="Avg Loss Duration"
                  leftValue={formatDurationCompact(summary.avg_win_duration_minutes)}
                  rightValue={formatDurationCompact(summary.avg_loss_duration_minutes)}
                  leftMagnitude={summary.avg_win_duration_minutes}
                  rightMagnitude={summary.avg_loss_duration_minutes}
                  leftBarClassName="bg-gradient-to-r from-cyan-300/90 to-emerald-300/80"
                  rightBarClassName="bg-gradient-to-l from-amber-300/90 to-orange-300/80"
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

      <Suspense
        fallback={
          <DeferredDashboardCardSkeleton
            title="Account Balance"
            description="Loading the daily balance view."
            bodyHeightClassName="h-[360px]"
          />
        }
      >
        <DailyAccountBalanceCard
          days={pnlCalendarDays}
          loading={pnlCalendarLoading}
          error={pnlCalendarError}
          currentBalance={selectedAccount?.balance ?? null}
        />
      </Suspense>

      <Suspense
        fallback={
          <DeferredDashboardCardSkeleton
            title="PnL Calendar"
            description="Loading calendar performance and journal markers."
            bodyHeightClassName="h-[520px]"
          />
        }
      >
        <PnlCalendarCard
          days={pnlCalendarDays}
          loading={pnlCalendarLoading}
          error={pnlCalendarError}
          journalDays={journalDays}
          journalDaysLoading={journalDaysLoading}
          selectedDate={selectedTradeDate}
          onDaySelect={setSelectedTradeDate}
          onJournalDayOpen={openJournalForDate}
          onAddJournalForSelectedDay={openJournalForDate}
          onVisibleRangeChange={handleCalendarVisibleRangeChange}
        />
      </Suspense>

      <Card>
        <CardHeader className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>{selectedTradeDate ? "Trade Events" : "Recent Trade Events"}</CardTitle>
            <CardDescription>
              {selectedTradeDate
                ? `Showing trades for ${selectedTradeDateLabel ?? selectedTradeDate}, up to ${DAY_FILTER_TRADE_LIMIT} events.`
                : `Showing up to ${TRADE_LIMIT} most recent events for the active account.`}
            </CardDescription>
          </div>
          {selectedTradeDate ? (
            <Button variant="ghost" size="sm" onClick={() => setSelectedTradeDate(null)}>
              Clear Day Filter
            </Button>
          ) : null}
        </CardHeader>
        <CardContent className="space-y-0">
          <div className="max-h-[320px] overflow-auto rounded-xl border border-slate-800/80">
            <table className="w-full min-w-[1100px] table-fixed border-collapse text-sm whitespace-nowrap">
              <thead className="sticky top-0 z-10 bg-slate-900/95 text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="w-[10%] px-2 py-2 text-left font-medium">Entry Time (ET)</th>
                  <th className="w-[10%] px-2 py-2 text-left font-medium">Exit Time (ET)</th>
                  <th className="w-[10%] px-2 py-2 text-center font-medium">Duration</th>
                  <th className="w-[10%] px-2 py-2 text-center font-medium">Symbol</th>
                  <th className="w-[10%] px-2 py-2 text-center font-medium">Direction</th>
                  <th className="w-[10%] px-2 py-2 text-center font-medium">Size</th>
                  <th className="w-[10%] px-2 py-2 text-right font-medium">Entry Price</th>
                  <th className="w-[10%] px-2 py-2 text-right font-medium">Exit Price</th>
                  <th className="w-[10%] px-2 py-2 text-right font-medium">PnL</th>
                  <th className="w-[10%] px-2 py-2 text-right font-medium">Trade ID</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {recentTradesLoading ? (
                  <tr>
                    <td colSpan={10} className="px-2 py-4 text-center text-slate-400">
                      Loading trades...
                    </td>
                  </tr>
                ) : recentTradesError ? (
                  <tr>
                    <td colSpan={10} className="px-2 py-4 text-center text-rose-300">
                      {recentTradesError}
                    </td>
                  </tr>
                ) : recentTrades.length === 0 ? (
                  <tr>
                    <td colSpan={10} className="px-2 py-4 text-center text-slate-400">
                      No trades available.
                    </td>
                  </tr>
                ) : (
                  recentTrades.map((trade) => {
                    const pnlValue = trade.pnl ?? 0;
                    const direction = formatTradeDirection(trade.side);
                    const entryTime = trade.entry_time;
                    const exitTime = trade.exit_time ?? trade.timestamp;
                    const entryPrice = trade.entry_price;
                    const exitPrice = trade.exit_price ?? trade.price;
                    return (
                      <tr key={trade.id} className="transition hover:bg-slate-900/65">
                        <td className="px-2 py-2 text-left text-slate-300">
                          {entryTime ? timestampFormatter.format(new Date(entryTime)) : "-"}
                        </td>
                        <td className="px-2 py-2 text-left text-slate-300">
                          {timestampFormatter.format(new Date(exitTime))}
                        </td>
                        <td className="px-2 py-2 text-center text-slate-300">
                          {formatTradeDuration(trade.duration_minutes)}
                        </td>
                        <td className="px-2 py-2 text-center font-medium text-slate-100">
                          {getDisplayTradeSymbol(trade.symbol, trade.contract_id)}
                        </td>
                        <td className="px-2 py-2 text-center">
                          <Badge variant={tradeDirectionBadgeVariant(trade.side)}>{direction}</Badge>
                        </td>
                        <td className="px-2 py-2 text-center text-slate-200">{formatInteger(trade.size)}</td>
                        <td className="px-2 py-2 text-right font-mono text-slate-200">
                          {entryPrice == null ? "-" : entryPrice.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 5 })}
                        </td>
                        <td className="px-2 py-2 text-right font-mono text-slate-200">
                          {exitPrice.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 5 })}
                        </td>
                        <td className={`px-2 py-2 text-right font-semibold ${pnlClass(pnlValue)}`}>{formatPnl(pnlValue)}</td>
                        <td className="px-2 py-2 text-right font-mono text-slate-400">
                          {trade.source_trade_id ?? trade.order_id}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
