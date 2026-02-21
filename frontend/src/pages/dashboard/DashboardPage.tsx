import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
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
import { PnlCalendarCard } from "./components/PnlCalendarCard";

const TRADE_LIMIT = 200;
const DAY_FILTER_TRADE_LIMIT = 1000;
type MetricsRangePreset = "1D" | "1W" | "1M" | "6M" | "ALL";

const METRICS_RANGE_OPTIONS: Array<{ key: MetricsRangePreset; label: string }> = [
  { key: "1D", label: "1D" },
  { key: "1W", label: "1W" },
  { key: "1M", label: "1M" },
  { key: "6M", label: "6M" },
  { key: "ALL", label: "All" },
];

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const pnlFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

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

function formatPnl(value: number) {
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${pnlFormatter.format(value)}`;
}

function formatFee(value: number) {
  return currencyFormatter.format(-Math.abs(value));
}

function formatPercent(value: number) {
  return `${value.toFixed(2)}%`;
}

function formatHours(value: number) {
  return `${value.toFixed(2)}h`;
}

function formatNumber(value: number) {
  return value.toFixed(2);
}

function formatMinutes(value: number) {
  const safeMinutes = Number.isFinite(value) ? Math.max(0, value) : 0;
  const totalSeconds = Math.round(safeMinutes * 60);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes} min ${seconds} sec`;
}

function pnlClass(value: number) {
  return value >= 0 ? "text-emerald-300" : "text-rose-300";
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

function buildMetricsRangeQuery(range: MetricsRangePreset): { start?: string; end?: string; allTime: boolean } {
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
      start = subtractUtcMonths(end, 1);
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

  const [selectedTradeDate, setSelectedTradeDate] = useState<string | null>(null);

  const [trades, setTrades] = useState<AccountTrade[]>([]);
  const [tradesLoading, setTradesLoading] = useState(false);
  const [tradesError, setTradesError] = useState<string | null>(null);

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
  const metricsRangeQuery = useMemo(() => buildMetricsRangeQuery(metricsRange), [metricsRange]);

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

  const reloadDashboard = useCallback(async () => {
    await Promise.all([loadSummaryAndCalendar(), loadTrades()]);
  }, [loadSummaryAndCalendar, loadTrades]);

  useEffect(() => {
    void loadSummaryAndCalendar();
  }, [loadSummaryAndCalendar]);

  useEffect(() => {
    void loadTrades();
  }, [loadTrades]);

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

  const summaryCards: Array<{ label: string; value: string; className?: string; detail?: string }> = [
    { label: "Net PnL", value: formatPnl(summary.net_pnl), className: pnlClass(summary.net_pnl) },
    { label: "Fees", value: currencyFormatter.format(summary.fees) },
    { label: "Gross", value: formatPnl(summary.gross_pnl), className: pnlClass(summary.gross_pnl) },
    {
      label: "Win Rate",
      value: formatPercent(summary.win_rate),
      detail: `${summary.win_count}W / ${summary.loss_count}L / ${summary.breakeven_count} BE`,
    },
    { label: "Max Drawdown", value: formatPnl(summary.max_drawdown), className: pnlClass(summary.max_drawdown) },
    { label: "Profit Factor", value: formatNumber(summary.profit_factor) },
    { label: "Average Win", value: formatPnl(summary.avg_win), className: pnlClass(summary.avg_win) },
    { label: "Average Loss", value: formatPnl(summary.avg_loss), className: pnlClass(summary.avg_loss) },
    { label: "Avg Win Duration", value: formatMinutes(summary.avg_win_duration_minutes) },
    { label: "Avg Loss Duration", value: formatMinutes(summary.avg_loss_duration_minutes) },
    { label: "Trades", value: String(summary.trade_count) },
    { label: "Half-turns", value: String(summary.half_turn_count) },
    { label: "Executions", value: String(summary.execution_count) },
    {
      label: "Day Win Rate",
      value: formatPercent(summary.day_win_rate),
      detail: `${summary.green_days} green / ${summary.red_days} red / ${summary.flat_days} flat`,
    },
    { label: "Avg Trades / Day", value: formatNumber(summary.avg_trades_per_day) },
    { label: "Active Days", value: String(summary.active_days) },
    { label: "Expectancy / Trade", value: formatPnl(summary.expectancy_per_trade), className: pnlClass(summary.expectancy_per_trade) },
    { label: "Tail Risk (Worst 5%)", value: formatPnl(summary.tail_risk_5pct), className: pnlClass(summary.tail_risk_5pct) },
    { label: "Risk & Drawdown", value: formatPercent(summary.risk_drawdown_score) },
    { label: "Average Drawdown", value: formatPnl(summary.average_drawdown), className: pnlClass(summary.average_drawdown) },
    { label: "Max Drawdown Length", value: formatHours(summary.max_drawdown_length_hours) },
    { label: "Recovery Time", value: formatHours(summary.recovery_time_hours) },
    { label: "Average Recovery", value: formatHours(summary.average_recovery_length_hours) },
    { label: "Efficiency / Hour", value: formatPnl(summary.efficiency_per_hour), className: pnlClass(summary.efficiency_per_hour) },
    { label: "Profit / Day", value: formatPnl(summary.profit_per_day), className: pnlClass(summary.profit_per_day) },
  ];

  return (
    <div className="space-y-6 pb-10">
      <Card>
        <CardHeader className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>Metrics Window</CardTitle>
            <CardDescription>Select the dashboard range for summary and calendar metrics.</CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
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
        </CardHeader>
      </Card>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {summaryLoading ? (
          Array.from({ length: summaryCards.length }).map((_, index) => (
            <Card key={`summary-loading-${index}`} className="p-4">
              <p className="text-xs uppercase tracking-[0.16em] text-slate-500">Loading...</p>
              <p className="mt-2 text-2xl font-semibold text-slate-100">...</p>
            </Card>
          ))
        ) : summaryError ? (
          <Card className="sm:col-span-2 xl:col-span-6 p-4">
            <p className="text-sm text-rose-300">{summaryError}</p>
          </Card>
        ) : (
          summaryCards.map((metric) => (
            <Card key={metric.label} className="p-4">
              <p className="text-xs uppercase tracking-[0.16em] text-slate-500">{metric.label}</p>
              <p className={`mt-2 text-2xl font-semibold text-slate-100 ${metric.className}`}>{metric.value}</p>
              {metric.detail ? <p className="mt-1 text-xs text-slate-400">{metric.detail}</p> : null}
            </Card>
          ))
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
