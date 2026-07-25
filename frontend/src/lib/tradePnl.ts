export interface TradePnlInput {
  pnl: number | null | undefined;
  fees: number | null | undefined;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function getTradeNetPnl(trade: TradePnlInput): number | null {
  if (!isFiniteNumber(trade.pnl)) {
    return null;
  }

  const fees = isFiniteNumber(trade.fees) && trade.fees > 0 ? trade.fees : 0;
  const netPnl = trade.pnl - fees;
  return Number.isFinite(netPnl) ? netPnl : null;
}
