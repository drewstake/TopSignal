import { describe, expect, it } from 'vitest';
import { SimulationClock, initialView, publishView } from './runtime';
import { createSimulation, stepSimulation } from './participants';
import { fitPriceRange, priceScale } from '../simulationPriceScale';
import { defaultView, viewStart, zoomView } from '../simulationViewport';

describe('Chart-independent funded playback', () => {
  it('preserves every engine step and decision across different clock batches and changing views', () => {
    const base = createSimulation(73521, 100);
    const expected = structuredClone(base);
    for (let i = 0; i < 288; i++) stepSimulation(expected);
    for (const batch of [25, 100]) {
      const state = structuredClone(base), clock = new SimulationClock();
      let view = defaultView();
      // The funded engine always runs at 18 simulated seconds per wall second.
      // Larger timer batches must postpone work without dropping any steps.
      for (let ms = 0; ms < 4000; ms += batch) {
        clock.accrue(batch);
        while (clock.backlog >= .25 - 1e-10) clock.run(() => stepSimulation(state), () => 0);
        for (const symbol of ['BX', 'BT', 'BG'] as const) {
          const bars = state.books[symbol].candles['1m'];
          view = zoomView(view, bars, 800, 400, ms % 50 ? .8 : 1.25);
          viewStart(view, bars, 800);
          for (const log of [false, true]) priceScale(fitPriceRange(bars, .25, log), 24, 400).price(200);
        }
        publishView(state, initialView(73521));
      }
      expect(state).toEqual(expected);
      expect(clock.backlog).toBeCloseTo(0);
    }
  }, 20000);
  it('keeps fractional debt when execution exceeds a frame budget', () => {
    const clock = new SimulationClock();
    clock.accrue(13); clock.accrue(17);
    let steps = 0, wall = 0;
    clock.run(() => { steps++; wall += 9; }, () => wall);
    expect(steps).toBe(1);
    expect(clock.backlog).toBeCloseTo(.03 * 18 - .25);
    clock.run(() => steps++, () => 0);
    expect(steps).toBe(2); expect(clock.backlog).toBeCloseTo(.04);
  });
});
