import { describe, expect, it } from 'vitest';
import { advanceSimulation, createSimulation, DEFAULT_CONFIG, INSTRUMENTS, MAX_TRADES, TIMEFRAMES } from './simulationPreview';
import { stepParticipants } from './simulationParticipants';
import { accountSummary, cancelAll } from './simulationExchange';

describe('Reference environment and engine invariants', () => {
  it('starts news at second 80 and follows exact bounds for subsequent news and regimes', () => {
    const s = createSimulation(73521, DEFAULT_CONFIG, 0);
    expect(s.nextRegime).toBeGreaterThanOrEqual(100); expect(s.nextRegime).toBeLessThanOrEqual(240);
    while (s.time < 79.75) stepParticipants(s);
    expect(s.news).toBe('No synthetic news'); stepParticipants(s);
    expect(s.events.at(-1)).toMatchObject({ step: 320, kind: 'News' });
    expect(s.nextNews - s.time).toBeGreaterThanOrEqual(140); expect(s.nextNews - s.time).toBeLessThanOrEqual(420);
    expect(s.newsUntil - s.time).toBeGreaterThanOrEqual(20); expect(s.newsUntil - s.time).toBeLessThanOrEqual(55);
    while (s.time < s.nextRegime) { const next = s.nextRegime; stepParticipants(s); if (s.nextRegime !== next) break; }
    expect(s.nextRegime - s.time).toBeGreaterThanOrEqual(100); expect(s.nextRegime - s.time).toBeLessThanOrEqual(240);
  });
  it('updates fair values once per simulated second without writing trade prices', () => {
    const s = createSimulation(73521, DEFAULT_CONFIG, 0);
    for (const a of s.participants) { a.next = 1000; cancelAll(s, a.id); }
    const before = structuredClone(s.books); stepParticipants(s); const fair = structuredClone(s.markets);
    for (let i = 0; i < 3; i++) stepParticipants(s);
    expect(s.markets).toEqual(fair); expect(s.books).toEqual(before); stepParticipants(s);
    expect(s.markets).not.toEqual(fair); expect(s.books).toEqual(before);
    s.markets.BX.anchor = s.books.BX.reference * 1000;
    while (s.time < 2.25) stepParticipants(s);
    expect(s.markets.BX.fair).toBe(s.books.BX.reference * 1.4); expect(s.books.BX.last).toBe(before.BX.last);
  });
  it('preserves tick alignment, signed inventory, ledger conservation and bounded histories', () => {
    const s = createSimulation();
    expect(s.step).toBe(7200); expect(s.time).toBe(1800); expect(s.trades).toHaveLength(MAX_TRADES);
    for (const key of INSTRUMENTS) {
      expect(s.books[key].recent.length).toBeLessThanOrEqual(64); expect(s.books[key].flow.length).toBeLessThanOrEqual(41);
      const net = s.accountOrder.reduce((n, id) => { const p = s.accounts[id].positions[key]; return n + (p ? p.direction * p.quantity : 0); }, 0);
      expect(net).toBe(0);
      for (const [tf] of TIMEFRAMES) expect(s.books[key].candles[tf].reduce((n, c) => n + c.volume, 0)).toBe(s.books[key].volume);
    }
    expect(s.trades.every(t => Number.isInteger(t.ticks))).toBe(true);
    expect(s.accountOrder.reduce((n, id) => n + s.accounts[id].cashFlow, 0)).toBeCloseTo(0, 5);
    expect(s.accountOrder.reduce((n, id) => n + accountSummary(s, s.accounts[id]).equity - s.accounts[id].base, 0)).toBeCloseTo(0, 5);
    expect(s.events.length).toBeLessThanOrEqual(40);
  });
  it('is deterministic across immutable batches while preserving published snapshots', () => {
    const initial = createSimulation(73, DEFAULT_CONFIG, 0), frozen = structuredClone(initial);
    let batched = initial;
    for (const count of [1, 5, 8, 32, 14, 40]) batched = advanceSimulation(batched, count);
    expect(batched).toEqual(advanceSimulation(initial, 100)); expect(initial).toEqual(frozen);
    expect(createSimulation(74, DEFAULT_CONFIG, 100)).not.toEqual(batched);
  });
});
