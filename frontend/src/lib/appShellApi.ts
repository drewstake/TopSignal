import { accountsApi } from "./api";

export function getSelectableAccounts() {
  return accountsApi.getSelectableAccounts();
}

export function refreshTrades(accountId: number, query: { start?: string; end?: string } = {}) {
  return accountsApi.refreshTrades(accountId, query);
}
