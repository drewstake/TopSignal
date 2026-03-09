import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Input } from "../../components/ui/Input";
import { Skeleton } from "../../components/ui/Skeleton";
import { Toggle } from "../../components/ui/Toggle";
import {
  ACCOUNT_QUERY_PARAM,
  parseAccountId,
  readStoredAccountId,
  readStoredMainAccountId,
  writeStoredAccountId,
  writeStoredMainAccountId,
} from "../../lib/accountSelection";
import { logPerfInfo } from "../../lib/perf";
import { accountsApi } from "../../lib/api";
import { sortAccountsForSelection } from "../../lib/accountOrdering";
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
  const [searchParams, setSearchParams] = useSearchParams();
  const accountFromQuery = parseAccountId(searchParams.get(ACCOUNT_QUERY_PARAM));

  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [accountsLoading, setAccountsLoading] = useState(true);
  const [accountsError, setAccountsError] = useState<string | null>(null);
  const [showHiddenAccounts, setShowHiddenAccounts] = useState(false);
  const [showMissingAccounts, setShowMissingAccounts] = useState(false);
  const [settingMainAccountId, setSettingMainAccountId] = useState<number | null>(null);
  const [lastTradeOverridesById, setLastTradeOverridesById] = useState<Record<number, string | null>>({});
  const [lastTradeLoadingById, setLastTradeLoadingById] = useState<Record<number, boolean>>({});
  const [lastTradeResolvedById, setLastTradeResolvedById] = useState<Record<number, boolean>>({});
  const [lastTradeError, setLastTradeError] = useState<string | null>(null);
  const [editingAccountId, setEditingAccountId] = useState<number | null>(null);
  const [editingName, setEditingName] = useState("");
  const [renamingAccountId, setRenamingAccountId] = useState<number | null>(null);
  const [renameErrorById, setRenameErrorById] = useState<Record<number, string | null>>({});
  const editInputRef = useRef<HTMLInputElement | null>(null);

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
    const startedAtIso = new Date().toISOString();
    const startedAtMs = performance.now();
    logPerfInfo("[perf][accounts] load-start", {
      started_at: startedAtIso,
      show_hidden: showHiddenAccounts,
      show_missing: showMissingAccounts,
    });
    setAccountsLoading(true);
    setAccountsError(null);
    setLastTradeError(null);

    try {
      const payload = await accountsApi.getAccounts({
        showInactive: true,
        showMissing: showMissingAccounts,
      });
      setAccounts(payload.filter((account) => showHiddenAccounts || account.account_state !== "HIDDEN"));
      setEditingAccountId(null);
      setEditingName("");
      setRenameErrorById({});
      setLastTradeOverridesById({});
      setLastTradeLoadingById({});
      setLastTradeResolvedById({});
    } catch (err) {
      setAccountsError(err instanceof Error ? err.message : "Failed to load accounts");
      setAccounts([]);
    } finally {
      const totalMs = Math.max(performance.now() - startedAtMs, 0);
      logPerfInfo("[perf][accounts] load-end", {
        started_at: startedAtIso,
        finished_at: new Date().toISOString(),
        total_ms: Number(totalMs.toFixed(2)),
        show_hidden: showHiddenAccounts,
        show_missing: showMissingAccounts,
      });
      setAccountsLoading(false);
    }
  }, [showHiddenAccounts, showMissingAccounts]);

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

  const setMainAccount = useCallback(
    async (accountId: number) => {
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
    [loadAccounts, setActiveAccount],
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

  const orderedAccounts = useMemo(() => sortAccountsForSelection(accounts), [accounts]);

  useEffect(() => {
    if (orderedAccounts.length === 0) {
      return;
    }

    if (accountFromQuery && orderedAccounts.some((account) => account.id === accountFromQuery)) {
      writeStoredAccountId(accountFromQuery);
      return;
    }

    const persistedMainAccountId = orderedAccounts.find((account) => account.is_main)?.id ?? null;
    if (persistedMainAccountId) {
      writeStoredMainAccountId(persistedMainAccountId);
      setActiveAccount(persistedMainAccountId);
      return;
    }

    const storedAccountId = readStoredAccountId();
    const storedMainAccountId = readStoredMainAccountId();
    if (storedMainAccountId && orderedAccounts.some((account) => account.id === storedMainAccountId)) {
      setActiveAccount(storedMainAccountId);
      return;
    }

    if (storedAccountId && orderedAccounts.some((account) => account.id === storedAccountId)) {
      setActiveAccount(storedAccountId);
      return;
    }

    setActiveAccount(orderedAccounts[0].id);
  }, [orderedAccounts, accountFromQuery, setActiveAccount]);

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
            <CardDescription>Select an account to make it active, or mark one as your default main account.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center justify-end">
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
              </div>
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
                    <th className="px-3 py-3 text-right font-medium">Main</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {accountsLoading ? (
                    Array.from({ length: 5 }).map((_, index) => (
                      <tr key={`accounts-loading-${index}`}>
                        <td colSpan={6} className="px-3 py-3">
                          <Skeleton className="h-6 w-full" />
                        </td>
                      </tr>
                    ))
                  ) : accountsError ? (
                    <tr>
                      <td colSpan={6} className="px-3 py-6 text-center text-rose-300">
                        {accountsError}
                      </td>
                    </tr>
                  ) : orderedAccounts.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="px-3 py-6 text-center text-slate-400">
                        No accounts found.
                      </td>
                    </tr>
                  ) : (
                    orderedAccounts.map((account) => {
                      const isActive = selectedAccount?.id === account.id;
                      const isMainAccount = account.is_main;
                      const isEditingName = editingAccountId === account.id;
                      const renameErrorMessage = renameErrorById[account.id];
                      const savingName = renamingAccountId === account.id;
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
                                  aria-label={`Edit account name for ${account.provider_name}`}
                                  className="h-8 min-w-0 flex-1 px-2.5"
                                />
                                <div className="flex shrink-0 items-center gap-1">
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    className="h-7 w-7 rounded-lg px-0 text-emerald-300 hover:text-emerald-200"
                                    disabled={savingName}
                                    aria-label={`Save account name for ${account.provider_name}`}
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
                                    className="h-7 w-7 rounded-lg px-0 text-slate-400 hover:text-slate-200"
                                    disabled={savingName}
                                    aria-label={`Cancel editing account name for ${account.provider_name}`}
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
                                  className="h-7 w-7 shrink-0 rounded-lg px-0 text-slate-400 hover:text-slate-100"
                                  disabled={renamingAccountId !== null}
                                  aria-label={`Edit account name for ${account.name}`}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    startEditingAccountName(account);
                                  }}
                                >
                                  <PencilIcon />
                                </Button>
                                <span className="truncate">{account.name}</span>
                              </div>
                            )}
                            {savingName ? <p className="mt-1 text-[11px] font-normal text-slate-500">Saving...</p> : null}
                            {renameErrorMessage ? (
                              <p className="mt-1 text-[11px] font-normal text-rose-300">{renameErrorMessage}</p>
                            ) : null}
                          </td>
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
                            <Badge variant={accountStateBadgeVariant(account.account_state)}>
                              {formatAccountStateLabel(account.account_state)}
                            </Badge>
                          </td>
                          <td className="px-3 py-3 text-right">
                            {isMainAccount ? (
                              <Badge variant="accent">Main</Badge>
                            ) : (
                              <Button
                                size="sm"
                                variant="ghost"
                                disabled={settingMainAccountId === account.id}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  void setMainAccount(account.id);
                                }}
                              >
                                {settingMainAccountId === account.id ? "Saving..." : "Set Main"}
                              </Button>
                            )}
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
