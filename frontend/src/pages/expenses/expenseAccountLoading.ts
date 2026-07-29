export interface ExpenseReconciliationAccountOptions {
  showInactive: true;
  showMissing: false;
  bypassCache: true;
  refreshProvider: true;
}

/**
 * Reconciliation can create and delete financial records, so a local-first or
 * timed account-list cache is not authoritative enough. Always force the
 * normal signed-in ProjectX refresh path and let the reconciler verify the
 * returned per-account sync health before it mutates anything.
 */
export function loadFreshAccountsForExpenseReconciliation<T>(
  getAccounts: (options: ExpenseReconciliationAccountOptions) => Promise<T[]>,
): Promise<T[]> {
  return getAccounts({
    showInactive: true,
    showMissing: false,
    bypassCache: true,
    refreshProvider: true,
  });
}
