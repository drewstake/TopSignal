// src/services/botRunner.ts
import { meanReversion, momentum, TradeSignal } from '../strategies';

// Map your dropdown‐friendly names to the strategy functions
const strategyMap: Record<string, (prices: number[]) => TradeSignal> = {
  'Mean Reversion': meanReversion,
  'Momentum':      momentum,
};

/**
 * Executes the named strategy against the provided price series.
 * Returns a trade signal or null if strategy not found.
 */
export function runStrategy(
  strategyName: string,
  prices: number[]
): TradeSignal | null {
  const strat = strategyMap[strategyName];
  return strat ? strat(prices) : null;
}
