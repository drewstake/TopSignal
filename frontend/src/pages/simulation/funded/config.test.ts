import { describe, expect, it } from 'vitest';
import reference from './config-reference.json';
import { EXECUTION, INSTRUMENTS, POPULATION, REGIMES, RESERVE, SCALPER, SETTINGS, SPECS, TIMEFRAMES } from './model';

describe('supplied October 15 configuration', () => {
  it('uses the exact active market, population, clock, retention and execution values', () => {
    const c = reference.MarketConfig;
    expect(INSTRUMENTS).toEqual(c.ContractOrder); expect(TIMEFRAMES).toEqual(c.Timeframes);
    for (const key of INSTRUMENTS) {
      const source = c.Contracts[key];
      expect(SPECS[key]).toEqual({ name: source.Name, last: source.Last, tick: source.Tick, multiplier: source.Multiplier, scale: source.Scale, margin: source.Margin });
    }
    for (const kind of Object.keys(POPULATION) as (keyof typeof POPULATION)[]) expect(POPULATION[kind]).toEqual([c.BotPopulation.Counts[kind], c.BotPopulation.Capital[kind]]);
    expect([SETTINGS.step, SETTINGS.speed, SETTINGS.broadcastMs / 1000, SETTINGS.warmup, SETTINGS.catchup, SETTINGS.budgetMs]).toEqual([c.StepSeconds, c.SimulationSpeed, c.BroadcastSeconds, c.WarmupSeconds, c.MaxCatchupSteps, c.SimulationFrameBudgetMs]);
    expect([SETTINGS.candles, SETTINGS.trades, SETTINGS.orders, SETTINGS.accounts]).toEqual([c.MaxCandles, c.MaxTradeHistory, c.MaxOrderHistory, c.MaxAccountHistory]);
    expect(Object.values(EXECUTION)).toEqual(Object.values(c.PlayerExecution));
    expect([SETTINGS.makerPolicy, SETTINGS.makerRest, SETTINGS.makerReprice, SETTINGS.makerSize, SETTINGS.makerReplenish, SETTINGS.makerDecision]).toEqual([c.MakerQuotePolicyVersion, c.MakerMinRestSeconds, c.MakerRepriceTicks, c.MakerQuoteSizeMultiplier, c.MakerReplenishSeconds, c.MakerDecisionMultiplier]);
    expect([SETTINGS.traderActivity, SETTINGS.makerActivity, SETTINGS.whaleActivity]).toEqual([c.BotPopulation.TraderActivityScale, c.BotPopulation.MakerActivityScale, c.BotPopulation.WhaleActivityScale]);
    expect(REGIMES.map(r => Object.values(r))).toEqual(c.Regimes.map(r => Object.values(r)));
    expect([RESERVE.capital, RESERVE.limit, RESERVE.size, RESERVE.sizes, RESERVE.variation, RESERVE.growth, RESERVE.spacing, RESERVE.offsets, RESERVE.decision, RESERVE.rest, RESERVE.reprice]).toEqual([c.Reserve.Capital, c.Reserve.InventoryLimit, c.Reserve.QuoteSize, c.Reserve.QuoteSizes, c.Reserve.SizeVariation, c.Reserve.DepthGrowth, c.Reserve.SpacingTicks, c.Reserve.Offsets, c.Reserve.DecisionSeconds, c.Reserve.RestSeconds, c.Reserve.RepriceTicks]);
    expect(Object.values(SCALPER).slice(1)).toEqual(Object.values(c.Scalper));
  });
});
