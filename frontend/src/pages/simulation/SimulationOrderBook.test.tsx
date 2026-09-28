// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { SimulationOrderBook } from './SimulationOrderBook';
import { buildSimulationDepth } from './simulationDepth';
import { advanceSimulation, createSimulation, executeMarketOrder, summarizeBook, type ParticipantOrder } from './simulationPreview';

afterEach(cleanup);
const order = (id: string, side: 'buy' | 'sell', price: number, quantity: number, kind: ParticipantOrder['kind'] = 'retail'): ParticipantOrder => ({
  id, participantId: id, side, price, quantity, kind,
});

describe('Simulation L2 order book', () => {
  it('groups remaining shares by cent price, sorts best first, and computes cumulative depth', () => {
    const book = [order('bid-low', 'buy', 4.95, 20), order('bid-best', 'buy', 4.99, 5),
      order('whale', 'buy', 4.99, 1000, 'whale'), order('filled', 'buy', 5, 0),
      order('ask-high', 'sell', 5.05, 10), order('ask-best', 'sell', 5.01, 100, 'market-maker')];
    const snapshot = structuredClone(book);
    const depth = buildSimulationDepth(book);
    expect(depth.bids.map((level) => [level.price, level.shares, level.orders, level.cumulative])).toEqual([
      [4.99, 1005, 2, 1005], [4.95, 20, 1, 1025],
    ]);
    expect(depth.asks.map((level) => [level.price, level.shares, level.cumulative])).toEqual([[5.01, 100, 100], [5.05, 10, 110]]);
    expect(depth.bids[0].byKind).toEqual({ retail: 5, whale: 1000 });
    expect(depth.maxLevelShares).toBe(1005);
    expect(depth.bidShares).toBe(1025);
    expect(depth.askShares).toBe(110);
    expect(depth.spread).toBe(0.02);
    expect(book).toEqual(snapshot);
  });

  it('updates displayed walls after fills and removes exhausted levels', () => {
    const book = [order('bid', 'buy', 4.99, 20), order('ask', 'sell', 5.01, 100), order('next', 'sell', 5.02, 40)];
    const { rerender } = render(<SimulationOrderBook book={book} lastPrice={5} />);
    const asks = screen.getByRole('table', { name: 'Simulated ask levels' });
    expect(within(asks).getByRole('row', { name: /\$5\.01/ }).textContent).toContain('100');
    const partial = executeMarketOrder(book, 'retail-market', 'buy', 60);
    rerender(<SimulationOrderBook book={partial.book} lastPrice={5.01} />);
    expect(within(asks).getByRole('row', { name: /\$5\.01/ }).textContent).toContain('40');
    const filled = executeMarketOrder(partial.book, 'retail-market', 'buy', 40);
    rerender(<SimulationOrderBook book={filled.book} lastPrice={5.01} />);
    expect(within(asks).queryByRole('row', { name: /\$5\.01/ })).toBeNull();
    expect(within(asks).getByRole('row', { name: /\$5\.02/ })).toBeTruthy();
    expect(screen.getByText('$0.03')).toBeTruthy();
  });

  it('handles missing sides without fake prices, depth bars, or spread', () => {
    const depth = buildSimulationDepth([]);
    expect(depth.spread).toBeNull();
    expect(depth.maxLevelShares).toBe(1);
    render(<SimulationOrderBook book={[]} lastPrice={5} />);
    expect(screen.getByText('No resting buy orders')).toBeTruthy();
    expect(screen.getByText('No resting sell orders')).toBeTruthy();
    expect(screen.getByText('—')).toBeTruthy();
  });

  it('keeps L2 totals consistent with the live book across quote replacements and trades', () => {
    let state = createSimulation();
    for (let index = 0; index < 30; index += 1) {
      state = advanceSimulation(state);
      const depth = buildSimulationDepth(state.book);
      const summary = summarizeBook(state.book, state.price);
      expect(depth.bidShares).toBe(summary.bidShares);
      expect(depth.askShares).toBe(summary.askShares);
      expect(depth.bids[0]?.price ?? null).toBe(summary.bid);
      expect(depth.asks[0]?.price ?? null).toBe(summary.ask);
      expect([...depth.bids, ...depth.asks].every((level) => level.shares > 0)).toBe(true);
    }
  });
});
