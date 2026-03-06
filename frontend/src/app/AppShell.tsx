import { Suspense, useEffect, useMemo, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { Button } from "../components/ui/Button";
import { Select } from "../components/ui/Select";
import { Skeleton } from "../components/ui/Skeleton";
import { Tabs } from "../components/ui/Tabs";
import { cn } from "../components/ui/cn";
import {
  ACCOUNT_QUERY_PARAM,
  MAIN_ACCOUNT_UPDATED_EVENT,
  parseAccountId,
  readStoredAccountId,
  readStoredMainAccountId,
  writeStoredAccountId,
} from "../lib/accountSelection";
import { getSelectableAccounts, refreshTrades } from "../lib/appShellApi";
import { sortAccountsForSelection } from "../lib/accountOrdering";
import { ACCOUNT_TRADES_SYNCED_EVENT, type AccountTradesSyncedDetail } from "../lib/tradeSyncEvents";
import type { AccountInfo } from "../lib/types";
import { getCurrentUserEmailSync, hasSupabaseConfig, signOutSupabase } from "../lib/supabase";

function AppShellRouteFallback() {
  return (
    <div className="space-y-5 pb-8">
      <div className="space-y-2">
        <Skeleton className="h-9 w-full max-w-[520px]" />
        <Skeleton className="h-4 w-56" />
      </div>
      <div className="grid gap-5 lg:grid-cols-3">
        <Skeleton className="h-56 w-full" />
        <Skeleton className="h-56 w-full lg:col-span-2" />
      </div>
      <Skeleton className="h-80 w-full" />
    </div>
  );
}

export function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [accountsLoading, setAccountsLoading] = useState(true);
  const [accountsError, setAccountsError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    async function loadAccounts() {
      setAccountsLoading(true);
      setAccountsError(null);

      try {
        const payload = await getSelectableAccounts();
        if (!isMounted) {
          return;
        }
        setAccounts(payload);
      } catch (err) {
        if (!isMounted) {
          return;
        }
        setAccounts([]);
        setAccountsError(err instanceof Error ? err.message : "Failed to load accounts");
      } finally {
        if (isMounted) {
          setAccountsLoading(false);
        }
      }
    }

    void loadAccounts();
    function handleMainAccountUpdated() {
      void loadAccounts();
    }
    if (typeof window !== "undefined") {
      window.addEventListener(MAIN_ACCOUNT_UPDATED_EVENT, handleMainAccountUpdated);
    }
    return () => {
      isMounted = false;
      if (typeof window !== "undefined") {
        window.removeEventListener(MAIN_ACCOUNT_UPDATED_EVENT, handleMainAccountUpdated);
      }
    };
  }, []);

  const queryAccountId = parseAccountId(new URLSearchParams(location.search).get(ACCOUNT_QUERY_PARAM));
  const orderedAccounts = useMemo(() => sortAccountsForSelection(accounts), [accounts]);
  const persistedMainAccountId = orderedAccounts.find((account) => account.is_main)?.id ?? null;
  const mainAccountId = readStoredMainAccountId();
  const storedActiveAccountId = readStoredAccountId();
  const selectedAccountValue = useMemo(() => {
    if (queryAccountId && orderedAccounts.some((account) => account.id === queryAccountId)) {
      return String(queryAccountId);
    }
    if (persistedMainAccountId && orderedAccounts.some((account) => account.id === persistedMainAccountId)) {
      return String(persistedMainAccountId);
    }
    if (mainAccountId && orderedAccounts.some((account) => account.id === mainAccountId)) {
      return String(mainAccountId);
    }
    if (storedActiveAccountId && orderedAccounts.some((account) => account.id === storedActiveAccountId)) {
      return String(storedActiveAccountId);
    }
    if (orderedAccounts.length > 0) {
      return String(orderedAccounts[0].id);
    }
    return "";
  }, [mainAccountId, orderedAccounts, persistedMainAccountId, queryAccountId, storedActiveAccountId]);
  const selectedAccountId = parseAccountId(selectedAccountValue);
  const accountSuffix = selectedAccountId ? `?${ACCOUNT_QUERY_PARAM}=${selectedAccountId}` : "";
  const currentUserEmail = getCurrentUserEmailSync();
  const isTradesRoute = location.pathname.startsWith("/trades");

  function handleAccountChange(rawValue: string) {
    const nextAccountId = parseAccountId(rawValue);
    if (!nextAccountId) {
      return;
    }

    setSyncMessage(null);
    writeStoredAccountId(nextAccountId);
    const next = new URLSearchParams(location.search);
    next.set(ACCOUNT_QUERY_PARAM, String(nextAccountId));
    navigate(
      {
        pathname: location.pathname,
        search: `?${next.toString()}`,
      },
      { replace: true },
    );
  }

  async function handleSyncNow() {
    if (!selectedAccountId) {
      return;
    }

    setSyncing(true);
    setSyncMessage(null);

    try {
      const result = await refreshTrades(selectedAccountId);
      setSyncMessage(`Fetched ${result.fetched_count}, stored ${result.inserted_count} new events.`);
      window.dispatchEvent(
        new CustomEvent<AccountTradesSyncedDetail>(ACCOUNT_TRADES_SYNCED_EVENT, {
          detail: {
            accountId: selectedAccountId,
            fetchedCount: result.fetched_count,
            insertedCount: result.inserted_count,
          },
        }),
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to sync account trades";
      setSyncMessage(message);
      window.dispatchEvent(
        new CustomEvent<AccountTradesSyncedDetail>(ACCOUNT_TRADES_SYNCED_EVENT, {
          detail: {
            accountId: selectedAccountId,
            fetchedCount: 0,
            insertedCount: 0,
            error: message,
          },
        }),
      );
    } finally {
      setSyncing(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-col bg-slate-950 text-slate-100">
      <header className="sticky top-0 z-30 border-b border-slate-800/80 bg-slate-950/95">
        <div className="mx-auto flex w-full max-w-[1400px] flex-col gap-4 px-4 py-4 lg:px-8">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-1">
              <div className="flex flex-wrap items-end gap-3">
                <div className="min-w-[220px]">
                  <label
                    htmlFor="app-active-account"
                    className="mb-1 block text-[11px] uppercase tracking-wide text-slate-500"
                  >
                    Active Account
                  </label>
                  <Select
                    id="app-active-account"
                    className="h-9 min-w-[220px]"
                    value={selectedAccountValue}
                    onChange={(event) => handleAccountChange(event.target.value)}
                    disabled={accountsLoading || orderedAccounts.length === 0}
                  >
                    {accountsLoading ? <option>Loading accounts...</option> : null}
                    {!accountsLoading && orderedAccounts.length === 0 ? <option>No accounts</option> : null}
                    {orderedAccounts.map((account) => (
                      <option key={account.id} value={account.id}>
                        {account.name} ({account.id})
                      </option>
                    ))}
                  </Select>
                </div>
                <Button className="h-9" onClick={handleSyncNow} disabled={syncing || !selectedAccountId}>
                  {syncing ? "Syncing..." : "Sync Latest Trades"}
                </Button>
              </div>
              {accountsError ? <p className="text-xs text-rose-300">{accountsError}</p> : null}
              {syncMessage ? <p className="text-xs text-slate-400">{syncMessage}</p> : null}
            </div>
            <div className="text-right">
              <p className="text-lg font-semibold tracking-tight text-slate-100">TopSignal</p>
              <p className="text-xs text-slate-400">ProjectX Account + Trade Dashboard</p>
              {hasSupabaseConfig ? (
                <div className="mt-2 flex items-center justify-end gap-2 text-xs text-slate-400">
                  <span>{currentUserEmail ?? "Signed in"}</span>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      void signOutSupabase();
                    }}
                  >
                    Sign out
                  </Button>
                </div>
              ) : null}
            </div>
          </div>

          <Tabs
            items={[
              { label: "Dashboard", to: "/" },
              { label: "Accounts", to: `/accounts${accountSuffix}` },
              { label: "Trades", to: `/trades${accountSuffix}` },
              { label: "Expenses", to: `/expenses${accountSuffix}` },
              { label: "Journal", to: `/journal${accountSuffix}` },
            ]}
          />
        </div>
      </header>
      <main
        className={cn(
          "mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 lg:px-8",
          isTradesRoute ? "lg:flex lg:min-h-0 lg:overflow-hidden" : "",
        )}
      >
        <Suspense fallback={<AppShellRouteFallback />}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  );
}
