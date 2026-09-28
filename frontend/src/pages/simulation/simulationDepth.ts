import type { ParticipantKind, ParticipantOrder } from './simulationPreview';

export interface DepthLevel {
  price: number;
  shares: number;
  orders: number;
  cumulative: number;
  byKind: Partial<Record<ParticipantKind, number>>;
}

export function buildSimulationDepth(book: readonly ParticipantOrder[]) {
  const levels = { buy: new Map<number, DepthLevel>(), sell: new Map<number, DepthLevel>() };
  for (const order of book) {
    if (order.quantity <= 0) continue;
    const cents = Math.round(order.price * 100);
    const side = levels[order.side];
    const level = side.get(cents) ?? { price: cents / 100, shares: 0, orders: 0, cumulative: 0, byKind: {} };
    level.shares += order.quantity;
    level.orders += 1;
    level.byKind[order.kind] = (level.byKind[order.kind] ?? 0) + order.quantity;
    side.set(cents, level);
  }
  const sorted = (side: 'buy' | 'sell') => {
    const result = [...levels[side].values()].sort((a, b) => side === 'buy' ? b.price - a.price : a.price - b.price);
    let total = 0;
    return result.map((level) => ({ ...level, cumulative: total += level.shares }));
  };
  const bids = sorted('buy');
  const asks = sorted('sell');
  return {
    bids, asks,
    bidShares: bids.at(-1)?.cumulative ?? 0,
    askShares: asks.at(-1)?.cumulative ?? 0,
    maxLevelShares: Math.max(1, ...bids.map((level) => level.shares), ...asks.map((level) => level.shares)),
    spread: bids.length && asks.length ? (Math.round(asks[0].price * 100) - Math.round(bids[0].price * 100)) / 100 : null,
  };
}
