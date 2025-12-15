import type { ReactNode } from "react";

import { fmtMoney } from "../../lib/format";
import { fmtDays, fmtDuration, fmtPF, fmtPct } from "../utils/format";
import type { DashboardComputed } from "../data/computeDashboard";

export type DaySummary = {
  activeDays: number;
  greenDays: number;
  redDays: number;
  flatDays: number;
  bestDay: { date: string; netPnl: number } | null;
  worstDay: { date: string; netPnl: number } | null;
};

interface SummaryCardsProps {
  totals: DashboardComputed["totals"] | undefined;
  daySummary: DaySummary;
  effectiveDaysBack: number;
}

export default function SummaryCards({ totals, daySummary, effectiveDaysBack }: SummaryCardsProps) {
  const sectionOne: MetricCardData[] = [
    {
      label: "Net PnL",
      description: "Net profit after fees for the selected date range (gross PnL minus commissions and fees).",
      value: fmtMoney(totals?.netPnl ?? 0),
      sub: `Range: last ${effectiveDaysBack} day(s)`,
    },
    {
      label: "Fees",
      description: "Total commissions/fees charged on executed trades within the range. Gross shows profit before fees.",
      value: fmtMoney(totals?.fees ?? 0),
      sub: `Gross: ${fmtMoney(totals?.grossPnl ?? 0)}`,
    },
    {
      label: "Win rate",
      description: "Percentage of realized trades closed in profit (winning trades divided by all realized trades).",
      value: fmtPct(totals?.winRate ?? 0),
      sub: `W ${totals?.wins ?? 0} / L ${totals?.losses ?? 0} / BE ${totals?.breakeven ?? 0}`,
    },
    {
      label: "Max drawdown",
      description: "Largest peak-to-trough decline of cumulative net equity across the date range, based on daily PnL.",
      value: fmtMoney(totals?.maxDrawdown ?? 0),
      sub: "From daily equity",
    },
  ];

  const sectionTwo: MetricCardData[] = [
    {
      label: "Profit factor",
      description: "Total gross profit from winning trades divided by the absolute gross loss from losing trades.",
      value: fmtPF(totals?.profitFactor ?? 0),
      sub: `Avg win ${fmtMoney(totals?.avgWin ?? 0)} | Avg loss ${fmtMoney(totals?.avgLoss ?? 0)}`,
    },
    {
      label: "Trades",
      description:
        "Count of realized trades (round trips) in the range. Half-turns and execution counts are listed below.",
      value: totals?.realizedTrades ?? 0,
      sub: `Half-turns ${totals?.halfTurns ?? 0} | Executions ${totals?.totalExecutions ?? 0}`,
    },
    {
      label: "Direction bias",
      description:
        "Mix of long versus short round-trip trades across the range, matching the Topstep direction bias calculation.",
      value: `${fmtPct(totals?.longPct ?? 0)} long trades`,
      sub: `Longs ${totals?.longTrades ?? 0} | Shorts ${totals?.shortTrades ?? 0} (${fmtPct(totals?.shortPct ?? 0)} short trades)`,
    },
    {
      label: "Day win rate",
      description: "Share of active trading days that ended positive. Calculated as green days divided by active days.",
      value: fmtPct(totals?.dayWinRate ?? 0),
      sub: `Green ${daySummary.greenDays} | Red ${daySummary.redDays} | Flat ${daySummary.flatDays}`,
    },
    {
      label: "Avg trades/day",
      description: "Average realized trades per active trading day (total realized trades divided by active days).",
      value: (totals?.avgTradesPerDay ?? 0).toFixed(2),
      sub: `Active days ${daySummary.activeDays}`,
    },
  ];

  const sectionThree: MetricCardData[] = [
    {
      label: "Expectancy / trade",
      description: "Expected PnL per trade using win rate × average win minus loss rate × average loss magnitude.",
      value: fmtMoney(totals?.expectancyPerTrade ?? 0),
      sub: `Tail risk (avg worst 5%): ${fmtMoney(totals?.tailRiskAvg ?? 0)}`,
    },
    {
      label: "Risk & drawdown",
      description: "Largest intraday drawdown from session peak, computed from realized executions during each day.",
      value: fmtMoney(totals?.maxIntradayDrawdown ?? 0),
      sub: `Avg DD ${fmtMoney(totals?.avgDrawdown ?? 0)} | Max length ${fmtDays(totals?.maxDrawdownLengthDays ?? 0)}`,
    },
    {
      label: "Recovery",
      description: "Average number of days it took to recover to a new equity high after entering a drawdown.",
      value: fmtDays(totals?.avgTimeToRecoveryDays ?? 0),
      sub: `Avg length ${fmtDays(totals?.avgDrawdownLengthDays ?? 0)}`,
    },
    {
      label: "Efficiency",
      description:
        "Net profit generated per hour spent in the market (sum of round-trip durations). Includes per-day average below.",
      value: `${fmtMoney(totals?.profitPerHour ?? 0)} / hr`,
      sub: `Per day ${fmtMoney(totals?.profitPerDay ?? 0)}`,
    },
  ];

  const sectionFour: MetricCardData[] = [
    {
      label: "Avg hold time",
      description: "Average time between entry and exit across all completed round-trip trades.",
      value: fmtDuration(totals?.avgTradeDurationMs ?? 0),
      sub: "All realized trades",
    },
    {
      label: "Avg winner hold",
      description: "Average holding period for profitable round-trip trades only.",
      value: fmtDuration(totals?.avgWinDurationMs ?? 0),
      sub: "Closed in profit",
    },
    {
      label: "Avg loser hold",
      description: "Average holding period for losing round-trip trades only.",
      value: fmtDuration(totals?.avgLossDurationMs ?? 0),
      sub: "Closed in loss",
    },
  ];

  return (
    <div className="space-y-3">
      <MetricSection metrics={sectionOne} columns="grid-cols-2 md:grid-cols-4" />
      <MetricSection metrics={sectionTwo} columns="grid-cols-2 md:grid-cols-5" />
      <MetricSection metrics={sectionThree} columns="grid-cols-2 md:grid-cols-4" />
      <MetricSection metrics={sectionFour} columns="grid-cols-1 md:grid-cols-3" />
    </div>
  );
}

type MetricCardData = {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  description: string;
};

function MetricSection({ metrics, columns }: { metrics: MetricCardData[]; columns: string }) {
  return (
    <div className={`grid gap-3 ${columns}`}>
      {metrics.map((metric) => (
        <MetricCard key={metric.label} {...metric} />
      ))}
    </div>
  );
}

function MetricCard({ label, value, sub, description }: MetricCardData) {
  return (
    <div className="rounded-2xl border border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950/30 p-4">
      <div className="flex items-start justify-between gap-2 text-xs text-zinc-600 dark:text-zinc-400">
        <span>{label}</span>
        <InfoTooltip label={label} description={description} />
      </div>
      <div className="mt-1 text-xl font-semibold text-zinc-900 dark:text-zinc-100">{value}</div>
      {sub ? <div className="mt-1 text-xs text-zinc-500">{sub}</div> : null}
    </div>
  );
}

function InfoTooltip({ label, description }: { label: string; description: ReactNode }) {
  return (
    <div className="group relative flex-shrink-0">
      <button
        type="button"
        aria-label={`${label} details`}
        className="flex h-5 w-5 items-center justify-center rounded-full border border-zinc-700 text-[10px] font-semibold text-zinc-800 dark:text-zinc-200 transition hover:border-zinc-500 hover:text-zinc-900 dark:text-zinc-100 focus:border-zinc-400 focus:text-zinc-900 dark:text-zinc-100 focus:outline-none"
      >
        i
      </button>
      <div className="absolute right-0 top-6 z-20 hidden w-64 rounded-xl bg-zinc-900/95 p-3 text-left text-xs text-zinc-900 dark:text-zinc-100 shadow-lg ring-1 ring-zinc-800 group-hover:block">
        {description}
      </div>
    </div>
  );
}
