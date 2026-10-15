import { describe, expect, it } from 'vitest';
import { fitPriceRange, formatChartPrice, panPriceView, priceScale, scalePriceView } from './simulationPriceScale';
import { defaultView } from './simulationViewport';
import { inspectCursor } from './simulationChartCursor';

const bar = (low: number, high: number) => ({ time: 0, open: low, low, high, close: high, volume: 1 });
describe('Shared price coordinates', () => {
  it('round-trips prices and pixels in both modes, including offscreen values', () => {
    for (const log of [false, true]) {
      const scale = priceScale({ minimum: 1, maximum: 1000, log }, 24, 740);
      for (const price of [.1, 1, 2, 10, 100, 1000, 2000]) expect(scale.price(scale.y(price))).toBeCloseTo(price, 8);
      for (const pixel of [-20, 24, 123.4, 600, 740, 800]) expect(scale.y(scale.price(pixel))).toBeCloseTo(pixel, 8);
    }
    const logarithmic = priceScale({ minimum: 1, maximum: 1000, log: true }, 0, 300);
    expect(logarithmic.y(10) - logarithmic.y(100)).toBeCloseTo(logarithmic.y(100) - logarithmic.y(1000));
    expect(logarithmic.price(150)).toBeCloseTo(Math.sqrt(1000));
  });
  it('fits only actual visible candles, pads flat and sparse data, and never logs zero', () => {
    for (const log of [false, true]) for (const bars of [[], [bar(10, 10)], [bar(1, 1000)]]) {
      const fitted = fitPriceRange(bars, .25, log, 10);
      expect(fitted.maximum).toBeGreaterThan(fitted.minimum);
      for (const b of bars) { expect(fitted.minimum).toBeLessThan(b.low); expect(fitted.maximum).toBeGreaterThan(b.high); }
      if (log) expect(fitted.minimum).toBeGreaterThan(0);
    }
    const mixed = fitPriceRange([bar(-5, 10), bar(10, 20), bar(0, 1000)], .25, true);
    expect(mixed).toEqual(fitPriceRange([bar(10, 20)], .25, true));
    const scale = priceScale(mixed, 0, 300);
    for (const value of [0, -1, Infinity, NaN]) { expect(scale.valid(value)).toBe(false); expect(scale.y(value)).toBeNaN(); }
    expect(formatChartPrice(.000001)).not.toBe('0.00');
  });
  it('keeps the pointer price fixed during scaling and pans by pixels in either mode', () => {
    for (const log of [false, true]) {
      const view = { ...defaultView(), minimum: 10, maximum: 1000, log };
      const before = priceScale(view, 0, 400), anchor = .73;
      const zoom = scalePriceView(view, .4, .25, anchor);
      expect(priceScale(zoom, 0, 400).price(400 * (1 - anchor))).toBeCloseTo(before.price(400 * (1 - anchor)), 8);
      const pan = panPriceView(view, .15);
      expect(priceScale(pan, 0, 400).y(50)).toBeCloseTo(before.y(50) + 60);
      expect(zoom.follow).toBe(view.follow); expect(pan.follow).toBe(view.follow);
      expect(zoom.auto).toBe(false);
    }
    for (const factor of [1e-100, 1e100]) {
      const view = scalePriceView({ ...defaultView(), minimum: 1, maximum: 100, log: true }, factor, .25);
      const pan = panPriceView(view, 1e100);
      expect(pan.minimum).toBeGreaterThan(0); expect(pan.maximum).toBeGreaterThan(pan.minimum);
      expect(Number.isFinite(pan.maximum)).toBe(true);
    }
  });
  it('uses inverse log coordinates for crosshair labels and exact closes for keyboard inspection', () => {
    const candle = bar(10, 100);
    const view = { visible: [candle], last: candle, firstVisible: 0, fallbackIndex: 0, start: 0, spacing: 12,
      minimum: 1, maximum: 1000, log: true, plotLeft: 14, plotRight: 800, plotTop: 0, plotBottom: 300, height: 333 };
    expect(inspectCursor({ pointer: { x: 20, y: 150 }, time: null }, view).price).toBeCloseTo(Math.sqrt(1000));
    const keyboard = inspectCursor({ pointer: null, time: 0 }, view);
    expect(keyboard.price).toBeCloseTo(candle.close); expect(keyboard.y).toBeCloseTo(100);
  });
});
