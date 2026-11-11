import type { Tier } from "./heatmapHistory";

/**
 * Time-follow and price-follow are independent:
 * - `end === null` keeps the right edge at "now" (live). Any time pan/zoom away
 *   from now freezes the window on the inspected interval.
 * - `priceFollow` recenters only when the market leaves the central band, so the
 *   vertical range is stable during normal quote flicker. Manual price pan or
 *   zoom turns it off until Recenter is pressed.
 */
export interface HeatmapViewport {
  span: number;
  end: number | null;
  centerTick: number | null;
  rangeTicks: number;
  priceFollow: boolean;
}

export const TIME_SPANS = [60_000, 180_000, 300_000, 900_000, 1_800_000, 3_600_000, 4 * 3_600_000] as const;
export const PRICE_RANGES = [40, 80, 160, 320, 640] as const;
export const MIN_SPAN = 30_000;
export const MAX_SPAN = 24 * 3_600_000;
export const MIN_RANGE = 16;
export const MAX_RANGE = 8_000;
/** Recenter when the mid leaves the middle 60% of the visible price range. */
export const FOLLOW_BAND = 0.3;

export const defaultViewport = (): HeatmapViewport => ({ span: 300_000, end: null, centerTick: null, rangeTicks: 160, priceFollow: true });

export function chooseTier(span: number): Tier {
  return span <= 10 * 60_000 ? 0 : span <= 3 * 3_600_000 ? 1 : 2;
}

export function viewEnd(view: HeatmapViewport, now: number): number {
  return view.end ?? now;
}

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

/** Keep a manual window inside the recorded session; reaching "now" resumes live follow. */
export function clampTime(view: HeatmapViewport, now: number, sessionStart: number | null): HeatmapViewport {
  const span = clamp(view.span, MIN_SPAN, MAX_SPAN);
  if (view.end === null) return { ...view, span };
  const earliest = sessionStart === null ? -Infinity : sessionStart + Math.min(span, Math.max(0, now - sessionStart));
  const end = Math.max(earliest, view.end);
  return end >= now - 250 ? { ...view, span, end: null } : { ...view, span, end };
}

export function panTime(view: HeatmapViewport, deltaMs: number, now: number, sessionStart: number | null): HeatmapViewport {
  return clampTime({ ...view, end: viewEnd(view, now) + deltaMs }, now, sessionStart);
}

/** Zoom around an anchor time. While following live the right edge stays at now. */
export function zoomTime(view: HeatmapViewport, factor: number, anchor: number | null, now: number, sessionStart: number | null): HeatmapViewport {
  const span = clamp(view.span * factor, MIN_SPAN, MAX_SPAN);
  if (view.end === null || anchor === null) return clampTime({ ...view, span }, now, sessionStart);
  const end = viewEnd(view, now);
  const ratio = (end - anchor) / view.span;
  return clampTime({ ...view, span, end: anchor + ratio * span }, now, sessionStart);
}

export function panPrice(view: HeatmapViewport, deltaTicks: number): HeatmapViewport {
  if (view.centerTick === null) return view;
  return { ...view, centerTick: view.centerTick + deltaTicks, priceFollow: false };
}

export function zoomPrice(view: HeatmapViewport, factor: number, anchorTick: number | null): HeatmapViewport {
  const rangeTicks = Math.round(clamp(view.rangeTicks * factor, MIN_RANGE, MAX_RANGE));
  if (view.centerTick === null) return { ...view, rangeTicks };
  const anchor = anchorTick ?? view.centerTick;
  const centerTick = anchor + (view.centerTick - anchor) * rangeTicks / view.rangeTicks;
  return { ...view, rangeTicks, centerTick, priceFollow: false };
}

/** Recenter only when needed, so the axis does not move with every quote. */
export function followPrice(view: HeatmapViewport, midTick: number | null): HeatmapViewport {
  if (midTick === null || !Number.isFinite(midTick)) return view;
  if (view.centerTick === null) return { ...view, centerTick: Math.round(midTick) };
  if (!view.priceFollow) return view;
  return Math.abs(midTick - view.centerTick) > view.rangeTicks * FOLLOW_BAND ? { ...view, centerTick: Math.round(midTick) } : view;
}

export function recenter(view: HeatmapViewport, midTick: number | null): HeatmapViewport {
  return midTick === null ? { ...view, priceFollow: true } : { ...view, centerTick: Math.round(midTick), priceFollow: true };
}

export const formatSpan = (span: number) => span >= 3_600_000 ? `${+(span / 3_600_000).toFixed(1)} h`
  : span >= 60_000 ? `${+(span / 60_000).toFixed(1)} min` : `${Math.round(span / 1000)} s`;

const NICE = [1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000];
/** Automatic saturation: a rounded high percentile of visible sizes, so one block order cannot wash out the map. */
export function niceSaturation(values: ArrayLike<number>, percentile = 0.97): number {
  const sample: number[] = [];
  const step = Math.max(1, Math.floor(values.length / 20_000));
  for (let i = 0; i < values.length; i += step) if (values[i] > 0) sample.push(values[i]);
  if (!sample.length) return 25;
  sample.sort((a, b) => a - b);
  const target = Math.max(5, sample[Math.min(sample.length - 1, Math.floor(sample.length * percentile))]);
  return NICE.find(value => value >= target) ?? 5000;
}

/** Fit the price range to an observed quote extent (explicit user action only, e.g. a new time window). */
export function fitPrice(view: HeatmapViewport, extent: { low: number; high: number } | null, midTick: number | null = null): HeatmapViewport {
  if (!extent) return view;
  // Room for the nearest observed book on both sides, and wide enough that the current
  // price sits inside the follow band, so auto-recentering does not undo the fit.
  const center = (extent.low + extent.high) / 2;
  const needed = Math.max((extent.high - extent.low) + 60, midTick === null ? 0 : Math.abs(midTick - center) / FOLLOW_BAND + 20);
  const preset = PRICE_RANGES.find(ticks => ticks >= needed);
  const rangeTicks = preset ?? Math.min(MAX_RANGE, Math.ceil(needed / 100) * 100);
  return { ...view, rangeTicks: Math.max(view.rangeTicks, rangeTicks), centerTick: Math.round(center) };
}
