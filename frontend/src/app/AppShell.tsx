import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { Button } from "../components/ui/Button";
import { Select } from "../components/ui/Select";
import { Skeleton } from "../components/ui/Skeleton";
import { Tabs } from "../components/ui/Tabs";
import { Toggle } from "../components/ui/Toggle";
import { cn } from "../components/ui/cn";
import {
  ACCOUNT_QUERY_PARAM,
  ACCOUNT_DISPLAY_NAME_UPDATED_EVENT,
  ACCOUNT_LIST_CHANGED_EVENT,
  MAIN_ACCOUNT_UPDATED_EVENT,
  buildAccountAwarePath,
  parseAccountId,
  readStoredAccountId,
  readStoredMainAccountId,
  writeStoredAccountId,
  type AccountListChangedDetail,
} from "../lib/accountSelection";
import {
  getSelectableAccounts,
  getSelectableAccountsLocalFirst,
  refreshTrades,
} from "../lib/appShellApi";
import { canApplyAccountScopedResult } from "../lib/appShellRequests";
import { sortAccountsForActiveSelection } from "../lib/accountOrdering";
import { formatProviderLastSeen } from "../lib/accountProviderState";
import { getDemoAccountLabel, getDemoUserEmail, useDemoMode } from "../lib/demoMode";
import { ACCOUNT_TRADES_SYNCED_EVENT, type AccountTradesSyncedDetail } from "../lib/tradeSyncEvents";
import type { AccountInfo } from "../lib/types";
import { getCurrentUserEmailSync, hasSupabaseConfig, signOutSupabase } from "../lib/supabase";
import { useLatestRequestGuard } from "../lib/latestRequest";

export interface AppShellOutletContext {
  accounts: AccountInfo[];
  accountsLoading: boolean;
  accountsError: string | null;
  selectedAccountId: number | null;
}

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
  const demoMode = useDemoMode();
  const beginAccountsRequest = useLatestRequestGuard();
  const beginProviderAccountsRequest = useLatestRequestGuard();
  const beginSyncRequest = useLatestRequestGuard();
  const activeSyncAccountIdRef = useRef<number | null>(null);
  const activeLifecycleAccountIdRef = useRef<number | null>(null);
  const locationRef = useRef(location);
  locationRef.current = location;

  useEffect(() => {
    let isMounted = true;

    async function loadAccounts() {
      const isCurrent = beginAccountsRequest();
      setAccountsLoading(true);
      setAccountsError(null);

      try {
        const payload = await getSelectableAccountsLocalFirst();
        if (!isMounted || !isCurrent()) {
          return;
        }
        setAccounts(payload);
      } catch (err) {
        if (!isMounted || !isCurrent()) {
          return;
        }
        setAccounts([]);
        setAccountsError(err instanceof Error ? err.message : "Failed to load accounts");
      } finally {
        if (isMounted && isCurrent()) {
          setAccountsLoading(false);
        }
      }
    }

    void loadAccounts();
    function handleMainAccountUpdated() {
      void loadAccounts();
    }
    function handleAccountDisplayNameUpdated() {
      void loadAccounts();
    }
    function handleAccountListChanged(event: Event) {
      const detail = (event as CustomEvent<AccountListChangedDetail>).detail;
      if (
        detail?.action === "archived" &&
        detail.accountId === activeLifecycleAccountIdRef.current &&
        detail.replacementAccountId !== null
      ) {
        writeStoredAccountId(detail.replacementAccountId);
        const currentLocation = locationRef.current;
        const next = new URLSearchParams(currentLocation.search);
        next.set(ACCOUNT_QUERY_PARAM, String(detail.replacementAccountId));
        navigate(
          {
            pathname: currentLocation.pathname,
            search: `?${next.toString()}`,
          },
          { replace: true },
        );
      }
      void loadAccounts();
    }
    if (typeof window !== "undefined") {
      window.addEventListener(MAIN_ACCOUNT_UPDATED_EVENT, handleMainAccountUpdated);
      window.addEventListener(ACCOUNT_DISPLAY_NAME_UPDATED_EVENT, handleAccountDisplayNameUpdated);
      window.addEventListener(ACCOUNT_LIST_CHANGED_EVENT, handleAccountListChanged);
    }
    return () => {
      isMounted = false;
      if (typeof window !== "undefined") {
        window.removeEventListener(MAIN_ACCOUNT_UPDATED_EVENT, handleMainAccountUpdated);
        window.removeEventListener(ACCOUNT_DISPLAY_NAME_UPDATED_EVENT, handleAccountDisplayNameUpdated);
        window.removeEventListener(ACCOUNT_LIST_CHANGED_EVENT, handleAccountListChanged);
      }
    };
  }, [beginAccountsRequest, navigate]);

  const queryAccountId = parseAccountId(new URLSearchParams(location.search).get(ACCOUNT_QUERY_PARAM));
  const orderedAccounts = useMemo(() => sortAccountsForActiveSelection(accounts), [accounts]);
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
  activeLifecycleAccountIdRef.current = selectedAccountId;
  const selectedAccount = orderedAccounts.find((account) => account.id === selectedAccountId) ?? null;
  const selectedAccountIsLocalOnly = selectedAccount?.trade_data_source === "csv_import";

  useEffect(() => {
    const isCurrent = beginProviderAccountsRequest();
    if (accountsLoading || selectedAccount?.trade_data_source !== "projectx") {
      return;
    }

    void getSelectableAccounts({ refreshProvider: true })
      .then((payload) => {
        if (isCurrent()) {
          setAccounts(payload);
        }
      })
      .catch(() => {
        // The saved account snapshot remains usable when ProjectX is unavailable.
      });
  }, [
    accountsLoading,
    beginProviderAccountsRequest,
    selectedAccount?.id,
    selectedAccount?.trade_data_source,
  ]);

  useEffect(() => {
    activeSyncAccountIdRef.current = selectedAccountId;
    beginSyncRequest();
    setSyncing(false);
    setSyncMessage(null);
  }, [beginSyncRequest, selectedAccountId]);

  const accountSuffix = selectedAccountId ? `?${ACCOUNT_QUERY_PARAM}=${selectedAccountId}` : "";
  const currentUserEmail = getCurrentUserEmailSync();
  const currentUserEmailDisplay = getDemoUserEmail(currentUserEmail);
  const isTradesRoute = location.pathname.startsWith("/trades");
  const outletContext = useMemo<AppShellOutletContext>(
    () => ({
      accounts: orderedAccounts,
      accountsLoading,
      accountsError,
      selectedAccountId,
    }),
    [accountsError, accountsLoading, orderedAccounts, selectedAccountId],
  );

  function handleAccountChange(rawValue: string) {
    const nextAccountId = parseAccountId(rawValue);
    if (!nextAccountId) {
      return;
    }

    setSyncMessage(null);
    activeSyncAccountIdRef.current = nextAccountId;
    beginSyncRequest();
    setSyncing(false);
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
    if (!selectedAccountId || selectedAccountIsLocalOnly) {
      return;
    }

    const requestedAccountId = selectedAccountId;
    const isCurrent = beginSyncRequest();
    setSyncing(true);
    setSyncMessage(null);

    try {
      const result = await refreshTrades(requestedAccountId);
      if (!canApplyAccountScopedResult(requestedAccountId, activeSyncAccountIdRef.current, isCurrent())) {
        return;
      }
      setSyncMessage(`Fetched ${result.fetched_count}, stored ${result.inserted_count} new events.`);
      window.dispatchEvent(
        new CustomEvent<AccountTradesSyncedDetail>(ACCOUNT_TRADES_SYNCED_EVENT, {
          detail: {
            accountId: requestedAccountId,
            fetchedCount: result.fetched_count,
            insertedCount: result.inserted_count,
          },
        }),
      );
    } catch (err) {
      if (!canApplyAccountScopedResult(requestedAccountId, activeSyncAccountIdRef.current, isCurrent())) {
        return;
      }
      const message = err instanceof Error ? err.message : "Failed to sync account trades";
      setSyncMessage(message);
      window.dispatchEvent(
        new CustomEvent<AccountTradesSyncedDetail>(ACCOUNT_TRADES_SYNCED_EVENT, {
          detail: {
            accountId: requestedAccountId,
            fetchedCount: 0,
            insertedCount: 0,
            error: message,
          },
        }),
      );
    } finally {
      if (canApplyAccountScopedResult(requestedAccountId, activeSyncAccountIdRef.current, isCurrent())) {
        setSyncing(false);
      }
    }
  }

  function handleDemoModeChange(enabled: boolean) {
    demoMode.setEnabled(enabled);
    if (typeof window !== "undefined") {
      window.location.reload();
    }
  }

  return (
    <div className="flex min-h-screen flex-col bg-app-bg text-app-text">
      <header className="relative z-30 border-b border-app-border/80 bg-app-bg/95 sm:sticky sm:top-0">
        <div className="mx-auto flex w-full max-w-[1400px] flex-col gap-2 px-3 py-2 sm:gap-4 sm:px-4 sm:py-4 lg:px-8">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex flex-wrap items-end gap-2 sm:gap-3 xl:flex-nowrap">
                <div className="w-full min-w-0 sm:w-[300px] sm:flex-none xl:w-[320px]">
                  <label
                    htmlFor="app-active-account"
                    className="mb-1 block text-[11px] uppercase tracking-wide text-app-muted-strong"
                  >
                    Active Account
                  </label>
                  <Select
                    id="app-active-account"
                    className="h-11 min-w-0 sm:h-9"
                    value={selectedAccountValue}
                    onChange={(event) => handleAccountChange(event.target.value)}
                    disabled={accountsLoading || orderedAccounts.length === 0}
                  >
                    {accountsLoading ? <option>Loading accounts...</option> : null}
                    {!accountsLoading && orderedAccounts.length === 0 ? <option>No accounts</option> : null}
                    {orderedAccounts.map((account) => (
                      <option key={account.id} value={account.id}>
                        {`${getDemoAccountLabel(account)}${
                          account.trade_data_source === "csv_import"
                            ? " — Live CSV"
                            : account.provider_data_stale
                              ? ` — stale provider data (${formatProviderLastSeen(account.last_seen_at)})`
                              : ""
                        }`}
                      </option>
                    ))}
                  </Select>
                </div>
                <Button
                  className="h-11 shrink-0 whitespace-nowrap sm:h-9"
                  onClick={handleSyncNow}
                  disabled={syncing || !selectedAccountId || selectedAccountIsLocalOnly}
                  title={selectedAccountIsLocalOnly ? "This account uses manually imported trade history." : undefined}
                >
                  {selectedAccountIsLocalOnly ? "Import-only Account" : syncing ? "Syncing..." : "Sync Latest Trades"}
                </Button>
                <Toggle
                  className="h-11 shrink-0 self-end sm:h-9"
                  checked={demoMode.enabled}
                  onChange={handleDemoModeChange}
                  label="Demo Mode"
                  aria-label="Demo mode"
                />
                {hasSupabaseConfig ? (
                  <div className="flex min-h-11 w-full min-w-0 max-w-full items-center gap-2 self-end rounded-lg border border-app-border bg-app-surface/60 px-2.5 text-xs text-app-muted sm:h-9 sm:min-h-0 sm:w-auto sm:max-w-[340px] xl:w-[260px] xl:flex-none">
                    <span className="min-w-0 flex-1 truncate" title={currentUserEmailDisplay}>
                      {currentUserEmailDisplay}
                    </span>
                    <Button
                      className="h-11 shrink-0 px-2 sm:h-7"
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
              {accountsError ? <p className="text-xs text-app-negative">{accountsError}</p> : null}
              {syncMessage ? <p className="text-xs text-app-muted">{syncMessage}</p> : null}
            </div>
            <div className="shrink-0 text-left sm:text-right">
              <p className="text-lg font-semibold tracking-tight text-app-text">TopSignal</p>
              <p className="text-xs text-app-muted">ProjectX Account + Trade Dashboard</p>
            </div>
          </div>

          <Tabs
            items={[
              { label: "Dashboard", to: buildAccountAwarePath("/", selectedAccountId) },
              { label: "Accounts", to: `/accounts${accountSuffix}` },
              { label: "Trades", to: `/trades${accountSuffix}` },
              { label: "Expenses", to: `/expenses${accountSuffix}` },
              { label: "Journal", to: `/journal${accountSuffix}` },
              { label: "Bot", to: `/bot${accountSuffix}` },
              { label: "Themes", to: "/themes" },
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
          <Outlet context={outletContext} />
        </Suspense>
      </main>
    </div>
  );
}
