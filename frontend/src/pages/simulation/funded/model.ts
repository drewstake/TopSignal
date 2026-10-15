// Get-Funded working-copy port, October 15, 2026. All prices in the book are ticks.
export const VERSION = 3;
export const PLAYER = 'player';
export const INSTRUMENTS = ['BX', 'BT', 'BG'] as const;
export type Instrument = typeof INSTRUMENTS[number];
export type Direction = 1 | -1;
export const TIMEFRAMES = [['1m', 60], ['5m', 300], ['15m', 900], ['1h', 3600], ['1D', 86400]] as const;
export type Timeframe = typeof TIMEFRAMES[number][0];
export const SETTINGS = Object.freeze({ step: .25, speed: 18, broadcastMs: 100, warmup: 1800, catchup: 32, budgetMs: 8,
  candles: 1200, trades: 4000, orders: 4000, accounts: 60, botExitRetry: 4, maxPlayerOrders: 16, priceBand: .1,
  makerPolicy: 4, makerRest: 18, makerReprice: 3, makerSize: 1.5, makerReplenish: 1.5, makerDecision: .5,
  traderActivity: 76 / 36, makerActivity: 2, whaleActivity: 4 / 3,
  liquidity: 1, volatility: 1, whaleFrequency: 1, newsFrequency: 1 });
export const EXECUTION = Object.freeze({ version: 1, window: 18, slice: 5000, owner: 20000, market: 100000, bookFraction: .1, guard: 20, ownersPerStep: 32 });
export const RESERVE = Object.freeze({ version: 2, capital: 5000000, limit: 2000, size: 24, sizes: { BX: 24, BT: 4, BG: 3 }, variation: [.7, 1.3], growth: .15, spacing: 1, offsets: [1, 2, 3, 4, 5], decision: 1, rest: 2, reprice: 2 });
export const SCALPER = Object.freeze({ version: 1, size: [1, 2], decision: [2.67, 6.67], cooldown: [12, 32], hold: [48, 160], lifetime: [3, 7], target: [4, 10], stop: [5, 12], spread: 3, slippage: 1, aggressive: .45 });
export const POPULATION = { Maker: [20, 750000], Liquidity: [24, 150000], Momentum: [12, 200000], Value: [20, 250000], Reversion: [12, 200000], Protective: [8, 150000], Scalper: [24, 100000], Whale: [4, 1500000], Reserve: [6, 5000000] } as const;
export type Kind = keyof typeof POPULATION;
// Initialization order differs from the population metadata's display order.
export const INITIALIZATION: Kind[] = ['Maker', 'Liquidity', 'Momentum', 'Value', 'Reversion', 'Protective', 'Whale', 'Scalper', 'Reserve'];
export interface Spec { name: string; last: number; tick: number; multiplier: number; scale: number; margin: number }
export const SPECS: Record<Instrument, Spec> = {
  BX: { name: 'Blox 500', last: 5824.75, tick: .25, multiplier: 50, scale: 1, margin: 750 },
  BT: { name: 'BloxTech 100', last: 20485.5, tick: .25, multiplier: 20, scale: 3, margin: 750 },
  BG: { name: 'Blox Gold', last: 2384.6, tick: .1, multiplier: 100, scale: 1, margin: 750 },
};
export const REGIMES = [
  { name: 'Quiet', weight: 24, volatility: .45, liquidity: 1.4, activity: .65, trend: 0 },
  { name: 'Trending', weight: 22, volatility: 1, liquidity: 1, activity: 1.1, trend: .1 },
  { name: 'Range-bound', weight: 24, volatility: .85, liquidity: 1.2, activity: 1, trend: 0 },
  { name: 'High volatility', weight: 16, volatility: 2.5, liquidity: .6, activity: 1.7, trend: 0 },
  { name: 'Thin liquidity', weight: 14, volatility: 1.5, liquidity: .22, activity: .85, trend: 0 },
];
export interface Candle { time: number; open: number; high: number; low: number; close: number; volume: number }
export interface Position { id: number; direction: Direction; quantity: number; entry: number; stop?: number; target?: number; manualStop?: boolean; manualTarget?: boolean; triggered?: string; exitOrder?: number; targetOrder?: number; retry?: { at: number; revision: number; eligibility: number; fair: number; reason?: string; step?: number } }
export interface ClosedTrade { position: number; contract: Instrument; direction: Direction; quantity: number; entry: number; exit: number; pnl: number; time: number; trade: number; reason: string }
export interface Fill { trade: number; order: number; contract: Instrument; direction: Direction; quantity: number; price: number; slippage: number; time: number }
export interface Account { id: string; base: number; balance: number; realized: number; cashFlow: number; limit: number; risk: number; player: boolean; locked: boolean; liquidating: boolean; resetting: boolean; positions: Partial<Record<Instrument, Position>>; openOrders: number[]; history: ClosedTrade[]; fills: Fill[]; completed: number; wins: number }
export interface OrderRequest { contract: Instrument; direction: Direction; quantity: number; type: 'Market' | 'Limit' | 'Stop'; price?: number; stopTicks?: number; targetTicks?: number; stopDollars?: number; targetDollars?: number; reduceOnly?: boolean; expires?: number }
export interface Order { id: number; owner: string; contract: Instrument; direction: Direction; original: number; remaining: number; filled: number; cancelled: number; notional: number; type: OrderRequest['type']; price?: number; time: number; reference: number; status: string; reason?: string; reduceOnly: boolean; reductionPosition?: number; protection?: string; protectivePosition?: number; stopTicks?: number; targetTicks?: number; expires?: number; queued?: boolean; triggered?: boolean; execution: 'Immediate' | 'Sliced' }
export interface Execution { id: number; time: number; step: number; contract: Instrument; ticks: number; quantity: number; buyer: string; seller: string; direction: Direction; incoming: number; resting: number; fair: number }
export interface Book { reference: number; fair: number; last: number | null; open: number | null; volume: number; bids: number[]; asks: number[]; recent: number[]; volatility: number; revision: number; candles: Record<Timeframe, Candle[]>; candleVolume: Record<Timeframe, number> }
export interface Participant { id: string; key: Instrument; kind: Kind; budget: number; risk: number; limit: number; speed: number; size: number; bias: number; belief: number; patience: number; aggression: number; next: number; stop: number; target: number; parent: number; parentSide: Direction; lookback: number; entryThreshold: number; signalWeight: number; valueOffset: number; halfLife: number; observationSeconds: number; observationNoise: number; observedAt: number; nextObservation: number; entryBuffer: number; minMove: number; quotes: Record<string, number>; replenish: Record<string, number>; maxHold: number; cooldown: number; style: 'Momentum' | 'Reversion'; readyAt: number; positionId?: number; openedAt?: number }
export interface State { version: typeof VERSION; seed: number; randomState: number; step: number; time: number; books: Record<Instrument, Book>; markets: Record<Instrument, { anchor: number; fair: number; impulse: number }>; participants: Participant[]; accounts: Record<string, Account>; accountOrder: string[]; orders: Record<number, Order>; archive: Order[]; trades: Execution[]; nextOrder: number; nextTrade: number; totalVolume: number; tradeHash: number; eligibility: number; regime: typeof REGIMES[number]; direction: Direction; nextRegime: number; nextNews: number; nextBeliefs: number; news: string; newsUntil: number; pendingStops: { owner: string; contract: Instrument; position: number }[]; dirty: Set<string>; syncing: boolean; processingQueue: boolean; queuePeers: Record<string, number>; execution: { window: number; owners: Record<string, number>; markets: Record<string, { used: number; limit: number }>; cursor: string } }
export const round = (n: number) => n < 0 ? -Math.floor(-n + .5) : Math.floor(n + .5);
export const clamp = (n: number, low: number, high: number) => Math.max(low, Math.min(high, n));
export function random(s: Pick<State, 'randomState'>) { s.randomState = s.randomState * 48271 % 2147483647; return s.randomState / 2147483647; }
export const range = (s: State, a: number, b: number) => a + (b - a) * random(s);
export const int = (s: State, a: number, b: number) => Math.floor(range(s, a, b + 1));
export const noise = (s: State) => (random(s) + random(s) + random(s) - 1.5) * 2;
export const contractLimit = (balance: number) => Math.max(5, Math.floor(round(balance * 100) / 100000));
export function protection(spec: Spec, quantity: number, dollars: number) {
  if (!Number.isSafeInteger(quantity) || quantity < 1) throw new Error('Enter a positive whole number of contracts.');
  if (!Number.isFinite(dollars) || dollars < .01 || dollars > 1e9) throw new Error('Protection must be from $0.01 to $1,000,000,000.');
  const ticks = Math.max(1, round(dollars / (spec.tick * spec.multiplier * quantity)));
  return { ticks, dollars: ticks * spec.tick * spec.multiplier * quantity };
}
export function simulationTime(seconds: number) {
  const days = Math.floor(seconds / 86400);
  return `${days ? `D${days + 1} ` : ''}${String(Math.floor(seconds / 3600) % 24).padStart(2, '0')}:${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(Math.floor(seconds) % 60).padStart(2, '0')}`;
}
