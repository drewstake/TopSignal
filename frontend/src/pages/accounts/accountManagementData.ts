import type { AccountInfo } from "../../lib/types";

export interface AccountManagementLoadOptions {
  showInactive: boolean;
  showMissing: boolean;
  refreshProvider: boolean;
  bypassCache: boolean;
  includeArchived: boolean;
}

export type AccountManagementLoader = (
  options: AccountManagementLoadOptions,
) => Promise<AccountInfo[]>;

export function loadAccountManagementRows(
  loader: AccountManagementLoader,
  refreshProvider: boolean,
) {
  return loader({
    showInactive: true,
    showMissing: true,
    refreshProvider,
    bypassCache: refreshProvider,
    includeArchived: true,
  });
}

export function filterAccountManagementRows(
  accounts: AccountInfo[],
  {
    showHidden,
    showMissing,
    showArchived,
  }: {
    showHidden: boolean;
    showMissing: boolean;
    showArchived: boolean;
  },
) {
  return accounts.filter(
    (account) =>
      (showHidden || account.account_state !== "HIDDEN") &&
      (showMissing || account.account_state !== "MISSING") &&
      (showArchived || !account.is_archived),
  );
}
