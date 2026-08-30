import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { DemoModeNotice } from "../../components/demo/DemoModeNotice";
import { useDemoInteractionPolicy } from "../../components/demo/useDemoInteractionPolicy";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Input } from "../../components/ui/Input";
import { Skeleton } from "../../components/ui/Skeleton";
import { Toggle } from "../../components/ui/Toggle";
import {
  ACCOUNT_QUERY_PARAM,
  dispatchAccountListChanged,
  parseAccountId,
  readStoredAccountId,
  readStoredMainAccountId,
  writeStoredAccountId,
  writeStoredMainAccountId,
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
import type { AccountInfo, JournalMergeResult } from "../../lib/types";
import {
  AccountSelectionButton,
  AccountTableScrollArea,
} from "./accountManagement";
import { filterAccountManagementRows, loadAccountManagementRows } from "./accountManagementData";
import { MergeJournalCard } from "./components/MergeJournalCard";
import {
  type MergeJournalFormState,
  approveMergeJournalSubmission,
  buildMergeJournalSuccessMessage,
  filterMergeSourceAccounts,
  getMergeDestinationAccounts,
  reconcileMergeJournalForm,
  validateMergeJournalForm,
} from "./mergeJournal";

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

function PencilIcon({ className = "h-3.5 w-3.5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className={className} aria-hidden="true">
      <path d="M12 20h9" strokeLinecap="round" strokeLinejoin="round" />
      <path
        d="M16.5 3.5a2.1 2.1 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5Z"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function CheckIcon({ className = "h-3.5 w-3.5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className} aria-hidden="true">
      <path d="m5 13 4 4L19 7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function XIcon({ className = "h-3.5 w-3.5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className} aria-hidden="true">
      <path d="M18 6 6 18" strokeLinecap="round" strokeLinejoin="round" />
      <path d="m6 6 12 12" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function AccountsPage() {
  const { demoModeEnabled, demoDisabledTitle } = useDemoInteractionPolicy();
  const [searchParams, setSearchParams] = useSearchParams();
  const accountFromQuery = parseAccountId(searchParams.get(ACCOUNT_QUERY_PARAM));

  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [mergeAccounts, setMergeAccounts] = useState<AccountInfo[]>([]);
  const [accountsLoading, setAccountsLoading] = useState(true);
  const [refreshingExpressAccounts, setRefreshingExpressAccounts] = useState(false);
  const [accountsError, setAccountsError] = useState<string | null>(null);
  const [showHiddenAccounts, setShowHiddenAccounts] = useState(false);
  const [showMissingAccounts, setShowMissingAccounts] = useState(false);
  const [showArchivedAccounts, setShowArchivedAccounts] = useState(false);
  const [settingMainAccountId, setSettingMainAccountId] = useState<number | null>(null);
  const [lifecycleAccountId, setLifecycleAccountId] = useState<number | null>(null);
  const [lastTradeOverridesById, setLastTradeOverridesById] = useState<Record<number, string | null>>({});
  const [lastTradeLoadingById, setLastTradeLoadingById] = useState<Record<number, boolean>>({});
  const [lastTradeResolvedById, setLastTradeResolvedById] = useState<Record<number, boolean>>({});
  const [lastTradeError, setLastTradeError] = useState<string | null>(null);
  const [editingAccountId, setEditingAccountId] = useState<number | null>(null);
  const [editingName, setEditingName] = useState("");
  const [renamingAccountId, setRenamingAccountId] = useState<number | null>(null);
  const [renameErrorById, setRenameErrorById] = useState<Record<number, string | null>>({});
  const [mergeForm, setMergeForm] = useState<MergeJournalFormState>({
    fromAccountId: "",
    toAccountId: "",
    onConflict: "skip",
    includeImages: true,
  });
  const [mergeJournalLoading, setMergeJournalLoading] = useState(false);
  const [mergeJournalError, setMergeJournalError] = useState<string | null>(null);
  const [mergeJournalSuccess, setMergeJournalSuccess] = useState<string | null>(null);
  const [mergeJournalResult, setMergeJournalResult] = useState<JournalMergeResult | null>(null);
  const [mergeOldAccountSearch, setMergeOldAccountSearch] = useState("");
  const editInputRef = useRef<HTMLInputElement | null>(null);
  const accountsVersionRef = useRef(0);
  const lastTradeRequestVersionByIdRef = useRef<Record<number, number>>({});
  const refreshExpressRequestRef = useRef(false);
  const pendingLifecycleActiveAccountIdRef = useRef<number | null>(null);
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
      setMergeAccounts(savedAccounts.filter((account) => !account.is_archived));
      setEditingAccountId(null);
      setEditingName("");
      setRenameErrorById({});
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
        setMergeAccounts([]);
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
      const activeAccountId =
        accountFromQuery ??
        readStoredAccountId() ??
        refreshedAccounts?.find((account) => account.is_main)?.id ??
        readStoredMainAccountId() ??
        refreshedAccounts?.[0]?.id ??
        null;
      const activeAccount = refreshedAccounts?.find((account) => account.id === activeAccountId);
      if (
        activeAccount?.trade_data_source === "csv_import" &&
        providerSummary &&
        providerSummary.status !== "cached_fallback"
      ) {
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
  }, [accountFromQuery, loadAccounts]);

  const freshnessAwareAccounts = useAccountProviderFreshness(accounts);
  const providerSyncNotice = useMemo(
    () => describeAccountProviderSync(summarizeAccountProviderSync(freshnessAwareAccounts)),
    [freshnessAwareAccounts],
  );

  const resolveLastTrade = useCallback(async (accountId: number, refresh = false) => {
    if (lastTradeLoadingById[accountId]) {
      return;
    }

    const accountsVersion = accountsVersionRef.current;
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
      if (isCurrent()) {
        setLastTradeResolvedById((prev) => ({ ...prev, [accountId]: true }));
        setLastTradeLoadingById((prev) => ({ ...prev, [accountId]: false }));
      }
    }
  }, [lastTradeLoadingById]);

  const setMainAccount = useCallback(
    async (accountId: number) => {
      if (accounts.some((account) => account.id === accountId && account.is_archived)) {
        setAccountsError("Restore this Live account before setting it as Main.");
        return;
      }
      setSettingMainAccountId(accountId);
      setAccountsError(null);
      try {
        await accountsApi.setMainAccount(accountId);
        writeStoredMainAccountId(accountId);
        setActiveAccount(accountId);
        await loadAccounts();
      } catch (err) {
        setAccountsError(err instanceof Error ? err.message : "Failed to update main account");
      } finally {
        setSettingMainAccountId(null);
      }
    },
    [accounts, loadAccounts, setActiveAccount],
  );

  const startEditingAccountName = useCallback((account: AccountInfo) => {
    setAccountsError(null);
    setRenameErrorById((prev) => ({ ...prev, [account.id]: null }));
    setEditingAccountId(account.id);
    setEditingName(account.name);
  }, []);

  const cancelEditingAccountName = useCallback(() => {
    setEditingAccountId(null);
    setEditingName("");
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
            pendingLifecycleActiveAccountIdRef.current = nextActiveAccountId;
            setActiveAccount(nextActiveAccountId);
          }
        }

        if (account.is_archived) {
          nextActiveAccountId = account.id;
          pendingLifecycleActiveAccountIdRef.current = account.id;
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

  const saveAccountName = useCallback(
    async (account: AccountInfo) => {
      const trimmedName = editingName.trim();
      if (trimmedName.length === 0) {
        setRenameErrorById((prev) => ({
          ...prev,
          [account.id]: "Account name cannot be empty.",
        }));
        return;
      }

      setRenamingAccountId(account.id);
      setAccountsError(null);
      setRenameErrorById((prev) => ({ ...prev, [account.id]: null }));
      try {
        const payload = await accountsApi.renameAccountDisplayName(account.id, trimmedName);
        setAccounts((prev) =>
          prev.map((candidate) =>
            candidate.id === account.id
              ? {
                  ...candidate,
                  name: payload.name,
                  provider_name: payload.provider_name,
                  custom_display_name: payload.custom_display_name,
                }
              : candidate,
          ),
        );
        setEditingAccountId(null);
        setEditingName("");
      } catch (err) {
        setRenameErrorById((prev) => ({
          ...prev,
          [account.id]: err instanceof Error ? err.message : "Failed to update account name.",
        }));
        setEditingAccountId(null);
        setEditingName("");
      } finally {
        setRenamingAccountId(null);
      }
    },
    [editingName],
  );

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

  useEffect(() => {
    if (editingAccountId === null) {
      return;
    }
    editInputRef.current?.focus();
    editInputRef.current?.select();
  }, [editingAccountId]);

  const visibleAccounts = useMemo(
    () => filterAccountManagementRows(freshnessAwareAccounts, {
      showHidden: showHiddenAccounts,
      showMissing: showMissingAccounts,
      showArchived: showArchivedAccounts,
    }),
    [freshnessAwareAccounts, showArchivedAccounts, showHiddenAccounts, showMissingAccounts],
  );
  const orderedAccounts = useMemo(() => sortAccountsForSelection(visibleAccounts), [visibleAccounts]);
  const selectableAccounts = useMemo(
    () => orderedAccounts.filter((account) => !account.is_archived),
    [orderedAccounts],
  );
  const orderedMergeAccounts = useMemo(() => sortAccountsForSelection(mergeAccounts), [mergeAccounts]);
  const orderedMergeDestinationAccounts = useMemo(
    () => sortAccountsForSelection(getMergeDestinationAccounts(mergeAccounts)),
    [mergeAccounts],
  );
  const filteredMergeSourceAccounts = useMemo(
    () => filterMergeSourceAccounts(orderedMergeAccounts, mergeOldAccountSearch),
    [mergeOldAccountSearch, orderedMergeAccounts],
  );

  useEffect(() => {
    if (selectableAccounts.length === 0) {
      return;
    }

    const pendingLifecycleActiveAccountId = pendingLifecycleActiveAccountIdRef.current;
    if (pendingLifecycleActiveAccountId !== null) {
      if (!selectableAccounts.some((account) => account.id === pendingLifecycleActiveAccountId)) {
        return;
      }
      if (accountFromQuery !== pendingLifecycleActiveAccountId) {
        setActiveAccount(pendingLifecycleActiveAccountId);
        return;
      }
      writeStoredAccountId(pendingLifecycleActiveAccountId);
      pendingLifecycleActiveAccountIdRef.current = null;
      return;
    }

    if (accountFromQuery && selectableAccounts.some((account) => account.id === accountFromQuery)) {
      writeStoredAccountId(accountFromQuery);
      return;
    }

    const persistedMainAccountId = selectableAccounts.find((account) => account.is_main)?.id ?? null;
    if (persistedMainAccountId) {
      writeStoredMainAccountId(persistedMainAccountId);
      setActiveAccount(persistedMainAccountId);
      return;
    }

    const storedAccountId = readStoredAccountId();
    const storedMainAccountId = readStoredMainAccountId();
    if (storedMainAccountId && selectableAccounts.some((account) => account.id === storedMainAccountId)) {
      setActiveAccount(storedMainAccountId);
      return;
    }

    if (storedAccountId && selectableAccounts.some((account) => account.id === storedAccountId)) {
      setActiveAccount(storedAccountId);
      return;
    }

    setActiveAccount(selectableAccounts[0].id);
  }, [selectableAccounts, accountFromQuery, setActiveAccount]);

  const selectedAccount = useMemo(
    () => accounts.find((account) => account.id === accountFromQuery && !account.is_archived) ?? null,
    [accounts, accountFromQuery],
  );
  const mergeAccountNamesById = useMemo(
    () => new Map(orderedMergeAccounts.map((account) => [account.id, getDemoAccountName(account)] as const)),
    [orderedMergeAccounts],
  );
  const mergeValidationMessage = useMemo(() => validateMergeJournalForm(mergeForm), [mergeForm]);
  const mergeSubmitDisabled = mergeJournalLoading || mergeValidationMessage !== null;

  useEffect(() => {
    setMergeForm((current) =>
      reconcileMergeJournalForm(
        current,
        orderedMergeAccounts,
        orderedMergeDestinationAccounts,
        selectedAccount?.id ?? null,
      ),
    );
  }, [orderedMergeAccounts, orderedMergeDestinationAccounts, selectedAccount?.id]);

  const updateMergeForm = useCallback((updater: (current: MergeJournalFormState) => MergeJournalFormState) => {
    setMergeForm((current) => updater(current));
    setMergeJournalError(null);
    setMergeJournalSuccess(null);
    setMergeJournalResult(null);
  }, []);

  const handleMergeJournal = useCallback(async () => {
    const validationMessage = validateMergeJournalForm(mergeForm);
    if (validationMessage) {
      return;
    }

    const fromAccountId = Number.parseInt(mergeForm.fromAccountId, 10);
    const toAccountId = Number.parseInt(mergeForm.toAccountId, 10);
    if (!approveMergeJournalSubmission(mergeForm, mergeAccountNamesById, (message) => window.confirm(message))) {
      return;
    }

    setMergeJournalLoading(true);
    setMergeJournalError(null);
    setMergeJournalSuccess(null);
    setMergeJournalResult(null);
    try {
      const result = await accountsApi.mergeJournalEntries({
        from_account_id: fromAccountId,
        to_account_id: toAccountId,
        on_conflict: mergeForm.onConflict,
        include_images: mergeForm.includeImages,
      });
      setMergeJournalResult(result);
      setMergeJournalSuccess(buildMergeJournalSuccessMessage(result, mergeAccountNamesById));
      await loadAccounts();
    } catch (err) {
      setMergeJournalError(err instanceof Error ? err.message : "Failed to merge journal history.");
    } finally {
      setMergeJournalLoading(false);
    }
  }, [loadAccounts, mergeAccountNamesById, mergeForm]);

  return (
    <div className="space-y-6 pb-10">
      <h1 className="sr-only">Accounts</h1>
      <DemoModeNotice>
        <p>
          These are isolated sample accounts. Selecting an account and filtering rows are simulated locally;
          provider refresh, renaming, Main-account changes, archiving, and journal merges are disabled.
        </p>
      </DemoModeNotice>
      <section>
        <Card>
          <CardHeader>
            <CardTitle>Trading Accounts</CardTitle>
            <CardDescription>Manage ProjectX accounts and Live accounts that use CSV trade imports.</CardDescription>
          </CardHeader>
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
                <p className="mt-1 text-[11px] text-slate-500">
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
              <p className="rounded-lg border border-rose-400/25 bg-rose-500/10 px-3 py-2 text-xs text-rose-200" role="alert">
                {accountsError}
              </p>
            ) : refreshingExpressAccounts ? (
              <p className="rounded-lg border border-cyan-400/25 bg-cyan-500/10 px-3 py-2 text-xs text-cyan-100" role="status" aria-live="polite">
                Refreshing ProjectX accounts… Saved rows remain available while the provider request is in progress.
              </p>
            ) : providerSyncNotice ? (
              <p
                className={`rounded-lg border px-3 py-2 text-xs ${
                  providerSyncNotice.tone === "error"
                    ? "border-rose-400/25 bg-rose-500/10 text-rose-200"
                    : providerSyncNotice.tone === "warning"
                      ? "border-amber-400/25 bg-amber-500/10 text-amber-200"
                      : "border-emerald-400/20 bg-emerald-500/10 text-emerald-100"
                }`}
                role={providerSyncNotice.tone === "error" ? "alert" : "status"}
                aria-live="polite"
              >
                {providerSyncNotice.message}
              </p>
            ) : null}
            {lastTradeError ? <p className="text-xs text-amber-300">{lastTradeError}</p> : null}
            <AccountTableScrollArea>
              <table className="w-full min-w-[680px] border-collapse text-sm">
                <thead className="bg-slate-900/70 text-xs uppercase tracking-wide text-slate-400">
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
                <tbody className="divide-y divide-slate-800/70">
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
                      <td colSpan={7} className="px-3 py-6 text-center text-slate-400">
                        No accounts found.
                      </td>
                    </tr>
                  ) : (
                    orderedAccounts.map((account) => {
                      const accountDisplayName = getDemoAccountName(account);
                      const isActive = selectedAccount?.id === account.id;
                      const isMainAccount = account.is_main;
                      const isEditingName = editingAccountId === account.id;
                      const renameErrorMessage = renameErrorById[account.id];
                      const savingName = renamingAccountId === account.id;
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
                            isActive ? "bg-cyan-500/10" : "hover:bg-slate-900/65"
                          }`}
                        >
                          <td className="px-3 py-3 text-left font-medium text-slate-100">
                            {isEditingName ? (
                              <div
                                className="flex min-w-0 items-start gap-2"
                                onClick={(event) => {
                                  event.stopPropagation();
                                }}
                              >
                                <Input
                                  ref={editInputRef}
                                  value={editingName}
                                  onChange={(event) => setEditingName(event.target.value)}
                                  onKeyDown={(event) => {
                                    if (event.key === "Enter") {
                                      event.preventDefault();
                                      void saveAccountName(account);
                                      return;
                                    }
                                    if (event.key === "Escape") {
                                      event.preventDefault();
                                      cancelEditingAccountName();
                                    }
                                  }}
                                  disabled={savingName}
                                  aria-label={`Edit account name for ${accountDisplayName}`}
                                  className="h-8 min-w-0 flex-1 px-2.5"
                                />
                                <div className="flex shrink-0 items-center gap-1">
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    className="h-11 w-11 rounded-lg px-0 text-emerald-300 hover:text-emerald-200 sm:h-8 sm:w-8"
                                    disabled={savingName}
                                    aria-label={`Save account name for ${accountDisplayName}`}
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      void saveAccountName(account);
                                    }}
                                  >
                                    <CheckIcon />
                                  </Button>
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    className="h-11 w-11 rounded-lg px-0 text-slate-400 hover:text-slate-200 sm:h-8 sm:w-8"
                                    disabled={savingName}
                                    aria-label={`Cancel editing account name for ${accountDisplayName}`}
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      cancelEditingAccountName();
                                    }}
                                  >
                                    <XIcon />
                                  </Button>
                                </div>
                              </div>
                            ) : (
                              <div className="flex min-w-0 items-center gap-2">
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-11 w-11 shrink-0 rounded-lg px-0 text-slate-400 hover:text-slate-100 sm:h-8 sm:w-8"
                                  disabled={demoModeEnabled || renamingAccountId !== null}
                                  title={demoDisabledTitle}
                                  aria-label={`Edit account name for ${accountDisplayName}`}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    startEditingAccountName(account);
                                  }}
                                >
                                  <PencilIcon />
                                </Button>
                                <AccountSelectionButton
                                  accountName={accountDisplayName}
                                  active={isActive}
                                  disabled={account.is_archived}
                                  onSelect={() => {
                                    setActiveAccount(account.id);
                                    if (!resolvedLastTradeAt && !resolvedLastTrade && !loadingLastTrade) {
                                      void resolveLastTrade(account.id);
                                    }
                                  }}
                                />
                              </div>
                            )}
                            {savingName ? <p className="mt-1 text-[11px] font-normal text-slate-500">Saving...</p> : null}
                            {renameErrorMessage ? (
                              <p className="mt-1 text-[11px] font-normal text-rose-300">{renameErrorMessage}</p>
                            ) : null}
                          </td>
                          <td className="px-3 py-3 text-right text-slate-300">
                            {account.trade_data_source === "csv_import" ? "—" : getDemoAccountId(account.id)}
                          </td>
                          <td className="px-3 py-3 text-right font-mono text-slate-200">
                            <span className={availableBalance === null ? "font-sans text-amber-300" : undefined}>
                              {formatAccountBalance(account.balance)}
                            </span>
                            {account.trade_data_source === "projectx" && account.provider_data_stale ? (
                              <p className="mt-1 font-sans text-[10px] text-amber-300" title={account.last_seen_at ?? undefined}>
                                {`Stale · ${formatProviderLastSeen(account.last_seen_at)}`}
                              </p>
                            ) : null}
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
                          <td className="px-3 py-3 text-right">
                            {account.is_archived ? (
                              <span className="text-xs text-slate-500">Unavailable</span>
                            ) : isMainAccount ? (
                              <Badge variant="accent">Main</Badge>
                            ) : (
                              <Button
                                size="sm"
                                variant="ghost"
                                disabled={demoModeEnabled || settingMainAccountId === account.id}
                                title={demoDisabledTitle}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  void setMainAccount(account.id);
                                }}
                              >
                                {settingMainAccountId === account.id ? "Saving..." : "Set Main"}
                              </Button>
                            )}
                          </td>
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
                              <span className="text-xs text-slate-600">—</span>
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
      <section>
        {demoModeEnabled ? (
          <Card>
            <CardHeader>
              <CardTitle>Merge Journal</CardTitle>
              <CardDescription>Journal history changes are unavailable while viewing sample accounts.</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="rounded-xl border border-app-border bg-app-bg/35 px-4 py-4 text-sm text-app-muted" role="status">
                Turn off Demo Mode to merge journal history between your connected accounts.
              </p>
            </CardContent>
          </Card>
        ) : (
        <MergeJournalCard
          sourceAccounts={filteredMergeSourceAccounts}
          destinationAccounts={orderedMergeDestinationAccounts}
          form={mergeForm}
          oldAccountSearch={mergeOldAccountSearch}
          loading={mergeJournalLoading}
          submitDisabled={mergeSubmitDisabled}
          validationMessage={mergeValidationMessage}
          errorMessage={mergeJournalError}
          successMessage={mergeJournalSuccess}
          successResult={mergeJournalResult}
          onOldAccountSearchChange={(value) => setMergeOldAccountSearch(value)}
          onFromAccountChange={(value) =>
            updateMergeForm((current) => ({
              ...current,
              fromAccountId: value,
            }))
          }
          onToAccountChange={(value) =>
            updateMergeForm((current) => ({
              ...current,
              toAccountId: value,
            }))
          }
          onConflictChange={(value) =>
            updateMergeForm((current) => ({
              ...current,
              onConflict: value,
            }))
          }
          onIncludeImagesChange={(value) =>
            updateMergeForm((current) => ({
              ...current,
              includeImages: value,
            }))
          }
          onSubmit={() => void handleMergeJournal()}
        />
        )}
      </section>
    </div>
  );
}
