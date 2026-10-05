import { describe, expect, it } from 'vitest';
import { addAccount, advanceClock, cancelAll, processStops, submitOrder } from './simulationExchange';
import { advanceSimulation, createSimulation, DEFAULT_CONFIG, INSTRUMENTS, type Participant, type SimulationCandle, type SimulationState } from './simulationPreview';
import { initializeStructureParticipants, scheduleStructure, tradeStructure, updateStructure } from './simulationStructure';
import { decodeSimulationCheckpoint, encodeSimulationCheckpoint } from './simulationCheckpoint';
import { STRUCTURE_BAR_LIMIT, type StructureMode } from './simulationStructureModel';
import type { SimulationSession } from './simulationControls';

const candle = (index: number, open = 100, high = 102, low = 98, close = 100): SimulationCandle => ({ time: index * 60, open, high, low, close, volume: 1 });
function fixture(mode: StructureMode = 'zone-limit') {
  const s = createSimulation(73521, { ...DEFAULT_CONFIG, makers: 0, traders: 0, whales: 0 }, 0);
  const a = s.participants.find(a => a.key === 'BX' && a.structure?.mode === mode)!, q = a.structure!;
  q.atrPeriod = 10; q.baseLength = 2; q.compactness = 1; q.departure = 1.2; q.tolerance = .2;
  q.pivotLeft = 2; q.pivotRight = 2; q.separation = 180; q.maxTests = 2; a.size = 2;
  q.bars = Array.from({ length: 16 }, (_, i) => candle(i)); q.lastBar = 900; q.checkedThrough = 960; q.atr = 4;
  s.time = 960; s.step = s.time * 4; s.books.BX.reference = 400; s.books.BX.last = 400;
  s.books.BX.candles['1m'] = q.bars.slice();
  addAccount(s, 'liquidity', 1000000, 350, .8); addAccount(s, 'taker', 1000000, 350, .8);
  return { s, a, q };
}
function feed(s: SimulationState, a: Participant, bar: SimulationCandle, closed = true) {
  const series = s.books.BX.candles['1m'];
  if (series.at(-1)?.time === bar.time) series[series.length - 1] = bar; else series.push(bar);
  s.books.BX.last = Math.round(bar.close / .25);
  s.time = bar.time + (closed ? 60 : 59.75); s.step = s.time * 4; updateStructure(s, a);
}
function departure(mode: StructureMode = 'zone-limit') {
  const f = fixture(mode);
  feed(f.s, f.a, candle(16, 100, 100.5, 99.5, 100));
  feed(f.s, f.a, candle(17, 100, 100.5, 99.5, 100.25));
  feed(f.s, f.a, candle(18, 100.25, 108.5, 100, 108));
  return f;
}
function support(f: ReturnType<typeof fixture>, side: 1 | -1 = 1, tests = 2) {
  f.q.serial = 1;
  f.q.levels = [{ id: 1, side, price: 100, createdAt: 600, lastTest: 600, lastPivot: 600, tests, touching: false }];
}
const quote = (s: SimulationState, side: 1 | -1, price: number, quantity = 2) => submitOrder(s, 'liquidity', { contract: 'BX', direction: side, price, quantity, type: 'Limit' });
const asSession = (market: SimulationState): SimulationSession => ({ market, running: false, speed: 1, generation: 0, instrument: 'BX', timeframe: '1m', fractionalSteps: .375 });

describe('Closed-minute structure and signals', () => {
  it('creates demand only after the departure closes, with the base range and no backdated order', () => {
    const { s, a, q } = fixture();
    feed(s, a, candle(16, 100, 100.5, 99.5, 100)); feed(s, a, candle(17, 100, 100.5, 99.5, 100.25));
    feed(s, a, candle(18, 100.25, 108.5, 100, 108), false);
    tradeStructure(s, a); expect(q.zones).toHaveLength(0); expect(s.nextOrder).toBe(0);
    s.time += .25; s.step++; updateStructure(s, a);
    expect(q.zones).toEqual([{ id: 1, side: 1, low: 99.5, high: 100.5, createdAt: 1140, tests: 0, lastTest: -1, touching: false }]);
    expect(s.nextOrder).toBe(0);
    const before = structuredClone(q); updateStructure(s, a); expect(q).toEqual(before);
  });
  it('creates supply for a downward departure and rejects wide bases and weak departures', () => {
    const { s, a, q } = fixture();
    feed(s, a, candle(16, 100, 100.5, 99.5, 100)); feed(s, a, candle(17, 100, 100.5, 99.5, 100.25));
    feed(s, a, candle(18, 100, 100.5, 92, 92.5)); expect(q.zones[0]?.side).toBe(-1);
    const wide = fixture(); feed(wide.s, wide.a, candle(16, 100, 110, 90, 100)); feed(wide.s, wide.a, candle(17, 100, 110, 90, 100));
    feed(wide.s, wide.a, candle(18, 100, 120, 100, 119)); expect(wide.q.zones).toHaveLength(0);
    const weak = fixture(); feed(weak.s, weak.a, candle(16, 100, 100.5, 99.5, 100)); feed(weak.s, weak.a, candle(17, 100, 100.5, 99.5, 100));
    feed(weak.s, weak.a, candle(18, 100, 101, 100, 101)); expect(weak.q.zones).toHaveLength(0);
  });
  it('ignores future candles and hidden fair value/news and waits for sufficient history', () => {
    const { s, a, q } = fixture(); q.bars = []; q.lastBar = -1; q.atr = 0;
    s.books.BX.candles['1m'] = [candle(99, 100, 200, 99, 199)];
    Object.defineProperty(s, 'markets', { get: () => { throw new Error('Hidden value accessed'); } });
    Object.defineProperty(s, 'news', { get: () => { throw new Error('News accessed'); } });
    s.time += 60; updateStructure(s, a); tradeStructure(s, a);
    expect(q.lastBar).toBe(-1); expect(q.reason).toContain('Insufficient'); expect(s.nextOrder).toBe(0);
  });
  it('confirms a swing only after all later candles close, then merges separated pivots', () => {
    const { s, a, q } = fixture('level-rejection');
    for (const [i, h] of [103, 104, 110, 105].entries()) feed(s, a, candle(16 + i, 101, h, 99, 102));
    feed(s, a, candle(20, 101, 104, 99, 102), false);
    expect(q.levels.find(l => l.price === 110)).toBeUndefined();
    s.time += .25; s.step++; updateStructure(s, a);
    expect(q.levels.find(l => l.price === 110)).toMatchObject({ createdAt: 1260, lastPivot: 1080, tests: 1, side: -1 });
    for (const [i, h] of [103, 104, 110.25, 105, 104].entries()) feed(s, a, candle(21 + i, 101, h, 99, 102));
    expect(q.levels.filter(l => l.side === -1)).toHaveLength(1); expect(q.levels.find(l => l.side === -1)?.tests).toBe(2);
  });
  it('counts separated support bounces, not adjacent touches, and enters only after rejection closes', () => {
    const f = fixture('level-rejection'); support(f, 1, 1);
    feed(f.s, f.a, candle(16, 100, 103, 99.75, 102.5), false);
    expect(f.q.setup).toBeUndefined(); f.s.time += .25; f.s.step++; updateStructure(f.s, f.a);
    expect(f.q.levels[0].tests).toBe(2); expect(f.q.setup?.side).toBe(1);
    feed(f.s, f.a, candle(17, 100, 103, 99.75, 102.5)); expect(f.q.levels[0].tests).toBe(2);
  });
  it('requires a later successful breakout retest and cancels a failed breakout', () => {
    const f = fixture('breakout-retest'); support(f, -1); f.q.bars.at(-1)!.close = 98;
    feed(f.s, f.a, candle(16, 99, 104, 98, 103));
    expect(f.q.breakout?.brokenAt).toBe(1020); expect(f.q.setup).toBeUndefined();
    expect(f.q.levels.some(l => l.id === 1)).toBe(false);
    feed(f.s, f.a, candle(17, 100, 104, 99.75, 103), false); expect(f.q.setup).toBeUndefined();
    f.s.time += .25; f.s.step++; updateStructure(f.s, f.a); expect(f.q.setup?.source).toBe('breakout');
    quote(f.s, -1, 103); tradeStructure(f.s, f.a); expect(f.s.accounts[f.a.id].positions.BX?.direction).toBe(1);
    const failed = fixture('breakout-retest'); support(failed, -1); failed.q.bars.at(-1)!.close = 98;
    feed(failed.s, failed.a, candle(16, 99, 104, 98, 103)); feed(failed.s, failed.a, candle(17, 103, 104, 97, 98));
    expect(failed.q.breakout).toBeUndefined(); expect(failed.q.setup).toBeUndefined();
  });
  it('retires zones on a decisive far-edge close, excess age or separate repeat tests', () => {
    const invalid = departure(); invalid.s.books.BX.last = 404; tradeStructure(invalid.s, invalid.a);
    expect(invalid.s.accounts[invalid.a.id].openOrders).toHaveLength(1);
    feed(invalid.s, invalid.a, candle(19, 101, 101, 95, 96));
    expect(invalid.q.zones).toHaveLength(0); expect(invalid.s.accounts[invalid.a.id].openOrders).toHaveLength(0);
    const aged = departure(); aged.q.maxAge = 60; aged.s.time += 120; updateStructure(aged.s, aged.a); expect(aged.q.zones).toHaveLength(0);
    const tested = departure();
    for (let i = 19; i <= 27; i++) feed(tested.s, tested.a, i % 4 === 3 ? candle(i, 101, 102, 100, 101.5) : candle(i, 103, 104, 102, 103));
    expect(tested.q.zones.find(z => z.id === 1)).toBeUndefined();
  });
  it('invalidates support and resistance on decisive closes and expires stale levels', () => {
    for (const side of [1, -1] as const) {
      const f = fixture('level-rejection'); support(f, side);
      feed(f.s, f.a, side === 1 ? candle(16, 100, 101, 96, 97) : candle(16, 100, 104, 99, 103));
      expect(f.q.levels.some(l => l.id === 1)).toBe(false); expect(f.q.setup).toBeUndefined();
    }
    const stale = fixture('level-rejection'); support(stale); stale.q.maxAge = 60;
    stale.s.time += 60; updateStructure(stale.s, stale.a); expect(stale.q.levels).toHaveLength(0);
  });
});

describe('Exchange entries, protection and risk', () => {
  it('posts one small patient order, handles a partial fill, cancels the remainder and never adds to a loser', () => {
    const { s, a, q } = departure(); s.books.BX.last = 404;
    const priceBefore = s.books.BX.last; tradeStructure(s, a);
    const entry = s.orders[q.entryOrder!]!; expect(entry.remaining).toBe(2); expect(entry.stopPrice).toBeLessThan(99.5);
    expect(s.books.BX.last).toBe(priceBefore);
    for (let i = 0; i < 10; i++) tradeStructure(s, a);
    expect(s.accounts[a.id].openOrders).toEqual([entry.id]);
    submitOrder(s, 'taker', { contract: 'BX', direction: -1, quantity: 1, type: 'Market' });
    expect(s.accounts[a.id].positions.BX?.quantity).toBe(1); expect(entry.remaining).toBe(1);
    tradeStructure(s, a); expect(s.orders[entry.id]).toBeUndefined();
    s.books.BX.last = 396; const count = s.nextOrder; tradeStructure(s, a); expect(s.nextOrder).toBe(count);
    expect(s.accounts[a.id].positions.BX?.stop).toBe(entry.stopPrice);
  });
  it('trades candle-confirmed demand rejection and supply rejection in opposite directions', () => {
    for (const side of [1, -1] as const) {
      const f = fixture('zone-rejection'); f.q.serial = 1;
      f.q.zones = [{ id: 1, side, low: 99.5, high: 100.5, createdAt: 600, tests: 0, lastTest: -1, touching: false }];
      feed(f.s, f.a, side === 1 ? candle(16, 100, 103, 100, 102) : candle(16, 100, 100.25, 97, 98));
      expect(f.q.setup?.side).toBe(side); quote(f.s, -side as 1 | -1, side === 1 ? 102 : 98);
      tradeStructure(f.s, f.a); expect(f.s.accounts[f.a.id].positions.BX?.direction).toBe(side);
      expect(f.s.accounts[f.a.id].positions.BX?.quantity).toBe(2);
    }
  });
  it('skips a close opposing target and uses configured reward/risk only when there is no opposing structure', () => {
    const f = fixture('level-rejection'); support(f);
    f.q.levels.push({ id: ++f.q.serial, side: -1, price: 104, createdAt: 600, lastTest: 600, lastPivot: 600, tests: 2, touching: false });
    feed(f.s, f.a, candle(16, 100, 103, 99.75, 102.5)); quote(f.s, -1, 102.5);
    tradeStructure(f.s, f.a); expect(f.q.reason).toContain('Insufficient room'); expect(f.s.accounts[f.a.id].positions.BX).toBeUndefined();
    f.q.levels = f.q.levels.filter(l => l.side === 1); f.s.config.structureRewardRisk = 3;
    tradeStructure(f.s, f.a); const p = f.s.accounts[f.a.id].positions.BX!;
    expect(p.target! - p.entry).toBeCloseTo((p.entry - p.stop!) * 3);
  });
  it('targets the nearest opposing structure with room, with a tick before its near edge', () => {
    const f = departure(); f.s.books.BX.last = 404;
    f.q.zones.push({ id: ++f.q.serial, side: -1, low: 106, high: 107, createdAt: 1140, tests: 0, lastTest: -1, touching: false });
    tradeStructure(f.s, f.a); expect(f.s.orders[f.q.entryOrder!]?.targetPrice).toBe(105.75);
  });
  it('does not repeat an expired or completed signal and respects the exchange capital/margin limit', () => {
    const f = departure(); f.s.books.BX.last = 404; tradeStructure(f.s, f.a);
    const order = f.s.orders[f.q.entryOrder!]!, before = f.s.nextOrder;
    f.s.time = order.expires!; advanceClock(f.s); tradeStructure(f.s, f.a);
    expect(f.s.nextOrder).toBe(before); expect(f.s.accounts[f.a.id].openOrders).toHaveLength(0);
    const poor = departure(); poor.s.books.BX.last = 404; poor.s.accounts[poor.a.id].balance = 100;
    tradeStructure(poor.s, poor.a); expect(poor.s.accounts[poor.a.id].openOrders).toHaveLength(0); expect(poor.q.reason).toContain('rejected');
    const halted = departure(); halted.s.accounts[halted.a.id].locked = true; tradeStructure(halted.s, halted.a); expect(halted.s.nextOrder).toBe(0);
  });
  it('keeps absolute brackets across multiple fill prices and refuses fills through the stop or target', () => {
    const { s, a } = fixture(); quote(s, -1, 100, 1); quote(s, -1, 100.5, 1);
    submitOrder(s, a.id, { contract: 'BX', direction: 1, quantity: 2, type: 'Limit', price: 101, stopPrice: 98, targetPrice: 105, minimumRewardRisk: 1.25 });
    expect(s.accounts[a.id].positions.BX).toMatchObject({ entry: 100.25, quantity: 2, stop: 98, target: 105 });
    for (const price of [97, 105]) {
      const f = fixture(); quote(f.s, -1, price, 2);
      const o = submitOrder(f.s, f.a.id, { contract: 'BX', direction: 1, quantity: 2, type: 'Limit', price: 106, stopPrice: 98, targetPrice: 105, minimumRewardRisk: 1.25 });
      expect(o?.filled).toBe(0); expect(f.s.accounts[f.a.id].positions.BX).toBeUndefined(); expect(f.s.accounts[f.a.id].openOrders).toHaveLength(0);
    }
  });
  it('cancels a newly invalidated entry before queued exits match at the candle boundary', () => {
    const f = departure(); f.s.books.BX.last = 404; tradeStructure(f.s, f.a);
    quote(f.s, -1, 101, 1); submitOrder(f.s, 'taker', { contract: 'BX', direction: 1, quantity: 1, type: 'Market' });
    f.s.accounts.taker.positions.BX!.triggered = 'Stop loss';
    feed(f.s, f.a, candle(19, 101, 101, 95, 96), false);
    const prints = f.s.nextTrade;
    advanceClock(f.s, s => updateStructure(s, f.a));
    expect(f.s.nextTrade).toBe(prints); expect(f.s.accounts[f.a.id].positions.BX).toBeUndefined();
    expect(f.s.accounts[f.a.id].openOrders).toHaveLength(0);
  });
  it('executes stops and targets through liquidity, retains an unfilled exit and accepts exit slippage', () => {
    for (const target of [false, true]) {
      const f = fixture(); quote(f.s, -1, 100, 1);
      submitOrder(f.s, f.a.id, { contract: 'BX', direction: 1, quantity: 1, type: 'Market', stopPrice: 98, targetPrice: 104, minimumRewardRisk: 1.25 });
      const trigger = target ? 104 : 97;
      quote(f.s, 1, trigger, 1); submitOrder(f.s, 'taker', { contract: 'BX', direction: -1, quantity: 1, type: 'Market' });
      processStops(f.s); expect(f.s.accounts[f.a.id].positions.BX?.triggered).toBe(target ? 'Take profit' : 'Stop loss');
      quote(f.s, 1, trigger - 1, 1); advanceClock(f.s);
      expect(f.s.accounts[f.a.id].positions.BX).toBeUndefined(); expect(f.s.accounts[f.a.id].realized).toBe((trigger - 1 - 100) * 50);
    }
  });
  it('liquidates new accounts under the same equity/margin rules', () => {
    const f = fixture(); quote(f.s, -1, 100, 1);
    submitOrder(f.s, f.a.id, { contract: 'BX', direction: 1, quantity: 1, type: 'Market', stopPrice: 98, targetPrice: 104, minimumRewardRisk: 1.25 });
    f.s.accounts[f.a.id].balance = 0; advanceClock(f.s);
    expect(f.s.accounts[f.a.id].locked).toBe(true); expect(f.s.accounts[f.a.id].positions.BX?.triggered).toBe('Account depleted');
    quote(f.s, 1, 99, 1); advanceClock(f.s); expect(f.s.accounts[f.a.id].positions.BX).toBeUndefined();
  });
});

describe('Determinism, bounded work and checkpoint migration', () => {
  it('seeds differences without drawing from the legacy stream and bounds all histories', () => {
    const s = createSimulation(), agents = s.participants.filter(a => a.structure);
    expect(agents).toHaveLength(12); expect(new Set(agents.map(a => a.structure!.lookback)).size).toBeGreaterThan(1);
    expect(new Set(agents.map(a => a.patience)).size).toBe(12);
    const rng = s.randomState; for (const a of agents) scheduleStructure(s, a); expect(s.randomState).toBe(rng);
    const f = fixture();
    for (let i = 16; i < 180; i++) feed(f.s, f.a, candle(i));
    expect(f.q.bars).toHaveLength(STRUCTURE_BAR_LIMIT); expect(f.q.zones.length).toBeLessThanOrEqual(12); expect(f.q.levels.length).toBeLessThanOrEqual(20);
    const before = structuredClone(f.q); for (let i = 0; i < 100; i++) updateStructure(f.s, f.a); expect(f.q).toEqual(before);
  });
  it('resumes an active structural run exactly and rejects missing or future decision state', () => {
    const s = createSimulation(), raw = encodeSimulationCheckpoint(asSession(s), 10);
    const restored = decodeSimulationCheckpoint(raw)!.session.market;
    expect(restored).toEqual(s); expect(advanceSimulation(restored, 1200)).toEqual(advanceSimulation(s, 1200));
    const corrupt = structuredClone(s); delete corrupt.participants.find(a => a.structure)!.structure;
    expect(decodeSimulationCheckpoint(encodeSimulationCheckpoint(asSession(corrupt), 10))).toBeNull();
    const future = structuredClone(s); future.participants.find(a => a.structure)!.structure!.bars.at(-1)!.time = s.time + 60;
    expect(decodeSimulationCheckpoint(encodeSimulationCheckpoint(asSession(future), 10))).toBeNull();
  });
  it('round-trips a partially filled structural entry with absolute protection and a pending breakout retest', () => {
    const s = createSimulation(73521, undefined, 0);
    for (const id of s.accountOrder) cancelAll(s, id);
    const a = s.participants.find(a => a.key === 'BX' && a.structure?.mode === 'zone-limit')!, q = a.structure!;
    const price = s.books.BX.last! * .25;
    a.size = 2; q.atr = 4; q.serial = 1;
    q.zones = [{ id: 1, side: 1, low: price - 1.25, high: price - .25, createdAt: 0, tests: 0, lastTest: -1, touching: false }];
    tradeStructure(s, a); const orderId = q.entryOrder!;
    submitOrder(s, s.participants[0].id, { contract: 'BX', direction: -1, quantity: 1, type: 'Market' });
    expect(s.orders[orderId].remaining).toBe(1); expect(s.accounts[a.id].positions.BX?.quantity).toBe(1);
    const retest = s.participants.find(a => a.key === 'BX' && a.structure?.mode === 'breakout-retest')!.structure!;
    retest.serial = 1; retest.breakout = { levelId: 1, side: 1, price: price - 2, tolerance: .5, brokenAt: 0, expires: 600 };
    const decoded = decodeSimulationCheckpoint(encodeSimulationCheckpoint(asSession(s), 10)); expect(decoded).not.toBeNull();
    expect(decoded!.session.market.orders[orderId]).toEqual(s.orders[orderId]);
    expect(advanceSimulation(decoded!.session.market, 500)).toEqual(advanceSimulation(s, 500));
  });
  it('migrates old v2 runs once, preserving clock, prices, history, positions, schedules and RNG', () => {
    const s = createSimulation(73521, { ...DEFAULT_CONFIG, supplyDemandTraders: 0, supportResistanceTraders: 0 }, 4000);
    const old = JSON.parse(encodeSimulationCheckpoint(asSession(s), 10));
    for (const key of ['supplyDemandTraders', 'supportResistanceTraders', 'structureRewardRisk', 'structureMinRewardRisk']) delete old.session.market.config[key];
    const migrated = decodeSimulationCheckpoint(JSON.stringify(old))!.session.market;
    for (const key of ['step', 'time', 'books', 'orders', 'pendingStops', 'randomState', 'trades', 'markets'] as const) expect(migrated[key]).toEqual(s[key]);
    for (const id of s.accountOrder) expect(migrated.accounts[id]).toEqual(s.accounts[id]);
    expect(migrated.participants.slice(0, 132)).toEqual(s.participants);
    for (const key of INSTRUMENTS) expect(migrated.participants.filter(a => a.key === key)).toHaveLength(48);
    const before = structuredClone(migrated); initializeStructureParticipants(migrated); expect(migrated).toEqual(before);
    const saved = decodeSimulationCheckpoint(encodeSimulationCheckpoint(asSession(migrated), 11))!.session.market;
    expect(saved).toEqual(migrated); expect(advanceSimulation(saved, 400)).toEqual(advanceSimulation(migrated, 400));
  });
  it('preserves explicit zero counts on reload and rejects partial migrations', () => {
    const s = createSimulation(123, { ...DEFAULT_CONFIG, supplyDemandTraders: 0, supportResistanceTraders: 0 }, 0);
    const raw = encodeSimulationCheckpoint(asSession(s), 10); expect(decodeSimulationCheckpoint(raw)!.session.market.participants).toHaveLength(132);
    const corrupt = JSON.parse(raw); delete corrupt.session.market.config.supplyDemandTraders;
    expect(decodeSimulationCheckpoint(JSON.stringify(corrupt))).toBeNull();
    const normal = createSimulation(123, undefined, 0); const removed = normal.participants.pop()!;
    cancelAll(normal, removed.id); delete normal.accounts[removed.id]; normal.accountOrder.pop();
    expect(decodeSimulationCheckpoint(encodeSimulationCheckpoint(asSession(normal), 10))).toBeNull();
  });
});
