import { aggregateExecution, DEFAULT_CONFIG, DEFAULT_SEED, MAX_TRADES, round, STEP_SECONDS,
  type Account, type Direction, type Instrument, type MarketBook, type Order, type OrderRequest, type SimulationConfig, type SimulationState } from './simulationModel';

export function createExchange(seed = DEFAULT_SEED, config: SimulationConfig = DEFAULT_CONFIG): SimulationState {
  const settings = structuredClone(config);
  const makeBook = (key: Instrument): MarketBook => ({
    reference: round(settings.contracts[key].last / settings.contracts[key].tick), last: null, open: null,
    volume: 0, bids: [], asks: [], recent: [], up: 0, down: 0, volatility: 1,
    candles: { '1m': [], '5m': [], '15m': [], '1h': [], '1d': [] },
    candleVolume: { '1m': 0, '5m': 0, '15m': 0, '1h': 0, '1d': 0 }, flow: [],
  });
  return {
    engineVersion: 2, initialSeed: seed, randomState: Math.floor(Math.abs(seed)) % 2147483646 + 1,
    step: 0, time: 0, config: settings,
    books: { BX: makeBook('BX'), BT: makeBook('BT'), BG: makeBook('BG') },
    accounts: {}, accountOrder: [], participants: [], orders: {}, nextOrder: 0, nextTrade: 0,
    trades: [], totalVolume: 0, tradeHash: 0, pendingStops: [],
    markets: {} as SimulationState['markets'], regime: settings.regimes[0], direction: 1,
    nextRegime: 0, nextNews: 80, nextBeliefs: 0, news: 'No synthetic news', newsUntil: 0, events: [],
  };
}
export function addAccount(s: SimulationState, id: string, base: number, limit: number, risk: number): Account {
  if (s.accounts[id]) return s.accounts[id];
  const a: Account = { id, base, balance: base, realized: 0, cashFlow: 0, limit, risk,
    locked: false, liquidating: false, positions: {}, openOrders: [], bought: 0, sold: 0, cancelled: 0, completed: 0 };
  s.accounts[id] = a; s.accountOrder.push(id);
  return a;
}
export function accountSummary(s: SimulationState, a: Account) {
  let unrealized = 0, margin = 0;
  for (const key of s.config.contractOrder) {
    const b = s.books[key], c = s.config.contracts[key], p = a.positions[key];
    if (p) {
      unrealized += ((b.last ?? b.reference) * c.tick - p.entry) * p.quantity * p.direction * c.multiplier;
      margin += p.quantity * c.margin;
    }
  }
  return { balance: a.balance, equity: a.balance + unrealized, unrealized, margin };
}
export function exposureAllowed(s: SimulationState, a: Account, key: Instrument, side: Direction, qty: number) {
  let used = 0;
  for (const symbol of s.config.contractOrder) {
    const p = a.positions[symbol], net = p ? p.quantity * p.direction : 0;
    let buy = 0, sell = 0;
    for (const id of a.openOrders) {
      const o = s.orders[id];
      if (o && !o.reduceOnly && o.contract === symbol) {
        if (o.direction === 1) buy += o.remaining; else sell += o.remaining;
      }
    }
    if (symbol === key) { if (side === 1) buy += qty; else sell += qty; }
    const worst = Math.max(Math.abs(net + buy), Math.abs(net - sell));
    if (worst > a.limit) return false;
    used += worst * s.config.contracts[symbol].margin;
  }
  return used <= Math.max(0, accountSummary(s, a).equity) * a.risk;
}
function retire(s: SimulationState, o: Order, status: string) {
  o.status = status;
  const a = s.accounts[o.owner];
  a.openOrders = a.openOrders.filter(id => id !== o.id);
  if (o.cancelled) a.cancelled += 1;
  delete s.orders[o.id];
}
function clean(s: SimulationState, b: MarketBook) {
  b.bids = b.bids.filter(id => s.orders[id]?.remaining > 0);
  b.asks = b.asks.filter(id => s.orders[id]?.remaining > 0);
}
export function cancelOrder(s: SimulationState, owner: string, id: number) {
  const o = s.orders[id];
  if (!o || o.owner !== owner) return;
  o.cancelled += o.remaining; o.remaining = 0;
  retire(s, o, 'Cancelled'); clean(s, s.books[o.contract]);
}
export function cancelAll(s: SimulationState, owner: string, key?: Instrument) {
  for (const id of [...s.accounts[owner].openOrders].sort((a, b) => a - b)) {
    const o = s.orders[id];
    if (o && (!key || o.contract === key)) cancelOrder(s, owner, id);
  }
}
function fillAccount(s: SimulationState, o: Order, price: number, qty: number) {
  const a = s.accounts[o.owner], c = s.config.contracts[o.contract];
  let p = a.positions[o.contract];
  const side = o.direction, oldNet = p ? p.quantity * p.direction : 0, delta = side * qty;
  a.cashFlow -= price * delta * c.multiplier;
  if (side === 1) a.bought += qty; else a.sold += qty;
  if (p && oldNet * delta < 0) {
    const closed = Math.min(Math.abs(oldNet), qty);
    const realized = (price - p.entry) * closed * p.direction * c.multiplier;
    a.balance += realized; a.realized += realized; a.completed += 1;
  }
  const net = oldNet + delta;
  if (net === 0) delete a.positions[o.contract];
  else if (!p || oldNet * net < 0) {
    p = { id: o.id, direction: net > 0 ? 1 : -1, quantity: Math.abs(net), entry: price };
    a.positions[o.contract] = p;
  } else {
    if (oldNet * delta > 0) p.entry = (p.entry * Math.abs(oldNet) + price * qty) / Math.abs(net);
    p.quantity = Math.abs(net);
  }
  p = a.positions[o.contract];
  if (p && !o.reduceOnly && p.direction === side) {
    if (o.stopTicks !== undefined) p.stop = round((p.entry - side * o.stopTicks * c.tick) / c.tick) * c.tick;
    if (o.targetTicks !== undefined) p.target = round((p.entry + side * o.targetTicks * c.tick) / c.tick) * c.tick;
    if (o.stopPrice !== undefined) p.stop = o.stopPrice;
    if (o.targetPrice !== undefined) p.target = o.targetPrice;
  }
  o.remaining -= qty; o.filled += qty; o.notional += price * qty;
}
function execute(s: SimulationState, incoming: Order, resting: Order, quantity: number) {
  const key = incoming.contract, b = s.books[key], ticks = resting.price!;
  const price = ticks * s.config.contracts[key].tick;
  const trade = { id: ++s.nextTrade, time: s.time, step: s.step, contract: key, ticks, quantity,
    buyer: incoming.direction === 1 ? incoming.owner : resting.owner,
    seller: incoming.direction === -1 ? incoming.owner : resting.owner, direction: incoming.direction };
  fillAccount(s, incoming, price, quantity); fillAccount(s, resting, price, quantity);
  if (b.last !== null) {
    if (ticks > b.last) b.up += 1; else if (ticks < b.last) b.down += 1;
    b.volatility = b.volatility * 0.96 + Math.abs(ticks - b.last) * 0.04;
  }
  // Only executions write market prices or candles. News/fair value are never prices.
  b.last = ticks; b.open ??= ticks; b.volume += quantity; s.totalVolume += quantity;
  b.recent.push(ticks); if (b.recent.length > 64) b.recent.shift();
  s.trades.push(trade); if (s.trades.length > MAX_TRADES) s.trades.shift();
  s.tradeHash = (s.tradeHash * 31 + ticks * 7 + quantity * 13 + trade.id + s.step) % 2147483647;
  aggregateExecution(b, s.time, price, quantity);
  let flow = b.flow.at(-1);
  if (!flow || flow.time !== s.time) { flow = { time: s.time, buy: 0, sell: 0 }; b.flow.push(flow); }
  if (incoming.direction === 1) flow.buy += quantity; else flow.sell += quantity;
  for (const id of s.accountOrder) {
    const p = s.accounts[id].positions[key];
    if (p && !p.triggered) {
      const reason = p.stop !== undefined && (price - p.stop) * p.direction <= 0 ? 'Stop loss'
        : p.target !== undefined && (price - p.target) * p.direction >= 0 ? 'Take profit' : null;
      if (reason) {
        p.triggered = reason;
        if (reason === 'Stop loss') delete p.target; else delete p.stop;
        s.pendingStops.push({ owner: id, contract: key, position: p.id });
      }
    }
  }
}
// Absolute structural brackets stay beyond the structure after partial fills or
// price improvement. Do not fill an entry through its stop or without target room.
function bracketAllows(s: SimulationState, o: Order, price: number) {
  if (o.stopPrice === undefined || o.targetPrice === undefined) return true;
  if (s.accounts[o.owner].positions[o.contract]?.triggered) return false;
  const risk = (price - o.stopPrice) * o.direction, reward = (o.targetPrice - price) * o.direction;
  return risk > 0 && reward > 0 && reward + 1e-8 >= risk * (o.minimumRewardRisk ?? 0);
}
export function submitOrder(s: SimulationState, owner: string, request: OrderRequest): Order | null {
  const a = s.accounts[owner], b = s.books[request.contract];
  const side = request.direction, qty = request.quantity, kind = request.type;
  if (!a || !b || (side !== 1 && side !== -1) || !Number.isInteger(qty) || qty < 1 || qty > 10000
    || (kind !== 'Market' && kind !== 'Limit')) return null;
  const p = a.positions[request.contract], reducing = request.reduceOnly === true;
  if (reducing && (!p || p.direction === side || qty > p.quantity)) return null;
  if ((a.locked || a.liquidating) && !reducing) return null;
  let price: number | undefined;
  if (kind === 'Limit') {
    const value = request.price, tick = s.config.contracts[request.contract].tick;
    if (value === undefined || !Number.isFinite(value) || value <= 0 || Math.abs(value / tick - round(value / tick)) > 0.00001) return null;
    price = round(value / tick);
    if (Math.abs(price - (b.last ?? b.reference)) > (b.last ?? b.reference) * s.config.priceBandFraction) return null;
  }
  for (const v of [request.stopTicks, request.targetTicks]) if (v !== undefined && (!Number.isInteger(v) || v < 1 || v > 10000)) return null;
  if (request.stopPrice !== undefined || request.targetPrice !== undefined || request.minimumRewardRisk !== undefined) {
    const tick = s.config.contracts[request.contract].tick;
    if (reducing || ![request.stopPrice, request.targetPrice].every(v => v !== undefined && Number.isFinite(v) && v > 0 && Math.abs(v / tick - round(v / tick)) < 1e-5)
      || (request.targetPrice! - request.stopPrice!) * side <= 0
      || !Number.isFinite(request.minimumRewardRisk) || request.minimumRewardRisk! < 1 || request.minimumRewardRisk! > 10) return null;
  }
  if (!reducing && !exposureAllowed(s, a, request.contract, side, qty)) return null;
  clean(s, b);
  const opposing = side === 1 ? b.asks : b.bids;
  const o: Order = { id: ++s.nextOrder, owner, contract: request.contract, direction: side, original: qty, remaining: qty,
    filled: 0, cancelled: 0, notional: 0, type: kind, status: 'Working', time: s.time,
    reference: s.orders[opposing[0]]?.price ?? b.last ?? b.reference, reduceOnly: reducing,
    ...(price !== undefined ? { price } : {}),
    ...(request.stopTicks !== undefined ? { stopTicks: request.stopTicks } : {}),
    ...(request.targetTicks !== undefined ? { targetTicks: request.targetTicks } : {}),
    ...(request.stopPrice !== undefined ? { stopPrice: request.stopPrice, targetPrice: request.targetPrice, minimumRewardRisk: request.minimumRewardRisk } : {}),
    ...(request.expires !== undefined ? { expires: request.expires } : {}) };
  s.orders[o.id] = o; a.openOrders.push(o.id);
  for (const id of opposing) {
    if (o.remaining <= 0 || (a.locked && !reducing)) break;
    const resting = s.orders[id];
    if (!resting || resting.remaining <= 0) continue;
    if (kind === 'Limit' && (resting.price! - price!) * side > 0) break;
    if (resting.owner === owner) {
      resting.cancelled += resting.remaining; resting.remaining = 0; retire(s, resting, 'Self-trade prevented');
      continue;
    }
    const executionPrice = resting.price! * s.config.contracts[request.contract].tick;
    if (!bracketAllows(s, resting, executionPrice)) {
      resting.cancelled += resting.remaining; resting.remaining = 0; retire(s, resting, 'Structure entry invalid'); continue;
    }
    if (!bracketAllows(s, o, executionPrice)) { o.cancelled += o.remaining; o.remaining = 0; break; }
    const ra = s.accounts[resting.owner], rp = ra.positions[o.contract];
    let available = resting.remaining;
    if (ra.locked && !resting.reduceOnly) available = 0;
    if (resting.reduceOnly) available = rp && rp.direction !== resting.direction ? Math.min(available, rp.quantity) : 0;
    if (available <= 0) { resting.cancelled += resting.remaining; resting.remaining = 0; retire(s, resting, 'Cancelled'); }
    else {
      execute(s, o, resting, Math.min(o.remaining, available));
      if (resting.remaining === 0) retire(s, resting, 'Filled');
    }
  }
  if (o.remaining === 0) retire(s, o, o.cancelled ? 'Structure entry invalid' : 'Filled');
  else if (kind === 'Market' || (a.locked && !reducing)) {
    o.cancelled += o.remaining; o.remaining = 0; retire(s, o, o.filled > 0 ? 'Partial / IOC' : 'No liquidity');
  } else {
    const same = side === 1 ? b.bids : b.asks;
    same.push(o.id);
    same.sort((left, right) => (s.orders[right].price! - s.orders[left].price!) * side || left - right);
  }
  clean(s, b);
  return o;
}
export function processStops(s: SimulationState) {
  let processed = 0;
  while (s.pendingStops.length && processed < 200) {
    const item = s.pendingStops.shift()!, a = s.accounts[item.owner], p = a?.positions[item.contract];
    processed += 1;
    if (p && p.id === item.position && p.triggered) {
      cancelAll(s, a.id, item.contract);
      submitOrder(s, a.id, { contract: item.contract, direction: -p.direction as Direction,
        quantity: Math.min(p.quantity, 10000), type: 'Market', reduceOnly: true });
    }
  }
}
export function advanceClock(s: SimulationState, beforeMatching?: (s: SimulationState) => void) {
  s.time += STEP_SECONDS; s.step += 1;
  for (const id of Object.keys(s.orders).map(Number).sort((a, b) => a - b)) {
    const o = s.orders[id]; if (o.expires !== undefined && o.expires <= s.time) cancelOrder(s, o.owner, id);
  }
  // Closed-candle strategies can retire entries before any queued exit or
  // liquidation matches orders at the new engine time.
  beforeMatching?.(s);
  for (const id of s.accountOrder) {
    const a = s.accounts[id], summary = accountSummary(s, a);
    if (!Object.keys(a.positions).length) a.liquidating = false;
    if (summary.equity <= 0 || summary.margin > Math.max(0, summary.equity) * 1.05) {
      if (summary.equity <= 0) a.locked = true;
      a.liquidating = true; cancelAll(s, id);
      for (const key of s.config.contractOrder) {
        const p = a.positions[key];
        if (p) { p.triggered = a.locked ? 'Account depleted' : 'Margin liquidation'; delete p.stop; delete p.target; }
      }
    }
    for (const key of s.config.contractOrder) {
      const p = a.positions[key]; if (p?.triggered) s.pendingStops.push({ owner: id, contract: key, position: p.id });
    }
  }
  processStops(s);
  for (const key of s.config.contractOrder) s.books[key].flow = s.books[key].flow.filter(f => f.time > s.time - 10);
}
