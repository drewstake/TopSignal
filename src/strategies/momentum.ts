import type { TradeSignal } from './index';

/**
 * Momentum strategy:
 * - Fast MA over `fastWindow`, slow MA over `slowWindow`.
 * - If fastMA > slowMA → BUY.
 * - If fastMA < slowMA → SELL.
 * - Confidence scales with the gap magnitude.
 */
export function momentum(
  prices: number[],
  fastWindow = 12,
  slowWindow = 26
): TradeSignal {
  if (prices.length < slowWindow) {
    return { action: 'hold', confidence: 0 };
  }
  const sum = (arr: number[]) => arr.reduce((a, b) => a + b, 0);
  const fastMA = sum(prices.slice(-fastWindow)) / fastWindow;
  const slowMA = sum(prices.slice(-slowWindow)) / slowWindow;
  const gap = Math.abs(fastMA - slowMA) / slowMA;
  const confidence = Math.min(gap * 10, 1);

  if (fastMA > slowMA) {
    return { action: 'buy', confidence };
  } else if (fastMA < slowMA) {
    return { action: 'sell', confidence };
  } else {
    return { action: 'hold', confidence: 0 };
  }
}
