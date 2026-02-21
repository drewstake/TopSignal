import { useEffect, useMemo, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { Button } from "../components/ui/Button";
import { Select } from "../components/ui/Select";
import { Tabs } from "../components/ui/Tabs";
import { ACCOUNT_QUERY_PARAM, parseAccountId, readStoredAccountId, writeStoredAccountId } from "../lib/accountSelection";
import { accountsApi } from "../lib/api";
import { ACCOUNT_TRADES_SYNCED_EVENT, type AccountTradesSyncedDetail } from "../lib/tradeSyncEvents";
import type { AccountInfo } from "../lib/types";

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
        const payload = await accountsApi.getAccounts();
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
    return () => {
      isMounted = false;
    };
  }, []);

  const queryAccountId = parseAccountId(new URLSearchParams(location.search).get(ACCOUNT_QUERY_PARAM));
  const activeAccountId = queryAccountId ?? readStoredAccountId();

  const accountSuffix = activeAccountId ? `?${ACCOUNT_QUERY_PARAM}=${activeAccountId}` : "";
  const selectedAccountValue = useMemo(() => {
    if (queryAccountId && accounts.some((account) => account.id === queryAccountId)) {
      return String(queryAccountId);
    }
    if (activeAccountId && accounts.some((account) => account.id === activeAccountId)) {
      return String(activeAccountId);
    }
    if (accounts.length > 0) {
      return String(accounts[0].id);
    }
    return "";
  }, [accounts, activeAccountId, queryAccountId]);
  const selectedAccountId = parseAccountId(selectedAccountValue);

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
      const result = await accountsApi.refreshTrades(selectedAccountId);
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
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="sticky top-0 z-30 border-b border-slate-800/80 bg-slate-950/95 backdrop-blur">
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
            </div>
          </div>

          <Tabs
            items={[
              { label: "Dashboard", to: "/" },
              { label: "Accounts", to: `/accounts${accountSuffix}` },
              { label: "Trades", to: `/trades${accountSuffix}` },
            ]}
          />
        </div>
      </header>
      <main className="mx-auto w-full max-w-[1400px] px-4 py-6 lg:px-8">
        <Outlet />
      </main>
    </div>
  );
}
