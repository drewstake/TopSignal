import type { Participant, SimulationState } from './simulationModel';
import { LEVEL_LIMIT, SIGNAL_LIMIT, STRUCTURE_BAR_LIMIT, ZONE_LIMIT } from './simulationStructureModel';

const num = (v: unknown, lo: number, hi: number): v is number => typeof v === 'number' && Number.isFinite(v) && v >= lo && v <= hi;
const int = (v: unknown, lo: number, hi: number): v is number => num(v, lo, hi) && Number.isInteger(v);
const side = (v: unknown) => v === 1 || v === -1;
const list = (v: unknown, max: number) => Array.isArray(v) && v.length <= max;

export function validStructure(s: SimulationState, a: Participant) {
  const q = a.structure, tick = s.config.contracts[a.key].tick;
  const price = (v: unknown): v is number => num(v, tick, 1e15) && Math.abs(v / tick - Math.round(v / tick)) < 1e-5;
  const minute = (v: unknown) => int(v, 0, s.time) && v % 60 === 0;
  const modes = a.kind === 'SupplyDemand' ? ['zone-limit', 'zone-rejection'] : ['level-rejection', 'breakout-retest'];
  if (!q || q.version !== 1 || !modes.includes(q.mode) || !int(q.randomState, 1, 2147483646) || a.limit !== 2 || a.size > 2
    || !int(q.lookback, 8, STRUCTURE_BAR_LIMIT) || !int(q.atrPeriod, 2, 32) || !int(q.baseLength, 2, 6)
    || !int(q.pivotLeft, 1, 6) || !int(q.pivotRight, 1, 6) || !num(q.tolerance, .05, 1)
    || !num(q.departure, .5, 4) || !num(q.compactness, .25, 2) || !int(q.separation, 60, 600)
    || !int(q.maxAge, 60, 86400) || !int(q.maxTests, 1, 10) || !minute(q.checkedThrough)
    || !(q.lastBar === -1 || minute(q.lastBar) && q.lastBar + 60 <= s.time)
    || !int(q.serial, 0, Number.MAX_SAFE_INTEGER) || !num(q.atr, 0, 1e15)
    || !list(q.bars, STRUCTURE_BAR_LIMIT) || !list(q.zones, ZONE_LIMIT) || !list(q.levels, LEVEL_LIMIT)
    || !list(q.used, SIGNAL_LIMIT) || !q.used.every(v => typeof v === 'string') || new Set(q.used).size !== q.used.length
    || typeof q.watched !== 'string' || typeof q.reason !== 'string') return false;
  let prior = -1;
  for (const bar of q.bars) {
    if (!minute(bar.time) || bar.time <= prior || bar.time + 60 > s.time || !int(bar.volume, 1, 1e15)
      || ![bar.open, bar.high, bar.low, bar.close].every(price) || bar.high < Math.max(bar.open, bar.close) || bar.low > Math.min(bar.open, bar.close)) return false;
    prior = bar.time;
  }
  if (prior !== q.lastBar || q.lastBar >= 0 && q.checkedThrough < q.lastBar + 60) return false;
  const ids = new Set<number>();
  for (const z of q.zones) {
    if (!int(z.id, 1, q.serial) || ids.has(z.id) || !side(z.side) || !price(z.low) || !price(z.high) || z.high < z.low
      || !minute(z.createdAt) || !int(z.tests, 0, q.maxTests) || !(z.lastTest === -1 || minute(z.lastTest) && z.lastTest >= z.createdAt)
      || typeof z.touching !== 'boolean') return false;
    ids.add(z.id);
  }
  for (const l of q.levels) {
    if (!int(l.id, 1, q.serial) || ids.has(l.id) || !side(l.side) || !price(l.price) || !minute(l.createdAt)
      || !minute(l.lastTest) || !minute(l.lastPivot) || !int(l.tests, 1, 1e15) || typeof l.touching !== 'boolean') return false;
    ids.add(l.id);
  }
  if (q.breakout) {
    const b = q.breakout;
    if (q.mode !== 'breakout-retest' || !int(b.levelId, 1, q.serial) || !side(b.side) || !price(b.price)
      || !num(b.tolerance, tick, 1e15) || !minute(b.brokenAt) || !num(b.expires, b.brokenAt, 1e15)) return false;
  }
  if (q.setup) {
    const t = q.setup;
    if (!['zone', 'level', 'breakout'].includes(t.source) || !int(t.structureId, 1, q.serial) || !side(t.side)
      || typeof t.signal !== 'string' || !price(t.entry) || !price(t.stop) || (t.entry - t.stop) * t.side <= 0
      || !num(t.createdAt, 0, s.time) || !num(t.expires, t.createdAt, 1e15)) return false;
  }
  if (q.entryOrder !== undefined && (!int(q.entryOrder, 1, s.nextOrder) || !q.setup
    || (s.orders[q.entryOrder] && s.orders[q.entryOrder].owner !== a.id))) return false;
  return true;
}
