import { describe, expect, it } from 'vitest';
import { EXECUTION, INSTRUMENTS, PLAYER, SETTINGS, SPECS, TIMEFRAMES, contractLimit, protection, type Direction, type Instrument, type Order, type OrderRequest, type State } from './model';
import { addAccount, advanceClock, amendProtection, beginReset, cancelAll, cancelOrder, closePosition, createExchange, depth, executionBudget, flatten, playerCapacity, processQueue, processStops, submitOrder, summary } from './exchange';
import { SimulationClock } from './runtime';

function setup() {
  const s = createExchange(0); addAccount(s, PLAYER, 10000, 10, .8, true);
  for (const id of ['seller', 'seller2', 'buyer']) addAccount(s, id, 1e9, 1000000, .8);
  return s;
}
function order(s: State, owner: string, r: OrderRequest) { const result = submitOrder(s, owner, r); if (!result.ok) throw new Error(result.error); return result.order; }
function quote(s: State, owner: string, side: Direction, qty: number, ticks: number, key: Instrument = 'BX') {
  return order(s, owner, { contract: key, type: 'Limit', price: ticks * SPECS[key].tick, direction: side, quantity: qty });
}
function liquid(s: State, key: Instrument = 'BX', qty = 200) { const ref = s.books[key].reference; quote(s, 'seller', -1, qty, ref + 1, key); quote(s, 'buyer', 1, qty, ref - 1, key); }
function buy(s: State, qty = 1, extra: Partial<OrderRequest> = {}) { return order(s, PLAYER, { contract: 'BX', direction: 1, quantity: qty, type: 'Market', ...extra }); }

describe('finite Get-Funded exchange', () => {
  it('has exact economics, initial player balance and cent-rounded capacity without a margin gate', () => {
    const s = setup(); const a = s.accounts[PLAYER];
    expect(summary(s, a)).toMatchObject({ balance: 10000, equity: 10000, realized: 0, unrealized: 0 });
    expect(INSTRUMENTS.map(k => SPECS[k].tick * SPECS[k].multiplier)).toEqual([12.5, 5, 10]);
    expect(INSTRUMENTS.map(k => SPECS[k].last)).toEqual([5824.75, 20485.5, 2384.6]);
    expect([9999.99, 9999.999, 10000, 4999, 123456].map(contractLimit)).toEqual([9, 10, 10, 5, 123]);
    a.risk = 0; liquid(s); expect(buy(s, 10).filled).toBe(10);
    expect(a.positions.BX?.quantity).toBe(10);
  });
  it('uses price/time priority, resting prices, partial fills and equal double-entry settlement', () => {
    const s = setup(), ref = s.books.BX.reference;
    const first = quote(s, 'seller', -1, 2, ref + 1), second = quote(s, 'seller2', -1, 3, ref + 1);
    quote(s, 'seller', -1, 100, ref + 2);
    const result = buy(s, 4);
    expect(result.filled).toBe(4); expect(first.filled).toBe(2); expect(second.filled).toBe(2); expect(second.remaining).toBe(1);
    expect(s.trades.map(t => t.resting)).toEqual([first.id, second.id]);
    expect(result.notional / result.filled).toBe((ref + 1) * SPECS.BX.tick);
    expect(Object.values(s.accounts).reduce((n, a) => n + (a.positions.BX?.quantity ?? 0) * (a.positions.BX?.direction ?? 1), 0)).toBe(0);
    expect(Object.values(s.accounts).reduce((n, a) => n + a.cashFlow, 0)).toBeCloseTo(0, 8);
    expect(s.totalVolume).toBe(4); expect(cancelOrder(s, 'seller2', second.id)).toBe(true); expect(second).toMatchObject({ filled: 2, remaining: 0, cancelled: 1 });
  });
  it('reserves positions, limits and off-book stops across markets in both directions', () => {
    const s = setup(), a = s.accounts[PLAYER]; liquid(s); buy(s, 4);
    order(s, PLAYER, { contract: 'BT', direction: 1, quantity: 3, type: 'Limit', price: SPECS.BT.last - 10 });
    const stop = order(s, PLAYER, { contract: 'BG', direction: -1, quantity: 3, type: 'Stop', price: SPECS.BG.last - 1 });
    expect(stop.status).toBe('Armed'); expect(s.books.BG.asks).not.toContain(stop.id);
    expect(playerCapacity(s, a, 'BX', 1)).toBe(0); expect(playerCapacity(s, a, 'BX', -1)).toBe(8);
    expect(submitOrder(s, PLAYER, { contract: 'BX', direction: 1, quantity: 1, type: 'Market' }).ok).toBe(false);
    cancelOrder(s, PLAYER, stop.id); expect(playerCapacity(s, a, 'BG', 1)).toBe(3);
    s.books.BX.last! += 100; expect(contractLimit(a.balance)).toBe(10); // open P&L is not capacity
  });
  it('rejects invalid quantities, off-tick prices, wrong-side stops, price bands and excess orders', () => {
    const s = setup();
    for (const quantity of [0, -1, 1.5, NaN, Infinity]) expect(submitOrder(s, PLAYER, { contract: 'BX', direction: 1, quantity, type: 'Market' }).ok).toBe(false);
    for (const price of [5824.8, 7000, NaN]) expect(submitOrder(s, PLAYER, { contract: 'BX', direction: 1, quantity: 1, type: 'Limit', price }).ok).toBe(false);
    expect(submitOrder(s, PLAYER, { contract: 'BX', direction: 1, quantity: 1, type: 'Stop', price: 5824.5 }).ok).toBe(false);
    s.accounts[PLAYER].base = s.accounts[PLAYER].balance = 200000;
    for (let i = 0; i < 16; i++) order(s, PLAYER, { contract: 'BX', direction: 1, quantity: 1, type: 'Limit', price: 5800 });
    expect(submitOrder(s, PLAYER, { contract: 'BX', direction: 1, quantity: 1, type: 'Limit', price: 5800 }).ok).toBe(false);
  });
  it('does not apply the legacy 100-contract ceiling', () => {
    const s = setup(); s.accounts[PLAYER].base = s.accounts[PLAYER].balance = 150000; liquid(s, 'BX', 5000);
    expect(buy(s, 150).filled).toBe(150);
  });
  it('cancels market remainders, preflights crossed limits atomically, and never invents liquidity', () => {
    const s = setup(); liquid(s, 'BX', 20);
    const rejected = submitOrder(s, PLAYER, { contract: 'BX', direction: 1, quantity: 3, type: 'Limit', price: SPECS.BX.last + .25 });
    expect(rejected.ok).toBe(false); expect(s.trades).toHaveLength(0);
    const result = buy(s, 5); expect(result).toMatchObject({ filled: 2, remaining: 0, cancelled: 3, status: 'Partial / IOC' });
    expect(s.books.BX.volume).toBe(2);
    cancelAll(s, 'seller'); const empty = buy(s); expect(empty.filled).toBe(0); expect(s.books.BX.volume).toBe(2); expect(depth(s, 'BX', -1)).toEqual([]);
  });
  it('enforces fixed window owner and market budgets across instruments, splitting and cancellation', () => {
    const s = setup(); liquid(s, 'BX', 1000); liquid(s, 'BT', 1000);
    buy(s, 1); const ownerKey = `${PLAYER}:1`; s.execution.owners[ownerKey] = EXECUTION.owner;
    const bx = buy(s, 1); expect(bx.filled).toBe(0);
    const bt = order(s, PLAYER, { contract: 'BT', direction: 1, quantity: 1, type: 'Market' }); expect(bt.filled).toBe(0);
    cancelAll(s, PLAYER); expect(s.execution.owners[ownerKey]).toBe(EXECUTION.owner);
    s.time = 17.75; expect(executionBudget(s, bx)).toBe(0);
    s.time = 18; expect(executionBudget(s, bx)).toBeGreaterThan(0);
  });
  it('rechecks actual non-player depth and applies directional collars to every aggressor', () => {
    const s = setup(), ref = s.books.BX.reference;
    quote(s, 'seller', -1, 100, ref + 20); const result = buy(s, 1); expect(result.filled).toBe(1);
    s.books.BX.fair = ref - 1;
    expect(order(s, 'buyer', { contract: 'BX', direction: 1, quantity: 1, type: 'Market' }).filled).toBe(0);
    const count = s.trades.length;
    expect(buy(s).filled).toBe(0); expect(s.trades).toHaveLength(count);
    cancelAll(s, 'seller'); s.books.BX.fair = ref; quote(s, 'seller', -1, 5, ref + 1);
    expect(executionBudget(s, result)).toBe(0);
    expect(EXECUTION.guard * SPECS.BT.scale).toBe(60);
    // A sell accepts any better (higher) execution price, even far above fair.
    quote(s, 'buyer', 1, 100, ref - 2); s.books.BX.fair = ref - 100;
    expect(order(s, 'seller2', { contract: 'BX', direction: -1, quantity: 1, type: 'Market' }).filled).toBe(1);
  });
  it('prevents self-trading and expires orders without trades or candles', () => {
    const s = setup(), ref = s.books.BX.reference;
    const self = quote(s, 'seller', -1, 3, ref + 1);
    const buySelf = order(s, 'seller', { contract: 'BX', direction: 1, quantity: 2, type: 'Market' });
    expect(self.status).toBe('Self-trade prevented'); expect(buySelf.filled).toBe(0); expect(s.trades).toHaveLength(0);
    const timed = order(s, 'buyer', { contract: 'BX', direction: 1, quantity: 2, type: 'Limit', price: SPECS.BX.last - 1, expires: .25 });
    advanceClock(s); expect(timed.status).toBe('Expired'); expect(s.books.BX.candles['1m']).toEqual([]);
  });
  it('derives every candle and volume solely from executions and skips empty intervals', () => {
    const s = setup(); liquid(s); buy(s, 2); s.time = 120; buy(s, 1);
    for (const [tf] of TIMEFRAMES) {
      expect(s.books.BX.candles[tf].reduce((n, c) => n + c.volume, 0)).toBe(3);
      expect(s.books.BX.candleVolume[tf]).toBe(3);
    }
    expect(s.books.BX.candles['1m'].map(c => c.time)).toEqual([0, 120]);
    expect(s.books.BX.last).toBe(s.trades.at(-1)?.ticks);
  });
  it('uses weighted entry, reduces then reverses, and settles realized/unrealized P&L without fees', () => {
    const s = setup(); liquid(s); buy(s, 2);
    cancelAll(s, 'seller'); quote(s, 'seller2', -1, 200, s.books.BX.reference + 3); buy(s, 2);
    const a = s.accounts[PLAYER], entry = SPECS.BX.last + .5;
    expect(a.positions.BX?.entry).toBe(entry);
    expect(summary(s, a).unrealized).toBeCloseTo(.25 * 4 * 50);
    const exit = order(s, PLAYER, { contract: 'BX', direction: -1, quantity: 6, type: 'Market' });
    expect(exit.filled).toBe(6); expect(a.positions.BX).toMatchObject({ direction: -1, quantity: 2, entry: SPECS.BX.last - .25 });
    expect(a.balance).toBeCloseTo(10000 - .75 * 4 * 50); expect(a.realized).toBeCloseTo(-150);
  });
  it('converts TOTAL protection dollars and creates a genuine linked limit target', () => {
    const s = setup(); liquid(s);
    expect(protection(SPECS.BX, 10, 500)).toEqual({ ticks: 4, dollars: 500 });
    expect(protection(SPECS.BT, 3, .01)).toEqual({ ticks: 1, dollars: 15 });
    expect(() => protection(SPECS.BX, 1, 1e9 + 1)).toThrow();
    buy(s, 2, { stopDollars: 500, targetDollars: 1500 });
    const p = s.accounts[PLAYER].positions.BX!, target = s.orders[p.targetOrder!];
    expect(p.stop).toBe(p.entry - 5); expect(p.target).toBe(p.entry + 15);
    expect(target).toMatchObject({ type: 'Limit', protection: 'Take profit', reduceOnly: true, reductionPosition: p.id, remaining: 2 });
    expect(s.books.BX.asks).toContain(target.id);
    amendProtection(s, 'BX', p.id, 'stop', 250); expect(p.stop).toBe(p.entry - 2.5);
    amendProtection(s, 'BX', p.id, 'target', null); expect(s.orders[target.id]).toBeUndefined(); expect(p.target).toBeUndefined();
  });
  it('triggers stop entries only on trades, with accurate immediate partial receipts', () => {
    const s = setup(), ref = s.books.BX.reference; liquid(s, 'BX', 30);
    const stop = order(s, PLAYER, { contract: 'BX', direction: 1, quantity: 5, type: 'Stop', price: (ref + 1) * .25 });
    s.books.BX.fair += 1; processStops(s); expect(stop.status).toBe('Armed');
    order(s, 'buyer', { contract: 'BX', direction: 1, quantity: 1, type: 'Market' }); processStops(s);
    expect(stop.type).toBe('Market'); expect(stop).toMatchObject({ filled: 2, remaining: 0, cancelled: 3 });
    expect(s.accounts[PLAYER].fills[0].order).toBe(stop.id);
  });
  it('queues closing remainders, never reverses, and does not attach a stale exit to a new position', () => {
    const s = setup(); liquid(s); buy(s, 5);
    cancelAll(s, 'buyer'); const result = closePosition(s, 'BX'); expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.order.remaining).toBe(5); expect(s.accounts[PLAYER].positions.BX?.quantity).toBe(5);
    // Fixed market allowance was zero; retry at next window after liquidity arrives.
    s.time = 18; quote(s, 'buyer', 1, 20, s.books.BX.reference - 1); processQueue(s);
    expect(result.order.filled).toBe(2); expect(s.accounts[PLAYER].positions.BX?.quantity).toBe(3);
    s.time = 36; quote(s, 'buyer', 1, 100, s.books.BX.reference - 1); processQueue(s);
    expect(result.order.remaining).toBe(0); expect(s.accounts[PLAYER].positions.BX).toBeUndefined();
    s.time = 54; buy(s, 1); processQueue(s); expect(s.accounts[PLAYER].positions.BX?.quantity).toBe(1);
    expect(s.orders[result.order.id]).toBeUndefined();
    flatten(s); processQueue(s); expect(s.accounts[PLAYER].positions.BX).toBeUndefined();
  });
  it('preserves protection priority and allows valid protective exits past player volume limits', () => {
    const s = setup(); liquid(s); buy(s, 2, { stopDollars: 25, targetDollars: 50 });
    const p = s.accounts[PLAYER].positions.BX!, targetId = p.targetOrder!;
    processStops(s); expect(p.targetOrder).toBe(targetId);
    s.execution.owners[`${PLAYER}:-1`] = EXECUTION.owner;
    cancelAll(s, 'seller'); cancelAll(s, 'buyer'); quote(s, 'buyer', 1, 1, s.books.BX.reference - 1);
    order(s, 'seller', { contract: 'BX', direction: -1, quantity: 1, type: 'Market' }); processStops(s);
    expect(p.triggered).toBe('Stop loss'); expect(p.quantity).toBe(2);
    quote(s, 'buyer', 1, 2, s.books.BX.reference - 1); processStops(s); processQueue(s);
    expect(s.accounts[PLAYER].positions.BX).toBeUndefined(); expect(s.orders[targetId]).toBeUndefined();
  });
  it('depletes on actual marks, rejects exposure, and keeps reset liquidation pending without fills', () => {
    const s = setup(); liquid(s); buy(s, 2); const a = s.accounts[PLAYER];
    cancelAll(s, 'buyer'); a.base = a.balance = 5;
    quote(s, 'buyer', 1, 1, s.books.BX.reference - 2);
    order(s, 'seller', { contract: 'BX', direction: -1, quantity: 1, type: 'Market' }); processStops(s);
    expect(a.locked).toBe(true); expect(a.positions.BX?.quantity).toBe(2);
    expect(submitOrder(s, PLAYER, { contract: 'BT', direction: 1, quantity: 1, type: 'Market' }).ok).toBe(false);
    const volume = s.totalVolume; beginReset(s); advanceClock(s); expect(s.totalVolume).toBe(volume); expect(a.resetting).toBe(true);
    quote(s, 'buyer', 1, 1, s.books.BX.reference - 2); processStops(s); processQueue(s); expect(a.positions.BX?.quantity).toBe(1);
    quote(s, 'buyer', 1, 1, s.books.BX.reference - 2); processStops(s); processQueue(s); expect(a.positions.BX).toBeUndefined();
  });
  it('bounds candles, tape, order archive and account history', () => {
    const s = setup();
    for (let i = 0; i < 4050; i++) { const q = quote(s, 'seller', -1, 1, s.books.BX.reference + 1); cancelOrder(s, 'seller', q.id); }
    expect(s.archive).toHaveLength(SETTINGS.orders);
    expect(s.trades).toHaveLength(0);
    for (let i = 0; i < 4050; i++) {
      s.time = i * 60; const side = i % 2 === 0 ? -1 : 1;
      quote(s, 'seller', side, 1, s.books.BX.reference);
      order(s, 'buyer', { contract: 'BX', direction: -side as Direction, quantity: 1, type: 'Market' });
    }
    expect(s.trades).toHaveLength(SETTINGS.trades); expect(s.books.BX.candles['1m']).toHaveLength(SETTINGS.candles);
    expect(s.accounts.buyer.history).toHaveLength(SETTINGS.accounts); expect(s.archive).toHaveLength(SETTINGS.orders);
    expect(s.books.BX.volume).toBe(4050);
  });
  it('keeps extreme-dollar targets as genuine orders, even when a short target is below zero', () => {
    const s = setup(); liquid(s);
    order(s, PLAYER, { contract: 'BX', direction: -1, quantity: 1, type: 'Market', targetDollars: 1e9 });
    const p = s.accounts[PLAYER].positions.BX!;
    expect(p.target).toBeLessThan(0);
    expect(s.orders[p.targetOrder!]).toMatchObject({ type: 'Limit', remaining: 1, protectivePosition: p.id, reduceOnly: true });
    expect(s.books.BX.bids).toContain(p.targetOrder);
    expect(s.accounts[PLAYER].balance).toBe(10000);
  });
  it('rotates past blocked owners after 32 attempts without starving another market', () => {
    const s = setup(); liquid(s, 'BT');
    for (let i = 0; i < 34; i++) {
      const owner = `queued${String(i).padStart(2, '0')}`, a = addAccount(s, owner, 10000, 10, .8, true);
      // Controlled position fixture; no production path can seed an open position.
      a.positions.BX = { id: 100000 + i, direction: 1, quantity: 1, entry: SPECS.BX.last };
      order(s, owner, { contract: 'BX', direction: -1, quantity: 1, type: 'Market', reduceOnly: true });
    }
    const last = addAccount(s, 'zz', 10000, 10, .8, true);
    last.positions.BT = { id: 200000, direction: 1, quantity: 1, entry: SPECS.BT.last };
    const exit = order(s, 'zz', { contract: 'BT', direction: -1, quantity: 1, type: 'Market', reduceOnly: true });
    // Make an eligible queued order without executing it during submission.
    if (!exit.remaining) {
      last.positions.BT = { id: 200001, direction: 1, quantity: 1, entry: SPECS.BT.last };
      cancelAll(s, 'buyer', 'BT');
    }
    const waiting = order(s, 'zz', { contract: 'BT', direction: -1, quantity: 1, type: 'Market', reduceOnly: true });
    quote(s, 'buyer', 1, 200, s.books.BT.reference - 1, 'BT'); s.time = 18;
    processQueue(s); expect(waiting.remaining).toBe(1);
    processQueue(s); expect(waiting.filled).toBe(1); expect(last.positions.BT).toBeUndefined();
  });
});

describe('fixed-step scheduler', () => {
  it('preserves backlog at both catchup and frame budget boundaries', () => {
    const clock = new SimulationClock(); clock.accrue(1000); let steps = 0;
    expect(clock.run(() => steps++, () => 0)).toBe(32); expect(clock.backlog).toBe(10);
    clock.run(() => steps++, () => 0); clock.run(() => steps++, () => 0); expect(steps).toBe(72); expect(clock.backlog).toBe(0);
    clock.accrue(1000); let wall = 0;
    expect(clock.run(() => { wall += 3; }, () => wall)).toBe(3); expect(clock.backlog).toBe(17.25);
  });
});

// This helper type makes receipt accounting constraints explicit for future regressions.
export function receiptConservesQuantity(o: Order) { return o.original === o.filled + o.remaining + o.cancelled; }
