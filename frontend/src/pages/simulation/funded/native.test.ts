import { expect, it } from 'vitest';
import reference from './fixtures/native-73521.json';
import { INSTRUMENTS } from './model';
import { createSimulation, stepSimulation } from './participants';

it('matches native Luau initialization and scheduler checkpoints through warmup', () => {
  const s = createSimulation(73521, 0);
  for (const row of reference) {
    while (s.step < row.Step) stepSimulation(s);
    expect({ random: s.randomState, orders: s.nextOrder, trades: s.nextTrade, volume: s.totalVolume, hash: s.tradeHash }, `step ${row.Step}`)
      .toEqual({ random: row.Random, orders: row.NextOrder, trades: row.NextTrade, volume: row.Volume, hash: row.Hash });
    for (const key of INSTRUMENTS) {
      const b = s.books[key], expected = row.Books[key];
      expect({ last: b.last, volume: b.volume, fair: b.fair, candles: b.candles['1m'].length }).toEqual({ last: expected.Last, volume: expected.Volume, fair: expected.Fair, candles: expected.Candles });
      expect(s.markets[key].anchor).toBeCloseTo(expected.Anchor, 8); expect(s.markets[key].fair).toBeCloseTo(expected.FairExact, 8);
    }
  }
}, 120000);
