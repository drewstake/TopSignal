export const ACCOUNT_TRADES_SYNCED_EVENT = "account-trades-synced";

export interface AccountTradesSyncedDetail {
  accountId: number;
  fetchedCount: number;
  insertedCount: number;
  error?: string;
}
