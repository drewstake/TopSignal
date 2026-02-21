import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import {
  ACCOUNT_QUERY_PARAM,
  parseAccountId,
  readStoredAccountId,
  writeStoredAccountId,
} from "../../lib/accountSelection";
import { accountsApi } from "../../lib/api";
import type { AccountInfo, AccountSummary, AccountTrade } from "../../lib/types";

const PAGE_SIZE = 50;

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

function pnlClass(value: number) {
  return value >= 0 ? "text-emerald-300" : "text-rose-300";
}

function toStartIso(date: string) {
  return `${date}T00:00:00Z`;
}

function toEndIso(date: string) {
  return `${date}T23:59:59.999Z`;
}

export function TradesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const accountFromQuery = parseAccountId(searchParams.get(ACCOUNT_QUERY_PARAM));

  const [accounts, setAccounts] = useState<AccountInfo[]>([]);

  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [symbolQuery, setSymbolQuery] = useState("");
  const [limit, setLimit] = useState(200);

  const [summary, setSummary] = useState<AccountSummary>(emptySummary);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const [trades, setTrades] = useState<AccountTrade[]>([]);
  const [tradesLoading, setTradesLoading] = useState(false);
  const [tradesError, setTradesError] = useState<string | null>(null);

  const [page, setPage] = useState(1);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);

  const startIso = startDate ? toStartIso(startDate) : undefined;
  const endIso = endDate ? toEndIso(endDate) : undefined;

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

  const loadTradesAndSummary = useCallback(async () => {
    if (!selectedAccount) {
      setSummary(emptySummary);
      setTrades([]);
      setSummaryError(null);
      setTradesError(null);
      return;
    }

    setSummaryLoading(true);
    setTradesLoading(true);
    setSummaryError(null);
    setTradesError(null);

    const summaryPromise = accountsApi.getSummary(selectedAccount.id, {
      start: startIso,
      end: endIso,
    });
    const tradesPromise = accountsApi.getTrades(selectedAccount.id, {
      limit,
      start: startIso,
      end: endIso,
    });

    try {
      const [nextSummary, nextTrades] = await Promise.all([summaryPromise, tradesPromise]);
      setSummary(nextSummary);
      setTrades(nextTrades);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load trade data";
      setSummaryError(message);
      setTradesError(message);
      setSummary(emptySummary);
      setTrades([]);
    } finally {
      setSummaryLoading(false);
      setTradesLoading(false);
    }
  }, [selectedAccount, startIso, endIso, limit]);

  useEffect(() => {
    void loadTradesAndSummary();
  }, [loadTradesAndSummary]);

  useEffect(() => {
    setPage(1);
  }, [symbolQuery, selectedAccount, startDate, endDate, limit]);

  const filteredTrades = useMemo(() => {
    const normalizedQuery = symbolQuery.trim().toLowerCase();
    if (!normalizedQuery) {
      return trades;
    }

    return trades.filter((trade) => {
      const symbol = (trade.symbol || trade.contract_id).toLowerCase();
      return symbol.includes(normalizedQuery);
    });
  }, [symbolQuery, trades]);

  const totalPages = Math.max(1, Math.ceil(filteredTrades.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);

  const pagedTrades = useMemo(() => {
    const startIndex = (currentPage - 1) * PAGE_SIZE;
    return filteredTrades.slice(startIndex, startIndex + PAGE_SIZE);
  }, [filteredTrades, currentPage]);

  async function handleSyncNow() {
    if (!selectedAccount) {
      return;
    }

    setSyncing(true);
    setSyncMessage(null);

    try {
      const result = await accountsApi.refreshTrades(selectedAccount.id, {
        start: startIso,
        end: endIso,
      });
      await loadTradesAndSummary();
      setSyncMessage(`Fetched ${result.fetched_count}, stored ${result.inserted_count} new events.`);
    } catch (err) {
      setSyncMessage(err instanceof Error ? err.message : "Failed to sync trades");
    } finally {
      setSyncing(false);
    }
  }

  return (
    <div className="space-y-6 pb-10">
      <Card>
        <CardHeader>
          <CardTitle>Account Trades</CardTitle>
          <CardDescription>Filter recent trade events and inspect account-level summary metrics.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Start</label>
              <Input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
            </div>

            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">End</label>
              <Input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
            </div>

            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Symbol Search</label>
              <Input
                value={symbolQuery}
                onChange={(event) => setSymbolQuery(event.target.value)}
                placeholder="NQ, ES, CL..."
              />
            </div>

            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Limit</label>
              <Select value={String(limit)} onChange={(event) => setLimit(Number(event.target.value))}>
                <option value={100}>100</option>
                <option value={200}>200</option>
                <option value={500}>500</option>
                <option value={1000}>1000</option>
              </Select>
            </div>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button onClick={handleSyncNow} disabled={syncing || !selectedAccount}>
              {syncing ? "Syncing..." : "Sync Latest"}
            </Button>
            {syncMessage ? <p className="text-xs text-slate-400">{syncMessage}</p> : null}
          </div>
        </CardContent>
      </Card>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
        {summaryLoading ? (
          Array.from({ length: 6 }).map((_, index) => <Skeleton key={`summary-skeleton-${index}`} className="h-24" />)
        ) : summaryError ? (
          <Card className="sm:col-span-2 xl:col-span-6">
            <p className="text-sm text-rose-300">{summaryError}</p>
          </Card>
        ) : (
          <>
            <MetricCard label="Net PnL" value={formatPnl(summary.net_pnl)} valueClassName={pnlClass(summary.net_pnl)} />
            <MetricCard label="Win Rate" value={`${summary.win_rate.toFixed(2)}%`} />
            <MetricCard label="Avg Win" value={formatPnl(summary.avg_win)} valueClassName={pnlClass(summary.avg_win)} />
            <MetricCard label="Avg Loss" value={formatPnl(summary.avg_loss)} valueClassName={pnlClass(summary.avg_loss)} />
            <MetricCard label="Fees" value={currencyFormatter.format(summary.fees)} />
            <MetricCard label="Trade Count" value={String(summary.trade_count)} />
          </>
        )}
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Trade Events</CardTitle>
          <CardDescription>
            Showing {filteredTrades.length} matching events{symbolQuery ? ` for "${symbolQuery}"` : ""}.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-0">
          <div className="max-h-[560px] overflow-auto rounded-xl border border-slate-800/80">
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
                  Array.from({ length: 12 }).map((_, index) => (
                    <tr key={`trades-skeleton-${index}`}>
                      <td colSpan={8} className="px-3 py-3">
                        <Skeleton className="h-6 w-full" />
                      </td>
                    </tr>
                  ))
                ) : tradesError ? (
                  <tr>
                    <td colSpan={8} className="px-3 py-6 text-center text-rose-300">
                      {tradesError}
                    </td>
                  </tr>
                ) : pagedTrades.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-3 py-6 text-center text-slate-400">
                      No trades match your filters.
                    </td>
                  </tr>
                ) : (
                  pagedTrades.map((trade) => {
                    const pnlValue = trade.pnl ?? 0;
                    return (
                      <tr key={trade.id} className="transition hover:bg-slate-900/65">
                        <td className="px-3 py-3 text-left text-slate-300">
                          {timestampFormatter.format(new Date(trade.timestamp))}
                        </td>
                        <td className="px-3 py-3 text-left font-medium text-slate-100">{trade.symbol || trade.contract_id}</td>
                        <td className="px-3 py-3 text-left">
                          <Badge variant={trade.side === "BUY" ? "accent" : "warning"}>{trade.side}</Badge>
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

          <div className="mt-3 flex items-center justify-between text-xs text-slate-400">
            <p>
              Page {currentPage} of {totalPages}
            </p>
            <div className="flex items-center gap-2">
              <Button variant="ghost" size="sm" onClick={() => setPage((current) => Math.max(1, current - 1))}>
                Prev
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
              >
                Next
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

interface MetricCardProps {
  label: string;
  value: string;
  valueClassName?: string;
}

function MetricCard({ label, value, valueClassName }: MetricCardProps) {
  return (
    <Card className="p-4">
      <p className="text-xs uppercase tracking-[0.16em] text-slate-500">{label}</p>
      <p className={`mt-2 text-2xl font-semibold text-slate-100 ${valueClassName ?? ""}`}>{value}</p>
    </Card>
  );
}
