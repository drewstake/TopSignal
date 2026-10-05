import { BASE_SPEED, STEP_SECONDS, TIMEFRAMES, UPDATE_INTERVAL_MS, MAX_CATCHUP_STEPS } from './simulationPreview';
import { newSession, simulationReducer, type SimulationAction, type SimulationSession } from './simulationControls';
import { initializeStructureParticipants, needsStructureMigration } from './simulationStructure';
import { decodeSimulationCheckpoint, encodeSimulationCheckpoint, inspectSimulationStorage, MAX_CHECKPOINT_BYTES, saveSimulationCheckpoint, SIMULATION_STORAGE_KEY, type CheckpointStorage } from './simulationCheckpoint';

export const CHECKPOINT_INTERVAL_MS = 2000;
export const PUBLISH_INTERVAL_MS = UPDATE_INTERVAL_MS;
export const SIMULATION_WRITER_KEY = 'topsignal.simulation.writer.v1';
const WRITER_TTL_MS = 15000;
type Snapshot = { session: SimulationSession; savedLocally: boolean; storageNotice: string; savedStep: number | null; conflict: boolean; advancing: number | null };
export interface SimulationRuntime {
  getSnapshot: () => Snapshot; subscribe: (listener: () => void) => () => void;
  dispatch: (action: SimulationAction) => void; start: () => void; checkpoint: () => boolean; dispose: () => void;
  advanceBoundary: () => void; cancelAdvance: () => void; exportCheckpoint: () => string;
  importCheckpoint: (raw: string) => { ok: boolean; message: string }; retryOwnership: () => void; takeOwnership: () => void;
}
interface RuntimeMemory {
  version: 2; snapshot: Snapshot; listeners: Set<() => void>; lastSaved: number; started: boolean;
  preserveUnreadable: boolean; start: () => void; dispatch: (action: SimulationAction) => void; expectedRaw?: string | null;
  controller?: SimulationRuntime; lockLifetime?: Promise<void>;
  writerId?: string;
}
interface RuntimeOptions { storage: CheckpointStorage | null; now?: () => number; session?: SimulationSession; memory?: RuntimeMemory }
export function nextBoundaryStep(session: SimulationSession) {
  const seconds = TIMEFRAMES.find(([tf]) => tf === session.timeframe)![1];
  return (Math.floor(session.market.time / seconds) + 1) * seconds / STEP_SECONDS;
}
export function createSimulationRuntime({ storage, now = Date.now, session, memory: existing }: RuntimeOptions) {
  const restored = existing?.version === 2 ? null : inspectSimulationStorage(storage);
  const readRaw = () => { try { return storage?.getItem(SIMULATION_STORAGE_KEY) ?? null; } catch { return null; } };
  const memory: RuntimeMemory = existing?.version === 2 ? existing : {
    version: 2, snapshot: { session: session ?? restored?.checkpoint?.session ?? newSession(), savedLocally: !!restored?.checkpoint,
      storageNotice: restored?.notice ?? '', savedStep: restored?.checkpoint?.session.market.step ?? null, conflict: false, advancing: null },
    listeners: new Set(), lastSaved: now(), started: false, preserveUnreadable: !session && !!restored?.unreadable,
    start: () => {}, dispatch: () => {}, expectedRaw: readRaw(),
  };
  // Compatible hot replacement adopts the live session and optional new UI metadata.
  memory.snapshot = { ...memory.snapshot, savedStep: memory.snapshot.savedStep ?? null, conflict: memory.snapshot.conflict ?? false, advancing: memory.snapshot.advancing ?? null };
  if (needsStructureMigration(memory.snapshot.session.market)) {
    const market = structuredClone(memory.snapshot.session.market);
    initializeStructureParticipants(market);
    memory.snapshot = { ...memory.snapshot, session: { ...memory.snapshot.session, market }, savedLocally: false };
  }
  if (memory.expectedRaw === undefined) memory.expectedRaw = readRaw();
  memory.writerId ??= crypto.randomUUID();
  const notify = () => {
    // React 19 development timing entries serialize changed props, including the
    // entire rolling tape. At 4x this can retain megabytes per render indefinitely.
    // Keep only the current frame's simulation diagnostics in the User Timing
    // buffer; leave other features' measurements and the market state untouched.
    if (import.meta.env.DEV && typeof performance.clearMeasures === 'function') {
      for (const name of ['SimulationChart', 'SimulationChartReadout', 'SimulationCrosshair', 'SimulationOrderBook', 'SimulationActivity', 'SimulationIndividuals', 'DenseGlyphs', 'CandleGlyph', 'VolumeGlyph', 'LiquidityCell']) {
        performance.clearMeasures('\u200b' + name);
      }
    }
    memory.listeners.forEach(listener => listener());
  };
  let lastPublished = now() - PUBLISH_INTERVAL_MS;
  const publish = (liveTick = false) => {
    // The live clock already delivers at 20 Hz. Publish every delivered batch
    // rather than dropping frames when timer jitter falls just under a cutoff.
    if (liveTick || now() - lastPublished >= PUBLISH_INTERVAL_MS) { lastPublished = now(); notify(); }
  };
  let timer: number | undefined, advanceTimer: number | undefined, lastTick = now(), disposed = false;
  const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined;
  let previousLock = existing?.lockLifetime;
  let ownsLock = !locks || !storage, releaseLock: (() => void) | undefined, lockPending = false, lockRequest = 0;
  let leaseEnabled = false;
  const readWriter = (): { id: string; expires: number } | null => {
    try {
      const value = JSON.parse(storage?.getItem(SIMULATION_WRITER_KEY) ?? 'null');
      return value && typeof value.id === 'string' && Number.isFinite(value.expires) ? value : null;
    } catch { return null; }
  };
  const writeWriter = (expires = now() + WRITER_TTL_MS) => {
    try { storage?.setItem(SIMULATION_WRITER_KEY, JSON.stringify({ id: memory.writerId, expires })); leaseEnabled = !!storage; }
    catch { /* Web Locks still protect playback when storage is unavailable. */ }
  };
  const cancelAdvance = () => {
    if (advanceTimer !== undefined) window.clearTimeout(advanceTimer); advanceTimer = undefined;
    if (memory.snapshot.advancing !== null) { memory.snapshot = { ...memory.snapshot, advancing: null }; notify(); }
  };
  const conflict = () => {
    cancelAdvance(); memory.snapshot = { ...memory.snapshot, conflict: true, savedLocally: false,
      session: { ...memory.snapshot.session, running: false }, storageNotice: 'Another tab owns or changed this run. Playback and saving are paused. Export your in-memory run before loading the saved run.' }; notify();
  };
  const writerMatches = () => {
    if (!leaseEnabled || readWriter()?.id === memory.writerId) return true;
    conflict(); ownsLock = false; releaseLock?.(); releaseLock = undefined; return false;
  };
  const unchanged = () => {
    if (!storage || memory.snapshot.conflict) return !memory.snapshot.conflict;
    if (!writerMatches()) return false;
    if (readRaw() !== memory.expectedRaw) { conflict(); return false; } return true;
  };
  const checkpoint = () => {
    if (disposed || !ownsLock || !unchanged()) return false;
    writeWriter();
    const success = !memory.preserveUnreadable && saveSimulationCheckpoint(memory.snapshot.session, now(), storage);
    memory.lastSaved = now(); if (success) memory.expectedRaw = readRaw();
    const savedStep = success ? memory.snapshot.session.market.step : memory.snapshot.savedStep;
    if (success !== memory.snapshot.savedLocally || savedStep !== memory.snapshot.savedStep) {
      memory.snapshot = { ...memory.snapshot, savedLocally: success, savedStep }; notify();
    }
    return success;
  };
  const acquire = (reload = false, takeover = false) => {
    if (disposed || lockPending) return;
    const ready = () => {
      const writer = readWriter();
      // A granted native lock proves the previous document is gone, even when
      // its unload event was skipped. Only the fallback must wait on a lease.
      if (!locks && !takeover && writer && writer.id !== memory.writerId && writer.expires > now()) { conflict(); ownsLock = false; releaseLock?.(); return; }
      writeWriter();
      if (reload) {
        const saved = inspectSimulationStorage(storage);
        if (!saved.checkpoint) { memory.snapshot = { ...memory.snapshot, storageNotice: 'No valid saved run is available. Export this run before resolving storage.' }; notify(); return; }
        memory.expectedRaw = readRaw(); memory.preserveUnreadable = false;
        memory.snapshot = { ...memory.snapshot, session: { ...saved.checkpoint.session, running: false, generation: memory.snapshot.session.generation + 1 }, conflict: false, storageNotice: saved.notice, advancing: null };
      }
      lastTick = now(); checkpoint(); notify();
    };
    // A released Web Lock is asynchronous. Wait for the old controller to finish
    // releasing it before a hot replacement tries ifAvailable on the same lock.
    if (previousLock) {
      const released = previousLock; previousLock = undefined; lockPending = true;
      void released.finally(() => { lockPending = false; acquire(reload, takeover); }); return;
    }
    if (ownsLock) { ready(); return; }
    if (!locks) { ownsLock = true; ready(); return; }
    lockPending = true;
    const request = ++lockRequest;
    memory.lockLifetime = locks.request('topsignal.simulation.writer.v2.owned', takeover ? { steal: true } : { ifAvailable: true }, async lock => {
      lockPending = false; if (disposed) return;
      if (!lock) { conflict(); return; }
      ownsLock = true;
      // Install release before notifying subscribers or starting any work.
      await new Promise<void>(resolve => { releaseLock = resolve; ready(); });
      if (request === lockRequest) { ownsLock = false; releaseLock = undefined; }
    }).then(() => undefined).catch(() => {
      // An explicit takeover revokes the old request. Stop that client before it
      // can publish or save again, even if its callback is still awaiting release.
      if (request !== lockRequest) return;
      lockPending = false; ownsLock = false; releaseLock?.(); releaseLock = undefined;
      if (!disposed) conflict();
    });
  };
  const storageChanged = (event: StorageEvent) => {
    if (event.key === SIMULATION_WRITER_KEY || event.key === null) writerMatches();
    if ((event.key === SIMULATION_STORAGE_KEY || event.key === null) && readRaw() !== memory.expectedRaw) conflict();
  };
  const exitPage = () => {
    checkpoint();
    // A reload gets a new writer identity. Do not leave the outgoing document's
    // lease live after its native Web Lock is released by navigation.
    if (leaseEnabled && readWriter()?.id === memory.writerId) writeWriter(0);
  };
  const visibilityChange = () => { lastTick = now(); checkpoint(); };
  const start = () => {
    if (timer !== undefined || disposed) return;
    memory.started = true; lastTick = now(); acquire();
    timer = window.setInterval(() => {
      const time = now(), elapsed = Math.max(0, Math.min(250, time - lastTick)); lastTick = time;
      const current = memory.snapshot.session;
      if (ownsLock && !memory.snapshot.conflict && writerMatches() && current.running && !document.hidden) {
        const progress = current.fractionalSteps + elapsed / 1000 * BASE_SPEED * current.speed / STEP_SECONDS;
        const whole = Math.floor(progress + 1e-10);
        memory.snapshot = { ...memory.snapshot, session: simulationReducer(current,
          { type: 'tick', steps: Math.min(MAX_CATCHUP_STEPS, whole), fractionalSteps: Math.max(0, progress - whole) }) }; publish(true);
      }
      if (time - memory.lastSaved >= CHECKPOINT_INTERVAL_MS) checkpoint();
    }, UPDATE_INTERVAL_MS);
    window.addEventListener('pagehide', exitPage); window.addEventListener('beforeunload', exitPage);
    window.addEventListener('storage', storageChanged); document.addEventListener('visibilitychange', visibilityChange);
  };
  memory.start = start;
  memory.dispatch = action => {
    const viewAction = action.type === 'instrument' || action.type === 'timeframe';
    if (!viewAction && (!ownsLock || !unchanged())) return;
    if (!viewAction) cancelAdvance();
    memory.snapshot = { ...memory.snapshot, session: simulationReducer(memory.snapshot.session, action) };
    if (action.type === 'reset') {
      memory.preserveUnreadable = false;
      memory.snapshot = { ...memory.snapshot, storageNotice: inspectSimulationStorage(storage).legacy ? 'Legacy simulation saved separately; original data preserved.' : '' };
    }
    if (action.type === 'toggle' || action.type === 'speed' || action.type === 'reset' || action.type === 'step') lastTick = now();
    checkpoint(); notify();
  };
  const controller: SimulationRuntime = {
    getSnapshot: () => memory.snapshot,
    subscribe: listener => { memory.listeners.add(listener); memory.start(); return () => { memory.listeners.delete(listener); }; },
    dispatch: action => memory.dispatch(action), start: () => memory.start(), checkpoint, cancelAdvance,
    advanceBoundary: () => {
      if (memory.snapshot.session.running || memory.snapshot.advancing !== null || !ownsLock || !unchanged()) return;
      memory.snapshot = { ...memory.snapshot, advancing: nextBoundaryStep(memory.snapshot.session) }; notify();
      const batch = () => {
        const target = memory.snapshot.advancing;
        if (disposed || target === null || memory.snapshot.conflict || !writerMatches()) return;
        if (!document.hidden) {
          const remaining = target - memory.snapshot.session.market.step;
          memory.snapshot = { ...memory.snapshot, session: simulationReducer(memory.snapshot.session, { type: 'step', steps: Math.min(MAX_CATCHUP_STEPS, remaining) }) }; publish();
          if (memory.snapshot.session.market.step >= target) { cancelAdvance(); checkpoint(); return; }
        }
        advanceTimer = window.setTimeout(batch, document.hidden ? 100 : 16);
      };
      advanceTimer = window.setTimeout(batch, 0);
    },
    exportCheckpoint: () => encodeSimulationCheckpoint(memory.snapshot.session, now()),
    importCheckpoint: raw => {
      if (raw.length * 2 > MAX_CHECKPOINT_BYTES) return { ok: false, message: 'Checkpoint is too large (4.5 MB maximum in browser storage).' };
      const imported = decodeSimulationCheckpoint(raw);
      if (!imported) return { ok: false, message: 'Invalid or incompatible checkpoint. The current run is unchanged.' };
      if (!ownsLock || !unchanged()) return { ok: false, message: 'Another tab owns or changed this run. Import was not applied.' };
      const replacement = { ...imported.session, running: false, generation: memory.snapshot.session.generation + 1 };
      // Commit the replacement to storage before exposing it or cancelling current work.
      if (!saveSimulationCheckpoint(replacement, now(), storage)) return { ok: false, message: 'Storage failed. The current run and prior save are unchanged.' };
      cancelAdvance(); memory.expectedRaw = readRaw(); memory.preserveUnreadable = false; lastTick = now();
      memory.snapshot = { session: replacement, savedLocally: true, savedStep: replacement.market.step, advancing: null, conflict: false,
        storageNotice: inspectSimulationStorage(storage).legacy ? 'Legacy simulation saved separately; original data preserved.' : '' }; notify();
      return { ok: true, message: 'Checkpoint imported and paused. Resume for exact continuation.' };
    },
    retryOwnership: () => acquire(true),
    takeOwnership: () => acquire(true, true),
    dispose: () => {
      if (disposed) return;
      checkpoint(); cancelAdvance(); disposed = true;
      if (leaseEnabled && readWriter()?.id === memory.writerId) writeWriter(0);
      if (timer !== undefined) window.clearInterval(timer); timer = undefined; releaseLock?.();
      window.removeEventListener('pagehide', exitPage); window.removeEventListener('beforeunload', exitPage); window.removeEventListener('storage', storageChanged);
      document.removeEventListener('visibilitychange', visibilityChange);
    },
  };
  memory.controller = controller;
  // Mounted controls can retain their old API after self-accepting HMR. Forward
  // every user operation to the live controller, not a disposed lock owner.
  const api: SimulationRuntime = {
    ...controller,
    checkpoint: () => memory.controller!.checkpoint(),
    cancelAdvance: () => memory.controller!.cancelAdvance(),
    advanceBoundary: () => memory.controller!.advanceBoundary(),
    exportCheckpoint: () => memory.controller!.exportCheckpoint(),
    importCheckpoint: raw => memory.controller!.importCheckpoint(raw),
    retryOwnership: () => memory.controller!.retryOwnership(),
    takeOwnership: () => memory.controller!.takeOwnership(),
  };
  return { api, memory };
}
function browserStorage(): CheckpointStorage | null { try { return window.localStorage; } catch { return null; } }
let current: ReturnType<typeof createSimulationRuntime> | undefined;
export function getSimulationRuntime(): SimulationRuntime {
  if (!current) {
    current = createSimulationRuntime({ storage: browserStorage(), memory: import.meta.hot?.data?.simulationMemory });
    if (current.memory.started) current.api.start();
  }
  return current.api;
}
if (import.meta.hot) {
  import.meta.hot.accept();
  import.meta.hot.dispose(data => { if (current) { current.api.dispose(); data.simulationMemory = current.memory; } });
  if (import.meta.hot.data?.simulationMemory) getSimulationRuntime();
}
