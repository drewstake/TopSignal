import { describe, expect, it } from 'vitest';
import { createExchange } from './simulationExchange';
import { createSimulation, DEFAULT_CONFIG, random, BEHAVIORS } from './simulationPreview';

describe('Shared reference random stream', () => {
  it('normalizes the seed and uses Park–Miller multiplication without 32-bit truncation', () => {
    for (const seed of [0, 1, -1, 73521, -73521, 2147483646]) {
      const s = createExchange(seed); let state = BigInt(Math.floor(Math.abs(seed)) % 2147483646 + 1);
      for (let i = 0; i < 1000; i++) { state = state * 48271n % 2147483647n; expect(random(s)).toBe(Number(state) / 2147483647); }
    }
  });
  it('uses weighted ordinary selection instead of fixed per-kind populations', () => {
    const counts = Object.fromEntries(BEHAVIORS.map(k => [k, 0]));
    for (let seed = 1; seed <= 50; seed++) {
      const s = createSimulation(seed * 1237, DEFAULT_CONFIG, 0);
      for (const a of s.participants) if (a.kind in counts) counts[a.kind]++;
    }
    for (const kind of BEHAVIORS) expect(counts[kind] / (50 * 108) * 100).toBeCloseTo(DEFAULT_CONFIG.weights[kind], -1);
  });
});
