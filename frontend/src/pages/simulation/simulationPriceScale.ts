import type { SimulationCandle } from './simulationModel';
import { bound, type ChartView } from './simulationViewport';

export type PriceRange = { minimum: number; maximum: number; log?: boolean };
// All layers use this transform. Nonpositive log values have no coordinate; they
// are omitted explicitly, never clamped to an invented positive execution.
export function priceScale(range: PriceRange, top: number, bottom: number) {
  const valid = (price: number) => Number.isFinite(price) && (!range.log || price > 0);
  const transform = (price: number) => range.log ? Math.log(price) : price;
  const inverse = (value: number) => range.log ? Math.exp(value) : value;
  const lo = transform(range.minimum), hi = transform(range.maximum);
  return {
    valid,
    y: (price: number) => valid(price) ? top + (hi - transform(price)) / (hi - lo) * (bottom - top) : NaN,
    price: (y: number) => inverse(hi - (y - top) / (bottom - top) * (hi - lo)),
  };
}

export function fitPriceRange(bars: readonly SimulationCandle[], tick: number, log = false, fallback = tick): PriceRange {
  let low = Infinity, high = -Infinity;
  for (const bar of bars) {
    // A candle spanning zero cannot be represented honestly on a log axis.
    if (![bar.open, bar.high, bar.low, bar.close].every(p => Number.isFinite(p) && (!log || p > 0))) continue;
    low = Math.min(low, bar.low); high = Math.max(high, bar.high);
  }
  if (!Number.isFinite(low)) low = high = Number.isFinite(fallback) && (!log || fallback > 0) ? fallback : Math.max(tick, 1);
  if (log) {
    const lo = Math.log(low), hi = Math.log(high);
    const padding = Math.max((hi - lo) * .16, Math.log1p(Math.max(tick, Number.EPSILON) * 2 / low), 1e-6);
    return { minimum: Math.exp(Math.max(-700, lo - padding)), maximum: Math.exp(Math.min(700, hi + padding)), log };
  }
  const padding = Math.max((high - low) * .16, tick * 2);
  return { minimum: low - padding, maximum: high + padding, log };
}

// Scale/pan in axis coordinates, so ratios are preserved on log charts and
// differences on linear charts. Bounds avoid overflow after extreme gestures.
export function scalePriceView(view: ChartView, factor: number, tick: number, anchor = .5): ChartView {
  const lo = view.log ? Math.log(view.minimum) : view.minimum;
  const hi = view.log ? Math.log(view.maximum) : view.maximum;
  const span = bound((hi - lo) * factor, view.log ? 1e-6 : tick * 2, view.log ? 100 : 1e7);
  const at = lo + (hi - lo) * anchor;
  const minimum = view.log ? bound(at - span * anchor, -700, 700 - span) : at - span * anchor;
  return { ...view, auto: false, minimum: view.log ? Math.exp(minimum) : minimum, maximum: view.log ? Math.exp(minimum + span) : minimum + span };
}
export function panPriceView(view: ChartView, fraction: number): ChartView {
  if (view.log) {
    const span = Math.log(view.maximum) - Math.log(view.minimum);
    const lo = bound(Math.log(view.minimum) + span * fraction, -700, 700 - span);
    return { ...view, auto: false, minimum: Math.exp(lo), maximum: Math.exp(lo + span) };
  }
  const shift = (view.maximum - view.minimum) * fraction;
  return { ...view, auto: false, minimum: view.minimum + shift, maximum: view.maximum + shift };
}

export function formatChartPrice(price: number) {
  if (!Number.isFinite(price)) return '—';
  if (price !== 0 && (Math.abs(price) < .01 || Math.abs(price) >= 1e8)) return price.toExponential(2);
  return price.toFixed(2);
}
