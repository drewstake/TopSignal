import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Badge } from "../../components/ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import { Toggle } from "../../components/ui/Toggle";
import {
  ACCOUNT_QUERY_PARAM,
  parseAccountId,
  readStoredAccountId,
  writeStoredAccountId,
} from "../../lib/accountSelection";
import { accountsApi } from "../../lib/api";
import type { AccountInfo } from "../../lib/types";

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const lastTradeFormatter = new Intl.DateTimeFormat("en-US", {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "UTC",
});

function formatLastTrade(lastTradeAt: string | null) {
  if (!lastTradeAt) {
    return "No trades";
  }

  const parsed = new Date(lastTradeAt);
  if (Number.isNaN(parsed.getTime())) {
    return "Unknown";
  }

  return `${lastTradeFormatter.format(parsed)} UTC`;
}

export function AccountsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const accountFromQuery = parseAccountId(searchParams.get(ACCOUNT_QUERY_PARAM));

  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [accountsLoading, setAccountsLoading] = useState(true);
  const [accountsError, setAccountsError] = useState<string | null>(null);
  const [includeInactiveAccounts, setIncludeInactiveAccounts] = useState(false);
  const [lastTradeOverridesById, setLastTradeOverridesById] = useState<Record<number, string | null>>({});
  const [lastTradeLoadingById, setLastTradeLoadingById] = useState<Record<number, boolean>>({});
  const [lastTradeResolvedById, setLastTradeResolvedById] = useState<Record<number, boolean>>({});
  const [lastTradeError, setLastTradeError] = useState<string | null>(null);

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
    setLastTradeError(null);

    try {
      const payload = await accountsApi.getAccounts(!includeInactiveAccounts);
      setAccounts(payload);
      setLastTradeOverridesById({});
      setLastTradeLoadingById({});
      setLastTradeResolvedById({});
    } catch (err) {
      setAccountsError(err instanceof Error ? err.message : "Failed to load accounts");
      setAccounts([]);
    } finally {
      setAccountsLoading(false);
    }
  }, [includeInactiveAccounts]);

  const resolveLastTrade = useCallback(async (accountId: number, refresh = false) => {
    if (lastTradeLoadingById[accountId]) {
      return;
    }

    setLastTradeLoadingById((prev) => ({ ...prev, [accountId]: true }));
    setLastTradeError(null);
    try {
      const payload = await accountsApi.getLastTrade(accountId, refresh);
      setLastTradeOverridesById((prev) => ({ ...prev, [accountId]: payload.last_trade_at }));
    } catch (err) {
      setLastTradeError(err instanceof Error ? err.message : "Failed to resolve last trade timestamp");
    } finally {
      setLastTradeResolvedById((prev) => ({ ...prev, [accountId]: true }));
      setLastTradeLoadingById((prev) => ({ ...prev, [accountId]: false }));
    }
  }, [lastTradeLoadingById]);

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

  return (
    <div className="space-y-6 pb-10">
      <section>
        <Card>
          <CardHeader>
            <CardTitle>ProjectX Accounts</CardTitle>
            <CardDescription>Select an account to make it active for the Trades view.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center justify-end">
              <Toggle
                checked={includeInactiveAccounts}
                onChange={setIncludeInactiveAccounts}
                label="Show inactive accounts"
                aria-label="Show inactive accounts"
              />
            </div>
            {lastTradeError ? <p className="text-xs text-amber-300">{lastTradeError}</p> : null}
            <div className="overflow-hidden rounded-xl border border-slate-800/80">
              <table className="w-full min-w-[680px] border-collapse text-sm">
                <thead className="bg-slate-900/70 text-xs uppercase tracking-wide text-slate-400">
                  <tr>
                    <th className="px-3 py-3 text-left font-medium">Name</th>
                    <th className="px-3 py-3 text-right font-medium">ID</th>
                    <th className="px-3 py-3 text-right font-medium">Balance</th>
                    <th className="px-3 py-3 text-right font-medium">Last Trade</th>
                    <th className="px-3 py-3 text-right font-medium">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {accountsLoading ? (
                    Array.from({ length: 5 }).map((_, index) => (
                      <tr key={`accounts-loading-${index}`}>
                        <td colSpan={5} className="px-3 py-3">
                          <Skeleton className="h-6 w-full" />
                        </td>
                      </tr>
                    ))
                  ) : accountsError ? (
                    <tr>
                      <td colSpan={5} className="px-3 py-6 text-center text-rose-300">
                        {accountsError}
                      </td>
                    </tr>
                  ) : accounts.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="px-3 py-6 text-center text-slate-400">
                        No accounts found.
                      </td>
                    </tr>
                  ) : (
                    accounts.map((account) => {
                      const isActive = selectedAccount?.id === account.id;
                      const localLastTradeAt = account.last_trade_at;
                      const resolvedLastTradeAt =
                        lastTradeOverridesById[account.id] !== undefined
                          ? lastTradeOverridesById[account.id]
                          : localLastTradeAt;
                      const loadingLastTrade = Boolean(lastTradeLoadingById[account.id]);
                      const resolvedLastTrade = Boolean(lastTradeResolvedById[account.id]);
                      return (
                        <tr
                          key={account.id}
                          className={`cursor-pointer transition ${
                            isActive ? "bg-cyan-500/10" : "hover:bg-slate-900/65"
                          }`}
                          onClick={() => {
                            setActiveAccount(account.id);
                            if (!resolvedLastTradeAt && !resolvedLastTrade && !loadingLastTrade) {
                              void resolveLastTrade(account.id);
                            }
                          }}
                        >
                          <td className="px-3 py-3 text-left font-medium text-slate-100">{account.name}</td>
                          <td className="px-3 py-3 text-right text-slate-300">{account.id}</td>
                          <td className="px-3 py-3 text-right font-mono text-slate-200">
                            {currencyFormatter.format(account.balance)}
                          </td>
                          <td className="px-3 py-3 text-right text-slate-300">
                            {resolvedLastTradeAt ? (
                              formatLastTrade(resolvedLastTradeAt)
                            ) : loadingLastTrade ? (
                              "Checking..."
                            ) : resolvedLastTrade ? (
                              "No trades"
                            ) : (
                              <button
                                type="button"
                                className="text-xs text-cyan-300 underline decoration-cyan-400/60 underline-offset-2 hover:text-cyan-200"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  void resolveLastTrade(account.id, true);
                                }}
                              >
                                Lookup
                              </button>
                            )}
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
      </section>
    </div>
  );
}
