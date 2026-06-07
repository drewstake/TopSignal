import { accountsApi } from "./api";

export interface TradeRefreshRange {
  start?: string;
  end?: string;
}

export function getSelectableAccounts() {
  return accountsApi.getSelectableAccounts();
}

export function refreshTrades(accountId: number, query: TradeRefreshRange = {}) {
  return accountsApi.refreshTrades(accountId, query);
}
