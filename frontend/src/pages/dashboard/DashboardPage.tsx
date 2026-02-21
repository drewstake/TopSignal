import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { ChipRow } from "../../components/metrics/ChipRow";
import { DirectionDonut } from "../../components/metrics/DirectionDonut";
import { MetricCard } from "../../components/metrics/MetricCard";
import { SplitBar } from "../../components/metrics/SplitBar";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Input } from "../../components/ui/Input";
import {
  ACCOUNT_QUERY_PARAM,
  parseAccountId,
  readStoredAccountId,
  writeStoredAccountId,
} from "../../lib/accountSelection";
import { accountsApi } from "../../lib/api";
import { getDisplayTradeSymbol } from "../../lib/tradeSymbol";
import { ACCOUNT_TRADES_SYNCED_EVENT, type AccountTradesSyncedDetail } from "../../lib/tradeSyncEvents";
import type { AccountInfo, AccountPnlCalendarDay, AccountSummary, AccountTrade } from "../../lib/types";
import { formatCurrency, formatInteger, formatMinutes, formatNumber, formatPercent, formatPnl } from "../../utils/formatters";
import {
  computeBreakevenWinRate,
  computeDirectionPercentages,
  computeDrawdownPercentOfNetPnl,
  computeStabilityScoreFromWorstDayPercent,
} from "../../utils/metrics";
import { PnlCalendarCard } from "./components/PnlCalendarCard";
import { computeDashboardDerivedMetrics } from "./metrics/calculations";
import type { MetricValue } from "./metrics/types";

const TRADE_LIMIT = 200;
const DAY_FILTER_TRADE_LIMIT = 1000;
const METRIC_TRADE_LIMIT = 1000;
type MetricsRangePreset = "1D" | "1W" | "1M" | "6M" | "ALL" | "CUSTOM";

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
  hour12: false,
  timeZone: "UTC",
});

const dateFormatter = new Intl.DateTimeFormat("en-US", {
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
};

function formatFee(value: number) {
  return formatCurrency(-Math.abs(value));
}

function pnlClass(value: number) {
  return value >= 0 ? "text-emerald-300" : "text-rose-300";
}

function pnlChipClass(value: number) {
  return value >= 0
    ? "border-emerald-400/30 bg-emerald-500/10 text-emerald-100"
    : "border-rose-400/35 bg-rose-500/10 text-rose-100";
}

function formatMetricValue(metric: MetricValue, formatter: (value: number) => string) {
  if (metric.value === null) {
    return "N/A";
  }
  return formatter(metric.value);
}

function metricDetail(metric: MetricValue, availableText: string) {
  if (metric.value === null) {
    return metric.missingReason ?? "Needs required source data.";
  }
  return availableText;
}

function metricPnlClass(metric: MetricValue) {
  if (metric.value === null) {
    return undefined;
  }
  return pnlClass(metric.value);
}

function sideVariant(side: string) {
  const normalized = side.toUpperCase();
  if (normalized === "BUY") {
    return "accent" as const;
  }
  if (normalized === "SELL") {
    return "warning" as const;
  }
  return "neutral" as const;
}

function parseUtcDay(value: string) {
  return new Date(`${value}T00:00:00.000Z`);
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

function parseDateInput(value: string) {
  const [yearText, monthText, dayText] = value.split("-");
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  if (!Number.isInteger(year) || !Number.isInteger(month) || !Number.isInteger(day)) {
    return null;
  }
  return { year, month, day };
}

function toUtcIsoDate(value: string, endOfDay: boolean) {
  const parsed = parseDateInput(value);
  if (!parsed) {
    return null;
  }
  const hour = endOfDay ? 23 : 0;
  const minute = endOfDay ? 59 : 0;
  const second = endOfDay ? 59 : 0;
  const millisecond = endOfDay ? 999 : 0;
  return new Date(Date.UTC(parsed.year, parsed.month - 1, parsed.day, hour, minute, second, millisecond)).toISOString();
}

function buildMetricsRangeQuery(
  range: MetricsRangePreset,
  customRange: CustomDateRange | null,
): { start?: string; end?: string; allTime: boolean } {
  if (range === "CUSTOM") {
    if (!customRange) {
      return { allTime: true };
    }
    const start = toUtcIsoDate(customRange.startDate, false);
    const end = toUtcIsoDate(customRange.endDate, true);
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
      start = new Date(end.getTime() - 24 * 60 * 60 * 1000);
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

function getUtcDayRange(value: string) {
  const start = parseUtcDay(value);
  const end = new Date(start.getTime() + 24 * 60 * 60 * 1000 - 1);
  return {
    start: start.toISOString(),
    end: end.toISOString(),
  };
}

export function DashboardPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const accountFromQuery = parseAccountId(searchParams.get(ACCOUNT_QUERY_PARAM));

  const [accounts, setAccounts] = useState<AccountInfo[]>([]);

  const [summary, setSummary] = useState<AccountSummary>(emptySummary);
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
      const payload = await accountsApi.getAccounts();
      setAccounts(payload);
    } catch {
      setAccounts([]);
    }
  }, []);

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

  useEffect(() => {
    if (accounts.length === 0) {
      return;
    }

    if (accountFromQuery && accounts.some((account) => account.id === accountFromQuery)) {
      writeStoredAccountId(accountFromQuery);
      return;
    }

    const storedAccountId = readStoredAccountId();
    if (storedAccountId && accounts.some((account) => account.id === storedAccountId)) {
      setActiveAccount(storedAccountId);
      return;
    }

    setActiveAccount(accounts[0].id);
  }, [accounts, accountFromQuery, setActiveAccount]);

  const selectedAccount = useMemo(
    () => accounts.find((account) => account.id === accountFromQuery) ?? null,
    [accounts, accountFromQuery],
  );
  const selectedAccountId = selectedAccount?.id ?? null;
  const metricsRangeQuery = useMemo(() => buildMetricsRangeQuery(metricsRange, customRange), [customRange, metricsRange]);
  const customRangeInvalid = customStartDate !== "" && customEndDate !== "" && customStartDate > customEndDate;

  const selectedTradeDateLabel = useMemo(() => {
    if (!selectedTradeDate) {
      return null;
    }
    return dateFormatter.format(parseUtcDay(selectedTradeDate));
  }, [selectedTradeDate]);

  const loadSummaryAndCalendar = useCallback(async () => {
    if (!selectedAccountId) {
      setSummary(emptySummary);
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
      const [nextSummary, nextPnlCalendar] = await Promise.all([
        accountsApi.getSummary(selectedAccountId, {
          start: metricsRangeQuery.start,
          end: metricsRangeQuery.end,
        }),
        accountsApi.getPnlCalendar(selectedAccountId, {
          start: metricsRangeQuery.start,
          end: metricsRangeQuery.end,
          all_time: metricsRangeQuery.allTime,
        }),
      ]);
      setSummary(nextSummary);
      setPnlCalendarDays(nextPnlCalendar);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load dashboard data";
      setSummaryError(message);
      setPnlCalendarError(message);
      setSummary(emptySummary);
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

    setTradesLoading(true);
    setTradesError(null);

    try {
      const query = selectedTradeDate
        ? { limit: DAY_FILTER_TRADE_LIMIT, ...getUtcDayRange(selectedTradeDate) }
        : { limit: TRADE_LIMIT };
      const nextTrades = await accountsApi.getTrades(selectedAccountId, query);
      setTrades(nextTrades);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load trade events";
      setTradesError(message);
      setTrades([]);
    } finally {
      setTradesLoading(false);
    }
  }, [selectedAccountId, selectedTradeDate]);

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

  const netPnlMetric: MetricValue = { value: summary.net_pnl };
  const profitPerDayMetric: MetricValue = { value: summary.profit_per_day };
  const efficiencyPerHourMetric: MetricValue = { value: summary.efficiency_per_hour };
  const expectancyPerTradeMetric: MetricValue = { value: summary.expectancy_per_trade };
  const maxDrawdownMetric: MetricValue = { value: summary.max_drawdown };
  const averageWinMetric: MetricValue = { value: summary.avg_win };
  const averageLossMetric: MetricValue = { value: summary.avg_loss };

  const drawdownPercentOfNet = useMemo(
    () => computeDrawdownPercentOfNetPnl(summary.max_drawdown, summary.net_pnl),
    [summary.max_drawdown, summary.net_pnl],
  );
  const payoffBreakevenWinRate = useMemo(() => computeBreakevenWinRate(summary.avg_win, summary.avg_loss), [summary.avg_loss, summary.avg_win]);

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

  const directionPrimaryValue =
    directionSplit.longPercent.value === null ? "N/A" : `${formatPercent(directionSplit.longPercent.value, 0)} Long`;
  const directionBiasText =
    directionSplit.longPercent.value === null || directionSplit.shortPercent.value === null
      ? directionSplit.longPercent.missingReason ?? "Needs directional trade data."
      : `Bias: Long ${formatPercent(directionSplit.longPercent.value, 0)}, Short ${formatPercent(directionSplit.shortPercent.value, 0)}`;

  const longDirectionTooltip =
    derivedMetrics.direction.longTrades.value === null
      ? derivedMetrics.direction.longTrades.missingReason ?? "Needs long trades."
      : `Long ${formatInteger(derivedMetrics.direction.longTrades.value)} trades | Win ${formatMetricValue(derivedMetrics.direction.longWinRate, formatPercent)} | PnL ${formatMetricValue(derivedMetrics.direction.longPnl, formatPnl)}`;

  const shortDirectionTooltip =
    derivedMetrics.direction.shortTrades.value === null
      ? derivedMetrics.direction.shortTrades.missingReason ?? "Needs short trades."
      : `Short ${formatInteger(derivedMetrics.direction.shortTrades.value)} trades | Win ${formatMetricValue(derivedMetrics.direction.shortWinRate, formatPercent)} | PnL ${formatMetricValue(derivedMetrics.direction.shortPnl, formatPnl)}`;

  return (
    <div className="space-y-6 pb-10">
      <Card className="!bg-transparent !shadow-none border-slate-800/60 p-2 md:p-2">
        <CardHeader className="mb-0 space-y-1">
          <div className="overflow-x-auto rounded-xl border border-slate-800/80 bg-slate-950/35 px-2 py-1.5">
            <div className="flex min-w-max items-center gap-1.5">
              <Input
                type="date"
                value={customStartDate}
                max={customEndDate || undefined}
                onChange={(event) => setCustomStartDate(event.target.value)}
                className="h-8 w-[140px] rounded-lg border-slate-700/80 bg-slate-900/55 px-2 text-xs"
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
                className="h-8 w-[140px] rounded-lg border-slate-700/80 bg-slate-900/55 px-2 text-xs"
                aria-label="Custom end date"
              />
              {METRICS_RANGE_OPTIONS.map((option) => {
                const active = option.key === metricsRange;
                return (
                  <Button
                    key={option.key}
                    variant={active ? "secondary" : "ghost"}
                    size="sm"
                    aria-pressed={active}
                    onClick={() => setMetricsRange(option.key)}
                    className={active ? "ring-1 ring-cyan-300/60" : undefined}
                  >
                    {option.label}
                  </Button>
                );
              })}
            </div>
          </div>
          {customRangeInvalid ? <p className="w-full text-xs text-rose-300">End date must be on or after start date.</p> : null}
        </CardHeader>
      </Card>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
        {summaryLoading ? (
          Array.from({ length: 8 }).map((_, index) => (
            <MetricCard key={`summary-loading-${index}`} title="Loading" primaryValue="..." className="animate-pulse" />
          ))
        ) : summaryError ? (
          <Card className="col-span-2 p-4 md:col-span-3 lg:col-span-4">
            <p className="text-sm text-rose-300">{summaryError}</p>
          </Card>
        ) : (
          <>
            <MetricCard
              title="Performance"
              primaryValue={formatMetricValue(netPnlMetric, formatPnl)}
              primaryClassName={metricPnlClass(netPnlMetric)}
              tooltip="Net PnL with daily and hourly production rates."
              chips={
                <ChipRow
                  chips={[
                    {
                      label: "Profit/Day",
                      value: formatMetricValue(profitPerDayMetric, formatPnl),
                      className: pnlChipClass(summary.profit_per_day),
                      tooltip: "Net PnL divided by active days.",
                    },
                    {
                      label: "Efficiency/Hour",
                      value: formatMetricValue(efficiencyPerHourMetric, formatPnl),
                      className: pnlChipClass(summary.efficiency_per_hour),
                      tooltip: "Tracked net PnL per active hour.",
                    },
                  ]}
                />
              }
              hoverDetails="Net realized PnL after fees for the selected range."
            />

            <MetricCard
              title="Risk"
              primaryValue={formatMetricValue(maxDrawdownMetric, formatPnl)}
              primaryClassName={metricPnlClass(maxDrawdownMetric)}
              tooltip="Largest peak-to-trough equity drop."
              hoverDetails={
                drawdownPercentOfNet.value === null
                  ? drawdownPercentOfNet.missingReason ?? "Needs non-zero net PnL."
                  : `Drawdown is ${formatPercent(drawdownPercentOfNet.value)} of net PnL.`
              }
            />

            <MetricCard
              title="Edge"
              primaryValue={formatMetricValue(expectancyPerTradeMetric, formatPnl)}
              primaryClassName={metricPnlClass(expectancyPerTradeMetric)}
              tooltip="Average expected net result per trade."
              chips={
                <ChipRow
                  chips={[
                    { label: "PF", value: formatNumber(summary.profit_factor), tooltip: "Profit factor." },
                    { label: "Win Rate", value: formatPercent(summary.win_rate), tooltip: "Winning closed trades." },
                    {
                      label: "W/L Ratio",
                      value: formatMetricValue(derivedMetrics.winLossRatio, (value) => formatNumber(value)),
                      tooltip: metricDetail(derivedMetrics.winLossRatio, "Average win / abs(average loss)."),
                    },
                  ]}
                />
              }
              hoverDetails={`${summary.win_count}W / ${summary.loss_count}L / ${summary.breakeven_count} BE`}
            />

            <MetricCard
              title="Payoff"
              primaryValue={formatMetricValue(derivedMetrics.winLossRatio, (value) => `${formatNumber(value)}x`)}
              tooltip="Average win versus average loss profile."
              subValue={
                <SplitBar
                  leftLabel="Avg Win"
                  rightLabel="Avg Loss"
                  leftValue={formatMetricValue(averageWinMetric, formatPnl)}
                  rightValue={formatMetricValue(averageLossMetric, formatPnl)}
                  leftMagnitude={Math.abs(summary.avg_win)}
                  rightMagnitude={Math.abs(summary.avg_loss)}
                  badge={formatMetricValue(derivedMetrics.winLossRatio, (value) => `${formatNumber(value)}x`)}
                />
              }
              hoverDetails={
                payoffBreakevenWinRate.value === null
                  ? payoffBreakevenWinRate.missingReason ?? "Needs non-zero average win and loss."
                  : `Breakeven win rate ${formatPercent(payoffBreakevenWinRate.value)} vs current ${formatPercent(summary.win_rate)}.`
              }
            />

            <MetricCard
              title="Direction"
              primaryValue={directionPrimaryValue}
              tooltip={metricDetail(derivedMetrics.direction.longPercent, "Long vs short directional mix.")}
              subValue={
                <DirectionDonut
                  longPercent={directionSplit.longPercent.value}
                  shortPercent={directionSplit.shortPercent.value}
                  centerText={directionPrimaryValue}
                  biasText={directionBiasText}
                  longDetails={longDirectionTooltip}
                  shortDetails={shortDirectionTooltip}
                  missingReason={directionSplit.longPercent.missingReason}
                />
              }
              hoverDetails="Hover long/short donut segments for count, win rate, and directional PnL."
            />

            <MetricCard
              title="Hold Time"
              primaryValue={formatMetricValue(derivedMetrics.winDurationOverLossDuration, (value) => `${formatNumber(value)}x`)}
              tooltip={metricDetail(derivedMetrics.winDurationOverLossDuration, "Average win hold / average loss hold.")}
              subValue={
                <SplitBar
                  leftLabel="Avg Win Duration"
                  rightLabel="Avg Loss Duration"
                  leftValue={formatMinutes(summary.avg_win_duration_minutes)}
                  rightValue={formatMinutes(summary.avg_loss_duration_minutes)}
                  leftMagnitude={summary.avg_win_duration_minutes}
                  rightMagnitude={summary.avg_loss_duration_minutes}
                  badge={formatMetricValue(derivedMetrics.winDurationOverLossDuration, (value) => `${formatNumber(value)}x`)}
                />
              }
              hoverDetails={`Win avg ${formatMinutes(summary.avg_win_duration_minutes)} | Loss avg ${formatMinutes(summary.avg_loss_duration_minutes)}`}
            />

            <MetricCard
              title="Swing"
              primaryValue={formatMetricValue(derivedMetrics.stability.dailyPnlVolatility, formatCurrency)}
              tooltip={metricDetail(derivedMetrics.stability.dailyPnlVolatility, "Population standard deviation of daily net PnL.")}
              chips={
                <ChipRow
                  chips={[
                    {
                      label: "Best Day",
                      value: formatMetricValue(derivedMetrics.stability.bestDay, formatPnl),
                      className:
                        derivedMetrics.stability.bestDay.value === null
                          ? undefined
                          : pnlChipClass(derivedMetrics.stability.bestDay.value),
                    },
                    {
                      label: "Worst Day",
                      value: formatMetricValue(derivedMetrics.stability.worstDay, formatPnl),
                      className:
                        derivedMetrics.stability.worstDay.value === null
                          ? undefined
                          : pnlChipClass(derivedMetrics.stability.worstDay.value),
                    },
                  ]}
                />
              }
              subValue={
                <div className="space-y-2">
                  <div className="flex items-center justify-between text-[11px] text-slate-400">
                    <span>Best %: {formatMetricValue(derivedMetrics.stability.bestDayPercentOfNet, formatPercent)}</span>
                    <span>Worst %: {formatMetricValue(derivedMetrics.stability.worstDayPercentOfNet, formatPercent)}</span>
                  </div>
                  <div className="space-y-1">
                    <div className="flex items-center justify-between text-[10px] uppercase tracking-[0.14em] text-slate-500">
                      <span>Stability</span>
                      <span>{formatMetricValue(stabilityScore, (value) => formatNumber(value, 0))}</span>
                    </div>
                    <div className="h-2 overflow-hidden rounded-full border border-slate-700/80 bg-slate-900/85">
                      <div
                        className="h-full bg-cyan-300/70 transition-all duration-500"
                        style={{ width: `${stabilityScore.value === null ? 0 : stabilityScore.value}%` }}
                      />
                    </div>
                  </div>
                </div>
              }
              hoverDetails="Stability gauge uses worst-day % of net PnL: smaller drawdowns score higher."
            />

            <MetricCard
              title="Activity"
              primaryValue={formatInteger(summary.trade_count)}
              tooltip="Execution activity in the selected range."
              chips={
                <ChipRow
                  chips={[
                    { label: "Avg Trades/Day", value: formatNumber(summary.avg_trades_per_day) },
                    { label: "Active Days", value: formatInteger(summary.active_days) },
                  ]}
                />
              }
              hoverDetails="Closed trades, normalized by active trading days."
            />
          </>
        )}
      </section>

      <PnlCalendarCard
        days={pnlCalendarDays}
        loading={pnlCalendarLoading}
        error={pnlCalendarError}
        selectedDate={selectedTradeDate}
        onDaySelect={setSelectedTradeDate}
      />

      <Card>
        <CardHeader className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>{selectedTradeDate ? "Trade Events" : "Recent Trade Events"}</CardTitle>
            <CardDescription>
              {selectedTradeDate
                ? `Showing trades for ${selectedTradeDateLabel ?? selectedTradeDate} (UTC), up to ${DAY_FILTER_TRADE_LIMIT} events.`
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
            <table className="w-full min-w-[1040px] border-collapse text-sm">
              <thead className="sticky top-0 z-10 bg-slate-900/95 text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="px-3 py-3 text-left font-medium">Timestamp (UTC)</th>
                  <th className="px-3 py-3 text-left font-medium">Symbol</th>
                  <th className="px-3 py-3 text-left font-medium">Side</th>
                  <th className="px-3 py-3 text-right font-medium">Size</th>
                  <th className="px-3 py-3 text-right font-medium">Price</th>
                  <th className="px-3 py-3 text-right font-medium">Fees</th>
                  <th className="px-3 py-3 text-right font-medium">PnL</th>
                  <th className="px-3 py-3 text-right font-medium">Trade ID</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {tradesLoading ? (
                  <tr>
                    <td colSpan={8} className="px-3 py-6 text-center text-slate-400">
                      Loading trades...
                    </td>
                  </tr>
                ) : tradesError ? (
                  <tr>
                    <td colSpan={8} className="px-3 py-6 text-center text-rose-300">
                      {tradesError}
                    </td>
                  </tr>
                ) : trades.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-3 py-6 text-center text-slate-400">
                      No trades available.
                    </td>
                  </tr>
                ) : (
                  trades.map((trade) => {
                    const pnlValue = trade.pnl ?? 0;
                    return (
                      <tr key={trade.id} className="transition hover:bg-slate-900/65">
                        <td className="px-3 py-3 text-left text-slate-300">
                          {timestampFormatter.format(new Date(trade.timestamp))}
                        </td>
                        <td className="px-3 py-3 text-left font-medium text-slate-100">
                          {getDisplayTradeSymbol(trade.symbol, trade.contract_id)}
                        </td>
                        <td className="px-3 py-3 text-left">
                          <Badge variant={sideVariant(trade.side)}>{trade.side}</Badge>
                        </td>
                        <td className="px-3 py-3 text-right text-slate-200">{trade.size.toFixed(2)}</td>
                        <td className="px-3 py-3 text-right font-mono text-slate-200">
                          {trade.price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 5 })}
                        </td>
                        <td className="px-3 py-3 text-right text-slate-300">{formatFee(trade.fees)}</td>
                        <td className={`px-3 py-3 text-right font-semibold ${pnlClass(pnlValue)}`}>{formatPnl(pnlValue)}</td>
                        <td className="px-3 py-3 text-right font-mono text-slate-400">
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
