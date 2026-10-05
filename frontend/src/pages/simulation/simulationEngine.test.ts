import { describe, expect, it } from 'vitest';
import { accountSummary, addAccount, advanceClock, cancelAll, createExchange, processStops, submitOrder } from './simulationExchange';
import { aggregateExecution, MAX_CANDLES, TIMEFRAMES, type Direction } from './simulationModel';
function exchange() {
  const s = createExchange();
  for (const id of ['a', 'b', 'c', 'taker']) addAccount(s, id, 1000000, 1000, 1);
  return s;
}
const limit = (s: ReturnType<typeof exchange>, owner: string, direction: Direction, price: number, quantity: number) => submitOrder(s, owner, { contract: 'BX', direction, price, quantity, type: 'Limit' })!;
const market = (s: ReturnType<typeof exchange>, owner: string, direction: Direction, quantity: number) => submitOrder(s, owner, { contract: 'BX', direction, quantity, type: 'Market' })!;

describe('Price/time exchange', () => {
  it('sweeps best price then FIFO at resting prices, including partial fills', () => {
    const s = exchange(); const worse = limit(s, 'a', -1, 5826, 5);
    const first = limit(s, 'b', -1, 5825, 3), second = limit(s, 'c', -1, 5825, 4);
    const result = market(s, 'taker', 1, 9);
    expect(s.trades.map(t => [t.seller, t.ticks * .25, t.quantity])).toEqual([['b', 5825, 3], ['c', 5825, 4], ['a', 5826, 2]]);
    expect(result.filled).toBe(9); expect(s.orders[worse.id].remaining).toBe(3);
    expect(s.orders[first.id]).toBeUndefined(); expect(s.orders[second.id]).toBeUndefined(); expect(s.books.BX.volume).toBe(9);
  });
  it('respects limit boundaries, retains limits and cancels market remainders', () => {
    const s = exchange(); limit(s, 'a', -1, 5825, 2); limit(s, 'b', -1, 5826, 3);
    const resting = limit(s, 'taker', 1, 5825.5, 6);
    expect(resting.filled).toBe(2); expect(resting.remaining).toBe(4); expect(s.orders[resting.id]).toBe(resting);
    expect(s.trades[0].ticks * .25).toBe(5825);
    const ioc = market(s, 'c', 1, 10);
    expect(ioc.filled).toBe(3); expect(ioc.cancelled).toBe(7); expect(ioc.remaining).toBe(0); expect(s.orders[ioc.id]).toBeUndefined();
  });
  it('cancels the older self quote without printing and replacement loses priority', () => {
    const s = exchange(); limit(s, 'taker', -1, 5825, 3); limit(s, 'a', -1, 5825, 4); market(s, 'taker', 1, 2);
    expect(s.trades).toHaveLength(1); expect(s.trades[0].seller).toBe('a'); expect(s.books.BX.volume).toBe(2);
    cancelAll(s, 'a'); limit(s, 'b', -1, 5825, 1); limit(s, 'a', -1, 5825, 1);
    market(s, 'taker', 1, 1); expect(s.trades.at(-1)?.seller).toBe('b');
  });
  it('expires quotes without changing last price or candles', () => {
    const s = exchange(); submitOrder(s, 'a', { contract: 'BG', direction: 1, quantity: 2, type: 'Limit', price: 2384.5, expires: .25 }); advanceClock(s);
    expect(Object.keys(s.orders)).toHaveLength(0); expect(s.books.BG.last).toBeNull(); expect(s.books.BG.candles['1m']).toEqual([]);
  });
  it('does not clamp traded prices to the fair-value bounds', () => {
    const s = exchange(); let price = 5824.75;
    for (let i = 0; i < 8; i++) {
      price = Math.round(price * 1.05 * 4) / 4;
      expect(limit(s, 'a', -1, price, 1)).not.toBeNull(); market(s, 'taker', 1, 1);
    }
    expect(s.books.BX.last! * .25).toBeGreaterThan(5824.75 * 1.4);
  });
  it('rejects off-tick orders and reserves worst-case margin and inventory capacity', () => {
    const s = exchange(); const a = s.accounts.a; a.limit = 5; a.balance = 3000; a.risk = .5;
    expect(limit(s, 'a', 1, 5824.13, 1)).toBeNull(); expect(limit(s, 'a', 1, 5824, 2)).not.toBeNull();
    expect(limit(s, 'a', 1, 5823, 1)).toBeNull(); expect(limit(s, 'a', -1, 5826, 2)).not.toBeNull();
    expect(limit(s, 'a', -1, 5827, 3)).toBeNull();
  });
  it('accounts for shorts, partial closes and reversals using contract multipliers', () => {
    const s = exchange(); limit(s, 'a', -1, 5825, 4); market(s, 'taker', 1, 4);
    expect(s.accounts.a.positions.BX).toMatchObject({ direction: -1, quantity: 4, entry: 5825 }); expect(s.accounts.taker.balance).toBe(1000000);
    limit(s, 'b', 1, 5826, 6); market(s, 'taker', -1, 6);
    expect(s.accounts.taker.realized).toBe(200); expect(s.accounts.taker.balance).toBe(1000200);
    expect(s.accounts.taker.positions.BX).toMatchObject({ direction: -1, quantity: 2, entry: 5826 });
    expect(Object.values(s.accounts).reduce((n, a) => n + a.cashFlow, 0)).toBe(0);
    expect(Object.values(s.accounts).reduce((n, a) => n + (a.positions.BX?.quantity ?? 0) * (a.positions.BX?.direction ?? 1), 0)).toBe(0);
    expect(Object.values(s.accounts).reduce((n, a) => n + accountSummary(s, a).equity - a.base, 0)).toBeCloseTo(0);
  });
  it('latches protective exits on executions and retries a partial stop until flat', () => {
    const s = exchange(); limit(s, 'a', -1, 5825, 4);
    submitOrder(s, 'taker', { contract: 'BX', direction: 1, quantity: 4, type: 'Market', stopTicks: 4, targetTicks: 8 });
    limit(s, 'b', 1, 5824, 3); market(s, 'c', -1, 1);
    expect(s.accounts.taker.positions.BX?.triggered).toBe('Stop loss');
    processStops(s); expect(s.accounts.taker.positions.BX?.quantity).toBe(2); expect(s.books.BX.volume).toBe(7);
    limit(s, 'b', 1, 5823.75, 2); advanceClock(s);
    expect(s.accounts.taker.positions.BX).toBeUndefined(); expect(s.books.BX.volume).toBe(9);
  });
  it('liquidates margin failures through the book and locks depleted accounts', () => {
    const s = exchange(); limit(s, 'a', -1, 5825, 4); market(s, 'taker', 1, 4);
    s.accounts.taker.balance = 1000; limit(s, 'b', 1, 5824.75, 4); advanceClock(s);
    expect(s.accounts.taker.positions.BX).toBeUndefined(); expect(s.accounts.taker.realized).toBe(-50);
    s.accounts.c.balance = 0; advanceClock(s); expect(s.accounts.c.locked).toBe(true); expect(market(s, 'c', 1, 1)).toBeNull();
  });
});
describe('Execution candles', () => {
  it('aggregates 100 → 103 → 99 → 102 with volume counted once', () => {
    const b = createExchange().books.BX;
    [100, 103, 99, 102].forEach((p, i) => aggregateExecution(b, i, p, i + 1));
    expect(b.candles['1m']).toEqual([{ time: 0, open: 100, high: 103, low: 99, close: 102, volume: 10 }]);
    for (const [tf] of TIMEFRAMES) expect(b.candles[tf]).toEqual(b.candles['1m']);
  });
  it('opens at the first trade, skips empty windows, respects boundaries and freezes completed bars', () => {
    const b = createExchange().books.BX;
    aggregateExecution(b, 59.75, 100, 2); const completed = b.candles['1m'][0];
    aggregateExecution(b, 60, 103, 3); aggregateExecution(b, 180, 99, 4);
    expect(b.candles['1m'].map(c => c.time)).toEqual([0, 60, 180]); expect(b.candles['1m'][1]).toMatchObject({ open: 103, low: 103 });
    expect(completed).toEqual({ time: 0, open: 100, high: 100, low: 100, close: 100, volume: 2 });
    expect(b.candles['5m'][0]).toEqual({ time: 0, open: 100, high: 103, low: 99, close: 99, volume: 9 });
    aggregateExecution(b, 300, 110, 1); expect(b.candles['5m'][1].open).toBe(110);
  });
  it('bounds every timeframe independently', () => {
    const b = createExchange().books.BX;
    for (let i = 0; i <= MAX_CANDLES; i++) aggregateExecution(b, i * 86400, 100 + i, 1);
    for (const [tf] of TIMEFRAMES) { expect(b.candles[tf]).toHaveLength(MAX_CANDLES); expect(b.candles[tf][0].time).toBe(86400); }
  });
  it.each(TIMEFRAMES)('handles the exact %s boundary without carrying the previous close', (tf, seconds) => {
    const b = createExchange().books.BX;
    aggregateExecution(b, seconds - .25, 100, 1); aggregateExecution(b, seconds, 103, 2);
    expect(b.candles[tf]).toEqual([
      { time: 0, open: 100, high: 100, low: 100, close: 100, volume: 1 },
      { time: seconds, open: 103, high: 103, low: 103, close: 103, volume: 2 },
    ]);
  });
});
