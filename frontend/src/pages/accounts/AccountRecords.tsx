import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { DemoModeNotice } from "../../components/demo/DemoModeNotice";
import { useDemoInteractionPolicy } from "../../components/demo/useDemoInteractionPolicy";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import { Toggle } from "../../components/ui/Toggle";
import {
  ACCOUNT_QUERY_PARAM,
  dispatchAccountListChanged,
  parseAccountId,
  writeStoredAccountId,
} from "../../lib/accountSelection";
import { logPerfInfo } from "../../lib/perf";
import { useLatestRequestGuard } from "../../lib/latestRequest";
import { accountsApi } from "../../lib/api";
import { sortAccountsForActiveSelection, sortAccountsForSelection } from "../../lib/accountOrdering";
import {
  describeAccountProviderSync,
  describeProviderRefreshException,
  formatAccountBalance,
  formatProviderLastSeen,
  getAvailableAccountBalance,
  summarizeAccountProviderSync,
  useAccountProviderFreshness,
} from "../../lib/accountProviderState";
import { getDemoAccountId, getDemoAccountName } from "../../lib/demoMode";
import type { AccountInfo } from "../../lib/types";
import {
  AccountSelectionButton,
  AccountTableScrollArea,
} from "./accountManagement";
import { filterAccountManagementRows, loadAccountManagementRows } from "./accountManagementData";
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

function formatAccountStateLabel(state: AccountInfo["account_state"]) {
  if (state === "ACTIVE") {
    return "Active";
  }
  if (state === "LOCKED_OUT") {
    return "Locked out";
  }
  if (state === "HIDDEN") {
    return "Hidden";
  }
  return "Missing (possible blown/closed)";
}

function accountStateBadgeVariant(state: AccountInfo["account_state"]) {
  if (state === "ACTIVE") {
    return "positive" as const;
  }
  if (state === "LOCKED_OUT" || state === "HIDDEN") {
    return "warning" as const;
  }
  return "negative" as const;
}

export function AccountRecords() {
  const { demoModeEnabled, demoDisabledTitle } = useDemoInteractionPolicy();
  const [searchParams, setSearchParams] = useSearchParams();
  const accountFromQuery = parseAccountId(searchParams.get(ACCOUNT_QUERY_PARAM));

  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [accountsLoading, setAccountsLoading] = useState(true);
  const [refreshingExpressAccounts, setRefreshingExpressAccounts] = useState(false);
  const [accountsError, setAccountsError] = useState<string | null>(null);
  const [showHiddenAccounts, setShowHiddenAccounts] = useState(false);
  const [showMissingAccounts, setShowMissingAccounts] = useState(false);
  const [showArchivedAccounts, setShowArchivedAccounts] = useState(false);
  const [lifecycleAccountId, setLifecycleAccountId] = useState<number | null>(null);
  const [lastTradeOverridesById, setLastTradeOverridesById] = useState<Record<number, string | null>>({});
  const [lastTradeLoadingById, setLastTradeLoadingById] = useState<Record<number, boolean>>({});
  const [lastTradeResolvedById, setLastTradeResolvedById] = useState<Record<number, boolean>>({});
  const [lastTradeError, setLastTradeError] = useState<string | null>(null);
  const accountsVersionRef = useRef(0);
  const lastTradeRequestVersionByIdRef = useRef<Record<number, number>>({});
  const lastTradeInFlightByIdRef = useRef<Record<number, number>>({});
  const refreshExpressRequestRef = useRef(false);
  const beginAccountsRequest = useLatestRequestGuard();

  const setActiveAccount = useCallback(
    (accountId: number) => {
      const next = new URLSearchParams(searchParams);
      next.set(ACCOUNT_QUERY_PARAM, String(accountId));
      setSearchParams(next, { replace: true });
      writeStoredAccountId(accountId);
    },
    [searchParams, setSearchParams],
  );

  const loadAccounts = useCallback(async ({
    refreshProvider = false,
    preserveRowsOnError = false,
  }: {
    refreshProvider?: boolean;
    preserveRowsOnError?: boolean;
  } = {}) => {
    const isCurrent = beginAccountsRequest();
    accountsVersionRef.current += 1;
    setLastTradeLoadingById({});
    const startedAtIso = new Date().toISOString();
    const startedAtMs = performance.now();
    logPerfInfo("[perf][accounts] load-start", {
      started_at: startedAtIso,
      refresh_provider: refreshProvider,
    });
    if (!refreshProvider) {
      setAccountsLoading(true);
    }
    setAccountsError(null);
    setLastTradeError(null);

    try {
      const savedAccounts = await loadAccountManagementRows(
        accountsApi.getAccounts,
        refreshProvider,
      );
      if (!isCurrent()) {
        return null;
      }
      setAccounts(savedAccounts);
      setLastTradeOverridesById({});
      setLastTradeLoadingById({});
      setLastTradeResolvedById({});
      return savedAccounts;
    } catch (err) {
      if (!isCurrent()) {
        return null;
      }
      const message = err instanceof Error ? err.message : "Failed to load accounts";
      setAccountsError(
        preserveRowsOnError
          ? `${describeProviderRefreshException(err)} Saved account data is still available.`
          : message,
      );
      if (!preserveRowsOnError) {
        setAccounts([]);
        setLastTradeOverridesById({});
        setLastTradeLoadingById({});
        setLastTradeResolvedById({});
      }
      return null;
    } finally {
      const totalMs = Math.max(performance.now() - startedAtMs, 0);
      logPerfInfo("[perf][accounts] load-end", {
        started_at: startedAtIso,
        finished_at: new Date().toISOString(),
        total_ms: Number(totalMs.toFixed(2)),
        refresh_provider: refreshProvider,
      });
      if (isCurrent()) {
        if (!refreshProvider) {
          setAccountsLoading(false);
        }
      }
    }
  }, [beginAccountsRequest]);

  const refreshExpressAccounts = useCallback(async () => {
    if (refreshExpressRequestRef.current) {
      return;
    }
    refreshExpressRequestRef.current = true;
    setRefreshingExpressAccounts(true);
    try {
      const refreshedAccounts = await loadAccounts({
        refreshProvider: true,
        preserveRowsOnError: true,
      });
      const providerSummary = refreshedAccounts
        ? summarizeAccountProviderSync(refreshedAccounts)
        : null;
      // Discovery must also update the shell when it has no selected account,
      // or when the previously selected ProjectX account is no longer returned.
      if (refreshedAccounts && providerSummary?.status !== "cached_fallback") {
        dispatchAccountListChanged({
          accountId: null,
          action: "provider_refreshed",
          replacementAccountId: null,
        });
      }
    } finally {
      refreshExpressRequestRef.current = false;
      setRefreshingExpressAccounts(false);
    }
  }, [loadAccounts]);

  const freshnessAwareAccounts = useAccountProviderFreshness(accounts);
  const providerSyncNotice = useMemo(
    () => describeAccountProviderSync(summarizeAccountProviderSync(freshnessAwareAccounts)),
    [freshnessAwareAccounts],
  );

  const resolveLastTrade = useCallback(async (accountId: number, refresh = false) => {
    const accountsVersion = accountsVersionRef.current;
    if (lastTradeInFlightByIdRef.current[accountId] === accountsVersion) {
      return;
    }

    lastTradeInFlightByIdRef.current[accountId] = accountsVersion;
    const requestVersion = (lastTradeRequestVersionByIdRef.current[accountId] ?? 0) + 1;
    lastTradeRequestVersionByIdRef.current[accountId] = requestVersion;
    const isCurrent = () =>
      accountsVersionRef.current === accountsVersion &&
      lastTradeRequestVersionByIdRef.current[accountId] === requestVersion;

    setLastTradeLoadingById((prev) => ({ ...prev, [accountId]: true }));
    setLastTradeError(null);
    try {
      const payload = await accountsApi.getLastTrade(accountId, refresh);
      if (isCurrent()) {
        setLastTradeOverridesById((prev) => ({ ...prev, [accountId]: payload.last_trade_at }));
      }
    } catch (err) {
      if (isCurrent()) {
        setLastTradeError(err instanceof Error ? err.message : "Failed to resolve last trade timestamp");
      }
    } finally {
      if (lastTradeInFlightByIdRef.current[accountId] === accountsVersion) {
        delete lastTradeInFlightByIdRef.current[accountId];
      }
      if (isCurrent()) {
        setLastTradeResolvedById((prev) => ({ ...prev, [accountId]: true }));
        setLastTradeLoadingById((prev) => ({ ...prev, [accountId]: false }));
      }
    }
  }, []);

  const changeLiveAccountArchiveState = useCallback(
    async (account: AccountInfo) => {
      if (account.trade_data_source !== "csv_import" || lifecycleAccountId !== null) {
        return;
      }

      const replacementCandidates = sortAccountsForActiveSelection(
        accounts.filter(
          (candidate) =>
            candidate.id !== account.id &&
            !candidate.is_archived &&
            (candidate.trade_data_source === "csv_import" ||
              candidate.account_state === "ACTIVE" ||
              candidate.account_state === "LOCKED_OUT"),
        ),
      );
      const replacementAccountId = account.is_main
        ? replacementCandidates[0]?.id ?? null
        : null;
      const activeReplacement = accountFromQuery === account.id
        ? replacementCandidates[0] ?? null
        : null;

      if (!account.is_archived) {
        if (account.is_main && replacementAccountId === null) {
          setAccountsError("Choose or restore another account before archiving the only Main account.");
          return;
        }
        if (accountFromQuery === account.id && activeReplacement === null) {
          setAccountsError("Choose or restore another account before archiving the only active account.");
          return;
        }
        const mainReplacement = replacementCandidates.find(
          (candidate) => candidate.id === replacementAccountId,
        ) ?? null;
        const mainReplacementMessage = mainReplacement
          ? ` ${getDemoAccountName(mainReplacement)} will become Main.`
          : "";
        const activeReplacementMessage = activeReplacement
          ? activeReplacement.trade_data_source === "projectx"
            ? ` This will switch the active account to Express account ${getDemoAccountName(activeReplacement)} and refresh ProjectX.`
            : ` ${getDemoAccountName(activeReplacement)} will become active.`
          : "";
        if (!window.confirm(`Archive ${getDemoAccountName(account)}? Imported history is retained.${mainReplacementMessage}${activeReplacementMessage}`)) {
          return;
        }
      }

      setLifecycleAccountId(account.id);
      setAccountsError(null);
      try {
        const result = account.is_archived
          ? await accountsApi.unarchiveLiveAccount(account.id)
          : await accountsApi.archiveLiveAccount(
              account.id,
              replacementAccountId ?? undefined,
            );

        let nextActiveAccountId: number | null = null;
        if (!account.is_archived && accountFromQuery === account.id) {
          nextActiveAccountId = activeReplacement?.id ?? result.replacement_main_account_id;
          if (nextActiveAccountId !== null) {
            setActiveAccount(nextActiveAccountId);
          }
        }

        if (account.is_archived) {
          nextActiveAccountId = account.id;
          setActiveAccount(account.id);
        }

        await loadAccounts();

        dispatchAccountListChanged({
          accountId: account.id,
          action: account.is_archived ? "unarchived" : "archived",
          replacementAccountId: nextActiveAccountId,
        });
      } catch (err) {
        setAccountsError(
          err instanceof Error
            ? err.message
            : `Failed to ${account.is_archived ? "restore" : "archive"} Live account.`,
        );
      } finally {
        setLifecycleAccountId(null);
      }
    },
    [accountFromQuery, accounts, lifecycleAccountId, loadAccounts, setActiveAccount],
  );

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

  const visibleAccounts = useMemo(
    () => filterAccountManagementRows(freshnessAwareAccounts, {
      showHidden: showHiddenAccounts,
      showMissing: showMissingAccounts,
      showArchived: showArchivedAccounts,
    }),
    [freshnessAwareAccounts, showArchivedAccounts, showHiddenAccounts, showMissingAccounts],
  );
  const orderedAccounts = useMemo(() => sortAccountsForSelection(visibleAccounts), [visibleAccounts]);
  useEffect(() => {
    if (accountsLoading || refreshingExpressAccounts || Object.values(lastTradeLoadingById).some(Boolean)) {
      return;
    }
    // Resolve missing dates in the background, one at a time to avoid a burst
    // of provider requests. Completed empty results and errors are not retried automatically.
    const nextAccount = orderedAccounts.find(
      (account) => !account.last_trade_at && !lastTradeResolvedById[account.id],
    );
    if (nextAccount) {
      void resolveLastTrade(nextAccount.id);
    }
  }, [accountsLoading, refreshingExpressAccounts, orderedAccounts, lastTradeLoadingById, lastTradeResolvedById, resolveLastTrade]);
  const selectedAccount = useMemo(
    () => accounts.find((account) => account.id === accountFromQuery && !account.is_archived) ?? null,
    [accounts, accountFromQuery],
  );
  return (
    <div className="mt-3 space-y-3">
      <DemoModeNotice>
        <p>
          Demo Mode uses sample accounts. Provider refresh, archive and restore are disabled.
        </p>
      </DemoModeNotice>
      <section>
        <Card>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  disabled={demoModeEnabled || accountsLoading || refreshingExpressAccounts}
                  title={demoDisabledTitle}
                  onClick={() => void refreshExpressAccounts()}
                >
                  {refreshingExpressAccounts ? "Refreshing Express Accounts..." : "Refresh Express Accounts"}
                </Button>
                <p className="mt-1 text-[11px] text-app-muted">
                  Saved Live and Express rows load locally. Refresh contacts ProjectX only when requested.
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <Toggle
                  checked={showHiddenAccounts}
                  onChange={setShowHiddenAccounts}
                  label="Show hidden"
                  aria-label="Show hidden accounts"
                />
                <Toggle
                  checked={showMissingAccounts}
                  onChange={setShowMissingAccounts}
                  label="Show missing"
                  aria-label="Show missing accounts"
                />
                <Toggle
                  checked={showArchivedAccounts}
                  onChange={setShowArchivedAccounts}
                  label="Show archived"
                  aria-label="Show archived accounts"
                />
              </div>
            </div>
            {accountsError ? (
              <p className="rounded-lg border border-app-negative/25 bg-app-negative/10 px-3 py-2 text-xs text-app-negative" role="alert">
                {accountsError}
              </p>
            ) : refreshingExpressAccounts ? (
              <p className="rounded-lg border border-app-accent/25 bg-app-accent/10 px-3 py-2 text-xs text-app-accent" role="status" aria-live="polite">
                Refreshing ProjectX accounts… Saved rows remain available while the provider request is in progress.
              </p>
            ) : providerSyncNotice ? (
              <p
                className={`rounded-lg border px-3 py-2 text-xs ${
                  providerSyncNotice.tone === "error"
                    ? "border-app-negative/25 bg-app-negative/10 text-app-negative"
                    : providerSyncNotice.tone === "warning"
                      ? "border-app-warning/25 bg-app-warning/10 text-app-warning"
                      : "border-app-positive/20 bg-app-positive/10 text-app-positive"
                }`}
                role={providerSyncNotice.tone === "error" ? "alert" : "status"}
                aria-live="polite"
              >
                {providerSyncNotice.message}
              </p>
            ) : null}
            {lastTradeError ? <p className="text-xs text-app-warning">{lastTradeError}</p> : null}
            <AccountTableScrollArea>
              <table className="w-full min-w-[680px] border-collapse text-sm">
                <thead className="bg-app-bg/70 text-xs uppercase tracking-wide text-app-muted">
                  <tr>
                    <th className="px-3 py-3 text-left font-medium">Name</th>
                    <th className="px-3 py-3 text-right font-medium">ID</th>
                    <th className="px-3 py-3 text-right font-medium">Balance</th>
                    <th className="px-3 py-3 text-right font-medium">Last Trade</th>
                    <th className="px-3 py-3 text-right font-medium">Status</th>
                    <th className="px-3 py-3 text-right font-medium">Main</th>
                    <th className="px-3 py-3 text-right font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-app-border/70">
                  {accountsLoading ? (
                    Array.from({ length: 5 }).map((_, index) => (
                      <tr key={`accounts-loading-${index}`}>
                        <td colSpan={7} className="px-3 py-3">
                          <Skeleton className="h-6 w-full" />
                        </td>
                      </tr>
                    ))
                  ) : orderedAccounts.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="px-3 py-6 text-center text-app-muted">
                        No accounts found.
                      </td>
                    </tr>
                  ) : (
                    orderedAccounts.map((account) => {
                      const accountDisplayName = getDemoAccountName(account);
                      const isActive = selectedAccount?.id === account.id;
                      const isMainAccount = account.is_main;
                      const availableBalance = getAvailableAccountBalance(account.balance);
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
                          className={`transition ${
                            isActive ? "bg-app-accent/10" : "hover:bg-app-bg/65"
                          }`}
                        >
                          <td className="px-3 py-3 text-left font-medium text-app-text">
                            <AccountSelectionButton accountName={accountDisplayName} active={isActive}
                              disabled={account.is_archived} onSelect={() => setActiveAccount(account.id)} />
                          </td>
                          <td className="px-3 py-3 text-right text-app-text">
                            {account.trade_data_source === "csv_import" ? "—" : getDemoAccountId(account.id)}
                          </td>
                          <td className="px-3 py-3 text-right font-mono text-app-text">
                            <span className={availableBalance === null ? "font-sans text-app-warning" : undefined}>
                              {formatAccountBalance(account.balance)}
                            </span>
                            {account.trade_data_source === "projectx" && account.provider_data_stale ? (
                              <p className="mt-1 font-sans text-[10px] text-app-warning" title={account.last_seen_at ?? undefined}>
                                {`Stale · ${formatProviderLastSeen(account.last_seen_at)}`}
                              </p>
                            ) : null}
                          </td>
                          <td className="px-3 py-3 text-right text-app-text">
                            {resolvedLastTradeAt ? (
                              formatLastTrade(resolvedLastTradeAt)
                            ) : loadingLastTrade || !resolvedLastTrade ? (
                              "Checking..."
                            ) : lastTradeOverridesById[account.id] === null ? (
                              "No trades"
                            ) : (
                              <button
                                type="button"
                                aria-label={`Retry last trade for ${accountDisplayName}`}
                                className="text-xs text-app-accent underline decoration-app-accent/60 underline-offset-2 hover:text-app-accent"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  void resolveLastTrade(account.id, true);
                                }}
                              >
                                Unavailable · Retry
                              </button>
                            )}
                          </td>
                          <td className="px-3 py-3 text-right">
                            {account.trade_data_source === "csv_import" ? (
                              <>
                              <Badge variant="accent">Live · CSV</Badge>
                                {account.is_archived ? <Badge className="ml-1" variant="warning">Archived</Badge> : null}
                              </>
                            ) : (
                              <>
                                <Badge variant={accountStateBadgeVariant(account.account_state)}>
                                  {formatAccountStateLabel(account.account_state)}
                                </Badge>
                                {account.provider_sync_status === "cached_fallback" ? (
                                  <Badge className="ml-1" variant="negative">Refresh failed · cached</Badge>
                                ) : account.provider_data_stale ? (
                                  <Badge className="ml-1" variant="warning">Aged cache</Badge>
                                ) : null}
                              </>
                            )}
                          </td>
                          <td className="px-3 py-3 text-right">{isMainAccount && <Badge variant="accent">Main</Badge>}</td>
                          <td className="px-3 py-3 text-right">
                            {account.trade_data_source === "csv_import" ? (
                              <Button
                                size="sm"
                                variant={account.is_archived ? "secondary" : "ghost"}
                                disabled={demoModeEnabled || lifecycleAccountId !== null}
                                title={demoDisabledTitle}
                                aria-label={`${account.is_archived ? "Restore" : "Archive"} ${accountDisplayName}`}
                                onClick={() => void changeLiveAccountArchiveState(account)}
                              >
                                {lifecycleAccountId === account.id
                                  ? account.is_archived
                                    ? "Restoring..."
                                    : "Archiving..."
                                  : account.is_archived
                                    ? "Restore"
                                    : "Archive"}
                              </Button>
                            ) : (
                              <span className="text-xs text-app-muted">—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </AccountTableScrollArea>
            <p className="text-[11px] text-app-muted sm:hidden">
              Swipe the account table horizontally to review balances, status, and actions.
            </p>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
