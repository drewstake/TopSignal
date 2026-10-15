import { EXECUTION, INSTRUMENTS, PLAYER, REGIMES, SETTINGS, SPECS, TIMEFRAMES, VERSION, contractLimit, protection, round,
  type Account, type Book, type Direction, type Instrument, type Order, type OrderRequest, type Position, type State } from './model';

export function createExchange(seed: number): State {
  if (!Number.isSafeInteger(seed)) throw new Error('Seed must be a safe integer.');
  const book = (key: Instrument): Book => ({ reference: round(SPECS[key].last / SPECS[key].tick), fair: round(SPECS[key].last / SPECS[key].tick), last: null, open: null, volume: 0, bids: [], asks: [], recent: [], volatility: 1, revision: 0,
    candles: { '1m': [], '5m': [], '15m': [], '1h': [], '1D': [] }, candleVolume: { '1m': 0, '5m': 0, '15m': 0, '1h': 0, '1D': 0 } });
  return { version: VERSION, seed, randomState: Math.floor(Math.abs(seed)) % 2147483646 + 1, step: 0, time: 0,
    books: { BX: book('BX'), BT: book('BT'), BG: book('BG') }, markets: {} as State['markets'], participants: [], accounts: {}, accountOrder: [], orders: {}, archive: [], trades: [], nextOrder: 0, nextTrade: 0, totalVolume: 0, tradeHash: 0, eligibility: 0,
    regime: REGIMES[0], direction: 1, nextRegime: 0, nextNews: 80, nextBeliefs: 0, news: 'No synthetic news', newsUntil: 0, pendingStops: [], dirty: new Set(), syncing: false, processingQueue: false, queuePeers: {}, execution: { window: -1, owners: {}, markets: {}, cursor: '' } };
}
export function addAccount(s: State, id: string, base: number, limit: number, risk: number, player = false) {
  const a: Account = { id, base, balance: base, realized: 0, cashFlow: 0, limit, risk, player, locked: false, liquidating: false, resetting: false, positions: {}, openOrders: [], history: [], fills: [], completed: 0, wins: 0 };
  s.accounts[id] = a; s.accountOrder.push(id); return a;
}
export function summary(s: State, a: Account) {
  let unrealized = 0, margin = 0;
  for (const key of INSTRUMENTS) {
    const p = a.positions[key], spec = SPECS[key], b = s.books[key];
    if (p) { unrealized += ((b.last ?? b.reference) * spec.tick - p.entry) * p.quantity * p.direction * spec.multiplier; margin += p.quantity * spec.margin; }
  }
  return { balance: a.balance, realized: a.realized, unrealized, equity: a.balance + unrealized, margin };
}
export function exposures(s: State, a: Account, exclude?: number) {
  const totals = { BX: { buy: 0, sell: 0 }, BT: { buy: 0, sell: 0 }, BG: { buy: 0, sell: 0 } };
  for (const id of a.openOrders) {
    const o = s.orders[id]; if (!o || o.reduceOnly || id === exclude) continue;
    totals[o.contract][o.direction === 1 ? 'buy' : 'sell'] += o.remaining;
  }
  return INSTRUMENTS.map(key => { const p = a.positions[key], net = p ? p.quantity * p.direction : 0, pending = totals[key];
    return { key, net, ...pending, reserved: Math.max(Math.abs(net + pending.buy), Math.abs(net - pending.sell)) }; });
}
export function playerCapacity(s: State, a: Account, key: Instrument, side: Direction, exclude?: number) {
  if (a.locked || a.liquidating || a.resetting || summary(s, a).equity <= 0) return 0;
  const exposure = exposures(s, a, exclude), current = exposure.find(e => e.key === key)!;
  const allowed = contractLimit(a.balance) - exposure.filter(e => e.key !== key).reduce((n, e) => n + e.reserved, 0);
  const same = side === 1 ? current.buy : current.sell, opposite = side === 1 ? current.sell : current.buy;
  if (Math.abs(current.net - side * opposite) > allowed) return 0;
  return Math.max(0, Math.floor(allowed - side * current.net - same));
}
export function exposureAllowed(s: State, a: Account, key: Instrument, side: Direction, qty: number) {
  if (a.player) return qty <= playerCapacity(s, a, key, side);
  let used = 0;
  for (const e of exposures(s, a)) {
    const buy = e.buy + (e.key === key && side === 1 ? qty : 0), sell = e.sell + (e.key === key && side === -1 ? qty : 0);
    const worst = Math.max(Math.abs(e.net + buy), Math.abs(e.net - sell));
    if (worst > a.limit) return false;
    used += worst * SPECS[e.key].margin;
  }
  return used <= Math.max(0, summary(s, a).equity) * a.risk;
}
export function allows(s: State, key: Instrument, ticks: number, side: Direction) {
  return (ticks - s.books[key].fair) * side <= EXECUTION.guard * Math.max(1, SPECS[key].scale);
}
function protectiveValid(s: State, o: Order) {
  const a = s.accounts[o.owner], p = a.positions[o.contract];
  if (!o.reduceOnly || !p || o.protectivePosition !== p.id || o.direction !== -p.direction) return false;
  if (o.protection === 'Take profit') return p.target !== undefined && o.type === 'Limit' && o.price === round(p.target / SPECS[o.contract].tick) && (!p.triggered || p.triggered === 'Take profit') && !a.liquidating;
  return o.protection === p.triggered && ['Stop loss', 'Account depleted', 'Margin liquidation', ...(a.resetting ? ['Account reset'] : [])].includes(o.protection ?? '');
}
export function available(s: State, o: Order): number {
  if (s.orders[o.id] !== o || o.remaining <= 0 || (o.expires !== undefined && o.expires <= s.time)) return 0;
  const a = s.accounts[o.owner], p = a.positions[o.contract];
  if (o.protectivePosition !== undefined && !protectiveValid(s, o)) return 0;
  if (o.reduceOnly) return p && p.id === o.reductionPosition && p.direction !== o.direction ? Math.min(p.quantity, o.remaining) : 0;
  if (a.locked || a.liquidating || a.resetting || summary(s, a).equity <= 0) return 0;
  if (a.player) return playerCapacity(s, a, o.contract, o.direction, o.id) >= o.remaining ? o.remaining : 0;
  return exposureAllowed(s, a, o.contract, o.direction, 0) ? o.remaining : 0;
}
function clean(s: State, b: Book) { b.bids = b.bids.filter(id => s.orders[id]?.remaining > 0); b.asks = b.asks.filter(id => s.orders[id]?.remaining > 0); }
function retire(s: State, o: Order, status: string) {
  if (!s.orders[o.id]) return;
  o.status = status; o.queued = false;
  s.accounts[o.owner].openOrders = s.accounts[o.owner].openOrders.filter(id => id !== o.id);
  delete s.orders[o.id]; s.archive.push(o); if (s.archive.length > SETTINGS.orders) s.archive.shift();
  s.books[o.contract].revision++;
  s.dirty.add(o.owner);
}
export function cancelOrder(s: State, owner: string, id: number) {
  const o = s.orders[id]; if (!o || o.owner !== owner) return false;
  const p = s.accounts[owner].positions[o.contract];
  if (p?.exitOrder === id && p.triggered === 'Player close') { delete p.triggered; delete p.exitOrder; }
  o.cancelled += o.remaining; o.remaining = 0; retire(s, o, 'Cancelled'); clean(s, s.books[o.contract]); return true;
}
export function cancelAll(s: State, owner: string, key?: Instrument) {
  for (const id of [...s.accounts[owner].openOrders].sort((a, b) => a - b)) { const o = s.orders[id]; if (o && (!key || key === o.contract)) cancelOrder(s, owner, id); }
}
function rest(s: State, o: Order) {
  const b = s.books[o.contract], ids = o.direction === 1 ? b.bids : b.asks;
  o.queued = false; o.reason = allows(s, o.contract, o.price!, -o.direction as Direction) ? undefined : 'Waiting · price safeguard'; o.status = o.filled ? 'Partially filled · working' : 'Working';
  if (!ids.includes(o.id)) ids.push(o.id);
  ids.sort((l, r) => (s.orders[r].price! - s.orders[l].price!) * o.direction || l - r); b.revision++;
}
export function pruneBook(s: State, key: Instrument) {
  const b = s.books[key];
  for (const id of [...b.bids, ...b.asks]) {
    const o = s.orders[id]; if (!o) continue;
    const qty = available(s, o);
    if (qty < o.remaining) { o.cancelled += o.remaining - qty; o.remaining = qty; if (!qty) { o.reason = 'Expired, position changed, or capacity unavailable'; retire(s, o, 'No longer eligible'); } }
  }
  clean(s, b);
}
export function eligibleDepth(s: State, key: Instrument, side: Direction) {
  let depth = 0;
  for (const id of side === 1 ? s.books[key].asks : s.books[key].bids) { const o = s.orders[id]; if (o && !s.accounts[o.owner].player && allows(s, key, o.price!, side)) depth += available(s, o); }
  return depth;
}
function executionState(s: State) {
  const window = Math.floor(s.time / EXECUTION.window);
  if (window !== s.execution.window) s.execution = { window, owners: {}, markets: {}, cursor: s.execution.cursor };
  return s.execution;
}
export function executionBudget(s: State, o: Order) {
  if (!s.accounts[o.owner].player || protectiveValid(s, o)) return Infinity;
  const e = executionState(s), owner = `${o.owner}:${o.direction}`, market = `${o.contract}:${o.direction}`;
  const budget = e.markets[market] ??= { used: 0, limit: Math.min(EXECUTION.market, Math.floor(eligibleDepth(s, o.contract, o.direction) * EXECUTION.bookFraction)) };
  if (!s.processingQueue && Object.values(s.orders).some(other => other.id !== o.id && other.queued && other.owner !== o.owner && other.contract === o.contract && other.direction === o.direction)) return 0;
  let value = Math.max(0, Math.min(EXECUTION.owner - (e.owners[owner] ?? 0), budget.limit - budget.used));
  if (value > 0) value = Math.min(value, Math.floor(eligibleDepth(s, o.contract, o.direction) * EXECUTION.bookFraction));
  if (s.processingQueue) value = Math.min(value, Math.max(1, Math.floor(budget.limit / Math.min(EXECUTION.ownersPerStep, s.queuePeers[market] ?? 1))));
  return value;
}
function recordBudget(s: State, o: Order, qty: number) {
  if (!s.accounts[o.owner].player || o.protectivePosition !== undefined) return;
  const e = executionState(s), owner = `${o.owner}:${o.direction}`, market = `${o.contract}:${o.direction}`;
  e.owners[owner] = (e.owners[owner] ?? 0) + qty;
  if (e.markets[market]) e.markets[market].used += qty;
}
function fillAccount(s: State, o: Order, price: number, qty: number, trade: number) {
  const a = s.accounts[o.owner], spec = SPECS[o.contract]; let p = a.positions[o.contract];
  const oldNet = p ? p.quantity * p.direction : 0, delta = o.direction * qty;
  a.cashFlow -= price * delta * spec.multiplier;
  if (o.protection === 'Take profit' && p) { p.triggered = 'Take profit'; p.exitOrder = o.id; s.pendingStops.push({ owner: a.id, contract: o.contract, position: p.id }); }
  if (p && oldNet * delta < 0) {
    const closed = Math.min(Math.abs(oldNet), qty), pnl = (price - p.entry) * closed * p.direction * spec.multiplier;
    a.realized += pnl; a.balance = a.base + a.realized; a.completed++; if (pnl > 0) a.wins++;
    a.history.unshift({ position: p.id, contract: o.contract, direction: p.direction, quantity: closed, entry: p.entry, exit: price, pnl, time: s.time, trade, reason: o.protection ?? p.triggered ?? 'Trade' });
    if (a.history.length > SETTINGS.accounts) a.history.pop();
  }
  const net = oldNet + delta;
  if (!net) delete a.positions[o.contract];
  else if (!p || oldNet * net < 0) { p = { id: o.id, direction: net > 0 ? 1 : -1, quantity: Math.abs(net), entry: price }; a.positions[o.contract] = p; }
  else { if (oldNet * delta > 0) p.entry = (p.entry * Math.abs(oldNet) + price * qty) / Math.abs(net); p.quantity = Math.abs(net); }
  p = a.positions[o.contract];
  if (p && !o.reduceOnly && p.direction === o.direction) {
    if (o.stopTicks !== undefined && !p.manualStop && (!a.player || p.stop === undefined)) p.stop = round((p.entry - o.direction * o.stopTicks * spec.tick) / spec.tick) * spec.tick;
    if (o.targetTicks !== undefined && !p.manualTarget && (!a.player || p.target === undefined)) p.target = round((p.entry + o.direction * o.targetTicks * spec.tick) / spec.tick) * spec.tick;
  }
  o.remaining -= qty; o.filled += qty; o.notional += price * qty;
  if (a.player) { a.fills.unshift({ trade, order: o.id, contract: o.contract, direction: o.direction, quantity: qty, price, slippage: (price - o.reference * spec.tick) * o.direction, time: s.time }); if (a.fills.length > SETTINGS.trades) a.fills.pop(); }
  s.eligibility++; s.dirty.add(a.id);
}
function execute(s: State, incoming: Order, resting: Order, quantity: number) {
  const key = incoming.contract, b = s.books[key], ticks = resting.price!, price = ticks * SPECS[key].tick;
  const trade = { id: ++s.nextTrade, time: s.time, step: s.step, contract: key, ticks, quantity, direction: incoming.direction,
    buyer: incoming.direction === 1 ? incoming.owner : resting.owner, seller: incoming.direction === -1 ? incoming.owner : resting.owner, incoming: incoming.id, resting: resting.id, fair: b.fair };
  fillAccount(s, incoming, price, quantity, trade.id); fillAccount(s, resting, price, quantity, trade.id); recordBudget(s, incoming, quantity);
  if (b.last !== null) b.volatility = b.volatility * .96 + Math.abs(ticks - b.last) * .04;
  // Sole writer of last price, volume, tape, and OHLCV. No synthetic settlement path.
  b.last = ticks; b.open ??= ticks; b.volume += quantity; s.totalVolume += quantity; b.revision++;
  b.recent.push(ticks); if (b.recent.length > 64) b.recent.shift();
  s.trades.push(trade); if (s.trades.length > SETTINGS.trades) s.trades.shift();
  s.tradeHash = (s.tradeHash * 31 + ticks * 7 + quantity * 13 + trade.id + s.step) % 2147483647;
  for (const [tf, seconds] of TIMEFRAMES) {
    const series = b.candles[tf], time = Math.floor(s.time / seconds) * seconds, previous = series.at(-1);
    if (!previous || previous.time !== time) { series.push({ time, open: price, high: price, low: price, close: price, volume: quantity }); if (series.length > SETTINGS.candles) series.shift(); }
    else series[series.length - 1] = { ...previous, high: Math.max(previous.high, price), low: Math.min(previous.low, price), close: price, volume: previous.volume + quantity };
    b.candleVolume[tf] += quantity;
  }
  for (const o of Object.values(s.orders)) if (o.contract === key && o.type === 'Stop' && (ticks - o.price!) * o.direction >= 0) o.triggered = true;
  for (const id of s.accountOrder) {
    const a = s.accounts[id];
    if (a.player && !a.locked && summary(s, a).equity <= 0) deplete(s, a);
    const p = a.positions[key];
    if (p && (!p.triggered || a.player && p.triggered === 'Take profit')) {
      const reason = p.stop !== undefined && (price - p.stop) * p.direction <= 0 ? 'Stop loss' : !p.triggered && p.target !== undefined && (price - p.target) * p.direction >= 0 ? 'Take profit' : undefined;
      if (reason) { p.triggered = reason; if (reason === 'Stop loss') delete p.target; else if (!a.player) delete p.stop; s.dirty.add(id); s.pendingStops.push({ owner: id, contract: key, position: p.id }); }
    }
  }
}
function deplete(s: State, a: Account) {
  a.locked = true; a.liquidating = true;
  for (const key of INSTRUMENTS) { const p = a.positions[key]; if (p) { p.triggered = a.resetting ? 'Account reset' : 'Account depleted'; delete p.stop; delete p.target; s.pendingStops.push({ owner: a.id, contract: key, position: p.id }); } }
  s.dirty.add(a.id);
}
// Preflight makes ordinary marketable limits atomic with respect to safeguards.
function standardLimitAllowed(s: State, o: Order) {
  if (!allows(s, o.contract, o.price!, o.direction)) return false;
  if (!s.accounts[o.owner].player) return true;
  const budget = executionBudget(s, o); let needed = 0;
  for (const id of o.direction === 1 ? s.books[o.contract].asks : s.books[o.contract].bids) {
    const r = s.orders[id]; if (!r) continue;
    if ((r.price! - o.price!) * o.direction > 0) break;
    const qty = r.owner === o.owner ? 0 : available(s, r); if (!qty) continue;
    if (!allows(s, o.contract, r.price!, o.direction)) return false;
    needed += Math.min(qty, o.remaining - needed); if (needed > budget) return false; if (needed >= o.remaining) return true;
  }
  return true;
}
export type Submission = { ok: true; order: Order } | { ok: false; error: string };
type Internal = { protection: string; position: number; deferred?: boolean };
// Internal stamps are a separate engine argument, never copied from UI requests.
export function submitOrder(s: State, owner: string, r: OrderRequest, internal?: Internal): Submission {
  const a = s.accounts[owner], b = s.books[r.contract], spec = SPECS[r.contract];
  const fail = (error: string): Submission => ({ ok: false, error });
  if (!a || !b || !spec || ![1, -1].includes(r.direction) || !Number.isSafeInteger(r.quantity) || r.quantity < 1) return fail('Choose a market and a positive whole number of contracts.');
  if (!['Market', 'Limit', 'Stop'].includes(r.type)) return fail('Use Market, Limit or Stop.');
  if (r.expires !== undefined && (!Number.isFinite(r.expires) || r.expires <= s.time)) return fail('Expiry must be in the future.');
  const reducing = r.reduceOnly === true, p = a.positions[r.contract];
  if (a.resetting && !internal) return fail('Reset is waiting for positions to liquidate.');
  if (reducing && (!p || p.direction === r.direction || r.quantity > p.quantity)) return fail('No matching position to reduce.');
  if ((a.locked || a.liquidating) && !reducing) return fail('Account depleted or closing. Close positions only.');
  let price: number | undefined;
  if (r.type !== 'Market') {
    if (r.price === undefined || !Number.isFinite(r.price) || !internal && r.price <= 0 || Math.abs(r.price / spec.tick - round(r.price / spec.tick)) > .00001) return fail('Price must match the market tick size.');
    price = round(r.price / spec.tick);
    if (!internal && Math.abs(price - (b.last ?? b.reference)) > (b.last ?? b.reference) * SETTINGS.priceBand) return fail('Entry price is outside the 10% price band.');
  }
  if (r.type === 'Stop' && (reducing || !a.player || (price! - (b.last ?? b.reference)) * r.direction <= 0)) return fail('Buy stops must be above and sell stops below the current traded price.');
  let stopTicks = r.stopTicks, targetTicks = r.targetTicks;
  try {
    if (r.stopDollars !== undefined) { if (stopTicks !== undefined) return fail('Use dollars or ticks, not both.'); stopTicks = protection(spec, r.quantity, r.stopDollars).ticks; }
    if (r.targetDollars !== undefined) { if (targetTicks !== undefined) return fail('Use dollars or ticks, not both.'); targetTicks = protection(spec, r.quantity, r.targetDollars).ticks; }
  } catch (error) { return fail((error as Error).message); }
  for (const ticks of [stopTicks, targetTicks]) if (ticks !== undefined && (!Number.isSafeInteger(ticks) || ticks < 1 || ticks > protection(spec, r.quantity, 1e9).ticks)) return fail('Protection is outside the supported tick range.');
  if (a.player && !reducing) {
    if (a.openOrders.filter(id => !s.orders[id]?.protection).length >= SETTINGS.maxPlayerOrders) return fail('At most 16 working entry orders. Cancel an order first.');
    const capacity = playerCapacity(s, a, r.contract, r.direction);
    if (r.quantity > capacity) return fail(`Account contract limit (${contractLimit(a.balance)} total). Maximum currently available: ${capacity} ${r.contract}.`);
  }
  if (!a.player && !reducing && !exposureAllowed(s, a, r.contract, r.direction, r.quantity)) return fail('Insufficient margin or inventory capacity.');
  const o: Order = { id: s.nextOrder + 1, owner, contract: r.contract, direction: r.direction, original: r.quantity, remaining: r.quantity, filled: 0, cancelled: 0, notional: 0, type: r.type, price, time: s.time, reference: s.orders[(r.direction === 1 ? b.asks : b.bids)[0]]?.price ?? b.last ?? b.reference, status: 'Working', reduceOnly: reducing, reductionPosition: reducing ? p!.id : undefined, execution: reducing ? 'Sliced' : 'Immediate', stopTicks, targetTicks, expires: r.expires, protection: internal?.protection, protectivePosition: internal?.position };
  if (internal && !protectiveValid(s, o)) return fail('Protective exit no longer matches its position.');
  if (r.type === 'Limit' && !reducing && !standardLimitAllowed(s, o)) return fail('Limit exceeds execution safeguards. Use a non-marketable price or reduce quantity. No order placed.');
  pruneBook(s, r.contract); s.nextOrder++; s.orders[o.id] = o; a.openOrders.push(o.id);
  if (internal?.protection === 'Take profit') { p!.targetOrder = o.id; if (p!.triggered === 'Take profit') p!.exitOrder = o.id; }
  if (r.type === 'Stop') { o.status = 'Armed'; return { ok: true, order: o }; }
  if (internal?.deferred) {
    const opposing = s.orders[(o.direction === 1 ? b.asks : b.bids)[0]];
    if (opposing && (opposing.price! - o.price!) * o.direction <= 0 || !allows(s, o.contract, o.price!, o.direction)) { o.queued = true; o.status = 'Waiting for matching'; }
    else rest(s, o);
  } else match(s, o);
  return { ok: true, order: o };
}
export function match(s: State, o: Order) {
  if (!s.orders[o.id]) return;
  const b = s.books[o.contract], a = s.accounts[o.owner]; o.queued = false; o.reason = undefined;
  const protectedExit = protectiveValid(s, o);
  let allowance = protectedExit ? Infinity : Math.min(a.player && o.execution === 'Sliced' ? EXECUTION.slice : Infinity, executionBudget(s, o));
  let limited = false;
  const opposing = o.direction === 1 ? b.asks : b.bids;
  for (const id of [...opposing]) {
    if (!o.remaining || !available(s, o)) break;
    const r = s.orders[id]; if (!r) continue;
    if (o.type === 'Limit' && (r.price! - o.price!) * o.direction > 0) break;
    if (allowance <= 0 || !allows(s, o.contract, r.price!, o.direction)) { limited = true; o.reason = allowance <= 0 ? 'Waiting · execution allowance' : 'Waiting · price safeguard'; break; }
    if (r.owner === o.owner) { r.cancelled += r.remaining; r.remaining = 0; retire(s, r, 'Self-trade prevented'); continue; }
    const qty = available(s, r);
    if (!qty) { r.cancelled += r.remaining; r.remaining = 0; retire(s, r, 'No longer eligible'); continue; }
    const quantity = Math.min(available(s, o), qty, allowance);
    if (quantity <= 0) break;
    execute(s, o, r, quantity); allowance -= quantity;
    if (!r.remaining) retire(s, r, 'Filled');
    if (allowance <= 0 && o.remaining > 0) { limited = true; o.reason = 'Waiting · execution allowance'; }
  }
  clean(s, b);
  const eligible = available(s, o);
  if (!o.remaining) retire(s, o, 'Filled');
  else if (eligible > 0 && (protectedExit && (limited || o.type === 'Market') || a.player && o.execution === 'Sliced' && (limited || o.type === 'Market') || !a.player && o.reduceOnly && o.type === 'Limit' && limited)) {
    o.queued = true; o.status = o.filled ? 'Partially filled · waiting' : 'Waiting for matching'; o.reason ??= 'Waiting for liquidity';
  } else if (o.type === 'Market' || !eligible || limited && o.execution !== 'Immediate') {
    o.reason ??= eligible ? 'No eligible liquidity' : 'Position or account capacity changed';
    o.cancelled += o.remaining; o.remaining = 0; retire(s, o, o.filled ? 'Partial / IOC' : 'No liquidity');
  } else {
    const first = s.orders[(o.direction === 1 ? b.asks : b.bids)[0]];
    if (first && (first.price! - o.price!) * o.direction <= 0) { o.cancelled += o.remaining; o.remaining = 0; retire(s, o, 'Price safeguard'); }
    else rest(s, o);
  }
  syncReductions(s); syncProtection(s);
}
function syncReductions(s: State) {
  for (const owner of [...s.dirty].sort()) {
    const a = s.accounts[owner];
    for (const id of [...a.openOrders]) {
      const o = s.orders[id]; if (!o?.reduceOnly) continue;
      const p = a.positions[o.contract];
      if (!p || p.id !== o.reductionPosition || p.direction === o.direction) cancelOrder(s, owner, id);
      else if (o.remaining > p.quantity) { o.cancelled += o.remaining - p.quantity; o.remaining = p.quantity; s.books[o.contract].revision++; }
    }
  }
}
export function syncProtection(s: State) {
  if (s.syncing) return; s.syncing = true;
  try {
    let passes = 0;
    while (s.dirty.size && passes++ < 200) {
      const owners = [...s.dirty].sort(); s.dirty.clear();
      for (const owner of owners) {
        const a = s.accounts[owner]; if (!a.player) continue;
        for (const key of INSTRUMENTS) {
          const p = a.positions[key];
          const wanted = p && p.target !== undefined && (!p.triggered || p.triggered === 'Take profit') && !a.locked && !a.liquidating;
          let target = p?.targetOrder !== undefined ? s.orders[p.targetOrder] : undefined;
          for (const id of [...a.openOrders]) {
            const child = s.orders[id]; if (child?.contract !== key || child.protection !== 'Take profit') continue;
            if (!wanted || child !== target || !protectiveValid(s, child) || child.remaining < p.quantity) { cancelOrder(s, owner, id); if (child === target) target = undefined; }
            else if (child.remaining > p.quantity) { child.cancelled += child.remaining - p.quantity; child.remaining = p.quantity; }
          }
          if (!wanted) { if (p) delete p.targetOrder; continue; }
          if (!target || !s.orders[target.id]) {
            delete p.targetOrder;
            submitOrder(s, owner, { contract: key, direction: -p.direction as Direction, quantity: p.quantity, type: 'Limit', price: p.target, reduceOnly: true }, { protection: 'Take profit', position: p.id, deferred: true });
          }
        }
      }
    }
  } finally { s.syncing = false; }
}
export function retryReady(s: State, a: Account, key: Instrument, p: Position) {
  if (a.player || p.triggered === 'Take profit') return true;
  const b = s.books[key], prior = p.retry;
  if (prior && prior.reason === p.triggered) {
    if (prior.step === s.step) return false;
    if (s.time < prior.at && prior.revision === b.revision && prior.eligibility === s.eligibility && prior.fair === b.fair) return false;
  }
  const side = -p.direction as Direction;
  if (!(side === 1 ? b.asks : b.bids).some(id => { const o = s.orders[id]; return o && allows(s, key, o.price!, side) && available(s, o) > 0; })) { retryAttempted(s, key, p, false); return false; }
  return true;
}
export function retryAttempted(s: State, key: Instrument, p: Position, attempted = true) {
  const b = s.books[key]; p.retry = { at: s.time + SETTINGS.botExitRetry, revision: b.revision, eligibility: s.eligibility, fair: b.fair, reason: p.triggered, step: attempted ? s.step : p.retry?.step };
}
export function processStops(s: State) {
  syncProtection(s);
  let count = 0;
  while (s.pendingStops.length && count++ < 200) {
    const item = s.pendingStops.shift()!, a = s.accounts[item.owner], p = a.positions[item.contract];
    if (!p || p.id !== item.position || !p.triggered) continue;
    if (a.player && p.triggered === 'Take profit') {
      for (const id of [...a.openOrders]) { const o = s.orders[id]; if (o?.contract === item.contract && id !== p.targetOrder) cancelOrder(s, a.id, id); }
      p.exitOrder = p.targetOrder;
      const target = p.targetOrder ? s.orders[p.targetOrder] : undefined; if (target?.queued) match(s, target); continue;
    }
    let exit = p.exitOrder ? s.orders[p.exitOrder] : undefined;
    if (exit?.protection && exit.protection !== p.triggered) { cancelOrder(s, a.id, exit.id); exit = undefined; }
    if (exit?.protection && exit.queued) match(s, exit);
    if (!exit) {
      cancelAll(s, a.id, item.contract);
      if (!retryReady(s, a, item.contract, p)) continue;
      const target = p.triggered === 'Take profit' ? p.target : undefined;
      const result = submitOrder(s, a.id, { contract: item.contract, direction: -p.direction as Direction, quantity: a.player ? p.quantity : Math.min(10000, p.quantity), type: target !== undefined ? 'Limit' : 'Market', price: target, reduceOnly: true }, a.player && p.triggered !== 'Player close' ? { protection: p.triggered, position: p.id } : undefined);
      if (!a.player) retryAttempted(s, item.contract, p);
      if (result.ok && result.order.remaining) p.exitOrder = result.order.id;
    }
  }
  // Keep the stop order identity and receipt as it activates; execution still uses all current checks.
  for (const o of Object.values(s.orders).sort((a, b) => a.id - b.id)) if (o.type === 'Stop' && o.triggered) {
    o.type = 'Market'; o.price = undefined; o.triggered = false; match(s, o);
  }
}
export function processQueue(s: State) {
  const e = executionState(s), pending: Record<string, Order> = {};
  for (const o of Object.values(s.orders)) if (o.queued) { const old = pending[o.owner]; if (!old || o.reduceOnly && !old.reduceOnly || o.reduceOnly === old.reduceOnly && o.id < old.id) pending[o.owner] = o; }
  const owners = Object.keys(pending).sort(); if (!owners.length) return;
  s.queuePeers = {}; for (const o of Object.values(pending)) { const key = `${o.contract}:${o.direction}`; s.queuePeers[key] = (s.queuePeers[key] ?? 0) + 1; }
  const start = Math.max(0, owners.findIndex(owner => owner > e.cursor)); s.processingQueue = true;
  try {
    for (let i = 0; i < Math.min(owners.length, EXECUTION.ownersPerStep); i++) {
      const owner = owners[(start + i) % owners.length], o = pending[owner];
      if (s.orders[o.id] && (!available(s, o) || executionBudget(s, o) > 0)) match(s, o);
      // Rotate after an attempted turn too: blocked owners cannot starve owners
      // beyond the 32-owner pass boundary in a different, liquid market.
      e.cursor = owner;
    }
  } finally { s.processingQueue = false; s.queuePeers = {}; }
}
export function advanceClock(s: State) {
  s.time += SETTINGS.step; s.step++;
  for (const o of Object.values(s.orders)) if (o.expires !== undefined && o.expires <= s.time) { cancelOrder(s, o.owner, o.id); o.status = 'Expired'; }
  for (const id of s.accountOrder) {
    const a = s.accounts[id], mark = summary(s, a);
    if (!Object.keys(a.positions).length) a.liquidating = false;
    if (mark.equity <= 0 || !a.player && mark.margin > Math.max(0, mark.equity) * 1.05) {
      if (mark.equity <= 0) deplete(s, a);
      a.liquidating = true;
      // Preserve already queued closes and their partial fill receipts.
      for (const order of [...a.openOrders]) if (!s.orders[order]?.reduceOnly) cancelOrder(s, id, order);
      for (const key of INSTRUMENTS) { const p = a.positions[key]; if (p) { p.triggered = a.resetting ? 'Account reset' : a.locked ? 'Account depleted' : 'Margin liquidation'; delete p.stop; delete p.target; } }
    }
    for (const key of INSTRUMENTS) { const p = a.positions[key]; if (p?.triggered) s.pendingStops.push({ owner: id, contract: key, position: p.id }); }
  }
  processStops(s);
  for (const key of INSTRUMENTS) pruneBook(s, key);
}
export function closePosition(s: State, key: Instrument): Submission {
  const a = s.accounts[PLAYER], p = a.positions[key];
  if (!p) return { ok: false, error: 'Position is already closed.' };
  const existing = p.exitOrder ? s.orders[p.exitOrder] : undefined;
  if (existing?.queued) return { ok: true, order: existing };
  cancelAll(s, PLAYER, key); p.triggered = 'Player close'; delete p.target; delete p.stop; s.dirty.add(PLAYER);
  const result = submitOrder(s, PLAYER, { contract: key, direction: -p.direction as Direction, quantity: p.quantity, type: 'Market', reduceOnly: true });
  if (result.ok && result.order.remaining) p.exitOrder = result.order.id; return result;
}
export function flatten(s: State) { cancelAll(s, PLAYER); return INSTRUMENTS.filter(key => s.accounts[PLAYER].positions[key]).map(key => closePosition(s, key)); }
export function beginReset(s: State) {
  const a = s.accounts[PLAYER]; cancelAll(s, PLAYER); a.resetting = true;
  for (const key of INSTRUMENTS) { const p = a.positions[key]; if (p) { p.triggered = 'Account reset'; delete p.stop; delete p.target; s.pendingStops.push({ owner: PLAYER, contract: key, position: p.id }); } }
  processStops(s);
}
export function amendProtection(s: State, key: Instrument, positionId: number, field: 'stop' | 'target', dollars: number | null) {
  const a = s.accounts[PLAYER], p = a.positions[key], spec = SPECS[key];
  if (!p || p.id !== positionId || p.triggered || a.locked) throw new Error('Position changed, closed, or protection already triggered.');
  if (dollars === null) { delete p[field]; p[field === 'stop' ? 'manualStop' : 'manualTarget'] = true; }
  else {
    const distance = protection(spec, p.quantity, dollars).ticks;
    const price = round(p.entry / spec.tick + (field === 'stop' ? -1 : 1) * p.direction * distance) * spec.tick;
    const current = (s.books[key].last ?? s.books[key].reference) * spec.tick;
    if (field === 'stop' ? (price - current) * p.direction >= 0 : (price - current) * p.direction <= 0) throw new Error('Protection must be beyond the current price on the correct side.');
    p[field] = price; p[field === 'stop' ? 'manualStop' : 'manualTarget'] = true;
  }
  s.dirty.add(PLAYER); syncProtection(s);
}
// Pure read model: no pruning, RNG calls or mutations from render/selection.
export function depth(s: State, key: Instrument, side: Direction) {
  const levels: { ticks: number; quantity: number }[] = [];
  for (const id of side === 1 ? s.books[key].bids : s.books[key].asks) {
    const o = s.orders[id]; if (!o || o.remaining <= 0) continue;
    const last = levels.at(-1);
    if (last && last.ticks === o.price) last.quantity += o.remaining;
    else { if (levels.length === 5) break; levels.push({ ticks: o.price!, quantity: o.remaining }); }
  }
  return levels;
}
