// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SimulationPage } from './SimulationPage';
import { createSimulationRuntime, type SimulationRuntime } from './simulationRuntime';
import { simulationTime, visibleOrders } from './simulationPreview';
const viewRuntime = vi.hoisted(() => ({ current: null as SimulationRuntime | null }));
vi.mock('./simulationRuntime', async importOriginal => ({ ...await importOriginal<typeof import('./simulationRuntime')>(), getSimulationRuntime: () => viewRuntime.current! }));
beforeEach(() => {
  vi.useFakeTimers(); vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  HTMLDialogElement.prototype.close = function () { this.open = false; };
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(false); localStorage.clear();
  viewRuntime.current = createSimulationRuntime({ storage: localStorage }).api;
});
afterEach(() => { cleanup(); viewRuntime.current?.dispose(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
describe('Simulation page', () => {
  it('exposes ownership recovery beside Resume and continues the saved run after takeover', () => {
    const owner = createSimulationRuntime({ storage: localStorage }).api;
    try {
      owner.start(); render(<SimulationPage />);
      expect(screen.getByRole('button', { name: 'Resume' })).toHaveProperty('disabled', true);
      expect(screen.getByRole('alert', { name: 'Playback ownership' })).toBeTruthy();
      const saved = owner.getSnapshot().session.market;
      fireEvent.click(screen.getByRole('button', { name: 'Use this tab' }));
      expect(screen.queryByRole('alert', { name: 'Playback ownership' })).toBeNull();
      expect(viewRuntime.current!.getSnapshot().session.market).toEqual(saved);
      fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
      act(() => vi.advanceTimersByTime(100));
      expect(screen.getByRole('button', { name: 'Pause' })).toHaveProperty('disabled', false);
      expect(viewRuntime.current!.getSnapshot().session.market.step).toBeGreaterThan(saved.step);
      expect(owner.getSnapshot().session.running).toBe(false);
    } finally { owner.dispose(); }
  });
  it('wires pause/resume, speed, seed and warmup reset to the shared market', () => {
    render(<SimulationPage />); expect(screen.getByLabelText('Simulation elapsed time').textContent).toBe('00:30:00.00');
    act(() => vi.advanceTimersByTime(1000)); expect(screen.getByLabelText('Simulation elapsed time').textContent).toBe('00:30:18.00');
    fireEvent.click(screen.getByRole('button', { name: 'Pause' })); const market = viewRuntime.current!.getSnapshot().session.market;
    act(() => vi.advanceTimersByTime(1000)); expect(viewRuntime.current!.getSnapshot().session.market).toBe(market);
    fireEvent.change(screen.getByLabelText('Simulation speed'), { target: { value: '4' } }); fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
    act(() => vi.advanceTimersByTime(1000)); expect(screen.getByLabelText('Simulation elapsed time').textContent).toBe('00:31:30.00');
    fireEvent.click(screen.getByRole('button', { name: 'Pause' })); fireEvent.click(screen.getByRole('button', { name: 'Advanced settings' })); fireEvent.change(screen.getByLabelText('Random seed'), { target: { value: '42' } });
    fireEvent.click(screen.getByRole('button', { name: 'Close advanced settings' })); fireEvent.click(screen.getByRole('button', { name: 'Reset' })); expect(viewRuntime.current!.getSnapshot().session.market.initialSeed).toBe(73521); fireEvent.click(screen.getByRole('button', { name: 'Replace run' })); expect(screen.getByLabelText('Simulation elapsed time').textContent).toBe('00:30:00.00');
    expect(viewRuntime.current!.getSnapshot().session.market.initialSeed).toBe(42); expect(screen.getByRole('img').outerHTML).not.toMatch(/NaN|Infinity/);
  });
  it('switches all instrument/timeframe views without advancing state and shows consistent quotes/depth', () => {
    render(<SimulationPage />); fireEvent.click(screen.getByRole('button', { name: 'Pause' })); const before = viewRuntime.current!.getSnapshot().session.market;
    fireEvent.click(screen.getByRole('button', { name: 'Book' }));
    for (const instrument of ['BT', 'BG', 'BX']) for (const timeframe of ['1m', '5m', '15m', '1h', '1d']) {
      fireEvent.change(screen.getByLabelText('Simulation instrument'), { target: { value: instrument } }); fireEvent.change(screen.getByLabelText('Chart timeframe'), { target: { value: timeframe } });
      expect(viewRuntime.current!.getSnapshot().session.market).toBe(before);
      expect(screen.getByRole('img').getAttribute('aria-label')).toContain(instrument);
      expect(screen.getByRole('complementary', { name: `${instrument} simulation L2 order book` })).toBeTruthy();
    }
    const price = before.books.BX.last! * .25;
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain(price.toFixed(2));
    expect(visibleOrders(before, 'BX').every(o => o.price % .25 === 0)).toBe(true);
    expect(screen.getByText('48 traders · 144 across all markets')).toBeTruthy();
  });
  it('applies explicit overrides without changing existing resources and validates drafts', () => {
    render(<SimulationPage />); fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    const before = viewRuntime.current!.getSnapshot().session.market;
    const field = screen.getByLabelText('Maker liquidity (×)'); fireEvent.change(field, { target: { value: '0.5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply changes' })); const after = viewRuntime.current!.getSnapshot().session.market;
    expect(after.config.liquidity).toBe(.5); expect(after.books).toBe(before.books); expect(after.accounts).toBe(before.accounts); expect(after.randomState).toBe(before.randomState);
    fireEvent.click(screen.getByRole('button', { name: 'Activity' }));
    const timeline = screen.getByRole('region', { name: 'Market event timeline' });
    expect(Array.from(timeline.querySelectorAll('.simulation-event strong'), event => event.textContent)).toEqual(after.events.slice(-2).reverse().map(event => event.name));
    fireEvent.click(within(screen.getByRole('navigation', { name: 'Market panels' })).getByRole('button', { name: 'Participants' }));
    fireEvent.change(field, { target: { value: '' } }); fireEvent.submit(field.closest('form')!); expect(screen.getByRole('alert').textContent).toContain('within its limits');
  });
  it('shows both structural groups, modes and explanations independently of the displayed timeframe', () => {
    render(<SimulationPage />); fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    expect(screen.getByText('Supply & demand traders', { selector: 'strong' })).toBeTruthy(); expect(screen.getByText('Support & resistance traders', { selector: 'strong' })).toBeTruthy();
    fireEvent.click(screen.getByText('Inspect individual participants · BX'));
    const market = viewRuntime.current!.getSnapshot().session.market;
    for (const [group, modes] of [['SupplyDemand', ['Patient zone limit', 'Candle-confirmed zone rejection']], ['SupportResistance', ['Support / resistance rejection', 'Breakout retest']]] as const) {
      fireEvent.change(screen.getByLabelText('Participant group'), { target: { value: group } });
      expect(screen.getByText('2 participants')).toBeTruthy();
      for (let i = 1; i <= 2; i++) fireEvent.click(screen.getByText(`bot:BX:${group}:${i}`));
      for (const mode of modes) expect(screen.getByText(mode)).toBeTruthy();
      expect(screen.getAllByText('Watched zone / level')).toHaveLength(2); expect(screen.getAllByText('Decision')).toHaveLength(2);
      fireEvent.change(screen.getByLabelText('Chart timeframe'), { target: { value: '1d' } });
      expect(viewRuntime.current!.getSnapshot().session.market).toBe(market);
    }
  });
  it('keeps running off-page and restores the paused selected chart after refresh', () => {
    const view = render(<SimulationPage />); view.unmount(); act(() => vi.advanceTimersByTime(1000)); render(<SimulationPage />);
    expect(screen.getByLabelText('Simulation elapsed time').textContent).toBe('00:30:18.00'); fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    fireEvent.change(screen.getByLabelText('Simulation instrument'), { target: { value: 'BG' } }); fireEvent.change(screen.getByLabelText('Chart timeframe'), { target: { value: '1d' } });
    const saved = viewRuntime.current!.getSnapshot().session; cleanup(); viewRuntime.current!.dispose();
    viewRuntime.current = createSimulationRuntime({ storage: localStorage }).api; render(<SimulationPage />);
    expect(viewRuntime.current.getSnapshot().session).toEqual(saved); expect(screen.getByRole('button', { name: 'Resume' })).toBeTruthy();
    expect(screen.getByLabelText('Simulation instrument')).toHaveProperty('value', 'BG'); expect(screen.getByLabelText('Chart timeframe')).toHaveProperty('value', '1d');
  });
});


it('keeps chart, book, tape and participant inspection on one selected snapshot', () => {
  render(<SimulationPage />); fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
  fireEvent.change(screen.getByLabelText('Simulation instrument'), { target: { value: 'BG' } });
  const market = viewRuntime.current!.getSnapshot().session.market, last = market.trades.filter(t => t.contract === 'BG').at(-1)!;
  expect(screen.getByRole('img').getAttribute('aria-label')).toContain((last.ticks * .1).toFixed(2));
  fireEvent.click(screen.getByRole('button', { name: 'Activity' }));
  const tape = screen.getByRole('region', { name: 'BG time and sales' });
  expect(tape.querySelector('tbody tr')?.textContent).toContain((last.ticks * .1).toFixed(2));
  const checkLatest = (side?: number) => {
    const current = viewRuntime.current!.getSnapshot().session.market;
    const expected = current.trades.filter(t => t.contract === 'BG' && (side === undefined || t.direction === side)).slice(-8).reverse();
    expect(Array.from(tape.querySelectorAll('tbody tr'), row => Array.from(row.querySelectorAll('td'), cell => cell.textContent))).toEqual(expected.map(t => [
      `${simulationTime(t.time)}.${String(Math.round(t.time % 1 * 100)).padStart(2, '0')}`, t.contract, (t.ticks * .1).toFixed(2), String(t.quantity), t.direction === 1 ? 'Buy' : 'Sell',
    ]));
  };
  checkLatest();
  fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
  act(() => vi.advanceTimersByTime(1000));
  expect(viewRuntime.current!.getSnapshot().session.market.trades.at(-1)?.id).not.toBe(market.trades.at(-1)?.id);
  checkLatest();
  fireEvent.change(screen.getByLabelText('Tape aggressor'), { target: { value: '1' } }); checkLatest(1);
  expect(screen.queryByRole('button', { name: 'Freeze tape' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Older' })).toBeNull();
  fireEvent.click(within(screen.getByRole('navigation', { name: 'Market panels' })).getByRole('button', { name: 'Participants' }));
  fireEvent.click(screen.getByText('Inspect individual participants · BG'));
  fireEvent.change(screen.getByLabelText('Search participant ID'), { target: { value: market.participants.find(p => p.key === 'BG')!.id } });
  expect(screen.getByText('1 participants')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Book' }));
  expect(screen.getByRole('complementary', { name: 'BG simulation L2 order book' })).toBeTruthy();
});
it('preserves panel preferences, cancels reset, and exits expansion with Escape', () => {
  const { unmount } = render(<SimulationPage />); fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
  const initial = viewRuntime.current!.getSnapshot().session.market;
  fireEvent.click(screen.getByRole('button', { name: 'Reset' })); fireEvent.click(screen.getByRole('button', { name: 'Cancel reset' }));
  expect(viewRuntime.current!.getSnapshot().session.market).toBe(initial);
  fireEvent.click(screen.getByRole('button', { name: 'Collapse side panel' })); unmount(); render(<SimulationPage />);
  expect(screen.queryByRole('complementary')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Expand workspace' })); expect(screen.getByRole('dialog')).toBeTruthy();
  fireEvent.keyDown(window, { key: 'Escape' }); expect(screen.queryByRole('dialog')).toBeNull();
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Expand workspace' }));
});

it('validates file replacement before committing and leaves a bad file harmless', async () => {
  render(<SimulationPage />); fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
  fireEvent.click(screen.getByRole('button', { name: 'Advanced settings' }));
  const current = viewRuntime.current!, original = current.getSnapshot().session.market, raw = current.exportCheckpoint();
  const file = (value: string) => ({ size: value.length, text: async () => value });
  await act(async () => fireEvent.change(screen.getByLabelText('Import checkpoint file'), { target: { files: [file('{}')] } }));
  expect(within(screen.getByRole('dialog', { name: 'Advanced settings' })).getByText('Invalid or incompatible checkpoint. Current run unchanged.')).toBeTruthy(); expect(current.getSnapshot().session.market).toBe(original);
  fireEvent.click(screen.getByRole('button', { name: 'Step 0.25s' }));
  await act(async () => fireEvent.change(screen.getByLabelText('Import checkpoint file'), { target: { files: [file(raw)] } }));
  expect(screen.getByRole('alertdialog', { name: 'Import replacement checkpoint' })).toBeTruthy();
  expect(current.getSnapshot().session.market.step).toBe(original.step + 1);
  fireEvent.click(screen.getByRole('button', { name: 'Import and replace run' })); expect(current.getSnapshot().session.market).toEqual(original);
});


it('keeps experiments and the chart available while hiding technical settings by default', () => {
  render(<SimulationPage />);
  fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
  expect(screen.getByRole('img', { name: /candlestick chart/ })).toBeTruthy();
  expect(screen.getByRole('region', { name: 'Market participants' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Reset' })).toBeTruthy();
  expect(screen.getByLabelText('Simulation speed')).toBeTruthy();
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(screen.queryByLabelText('Random seed')).toBeNull();
  expect(screen.queryByRole('region', { name: 'Detailed statistics' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Stats' }));
  expect(screen.getByRole('region', { name: 'Detailed statistics' })).toBeTruthy();
  expect(screen.getByRole('img', { name: /candlestick chart/ })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Advanced settings' }));
  const drawer = screen.getByRole('dialog', { name: 'Advanced settings' });
  fireEvent.change(within(drawer).getByLabelText('News frequency (×)'), { target: { value: '0.5' } });
  fireEvent(drawer, new Event('cancel', { cancelable: true }));
  expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Advanced settings' }));
  expect(within(screen.getByRole('dialog', { name: 'Advanced settings' })).getByLabelText('News frequency (×)')).toHaveProperty('value', '0.5');
  fireEvent.click(screen.getByRole('button', { name: 'Close advanced settings' }));
  fireEvent.click(within(screen.getByRole('navigation', { name: 'Market panels' })).getByRole('button', { name: 'Participants' }));
  const chart = screen.getByRole('img', { name: /candlestick chart/ });
  const before = viewRuntime.current!.getSnapshot().session.market;
  fireEvent.change(screen.getByLabelText('Maker liquidity (×)'), { target: { value: '0.6' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply changes' }));
  const after = viewRuntime.current!.getSnapshot().session.market;
  expect(after.config.liquidity).toBe(0.6);
  expect(after.config.newsFrequency).toBe(before.config.newsFrequency); // Hidden drafts are not applied by participant controls.
  expect(after.books).toBe(before.books);
  expect(after.accounts).toBe(before.accounts);
  expect(screen.getByRole('img', { name: /candlestick chart/ })).toBe(chart);
});
