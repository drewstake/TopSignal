import { describe, expect, it } from 'vitest';
import { INSTRUMENTS, PLAYER, POPULATION, SPECS, TIMEFRAMES, noise, random, type State } from './model';
import { createSimulation, stepSimulation } from './participants';
import { depth, submitOrder, summary } from './exchange';
import { publishView, initialView } from './runtime';

describe('Get-Funded world', () => {
  it('initializes exactly 390 funded bots in source order, with normalization and inventory caps', () => {
    const s = createSimulation(0, 0);
    expect(s.participants).toHaveLength(390); expect(s.accountOrder).toHaveLength(391);
    expect(INSTRUMENTS).toEqual(['BX', 'BT', 'BG']);
    for (const key of INSTRUMENTS) {
      expect(s.participants.filter(p => p.key === key)).toHaveLength(130);
      for (const [kind, [count, capital]] of Object.entries(POPULATION)) {
        const bots = s.participants.filter(p => p.key === key && p.kind === kind);
        expect(bots).toHaveLength(count);
        for (const bot of bots) {
          expect(bot.budget).toBe(capital); expect(s.accounts[bot.id].base).toBe(capital);
          expect(bot.limit).toBeLessThanOrEqual(Math.floor(capital * bot.risk * .5 / SPECS[key].margin));
          expect(bot.size).toBeGreaterThanOrEqual(1); expect(bot.limit).toBeGreaterThanOrEqual(bot.size);
        }
      }
    }
    expect(s.participants.filter(a => a.kind === 'Scalper' && a.key === 'BT').every(a => a.size === 1)).toBe(true);
    expect(s.participants.filter(a => a.kind === 'Reserve').map(a => a.size)).toEqual([...Array(6).fill(24), ...Array(6).fill(4), ...Array(6).fill(3)]);
    expect(s.participants.filter(a => a.key === 'BX' && a.kind === 'Scalper' && a.style === 'Momentum')).toHaveLength(12);
    expect(s.participants[116].kind).toBe('Scalper');
  });
  it('uses the supplied deterministic generator, including non-Gaussian three-draw noise', () => {
    const state = { randomState: 1 };
    expect(random(state)).toBe(48271 / 2147483647);
    const a = createSimulation(42, 0), b = structuredClone(a); const expected = (random(b) + random(b) + random(b) - 1.5) * 2;
    expect(noise(a)).toBe(expected); expect(a.randomState).toBe(b.randomState);
  });
  it('runs the full warmup through actual finite executions and reproduces seeded inputs', () => {
    const start = performance.now(), a = createSimulation(73521), b = createSimulation(73521);
    expect(a.step).toBe(7200); expect(a.time).toBe(1800); expect(a).toEqual(b);
    expect(a.accounts[PLAYER].balance).toBe(10000); expect(a.accounts[PLAYER].openOrders).toEqual([]);
    expect(a.totalVolume).toBeGreaterThan(1000); expect(a.trades.length).toBeLessThanOrEqual(4000);
    const accounts = Object.values(a.accounts);
    expect(accounts.reduce((total, account) => total + summary(a, account).equity - account.base, 0)).toBeCloseTo(0, 5);
    expect(accounts.reduce((total, account) => total + account.cashFlow, 0)).toBeCloseTo(0, 4);
    for (const trade of a.trades) expect((trade.ticks - trade.fair) * trade.direction).toBeLessThanOrEqual(20 * SPECS[trade.contract].scale);
    for (const key of INSTRUMENTS) {
      expect(accounts.reduce((total, account) => total + (account.positions[key]?.quantity ?? 0) * (account.positions[key]?.direction ?? 1), 0)).toBe(0);
      const bids = depth(a, key, 1), asks = depth(a, key, -1);
      if (bids.length && asks.length) expect(bids[0].ticks).toBeLessThan(asks[0].ticks);
      expect(a.books[key].candles['1m'].length).toBeGreaterThanOrEqual(25);
      expect(a.books[key].last).toBe(a.trades.filter(t => t.contract === key).at(-1)?.ticks);
      for (const [tf] of TIMEFRAMES) { expect(a.books[key].candleVolume[tf]).toBe(a.books[key].volume); expect(a.books[key].candles[tf].reduce((n, bar) => n + bar.volume, 0)).toBe(a.books[key].volume); }
    }
    const inputs = (s: State) => { submitOrder(s, PLAYER, { contract: 'BX', direction: 1, quantity: 2, type: 'Market', stopDollars: 500, targetDollars: 1500 }); for (let i = 0; i < 120; i++) stepSimulation(s); };
    inputs(a); inputs(b); expect(a).toEqual(b);
    const before = structuredClone(a); for (let i = 0; i < 10; i++) { publishView(a, initialView(a.seed)); for (const key of INSTRUMENTS) depth(a, key, 1); } expect(a).toEqual(before);
    console.info(`Two full warmups + replay: ${((performance.now() - start) / 1000).toFixed(2)}s; volume ${a.totalVolume}; trades ${a.nextTrade}`);
  }, 120000);
});
