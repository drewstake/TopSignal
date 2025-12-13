import { topstepPost } from "./topstepClient";

export type TopstepTrade = {
  id: number;
  accountId: number;
  contractId: string;
  creationTimestamp: string; // ISO string
  price: number;
  profitAndLoss: number | null; // null = half-turn per docs
  fees: number;
  side: number; // 1 = buy, 0 = sell (per examples)
  size: number;
  voided: boolean;
  orderId: number;
};

export type TradeSearchResponse = {
  trades: TopstepTrade[];
  success: boolean;
  errorCode: number;
  errorMessage: string | null;
};

export async function searchTrades(args: {
  accountId: number;
  startTimestamp: string;
  endTimestamp?: string | null;
  cacheTtlMs?: number;
  forceRefresh?: boolean;
}) {
  return topstepPost<TradeSearchResponse>(
    "/api/Trade/search",
    {
      accountId: args.accountId,
      startTimestamp: args.startTimestamp,
      endTimestamp: args.endTimestamp ?? null,
    },
    {
      cacheTtlMs: args.cacheTtlMs,
      forceRefresh: args.forceRefresh,
    }
  );
}
