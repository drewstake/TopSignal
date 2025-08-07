import type { TradeSignal } from './index';

/**
 * Mean‐Reversion strategy:
 * - Rolling average over last `windowSize` prices.
 * - If price > avg by > threshold → SELL.
 * - If price < avg by > threshold → BUY.
 * - Else HOLD.
 */
export function meanReversion(
  prices: number[],
  windowSize = 20,
  threshold = 0.02
): TradeSignal {
  if (prices.length < windowSize) {
    return { action: 'hold', confidence: 0 };
  }
  const window = prices.slice(-windowSize);
  const avg = window.reduce((sum, p) => sum + p, 0) / windowSize;
  const current = prices[prices.length - 1];
  const deviation = (current - avg) / avg;

  if (deviation > threshold) {
    return {
      action: 'sell',
      confidence: Math.min((deviation - threshold) / threshold, 1),
    };
  } else if (deviation < -threshold) {
    return {
      action: 'buy',
      confidence: Math.min((-deviation - threshold) / threshold, 1),
    };
  } else {
    return {
      action: 'hold',
      confidence: 1 - Math.abs(deviation) / threshold,
    };
  }
}
