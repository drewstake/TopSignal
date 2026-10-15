// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TradingPage } from './TradingPage';
import { createExchange, addAccount, submitOrder } from './exchange';
import { PLAYER, SPECS, type State } from './model';
import { initialView, publishView, type View } from './runtime';

const store = vi.hoisted(() => ({ view: null as View | null, dispatch: vi.fn() }));
vi.mock('./runtime', async original => ({ ...await original<typeof import('./runtime')>(), getFundedRuntime: () => ({ getSnapshot: () => store.view!, subscribe: () => () => {}, dispatch: store.dispatch }) }));
vi.mock('../SimulationChart', () => ({ SimulationChart: ({ symbol, timeframe }: { symbol: string; timeframe: string }) => <div role="img" aria-label={`${symbol} ${timeframe} authoritative chart`} /> }));
let state: State;
beforeEach(() => {
  store.dispatch.mockClear(); state = createExchange(123); addAccount(state, PLAYER, 10000, 10, .8, true);
  store.view = structuredClone(publishView(state, { ...initialView(123), warming: false, warmupProgress: 1 }));
});
afterEach(cleanup);

describe('replacement trading interface', () => {
  it('wires market buys and sells to the authoritative worker with total-dollar protection', () => {
    render(<TradingPage />); fireEvent.change(screen.getByLabelText('Contracts'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: /Buy \/ Long/ }));
    expect(store.dispatch).toHaveBeenLastCalledWith({ type: 'order', request: { contract: 'BX', direction: 1, quantity: 3, type: 'Market', price: undefined, stopDollars: 500, targetDollars: 1500 } });
    fireEvent.click(screen.getByRole('button', { name: /Sell \/ Short/ })); expect(store.dispatch.mock.lastCall?.[0].request.direction).toBe(-1);
    expect(screen.getByText('13 ticks · $487.50 total')).toBeTruthy();
  });
  it('normalizes limit prices, validates whole contracts, and supports disabled protection', () => {
    render(<TradingPage />);
    fireEvent.change(screen.getByLabelText('Order type'), { target: { value: 'Limit' } }); fireEvent.change(screen.getByLabelText('Limit entry price'), { target: { value: '5824.82' } });
    fireEvent.click(screen.getByLabelText('Stop loss', { exact: true })); fireEvent.click(screen.getByLabelText('Take profit', { exact: true }));
    fireEvent.click(screen.getByRole('button', { name: /Buy \/ Long/ })); expect(store.dispatch.mock.lastCall?.[0].request).toMatchObject({ type: 'Limit', price: 5824.75, stopDollars: undefined, targetDollars: undefined });
    store.dispatch.mockClear(); fireEvent.change(screen.getByLabelText('Contracts'), { target: { value: '1.5' } }); fireEvent.click(screen.getByRole('button', { name: /Sell \/ Short/ }));
    expect(store.dispatch).not.toHaveBeenCalled(); expect(screen.getByRole('alert').textContent).toContain('whole number');
  });
  it('changes chart selections without dispatching a simulation command', () => {
    render(<TradingPage />); fireEvent.click(screen.getByRole('button', { name: /BT BloxTech/ })); fireEvent.click(screen.getByRole('button', { name: '1D' }));
    expect(screen.getByRole('img').getAttribute('aria-label')).toBe('BT 1D authoritative chart'); expect(store.dispatch).not.toHaveBeenCalled();
    expect(screen.queryByLabelText('Simulation speed')).toBeNull(); expect(screen.getByText('18× · 72 steps/s')).toBeTruthy(); expect(screen.queryByText('Advanced settings')).toBeNull();
  });
  it('wires pause, step, same-seed restart, and flatten', () => {
    render(<TradingPage />); fireEvent.click(screen.getByRole('button', { name: /Pause/ })); expect(store.dispatch).toHaveBeenLastCalledWith({ type: 'play', playing: false });
    fireEvent.click(screen.getByRole('button', { name: 'Restart same seed' })); expect(store.dispatch).toHaveBeenLastCalledWith({ type: 'restart', seed: 123 });
    fireEvent.click(screen.getByRole('button', { name: 'Flatten all' })); expect(store.dispatch).toHaveBeenLastCalledWith({ type: 'flatten' });
  });
  it('expands the chart without dispatching engine commands', () => {
    render(<TradingPage />);
    const expand = screen.getByRole('button', { name: 'Expand chart' });
    fireEvent.click(expand); expect(screen.getByRole('dialog', { name: 'Expanded market workspace' })).toBeTruthy();
    expect(document.body.style.overflow).toBe('hidden');
    fireEvent.keyDown(window, { key: 'Escape' }); expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(expand); expect(document.body.style.overflow).not.toBe('hidden');
    expect(store.dispatch).not.toHaveBeenCalled();
  });
  it('shows actual positions, working orders, fills and protection amendments', () => {
    addAccount(state, 'seller', 1000000, 500, .8); addAccount(state, 'buyer', 1000000, 500, .8);
    submitOrder(state, 'seller', { contract: 'BX', direction: -1, quantity: 100, type: 'Limit', price: SPECS.BX.last + .25 });
    submitOrder(state, PLAYER, { contract: 'BX', direction: 1, quantity: 1, type: 'Market', stopDollars: 500, targetDollars: 1500 });
    store.view = structuredClone(publishView(state, { ...initialView(123), warming: false, warmupProgress: 1 }));
    render(<TradingPage />); expect(screen.getByText('Long 1')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Close position' })); expect(store.dispatch).toHaveBeenLastCalledWith({ type: 'close', key: 'BX' });
    fireEvent.click(screen.getByRole('button', { name: 'Edit protection' })); fireEvent.change(screen.getByLabelText('BX protection total dollars'), { target: { value: '250' } }); fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(store.dispatch.mock.lastCall?.[0]).toMatchObject({ type: 'protection', key: 'BX', field: 'stop', dollars: 250 });
    fireEvent.click(screen.getByRole('button', { name: /Working orders/ })); expect(screen.getByText('Take profit', { selector: 'td' })).toBeTruthy();
    const target = state.accounts[PLAYER].positions.BX!.targetOrder!; fireEvent.click(screen.getByRole('button', { name: `Cancel #${target}` })); expect(store.dispatch).toHaveBeenLastCalledWith({ type: 'cancel', id: target });
    fireEvent.click(screen.getByRole('button', { name: 'Fills' })); expect(screen.getByText('Slippage / contract')).toBeTruthy();
  });
});
