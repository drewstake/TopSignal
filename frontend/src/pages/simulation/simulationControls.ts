import { advanceSimulation, createSimulation, MAX_CATCHUP_STEPS, type Instrument, type SimulationState, type Timeframe } from './simulationPreview';

export interface SimulationSession {
  market: SimulationState; running: boolean; speed: number; generation: number;
  instrument: Instrument; timeframe: Timeframe; fractionalSteps: number;
}
export type EnvironmentOverrides = Pick<SimulationState['config'], 'liquidity' | 'volatility' | 'whaleFrequency' | 'newsFrequency'>;
export type SimulationAction =
  | { type: 'toggle' }
  | { type: 'speed'; speed: number }
  | { type: 'tick'; steps: number; fractionalSteps?: number }
  | { type: 'step'; steps: number }
  | { type: 'instrument'; instrument: Instrument }
  | { type: 'timeframe'; timeframe: Timeframe }
  | { type: 'configure'; overrides: EnvironmentOverrides }
  | { type: 'reset'; seed: number };

export function newSession(): SimulationSession {
  return { market: createSimulation(), running: true, speed: 1, generation: 0, instrument: 'BX', timeframe: '1m', fractionalSteps: 0 };
}
export function simulationReducer(session: SimulationSession, action: SimulationAction): SimulationSession {
  if (action.type === 'toggle') return { ...session, running: !session.running };
  if (action.type === 'speed') return [0.5, 1, 2, 4].includes(action.speed) ? { ...session, speed: action.speed } : session;
  if (action.type === 'instrument') return { ...session, instrument: action.instrument };
  if (action.type === 'timeframe') return { ...session, timeframe: action.timeframe };
  if (action.type === 'reset') return Number.isInteger(action.seed) && Math.abs(action.seed) <= 2147483646 ? { ...session, generation: session.generation + 1, fractionalSteps: 0,
    market: createSimulation(action.seed, session.market.config) } : session;
  if (action.type === 'configure') {
    const o = action.overrides;
    if (Object.values(o).some(v => !Number.isFinite(v) || v < 0 || v > 4) || o.volatility < 0.1 || o.liquidity < 0.1) return session;
    if (['liquidity', 'volatility', 'whaleFrequency', 'newsFrequency'].some(key => !Number.isFinite(o[key as keyof EnvironmentOverrides]))) return session;
    return { ...session, market: { ...session.market, config: { ...session.market.config, ...o }, events: [...session.market.events,
      { step: session.market.step, kind: 'Override' as const, name: `Liquidity ${o.liquidity}×; volatility ${o.volatility}×; whales ${o.whaleFrequency}×; news ${o.newsFrequency}×`, direction: session.market.direction }].slice(-40) } };
  }
  if (action.type === 'step' && session.running || action.type === 'tick' && !session.running || !Number.isFinite(action.steps)) return session;
  const steps = Math.min(MAX_CATCHUP_STEPS, Math.max(0, Math.floor(action.steps)));
  return { ...session, market: steps ? advanceSimulation(session.market, steps) : session.market,
    fractionalSteps: action.type === 'step' ? 0 : action.fractionalSteps ?? session.fractionalSteps };
}
