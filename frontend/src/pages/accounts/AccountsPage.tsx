import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import {
  ACCOUNT_QUERY_PARAM,
  parseAccountId,
  readStoredAccountId,
  writeStoredAccountId,
} from "../../lib/accountSelection";
import { accountsApi } from "../../lib/api";
import type { AccountInfo, AccountSummary, AccountTrade } from "../../lib/types";

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

export function AccountsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const accountFromQuery = parseAccountId(searchParams.get(ACCOUNT_QUERY_PARAM));

  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [accountsLoading, setAccountsLoading] = useState(true);
  const [accountsError, setAccountsError] = useState<string | null>(null);

  const [summary, setSummary] = useState<AccountSummary>(emptySummary);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const [recentTrades, setRecentTrades] = useState<AccountTrade[]>([]);
  const [tradesLoading, setTradesLoading] = useState(false);
  const [tradesError, setTradesError] = useState<string | null>(null);

  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);

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

  const loadAccountDetails = useCallback(async (accountId: number) => {
    setSummaryLoading(true);
    setTradesLoading(true);
    setSummaryError(null);
    setTradesError(null);

    const summaryPromise = accountsApi.getSummary(accountId);
    const tradesPromise = accountsApi.getTrades(accountId, { limit: 20 });

    try {
      const [nextSummary, nextTrades] = await Promise.all([summaryPromise, tradesPromise]);
      setSummary(nextSummary);
      setRecentTrades(nextTrades);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load account details";
      setSummaryError(message);
      setTradesError(message);
      setSummary(emptySummary);
      setRecentTrades([]);
    } finally {
      setSummaryLoading(false);
      setTradesLoading(false);
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

  useEffect(() => {
    if (!selectedAccount) {
      setSummary(emptySummary);
      setRecentTrades([]);
      setSummaryError(null);
      setTradesError(null);
      return;
    }

    void loadAccountDetails(selectedAccount.id);
  }, [selectedAccount, loadAccountDetails]);

  async function handleSyncNow() {
    if (!selectedAccount) {
      return;
    }

    setSyncing(true);
    setSyncMessage(null);

    try {
      const result = await accountsApi.refreshTrades(selectedAccount.id);
      await loadAccountDetails(selectedAccount.id);
      setSyncMessage(`Fetched ${result.fetched_count}, stored ${result.inserted_count} new events.`);
    } catch (err) {
      setSyncMessage(err instanceof Error ? err.message : "Failed to sync account trades");
    } finally {
      setSyncing(false);
    }
  }

  return (
    <div className="space-y-6 pb-10">
      <section className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>ProjectX Accounts</CardTitle>
            <CardDescription>Select an account to make it active for the Trades view.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-hidden rounded-xl border border-slate-800/80">
              <table className="w-full min-w-[520px] border-collapse text-sm">
                <thead className="bg-slate-900/70 text-xs uppercase tracking-wide text-slate-400">
                  <tr>
                    <th className="px-3 py-3 text-left font-medium">Name</th>
                    <th className="px-3 py-3 text-right font-medium">ID</th>
                    <th className="px-3 py-3 text-right font-medium">Balance</th>
                    <th className="px-3 py-3 text-right font-medium">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {accountsLoading ? (
                    Array.from({ length: 5 }).map((_, index) => (
                      <tr key={`accounts-loading-${index}`}>
                        <td colSpan={4} className="px-3 py-3">
                          <Skeleton className="h-6 w-full" />
                        </td>
                      </tr>
                    ))
                  ) : accountsError ? (
                    <tr>
                      <td colSpan={4} className="px-3 py-6 text-center text-rose-300">
                        {accountsError}
                      </td>
                    </tr>
                  ) : accounts.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="px-3 py-6 text-center text-slate-400">
                        No accounts found.
                      </td>
                    </tr>
                  ) : (
                    accounts.map((account) => {
                      const isActive = selectedAccount?.id === account.id;
                      return (
                        <tr
                          key={account.id}
                          className={`cursor-pointer transition ${
                            isActive ? "bg-cyan-500/10" : "hover:bg-slate-900/65"
                          }`}
                          onClick={() => setActiveAccount(account.id)}
                        >
                          <td className="px-3 py-3 text-left font-medium text-slate-100">{account.name}</td>
                          <td className="px-3 py-3 text-right text-slate-300">{account.id}</td>
                          <td className="px-3 py-3 text-right font-mono text-slate-200">
                            {currencyFormatter.format(account.balance)}
                          </td>
                          <td className="px-3 py-3 text-right">
                            <Badge variant={account.status.toUpperCase() === "ACTIVE" ? "positive" : "neutral"}>
                              {account.status}
                            </Badge>
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

        <Card>
          <CardHeader>
            <CardTitle>Active Account</CardTitle>
            <CardDescription>Quick health check from locally stored trade events.</CardDescription>
          </CardHeader>
          <CardContent>
            {!selectedAccount ? (
              <p className="text-sm text-slate-400">Select an account to load summary metrics.</p>
            ) : (
              <>
                <div className="rounded-xl border border-slate-800/80 bg-slate-900/45 p-3">
                  <p className="text-xs uppercase tracking-wide text-slate-500">Selected</p>
                  <p className="mt-1 text-sm font-semibold text-slate-100">{selectedAccount.name}</p>
                  <p className="text-xs text-slate-400">ID {selectedAccount.id}</p>
                  <p className="mt-2 text-sm font-mono text-slate-200">
                    {currencyFormatter.format(selectedAccount.balance)}
                  </p>
                </div>

                <div className="grid grid-cols-2 gap-2 text-xs">
                  {summaryLoading ? (
                    <>
                      <Skeleton className="h-14" />
                      <Skeleton className="h-14" />
                      <Skeleton className="h-14" />
                      <Skeleton className="h-14" />
                    </>
                  ) : summaryError ? (
                    <p className="col-span-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-rose-200">
                      {summaryError}
                    </p>
                  ) : (
                    <>
                      <StatBox label="Net PnL" value={formatPnl(summary.net_pnl)} valueClassName={pnlClass(summary.net_pnl)} />
                      <StatBox label="Win Rate" value={`${summary.win_rate.toFixed(2)}%`} />
                      <StatBox label="Avg Win" value={formatPnl(summary.avg_win)} valueClassName={pnlClass(summary.avg_win)} />
                      <StatBox label="Avg Loss" value={formatPnl(summary.avg_loss)} valueClassName={pnlClass(summary.avg_loss)} />
                    </>
                  )}
                </div>

                <div className="space-y-2">
                  <Button className="w-full" onClick={handleSyncNow} disabled={syncing}>
                    {syncing ? "Syncing..." : "Sync Latest Trades"}
                  </Button>
                  {syncMessage ? <p className="text-xs text-slate-400">{syncMessage}</p> : null}
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Recent Trade Events</CardTitle>
          <CardDescription>Most recent fills/trades stored for the active account.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-0">
          <div className="max-h-[420px] overflow-auto rounded-xl border border-slate-800/80">
            <table className="w-full min-w-[980px] border-collapse text-sm">
              <thead className="sticky top-0 z-10 bg-slate-900/95 text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="px-3 py-3 text-left font-medium">Timestamp (UTC)</th>
                  <th className="px-3 py-3 text-left font-medium">Symbol</th>
                  <th className="px-3 py-3 text-left font-medium">Side</th>
                  <th className="px-3 py-3 text-right font-medium">Size</th>
                  <th className="px-3 py-3 text-right font-medium">Price</th>
                  <th className="px-3 py-3 text-right font-medium">Fees</th>
                  <th className="px-3 py-3 text-right font-medium">PnL</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {tradesLoading ? (
                  Array.from({ length: 8 }).map((_, index) => (
                    <tr key={`trades-loading-${index}`}>
                      <td colSpan={7} className="px-3 py-3">
                        <Skeleton className="h-6 w-full" />
                      </td>
                    </tr>
                  ))
                ) : tradesError ? (
                  <tr>
                    <td colSpan={7} className="px-3 py-6 text-center text-rose-300">
                      {tradesError}
                    </td>
                  </tr>
                ) : recentTrades.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-3 py-6 text-center text-slate-400">
                      No trade events in local storage yet.
                    </td>
                  </tr>
                ) : (
                  recentTrades.map((trade) => {
                    const pnl = trade.pnl ?? 0;
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
                        <td className={`px-3 py-3 text-right font-semibold ${pnlClass(pnl)}`}>{formatPnl(pnl)}</td>
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

interface StatBoxProps {
  label: string;
  value: string;
  valueClassName?: string;
}

function StatBox({ label, value, valueClassName }: StatBoxProps) {
  return (
    <article className="rounded-xl border border-slate-800/80 bg-slate-900/45 p-3">
      <p className="text-[11px] uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-1 text-sm font-semibold text-slate-100 ${valueClassName ?? ""}`}>{value}</p>
    </article>
  );
}
