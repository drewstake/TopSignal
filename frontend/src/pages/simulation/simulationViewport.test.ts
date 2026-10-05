import { describe, expect, it } from 'vitest';
import { anchorView, boundStart, defaultView, MAX_SPACING, MIN_SPACING, viewStart, wheelPixels, zoomView } from './simulationViewport';
import { movingAverage, retainedVwap } from './simulationIndicators';
import type { Execution, SimulationCandle } from './simulationModel';
const bars: SimulationCandle[] = Array.from({ length: 1200 }, (_, i) => ({ time: i * 60, open: 10, high: 12, low: 8, close: 10 + i, volume: 2 }));
describe('Time-anchored viewport', () => {
  it('keeps the fractional candle under the pointer fixed and enforces zoom bounds', () => {
    const width = 800, pointer = 327;
    let view = anchorView(defaultView(), bars, 400.3, width);
    const anchor = viewStart(view, bars, width) + pointer / view.spacing;
    for (const factor of [1.3, .3, 20, .4, 1.7]) {
      view = zoomView(view, bars, width, pointer, factor);
      expect(viewStart(view, bars, width) + pointer / view.spacing).toBeCloseTo(anchor, 8);
    }
    expect(zoomView(view, bars, width, pointer, 1e8).spacing).toBe(MAX_SPACING);
    expect(zoomView(view, bars, width, pointer, 1e-8).spacing).toBe(MIN_SPACING);
  });
  it('normalizes pixels, lines, pages and bounds extreme deltas', () => {
    expect(wheelPixels(-48, 0, 440)).toBe(-48);
    expect(wheelPixels(-3, 1, 440)).toBe(-48);
    expect(wheelPixels(-1, 2, 440)).toBe(-440);
    expect(wheelPixels(1e9, 0, 440)).toBe(600);
  });
  it('holds historical candles through appends, eviction and resize, independently of price scaling', () => {
    const view = anchorView({ ...defaultView(), auto: false }, bars, 200.25, 800);
    const target = bars[230].time;
    const position = (series: SimulationCandle[], width: number) => (series.findIndex(b => b.time === target) - viewStart(view, series, width)) * view.spacing;
    expect(position(bars.slice(50), 450)).toBe(position(bars, 800));
    expect(position([...bars, { ...bars[0], time: 72000 }], 1200)).toBe(position(bars, 800));
    const following = { ...view, follow: true };
    expect(viewStart(following, bars, 800)).toBeGreaterThan(viewStart(view, bars, 800));
    expect(following.auto).toBe(false);
  });
  it('always retains an intersecting candle at both dataset boundaries', () => {
    for (const count of [1, 3, 1200]) for (const spacing of [MIN_SPACING, 12, MAX_SPACING]) for (const request of [-1e12, 1e12]) {
      const start = boundStart(request, count, 800 / spacing);
      expect(Math.ceil(start + 800 / spacing)).toBeGreaterThan(0);
      expect(Math.floor(start)).toBeLessThan(count);
    }
  });
});
describe('Available-history indicators', () => {
  it('requires the full actual-candle lookback without fabricating empty buckets', () => {
    const series = [bars[0], bars[1], { ...bars[2], time: 3600 }];
    expect(movingAverage(series, 2)).toEqual([null, 10.5, 11.5]);
    expect(movingAverage(series, 5)).toEqual([null, null, null]);
    expect(movingAverage(series, 0)).toEqual([null, null, null]);
  });
  it('uses actual retained execution quantities and instrument ticks, not candle approximations', () => {
    const trade = (time: number, ticks: number, quantity: number, contract = 'BX'): Execution => ({ id: time, time, ticks, quantity, contract, step: time * 4, buyer: 'a', seller: 'b', direction: 1 } as Execution);
    const tape = [trade(65, 40, 1), trade(70, 80, 9, 'BG'), trade(125, 48, 3)];
    expect(retainedVwap(bars.slice(0, 3), tape, 'BX', .25, 60)).toEqual([null, 10, 11.5]);
    expect(retainedVwap(bars.slice(0, 3), [], 'BX', .25, 60)).toEqual([null, null, null]);
  });
});
