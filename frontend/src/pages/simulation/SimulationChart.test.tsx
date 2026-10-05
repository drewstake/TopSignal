// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SimulationChart } from './SimulationChart';

import { createSimulation, type SimulationCandle } from './simulationPreview';
import { createSimulationRuntime } from './simulationRuntime';

beforeEach(() => {
  vi.useFakeTimers(); localStorage.clear();
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const candle = (update: number, close: number): SimulationCandle => ({ time: update, open: 5, high: Math.max(5, close), low: Math.min(5, close), close, volume: 10 });

describe('Simulation crosshair', () => {
  function hover(chart: HTMLElement, clientX: number, clientY: number) {
    fireEvent(chart, new MouseEvent('pointermove', { bubbles: true, clientX, clientY }));
  }

  it('updates hover immediately without rerendering the market chart or rebuilding its wheel listener', () => {
    const { api } = createSimulationRuntime({ storage: null, session: {
      market: createSimulation(73521, undefined, 500), running: false, speed: 1,
      generation: 0, instrument: 'BX', timeframe: '1m', fractionalSteps: 0,
    } });
    const getSnapshot = vi.fn(api.getSnapshot);
    const { container } = render(<SimulationChart runtime={{ ...api, getSnapshot }} />);
    try {
      const chart = screen.getByRole('img');
      vi.spyOn(chart, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 960, height: 440 } as DOMRect);
      const addListener = vi.spyOn(chart, 'addEventListener');
      getSnapshot.mockClear();
      for (let i = 0; i < 100; i++) {
        hover(chart, 20 + i * 8, 100 + i);
        const cursor = container.querySelector('.simulation-crosshair circle');
        expect(cursor?.getAttribute('cx')).toBe(String(20 + i * 8));
        expect(cursor?.getAttribute('cy')).toBe(String(100 + i));
      }
      expect(getSnapshot).not.toHaveBeenCalled();
      expect(addListener).not.toHaveBeenCalled();
      fireEvent.pointerLeave(chart);
      expect(container.querySelector('.simulation-crosshair')).toBeNull();
    } finally { api.dispose(); }
  });

  it('follows the pointer past the latest candle without inventing candle data', () => {
    const bars = [candle(0, 4.9), candle(60, 5), candle(120, 5.1)];
    const { container, rerender } = render(<SimulationChart candles={bars} />);
    const chart = screen.getByRole('img');
    vi.spyOn(chart, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 960, height: 440 } as DOMRect);
    for (const clientX of [60, 400, 876]) {
      hover(chart, clientX, 150);
      const crosshair = container.querySelector('.simulation-crosshair')!;
      expect(crosshair.querySelector('circle')?.getAttribute('cx')).toBe(String(clientX));
      expect(crosshair.querySelector('line')?.getAttribute('x1')).toBe(String(clientX));
      expect(crosshair.querySelector('circle')?.getAttribute('cy')).toBe('150');
      expect(crosshair.querySelectorAll('text')).toHaveLength(1); // Price only; no fabricated candle time.
      expect(screen.getByRole('status').textContent).toContain('No candle at cursor.');
    }
    rerender(<SimulationChart candles={[...bars, candle(180, 6)]} />);
    expect(container.querySelector('.simulation-crosshair circle')?.getAttribute('cx')).toBe('876');
    expect(container.querySelectorAll('[data-candle]')).toHaveLength(4);
    expect(container.querySelector('.simulation-ohlc')?.textContent).toContain('C 6.00');
    fireEvent.keyDown(chart, { key: 'ArrowLeft' });
    expect(screen.getByRole('status').textContent).toContain('Sim 00:02:00.');
    hover(chart, 44, 150);
    expect(screen.getByRole('status').textContent).toContain('Sim 00:02:00.');
    expect(container.querySelectorAll('.simulation-crosshair text')).toHaveLength(2);
    hover(chart, 877, 150);
    expect(container.querySelector('.simulation-crosshair')).toBeNull();
  });

  it('tracks whitespace on both sides of retained candles after panning and letterboxing', () => {
    localStorage.setItem('topsignal.simulation.workspace.v1.chart.BX.1m', JSON.stringify({ time: 0, offset: -10, spacing: 12, follow: false, auto: true, minimum: 4, maximum: 6 }));
    const { container } = render(<SimulationChart candles={[candle(0, 5), candle(60, 5.1)]} />);
    const chart = screen.getByRole('img');
    vi.spyOn(chart, 'getBoundingClientRect').mockReturnValue({ left: 100, top: 50, width: 480, height: 440 } as DOMRect);
    for (const [clientX, plotX] of [[120, 40], [300, 400]]) {
      hover(chart, clientX, 210);
      expect(container.querySelector('.simulation-crosshair circle')?.getAttribute('cx')).toBe(String(plotX));
      expect(container.querySelector('.simulation-crosshair circle')?.getAttribute('cy')).toBe('100');
      expect(screen.getByRole('status').textContent).toContain('No candle at cursor.');
    }
    hover(chart, 170, 210); // First real candle remains inspectable between the blank areas.
    expect(screen.getByRole('status').textContent).toContain('Sim 00:00:00.');
  });

  it('keeps hover height fixed through live close changes, rescaling, and new candles', () => {
    const bars = [candle(1, 4.9), candle(2, 5), candle(3, 5.1)];
    const { container, rerender } = render(<SimulationChart candles={bars} />);
    const chart = screen.getByRole('img');
    vi.spyOn(chart, 'getBoundingClientRect').mockReturnValue({ left: 100, top: 50, width: 480, height: 220 } as DOMRect);
    hover(chart, 122, 100);
    const height = () => container.querySelector('.simulation-crosshair circle')?.getAttribute('cy');
    expect(height()).toBe('100');
    for (const close of [4.8, 5.3, 4.7, 5.4]) {
      rerender(<SimulationChart candles={[...bars.slice(0, 2), candle(3, close)]} />);
      act(() => vi.advanceTimersByTime(80));
      expect(height()).toBe('100');
    }
    rerender(<SimulationChart candles={[...bars, candle(4, 6)]} />);
    expect(height()).toBe('100');
    hover(chart, 122, 130);
    expect(height()).toBe('160');
  });

  it('accounts for viewBox letterboxing when locating the pointer', () => {
    const { container } = render(<SimulationChart candles={[candle(1, 5)]} />);
    const chart = screen.getByRole('img');
    vi.spyOn(chart, 'getBoundingClientRect').mockReturnValue({ left: 100, top: 50, width: 480, height: 440 } as DOMRect);
    hover(chart, 110, 210); // 110px vertical letterbox + 50px at half scale.
    expect(container.querySelector('.simulation-crosshair circle')?.getAttribute('cy')).toBe('100');
  });

  it('preserves keyboard inspection and clears hover on Escape, leave, and blur', () => {
    const { container } = render(<SimulationChart candles={[candle(1, 4.9), candle(2, 5), candle(3, 5.1)]} />);
    const chart = screen.getByRole('img');
    vi.spyOn(chart, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 960, height: 440 } as DOMRect);
    hover(chart, 44, 100);
    fireEvent.keyDown(chart, { key: 'ArrowLeft' });
    expect(screen.getByRole('status').textContent).toContain('Sim 00:00:02.');
    expect(Number(container.querySelector('.simulation-crosshair circle')?.getAttribute('cy'))).toBeCloseTo(215.5);
    fireEvent.keyDown(chart, { key: 'ArrowRight' });
    expect(screen.getByRole('status').textContent).toContain('Sim 00:00:03.');
    for (const dismiss of [() => fireEvent.keyDown(chart, { key: 'Escape' }), () => fireEvent.pointerLeave(chart), () => fireEvent.blur(chart)]) {
      hover(chart, 44, 100);
      dismiss();
      expect(container.querySelector('.simulation-crosshair')).toBeNull();
    }
  });
});


describe('Execution-accurate chart', () => {
  it('updates directly from the shared runtime without replacing chart props or resetting the viewport', () => {
    const { api } = createSimulationRuntime({ storage: null, session: {
      market: createSimulation(73521, undefined, 500), running: false, speed: 1,
      generation: 0, instrument: 'BX', timeframe: '1m', fractionalSteps: 0,
    } });
    try {
      const { container } = render(<SimulationChart runtime={api} />);
      fireEvent.click(screen.getByRole('button', { name: 'Zoom In' }));
      const spacing = screen.getByRole('img').dataset.spacing;
      act(() => api.dispatch({ type: 'step', steps: 32 }));
      const book = api.getSnapshot().session.market.books.BX, active = book.candles['1m'].at(-1)!;
      expect(container.querySelector('.simulation-price-label')?.textContent).toBe((book.last! * .25).toFixed(2));
      expect(container.querySelector(`[data-candle="${active.time}"]`)?.getAttribute('data-display-close')).toBe(String(active.close));
      expect(screen.getByRole('img').dataset.spacing).toBe(spacing);
    } finally { api.dispose(); }
  });
  it('aligns the executed-price line exactly even next to a manual scale boundary', () => {
    localStorage.setItem('topsignal.simulation.workspace.v1.chart.BX.1m', JSON.stringify({ time: 0, offset: 0, spacing: 12, follow: true, auto: false, minimum: 4.01, maximum: 5.01 }));
    const { container } = render(<SimulationChart candles={[candle(0, 5)]} />);
    const transform = (container.querySelector('.simulation-price-marker') as SVGGElement).style.transform;
    expect(Number(transform.match(/translateY\((.*)px\)/)?.[1])).toBeCloseTo(27.83);
    expect(container.querySelector('.simulation-price-marker line')).not.toBeNull();
  });
  it('batches dense retained history into bounded paths while keeping exact candle inspection', () => {
    const bars = Array.from({ length: 1200 }, (_, i) => candle(i * 60, 5 + (i % 3 - 1) * .25));
    const { container } = render(<SimulationChart candles={bars} />);
    fireEvent.click(screen.getByRole('button', { name: 'Fit History' }));
    expect(screen.getByRole('img').getAttribute('data-rendered-candles')).toBe('1200');
    expect(container.querySelectorAll('.simulation-dense-candles')).toHaveLength(3);
    expect(container.querySelectorAll('[data-candle]')).toHaveLength(0);
    expect(container.innerHTML).not.toMatch(/NaN|Infinity/);
    fireEvent.keyDown(screen.getByRole('img'), { key: 'ArrowLeft' });
    expect(screen.getByRole('status').textContent).toContain('Sim 19:58:00.');
    expect(container.querySelector('.simulation-price-label')?.textContent).toBe('5.25');
  });
  it('renders the actual active close immediately and leaves completed candles unchanged', () => {
    const closed = candle(0, 4.9), original = candle(60, 5);
    const { container, rerender } = render(<SimulationChart candles={[closed, original]} />);
    const updated = candle(60, 5.25), snapshot = structuredClone(updated);
    rerender(<SimulationChart candles={[closed, updated]} />);
    expect(container.querySelector('[data-candle="60"]')?.getAttribute('data-display-close')).toBe('5.25');
    expect(container.querySelector('[data-candle="0"]')?.getAttribute('data-display-close')).toBe('4.9');
    expect(container.querySelector('.simulation-price-label')?.textContent).toBe('5.25');
    expect(container.querySelector('.simulation-ohlc')?.textContent).toContain('C 5.25');
    expect(updated).toEqual(snapshot);
  });
  it('uses neutral dojis, green rising candles and red falling candles without a volume panel', () => {
    const { container } = render(<SimulationChart candles={[candle(0, 4.75), candle(60, 5.25), candle(120, 5)]} />);
    expect(container.querySelector('[data-candle="0"]')?.parentElement?.getAttribute('class')).toBe('simulation-down');
    expect(container.querySelector('[data-candle="60"]')?.parentElement?.getAttribute('class')).toBe('simulation-up');
    expect(container.querySelector('[data-candle="120"]')?.parentElement?.getAttribute('class')).toBe('simulation-neutral');
    expect(screen.queryByText('VOLUME')).toBeNull();
    expect(container.querySelector('.simulation-ohlc')?.textContent).not.toContain('Vol');
    expect(screen.getByRole('img').getAttribute('aria-label')).not.toContain('volume');
    expect(container.querySelector('.simulation-price-marker.simulation-neutral')).not.toBeNull();
  });
  it('handles empty and sparse higher-timeframe series with finite geometry and instrument labels', () => {
    const { container, rerender } = render(<SimulationChart candles={[]} symbol="BG" tickSize={.1} />);
    expect(screen.getByRole('status').textContent).toContain('Waiting');
    rerender(<SimulationChart candles={[{ time: 0, open: 2384.6, high: 2384.6, low: 2384.6, close: 2384.6, volume: 2 }]} symbol="BG" tickSize={.1} />);
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('BG');
    expect(container.innerHTML).not.toMatch(/NaN|Infinity/);
    expect(container.querySelector('.simulation-price-label')?.textContent).toBe('2384.60');
  });
});

describe('Chart panning', () => {
  const bars = Array.from({ length: 120 }, (_, index) => candle(index * 60, 6));

  function preparePointer() {
    const chart = screen.getByRole('img');
    vi.spyOn(chart, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 960, height: 440 } as DOMRect);
    let captured: number | null = null;
    const capture = vi.fn((id: number) => { captured = id; });
    const release = vi.fn(() => { captured = null; });
    Object.defineProperties(chart, {
      setPointerCapture: { value: capture },
      hasPointerCapture: { value: (id: number) => captured === id },
      releasePointerCapture: { value: release },
    });
    function pointer(type: string, x: number, y: number, options: { id?: number; primary?: boolean; button?: number; pointerType?: string } = {}) {
      const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: options.button ?? 0 });
      Object.defineProperties(event, {
        pointerId: { value: options.id ?? 1 },
        isPrimary: { value: options.primary ?? true },
        pointerType: { value: options.pointerType ?? 'mouse' },
      });
      fireEvent(chart, event);
    }
    return { chart, pointer, capture, release };
  }

  it('drags both axes smoothly, reveals earlier executions, and leaves candle data unchanged', () => {
    const original = structuredClone(bars);
    const { container } = render(<SimulationChart candles={bars} />);
    const { chart, pointer, capture, release } = preparePointer();
    const body = () => container.querySelector('[data-candle="3000"]')!;
    const originalX = Number(body().getAttribute('x'));
    const originalY = Number(body().getAttribute('y'));
    expect(container.querySelector('[data-candle="2400"]')).toBeNull();
    pointer('pointerdown', 300, 160);
    pointer('pointermove', 425, 190);
    expect(Number(body().getAttribute('x'))).toBeCloseTo(originalX + 125);
    expect(Number(body().getAttribute('y'))).toBeCloseTo(originalY + 30);
    expect(container.querySelector('[data-candle="2400"]')).not.toBeNull();
    expect(chart.classList.contains('is-dragging')).toBe(true);
    expect(capture).toHaveBeenCalledWith(1);
    pointer('pointerup', 425, 190);
    expect(release).toHaveBeenCalledWith(1);
    expect(chart.classList.contains('is-dragging')).toBe(false);
    expect(screen.getByText('Browsing history · Manual price')).toBeTruthy();
    expect(bars).toEqual(original);
  });

  it('holds the viewport through live updates and bounded-history eviction, then resumes following', () => {
    const { container, rerender } = render(<SimulationChart candles={bars} />);
    const { pointer } = preparePointer();
    pointer('pointerdown', 300, 160);
    pointer('pointermove', 420, 160);
    pointer('pointerup', 420, 160);
    const geometry = () => {
      const body = container.querySelector('[data-candle="3000"]')!;
      return [body.getAttribute('x'), body.getAttribute('y')];
    };
    const held = geometry();
    rerender(<SimulationChart candles={[...bars.slice(1), candle(7200, 6.25)]} />);
    expect(geometry()).toEqual(held);
    expect(container.querySelector('[data-candle="7200"]')).toBeNull();
    expect(container.querySelector('.simulation-price-label')?.textContent).toBe('6.25');
    fireEvent.click(screen.getByRole('button', { name: 'Follow Latest' }));
    expect(container.querySelector('[data-candle="7200"]')).not.toBeNull();
    expect(screen.getByText('Following latest · Manual price')).toBeTruthy();
    rerender(<SimulationChart candles={[...bars.slice(2), candle(7200, 6.25), candle(7260, 6.5)]} />);
    expect(container.querySelector('[data-candle="7260"]')).not.toBeNull();
  });

  it('supports keyboard panning and returning to live with Home or double-click', () => {
    const { container } = render(<SimulationChart candles={bars} />);
    const chart = screen.getByRole('img');
    fireEvent.keyDown(chart, { key: 'PageUp' });
    expect(container.querySelector('[data-candle="0"]')).not.toBeNull();
    expect(container.querySelector('[data-candle="7140"]')).toBeNull();
    fireEvent.keyDown(chart, { key: 'ArrowLeft' });
    expect(screen.getByRole('status').textContent).toContain('Open 5.00');
    fireEvent.keyDown(chart, { key: 'PageDown' });
    expect(container.querySelector('[data-candle="7140"]')).not.toBeNull();
    fireEvent.keyDown(chart, { key: 'Home' });
    expect(screen.getByRole('button', { name: 'Follow Latest' }).getAttribute('aria-pressed')).toBe('true');
    fireEvent.keyDown(chart, { key: 'PageUp' });
    fireEvent.doubleClick(chart);
    expect(screen.getByRole('button', { name: 'Follow Latest' }).getAttribute('aria-pressed')).toBe('true');
  });

  it('handles touch, pointer cancellation, secondary buttons and additional pointers', () => {
    const { container } = render(<SimulationChart candles={bars} />);
    const { chart, pointer, capture } = preparePointer();
    pointer('pointerdown', 300, 160, { button: 2 });
    pointer('pointerdown', 300, 160, { id: 2, primary: false });
    pointer('pointerdown', 5, 160);
    expect(capture).not.toHaveBeenCalled();
    for (const finish of ['pointercancel', 'lostpointercapture']) {
      pointer('pointerdown', 300, 160, { pointerType: 'touch' });
      const originalX = container.querySelector('[data-candle="3000"]')?.getAttribute('x');
      pointer('pointermove', 420, 160, { id: 2, primary: false });
      expect(container.querySelector('[data-candle="3000"]')?.getAttribute('x')).toBe(originalX);
      pointer('pointermove', 420, 160, { pointerType: 'touch' });
      expect(container.querySelector('[data-candle="3000"]')?.getAttribute('x')).not.toBe(originalX);
      pointer(finish, 420, 160);
      expect(chart.classList.contains('is-dragging')).toBe(false);
      const finalX = container.querySelector('[data-candle="3000"]')?.getAttribute('x');
      pointer('pointermove', 500, 160);
      expect(container.querySelector('[data-candle="3000"]')?.getAttribute('x')).toBe(finalX);
    }
  });

  it('uses rendered coordinates when dragging a letterboxed chart and bounds sparse history', () => {
    const { container } = render(<SimulationChart candles={[candle(0, 6)]} />);
    const { chart, pointer } = preparePointer();
    vi.mocked(chart.getBoundingClientRect).mockReturnValue({ left: 100, top: 50, width: 480, height: 440 } as DOMRect);
    const body = () => container.querySelector('[data-candle="0"]')!;
    const initialX = Number(body().getAttribute('x'));
    const initialY = Number(body().getAttribute('y'));
    pointer('pointerdown', 250, 240);
    pointer('pointermove', 280, 260);
    expect(Number(body().getAttribute('x'))).toBeCloseTo(initialX + 60);
    expect(Number(body().getAttribute('y'))).toBeCloseTo(initialY + 40);
    for (const x of [-10000, 10000]) {
      pointer('pointermove', x, 260);
      expect(Number(body().getAttribute('x'))).toBeGreaterThanOrEqual(14);
      expect(Number(body().getAttribute('x'))).toBeLessThan(876);
      expect(container.innerHTML).not.toMatch(/NaN|Infinity/);
    }
    pointer('pointerup', 280, 260);
  });
});


// Gesture integration tests exercise the real non-passive DOM wheel listener.
describe('Chart zoom gestures and restored views', () => {
  const bars = Array.from({ length: 300 }, (_, i) => candle(i * 60, 6 + i % 3));
  function chartBounds() {
    const chart = screen.getByRole('img');
    vi.spyOn(chart, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 960, height: 440 } as DOMRect);
    Object.defineProperties(chart, { setPointerCapture: { value: vi.fn() }, hasPointerCapture: { value: () => true }, releasePointerCapture: { value: vi.fn() } });
    return chart;
  }
  function pointer(chart: HTMLElement, type: string, x: number, y: number, id = 1, touch = false) {
    const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 });
    Object.defineProperties(event, { pointerId: { value: id }, pointerType: { value: touch ? 'touch' : 'mouse' }, isPrimary: { value: id === 1 } });
    fireEvent(chart, event);
  }
  it('anchors wheel zoom, consumes only chart scroll, and removes the listener on unmount', () => {
    const { unmount } = render(<SimulationChart candles={bars} />), chart = chartBounds();
    const anchor = () => Number(chart.dataset.start) + (400 - 14) / Number(chart.dataset.spacing);
    const before = anchor();
    const event = new WheelEvent('wheel', { bubbles: true, cancelable: true, clientX: 400, clientY: 200, deltaY: -3, deltaMode: 1 });
    fireEvent(chart, event); expect(event.defaultPrevented).toBe(true);
    expect(Number(chart.dataset.spacing)).toBeGreaterThan(12); expect(anchor()).toBeCloseTo(before);
    const page = new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 100 });
    fireEvent(document.body, page); expect(page.defaultPrevented).toBe(false);
    const remove = vi.spyOn(chart, 'removeEventListener'); unmount(); expect(remove).toHaveBeenCalledWith('wheel', expect.any(Function));
  });
  it('scales axes, preserves zoom when following, and resets predictably', () => {
    const { container } = render(<SimulationChart candles={bars} />), chart = chartBounds();
    pointer(chart, 'pointerdown', 400, 423); pointer(chart, 'pointermove', 450, 423); pointer(chart, 'pointerup', 450, 423);
    const spacing = Number(chart.dataset.spacing); expect(spacing).toBeGreaterThan(12);
    pointer(chart, 'pointerdown', 920, 150); pointer(chart, 'pointermove', 920, 200); pointer(chart, 'pointerup', 920, 200);
    expect(screen.getByRole('button', { name: 'Auto Price Scale' }).getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(screen.getByRole('button', { name: 'Follow Latest' })); expect(Number(chart.dataset.spacing)).toBe(spacing);
    expect(container.innerHTML).not.toMatch(/NaN|Infinity/);
    fireEvent.click(screen.getByRole('button', { name: 'Reset View' })); expect(Number(chart.dataset.spacing)).toBe(12);
    expect(screen.getByRole('button', { name: 'Auto Price Scale' }).getAttribute('aria-pressed')).toBe('true');
  });
  it('pinches with two captured touches and safely cancels either touch', () => {
    render(<SimulationChart candles={bars} />); const chart = chartBounds();
    pointer(chart, 'pointerdown', 300, 200, 1, true); pointer(chart, 'pointerdown', 500, 200, 2, true);
    pointer(chart, 'pointermove', 600, 200, 2, true); expect(Number(chart.dataset.spacing)).toBeCloseTo(18);
    pointer(chart, 'pointercancel', 600, 200, 2, true); const start = chart.dataset.start;
    pointer(chart, 'pointermove', 200, 200, 1, true); expect(chart.dataset.start).toBe(start);
    pointer(chart, 'pointerup', 200, 200, 1, true); expect(chart.classList.contains('is-dragging')).toBe(false);
  });
  it('restores separate views across instruments and timeframes without altering candles', () => {
    const original = structuredClone(bars), view = render(<SimulationChart key="BX-1m" candles={bars} symbol="BX" timeframe="1m" />);
    const chart = chartBounds(); fireEvent.click(screen.getByRole('button', { name: 'Zoom In' })); const spacing = chart.dataset.spacing;
    view.rerender(<SimulationChart key="BG-1d" candles={bars} symbol="BG" timeframe="1d" />); expect(screen.getByRole('img').dataset.spacing).toBe('12');
    view.rerender(<SimulationChart key="BX-1m" candles={bars} symbol="BX" timeframe="1m" />); expect(screen.getByRole('img').dataset.spacing).toBe(spacing); expect(bars).toEqual(original);
  });
  it('keeps crosshair selection accurate after wheel zoom and container resize', () => {
    let resize: ResizeObserverCallback | undefined;
    vi.stubGlobal('ResizeObserver', class { constructor(callback: ResizeObserverCallback) { resize = callback; } observe() {} disconnect() {} });
    const { container } = render(<SimulationChart candles={bars} />), chart = chartBounds();
    fireEvent.wheel(chart, { clientX: 400, clientY: 150, deltaY: -80 }); pointer(chart, 'pointermove', 400, 150);
    const chosen = screen.getByRole('status').textContent;
    act(() => resize?.([{ contentRect: { width: 700 } } as ResizeObserverEntry], {} as ResizeObserver));
    vi.mocked(chart.getBoundingClientRect).mockReturnValue({ left: 0, top: 0, width: 700, height: 440 } as DOMRect);
    pointer(chart, 'pointermove', 400, 150);
    expect(screen.getByRole('status').textContent).toBe(chosen); expect(container.querySelector('.simulation-crosshair circle')?.getAttribute('cy')).toBe('150');
  });
  it('uses the expanded plot for inspection and panning, then shrinks back to its minimum height', () => {
    let resize: ResizeObserverCallback | undefined;
    vi.stubGlobal('ResizeObserver', class { constructor(callback: ResizeObserverCallback) { resize = callback; } observe() {} disconnect() {} });
    const { container } = render(<SimulationChart candles={bars} />), chart = chartBounds();
    act(() => resize?.([{ contentRect: { width: 960, height: 616 } } as ResizeObserverEntry], {} as ResizeObserver));
    vi.mocked(chart.getBoundingClientRect).mockReturnValue({ left: 0, top: 0, width: 960, height: 616 } as DOMRect);
    expect(chart.getAttribute('viewBox')).toBe('0 0 960 616');
    pointer(chart, 'pointermove', 400, 500);
    expect(container.querySelector('.simulation-crosshair circle')?.getAttribute('cy')).toBe('500');
    pointer(chart, 'pointerdown', 400, 500); pointer(chart, 'pointermove', 440, 520); pointer(chart, 'pointerup', 440, 520);
    expect(chart.dataset.spacing).toBe('12');
    expect(screen.getByText('Browsing history · Manual price')).toBeTruthy();
    act(() => resize?.([{ contentRect: { width: 960, height: 440 } } as ResizeObserverEntry], {} as ResizeObserver));
    expect(chart.getAttribute('viewBox')).toBe('0 0 960 440');
  });
});

it('measures actual candle intervals and reports elapsed time across empty windows', () => {
  render(<SimulationChart candles={[candle(0, 5), candle(60, 6), candle(3600, 7)]} />);
  fireEvent.click(screen.getByRole('button', { name: 'Measure' }));
  const chart = screen.getByRole('img'); fireEvent.keyDown(chart, { key: 'Enter' }); fireEvent.keyDown(chart, { key: 'ArrowLeft' }); fireEvent.keyDown(chart, { key: 'Enter' });
  expect(screen.getByText(/−?Measure: -1.00/).textContent).toContain('-4 ticks · 2 candles (inclusive) · 3540 simulated seconds');
});
it('jumps within retained time, handles empty windows, and rejects future or invalid times', () => {
  render(<SimulationChart candles={[candle(0, 5), candle(3600, 6)]} elapsedTime={3700} />);
  const input = screen.getByLabelText('Jump to simulated seconds');
  fireEvent.change(input, { target: { value: '1800' } }); fireEvent.submit(input.closest('form')!);
  expect(screen.getByText(/Empty window: nearest retained candle shown/)).toBeTruthy();
  for (const value of ['3701', '-1', 'nope']) { fireEvent.change(input, { target: { value } }); fireEvent.submit(input.closest('form')!); expect(screen.getByText('Use retained seconds 0–3700.')).toBeTruthy(); }
});
