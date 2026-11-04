export type MarketTradeSide = "buy" | "sell" | "unknown";
export interface MarketTradePrint {
  id: string;
  timestamp: string;
  price: number;
  volume: number;
  side: MarketTradeSide;
}
export interface MarketTradeBatch {
  contract_id: string;
  trades: MarketTradePrint[];
  dropped: number;
}

export function parseMarketTradeBatch(value: unknown): MarketTradeBatch | null {
  if (!value || typeof value !== "object") return null;
  const batch = value as Partial<MarketTradeBatch>;
  if (typeof batch.contract_id !== "string" || !Array.isArray(batch.trades) || batch.trades.length > 256 ||
      !Number.isSafeInteger(batch.dropped) || batch.dropped! < 0) return null;
  const trades: MarketTradePrint[] = [];
  let dropped = batch.dropped!;
  for (const item of batch.trades) {
    if (!item || typeof item.id !== "string" || !item.id || item.id.length > 160 ||
        typeof item.timestamp !== "string" || !Number.isFinite(Date.parse(item.timestamp)) ||
        !/(Z|[+-]\d\d:\d\d)$/.test(item.timestamp) ||
        typeof item.price !== "number" || !Number.isFinite(item.price) || item.price <= 0 ||
        typeof item.volume !== "number" || !Number.isFinite(item.volume) || item.volume <= 0) {
      dropped++; continue;
    }
    trades.push({ id: item.id, timestamp: item.timestamp, price: item.price, volume: item.volume,
      side: item.side === "buy" || item.side === "sell" ? item.side : "unknown" });
  }
  return { contract_id: batch.contract_id, trades, dropped };
}
