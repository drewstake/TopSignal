import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Badge } from "../../components/ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
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

export function AccountsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const accountFromQuery = parseAccountId(searchParams.get(ACCOUNT_QUERY_PARAM));

  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [accountsLoading, setAccountsLoading] = useState(true);
  const [accountsError, setAccountsError] = useState<string | null>(null);

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
      </section>
    </div>
  );
}
