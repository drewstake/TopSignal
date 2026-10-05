import { describe, expect, it } from 'vitest';
import { cancelAll } from './simulationExchange';
import { createSimulation, DEFAULT_CONFIG, INSTRUMENTS, REGIMES } from './simulationPreview';
import { quoteMaker, tradeParticipant } from './simulationParticipants';

function makerMarket() {
  const s = createSimulation(73521, DEFAULT_CONFIG, 0);
  for (const id of s.accountOrder) cancelAll(s, id);
  const a = s.participants[0]; s.accounts[a.id].positions = {};
  s.regime = REGIMES[0]; s.randomState = 1234;
  return { s, a };
}
describe('Reference participants', () => {
  it('creates 5 makers, 36 weighted ordinary traders and 3 whales per instrument with randomized risk and schedules', () => {
    const s = createSimulation(73521, DEFAULT_CONFIG, 0);
    for (const key of INSTRUMENTS) {
      const agents = s.participants.filter(a => a.key === key);
      expect(agents).toHaveLength(48); expect(agents.filter(a => a.kind === 'Maker')).toHaveLength(5); expect(agents.filter(a => a.kind === 'Whale')).toHaveLength(3);
      expect(agents.filter(a => a.kind === 'SupplyDemand')).toHaveLength(2); expect(agents.filter(a => a.kind === 'SupportResistance')).toHaveLength(2);
      expect(new Set(agents.map(a => a.budget)).size).toBeGreaterThan(30);
      for (const a of agents) {
        expect(a.next).toBeGreaterThanOrEqual(.2); expect(a.next).toBeLessThan(18);
        expect(a.risk).toBeGreaterThanOrEqual(.45); expect(a.risk).toBeLessThan(.85);
        expect(a.stop).toBeGreaterThanOrEqual(16); expect(a.target).toBeLessThanOrEqual(140);
      }
    }
  });
  it('quotes three levels per side, widens under volatility and reduces size under news', () => {
    const { s, a } = makerMarket();
    const high = structuredClone(s), news = structuredClone(s);
    high.regime = { ...s.regime, volatility: 2.5 }; news.newsUntil = 20;
    quoteMaker(s, a); quoteMaker(high, high.participants[0]); quoteMaker(news, news.participants[0]);
    const quotes = (state: typeof s) => Object.values(state.orders);
    expect(quotes(s)).toHaveLength(6); expect(quotes(s).filter(o => o.direction === 1)).toHaveLength(3);
    const spread = (state: typeof s) => Math.min(...quotes(state).filter(o => o.direction === -1).map(o => o.price!)) - Math.max(...quotes(state).filter(o => o.direction === 1).map(o => o.price!));
    expect(spread(high)).toBeGreaterThan(spread(s));
    expect(quotes(news).reduce((n, o) => n + o.remaining, 0)).toBeLessThan(quotes(s).reduce((n, o) => n + o.remaining, 0));
    expect(s.books.BX.last).toBe(high.books.BX.last); // Posting alone cannot write price.
  });
  it('skews quotes down for long inventory and stops locked bots without consuming RNG', () => {
    const { s, a } = makerMarket(), long = structuredClone(s);
    long.accounts[a.id].positions.BX = { id: 1, direction: 1, quantity: 100, entry: 5825 };
    quoteMaker(s, a); quoteMaker(long, long.participants[0]);
    const bids = (state: typeof s) => Math.max(...Object.values(state.orders).filter(o => o.direction === 1).map(o => o.price!));
    expect(bids(long)).toBeLessThan(bids(s));
    const bot = s.participants[5]; s.accounts[bot.id].locked = true;
    const before = structuredClone(s); tradeParticipant(s, bot); expect(s).toEqual(before);
  });
  it('splits whale parents and preserves scheduled direction for subsequent slices', () => {
    const s = createSimulation(73521, DEFAULT_CONFIG, 0), a = s.participants.find(p => p.kind === 'Whale')!;
    a.parent = 100; a.parentSide = -1; a.bias = -3;
    for (let i = 0; i < 100 && a.parent === 100; i++) tradeParticipant(s, a);
    expect(a.parent).toBeLessThan(100); expect(a.parent).toBeGreaterThanOrEqual(100 - Math.max(5, Math.floor(a.size / 3)));
    expect(a.parentSide).toBe(-1);
  });
});
