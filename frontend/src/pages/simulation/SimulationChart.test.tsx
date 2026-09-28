// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SimulationChart } from './SimulationChart';
import { smoothDisplayPrice } from './simulationMotion';
import type { SimulationCandle } from './simulationPreview';

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const candle = (update: number, close: number): SimulationCandle => ({ update, open: 5, high: Math.max(5, close), low: Math.min(5, close), close, volume: 10 });

describe('Live candle smoothing', () => {
  it('damps rapid bid/ask reversals while following sustained moves without overshooting', () => {
    let displayed = 5;
    const trace: number[] = [];
    for (let i = 0; i < 120; i += 1) {
      displayed = smoothDisplayPrice(displayed, i % 2 ? 5.01 : 4.99, 1000 / 60);
      trace.push(displayed);
    }
    expect(Math.max(...trace) - Math.min(...trace)).toBeLessThan(0.003);
    const smallMove = smoothDisplayPrice(5, 5.01, 16);
    const largeMove = smoothDisplayPrice(5, 5.1, 16);
    expect(largeMove - 5).toBeGreaterThan(smallMove - 5);
    for (let i = 0; i < 120; i += 1) {
      const next = smoothDisplayPrice(displayed, 5.1, 1000 / 60);
      expect(next).toBeGreaterThanOrEqual(displayed);
      expect(next).toBeLessThanOrEqual(5.1);
      displayed = next;
    }
    expect(displayed).toBeCloseTo(5.1, 3);
  });

  it('gives the same motion over equal elapsed time at different refresh rates', () => {
    const run = (frames: number) => {
      let price = 5;
      for (let i = 0; i < frames; i += 1) price = smoothDisplayPrice(price, 5.1, 500 / frames);
      return price;
    };
    expect(run(30)).toBeCloseTo(run(60), 10);
  });

  it('smooths only the live body, keeps actual OHLC, finalizes closed candles, and clears animation on unmount', () => {
    const original = candle(1, 5);
    const { container, rerender } = render(<SimulationChart candles={[original]} />);
    const updated = candle(1, 5.1);
    const snapshot = structuredClone(updated);
    rerender(<SimulationChart candles={[updated]} />);
    act(() => vi.advanceTimersByTime(80));
    const shown = Number(container.querySelector('[data-candle="1"]')!.getAttribute('data-display-close'));
    expect(shown).toBeGreaterThan(5);
    expect(shown).toBeLessThan(5.1);
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('Last trade price $5.10');
    expect(container.querySelector('.simulation-ohlc')!.textContent).toContain('C 5.10');
    expect(updated).toEqual(snapshot);
    rerender(<SimulationChart candles={[updated, { ...candle(2, 5.12), open: 5.1, low: 5.1 }]} />);
    expect(Number(container.querySelector('[data-candle="1"]')!.getAttribute('data-display-close'))).toBe(5.1);
    expect(Number(container.querySelector('[data-candle="2"]')!.getAttribute('data-display-close'))).toBe(5.1);
    act(() => vi.advanceTimersByTime(80));
    const newShown = Number(container.querySelector('[data-candle="2"]')!.getAttribute('data-display-close'));
    expect(newShown).toBeGreaterThan(5.1);
    expect(newShown).toBeLessThan(5.12);
    cleanup();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('shows the actual close immediately for reduced motion', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    const { container, rerender } = render(<SimulationChart candles={[candle(1, 5)]} />);
    rerender(<SimulationChart candles={[candle(1, 5.1)]} />);
    expect(Number(container.querySelector('[data-candle="1"]')!.getAttribute('data-display-close'))).toBe(5.1);
  });
});
