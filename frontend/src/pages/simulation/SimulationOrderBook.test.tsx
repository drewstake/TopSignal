// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SimulationOrderBook } from './SimulationOrderBook';
import { buildPriceLadder, buildSimulationDepth } from './simulationDepth';
import { advanceSimulation, createSimulation, visibleOrders, summarizeBook, type ParticipantOrder } from './simulationPreview';

afterEach(() => { cleanup(); vi.useRealTimers(); });
const order = (id: string, side: 'buy' | 'sell', price: number, quantity: number, kind: ParticipantOrder['kind'] = 'Liquidity'): ParticipantOrder =>
  ({ id, participantId: id, side, price, quantity, kind });

describe('Centered price ladder', () => {
  it('aggregates liquidity by tick, keeps a uniform ladder, and uses one scale including offscreen walls', () => {
    const book = [order('bid-low', 'buy', 4.95, 20), order('bid-best', 'buy', 4.99, 5),
      order('Whale', 'buy', 4.99, 1000, 'Whale'), order('filled', 'buy', 5, 0),
      order('ask-high', 'sell', 5.25, 10), order('ask-best', 'sell', 5.01, 100, 'Maker')];
    const snapshot = structuredClone(book);
    const depth = buildPriceLadder(book, 500, .01);
    expect(depth.bids.map(l => [l.price, l.contracts, l.orders, l.cumulative])).toEqual([[4.99, 1005, 2, 1005], [4.95, 20, 1, 1025]]);
    expect(depth.asks.map(l => [l.price, l.contracts, l.cumulative])).toEqual([[5.01, 100, 100], [5.25, 10, 110]]);
    expect(depth.bids[0].byKind).toEqual({ Liquidity: 5, Whale: 1000 });
    expect(depth.maxLevelContracts).toBe(1005);
    expect(depth.rows).toHaveLength(21);
    expect(depth.rows.map(r => r.ticks)).toEqual(Array.from({ length: 21 }, (_, i) => 510 - i));
    expect(depth.spread).toBe(0.02);
    expect(buildPriceLadder(book, 1, .01).rows.at(-1)?.price).toBe(0.01);
    expect(book).toEqual(snapshot);
  });
  it('updates bars after partial fills without deleting or moving exhausted price rows', () => {
    const book = [order('bid', 'buy', 4.99, 200), order('ask', 'sell', 5.01, 100), order('next', 'sell', 5.02, 40)];
    const { rerender } = render(<SimulationOrderBook tickSize={.01} book={book} lastPrice={5} />);
    const table = screen.getByRole('table', { name: 'Simulated price ladder' });
    const row = within(table).getByRole('row', { name: 'Price $5.01' });
    expect(row.querySelector('.simulation-ladder-ask i')?.getAttribute('style')).toBe('width: 50%;');
    const partial = { book: book.map(o => o.id === 'ask' ? { ...o, quantity: 40 } : o) };
    rerender(<SimulationOrderBook tickSize={.01} book={partial.book} lastPrice={5.01} />);
    expect(within(table).getByRole('row', { name: 'Price $5.01, last trade' })).toBe(row);
    expect(row.querySelector('.simulation-ladder-ask')?.textContent).toBe('40 Σ 40');
    const filled = { book: partial.book.filter(o => o.id !== 'ask') };
    rerender(<SimulationOrderBook tickSize={.01} book={filled.book} lastPrice={5.01} />);
    expect(row.querySelector('.simulation-ladder-ask')?.textContent).toBe('·');
    expect(within(table).getAllByRole('row')).toHaveLength(22);
    expect(screen.getByText('$0.03')).toBeTruthy();
  });
  it('recenters only near the edge, preserves manual browsing, and exposes deeper levels by keyboard', () => {
    vi.useFakeTimers();
    const { rerender } = render(<SimulationOrderBook tickSize={.01} book={[]} lastPrice={5} />);
    rerender(<SimulationOrderBook tickSize={.01} book={[]} lastPrice={5.03} />);
    act(() => vi.advanceTimersByTime(500));
    expect(screen.getByRole('row', { name: 'Price $5.10' })).toBeTruthy();
    rerender(<SimulationOrderBook tickSize={.01} book={[]} lastPrice={5.09} />);
    act(() => vi.advanceTimersByTime(200));
    rerender(<SimulationOrderBook tickSize={.01} book={[]} lastPrice={5.10} />);
    act(() => vi.advanceTimersByTime(200));
    expect(screen.getByRole('row', { name: 'Price $5.20' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Show higher prices' }));
    rerender(<SimulationOrderBook tickSize={.01} book={[]} lastPrice={4.5} />);
    act(() => vi.advanceTimersByTime(500));
    expect(screen.getByRole('row', { name: 'Price $5.30' })).toBeTruthy();
    fireEvent.keyDown(screen.getByRole('region', { name: /Price ladder/ }), { key: 'Home' });
    expect(screen.getByRole('row', { name: 'Price $4.50, last trade' })).toBeTruthy();
  });
  it('handles missing sides without fake spread or liquidity', () => {
    const depth = buildSimulationDepth([]);
    expect(depth.spread).toBeNull();
    expect(depth.maxLevelContracts).toBe(1);
    render(<SimulationOrderBook tickSize={.01} book={[]} lastPrice={5} />);
    expect(screen.getByText(/No resting orders/)).toBeTruthy();
    expect(screen.getByText('—')).toBeTruthy();
  });
  it('keeps aggregated totals consistent through trades and quote replacements', () => {
    let state = createSimulation(73521, undefined, 0);
    for (let i = 0; i < 50; i++) {
      state = advanceSimulation(state);
      const orders = visibleOrders(state, 'BX');
      const depth = buildSimulationDepth(orders);
      const summary = summarizeBook(orders, state.books.BX.last! * .25);
      expect(depth.bidContracts).toBe(summary.bidContracts);
      expect(depth.askContracts).toBe(summary.askContracts);
      expect(depth.bids[0]?.price ?? null).toBe(summary.bid);
      expect(depth.asks[0]?.price ?? null).toBe(summary.ask);
    }
  });
});

it.each([.25, .1])('uses the selected contract tick size %s for depth rows', tick => {
  const depth = buildPriceLadder([order('bid', 'buy', tick * 100, 2)], 100, tick);
  expect(depth.rows[0].price).toBe(tick * 110);
  expect(depth.rows[10].bid?.contracts).toBe(2);
  expect(depth.rows[11].price).toBe(tick * 99);
});


it('wheel-browses stable ticks and exposes live level contributions by keyboard-accessible controls', () => {
  const book = [order('bid', 'buy', 4.99, 20, 'Maker')];
  render(<SimulationOrderBook book={book} lastPrice={5} tickSize={.01} />);
  fireEvent.click(screen.getByRole('button', { name: 'Inspect bid at 4.99: 20 contracts; cumulative 20' }));
  expect(screen.getByText('Market makers: 20 contracts')).toBeTruthy();
  const wheel = new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: -60 });
  fireEvent(screen.getByRole('region', { name: /Price ladder/ }), wheel);
  expect(wheel.defaultPrevented).toBe(true); expect(screen.getByRole('button', { name: 'Follow price' }).getAttribute('aria-pressed')).toBe('false');
  expect(screen.getByRole('row', { name: 'Price $5.13' })).toBeTruthy();
});
