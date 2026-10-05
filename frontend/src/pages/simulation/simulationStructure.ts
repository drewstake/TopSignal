// These strategies observe executions and completed minute candles only. Their
// private random streams never advance the reference participants' shared RNG.
import { addAccount, cancelOrder, submitOrder } from './simulationExchange';
import { DEFAULT_CONFIG, random, type Direction, type Participant, type SimulationCandle, type SimulationState } from './simulationModel';
import { LEVEL_LIMIT, SIGNAL_LIMIT, STRUCTURE_BAR_LIMIT, ZONE_LIMIT,
  type PriceLevel, type PriceZone, type StructureSetup, type StructureState } from './simulationStructureModel';

export const isStructural = (a: Participant) => a.kind === 'SupplyDemand' || a.kind === 'SupportResistance';
const snap = (value: number, tick: number, direction: Direction) => (direction === 1 ? Math.floor(value / tick + 1e-8) : Math.ceil(value / tick - 1e-8)) * tick;
const intersects = (bar: SimulationCandle, low: number, high: number) => bar.low <= high && bar.high >= low;
const zoneName = (z: PriceZone) => `${z.side === 1 ? 'Demand' : 'Supply'} ${z.low.toFixed(2)}–${z.high.toFixed(2)} · ${z.tests} test${z.tests === 1 ? '' : 's'}`;
const levelName = (l: PriceLevel) => `${l.side === 1 ? 'Support' : 'Resistance'} ${l.price.toFixed(2)} · ${l.tests} test${l.tests === 1 ? '' : 's'}`;

export function needsStructureMigration(s: SimulationState) { return s.config.supplyDemandTraders === undefined; }

// Also used for compatible hot replacement. Existing state is never replayed,
// repriced or reseeded; new accounts are appended once with stable IDs.
export function initializeStructureParticipants(s: SimulationState) {
  s.config.supplyDemandTraders ??= DEFAULT_CONFIG.supplyDemandTraders;
  s.config.supportResistanceTraders ??= DEFAULT_CONFIG.supportResistanceTraders;
  s.config.structureRewardRisk ??= DEFAULT_CONFIG.structureRewardRisk;
  s.config.structureMinRewardRisk ??= DEFAULT_CONFIG.structureMinRewardRisk;
  for (const key of s.config.contractOrder) for (const kind of ['SupplyDemand', 'SupportResistance'] as const) {
    const count = kind === 'SupplyDemand' ? s.config.supplyDemandTraders : s.config.supportResistanceTraders;
    for (let index = 1; index <= count; index++) {
      const id = `bot:${key}:${kind}:${index}`;
      if (s.participants.some(a => a.id === id)) continue;
      let seed = Math.abs(s.initialSeed) + 1;
      for (const ch of id) seed = (seed * 31 + ch.charCodeAt(0)) % 2147483646;
      const rng = { randomState: seed + 1 };
      const between = (lo: number, hi: number) => lo + random(rng) * (hi - lo);
      const whole = (lo: number, hi: number) => Math.floor(between(lo, hi + 1));
      const bars = s.books[key].candles['1m'].slice(-STRUCTURE_BAR_LIMIT).filter(b => b.time + 60 <= s.time);
      const structure: StructureState = {
        version: 1, mode: kind === 'SupplyDemand' ? index === 1 ? 'zone-limit' : 'zone-rejection' : index === 1 ? 'level-rejection' : 'breakout-retest',
        randomState: 1, lookback: whole(32, 60), atrPeriod: whole(10, 16), baseLength: whole(2, 4), pivotLeft: whole(2, 4), pivotRight: whole(2, 3),
        tolerance: between(.15, .3), departure: between(1.15, 1.65), compactness: between(.85, 1.2),
        separation: whole(3, 5) * 60, maxAge: whole(60, 120) * 60, maxTests: whole(2, 3),
        checkedThrough: Math.floor(s.time / 60) * 60, lastBar: bars.at(-1)?.time ?? -1, serial: 0, atr: 0,
        bars, zones: [], levels: [], used: [], watched: 'None', reason: 'Waiting for completed 1-minute history and a new structure',
      };
      const a: Participant = { id, key, kind, budget: whole(50000, 100000), risk: between(.45, .65), limit: 2,
        speed: between(2.5, 8), size: whole(1, 2), patience: between(18, 35), next: s.time + between(.2, 12),
        // Legacy fields remain serializable but are never strategy inputs.
        bias: 0, belief: 0, aggression: .2, stop: 16, target: 20, parent: 0, parentSide: 1, structure };
      structure.randomState = rng.randomState;
      addAccount(s, id, a.budget, a.limit, a.risk); s.participants.push(a);
    }
  }
}

function volatility(q: StructureState, tick: number) {
  const bars = q.bars.slice(-q.lookback - 1);
  if (bars.length <= q.atrPeriod) return 0;
  const ranges = bars.slice(1).map((bar, i) => Math.max(bar.high - bar.low,
    Math.abs(bar.high - bars[i].close), Math.abs(bar.low - bars[i].close)));
  const short = ranges.slice(-q.atrPeriod).reduce((sum, value) => sum + value, 0) / q.atrPeriod;
  const long = ranges.reduce((sum, value) => sum + value, 0) / ranges.length;
  return Math.max(tick * 2, (short + long) / 2);
}
function offer(q: StructureState, setup: StructureSetup, watched: string) {
  if (q.entryOrder !== undefined || q.used.includes(setup.signal)) return;
  q.setup = setup; q.watched = watched; q.reason = 'Closed-candle confirmation; waiting for scheduled decision';
}
function updateZones(q: StructureState, bar: SimulationCandle, tick: number) {
  const now = bar.time + 60, buffer = Math.max(tick * 2, q.atr * .2);
  q.zones = q.zones.filter(z => {
    if (now - z.createdAt > q.maxAge || (bar.close - (z.side === 1 ? z.low - buffer : z.high + buffer)) * z.side < 0) return false;
    const touch = intersects(bar, z.low, z.high);
    if (touch && !z.touching && (z.lastTest < 0 || bar.time - z.lastTest >= q.separation)) { z.tests++; z.lastTest = bar.time; }
    z.touching = touch;
    return z.tests <= q.maxTests;
  });
  if (q.mode === 'zone-rejection') for (const z of [...q.zones].reverse()) {
    if (z.touching && z.lastTest >= 0 && (bar.close - (z.side === 1 ? z.high : z.low)) * z.side > tick
      && (bar.close - bar.open) * z.side > 0) {
      offer(q, { signal: `zone:${z.id}:${z.lastTest}`, source: 'zone', structureId: z.id, side: z.side,
        entry: bar.close, stop: snap(z.side === 1 ? z.low - buffer : z.high + buffer, tick, z.side), createdAt: now, expires: now + 60 }, zoneName(z));
      break;
    }
  }
  const base = q.bars.slice(-q.baseLength);
  if (!q.atr || base.length !== q.baseLength || base.some((b, i) => b.time !== bar.time - (base.length - i) * 60)) return;
  const low = Math.min(...base.map(b => b.low)), high = Math.max(...base.map(b => b.high));
  if (high - low > q.atr * q.compactness || Math.abs(bar.close - bar.open) < q.atr * .8) return;
  const side: Direction | null = bar.close > high + q.atr * q.departure ? 1 : bar.close < low - q.atr * q.departure ? -1 : null;
  if (side && !q.zones.some(z => z.side === side && Math.abs(z.low - low) < tick && Math.abs(z.high - high) < tick)) {
    q.zones.push({ id: ++q.serial, side, low, high, createdAt: now, tests: 0, lastTest: -1, touching: false });
    q.zones = q.zones.slice(-ZONE_LIMIT);
  }
}
function updateLevels(q: StructureState, bar: SimulationCandle, tick: number) {
  const now = bar.time + 60, tolerance = Math.max(tick * 2, q.atr * q.tolerance), buffer = Math.max(tick * 2, q.atr * .2);
  const prior = q.bars.at(-1);
  q.levels = q.levels.filter(l => {
    if (now - Math.max(l.createdAt, l.lastTest + 60) > q.maxAge) return false;
    if ((bar.close - l.price) * l.side < -tolerance) {
      if (q.mode === 'breakout-retest' && l.tests >= 2 && prior && (prior.close - l.price) * l.side >= -tolerance && !q.breakout) {
        q.breakout = { levelId: l.id, side: -l.side as Direction, price: l.price, tolerance, brokenAt: now, expires: now + q.separation * 4 };
        q.watched = levelName(l); q.reason = 'Breakout closed; waiting for a later successful retest';
      }
      return false;
    }
    const touch = intersects(bar, l.price - tolerance, l.price + tolerance);
    if (touch && !l.touching && bar.time - l.lastTest >= q.separation && (bar.close - l.price) * l.side > tolerance) {
      l.tests++; l.lastTest = bar.time;
    }
    l.touching = touch;
    if (q.mode === 'level-rejection' && touch && l.tests >= 2 && (bar.close - l.price) * l.side > tolerance && (bar.close - bar.open) * l.side > 0) {
      offer(q, { signal: `level:${l.id}:${l.lastTest}`, source: 'level', structureId: l.id, side: l.side,
        entry: bar.close, stop: snap(l.side === 1 ? Math.min(l.price - tolerance, bar.low) - buffer : Math.max(l.price + tolerance, bar.high) + buffer, tick, l.side),
        createdAt: now, expires: now + 60 }, levelName(l));
    }
    return true;
  });
  const breakout = q.breakout;
  if (breakout) {
    const { side, price, tolerance: tol } = breakout;
    if (now > breakout.expires || (bar.close - price) * side < -tol) {
      delete q.breakout; if (q.setup?.source === 'breakout') delete q.setup;
      q.reason = 'Breakout expired or closed back through the failed structure';
    } else if (bar.time >= breakout.brokenAt && intersects(bar, price - tol, price + tol)
      && (bar.close - price) * side > tol && (bar.close - bar.open) * side > 0) {
      offer(q, { signal: `breakout:${breakout.levelId}:${breakout.brokenAt}`, source: 'breakout', structureId: breakout.levelId, side,
        entry: bar.close, stop: snap(side === 1 ? Math.min(price - tol, bar.low) - buffer : Math.max(price + tol, bar.high) + buffer, tick, side),
        createdAt: now, expires: now + 60 }, `Breakout retest ${price.toFixed(2)}`);
    }
  }
  // The right-hand window includes only candles that have just completed.
  const history = [...q.bars.slice(-q.lookback), bar], at = history.length - 1 - q.pivotRight;
  if (!q.atr || at < q.pivotLeft) return;
  const pivot = history[at], window = history.slice(at - q.pivotLeft, at + q.pivotRight + 1);
  for (const side of [1, -1] as const) {
    const price = side === 1 ? pivot.low : pivot.high;
    if (!window.every(b => b === pivot || (side === 1 ? b.low > price : b.high < price))) continue;
    // A newly confirmed old swing that has already broken is not a live level.
    if ((bar.close - price) * side < -tolerance) continue;
    const merged = q.levels.filter(l => l.side === side && Math.abs(l.price - price) <= tolerance).sort((a, b) => Math.abs(a.price - price) - Math.abs(b.price - price))[0];
    if (merged) {
      if (pivot.time - merged.lastPivot >= q.separation) {
        // Count a pivot and a live touch of the same excursion only once.
        if (pivot.time - merged.lastTest >= q.separation) { merged.tests++; merged.lastTest = pivot.time; }
        merged.price = snap((merged.price + price) / 2, tick, side); merged.lastPivot = pivot.time;
      }
    } else q.levels.push({ id: ++q.serial, side, price, createdAt: now, lastTest: pivot.time, lastPivot: pivot.time, tests: 1,
      touching: intersects(bar, price - tolerance, price + tolerance) });
  }
  q.levels = q.levels.slice(-LEVEL_LIMIT);
}

function setupAlive(q: StructureState) {
  const setup = q.setup;
  return !!setup && (setup.source === 'zone' ? q.zones.some(z => z.id === setup.structureId)
    : setup.source === 'level' ? q.levels.some(l => l.id === setup.structureId) : q.breakout?.levelId === setup.structureId);
}

export function updateStructure(s: SimulationState, a: Participant) {
  const q = a.structure!;
  const boundary = Math.floor(s.time / 60) * 60;
  if (boundary <= q.checkedThrough) return;
  q.checkedThrough = boundary;
  if (q.entryOrder !== undefined && !s.orders[q.entryOrder]) delete q.entryOrder;
  // Normally one new bar. Never scan the retained 1,200-bar chart history.
  const recent = s.books[a.key].candles['1m'].slice(-STRUCTURE_BAR_LIMIT);
  for (const bar of recent) {
    if (bar.time <= q.lastBar || bar.time + 60 > s.time) continue;
    const tick = s.config.contracts[a.key].tick;
    q.atr = volatility(q, tick);
    if (q.atr) {
      if (a.kind === 'SupplyDemand') updateZones(q, bar, tick); else updateLevels(q, bar, tick);
    }
    q.bars.push(bar); q.bars = q.bars.slice(-STRUCTURE_BAR_LIMIT); q.lastBar = bar.time;
  }
  // Age also advances across empty minute windows, without fabricating candles.
  q.zones = q.zones.filter(z => s.time - z.createdAt <= q.maxAge);
  q.levels = q.levels.filter(l => s.time - Math.max(l.createdAt, l.lastTest + 60) <= q.maxAge);
  if (q.breakout && s.time > q.breakout.expires) delete q.breakout;
  if (q.setup && (!setupAlive(q) || s.time >= q.setup.expires)) {
    if (q.entryOrder !== undefined) cancelOrder(s, a.id, q.entryOrder);
    delete q.entryOrder; delete q.setup; q.reason = 'Setup retired, invalidated or expired; waiting for a new signal';
  }
}

function opposingTarget(q: StructureState, side: Direction, entry: number, stop: number, tick: number, rr: number) {
  const candidates = q.mode.startsWith('zone') ? q.zones.filter(z => z.side !== side).map(z => side === 1 ? z.low : z.high)
    : q.levels.filter(l => l.side !== side && l.tests >= 2).map(l => l.price);
  const next = candidates.filter(price => (price - entry) * side > 0).sort((a, b) => (a - b) * side)[0];
  return snap(next === undefined ? entry + (entry - stop) * rr : next - side * tick, tick, side);
}

export function tradeStructure(s: SimulationState, a: Participant) {
  const q = a.structure!, account = s.accounts[a.id], book = s.books[a.key], tick = s.config.contracts[a.key].tick;
  const position = account.positions[a.key];
  if (account.locked || account.liquidating || position) {
    if (q.entryOrder !== undefined) cancelOrder(s, a.id, q.entryOrder);
    delete q.entryOrder;
    q.reason = account.locked ? 'Account halted' : account.liquidating ? 'Exchange liquidation pending' : position?.triggered ? `${position.triggered}: waiting for exit liquidity` : 'Position open; managing exchange stop and target';
    return;
  }
  if (account.openOrders.some(id => !s.orders[id].reduceOnly)) { q.reason = 'Entry working; waiting for a fill or expiry'; return; }
  delete q.entryOrder;
  if (!q.atr) { q.reason = 'Insufficient completed 1-minute history'; return; }
  const last = (book.last ?? book.reference) * tick;
  if (q.mode === 'zone-limit') {
    delete q.setup;
    for (const z of [...q.zones].sort((a, b) => a.tests - b.tests || b.createdAt - a.createdAt)) {
      const signal = `limit:${z.id}`;
      if (q.used.includes(signal)) continue;
      q.watched = zoneName(z);
      const entry = snap(z.side === 1 ? z.high - (z.high - z.low) * .25 : z.low + (z.high - z.low) * .25, tick, z.side);
      if ((last - entry) * z.side < tick || (last - entry) * z.side > q.atr * 1.5) continue;
      const buffer = Math.max(tick * 2, q.atr * .2);
      q.setup = { signal, source: 'zone', structureId: z.id, side: z.side, entry,
        stop: snap(z.side === 1 ? z.low - buffer : z.high + buffer, tick, z.side), createdAt: s.time, expires: s.time + a.patience * 3 };
      break;
    }
  }
  const setup = q.setup;
  if (!setup || q.used.includes(setup.signal) || !setupAlive(q) || s.time >= setup.expires) {
    delete q.setup;
    if (q.breakout) { q.watched = `Breakout ${q.breakout.price.toFixed(2)}`; q.reason = 'Waiting for a later closed-candle breakout retest'; }
    else {
      const watch = a.kind === 'SupplyDemand' ? q.zones.at(-1) : q.levels.at(-1);
      q.watched = watch ? 'price' in watch ? levelName(watch) : zoneName(watch) : 'None';
      q.reason = !watch ? 'No eligible structure; waiting for completed candles'
        : 'price' in watch && watch.tests < 2 ? 'Waiting for a second separated test of the swing level'
          : q.mode === 'breakout-retest' ? 'Waiting for a close beyond a confirmed level'
            : q.mode === 'zone-limit' ? 'Waiting for price to approach a fresh zone'
              : 'Waiting for a fresh return and a closed-candle rejection';
    }
    return;
  }
  const passive = q.mode === 'zone-limit', side = setup.side;
  const quoteId = (side === 1 ? book.asks : book.bids)[0], quote = s.orders[quoteId]?.price;
  if (!passive && quote === undefined) { q.reason = 'No opposing liquidity for confirmed entry'; return; }
  // Confirmed entries use a capped marketable limit, then cancel any remainder.
  // This bounds slippage and preserves minimum reward/risk at every actual fill.
  const entry = passive ? setup.entry : quote! * tick;
  const stop = setup.stop, target = opposingTarget(q, side, entry, stop, tick, s.config.structureRewardRisk);
  const risk = (entry - stop) * side, reward = (target - entry) * side;
  if (risk < tick || stop <= 0 || reward < risk * s.config.structureMinRewardRisk) { q.reason = 'Insufficient room to target for minimum reward/risk'; return; }
  const cap = snap((target + s.config.structureMinRewardRisk * stop) / (1 + s.config.structureMinRewardRisk), tick, side);
  const price = passive ? entry : side === 1 ? Math.min(cap, entry + tick * 2) : Math.max(cap, entry - tick * 2);
  q.used.push(setup.signal); q.used = q.used.slice(-SIGNAL_LIMIT);
  const order = submitOrder(s, a.id, { contract: a.key, direction: side, quantity: a.size, type: 'Limit', price,
    stopPrice: stop, targetPrice: target, minimumRewardRisk: s.config.structureMinRewardRisk, expires: setup.expires });
  if (!order) { q.reason = 'Entry rejected by exchange capital, margin, inventory or price limits'; return; }
  if (!passive || order.filled > 0) cancelOrder(s, a.id, order.id);
  if (s.orders[order.id]) q.entryOrder = order.id;
  q.reason = order.filled ? `${side === 1 ? 'Bought' : 'Sold'} ${order.filled} contract(s); exchange stop and target active`
    : passive && s.orders[order.id] ? 'Patient limit working at the watched zone' : 'No fill within entry slippage and reward/risk limits';
}

export function scheduleStructure(s: SimulationState, a: Participant) {
  a.next = s.time + a.speed * (.6 + random(a.structure!) * 1.1);
}
