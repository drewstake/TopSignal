import { accountsApi, type SelectableAccountsOptions } from "./api";

export interface TradeRefreshRange {
  start?: string;
  end?: string;
}

export function getSelectableAccounts(options: SelectableAccountsOptions = {}) {
  return accountsApi.getSelectableAccounts(options);
}

export function getSelectableAccountsLocalFirst() {
  return accountsApi.getSelectableAccountsLocalFirst();
}

export function refreshTrades(accountId: number, query: TradeRefreshRange = {}) {
  return accountsApi.refreshTrades(accountId, query);
}
