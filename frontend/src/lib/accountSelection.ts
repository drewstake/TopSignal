import { getBrowserStorageScope } from "./storageScope";

export const ACTIVE_ACCOUNT_STORAGE_KEY = "topsignal.activeAccountId";
export const MAIN_ACCOUNT_STORAGE_KEY = "topsignal.mainAccountId";
export const DEMO_RETURN_SNAPSHOT_STORAGE_KEY = "topsignal.demoReturnSnapshot";
export const DEMO_RETURN_SCOPE_STORAGE_KEY = "topsignal.demoReturnScope";
export const ACCOUNT_QUERY_PARAM = "account";
export const MAIN_ACCOUNT_UPDATED_EVENT = "topsignal.main-account-updated";
export const ACCOUNT_DISPLAY_NAME_UPDATED_EVENT = "topsignal.account-display-name-updated";
export const ACCOUNT_LIST_CHANGED_EVENT = "topsignal.account-list-changed";

export interface MainAccountUpdatedDetail {
  accountId: number;
}

export interface AccountDisplayNameUpdatedDetail {
  accountId: number;
}

export interface AccountListChangedDetail {
  accountId: number | null;
  action: "archived" | "unarchived" | "provider_refreshed";
  replacementAccountId: number | null;
}

export interface DemoReturnLocationInput {
  pathname: string;
  search?: string;
  hash?: string;
}

export interface DemoReturnSnapshot {
  accountId: number | null;
  path: string;
  scope: string;
}

let volatileDemoReturnSnapshot: DemoReturnSnapshot | null = null;

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

  return readAndMigrateStoredAccountId(ACTIVE_ACCOUNT_STORAGE_KEY);
}

export function writeStoredAccountId(accountId: number): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(scopedAccountStorageKey(ACTIVE_ACCOUNT_STORAGE_KEY), String(accountId));
  } catch {
    // Selection remains usable in the current render when storage is unavailable.
  }
}

export function readStoredMainAccountId(): number | null {
  if (typeof window === "undefined") {
    return null;
  }

  return readAndMigrateStoredAccountId(MAIN_ACCOUNT_STORAGE_KEY);
}

export function writeStoredMainAccountId(accountId: number): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(scopedAccountStorageKey(MAIN_ACCOUNT_STORAGE_KEY), String(accountId));
  } catch {
    // The event below still updates the active tab.
  }
  if (typeof window.dispatchEvent !== "function") {
    return;
  }
  window.dispatchEvent(
    new CustomEvent<MainAccountUpdatedDetail>(MAIN_ACCOUNT_UPDATED_EVENT, {
      detail: { accountId },
    }),
  );
}

function scopedAccountStorageKey(baseKey: string, scope = getBrowserStorageScope()): string {
  return `${baseKey}:${scope}`;
}

function readAndMigrateStoredAccountId(baseKey: string): number | null {
  try {
    const scope = getBrowserStorageScope();
    const scopedKey = scopedAccountStorageKey(baseKey, scope);
    const scopedValue = parseAccountId(window.localStorage.getItem(scopedKey));
    if (scopedValue !== null || scope === "demo") {
      return scopedValue;
    }

    // One-time live-only migration preserves an existing user's selection
    // without ever exposing an unscoped real account id to Demo Mode.
    const legacyValue = parseAccountId(window.localStorage.getItem(baseKey));
    if (legacyValue !== null) {
      window.localStorage.setItem(scopedKey, String(legacyValue));
      window.localStorage.removeItem(baseKey);
    }
    return legacyValue;
  } catch {
    return null;
  }
}

function demoReturnSnapshotKey(scope: string): string {
  return `${DEMO_RETURN_SNAPSHOT_STORAGE_KEY}:${scope}`;
}

function isValidReturnScope(scope: string | null): scope is string {
  return Boolean(scope && scope !== "demo" && /^(anonymous|user-[a-z0-9]+|session-[a-z0-9]+)$/.test(scope));
}

function parseReturnSnapshot(raw: string | null, liveScope: string): DemoReturnSnapshot | null {
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as Partial<DemoReturnSnapshot>;
    const accountId = parsed.accountId === null ? null : parseAccountId(String(parsed.accountId ?? ""));
    if (
      parsed.scope !== liveScope ||
      typeof parsed.path !== "string" ||
      !parsed.path.startsWith("/") ||
      parsed.path.startsWith("//") ||
      (parsed.accountId !== null && accountId === null)
    ) {
      return null;
    }
    return { accountId, path: parsed.path, scope: liveScope };
  } catch {
    return null;
  }
}

function safeReturnPath(location: DemoReturnLocationInput, accountId: number | null): string {
  const pathname = location.pathname.startsWith("/") && !location.pathname.startsWith("//")
    ? location.pathname
    : "/";
  const params = new URLSearchParams(location.search ?? "");
  if (accountId !== null) {
    params.set(ACCOUNT_QUERY_PARAM, String(accountId));
  }
  const query = params.toString();
  const hash = location.hash?.startsWith("#") ? location.hash : "";
  return `${pathname}${query ? `?${query}` : ""}${hash}`;
}

/**
 * Captures the exact live route/account before entering Demo Mode. The record
 * lives in this tab's authenticated browser lane, so another tab or signed-in
 * user cannot inherit it and Demo selections cannot overwrite it.
 */
export function captureLiveModeReturnSnapshot(
  location: DemoReturnLocationInput,
  accountId: number | null,
): DemoReturnSnapshot | null {
  if (typeof window === "undefined") {
    return null;
  }

  const scope = getBrowserStorageScope();
  if (scope === "demo") {
    return null;
  }
  const snapshot: DemoReturnSnapshot = {
    accountId,
    path: safeReturnPath(location, accountId),
    scope,
  };
  volatileDemoReturnSnapshot = snapshot;
  try {
    window.sessionStorage.setItem(demoReturnSnapshotKey(scope), JSON.stringify(snapshot));
    window.sessionStorage.setItem(DEMO_RETURN_SCOPE_STORAGE_KEY, scope);
    // Remove the pre-tab-isolation record once this tab has a current snapshot.
    window.localStorage.removeItem(demoReturnSnapshotKey(scope));
    window.localStorage.removeItem(DEMO_RETURN_SCOPE_STORAGE_KEY);
    return snapshot;
  } catch {
    return snapshot;
  }
}

/** Reads the current live session's return record, including while in Demo. */
export function readLiveModeReturnSnapshot(): DemoReturnSnapshot | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    // Demo Mode must not inspect the auth client. The opaque, non-secret scope
    // was captured atomically while still live and is sufficient to locate this
    // tab's matching return record.
    const tabScope = window.sessionStorage.getItem(DEMO_RETURN_SCOPE_STORAGE_KEY);
    if (isValidReturnScope(tabScope)) {
      const tabSnapshot = parseReturnSnapshot(
        window.sessionStorage.getItem(demoReturnSnapshotKey(tabScope)),
        tabScope,
      );
      if (tabSnapshot) {
        volatileDemoReturnSnapshot = tabSnapshot;
        return tabSnapshot;
      }
    }

    if (volatileDemoReturnSnapshot) {
      return volatileDemoReturnSnapshot;
    }

    // One-time migration for a tab that was already in Demo Mode during an
    // upgrade. New captures never write these shared legacy keys.
    const legacyScope = window.localStorage.getItem(DEMO_RETURN_SCOPE_STORAGE_KEY);
    if (!isValidReturnScope(legacyScope)) {
      return null;
    }
    const legacySnapshot = parseReturnSnapshot(
      window.localStorage.getItem(demoReturnSnapshotKey(legacyScope)),
      legacyScope,
    );
    window.localStorage.removeItem(demoReturnSnapshotKey(legacyScope));
    window.localStorage.removeItem(DEMO_RETURN_SCOPE_STORAGE_KEY);
    if (!legacySnapshot) {
      return null;
    }
    window.sessionStorage.setItem(demoReturnSnapshotKey(legacyScope), JSON.stringify(legacySnapshot));
    window.sessionStorage.setItem(DEMO_RETURN_SCOPE_STORAGE_KEY, legacyScope);
    volatileDemoReturnSnapshot = legacySnapshot;
    return legacySnapshot;
  } catch {
    return volatileDemoReturnSnapshot;
  }
}

export function clearLiveModeReturnSnapshot(): void {
  if (typeof window === "undefined") {
    return;
  }
  volatileDemoReturnSnapshot = null;
  try {
    const tabScope = window.sessionStorage.getItem(DEMO_RETURN_SCOPE_STORAGE_KEY);
    if (tabScope && tabScope !== "demo") {
      window.sessionStorage.removeItem(demoReturnSnapshotKey(tabScope));
    }
    window.sessionStorage.removeItem(DEMO_RETURN_SCOPE_STORAGE_KEY);

    const legacyScope = window.localStorage.getItem(DEMO_RETURN_SCOPE_STORAGE_KEY);
    if (legacyScope && legacyScope !== "demo") {
      window.localStorage.removeItem(demoReturnSnapshotKey(legacyScope));
    }
    window.localStorage.removeItem(DEMO_RETURN_SCOPE_STORAGE_KEY);
  } catch {
    // Sign-out and mode transitions remain safe when storage is unavailable.
  }
}

export function buildAccountAwarePath(path: string, accountId: number | null): string {
  if (accountId === null) {
    return path;
  }
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}${ACCOUNT_QUERY_PARAM}=${encodeURIComponent(String(accountId))}`;
}

export function dispatchAccountDisplayNameUpdated(accountId: number): void {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") {
    return;
  }
  window.dispatchEvent(
    new CustomEvent<AccountDisplayNameUpdatedDetail>(ACCOUNT_DISPLAY_NAME_UPDATED_EVENT, {
      detail: { accountId },
    }),
  );
}

export function dispatchAccountListChanged(detail: AccountListChangedDetail): void {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") {
    return;
  }
  window.dispatchEvent(
    new CustomEvent<AccountListChangedDetail>(ACCOUNT_LIST_CHANGED_EVENT, {
      detail,
    }),
  );
}
