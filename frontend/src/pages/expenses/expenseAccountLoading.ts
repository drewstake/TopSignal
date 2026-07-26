export interface ExpenseReconciliationAccountOptions {
  showInactive: true;
  showMissing: false;
  bypassCache: false;
  refreshProvider: false;
}

export function loadLocalAccountsForExpenseReconciliation<T>(
  getAccounts: (options: ExpenseReconciliationAccountOptions) => Promise<T[]>,
): Promise<T[]> {
  return getAccounts({
    showInactive: true,
    showMissing: false,
    bypassCache: false,
    refreshProvider: false,
  });
}
