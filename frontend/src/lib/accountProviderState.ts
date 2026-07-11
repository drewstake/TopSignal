import type { AccountInfo } from "./types";
import { formatCurrency } from "../utils/formatters";

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
