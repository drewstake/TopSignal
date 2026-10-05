import type { SimulationSession } from './simulationControls';
import { initializeStructureParticipants, isStructural, needsStructureMigration } from './simulationStructure';
import { validStructure } from './simulationStructureValidation';
import { DEFAULT_CONFIG, INSTRUMENTS, MAX_CANDLES, MAX_TRADES, PARTICIPANT_GROUPS, STEP_SECONDS, TIMEFRAMES,
  type SimulationState } from './simulationModel';

export const LEGACY_SIMULATION_STORAGE_KEY = 'topsignal.simulation.session.v1';
export const PREVIOUS_SIMULATION_STORAGE_KEY = 'topsignal.simulation.session.v2';
// Older clients did not stop saving after their Web Lock was revoked. Keep the
// coordinated writer's save separate, importing the prior checkpoint only once.
export const SIMULATION_STORAGE_KEY = 'topsignal.simulation.session.v2.owned';
export interface SimulationCheckpoint { version: 2; savedAt: number; session: SimulationSession }
export type CheckpointStorage = Pick<Storage, 'getItem' | 'setItem'>;
// Conservative UTF-16 byte accounting. Quota errors leave the previous save intact.
export const MAX_CHECKPOINT_BYTES = 4_500_000;
const num = (v: unknown, min = -1e15, max = 1e15): v is number => typeof v === 'number' && Number.isFinite(v) && v >= min && v <= max;
const integer = (v: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): v is number => num(v, min, max) && Number.isInteger(v);
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const array = (v: unknown, max: number): v is unknown[] => Array.isArray(v) && v.length <= max;
const side = (v: unknown) => v === 1 || v === -1;
function safeTree(v: unknown, depth = 0): boolean {
  if (depth > 20) return false;
  if (typeof v === 'number') return num(v);
  if (typeof v === 'string') return v.length < 256;
  if (v === null || typeof v === 'boolean') return true;
  if (Array.isArray(v)) return v.every(x => safeTree(x, depth + 1));
  return record(v) && Object.entries(v).every(([k, x]) => !['__proto__', 'constructor', 'prototype'].includes(k) && safeTree(x, depth + 1));
}
function validMarket(s: SimulationState) {
  if (s.engineVersion !== 2 || !integer(s.step) || s.time !== s.step * STEP_SECONDS || !integer(s.initialSeed, -2147483646, 2147483646)
    || !integer(s.randomState, 1, 2147483646) || !integer(s.nextOrder) || !integer(s.nextTrade)
    || !integer(s.totalVolume) || !integer(s.tradeHash, 0, 2147483646)) return false;
  const c = s.config;
  const legacy = needsStructureMigration(s);
  if (legacy ? [c.supportResistanceTraders, c.structureRewardRisk, c.structureMinRewardRisk].some(v => v !== undefined)
    : !integer(c.supplyDemandTraders, 0, 2) || !integer(c.supportResistanceTraders, 0, 2)
      || !num(c.structureRewardRisk, 1, 10) || !num(c.structureMinRewardRisk, 1, c.structureRewardRisk)) return false;
  if (JSON.stringify(c.contractOrder) !== JSON.stringify(INSTRUMENTS)
    || !integer(c.makers, 0, 5) || !integer(c.traders, 0, 36) || !integer(c.whales, 0, 3)
    || !num(c.liquidity, 0.1, 4) || !num(c.volatility, 0.1, 4) || !num(c.whaleFrequency, 0, 4) || !num(c.newsFrequency, 0, 4)
    || c.priceBandFraction !== 0.1 || JSON.stringify(c.regimeSeconds) !== '[100,240]'
    || JSON.stringify(c.regimes) !== JSON.stringify(DEFAULT_CONFIG.regimes)
    || JSON.stringify(c.weights) !== JSON.stringify(DEFAULT_CONFIG.weights)) return false;
  for (const key of INSTRUMENTS) if (JSON.stringify(c.contracts[key]) !== JSON.stringify(DEFAULT_CONFIG.contracts[key])) return false;
  if (!array(s.participants, 144) || !array(s.accountOrder, 144) || !record(s.accounts)
    || new Set(s.accountOrder).size !== s.accountOrder.length || Object.keys(s.accounts).length !== s.accountOrder.length
    || s.participants.length !== s.accountOrder.length || !record(s.orders) || Object.keys(s.orders).length > 2048
    || !array(s.pendingStops, 2000) || !array(s.trades, MAX_TRADES) || !array(s.events, 40)) return false;
  const participantIds = new Set<string>();
  for (const p of s.participants) {
    if (!record(p) || typeof p.id !== 'string' || participantIds.has(p.id) || !s.accounts[p.id] || !INSTRUMENTS.includes(p.key)
      || !PARTICIPANT_GROUPS.some(g => g.kind === p.kind) || !integer(p.budget, 1) || !num(p.risk, 0.45, 0.85)
      || !integer(p.limit, 1, 350) || !num(p.speed, 1.5, 100) || !integer(p.size, 1, 60)
      || !num(p.bias) || !num(p.belief) || !num(p.patience, 4, 35) || !num(p.aggression, 0.2, 0.8)
      || !num(p.next, 0) || !integer(p.stop, 16, 85) || !integer(p.target, 20, 140) || !integer(p.parent, 0, 240) || !side(p.parentSide)) return false;
    participantIds.add(p.id);
    if (isStructural(p) ? legacy || !validStructure(s, p) : p.structure !== undefined) return false;
  }
  for (const key of INSTRUMENTS) for (const kind of ['SupplyDemand', 'SupportResistance'] as const) {
    const agents = s.participants.filter(p => p.key === key && p.kind === kind);
    const expected = legacy ? 0 : kind === 'SupplyDemand' ? c.supplyDemandTraders : c.supportResistanceTraders;
    if (agents.length !== expected || agents.some(p => !Array.from({ length: expected }, (_, i) => `bot:${key}:${kind}:${i + 1}`).includes(p.id))) return false;
  }
  const accountedOrders: number[] = [];
  for (const id of s.accountOrder) {
    const a = s.accounts[id];
    if (!a || a.id !== id || !participantIds.has(id) || !num(a.base, 1) || !num(a.balance) || !num(a.realized) || !num(a.cashFlow)
      || !integer(a.limit, 1, 350) || !num(a.risk, 0.45, 0.85) || typeof a.locked !== 'boolean' || typeof a.liquidating !== 'boolean'
      || !record(a.positions) || !array(a.openOrders, 2048) || ![a.bought, a.sold, a.cancelled, a.completed].every(v => integer(v))) return false;
    for (const [key, p] of Object.entries(a.positions)) {
      if (!INSTRUMENTS.includes(key as typeof INSTRUMENTS[number]) || !p || !integer(p.id, 1, s.nextOrder) || !side(p.direction)
        || !integer(p.quantity, 1, 10000) || !num(p.entry, 0) || (p.stop !== undefined && !num(p.stop))
        || (p.target !== undefined && !num(p.target)) || (p.triggered !== undefined && !['Stop loss', 'Take profit', 'Margin liquidation', 'Account depleted'].includes(p.triggered))) return false;
    }
    for (const orderId of a.openOrders) { if (!s.orders[orderId] || s.orders[orderId].owner !== id) return false; accountedOrders.push(orderId); }
    const net = Object.values(a.positions).reduce((n, p) => n + p!.quantity * p!.direction, 0);
    if (a.bought - a.sold !== net || Math.abs(a.balance - a.base - a.realized) > Math.max(1, Math.abs(a.balance)) * 1e-9) return false;
  }
  const bookOrders: number[] = [];
  for (const key of INSTRUMENTS) {
    const b = s.books[key], m = s.markets[key];
    if (!b || !m || !num(m.fair) || !num(m.anchor) || !num(m.impulse) || !integer(b.reference, 1)
      || (b.last !== null && !integer(b.last, 1)) || (b.open !== null && !integer(b.open, 1))
      || !integer(b.volume) || !integer(b.up) || !integer(b.down) || !num(b.volatility, 0)
      || !array(b.recent, 64) || !b.recent.every(v => integer(v, 1)) || !array(b.flow, 41)
      || !b.flow.every(f => num(f.time, 0, s.time) && integer(f.buy) && integer(f.sell))) return false;
    for (const [list, direction] of [[b.bids, 1], [b.asks, -1]] as const) {
      if (!array(list, 2048)) return false;
      let prior: typeof s.orders[number] | undefined;
      for (const id of list) {
        const o = s.orders[id];
        if (!o || o.contract !== key || o.direction !== direction || (prior && ((o.price! - prior.price!) * direction > 0 || (o.price === prior.price && o.id <= prior.id)))) return false;
        prior = o; bookOrders.push(id);
      }
    }
    for (const [tf, seconds] of TIMEFRAMES) {
      const series = b.candles[tf];
      if (!array(series, MAX_CANDLES) || !integer(b.candleVolume[tf]) || b.candleVolume[tf] !== b.volume) return false;
      let prior = -1;
      for (const bar of series) {
        if (!integer(bar.time, 0, s.time) || bar.time % seconds || bar.time <= prior || !integer(bar.volume, 1)
          || ![bar.open, bar.high, bar.low, bar.close].every(v => num(v, 0) && Math.abs(v / c.contracts[key].tick - Math.round(v / c.contracts[key].tick)) < 1e-5)
          || bar.high < Math.max(bar.open, bar.close) || bar.low > Math.min(bar.open, bar.close)) return false;
        prior = bar.time;
      }
      if (series.reduce((n, bar) => n + bar.volume, 0) > b.volume || (b.last !== null && (!series.length || Math.abs(series.at(-1)!.close - b.last * c.contracts[key].tick) > 1e-7))
        || (b.last === null && (series.length !== 0 || b.volume !== 0))) return false;
    }
  }
  for (const [id, o] of Object.entries(s.orders)) {
    if (Number(id) !== o.id || !integer(o.id, 1, s.nextOrder) || !s.accounts[o.owner] || !INSTRUMENTS.includes(o.contract)
      || o.type !== 'Limit' || !side(o.direction) || !integer(o.price, 1) || !integer(o.original, 1, 10000)
      || !integer(o.remaining, 1, o.original) || !integer(o.filled) || !integer(o.cancelled)
      || o.original !== o.remaining + o.filled + o.cancelled || !num(o.notional, 0) || !num(o.time, 0, s.time)
      || !integer(o.reference, 1) || typeof o.reduceOnly !== 'boolean' || o.status !== 'Working'
      || (o.expires !== undefined && !num(o.expires, s.time))
      || [o.stopTicks, o.targetTicks].some(v => v !== undefined && !integer(v, 1, 10000))) return false;
    if (o.stopPrice !== undefined || o.targetPrice !== undefined || o.minimumRewardRisk !== undefined) {
      const tick = c.contracts[o.contract].tick;
      if (o.reduceOnly || ![o.stopPrice, o.targetPrice].every(v => num(v, tick) && Math.abs(v / tick - Math.round(v / tick)) < 1e-5)
        || (o.targetPrice! - o.stopPrice!) * o.direction <= 0 || !num(o.minimumRewardRisk, 1, 10)) return false;
    }
  }
  const count = Object.keys(s.orders).length;
  if (bookOrders.length !== count || accountedOrders.length !== count || new Set(bookOrders).size !== count || new Set(accountedOrders).size !== count) return false;
  if (!s.pendingStops.every(p => !!s.accounts[p.owner] && INSTRUMENTS.includes(p.contract) && integer(p.position, 1, s.nextOrder))) return false;
  let priorTrade = 0;
  let priorStep = -1;
  for (const t of s.trades) {
    if (!integer(t.id, priorTrade + 1, s.nextTrade) || !integer(t.step, 0, s.step) || t.time !== t.step * STEP_SECONDS
      || !INSTRUMENTS.includes(t.contract) || !integer(t.ticks, 1) || !integer(t.quantity, 1, 10000) || !side(t.direction)
      || !s.accounts[t.buyer] || !s.accounts[t.seller] || t.buyer === t.seller) return false;
    if (t.step < priorStep) return false;
    priorStep = t.step; priorTrade = t.id;
  }
  if (priorTrade !== s.nextTrade || INSTRUMENTS.reduce((n, key) => n + s.books[key].volume, 0) !== s.totalVolume) return false;
  for (const key of INSTRUMENTS) {
    const tape = s.trades.filter(t => t.contract === key), last = tape.at(-1);
    if (tape.reduce((n, t) => n + t.quantity, 0) > s.books[key].volume || (last && last.ticks !== s.books[key].last)) return false;
  }
  return c.regimes.some(r => JSON.stringify(r) === JSON.stringify(s.regime)) && side(s.direction)
    && [s.nextRegime, s.nextNews, s.nextBeliefs, s.newsUntil].every(v => num(v, 0)) && typeof s.news === 'string'
    && s.events.every(e => integer(e.step, 0, s.step) && ['Regime', 'News', 'Override'].includes(e.kind) && typeof e.name === 'string' && side(e.direction));
}
// Compact only presentation arrays; no rounding, truncation or RNG changes.
export function encodeSimulationCheckpoint(session: SimulationSession, savedAt: number) {
  return JSON.stringify({ version: 2, savedAt, session }, (key, value) => {
    if (key === 'candles') return Object.fromEntries(TIMEFRAMES.map(([tf]) => [tf,
      (value as SimulationState['books']['BX']['candles'])[tf].map(b => [b.time, b.open, b.high, b.low, b.close, b.volume])]));
    if (key === 'trades') return (value as SimulationState['trades']).map(t => [t.id, t.time, t.step, t.contract, t.ticks, t.quantity, t.buyer, t.seller, t.direction]);
    return value;
  });
}
export function decodeSimulationCheckpoint(raw: string | null): SimulationCheckpoint | null {
  if (!raw || raw.length * 2 > MAX_CHECKPOINT_BYTES) return null;
  try {
    const parsed = JSON.parse(raw, (key, value) => {
      if (key === 'candles') return Object.fromEntries(TIMEFRAMES.map(([tf]) => [tf, value[tf].map((b: number[]) => {
        if (!Array.isArray(b) || b.length !== 6) throw new Error('Invalid candle tuple');
        return { time: b[0], open: b[1], high: b[2], low: b[3], close: b[4], volume: b[5] };
      })]));
      if (key === 'trades') return value.map((t: unknown[]) => {
        if (!Array.isArray(t) || t.length !== 9) throw new Error('Invalid trade tuple');
        return { id: t[0], time: t[1], step: t[2], contract: t[3], ticks: t[4], quantity: t[5], buyer: t[6], seller: t[7], direction: t[8] };
      });
      return value;
    }) as SimulationCheckpoint;
    const s = parsed.session;
    if (!safeTree(parsed) || parsed.version !== 2 || !num(parsed.savedAt, 0) || typeof s.running !== 'boolean'
      || ![0.5, 1, 2, 4].includes(s.speed) || !integer(s.generation) || !INSTRUMENTS.includes(s.instrument)
      || !TIMEFRAMES.some(([tf]) => tf === s.timeframe) || !num(s.fractionalSteps, 0, 1 - Number.EPSILON) || !validMarket(s.market)) return null;
    if (needsStructureMigration(s.market)) {
      initializeStructureParticipants(s.market);
      if (!validMarket(s.market)) return null;
    }
    return parsed;
  } catch { return null; }
}
export function inspectSimulationStorage(storage: CheckpointStorage | null) {
  try {
    const raw = storage?.getItem(SIMULATION_STORAGE_KEY) ?? storage?.getItem(PREVIOUS_SIMULATION_STORAGE_KEY) ?? null;
    const legacy = !!storage?.getItem(LEGACY_SIMULATION_STORAGE_KEY);
    const checkpoint = decodeSimulationCheckpoint(raw), unreadable = !!raw && !checkpoint;
    return { checkpoint, legacy, unreadable, notice: unreadable ? 'Saved simulation could not be read; original data preserved. Reset to save a new run.'
      : legacy ? 'Legacy simulation saved separately; original data preserved.' : '' };
  } catch { return { checkpoint: null, legacy: false, unreadable: true, notice: 'Browser storage cannot be read.' }; }
}
export function readSimulationCheckpoint(storage: CheckpointStorage | null): SimulationCheckpoint | null { return inspectSimulationStorage(storage).checkpoint; }
export function saveSimulationCheckpoint(session: SimulationSession, savedAt = Date.now(), storage?: CheckpointStorage | null): boolean {
  try {
    const target = storage === undefined ? localStorage : storage;
    const raw = encodeSimulationCheckpoint(session, savedAt);
    if (!target || raw.length * 2 > MAX_CHECKPOINT_BYTES) return false;
    target.setItem(SIMULATION_STORAGE_KEY, raw);
    return true;
  } catch { return false; }
}
