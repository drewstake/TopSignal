import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Select } from "../../components/ui/Select";
import {
  ACCOUNT_QUERY_PARAM,
  parseAccountId,
  readStoredAccountId,
  writeStoredAccountId,
} from "../../lib/accountSelection";
import { accountsApi } from "../../lib/api";
import type { AccountInfo, AccountPnlCalendarDay, AccountSummary, AccountTrade } from "../../lib/types";
import { PnlCalendarCard } from "./components/PnlCalendarCard";

const TRADE_LIMIT = 200;
const DAY_FILTER_TRADE_LIMIT = 1000;

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
  avg_win: 0,
  avg_loss: 0,
  max_drawdown: 0,
  trade_count: 0,
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
  const [accountsLoading, setAccountsLoading] = useState(true);
  const [accountsError, setAccountsError] = useState<string | null>(null);

  const [summary, setSummary] = useState<AccountSummary>(emptySummary);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const [selectedTradeDate, setSelectedTradeDate] = useState<string | null>(null);

  const [trades, setTrades] = useState<AccountTrade[]>([]);
  const [tradesLoading, setTradesLoading] = useState(false);
  const [tradesError, setTradesError] = useState<string | null>(null);

  const [pnlCalendarDays, setPnlCalendarDays] = useState<AccountPnlCalendarDay[]>([]);
  const [pnlCalendarLoading, setPnlCalendarLoading] = useState(false);
  const [pnlCalendarError, setPnlCalendarError] = useState<string | null>(null);

  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);

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
    setAccountsLoading(true);
    setAccountsError(null);

    try {
      const payload = await accountsApi.getAccounts();
      setAccounts(payload);
    } catch (err) {
      setAccountsError(err instanceof Error ? err.message : "Failed to load accounts");
      setAccounts([]);
    } finally {
      setAccountsLoading(false);
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
        accountsApi.getSummary(selectedAccountId),
        accountsApi.getPnlCalendar(selectedAccountId),
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
  }, [selectedAccountId]);

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

  async function handleSyncNow() {
    if (!selectedAccountId) {
      return;
    }

    setSyncing(true);
    setSyncMessage(null);

    try {
      const result = await accountsApi.refreshTrades(selectedAccountId);
      await reloadDashboard();
      setSyncMessage(`Fetched ${result.fetched_count}, stored ${result.inserted_count} new events.`);
    } catch (err) {
      setSyncMessage(err instanceof Error ? err.message : "Failed to sync account trades");
    } finally {
      setSyncing(false);
    }
  }

  const summaryCards = [
    { label: "Net PnL", value: formatPnl(summary.net_pnl), className: pnlClass(summary.net_pnl) },
    { label: "Win Rate", value: `${summary.win_rate.toFixed(2)}%`, className: "" },
    { label: "Avg Win", value: formatPnl(summary.avg_win), className: pnlClass(summary.avg_win) },
    { label: "Avg Loss", value: formatPnl(summary.avg_loss), className: pnlClass(summary.avg_loss) },
    { label: "Fees", value: currencyFormatter.format(summary.fees), className: "" },
    { label: "Trade Count", value: String(summary.trade_count), className: "" },
  ];

  return (
    <div className="space-y-6 pb-10">
      <Card>
        <CardHeader>
          <CardTitle>Active Account Dashboard</CardTitle>
          <CardDescription>
            Trades are pulled from ProjectX `Trade/search`, stored locally, and reflected in summary metrics.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <div className="xl:col-span-2">
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Account</label>
              <Select
                value={selectedAccount ? String(selectedAccount.id) : ""}
                onChange={(event) => {
                  const next = parseAccountId(event.target.value);
                  if (next) {
                    setActiveAccount(next);
                  }
                }}
                disabled={accountsLoading || accounts.length === 0}
              >
                {accountsLoading ? <option>Loading accounts...</option> : null}
                {!accountsLoading && accounts.length === 0 ? <option>No accounts</option> : null}
                {accounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name} ({account.id})
                  </option>
                ))}
              </Select>
              {accountsError ? <p className="mt-1 text-xs text-rose-300">{accountsError}</p> : null}
            </div>

            <div className="flex items-end">
              <Button
                className="w-full"
                variant="secondary"
                onClick={() => void reloadDashboard()}
                disabled={summaryLoading || tradesLoading || pnlCalendarLoading}
              >
                Reload
              </Button>
            </div>

            <div className="flex items-end">
              <Button className="w-full" onClick={handleSyncNow} disabled={syncing || !selectedAccount}>
                {syncing ? "Syncing..." : "Sync Latest Trades"}
              </Button>
            </div>
          </div>

          {syncMessage ? <p className="text-xs text-slate-400">{syncMessage}</p> : null}
        </CardContent>
      </Card>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
        {summaryLoading ? (
          Array.from({ length: 6 }).map((_, index) => (
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
                        <td className="px-3 py-3 text-left font-medium text-slate-100">{trade.symbol || trade.contract_id}</td>
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
