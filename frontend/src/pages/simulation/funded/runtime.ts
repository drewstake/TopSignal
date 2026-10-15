import type { Account, Book, Direction, Execution, Instrument, Order, OrderRequest, State } from './model';
import { SETTINGS, SPECS, INSTRUMENTS, PLAYER, contractLimit } from './model';
import { depth, exposures, playerCapacity, summary } from './exchange';
import type { Submission } from './exchange';

export type Command = { type: 'play'; playing: boolean } | { type: 'step' } | { type: 'restart'; seed: number } | { type: 'order'; request: OrderRequest } | { type: 'cancel'; id: number } | { type: 'close'; key: Instrument } | { type: 'flatten' } | { type: 'protection'; key: Instrument; position: number; field: 'stop' | 'target'; dollars: number | null };
export interface MarketView { books: Record<Instrument, Book>; trades: Execution[]; time: number }
type DirectionCapacity = Record<Instrument, Record<Direction, number>>;
export interface View { session: { market: MarketView | null }; seed: number; step: number; playing: boolean; warming: boolean; warmupProgress: number; restarting: boolean; backlog: number; regime: string; news: string; newsUntil: number; account: Account | null; totals: ReturnType<typeof summary> | null; capacity: { limit: number; reserved: number; available: number; directions: DirectionCapacity } | null; books: Record<Instrument, { bids: ReturnType<typeof depth>; asks: ReturnType<typeof depth> }>; orders: Order[]; history: Order[]; receipt: Order | null; notice: string; error: string; generation: number }
export function initialView(seed: number): View {
  return { session: { market: null }, seed, step: 0, playing: true, warming: true, warmupProgress: 0, restarting: false, backlog: 0, regime: '', news: '', newsUntil: 0, account: null, totals: null, capacity: null, books: { BX: { bids: [], asks: [] }, BT: { bids: [], asks: [] }, BG: { bids: [], asks: [] } }, orders: [], history: [], receipt: null, notice: '', error: '', generation: 0 };
}
export function publishView(s: State, flags: Pick<View, 'playing' | 'warming' | 'warmupProgress' | 'restarting' | 'backlog' | 'receipt' | 'notice' | 'error' | 'generation'>): View {
  const a = s.accounts[PLAYER], reserved = a ? exposures(s, a).reduce((n, e) => n + e.reserved, 0) : 0;
  return { ...flags, session: { market: { books: s.books, trades: s.trades, time: s.time } }, seed: s.seed, step: s.step, regime: s.regime.name, news: s.news, newsUntil: s.newsUntil,
    account: a ?? null, totals: a ? summary(s, a) : null,
    capacity: a ? { limit: contractLimit(a.balance), reserved, available: Math.max(0, contractLimit(a.balance) - reserved), directions: Object.fromEntries(INSTRUMENTS.map(key => [key, { 1: playerCapacity(s, a, key, 1), '-1': playerCapacity(s, a, key, -1) }])) as DirectionCapacity } : null,
    books: Object.fromEntries(INSTRUMENTS.map(key => [key, { bids: depth(s, key, 1), asks: depth(s, key, -1) }])) as View['books'],
    orders: a ? a.openOrders.map(id => s.orders[id]).filter(Boolean) : [], history: s.archive.filter(o => o.owner === PLAYER).slice(-100).reverse() };
}
export function orderMessage(result: Submission) {
  if (!result.ok) return result.error;
  const o = result.order, avg = o.filled ? (o.notional / o.filled).toFixed(2) : '—';
  return `#${o.id} ${o.direction === 1 ? 'Buy' : 'Sell'} ${o.contract}: ${o.filled} filled, ${o.remaining} working, ${o.cancelled} cancelled. Average ${avg}. ${o.reason ?? o.status}`;
}
// Wall time creates debt; a frame budget can postpone work but never erase debt.
export class SimulationClock {
  backlog = 0;
  accrue(wallMilliseconds: number) { this.backlog += Math.max(0, wallMilliseconds) / 1000 * SETTINGS.speed; }
  run(step: () => void, now: () => number) {
    const start = now(); let count = 0;
    while (this.backlog + 1e-10 >= SETTINGS.step && count < SETTINGS.catchup && now() - start < SETTINGS.budgetMs) { step(); this.backlog -= SETTINGS.step; count++; }
    this.backlog = Math.max(0, this.backlog); return count;
  }
}
const SEED_KEY = 'topsignal.get-funded.v3.seed';
export function freshSeed() { const bytes = new Uint32Array(1); crypto.getRandomValues(bytes); return bytes[0] % 2147483646; }
function readSeed() { try { const raw = localStorage.getItem(SEED_KEY); if (raw !== null && /^\d+$/.test(raw) && Number.isSafeInteger(Number(raw))) return Number(raw); } catch { /* In-memory sessions work when storage is unavailable. */ } return freshSeed(); }
export class FundedRuntime {
  private snapshot: View;
  private listeners = new Set<() => void>();
  private worker: Worker | null = null;
  private savedSeed: number | null = null;
  constructor() { this.snapshot = initialView(readSeed()); }
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => { this.listeners.add(listener); this.start(); return () => { this.listeners.delete(listener); }; };
  private start() {
    if (this.worker) return;
    this.worker = new Worker(new URL('./worker.ts', import.meta.url), { type: 'module' });
    this.worker.onmessage = (event: MessageEvent<View>) => {
      this.snapshot = event.data;
      if (this.savedSeed !== this.snapshot.seed) {
        try { localStorage.setItem(SEED_KEY, String(this.snapshot.seed)); this.savedSeed = this.snapshot.seed; } catch { /* Retry optional persistence on the next publication. */ }
      }
      for (const listener of this.listeners) listener();
    };
    this.worker.onerror = () => { this.snapshot = { ...this.snapshot, playing: false, error: 'Simulation worker stopped. Reload to start a fresh session with the saved seed.' }; for (const listener of this.listeners) listener(); };
    this.worker.postMessage({ type: 'initialize', seed: this.snapshot.seed });
  }
  dispatch = (command: Command) => { this.start(); this.worker!.postMessage(command); };
  dispose() { this.worker?.terminate(); this.worker = null; this.listeners.clear(); }
}
let runtime: FundedRuntime | undefined;
export function getFundedRuntime() { return runtime ??= new FundedRuntime(); }
if (import.meta.hot) import.meta.hot.dispose(() => runtime?.dispose());
export const formatPrice = (key: Instrument, ticks: number) => (ticks * SPECS[key].tick).toFixed(2);
