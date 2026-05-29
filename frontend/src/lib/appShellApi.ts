import { accountsApi } from "./api";
import { getTradingDayRange, tradingDayKey } from "./tradingDay";

export interface TradeRefreshRange {
  start?: string;
  end?: string;
}

export function getSelectableAccounts() {
  return accountsApi.getSelectableAccounts();
}

export function getLatestTradesSyncRange(now: Date = new Date()): TradeRefreshRange {
  return getTradingDayRange(tradingDayKey(now)) ?? {};
}

export function refreshTrades(accountId: number, query: TradeRefreshRange = {}) {
  return accountsApi.refreshTrades(accountId, query);
}
