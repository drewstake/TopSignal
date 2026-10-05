// Public engine / read-model boundary. No broker, network or account APIs.
import { createExchange, accountSummary } from './simulationExchange';
import { initializeParticipants, stepParticipants } from './simulationParticipants';
import { DEFAULT_CONFIG, DEFAULT_SEED, INSTRUMENTS, PARTICIPANT_GROUPS, TIMEFRAMES, type Instrument, type ParticipantOrder, type SimulationState } from './simulationModel';
export * from './simulationModel';

export function createSimulation(seed = DEFAULT_SEED, config = DEFAULT_CONFIG, warmupSteps = 7200): SimulationState {
  const state = createExchange(Number.isFinite(seed) ? seed : DEFAULT_SEED, config);
  initializeParticipants(state);
  for (let i = 0; i < warmupSteps; i++) stepParticipants(state);
  return state;
}
// Clone once per delivered batch. Published snapshots remain immutable even
// when React renders on a different schedule.
export function advanceSimulation(state: SimulationState, steps = 1): SimulationState {
  // Existing executions and candle objects are immutable: aggregation replaces the
  // active candle. Clone their bounded array containers, not 18,000 historical
  // objects on every frame. All mutable decision/account/book state is still cloned.
  const withoutHistory = (key: Instrument): SimulationState['books'][Instrument] => ({ ...state.books[key], candles: { '1m': [], '5m': [], '15m': [], '1h': [], '1d': [] } });
  const books = { BX: withoutHistory('BX'), BT: withoutHistory('BT'), BG: withoutHistory('BG') };
  const next: SimulationState = structuredClone({ ...state, trades: [], books });
  next.trades = state.trades.slice();
  for (const key of INSTRUMENTS) next.books[key].candles = Object.fromEntries(TIMEFRAMES.map(([tf]) => [tf, state.books[key].candles[tf].slice()])) as SimulationState['books'][Instrument]['candles'];
  for (let i = 0; i < steps; i++) stepParticipants(next);
  return next;
}
export function visibleOrders(s: SimulationState, key: Instrument): ParticipantOrder[] {
  const kinds = new Map(s.participants.map(p => [p.id, p.kind]));
  return [...s.books[key].bids, ...s.books[key].asks].map(id => {
    const o = s.orders[id];
    return { id: String(id), participantId: o.owner, kind: kinds.get(o.owner)!, side: o.direction === 1 ? 'buy' : 'sell',
      price: o.price! * s.config.contracts[key].tick, quantity: o.remaining };
  });
}
export function summarizeBook(book: readonly ParticipantOrder[], price: number) {
  const buys = book.filter(o => o.side === 'buy' && o.quantity > 0), sells = book.filter(o => o.side === 'sell' && o.quantity > 0);
  return { price, bid: buys.length ? Math.max(...buys.map(o => o.price)) : null, ask: sells.length ? Math.min(...sells.map(o => o.price)) : null,
    bidContracts: buys.reduce((sum, o) => sum + o.quantity, 0), askContracts: sells.reduce((sum, o) => sum + o.quantity, 0) };
}
export function summarizeParticipants(s: SimulationState, key: Instrument) {
  return PARTICIPANT_GROUPS.map(group => {
    const agents = s.participants.filter(p => p.key === key && p.kind === group.kind);
    const accounts = agents.map(p => s.accounts[p.id]);
    const total = (value: (a: typeof accounts[number]) => number) => accounts.reduce((sum, a) => sum + value(a), 0);
    return { ...group, count: agents.length, halted: accounts.filter(a => a.locked).length,
      netInventory: total(a => (a.positions[key]?.quantity ?? 0) * (a.positions[key]?.direction ?? 1)),
      inventory: total(a => a.positions[key]?.quantity ?? 0), balance: total(a => a.balance),
      equity: total(a => accountSummary(s, a).equity), realized: total(a => a.realized),
      bought: total(a => a.bought), sold: total(a => a.sold), cancelled: total(a => a.cancelled),
      resting: total(a => a.openOrders.reduce((n, id) => n + s.orders[id].remaining, 0)),
      parentRemaining: agents.reduce((n, a) => n + a.parent, 0) };
  });
}
export function simulationTime(seconds: number) {
  const days = Math.floor(seconds / 86400);
  return `${days ? `D${days + 1} ` : ''}${String(Math.floor(seconds / 3600) % 24).padStart(2, '0')}:${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(Math.floor(seconds) % 60).padStart(2, '0')}`;
}
