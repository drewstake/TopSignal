import type { SimulationCandle } from './simulationModel';

export const MIN_SPACING = 0.5;
export const MAX_SPACING = 80;
export const DEFAULT_SPACING = 12;
export interface ChartView {
  time: number; offset: number; spacing: number; follow: boolean; auto: boolean;
  minimum: number; maximum: number; log?: boolean;
}
export const defaultView = (): ChartView => ({ time: 0, offset: 0, spacing: DEFAULT_SPACING, follow: true, auto: true, minimum: 0, maximum: 1, log: false });
export const bound = (v: number, min: number, max: number) => Math.max(min, Math.min(max, v));
export function wheelPixels(delta: number, mode: number, height: number) {
  return bound(delta * (mode === 1 ? 16 : mode === 2 ? height : 1), -600, 600);
}
// Ordinal slots represent actual candles, including when empty time buckets are absent.
// Store the leftmost candle's time and fractional slot, never a mutable array index.
export function boundStart(start: number, count: number, capacity: number) {
  return bound(start, Math.min(0, 1 - capacity), Math.max(0, count - 1));
}
export function viewStart(view: ChartView, bars: SimulationCandle[], plotWidth: number) {
  const capacity = plotWidth / view.spacing;
  if (view.follow) return Math.max(0, bars.length - Math.floor(capacity));
  const index = bars.findIndex(b => b.time >= view.time);
  return boundStart((index < 0 ? bars.length - 1 : index) + view.offset, bars.length, capacity);
}
export function anchorView(view: ChartView, bars: SimulationCandle[], start: number, plotWidth: number): ChartView {
  const bounded = boundStart(start, bars.length, plotWidth / view.spacing);
  const anchor = bound(Math.floor(bounded), 0, Math.max(0, bars.length - 1));
  return { ...view, time: bars[anchor]?.time ?? 0, offset: bounded - anchor, follow: false };
}
export function zoomView(view: ChartView, bars: SimulationCandle[], plotWidth: number, pointerX: number, factor: number) {
  const spacing = bound(view.spacing * factor, MIN_SPACING, MAX_SPACING);
  const anchor = viewStart(view, bars, plotWidth) + pointerX / view.spacing;
  return anchorView({ ...view, spacing }, bars, anchor - pointerX / spacing, plotWidth);
}
