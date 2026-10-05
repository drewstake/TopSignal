import { describe, expect, it } from 'vitest';
import fixtures from './fixtures/reference-73521.json';
import { createSimulation, DEFAULT_CONFIG, INSTRUMENTS, TIMEFRAMES } from './simulationPreview';
import { stepParticipants } from './simulationParticipants';

describe('Read-only Luau reference fixture, seed 73521', () => {
  it('matches auction, first step, first news, warmup and subsequent playback', () => {
    const s = createSimulation(73521, { ...DEFAULT_CONFIG, supplyDemandTraders: 0, supportResistanceTraders: 0 }, 0);
    for (const expected of fixtures) {
      while (s.step < expected.step) stepParticipants(s);
      for (const field of ['step', 'randomState', 'tradeHash', 'nextOrder', 'nextTrade', 'totalVolume'] as const) expect(s[field], `${field} at step ${s.step}`).toBe(expected[field]);
      expect(s.regime.name).toBe(expected.regime);
      for (const field of ['nextRegime', 'nextNews', 'newsUntil'] as const) expect(s[field]).toBeCloseTo(expected[field], 9);
      for (const key of INSTRUMENTS) {
        const b = s.books[key], ref = expected.books[key];
        for (const field of ['last', 'volume', 'up', 'down'] as const) expect(b[field]).toBe(ref[field]);
        expect(b.volatility).toBeCloseTo(ref.volatility, 10);
        for (const field of ['fair', 'anchor', 'impulse'] as const) expect(s.markets[key][field]).toBeCloseTo(ref[field], 8);
        for (const [tf] of TIMEFRAMES) {
          const bars = ref.candles[tf === '1d' ? '1D' : tf];
          expect(b.candles[tf]).toHaveLength(bars.count);
          const bar = bars.last;
          expect(b.candles[tf].at(-1)).toEqual({ time: bar.Time, open: bar.Open, high: bar.High, low: bar.Low, close: bar.Close, volume: bar.Volume });
        }
      }
      for (const field of ['balance', 'cashFlow', 'realized'] as const) expect(s.accountOrder.reduce((n, id) => n + s.accounts[id][field], 0)).toBeCloseTo(expected.totals[field], 6);
    }
  });
});
