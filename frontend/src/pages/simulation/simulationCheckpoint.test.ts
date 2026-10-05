import { describe, expect, it } from 'vitest';
import { decodeSimulationCheckpoint, encodeSimulationCheckpoint, inspectSimulationStorage, LEGACY_SIMULATION_STORAGE_KEY, MAX_CHECKPOINT_BYTES, PREVIOUS_SIMULATION_STORAGE_KEY, readSimulationCheckpoint, saveSimulationCheckpoint, SIMULATION_STORAGE_KEY } from './simulationCheckpoint';
import { advanceSimulation, createSimulation, MAX_CANDLES, INSTRUMENTS, TIMEFRAMES } from './simulationPreview';
import type { SimulationSession } from './simulationControls';
const session = (): SimulationSession => ({ market: createSimulation(42, undefined, 500), running: false, speed: 4, generation: 3, instrument: 'BG', timeframe: '15m', fractionalSteps: .375 });
const encode = (s: SimulationSession) => encodeSimulationCheckpoint(s, 1234);

describe('Version 2 local checkpoints', () => {
  it('round-trips every field and continues identically to an uninterrupted run', () => {
    const current = session(), restored = decodeSimulationCheckpoint(encode(current));
    expect(restored?.session).toEqual(current);
    expect(advanceSimulation(restored!.session.market, 400)).toEqual(advanceSimulation(current.market, 400));
  });
  it('retains pending protective exits and their exact continuation', () => {
    const current = session(), s = current.market;
    const a = s.participants.find(agent => s.accounts[agent.id].positions[agent.key])!;
    const position = s.accounts[a.id].positions[a.key]!;
    position.triggered = 'Stop loss'; delete position.target;
    s.pendingStops.push({ owner: a.id, contract: a.key, position: position.id });
    const restored = decodeSimulationCheckpoint(encode(current));
    expect(restored?.session.market.pendingStops).toEqual(s.pendingStops);
    expect(advanceSimulation(restored!.session.market, 80)).toEqual(advanceSimulation(s, 80));
  });
  it('fits full bounded candle histories and the full retained tape in its storage budget', () => {
    const current = { ...session(), market: createSimulation() };
    for (const key of INSTRUMENTS) for (const [tf, seconds] of TIMEFRAMES) {
      const price = current.market.books[key].last! * current.market.config.contracts[key].tick;
      current.market.books[key].candles[tf] = Array.from({ length: MAX_CANDLES }, (_, i) => ({ open: price, high: price, low: price, close: price, volume: 1, time: i * seconds }));
    }
    current.market.time = MAX_CANDLES * 86400; current.market.step = current.market.time * 4;
    const raw = encode(current); expect(raw.length * 2).toBeLessThan(MAX_CHECKPOINT_BYTES);
    // Orders and flow are moved forward too, as this is a synthetic capacity fixture.
    for (const o of Object.values(current.market.orders)) if (o.expires) o.expires += current.market.time;
    expect(decodeSimulationCheckpoint(encode(current))).not.toBeNull();
  });
  it('rejects corrupt, unsupported, oversized, invalid RNG, broken FIFO and missing schedules', () => {
    for (const raw of [null, '', '{', '{}', 'x'.repeat(MAX_CHECKPOINT_BYTES / 2 + 1), '{"version":1}']) expect(decodeSimulationCheckpoint(raw)).toBeNull();
    const initial = session();
    const mutations: ((s: SimulationSession) => void)[] = [
      s => { s.market.randomState = 0; }, s => { s.fractionalSteps = 1; },
      s => { s.market.participants[0].next = NaN; }, s => { s.market.books.BX.bids.reverse(); },
      s => { s.market.orders[s.market.books.BX.bids[0]].owner = 'missing'; },
      s => { s.market.books.BX.candles['1m'][0].high = 1; },
    ];
    for (const mutate of mutations) { const s = structuredClone(initial); mutate(s); expect(decodeSimulationCheckpoint(encode(s))).toBeNull(); }
  });
  it('keeps legacy raw data untouched under its original key', () => {
    const data = new Map([[LEGACY_SIMULATION_STORAGE_KEY, '{"version":1,"original":"legacy data"}']]);
    const storage = { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => { data.set(k, v); } };
    expect(inspectSimulationStorage(storage).legacy).toBe(true); expect(readSimulationCheckpoint(storage)).toBeNull();
    expect(saveSimulationCheckpoint(session(), 1234, storage)).toBe(true);
    expect(data.get(LEGACY_SIMULATION_STORAGE_KEY)).toBe('{"version":1,"original":"legacy data"}');
    expect(data.has(SIMULATION_STORAGE_KEY)).toBe(true);
  });
  it('adopts the prior v2 continuation once, preserves its source and never falls back over an existing owned save', () => {
    const original = session(), raw = encode(original), data = new Map([[PREVIOUS_SIMULATION_STORAGE_KEY, raw]]);
    const storage = { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => { data.set(k, v); } };
    expect(readSimulationCheckpoint(storage)?.session).toEqual(original);
    const continued = { ...original, market: advanceSimulation(original.market, 20) };
    expect(saveSimulationCheckpoint(continued, 2000, storage)).toBe(true);
    expect(data.get(PREVIOUS_SIMULATION_STORAGE_KEY)).toBe(raw);
    expect(readSimulationCheckpoint(storage)?.session).toEqual(continued);
    data.set(SIMULATION_STORAGE_KEY, 'unreadable owned save');
    expect(inspectSimulationStorage(storage).unreadable).toBe(true);
    expect(readSimulationCheckpoint(storage)).toBeNull();
  });
  it('handles denied reads and quota errors without replacing the last successful save', () => {
    const blocked = { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('quota'); } };
    expect(readSimulationCheckpoint(blocked)).toBeNull(); expect(saveSimulationCheckpoint(session(), 1234, blocked)).toBe(false);
    expect(saveSimulationCheckpoint(session(), 1234, null)).toBe(false);
  });
});

it('rejects inconsistent volumes, ledgers, execution order, tuple shapes and non-finite data', () => {
  const current = session();
  const mutations: ((s: SimulationSession) => void)[] = [
    s => { s.market.books.BX.volume++; }, s => { s.market.totalVolume++; },
    s => { s.market.accounts[s.market.accountOrder[0]].bought++; },
    s => { s.market.accounts[s.market.accountOrder[0]].balance += 100; },
    s => { s.market.trades.reverse(); }, s => { s.market.initialSeed = .5; },
    s => { s.market.books.BX.candles['1m'].at(-1)!.close += .25; },
  ];
  for (const mutate of mutations) { const s = structuredClone(current); mutate(s); expect(decodeSimulationCheckpoint(encode(s))).toBeNull(); }
  const parsed = JSON.parse(encode(current)); parsed.session.market.trades[0].push('unexpected');
  expect(decodeSimulationCheckpoint(JSON.stringify(parsed))).toBeNull();
});
