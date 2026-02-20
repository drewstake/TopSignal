export const ACTIVE_ACCOUNT_STORAGE_KEY = "topsignal.activeAccountId";
export const ACCOUNT_QUERY_PARAM = "account";

export function parseAccountId(rawValue: string | null): number | null {
  if (rawValue === null) {
    return null;
  }

  const parsed = Number(rawValue);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    return null;
  }

  return parsed;
}

export function readStoredAccountId(): number | null {
  if (typeof window === "undefined") {
    return null;
  }

  return parseAccountId(window.localStorage.getItem(ACTIVE_ACCOUNT_STORAGE_KEY));
}

export function writeStoredAccountId(accountId: number): void {
  if (typeof window === "undefined") {
    return;
  }

  window.localStorage.setItem(ACTIVE_ACCOUNT_STORAGE_KEY, String(accountId));
}
