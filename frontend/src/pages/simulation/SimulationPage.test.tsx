// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SimulationPage } from './SimulationPage';
import { advanceSimulation, createSimulation, executeMarketOrder, repriceMarketMakers, sampleQuotePrice, submitLimitOrder, MARKET_PARTICIPANTS, MARKET_RETAIL_PARTICIPANTS, PARTICIPANT_ORDERS, summarizeBook, UPDATES_PER_CANDLE, UPDATES_PER_SECOND, type ParticipantOrder } from './simulationPreview';

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

const order = (id: string, side: 'buy' | 'sell', price: number, quantity: number): ParticipantOrder => ({
  id, participantId: id, kind: 'retail', side, price, quantity,
});

describe('Market simulation', () => {
  it('samples every cent across the full range, including the current value', () => {
    expect(Array.from({ length: 11 }, (_, index) => sampleQuotePrice(5, (index + 0.5) / 11)))
      .toEqual([4.95, 4.96, 4.97, 4.98, 4.99, 5, 5.01, 5.02, 5.03, 5.04, 5.05]);
    expect(sampleQuotePrice(10, 0)).toBe(9.9);
    expect(sampleQuotePrice(10, 0.999)).toBe(10.1);
    expect(sampleQuotePrice(0.01, 0.9)).toBe(0.01);
    for (const side of ['buy', 'sell']) {
      const prices = PARTICIPANT_ORDERS.filter((quote) => quote.side === side).map((quote) => quote.price);
      expect(prices.some((price) => price < 5)).toBe(true);
      expect(prices.some((price) => price === 5)).toBe(true);
      expect(prices.some((price) => price > 5)).toBe(true);
    }
  });

  it('fills market orders at the best resting price, respecting partial fills and queue order', () => {
    const book = [order('expensive', 'sell', 5.03, 3), order('first', 'sell', 5.01, 2),
      order('second', 'sell', 5.01, 4), order('bid', 'buy', 4.99, 10)];
    const result = executeMarketOrder(book, 'taker', 'buy', 4);
    expect(result.fills.map((fill) => [fill.makerId, fill.price, fill.quantity])).toEqual([
      ['first', 5.01, 2], ['second', 5.01, 2],
    ]);
    expect(result.book.map((quote) => quote.quantity)).toEqual([3, 0, 2, 10]);
    expect(book.map((quote) => quote.quantity)).toEqual([3, 2, 4, 10]);
    const sell = executeMarketOrder([order('lower', 'buy', 4.97, 2), order('best', 'buy', 4.99, 1)], 'taker', 'sell', 5);
    expect(sell.fills.map((fill) => [fill.price, fill.quantity])).toEqual([[4.99, 1], [4.97, 2]]);
    expect(sell.unfilled).toBe(2);
  });

  it('executes crossing limits only within their price, rests the remainder, and excludes self-trades', () => {
    const asks = [order('ask1', 'sell', 5, 2), order('ask2', 'sell', 5.04, 10)];
    const result = submitLimitOrder(asks, order('buyer', 'buy', 5.02, 5));
    expect(result.fills.map((fill) => [fill.price, fill.quantity])).toEqual([[5, 2]]);
    expect(result.book.at(-1)).toMatchObject({ price: 5.02, quantity: 3 });
    const sell = submitLimitOrder([order('bid', 'buy', 5.03, 2)], order('seller', 'sell', 4.98, 1));
    expect(sell.fills[0]).toMatchObject({ price: 5.03, quantity: 1 });
    expect(executeMarketOrder([order('self', 'sell', 5, 10)], 'self', 'buy', 1).fills).toEqual([]);
    expect(submitLimitOrder(asks, order('passive', 'buy', 4.95, 1)).fills).toEqual([]);
  });

  it('samples fresh market-maker quotes on each replacement while preserving remaining sizes', () => {
    const participant = MARKET_PARTICIPANTS.find((p) => p.kind === 'market-maker')!;
    const book: ParticipantOrder[] = [
      { id: `${participant.id}-buy`, participantId: participant.id, kind: participant.kind, side: 'buy', price: 4.95, quantity: 37 },
      { id: `${participant.id}-sell`, participantId: participant.id, kind: participant.kind, side: 'sell', price: 5.05, quantity: 0 },
    ];
    const low = repriceMarketMakers(book, 6, () => 0.2);
    const high = repriceMarketMakers(book, 6, () => 0.8);
    expect(low.book[0].price).not.toBe(high.book[0].price);
    for (const result of [low, high]) {
      expect(result.fills).toEqual([]);
      expect(result.book.map((quote) => quote.quantity)).toEqual([37, 100]);
      expect(result.book.every((quote) => Math.abs(quote.price - 6) <= 0.0600001)).toBe(true);
      expect(result.book[0].price).toBeLessThan(result.book[1].price);
    }
    expect(book.map((quote) => quote.quantity)).toEqual([37, 0]);
  });

  it('includes executions from initial and replacement quotes in candles without mutating state', () => {
    const initial = createSimulation();
    const original = structuredClone(initial);
    expect(initial.candles[0].volume).toBe(initial.lastFills.reduce((sum, fill) => sum + fill.quantity, 0));
    const next = advanceSimulation(initial);
    expect(initial).toEqual(original);
    expect(advanceSimulation(initial)).toEqual(next);
    const prices = next.lastFills.map((fill) => fill.price);
    expect(next.candles.at(-1)).toEqual({ update: 1, open: initial.price, high: Math.max(initial.price, ...prices),
      low: Math.min(initial.price, ...prices), close: prices.at(-1), volume: next.lastFills.reduce((sum, fill) => sum + fill.quantity, 0) });
    const retailFills = next.lastFills.filter((fill) => fill.takerId.startsWith('market-retail-'));
    expect(next.buyVolume + next.sellVolume).toBe(retailFills.reduce((sum, fill) => sum + fill.quantity, 0));
    const empty = advanceSimulation({ ...initial, goals: [], book: [], candles: [], price: 5, buyVolume: 0, sellVolume: 0 });
    expect(empty.candles).toEqual([]);
    expect(empty.price).toBe(5);
    expect(empty.buyVolume + empty.sellVolume).toBe(0);
  });

  it('keeps other makers on the book while refreshing one maker', () => {
    const makers = MARKET_PARTICIPANTS.filter((participant) => participant.kind === 'market-maker');
    const book: ParticipantOrder[] = makers.slice(0, 2).flatMap((participant) => [
      { id: `${participant.id}-buy`, participantId: participant.id, kind: participant.kind, side: 'buy', price: 4.9, quantity: 37 },
      { id: `${participant.id}-sell`, participantId: participant.id, kind: participant.kind, side: 'sell', price: 5.1, quantity: 42 },
    ]);
    const next = repriceMarketMakers(book, 5, () => 0.5, [makers[0]]);
    expect(next.book.filter((order) => order.participantId === makers[1].id)).toEqual(book.slice(2));
    expect(next.book.find((order) => order.id === `${makers[0].id}-buy`)!.price).toBe(5);
    expect(next.fills).toEqual([]);
  });

  it('keeps candles connected while preserving all trade extremes and volume', () => {
    let state = createSimulation();
    const open = state.price;
    const fills: typeof state.lastFills = [];
    for (let update = 1; update <= UPDATES_PER_CANDLE; update += 1) {
      state = advanceSimulation(state);
      fills.push(...state.lastFills);
      expect(state.candles).toHaveLength(2);
      expect(state.candles.at(-1)).toEqual({
        update: 1, open, close: fills.at(-1)!.price,
        high: Math.max(open, ...fills.map((fill) => fill.price)), low: Math.min(open, ...fills.map((fill) => fill.price)),
        volume: fills.reduce((sum, fill) => sum + fill.quantity, 0),
      });
    }
    const closed = structuredClone(state.candles.at(-1));
    const next = advanceSimulation(state);
    expect(next.candles).toHaveLength(3);
    expect(next.candles.at(-2)).toEqual(closed);
    expect(next.candles.at(-1)!.update).toBe(2);
    expect(next.candles.at(-1)!.open).toBe(closed!.close);
  });

  it('keeps the book uncrossed, quantities valid, and trade history bounded over continuous updates', () => {
    let state = createSimulation();
    const executions = new Map<string, number>();
    for (let i = 0; i < 2100; i += 1) {
      state = advanceSimulation(state);
      const snapshot = summarizeBook(state.book, state.price);
      if (snapshot.bid !== null && snapshot.ask !== null) expect(snapshot.bid).toBeLessThan(snapshot.ask);
      expect(state.book.every((quote) => quote.price > 0 && quote.quantity >= 0)).toBe(true);
      expect(state.lastFills.every((fill) => fill.takerId !== fill.makerId)).toBe(true);
      for (const goal of state.goals) {
        const quantity = state.lastFills.filter((fill) => fill.takerId === goal.participantId || fill.makerId === goal.participantId)
          .reduce((sum, fill) => sum + fill.quantity, 0);
        executions.set(goal.participantId, (executions.get(goal.participantId) ?? 0) + quantity);
        if (i % UPDATES_PER_SECOND !== 0 && i !== 2099) continue;
        expect(goal.filled).toBeGreaterThanOrEqual(0);
        expect(goal.filled).toBeLessThanOrEqual(goal.target);
        const totalFilled = goal.totalFilled;
        expect(totalFilled).toBe(executions.get(goal.participantId));
        const resting = state.book.filter((quote) => quote.participantId === goal.participantId && quote.quantity > 0);
        expect(resting.length).toBeLessThanOrEqual(1);
        expect(resting.reduce((sum, quote) => sum + quote.quantity, 0)).toBeLessThanOrEqual(goal.target - goal.filled);
        expect(resting.every((quote) => quote.side === goal.side)).toBe(true);
      }
    }
    expect(state.candles.length).toBeLessThanOrEqual(400);
    expect(state.buyVolume).toBeGreaterThan(0);
    expect(state.sellVolume).toBeGreaterThan(0);
    expect(state.book.length).toBeLessThanOrEqual(PARTICIPANT_ORDERS.length + state.goals.length);
    expect(state.goals.some((goal) => goal.completedGoals > 0)).toBe(true);
    expect(MARKET_PARTICIPANTS).toHaveLength(1200);
    expect(MARKET_RETAIL_PARTICIPANTS).toHaveLength(2000);
    expect(new Set([...MARKET_PARTICIPANTS, ...MARKET_RETAIL_PARTICIPANTS].map((p) => p.id)).size).toBe(3200);
  });

  it('varies retail arrivals and sizes while accounting for actual executed shares', () => {
    let state: ReturnType<typeof createSimulation> = { ...createSimulation(), goals: [],
      book: [order('bid', 'buy', 4.99, 1000000), order('ask', 'sell', 5.01, 1000000)] };
    const counts = new Set<number>();
    const sizes = new Set<number>();
    let volume = 0;
    let buyVolume = 0;
    let sellVolume = 0;
    for (let i = 0; i < UPDATES_PER_SECOND * 3; i += 1) {
      state = advanceSimulation(state);
      counts.add(state.lastFills.length);
      for (const fill of state.lastFills) {
        sizes.add(fill.quantity);
        volume += fill.quantity;
        if (fill.side === 'buy') buyVolume += fill.quantity;
        else sellVolume += fill.quantity;
      }
    }
    expect(counts.has(0)).toBe(true);
    expect(counts.size).toBeGreaterThan(3);
    expect(sizes.has(1)).toBe(true);
    expect([...sizes].some((size) => size > 10)).toBe(true);
    expect([...sizes].every((size) => size >= 1 && size <= 40)).toBe(true);
    expect(state.buyVolume).toBe(buyVolume);
    expect(state.sellVolume).toBe(sellVolume);
    expect(state.candles.at(-1)!.volume).toBe(volume);
  });

  it('carries the previous close across a price gap without inventing volume and caps history', () => {
    const initial = createSimulation();
    const candles = Array.from({ length: 400 }, (_, update) => ({ update, open: 5, high: 5, low: 5, close: 5, volume: 10 }));
    const next = advanceSimulation({ ...initial, goals: [], candles, update: 399 * UPDATES_PER_CANDLE,
      price: 5, book: [order('bid', 'buy', 4, 1000), order('ask', 'sell', 6, 1000)] });
    expect(next.candles).toHaveLength(400);
    expect(next.candles.at(-1)!.open).toBe(5);
    expect(next.candles.at(-1)!.high).toBeGreaterThanOrEqual(5);
    expect(next.candles.at(-1)!.low).toBeLessThanOrEqual(5);
    expect(next.candles.at(-1)!.close).toBe(next.lastFills.at(-1)!.price);
    expect(next.candles.at(-1)!.volume).toBe(next.lastFills.reduce((sum, fill) => sum + fill.quantity, 0));
  });

  it('shows actual trades on the always-running candlestick chart with no time controls', () => {
    const frames = vi.spyOn(window, 'requestAnimationFrame');
    render(<SimulationPage />);
    act(() => vi.advanceTimersByTime(100));
    expect(frames.mock.calls.length).toBeGreaterThan(3);
    expect(screen.getByText('3,200 participants')).toBeTruthy();
    expect(screen.getByRole('article', { name: 'Retail market orders' })).toBeTruthy();
    expect(screen.getAllByText('shares per goal')).toHaveLength(2);
    expect(screen.getAllByText('Current goals filled')).toHaveLength(2);
    act(() => vi.advanceTimersByTime(3000));
    expect(screen.getByText('Last trade price')).toBeTruthy();
    expect(screen.getByText('Candle 1')).toBeTruthy();
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('BLOX100 candlestick');
    expect(screen.getByRole('img').outerHTML).not.toMatch(/NaN|Infinity/);
    expect(screen.queryByRole('group', { name: 'Chart interval' })).toBeNull();
    expect(screen.queryByRole('button', { name: /play|pause/i })).toBeNull();
    cleanup();
    expect(vi.getTimerCount()).toBe(0);
    frames.mockRestore();
  });
});
