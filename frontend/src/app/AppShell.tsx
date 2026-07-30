import { Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
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
  captureLiveModeReturnSnapshot,
  clearLiveModeReturnSnapshot,
  parseAccountId,
  readLiveModeReturnSnapshot,
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
import {
  describeAccountProviderSync,
  describeProviderRefreshException,
  formatProviderLastSeen,
  summarizeAccountProviderSync,
  useAccountProviderFreshness,
} from "../lib/accountProviderState";
import { useCompactMode, type CompactModeController } from "../lib/compactMode";
import { getDemoAccountLabel, getDemoUserEmail, subscribeToDemoModeChanges, useDemoMode } from "../lib/demoMode";
import { DEMO_AS_OF_LABEL } from "../lib/demoScenario";
import { hasActiveLiveMutationRequests } from "../lib/liveMutationState";
import { ACCOUNT_TRADES_SYNCED_EVENT, type AccountTradesSyncedDetail } from "../lib/tradeSyncEvents";
import { requestTradeImportFilePicker } from "../lib/tradeImportEvents";
import type { AccountInfo } from "../lib/types";
import { getCurrentUserEmailSync, hasSupabaseConfig, signOutSupabase } from "../lib/supabase";
import { useLatestRequestGuard } from "../lib/latestRequest";
import { reloadPage, replacePagePath } from "./pageNavigation";

export interface AppShellOutletContext {
  accounts: AccountInfo[];
  accountsLoading: boolean;
  accountsError: string | null;
  reloadAccounts: () => void;
  selectedAccountId: number | null;
  compactMode: CompactModeController;
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
  const [providerAccountsRefreshing, setProviderAccountsRefreshing] = useState(false);
  const [providerRefreshError, setProviderRefreshError] = useState<string | null>(null);
  const [accountsReloadVersion, setAccountsReloadVersion] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [demoModeMessage, setDemoModeMessage] = useState<string | null>(null);
  const demoMode = useDemoMode();
  const compactMode = useCompactMode();
  const beginAccountsRequest = useLatestRequestGuard();
  const beginProviderAccountsRequest = useLatestRequestGuard();
  const beginSyncRequest = useLatestRequestGuard();
  const activeSyncAccountIdRef = useRef<number | null>(null);
  const activeLifecycleAccountIdRef = useRef<number | null>(null);
  const locationRef = useRef(location);
  locationRef.current = location;

  useEffect(() => {
    return subscribeToDemoModeChanges(({ enabled, source, blockedByLiveMutation }) => {
      if (source !== "storage") {
        return;
      }

      if (blockedByLiveMutation) {
        setDemoModeMessage("Finish the current live save, import, provider refresh, or backtest before entering Demo Mode.");
        return;
      }

      // Run synchronously inside the native storage event so React cannot
      // render live controls over stale Demo state (or the reverse).
      beginAccountsRequest();
      beginProviderAccountsRequest();
      beginSyncRequest();
      activeSyncAccountIdRef.current = null;
      activeLifecycleAccountIdRef.current = null;
      if (!enabled) {
        const returnSnapshot = readLiveModeReturnSnapshot();
        if (returnSnapshot) {
          replacePagePath(returnSnapshot.path);
        }
      }
      reloadPage();
    });
  }, [
    beginAccountsRequest,
    beginProviderAccountsRequest,
    beginSyncRequest,
  ]);

  useEffect(() => {
    let isMounted = true;

    async function loadAccounts() {
      const isCurrent = beginAccountsRequest();
      setAccountsLoading(true);
      setAccountsError(null);
      setProviderRefreshError(null);

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
  }, [accountsReloadVersion, beginAccountsRequest, navigate]);

  const queryAccountId = parseAccountId(new URLSearchParams(location.search).get(ACCOUNT_QUERY_PARAM));
  const freshnessAwareAccounts = useAccountProviderFreshness(accounts);
  const orderedAccounts = useMemo(
    () => sortAccountsForActiveSelection(freshnessAwareAccounts),
    [freshnessAwareAccounts],
  );
  const persistedMainAccountId = orderedAccounts.find((account) => account.is_main)?.id ?? null;
  const mainAccountId = readStoredMainAccountId();
  const storedActiveAccountId = readStoredAccountId();
  const selectedAccountValue = useMemo(() => {
    if (queryAccountId && orderedAccounts.some((account) => account.id === queryAccountId)) {
      return String(queryAccountId);
    }
    if (storedActiveAccountId && orderedAccounts.some((account) => account.id === storedActiveAccountId)) {
      return String(storedActiveAccountId);
    }
    if (persistedMainAccountId && orderedAccounts.some((account) => account.id === persistedMainAccountId)) {
      return String(persistedMainAccountId);
    }
    if (mainAccountId && orderedAccounts.some((account) => account.id === mainAccountId)) {
      return String(mainAccountId);
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

  useLayoutEffect(() => {
    if (demoMode.enabled) {
      return;
    }
    // localStorage is already flipped by the time another tab's storage event
    // arrives. Keep a tab-local live snapshot current ahead of that event so
    // every tab restores its own route and account after Demo Mode exits.
    captureLiveModeReturnSnapshot(location, selectedAccountId);
  }, [demoMode.enabled, location, selectedAccountId]);

  useEffect(() => {
    if (demoMode.enabled || accountsLoading || selectedAccount?.trade_data_source !== "projectx") {
      return;
    }

    const isCurrent = beginProviderAccountsRequest();
    setProviderAccountsRefreshing(true);
    setProviderRefreshError(null);

    void getSelectableAccounts({ refreshProvider: true })
      .then((payload) => {
        if (isCurrent()) {
          setAccounts(payload);
        }
      })
      .catch((error) => {
        if (isCurrent()) {
          setProviderRefreshError(describeProviderRefreshException(error));
        }
      })
      .finally(() => {
        if (isCurrent()) {
          setProviderAccountsRefreshing(false);
        }
      });
  }, [
    accountsLoading,
    beginProviderAccountsRequest,
    demoMode.enabled,
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
  const currentUserEmail = demoMode.enabled ? null : getCurrentUserEmailSync();
  const currentUserEmailDisplay = getDemoUserEmail(currentUserEmail);
  const sessionIdentityDisplay = demoMode.enabled
    ? "Live session remains signed in"
    : currentUserEmailDisplay;
  const isTradesRoute = location.pathname.startsWith("/trades");
  const isDashboardRoute = location.pathname === "/";
  const providerSyncSummary = useMemo(
    () => summarizeAccountProviderSync(orderedAccounts),
    [orderedAccounts],
  );
  const providerSyncNotice = useMemo(
    () => describeAccountProviderSync(providerSyncSummary),
    [providerSyncSummary],
  );
  const outletContext = useMemo<AppShellOutletContext>(
    () => ({
      accounts: orderedAccounts,
      accountsLoading,
      accountsError,
      reloadAccounts: () => setAccountsReloadVersion((version) => version + 1),
      selectedAccountId,
      compactMode: {
        enabled: compactMode.enabled,
        setEnabled: compactMode.setEnabled,
      },
    }),
    [accountsError, accountsLoading, compactMode.enabled, compactMode.setEnabled, orderedAccounts, selectedAccountId],
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
    if (demoMode.enabled || !selectedAccountId || selectedAccountIsLocalOnly) {
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

  function handleAccountAction() {
    if (demoMode.enabled || !selectedAccountId) {
      return;
    }
    if (selectedAccountIsLocalOnly) {
      if (location.pathname === "/") {
        requestTradeImportFilePicker();
      } else {
        navigate(buildAccountAwarePath("/", selectedAccountId));
      }
      return;
    }
    void handleSyncNow();
  }

  function handleDemoModeChange(enabled: boolean) {
    if (syncing || enabled === demoMode.enabled) {
      return;
    }
    if (enabled && hasActiveLiveMutationRequests()) {
      setDemoModeMessage("Finish the current live save, import, provider refresh, or backtest before entering Demo Mode.");
      return;
    }
    setDemoModeMessage(null);

    const returnSnapshot = enabled
      ? captureLiveModeReturnSnapshot(location, selectedAccountId)
      : readLiveModeReturnSnapshot();
    beginAccountsRequest();
    beginProviderAccountsRequest();
    beginSyncRequest();
    activeSyncAccountIdRef.current = null;
    activeLifecycleAccountIdRef.current = null;
    demoMode.setEnabled(enabled);
    if (typeof window !== "undefined") {
      if (!enabled && returnSnapshot) {
        replacePagePath(returnSnapshot.path);
      }
      reloadPage();
    }
  }

  async function handleSignOut() {
    if (
      demoMode.enabled &&
      typeof window !== "undefined" &&
      !window.confirm("This signs out your real TopSignal session. Demo data itself is not an account. Continue?")
    ) {
      return;
    }
    await signOutSupabase();
    clearLiveModeReturnSnapshot();
  }

  return (
    <div className="flex min-h-screen flex-col bg-app-bg text-app-text">
      <a
        href="#app-main-content"
        className="sr-only left-3 top-3 z-50 rounded-lg bg-app-surface px-4 py-2 text-sm font-semibold text-app-text shadow-lg focus:not-sr-only focus:fixed focus:outline-none focus:ring-2 focus:ring-app-accent"
      >
        Skip to main content
      </a>
      <header className="relative z-30 border-b border-app-border/80 bg-app-bg/95 sm:sticky sm:top-0">
        <div
          className={cn(
            "mx-auto flex w-full flex-col gap-2 px-3 py-2 sm:px-4 lg:px-8",
            isDashboardRoute && compactMode.enabled
              ? "max-w-[1920px] sm:gap-3 sm:py-3"
              : "max-w-[1400px] sm:gap-4 sm:py-4",
          )}
        >
          <div
            className={cn(
              "flex flex-col sm:flex-row sm:items-start sm:justify-between sm:gap-4",
              isDashboardRoute && compactMode.enabled ? "gap-2" : "gap-4",
            )}
          >
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
                            ? ""
                            : account.provider_sync_status === "cached_fallback"
                              ? ` — cached after refresh failure (${formatProviderLastSeen(account.last_seen_at)})`
                              : account.provider_data_stale
                                ? ` — aged saved data (${formatProviderLastSeen(account.last_seen_at)})`
                                : ""
                        }`}
                      </option>
                    ))}
                  </Select>
                </div>
                <Button
                  className="h-11 shrink-0 whitespace-nowrap sm:h-9"
                  onClick={handleAccountAction}
                  disabled={demoMode.enabled || syncing || !selectedAccountId}
                  title={
                    demoMode.enabled
                      ? "Live sync and imports are disabled while viewing demonstration data."
                      : selectedAccountIsLocalOnly
                        ? "Upload a CSV or Excel trade export."
                        : undefined
                  }
                >
                  {demoMode.enabled
                    ? "Demo Data — Read Only"
                    : selectedAccountIsLocalOnly
                      ? "Upload Trade File"
                      : syncing
                        ? "Syncing..."
                        : "Sync Latest Trades"}
                </Button>
                {isDashboardRoute ? (
                  <div className="flex flex-wrap items-center gap-2 self-end">
                    <Toggle
                      className="h-11 shrink-0 sm:h-9"
                      checked={demoMode.enabled}
                      onChange={handleDemoModeChange}
                      disabled={syncing}
                      label="Demo Mode"
                      aria-label="Demo mode"
                      title={syncing ? "Wait for the current live sync to finish before changing data modes." : undefined}
                    />
                    <Toggle
                      className="h-11 shrink-0 sm:h-9"
                      checked={compactMode.enabled}
                      onChange={compactMode.setEnabled}
                      label="Compact Dashboard"
                      aria-label="Compact Dashboard"
                      title="Use the compact layout on the Dashboard."
                    />
                  </div>
                ) : (
                  <Toggle
                    className="h-11 shrink-0 self-end sm:h-9"
                    checked={demoMode.enabled}
                    onChange={handleDemoModeChange}
                    disabled={syncing}
                    label="Demo Mode"
                    aria-label="Demo mode"
                    title={syncing ? "Wait for the current live sync to finish before changing data modes." : undefined}
                  />
                )}
                {hasSupabaseConfig ? (
                  <div className="flex min-h-11 w-full min-w-0 max-w-full items-center gap-2 self-end rounded-lg border border-app-border bg-app-surface/60 px-2.5 text-xs text-app-muted sm:h-9 sm:min-h-0 sm:w-auto sm:max-w-[340px] xl:w-[260px] xl:flex-none">
                    <span className="min-w-0 flex-1 truncate" title={sessionIdentityDisplay}>
                      {sessionIdentityDisplay}
                    </span>
                    <Button
                      className="h-11 shrink-0 px-2 sm:h-7"
                      size="sm"
                      variant="ghost"
                      onClick={() => void handleSignOut()}
                    >
                      {demoMode.enabled ? "Sign out live session" : "Sign out"}
                    </Button>
                  </div>
                ) : null}
              </div>
              {accountsError ? <p className="text-xs text-app-negative">{accountsError}</p> : null}
              {providerAccountsRefreshing ? (
                <p className="text-xs text-app-muted" role="status" aria-live="polite">
                  Refreshing ProjectX accounts… Saved accounts remain available while this finishes.
                </p>
              ) : providerRefreshError ? (
                <p className="text-xs text-app-negative" role="alert">
                  {providerRefreshError} Saved account data remains available.
                </p>
              ) : providerSyncNotice ? (
                <p
                  className={cn(
                    "text-xs",
                    providerSyncNotice.tone === "error"
                      ? "text-app-negative"
                      : providerSyncNotice.tone === "warning"
                        ? "text-app-warning"
                        : "text-app-muted",
                  )}
                  role={providerSyncNotice.tone === "error" ? "alert" : "status"}
                  aria-live="polite"
                >
                  {providerSyncNotice.message}
                </p>
              ) : null}
              {syncMessage ? <p className="text-xs text-app-muted">{syncMessage}</p> : null}
              {demoModeMessage ? (
                <p className="text-xs text-app-muted" role="status" aria-live="polite">
                  {demoModeMessage}
                </p>
              ) : null}
            </div>
            <div className="shrink-0 text-left sm:text-right">
              <p className="text-lg font-semibold tracking-tight text-app-text">TopSignal</p>
              <p
                className={cn(
                  "text-xs text-app-muted",
                  isDashboardRoute && compactMode.enabled ? "hidden sm:block" : undefined,
                )}
              >
                ProjectX Account + Trade Dashboard
              </p>
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
              { label: "Themes", to: `/themes${accountSuffix}` },
            ]}
          />
        </div>
      </header>
      <main
        id="app-main-content"
        tabIndex={-1}
        className={cn(
          "mx-auto w-full flex-1 px-4 lg:px-8",
          isDashboardRoute && compactMode.enabled ? "max-w-[1920px] pb-6 pt-2" : "max-w-[1400px] py-6",
          isTradesRoute ? "lg:flex lg:min-h-0 lg:flex-col lg:overflow-hidden" : "",
        )}
      >
        {demoMode.enabled ? (
          <div
            className="sticky top-0 z-20 -mx-4 mb-3 border-y border-app-accent/40 bg-app-bg/95 px-4 py-2 text-center text-xs font-semibold tracking-wide text-app-text shadow-sm backdrop-blur sm:static sm:mx-0 sm:rounded-lg sm:border"
            role="note"
            aria-label={`Demonstration data, read only, scenario as of ${DEMO_AS_OF_LABEL}`}
          >
            Demo data · read only · scenario as of {DEMO_AS_OF_LABEL}
          </div>
        ) : null}
        <Suspense fallback={<AppShellRouteFallback />}>
          <Outlet context={outletContext} />
        </Suspense>
      </main>
    </div>
  );
}
