export const RECENT_TRADES_RENDER_PAGE_SIZE = 50;

export function clampRecentTradesVisibleCount(totalCount: number, requestedCount: number) {
  const safeTotal = Math.max(0, Math.trunc(totalCount));
  const safeRequested = Math.max(RECENT_TRADES_RENDER_PAGE_SIZE, Math.trunc(requestedCount));
  return Math.min(safeTotal, safeRequested);
}

export function getNextRecentTradesVisibleCount(currentCount: number, totalCount: number) {
  return clampRecentTradesVisibleCount(totalCount, currentCount + RECENT_TRADES_RENDER_PAGE_SIZE);
}

export function getVisibleRecentTrades<T>(trades: readonly T[], requestedCount: number): T[] {
  return trades.slice(0, clampRecentTradesVisibleCount(trades.length, requestedCount));
}
