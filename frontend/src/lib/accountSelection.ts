export const ACTIVE_ACCOUNT_STORAGE_KEY = "topsignal.activeAccountId";
export const MAIN_ACCOUNT_STORAGE_KEY = "topsignal.mainAccountId";
export const ACCOUNT_QUERY_PARAM = "account";
export const MAIN_ACCOUNT_UPDATED_EVENT = "topsignal.main-account-updated";

export interface MainAccountUpdatedDetail {
  accountId: number;
}

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

export function readStoredMainAccountId(): number | null {
  if (typeof window === "undefined") {
    return null;
  }

  return parseAccountId(window.localStorage.getItem(MAIN_ACCOUNT_STORAGE_KEY));
}

export function writeStoredMainAccountId(accountId: number): void {
  if (typeof window === "undefined") {
    return;
  }

  window.localStorage.setItem(MAIN_ACCOUNT_STORAGE_KEY, String(accountId));
  if (typeof window.dispatchEvent !== "function") {
    return;
  }
  window.dispatchEvent(
    new CustomEvent<MainAccountUpdatedDetail>(MAIN_ACCOUNT_UPDATED_EVENT, {
      detail: { accountId },
    }),
  );
}
