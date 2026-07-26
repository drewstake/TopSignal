import { useEffect, useMemo, useState, type ReactNode } from "react";

import { Card } from "../../../components/ui/Card";
import { cn } from "../../../components/ui/cn";
import type { AccountPnlCalendarDay, AccountSummary, AccountTrade } from "../../../lib/types";
import { formatCurrency, formatNumber, formatPercent, formatPnl } from "../../../utils/formatters";
import { buildCompactChartPoints } from "../compactDashboardData";
import { CompactDashboardCalendar } from "./CompactDashboardCalendar";
import {
  CompactContextStrip,
  CompactPerformanceChart,
  CompactScoreCard,
  type CompactPerformanceContext,
  type CompactScoreBreakdown,
} from "./CompactDashboardPerformance";
import {
  CompactMetricCard,
  compactFocusRing,
} from "./CompactDashboardPrimitives";
import { CompactRecentTrades } from "./CompactRecentTrades";

function clamp(value: number, min = 0, max = 100) {
  return Math.min(max, Math.max(min, Number.isFinite(value) ? value : min));
}

function getGreeting(now = new Date()) {
  const hour = now.getHours();
  if (hour < 12) {
    return "Good morning";
  }
  if (hour < 18) {
    return "Good afternoon";
  }
  return "Good evening";
}

function useGreeting() {
  const [greeting, setGreeting] = useState(() => getGreeting());
  useEffect(() => {
    const timer = window.setInterval(() => setGreeting(getGreeting()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  return greeting;
}

function pnlTextClass(value: number) {
  if (value > 0) {
    return "text-app-positive-text";
  }
  if (value < 0) {
    return "text-app-negative-text";
  }
  return "text-app-text";
}

function SkeletonBlock({ className }: { className: string }) {
  return <div className={cn("animate-pulse rounded-xl bg-app-border/35 motion-reduce:animate-none", className)} />;
}

export function CompactDashboardSkeleton() {
  return (
    <section
      className="space-y-4"
      aria-label="Compact dashboard loading"
      aria-busy="true"
      data-dashboard-view="compact"
    >
      <span className="sr-only" role="status" aria-live="polite">Loading compact dashboard</span>
      <div className="flex items-end justify-between gap-4 py-1" aria-hidden="true">
        <div className="space-y-2">
          <SkeletonBlock className="h-5 w-32" />
          <SkeletonBlock className="h-3 w-52 max-w-[70vw]" />
        </div>
        <SkeletonBlock className="h-8 w-28" />
      </div>
      <Card className="grid grid-cols-2 gap-px overflow-hidden p-0 md:grid-cols-4 md:p-0" aria-hidden="true">
        {Array.from({ length: 4 }, (_, index) => (
          <div key={index} className="space-y-2 border-app-border/60 p-4">
            <SkeletonBlock className="h-3 w-20" />
            <SkeletonBlock className="h-5 w-28 max-w-full" />
            <SkeletonBlock className="h-3 w-24 max-w-full" />
          </div>
        ))}
      </Card>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6 lg:grid-cols-5" aria-hidden="true">
        {Array.from({ length: 5 }, (_, index) => (
          <Card
            key={index}
            className={cn(
              "min-h-[112px] space-y-3 p-3 sm:min-h-[132px] sm:p-4 lg:col-span-1",
              index === 0 && "col-span-2 md:col-span-2",
              (index === 1 || index === 2) && "md:col-span-2",
              (index === 3 || index === 4) && "md:col-span-3",
            )}
          >
            <SkeletonBlock className="h-3 w-24" />
            <SkeletonBlock className="h-8 w-28 max-w-full" />
            <SkeletonBlock className="h-3 w-20" />
          </Card>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-5" aria-hidden="true">
        <Card className="min-h-[370px] lg:col-span-3"><SkeletonBlock className="h-full min-h-[330px] w-full" /></Card>
        <Card className="min-h-[370px] lg:col-span-2"><SkeletonBlock className="h-full min-h-[330px] w-full" /></Card>
      </div>
      <div className="grid gap-4 lg:grid-cols-5" aria-hidden="true">
        <Card className="min-h-[500px] lg:col-span-2"><SkeletonBlock className="h-full min-h-[460px] w-full" /></Card>
        <Card className="min-h-[500px] lg:col-span-3"><SkeletonBlock className="h-full min-h-[460px] w-full" /></Card>
      </div>
    </section>
  );
}

export interface CompactDashboardViewProps {
  accountName: string;
  rangeLabel: string;
  rangeStartDate?: string;
  rangeEndDate?: string;
  summary: AccountSummary;
  score: number;
  scoreBreakdown?: CompactScoreBreakdown | null;
  performanceContext?: CompactPerformanceContext | null;
  days: readonly AccountPnlCalendarDay[];
  calendarDays?: readonly AccountPnlCalendarDay[];
  trades: readonly AccountTrade[];
  summaryLoading: boolean;
  summaryError: string | null;
  daysLoading: boolean;
  daysError: string | null;
  tradesLoading: boolean;
  tradesError: string | null;
  journalDays: ReadonlySet<string>;
  journalDaysLoading: boolean;
  journalDaysError?: string | null;
  selectedDate: string | null;
  selectedDateLabel?: string | null;
  calendarScopeKey?: string;
  dataWarnings?: readonly string[];
  accountNameById?: Readonly<Record<number, string>>;
  onDaySelect: (date: string | null) => void;
  onClearDayFilter?: () => void;
  onJournalDayOpen: (date: string) => void;
  onCalendarVisibleRangeChange: (startDate: string, endDate: string) => void;
}

export function CompactDashboardView({
  accountName,
  rangeLabel,
  rangeStartDate,
  rangeEndDate,
  summary,
  score,
  scoreBreakdown = null,
  performanceContext = null,
  days,
  calendarDays,
  trades,
  summaryLoading,
  summaryError,
  daysLoading,
  daysError,
  tradesLoading,
  tradesError,
  journalDays,
  journalDaysLoading,
  journalDaysError = null,
  selectedDate,
  selectedDateLabel,
  calendarScopeKey,
  dataWarnings = [],
  accountNameById,
  onDaySelect,
  onClearDayFilter,
  onJournalDayOpen,
  onCalendarVisibleRangeChange,
}: CompactDashboardViewProps) {
  const greeting = useGreeting();
  const chartPoints = useMemo(
    () => buildCompactChartPoints(days, Math.max(1, days.length)),
    [days],
  );
  const resolvedCalendarDays = calendarDays ?? days;
  const resolvedContext: CompactPerformanceContext = performanceContext ?? {
    tradingDayCount: days.length,
    maxDrawdown: Number.isFinite(summary.max_drawdown) ? summary.max_drawdown : 0,
    riskBase: null,
    riskBaseLabel: "Risk base unavailable",
  };
  const noTrades = !summaryLoading && !summaryError && summary.trade_count === 0;
  const averageLossMagnitude = Math.abs(summary.avg_loss);
  const hasPayoffSample = summary.win_count > 0 && summary.loss_count > 0 && averageLossMagnitude > 0;
  const payoffRatio = hasPayoffSample ? summary.avg_win / averageLossMagnitude : 0;
  const payoffTotal = Math.abs(summary.avg_win) + averageLossMagnitude;
  const winWidth = payoffTotal > 0 ? clamp((Math.abs(summary.avg_win) / payoffTotal) * 100) : 50;
  const lossWidth = 100 - winWidth;
  const clearDayFilter = onClearDayFilter ?? (() => onDaySelect(null));
  const resolvedCalendarScopeKey = calendarScopeKey
    ?? `${accountName}|${rangeStartDate ?? ""}|${rangeEndDate ?? ""}`;
  const selectedFilterLabel = selectedDateLabel ?? selectedDate;

  const metricFootnote = (children: ReactNode) => (
    <div className="mt-2 text-xs text-app-muted-text">{children}</div>
  );

  return (
    <section className="space-y-4" aria-label="Compact dashboard" data-dashboard-view="compact">
      <div className="flex flex-col gap-2 py-0.5 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <p className="text-base font-semibold text-app-text">{greeting}</p>
          <p className="mt-1 truncate text-xs text-app-muted-text">Focused performance context for {accountName}.</p>
        </div>
        <div className="inline-flex w-fit items-center gap-2 rounded-full border border-app-border/70 bg-app-surface/65 px-3 py-2 text-xs text-app-muted-text">
          <span className="h-2 w-2 rounded-full bg-app-accent" aria-hidden="true" />
          Compact view
        </div>
      </div>

      {selectedDate && selectedFilterLabel ? (
        <div
          className="flex flex-wrap items-center gap-2 rounded-xl border border-app-accent/35 bg-app-accent/10 px-3 py-2 text-xs text-app-text"
          role="status"
          aria-live="polite"
        >
          <span className="font-semibold">Day filter:</span>
          <span>{selectedFilterLabel}</span>
          <button
            type="button"
            onClick={clearDayFilter}
            className={cn(
              "ml-auto inline-flex min-h-11 items-center justify-center rounded-xl px-3 font-semibold text-app-text transition hover:bg-app-accent/15",
              compactFocusRing,
            )}
          >
            Clear day filter
          </button>
        </div>
      ) : null}

      {dataWarnings.length > 0 ? (
        <div
          className="rounded-xl border border-app-warning/40 bg-app-warning/10 px-3 py-3 text-xs text-app-text"
          role="status"
          aria-live="polite"
        >
          <p className="font-semibold">Some account data was excluded</p>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-app-muted-text">
            {dataWarnings.map((warning) => <li key={warning}>{warning}</li>)}
          </ul>
        </div>
      ) : null}

      {summaryError ? (
        <div className="rounded-xl border border-app-negative/40 bg-app-negative-soft/25 px-4 py-3 text-sm text-app-negative-text" role="alert">
          Summary unavailable: {summaryError}
        </div>
      ) : null}

      {daysError ? (
        <div className="rounded-xl border border-app-negative/40 bg-app-negative-soft/25 px-4 py-3 text-sm text-app-negative-text" role="alert">
          Performance data unavailable: {daysError}
        </div>
      ) : null}

      <CompactContextStrip
        rangeLabel={rangeLabel}
        context={resolvedContext}
        scoreBreakdown={scoreBreakdown}
        loading={daysLoading}
        error={daysError}
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-6 lg:grid-cols-5">
        <CompactMetricCard
          label="Net P&L"
          info="Realized profit and loss after fees in the active Compact scope."
          value={formatPnl(summary.net_pnl)}
          kind="net"
          loading={summaryLoading}
          error={summaryError}
          unavailableReason={noTrades ? "No closed trades in this scope." : undefined}
          valueClassName={pnlTextClass(summary.net_pnl)}
          className="col-span-2 md:col-span-2 lg:col-span-1"
        >
          {metricFootnote(`${summary.trade_count.toLocaleString("en-US")} closed trade${summary.trade_count === 1 ? "" : "s"}`)}
        </CompactMetricCard>
        <CompactMetricCard
          label="Trade Expectancy"
          info="Average net P&L expected per closed trade in this scope."
          value={formatCurrency(summary.expectancy_per_trade)}
          kind="expectancy"
          loading={summaryLoading}
          error={summaryError}
          unavailableReason={noTrades ? "Requires at least one closed trade." : undefined}
          valueClassName={pnlTextClass(summary.expectancy_per_trade)}
          className="md:col-span-2 lg:col-span-1"
        >
          {metricFootnote("Expected net value per trade")}
        </CompactMetricCard>
        <CompactMetricCard
          label="Profit Factor"
          info="Gross winning P&L divided by gross losing P&L. It is unbounded without a losing trade."
          value={summary.loss_count === 0 && summary.win_count > 0 ? "∞" : formatNumber(summary.profit_factor, 2)}
          kind="profit"
          loading={summaryLoading}
          error={summaryError}
          unavailableReason={noTrades
            ? "Requires closed trades."
            : summary.win_count === 0 && summary.loss_count === 0
              ? "Requires at least one winning or losing trade."
              : undefined}
          className="md:col-span-2 lg:col-span-1"
        >
          {metricFootnote(summary.loss_count === 0 && summary.win_count > 0 ? "No losing trades" : "Gross wins ÷ gross losses")}
        </CompactMetricCard>
        <CompactMetricCard
          label="Win Rate"
          info="Winning closed trades as a percentage of all closed trades in this scope."
          value={formatPercent(summary.win_rate, 2)}
          kind="win"
          loading={summaryLoading}
          error={summaryError}
          unavailableReason={noTrades ? "Requires at least one closed trade." : undefined}
          className="md:col-span-3 lg:col-span-1"
        >
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
            <span className="rounded-full bg-app-positive-soft/25 px-2 py-1 text-app-positive-text">{summary.win_count} W</span>
            <span className="rounded-full bg-app-negative-soft/25 px-2 py-1 text-app-negative-text">{summary.loss_count} L</span>
            {summary.breakeven_count > 0 ? (
              <span className="rounded-full bg-app-surface-raised px-2 py-1 text-app-muted-text">{summary.breakeven_count} BE</span>
            ) : null}
          </div>
        </CompactMetricCard>
        <CompactMetricCard
          label="Average Payoff"
          info="Average winning trade divided by the absolute average losing trade."
          value={formatNumber(payoffRatio, 2)}
          kind="payoff"
          loading={summaryLoading}
          error={summaryError}
          unavailableReason={noTrades
            ? "Requires closed trades."
            : summary.win_count === 0
              ? "Average payoff requires at least one winning trade."
              : summary.loss_count === 0 || averageLossMagnitude === 0
                ? "Average payoff is unbounded without a losing trade."
              : undefined}
          unavailableLabel={summary.win_count === 0
            ? "No winning trades"
            : summary.loss_count === 0 || averageLossMagnitude === 0
              ? "No losing trades"
              : undefined}
          className="md:col-span-3 lg:col-span-1"
        >
          <div className="mt-2 min-w-0">
            <div className="flex h-1.5 overflow-hidden rounded-full bg-app-border/50" aria-hidden="true">
              <span className="bg-app-positive" style={{ width: `${winWidth}%` }} />
              <span className="bg-app-negative" style={{ width: `${lossWidth}%` }} />
            </div>
            <div className="mt-1.5 flex justify-between gap-2 text-xs">
              <span className="truncate text-app-positive-text">{formatCurrency(summary.avg_win)}</span>
              <span className="truncate text-app-negative-text">{formatCurrency(-averageLossMagnitude)}</span>
            </div>
          </div>
        </CompactMetricCard>
      </div>

      <div className="grid items-stretch gap-4 lg:grid-cols-5">
        <div className="min-w-0 lg:col-span-3">
          <CompactPerformanceChart points={chartPoints} loading={daysLoading} error={daysError} />
        </div>
        <div className="min-w-0 lg:col-span-2">
          <CompactScoreCard
            score={score}
            breakdown={scoreBreakdown}
            loading={daysLoading}
            error={daysError}
          />
        </div>
      </div>

      <div className="grid items-stretch gap-4 lg:grid-cols-5">
        <div className="h-full min-w-0 lg:col-span-2">
          <CompactRecentTrades
            trades={trades}
            loading={tradesLoading}
            error={tradesError}
            accountNameById={accountNameById}
          />
        </div>
        <div className="h-full min-w-0 lg:col-span-3">
          <CompactDashboardCalendar
            days={resolvedCalendarDays}
            rangeStartDate={rangeStartDate}
            rangeEndDate={rangeEndDate}
            loading={daysLoading}
            error={daysError}
            journalDays={journalDays}
            journalDaysLoading={journalDaysLoading}
            journalDaysError={journalDaysError}
            scopeKey={resolvedCalendarScopeKey}
            selectedDate={selectedDate}
            onDaySelect={onDaySelect}
            onJournalDayOpen={onJournalDayOpen}
            onVisibleRangeChange={onCalendarVisibleRangeChange}
          />
        </div>
      </div>
    </section>
  );
}

export type { CompactPerformanceContext, CompactScoreBreakdown };
