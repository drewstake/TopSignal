import { useEffect, useMemo, useState } from "react";

import type { AccountInfo, AccountProviderSyncStatus } from "./types";
import { formatCurrency } from "../utils/formatters";

const MAX_BROWSER_TIMEOUT_MS = 2_147_483_647;

const providerLastSeenFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZone: "America/New_York",
});

export function getAvailableAccountBalance(balance: number | null): number | null {
  return typeof balance === "number" && Number.isFinite(balance) ? balance : null;
}

export function formatAccountBalance(balance: number | null): string {
  const availableBalance = getAvailableAccountBalance(balance);
  return availableBalance === null ? "Unavailable" : formatCurrency(availableBalance);
}

export function getAccountProviderState(account: Pick<AccountInfo, "balance" | "provider_data_stale" | "last_seen_at">) {
  return {
    balance: getAvailableAccountBalance(account.balance),
    stale: account.provider_data_stale,
    lastSeenAt: account.last_seen_at,
  };
}

export interface AccountProviderSyncSummary {
  status: Exclude<AccountProviderSyncStatus, "not_applicable">;
  errorCode: string | null;
  errorMessage: string | null;
  lastSuccessfulRefreshAt: string | null;
}

export interface AccountProviderSyncNotice {
  tone: "success" | "warning" | "error";
  message: string;
}

const providerSyncSeverity: Record<Exclude<AccountProviderSyncStatus, "not_applicable">, number> = {
  provider_fresh: 0,
  cache_fresh: 1,
  cache_stale: 2,
  cached_fallback: 3,
};

const providerSyncErrorFallbacks: Record<string, string> = {
  projectx_credentials_not_configured:
    "ProjectX credentials are not configured for this signed-in user.",
  projectx_credentials_unavailable:
    "The saved ProjectX credential cannot be opened by this runtime. Ask an administrator to verify the credential encryption key and restart the app.",
  projectx_auth_failed:
    "ProjectX rejected the saved credential for this signed-in user. Update that user's ProjectX credential before retrying.",
  projectx_network_error:
    "TopSignal could not reach ProjectX. Check the network connection and retry.",
};

function providerStaleDeadlineMs(account: Pick<AccountInfo, "provider_data_stale_at">): number | null {
  if (!account.provider_data_stale_at) {
    return null;
  }
  const timestamp = Date.parse(account.provider_data_stale_at);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function providerDeadlineHasPassed(account: AccountInfo, nowMs: number): boolean {
  const deadlineMs = providerStaleDeadlineMs(account);
  return account.trade_data_source === "projectx" && deadlineMs !== null && nowMs >= deadlineMs;
}

function accountProviderSyncStatus(account: AccountInfo, nowMs: number = Date.now()): AccountProviderSyncStatus {
  const status = account.provider_sync_status ?? (
    account.trade_data_source === "csv_import"
      ? "not_applicable"
      : account.provider_data_stale
        ? "cache_stale"
        : "cache_fresh"
  );
  if (
    providerDeadlineHasPassed(account, nowMs) &&
    status !== "cached_fallback" &&
    status !== "not_applicable"
  ) {
    return "cache_stale";
  }
  return status;
}

export function reclassifyAccountProviderFreshness(
  account: AccountInfo,
  nowMs: number = Date.now(),
): AccountInfo {
  if (account.trade_data_source === "csv_import") {
    if (account.provider_data_stale === false && account.provider_sync_status === "not_applicable") {
      return account;
    }
    return {
      ...account,
      provider_data_stale: false,
      provider_sync_status: "not_applicable",
      provider_sync_error_code: null,
      provider_sync_error_message: null,
    };
  }
  if (!providerDeadlineHasPassed(account, nowMs)) {
    return account;
  }

  const status = accountProviderSyncStatus(account, nowMs);
  if (account.provider_data_stale && account.provider_sync_status === status) {
    return account;
  }
  return {
    ...account,
    provider_data_stale: true,
    provider_sync_status: status,
  };
}

export function reclassifyAccountListProviderFreshness(
  accounts: readonly AccountInfo[],
  nowMs: number = Date.now(),
): AccountInfo[] {
  return accounts.map((account) => reclassifyAccountProviderFreshness(account, nowMs));
}

/**
 * Keeps already-rendered account rows honest when a server-defined freshness
 * deadline passes. This changes only local presentation state; it never starts
 * a provider or backend request.
 */
export function useAccountProviderFreshness(accounts: readonly AccountInfo[]): AccountInfo[] {
  const [observedNowMs, setObservedNowMs] = useState(Number.NEGATIVE_INFINITY);
  const reclassifiedAccounts = useMemo(
    () => reclassifyAccountListProviderFreshness(accounts, observedNowMs),
    [accounts, observedNowMs],
  );

  useEffect(() => {
    const currentMs = Date.now();
    let nextDeadlineMs: number | null = null;
    let hasUnobservedExpiredDeadline = false;
    for (const account of accounts) {
      if (account.trade_data_source !== "projectx" || account.provider_data_stale) {
        continue;
      }
      const deadlineMs = providerStaleDeadlineMs(account);
      if (deadlineMs === null || deadlineMs <= observedNowMs) {
        continue;
      }
      if (deadlineMs <= currentMs) {
        hasUnobservedExpiredDeadline = true;
        continue;
      }
      nextDeadlineMs = nextDeadlineMs === null
        ? deadlineMs
        : Math.min(nextDeadlineMs, deadlineMs);
    }
    if (!hasUnobservedExpiredDeadline && nextDeadlineMs === null) {
      return undefined;
    }

    const wakeAtMs = hasUnobservedExpiredDeadline
      ? currentMs
      : Math.min(nextDeadlineMs as number, currentMs + MAX_BROWSER_TIMEOUT_MS);
    const timeoutMs = Math.max(0, wakeAtMs - currentMs);
    const timeoutId = window.setTimeout(() => {
      setObservedNowMs((previousMs) => Math.max(previousMs, wakeAtMs));
    }, timeoutMs);
    return () => window.clearTimeout(timeoutId);
  }, [accounts, observedNowMs]);

  return reclassifiedAccounts;
}

export function summarizeAccountProviderSync(accounts: readonly AccountInfo[]): AccountProviderSyncSummary | null {
  const projectXAccounts = accounts.filter((account) => account.trade_data_source === "projectx");
  if (projectXAccounts.length === 0) {
    return null;
  }

  const nowMs = Date.now();
  const representative = projectXAccounts.reduce((current, account) => {
    const resolvedCurrentStatus = accountProviderSyncStatus(current, nowMs);
    const resolvedAccountStatus = accountProviderSyncStatus(account, nowMs);
    const currentStatus = resolvedCurrentStatus === "not_applicable"
      ? "cache_stale"
      : resolvedCurrentStatus;
    const accountStatus = resolvedAccountStatus === "not_applicable"
      ? "cache_stale"
      : resolvedAccountStatus;
    return providerSyncSeverity[accountStatus] > providerSyncSeverity[currentStatus] ? account : current;
  });
  const resolvedRepresentativeStatus = accountProviderSyncStatus(representative, nowMs);
  const status = resolvedRepresentativeStatus === "not_applicable"
    ? "cache_stale"
    : resolvedRepresentativeStatus;

  return {
    status,
    errorCode: representative.provider_sync_error_code ?? null,
    errorMessage: representative.provider_sync_error_message ?? null,
    lastSuccessfulRefreshAt:
      representative.provider_last_successful_refresh_at ?? representative.last_seen_at,
  };
}

function providerSyncFailureMessage(errorCode: string | null, safeMessage: string | null): string {
  if (safeMessage?.trim()) {
    return safeMessage.trim();
  }
  if (errorCode && providerSyncErrorFallbacks[errorCode]) {
    return providerSyncErrorFallbacks[errorCode];
  }
  return "ProjectX account refresh failed. Retry the refresh or check the signed-in user's provider configuration.";
}

export function describeAccountProviderSync(
  summary: AccountProviderSyncSummary | null,
): AccountProviderSyncNotice | null {
  if (summary === null) {
    return null;
  }

  const lastRefresh = formatProviderLastSeen(summary.lastSuccessfulRefreshAt);
  if (summary.status === "cached_fallback") {
    return {
      tone: "error",
      message: `${providerSyncFailureMessage(summary.errorCode, summary.errorMessage)} Saved ProjectX account data is still shown. ${lastRefresh}.`,
    };
  }
  if (summary.status === "cache_stale") {
    return {
      tone: "warning",
      message: `Showing aged saved ProjectX account data. ${lastRefresh}. Refresh ProjectX accounts before relying on balances or trading status.`,
    };
  }
  if (summary.status === "provider_fresh") {
    return {
      tone: "success",
      message: `ProjectX account data refreshed successfully. ${lastRefresh}.`,
    };
  }
  return {
    tone: "success",
    message: `Showing recently refreshed ProjectX account data. ${lastRefresh}.`,
  };
}

export function describeProviderRefreshException(error: unknown): string {
  const detail = (error as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object") {
    const errorCode = typeof (detail as { code?: unknown }).code === "string"
      ? (detail as { code: string }).code
      : null;
    const safeMessage = typeof (detail as { message?: unknown }).message === "string"
      ? (detail as { message: string }).message
      : null;
    return providerSyncFailureMessage(errorCode, safeMessage);
  }
  return providerSyncFailureMessage(
    error instanceof TypeError ? "projectx_network_error" : null,
    null,
  );
}

export function formatProviderLastSeen(lastSeenAt: string | null): string {
  if (!lastSeenAt) {
    return "Last seen time unavailable";
  }
  const date = new Date(lastSeenAt);
  if (Number.isNaN(date.getTime())) {
    return "Last seen time unavailable";
  }
  return `Last seen ${providerLastSeenFormatter.format(date)} ET`;
}
