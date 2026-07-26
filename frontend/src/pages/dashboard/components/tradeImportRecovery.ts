const PENDING_IMPORT_STORAGE_PREFIX = "topsignal:pending-trade-import";

function pendingImportStorageKey(accountId: number): string {
  return `${PENDING_IMPORT_STORAGE_PREFIX}:${accountId}`;
}

export function rememberPendingTradeImport(accountId: number, previewToken: string): void {
  try {
    window.sessionStorage.setItem(pendingImportStorageKey(accountId), previewToken);
  } catch {
    // Recovery remains available during this mounted session if storage is disabled.
  }
}

export function readPendingTradeImport(accountId: number): string | null {
  try {
    return window.sessionStorage.getItem(pendingImportStorageKey(accountId));
  } catch {
    return null;
  }
}

export function clearPendingTradeImport(accountId: number, previewToken?: string): void {
  try {
    const key = pendingImportStorageKey(accountId);
    if (previewToken && window.sessionStorage.getItem(key) !== previewToken) {
      return;
    }
    window.sessionStorage.removeItem(key);
  } catch {
    // Storage may be unavailable; there is no durable marker to clear.
  }
}
