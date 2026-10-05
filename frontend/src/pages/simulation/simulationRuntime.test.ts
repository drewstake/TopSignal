// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { performance as timing } from 'node:perf_hooks';
import { createSimulationRuntime, SIMULATION_WRITER_KEY, type SimulationRuntime } from './simulationRuntime';
import { encodeSimulationCheckpoint, readSimulationCheckpoint, PREVIOUS_SIMULATION_STORAGE_KEY, SIMULATION_STORAGE_KEY } from './simulationCheckpoint';
import { advanceSimulation, createSimulation, DEFAULT_CONFIG, MAX_CATCHUP_STEPS } from './simulationPreview';
import { simulationReducer, type SimulationSession } from './simulationControls';
const active: SimulationRuntime[] = [];
const fresh = (): SimulationSession => ({ market: createSimulation(73521, undefined, 0), running: true, speed: 1, generation: 0, instrument: 'BX', timeframe: '1m', fractionalSteps: 0 });
const runtime = (options: Parameters<typeof createSimulationRuntime>[0] = { storage: localStorage, session: fresh() }) => {
  const result = createSimulationRuntime(options); active.push(result.api); return result;
};
beforeEach(() => { vi.useFakeTimers(); localStorage.clear(); vi.spyOn(document, 'hidden', 'get').mockReturnValue(false); });
afterEach(() => { active.splice(0).forEach(api => api.dispose()); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

function webLocks() {
  let held: { reject: (reason: Error) => void } | undefined;
  return { request: vi.fn((_name: string, options: LockOptions, callback: (lock: Lock | null) => Promise<void>) => {
    if (held && options.ifAvailable) return Promise.resolve(callback(null));
    if (held && options.steal) { held.reject(new DOMException('Lock stolen', 'AbortError')); held = undefined; }
    return new Promise<void>((resolve, reject) => {
      const owner = { reject }; held = owner;
      void callback({ name: 'topsignal.simulation.writer.v2.owned', mode: 'exclusive' }).then(() => {
        if (held === owner) held = undefined;
        resolve();
      }, reject);
    });
  }) };
}

describe('Persistent reference simulation runtime', () => {
  it('uses one clock across subscribers, advances at 18 seconds/second and persists across navigation', () => {
    const clock = vi.spyOn(window, 'setInterval'), { api } = runtime();
    const first = api.subscribe(vi.fn()), second = api.subscribe(vi.fn()); vi.advanceTimersByTime(1000);
    expect(api.getSnapshot().session.market.step).toBe(72); first(); second(); vi.advanceTimersByTime(1000);
    expect(api.getSnapshot().session.market.step).toBe(144); expect(clock).toHaveBeenCalledTimes(1);
    expect(readSimulationCheckpoint(localStorage)?.session).toEqual(api.getSnapshot().session);
  });
  it('preserves fractional steps, candles, positions and RNG across refresh without offline catch-up', () => {
    const { api } = runtime(); api.start(); vi.advanceTimersByTime(150);
    const before = api.getSnapshot().session; expect(before.fractionalSteps).toBeCloseTo(.8);
    api.dispose(); vi.advanceTimersByTime(3600000);
    const restored = runtime({ storage: localStorage }).api; expect(restored.getSnapshot().session).toEqual(before);
    restored.start(); vi.advanceTimersByTime(50);
    expect(restored.getSnapshot().session.market).toEqual(advanceSimulation(before.market, 4));
    expect(restored.getSnapshot().session.fractionalSteps).toBeCloseTo(.4);
  });
  it('expires the outgoing page lease so refresh can immediately continue its saved state', () => {
    const first = runtime(); first.api.start(); vi.advanceTimersByTime(150);
    const before = first.api.getSnapshot().session;
    window.dispatchEvent(new Event('pagehide'));
    const restored = runtime({ storage: localStorage }); restored.api.start();
    expect(restored.api.getSnapshot().conflict).toBe(false);
    expect(restored.api.getSnapshot().session).toEqual(before);
    vi.advanceTimersByTime(50);
    expect(restored.api.getSnapshot().session.market).toEqual(advanceSimulation(before.market, 4));
    expect(first.api.getSnapshot().session.market).toBe(before.market);
  });
  it('treats an available native lock as authoritative when a dead document left an unexpired lease', async () => {
    vi.stubGlobal('navigator', { locks: webLocks() });
    localStorage.setItem(SIMULATION_WRITER_KEY, JSON.stringify({ id: 'closed-document', expires: Date.now() + 15000 }));
    const { api } = runtime(); api.start(); await vi.advanceTimersByTimeAsync(100);
    expect(api.getSnapshot().conflict).toBe(false);
    expect(api.getSnapshot().session.market.step).toBe(7);
  });
  it('hands hot updates the live memory and subscribers while old callbacks reach the new clock', () => {
    const initial = runtime(), listener = vi.fn(); initial.api.subscribe(listener); vi.advanceTimersByTime(150);
    const before = initial.api.getSnapshot().session; initial.api.dispose();
    const replacement = runtime({ storage: localStorage, memory: initial.memory }); replacement.api.start(); listener.mockClear(); vi.advanceTimersByTime(50);
    expect(initial.api.getSnapshot().session.market).toEqual(advanceSimulation(before.market, 4)); expect(listener).toHaveBeenCalledTimes(1); expect(vi.getTimerCount()).toBe(1);
    initial.api.dispatch({ type: 'toggle' }); const paused = replacement.api.getSnapshot(); vi.advanceTimersByTime(3000);
    expect(replacement.api.getSnapshot()).toBe(paused);
  });
  it('waits for asynchronous Web Lock release during HMR and keeps mounted controls connected', async () => {
    const locks = webLocks(); vi.stubGlobal('navigator', { locks });
    const initial = runtime(); initial.api.start(); await vi.advanceTimersByTimeAsync(150);
    const before = initial.api.getSnapshot().session;
    initial.api.dispose(); const replacement = runtime({ storage: localStorage, memory: initial.memory }); replacement.api.start();
    await vi.advanceTimersByTimeAsync(50);
    expect(replacement.api.getSnapshot().conflict).toBe(false);
    expect(initial.api.getSnapshot().session.market).toEqual(advanceSimulation(before.market, 4));
    expect(locks.request).toHaveBeenCalledTimes(2);
    initial.api.dispatch({ type: 'toggle' });
    const exported = initial.api.exportCheckpoint();
    initial.api.advanceBoundary(); await vi.advanceTimersByTimeAsync(0);
    expect(replacement.api.getSnapshot().session.market.step).toBeGreaterThan(before.market.step + 4);
    initial.api.cancelAdvance();
    expect(replacement.api.getSnapshot().advancing).toBeNull();
    expect(initial.api.importCheckpoint(exported).ok).toBe(true);
    expect(initial.api.checkpoint()).toBe(true);
  });
  it('lets an explicit takeover resume while a revoked Web Lock owner cannot advance or save', async () => {
    vi.stubGlobal('navigator', { locks: webLocks() });
    const first = runtime(); first.api.start(); await vi.advanceTimersByTimeAsync(150);
    first.api.checkpoint();
    const second = runtime(); second.api.start(); await vi.advanceTimersByTimeAsync(0);
    expect(second.api.getSnapshot().conflict).toBe(true);
    second.api.takeOwnership(); await vi.advanceTimersByTimeAsync(0);
    expect(second.api.getSnapshot().conflict).toBe(false);
    expect(first.api.getSnapshot().conflict).toBe(true);
    const prior = first.api.getSnapshot().session.market;
    second.api.dispatch({ type: 'toggle' }); await vi.advanceTimersByTimeAsync(2000);
    expect(second.api.getSnapshot().session.market.step).toBeGreaterThan(prior.step);
    expect(first.api.getSnapshot().session.market).toBe(prior);
    expect(first.api.checkpoint()).toBe(false);
    expect(readSimulationCheckpoint(localStorage)?.session.market).toEqual(second.api.getSnapshot().session.market);
  });
  it('fences the former writer and can retry after a takeover without Web Locks', () => {
    const first = runtime(); first.api.start(); vi.advanceTimersByTime(150); first.api.checkpoint();
    const second = runtime(); second.api.start(); second.api.takeOwnership();
    const before = first.api.getSnapshot().session.market;
    second.api.dispatch({ type: 'toggle' }); vi.advanceTimersByTime(2000);
    expect(first.api.getSnapshot().conflict).toBe(true);
    expect(first.api.getSnapshot().session.market).toBe(before);
    expect(second.api.getSnapshot().session.market.step).toBeGreaterThan(before.step);
    expect(first.api.checkpoint()).toBe(false);
    second.api.dispose(); first.api.retryOwnership();
    expect(first.api.getSnapshot().conflict).toBe(false);
    first.api.dispatch({ type: 'toggle' }); vi.advanceTimersByTime(100);
    expect(first.api.getSnapshot().session.market.step).toBeGreaterThan(second.api.getSnapshot().session.market.step);
  });
  it('continues and reloads the protected save when an obsolete client keeps writing its old checkpoint', () => {
    const original = { ...fresh(), running: false }, oldRaw = encodeSimulationCheckpoint(original, Date.now());
    localStorage.setItem(PREVIOUS_SIMULATION_STORAGE_KEY, oldRaw);
    const { api } = runtime({ storage: localStorage }); api.start(); api.dispatch({ type: 'toggle' });
    for (let i = 0; i < 3; i++) {
      vi.advanceTimersByTime(2000);
      localStorage.setItem(PREVIOUS_SIMULATION_STORAGE_KEY, encodeSimulationCheckpoint(original, Date.now()));
      window.dispatchEvent(new StorageEvent('storage', { key: PREVIOUS_SIMULATION_STORAGE_KEY }));
    }
    expect(api.getSnapshot().conflict).toBe(false); expect(api.getSnapshot().session.market.step).toBe(432);
    api.dispose();
    const restored = runtime({ storage: localStorage });
    expect(restored.api.getSnapshot().session.market).toEqual(api.getSnapshot().session.market);
    expect(localStorage.getItem(PREVIOUS_SIMULATION_STORAGE_KEY)).not.toBeNull();
  });
  it('gives identical step results at every playback speed', () => {
    const initial = fresh();
    for (const speed of [.5, 1, 2, 4]) {
      const { api } = runtime({ storage: null, session: { ...initial, speed } }); api.start(); vi.advanceTimersByTime(2000 / speed);
      expect(api.getSnapshot().session.market).toEqual(advanceSimulation(initial.market, 144)); api.dispose();
    }
  });
  it('bounds delayed delivery and discards hidden-tab elapsed time', () => {
    let now = 0; const { api } = runtime({ storage: null, session: { ...fresh(), speed: 4 }, now: () => now }); api.start();
    now = 3600000; vi.advanceTimersByTime(50); expect(api.getSnapshot().session.market.step).toBe(MAX_CATCHUP_STEPS);
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true); now += 3600000; vi.advanceTimersByTime(50);
    expect(api.getSnapshot().session.market.step).toBe(MAX_CATCHUP_STEPS);
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false); document.dispatchEvent(new Event('visibilitychange')); now += 50; vi.advanceTimersByTime(50);
    expect(api.getSnapshot().session.market.step).toBe(MAX_CATCHUP_STEPS + 14);
  });
  it('keeps controls usable under quota errors and refuses to overwrite an unreadable save', () => {
    const failed = runtime({ storage: { getItem: () => null, setItem: () => { throw new Error('quota'); } }, session: fresh() }).api;
    failed.start(); vi.advanceTimersByTime(100); expect(failed.getSnapshot().savedLocally).toBe(false); failed.dispatch({ type: 'toggle' });
    const paused = failed.getSnapshot().session; vi.advanceTimersByTime(100); expect(failed.getSnapshot().session).toBe(paused);
    localStorage.setItem(SIMULATION_STORAGE_KEY, 'unreadable original'); const protectedSave = runtime({ storage: localStorage }).api;
    protectedSave.start(); protectedSave.dispatch({ type: 'toggle' }); expect(localStorage.getItem(SIMULATION_STORAGE_KEY)).toBe('unreadable original');
  });
  it('does not advance or consume RNG on view switches, pause or speed changes', () => {
    const s = fresh(); let changed = simulationReducer(s, { type: 'instrument', instrument: 'BG' });
    changed = simulationReducer(changed, { type: 'timeframe', timeframe: '1d' }); changed = simulationReducer(changed, { type: 'speed', speed: 4 });
    expect(changed.market).toBe(s.market); changed = simulationReducer(changed, { type: 'toggle' });
    expect(simulationReducer(changed, { type: 'tick', steps: 32 })).toBe(changed);
    expect(simulationReducer(s, { type: 'speed', speed: 99 })).toBe(s);
  });
  it('does not discard elapsed playback time when switching views between timer deliveries', () => {
    const { api } = runtime(); api.start(); vi.advanceTimersByTime(25);
    api.dispatch({ type: 'instrument', instrument: 'BT' }); api.dispatch({ type: 'timeframe', timeframe: '5m' });
    vi.advanceTimersByTime(25);
    expect(api.getSnapshot().session.market.step).toBe(3);
    expect(api.getSnapshot().session.fractionalSteps).toBeCloseTo(.6);
  });
});

it('steps exactly once while paused and advances boundaries through bounded engine batches', () => {
  const s = { ...fresh(), running: false }, { api } = runtime({ storage: null, session: s }); api.start();
  api.dispatch({ type: 'step', steps: 1 }); expect(api.getSnapshot().session.market).toEqual(advanceSimulation(s.market, 1));
  expect(api.getSnapshot().session.market.time).toBe(.25);
  api.advanceBoundary(); expect(api.getSnapshot().advancing).toBe(240);
  vi.advanceTimersByTime(200); expect(api.getSnapshot().session.market).toEqual(advanceSimulation(s.market, 240)); expect(api.getSnapshot().advancing).toBeNull();
  api.dispatch({ type: 'timeframe', timeframe: '1d' }); api.advanceBoundary(); vi.advanceTimersByTime(16);
  const partial = api.getSnapshot().session.market; expect(partial.step).toBeLessThanOrEqual(304);
  api.cancelAdvance(); vi.advanceTimersByTime(1000); expect(api.getSnapshot().session.market).toBe(partial);
});
it('imports transactionally, pauses, exports exact continuation and preserves failed replacements', () => {
  const { api } = runtime(); api.start(); api.dispatch({ type: 'toggle' });
  const raw = api.exportCheckpoint(), original = api.getSnapshot().session;
  api.dispatch({ type: 'step', steps: 10 }); expect(api.importCheckpoint(raw).ok).toBe(true);
  expect(api.getSnapshot().session.market).toEqual(original.market);
  api.dispatch({ type: 'step', steps: 1 }); expect(api.getSnapshot().session.market).toEqual(advanceSimulation(original.market, 1));
  const before = api.getSnapshot().session, stored = localStorage.getItem(SIMULATION_STORAGE_KEY);
  expect(api.importCheckpoint('{}').ok).toBe(false); expect(api.getSnapshot().session).toBe(before); expect(localStorage.getItem(SIMULATION_STORAGE_KEY)).toBe(stored);
  const failed = runtime({ storage: { getItem: () => null, setItem: () => { throw new Error('quota'); } }, session: fresh() }).api;
  const preserved = failed.getSnapshot().session; expect(failed.importCheckpoint(raw).ok).toBe(false); expect(failed.getSnapshot().session).toBe(preserved);
});
it('detects another tab before overwriting and lets the user load the saved continuation', () => {
  const first = runtime(), second = runtime(); first.api.start();
  const saved = localStorage.getItem(SIMULATION_STORAGE_KEY); second.api.start();
  expect(second.api.getSnapshot().conflict).toBe(true); expect(second.api.getSnapshot().session.running).toBe(false);
  expect(localStorage.getItem(SIMULATION_STORAGE_KEY)).toBe(saved);
  second.api.dispatch({ type: 'step', steps: 1 }); expect(localStorage.getItem(SIMULATION_STORAGE_KEY)).toBe(saved);
  first.api.dispose(); second.api.retryOwnership(); expect(second.api.getSnapshot().conflict).toBe(false);
  expect(second.api.getSnapshot().session.market).toEqual(first.api.getSnapshot().session.market);
});
it('records bounded override metadata without consuming RNG or altering market decisions', () => {
  const initial = fresh(); let s = initial;
  for (let i = 0; i < 60; i++) s = simulationReducer(s, { type: 'configure', overrides: { liquidity: 1, volatility: 1, whaleFrequency: 1, newsFrequency: 1 } });
  expect(s.market.events).toHaveLength(40); expect(s.market.randomState).toBe(initial.market.randomState);
  const next = advanceSimulation(s.market, 50), baseline = advanceSimulation(initial.market, 50);
  expect({ ...next, events: [] }).toEqual({ ...baseline, events: [] });
});

it('shares immutable history objects while isolating every mutable continuation field', () => {
  const before = createSimulation(), snapshot = structuredClone(before);
  const after = advanceSimulation(before, 32);
  expect(before).toEqual(snapshot);
  expect(after.books.BX.candles['1m']).not.toBe(before.books.BX.candles['1m']);
  expect(after.books.BX.candles['1m'][0]).toBe(before.books.BX.candles['1m'][0]);
  expect(after.accounts).not.toBe(before.accounts); expect(after.orders).not.toBe(before.orders);
  expect(after.trades).not.toBe(before.trades);
});

it('publishes every 20 Hz live batch while preserving every delivered engine step', () => {
  const { api } = runtime({ storage: null, session: { ...fresh(), speed: 4 } }), listener = vi.fn();
  api.subscribe(listener); listener.mockClear(); vi.advanceTimersByTime(1000);
  expect(api.getSnapshot().session.market.step).toBe(288);
  expect(listener.mock.calls.length).toBe(20);
  api.dispatch({ type: 'toggle' }); expect(api.getSnapshot().session.running).toBe(false);
});

it('migrates a live pre-structure hot-reload snapshot once without restarting playback', () => {
  const market = createSimulation(73521, { ...DEFAULT_CONFIG, supplyDemandTraders: 0, supportResistanceTraders: 0 }, 800);
  const initial = runtime({ storage: null, session: { ...fresh(), market } });
  const old = JSON.parse(JSON.stringify(initial.memory.snapshot.session.market));
  for (const key of ['supplyDemandTraders', 'supportResistanceTraders', 'structureRewardRisk', 'structureMinRewardRisk']) delete old.config[key];
  initial.memory.snapshot.session.market = old;
  const replacement = runtime({ storage: null, memory: initial.memory });
  const migrated = replacement.api.getSnapshot().session.market;
  expect(migrated.step).toBe(800); expect(migrated.books).toEqual(market.books); expect(migrated.randomState).toBe(market.randomState);
  expect(migrated.participants).toHaveLength(144); expect(replacement.api.getSnapshot().session.running).toBe(true);
  const again = runtime({ storage: null, memory: replacement.memory }); expect(again.api.getSnapshot().session.market).toBe(migrated);
});

it('releases prior development render diagnostics without clearing unrelated timing entries', () => {
  vi.stubGlobal('performance', timing);
  const names = ['\u200bSimulationChart', '\u200bSimulationActivity', '\u200bDenseGlyphs'];
  try {
    timing.measure('simulation-test-unrelated');
    const { api } = runtime({ storage: null, session: fresh() }); api.start();
    for (let i = 0; i < 10; i++) {
      for (const name of names) timing.measure(name, { detail: { tape: new Array(4000).fill(i) } });
      vi.advanceTimersByTime(100);
      for (const name of names) expect(timing.getEntriesByName(name)).toHaveLength(0);
    }
    expect(timing.getEntriesByName('simulation-test-unrelated')).toHaveLength(1);
    expect(api.getSnapshot().session.market.step).toBe(72);
    api.dispose();
  } finally {
    timing.clearMeasures('simulation-test-unrelated');
    for (const name of names) timing.clearMeasures(name);
    vi.unstubAllGlobals();
  }
});
