// Synthetic exchange only. Prices, beliefs and order limits use integer tick units;
// position entries and realized P&L use contract dollars, as in MarketEngine.luau.
import type { StructureState } from './simulationStructureModel';
export const DEFAULT_SEED = 73521;
export const STEP_SECONDS = 0.25;
export const BASE_SPEED = 18;
export const UPDATE_INTERVAL_MS = 50;
export const MAX_CATCHUP_STEPS = 32;
export const MAX_CANDLES = 1200;
export const MAX_TRADES = 4000;
export const INSTRUMENTS = ['BX', 'BT', 'BG'] as const;
export type Instrument = typeof INSTRUMENTS[number];
export const TIMEFRAMES = [['1m', 60], ['5m', 300], ['15m', 900], ['1h', 3600], ['1d', 86400]] as const;
export type Timeframe = typeof TIMEFRAMES[number][0];
export type Direction = 1 | -1;
export const PARTICIPANT_GROUPS = [
  { kind: 'Maker', label: 'Market makers', description: 'Three quote levels per side, adjusted for inventory, volatility and available liquidity.' },
  { kind: 'Liquidity', label: 'Liquidity traders', description: 'Varied orders driven by bias, noise and signed inventory.' },
  { kind: 'Momentum', label: 'Momentum traders', description: 'Follow recent executions while managing inventory.' },
  { kind: 'Value', label: 'Value traders', description: 'Trade toward a personal estimate of fair value.' },
  { kind: 'Reversion', label: 'Reversion traders', description: 'Trade against recent movement with a fair-value influence.' },
  { kind: 'Protective', label: 'Protective traders', description: 'Attach stop loss and take profit exits that execute in the book.' },
  { kind: 'Whale', label: 'Whales', description: 'Larger orders, sometimes split over time, with 80% aggressive execution.' },
  { kind: 'SupplyDemand', label: 'Supply & demand traders', description: 'Small orders on returns to fresh bases after a closed 1-minute departure: patient limits or confirmed rejections.' },
  { kind: 'SupportResistance', label: 'Support & resistance traders', description: 'Small orders on confirmed, repeatedly tested swing levels: rejections or breakout retests.' },
] as const;
export type ParticipantKind = typeof PARTICIPANT_GROUPS[number]['kind'];
export const BEHAVIORS = ['Liquidity', 'Momentum', 'Value', 'Reversion', 'Protective'] as const;
export interface ContractSpec { name: string; last: number; tick: number; scale: number; multiplier: number; margin: number }
export interface Regime { name: string; weight: number; volatility: number; liquidity: number; activity: number; trend: number }
export const REGIMES: Regime[] = [
  { name: 'Quiet', weight: 24, volatility: 0.45, liquidity: 1.4, activity: 0.65, trend: 0 },
  { name: 'Trending', weight: 22, volatility: 1, liquidity: 1, activity: 1.1, trend: 0.10 },
  { name: 'Range-bound', weight: 24, volatility: 0.85, liquidity: 1.2, activity: 1, trend: 0 },
  { name: 'High volatility', weight: 16, volatility: 2.5, liquidity: 0.6, activity: 1.7, trend: 0 },
  { name: 'Thin liquidity', weight: 14, volatility: 1.5, liquidity: 0.22, activity: 0.85, trend: 0 },
];
export interface SimulationConfig {
  contractOrder: Instrument[]; contracts: Record<Instrument, ContractSpec>;
  makers: number; traders: number; whales: number;
  supplyDemandTraders: number; supportResistanceTraders: number;
  structureRewardRisk: number; structureMinRewardRisk: number;
  weights: Record<typeof BEHAVIORS[number], number>;
  liquidity: number; volatility: number; whaleFrequency: number; newsFrequency: number;
  regimeSeconds: [number, number]; regimes: Regime[]; priceBandFraction: number;
}
export const DEFAULT_CONFIG: SimulationConfig = {
  contractOrder: [...INSTRUMENTS],
  contracts: {
    BX: { name: 'Blox 500', last: 5824.75, tick: 0.25, scale: 1, multiplier: 50, margin: 750 },
    BT: { name: 'BloxTech 100', last: 20485.50, tick: 0.25, scale: 3, multiplier: 20, margin: 750 },
    BG: { name: 'Blox Gold', last: 2384.60, tick: 0.10, scale: 1, multiplier: 100, margin: 750 },
  },
  makers: 5, traders: 36, whales: 3,
  supplyDemandTraders: 2, supportResistanceTraders: 2, structureRewardRisk: 2, structureMinRewardRisk: 1.25,
  weights: { Liquidity: 30, Momentum: 20, Value: 22, Reversion: 18, Protective: 10 },
  liquidity: 1, volatility: 1, whaleFrequency: 1, newsFrequency: 1,
  regimeSeconds: [100, 240], regimes: REGIMES, priceBandFraction: 0.10,
};
export interface SimulationCandle { time: number; open: number; high: number; low: number; close: number; volume: number }
export interface Position {
  id: number; direction: Direction; quantity: number; entry: number;
  stop?: number; target?: number; triggered?: string;
}
export interface Account {
  id: string; base: number; balance: number; realized: number; cashFlow: number;
  limit: number; risk: number; locked: boolean; liquidating: boolean;
  positions: Partial<Record<Instrument, Position>>; openOrders: number[];
  bought: number; sold: number; cancelled: number; completed: number;
}
export interface Participant {
  id: string; key: Instrument; kind: ParticipantKind; budget: number; risk: number; limit: number;
  speed: number; size: number; bias: number; belief: number; patience: number; aggression: number;
  next: number; stop: number; target: number; parent: number; parentSide: Direction;
  structure?: StructureState;
}
export interface Order {
  id: number; owner: string; contract: Instrument; direction: Direction; type: 'Market' | 'Limit';
  original: number; remaining: number; filled: number; cancelled: number; notional: number;
  price?: number; time: number; reference: number; status: string;
  stopTicks?: number; targetTicks?: number; reduceOnly: boolean; expires?: number;
  stopPrice?: number; targetPrice?: number; minimumRewardRisk?: number;
}
export interface OrderRequest {
  contract: Instrument; direction: Direction; quantity: number; type: 'Market' | 'Limit';
  price?: number; stopTicks?: number; targetTicks?: number; reduceOnly?: boolean; expires?: number;
  stopPrice?: number; targetPrice?: number; minimumRewardRisk?: number;
}
export interface Execution {
  id: number; time: number; step: number; contract: Instrument; ticks: number; quantity: number;
  buyer: string; seller: string; direction: Direction;
}
export interface MarketBook {
  reference: number; last: number | null; open: number | null; volume: number;
  bids: number[]; asks: number[]; recent: number[]; up: number; down: number; volatility: number;
  candles: Record<Timeframe, SimulationCandle[]>; candleVolume: Record<Timeframe, number>;
  flow: { time: number; buy: number; sell: number }[];
}
export interface SimulationState {
  engineVersion: 2; initialSeed: number; randomState: number; step: number; time: number;
  config: SimulationConfig; books: Record<Instrument, MarketBook>;
  accounts: Record<string, Account>; accountOrder: string[]; participants: Participant[];
  orders: Record<number, Order>; nextOrder: number; nextTrade: number;
  trades: Execution[]; totalVolume: number; tradeHash: number;
  pendingStops: { owner: string; contract: Instrument; position: number }[];
  markets: Record<Instrument, { fair: number; anchor: number; impulse: number }>;
  regime: Regime; direction: Direction; nextRegime: number; nextNews: number; nextBeliefs: number;
  news: string; newsUntil: number; events: { step: number; kind: 'Regime' | 'News' | 'Override'; name: string; direction: Direction }[];
}
// Presentation orders contain dollar prices; they never go back into the engine.
export interface ParticipantOrder { id: string; participantId: string; kind: ParticipantKind; side: 'buy' | 'sell'; price: number; quantity: number }
export const round = (n: number) => n < 0 ? -Math.floor(-n + 0.5) : Math.floor(n + 0.5); // Luau math.round
export const clamp = (n: number, low: number, high: number) => Math.max(low, Math.min(high, n));
export function random(state: Pick<SimulationState, 'randomState'>) {
  state.randomState = state.randomState * 48271 % 2147483647;
  return state.randomState / 2147483647;
}
export const range = (s: SimulationState, a: number, b: number) => a + (b - a) * random(s);
export const int = (s: SimulationState, a: number, b: number) => Math.floor(range(s, a, b + 1));
export const noise = (s: SimulationState) => (random(s) + random(s) + random(s) - 1.5) * 2;

export function aggregateExecution(book: MarketBook, time: number, price: number, quantity: number) {
  for (const [key, seconds] of TIMEFRAMES) {
    const series = book.candles[key];
    const bucket = Math.floor(time / seconds) * seconds;
    const previous = series.at(-1);
    // Replace only the current candle. Completed bars are never mutated.
    if (!previous || previous.time !== bucket) {
      series.push({ time: bucket, open: price, high: price, low: price, close: price, volume: quantity });
      if (series.length > MAX_CANDLES) series.shift();
    } else series[series.length - 1] = { ...previous, high: Math.max(previous.high, price), low: Math.min(previous.low, price), close: price, volume: previous.volume + quantity };
    book.candleVolume[key] += quantity;
  }
}
