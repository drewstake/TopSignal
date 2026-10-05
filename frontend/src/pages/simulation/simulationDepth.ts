import type { ParticipantKind, ParticipantOrder } from './simulationPreview';

export interface DepthLevel {
  price: number;
  contracts: number;
  orders: number;
  cumulative: number;
  byKind: Partial<Record<ParticipantKind, number>>;
}

export function buildSimulationDepth(book: readonly ParticipantOrder[], tickSize = 0.25) {
  const levels = { buy: new Map<number, DepthLevel>(), sell: new Map<number, DepthLevel>() };
  for (const order of book) {
    if (order.quantity <= 0) continue;
    const ticks = Math.round(order.price / tickSize);
    const side = levels[order.side];
    const level = side.get(ticks) ?? { price: ticks * tickSize, contracts: 0, orders: 0, cumulative: 0, byKind: {} };
    level.contracts += order.quantity;
    level.orders += 1;
    level.byKind[order.kind] = (level.byKind[order.kind] ?? 0) + order.quantity;
    side.set(ticks, level);
  }
  const sorted = (side: 'buy' | 'sell') => {
    const result = [...levels[side].values()].sort((a, b) => side === 'buy' ? b.price - a.price : a.price - b.price);
    let total = 0;
    return result.map((level) => ({ ...level, cumulative: total += level.contracts }));
  };
  const bids = sorted('buy');
  const asks = sorted('sell');
  return {
    bids, asks,
    bidContracts: bids.at(-1)?.cumulative ?? 0,
    askContracts: asks.at(-1)?.cumulative ?? 0,
    maxLevelContracts: Math.max(1, ...bids.map((level) => level.contracts), ...asks.map((level) => level.contracts)),
    spread: bids.length && asks.length ? Math.round((asks[0].price - bids[0].price) / tickSize) * tickSize : null,
  };
}

export const LADDER_RADIUS = 10;
// Include empty ticks so cancellations and fills never shift neighboring rows.
export function buildPriceLadder(book: readonly ParticipantOrder[], centerTicks: number, tickSize = 0.25) {
  const depth = buildSimulationDepth(book, tickSize);
  const bids = new Map(depth.bids.map(level => [Math.round(level.price / tickSize), level]));
  const asks = new Map(depth.asks.map(level => [Math.round(level.price / tickSize), level]));
  const center = Math.max(LADDER_RADIUS + 1, Math.round(centerTicks));
  const populated = [...depth.bids, ...depth.asks];
  const average = populated.reduce((sum, l) => sum + l.contracts, 0) / Math.max(1, populated.length);
  return { ...depth, wallThreshold: Math.max(50, average * 1.8),
    rows: Array.from({ length: LADDER_RADIUS * 2 + 1 }, (_, index) => {
      const ticks = center + LADDER_RADIUS - index;
      return { ticks, price: ticks * tickSize, bid: bids.get(ticks), ask: asks.get(ticks) };
    }) };
}
