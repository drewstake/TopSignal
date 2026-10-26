export type HistoricalCandle = {
  time: number; end_time: number; open: number; high: number; low: number; close: number;
  volume: number; contract: string; count: number;
};
export type Timeframe = "1m" | "5m" | "4h" | "1d";
export type ChartWindow = {
  symbol: string; source: string; timeframe: Timeframe; coverage_start: string; coverage_end: string;
  start: string; end: string; total: number; aggregate_size: number; truncated: boolean; candles: HistoricalCandle[];
};
export type HistoryRange = { from: number; to: number };

/** Last bar actually intersecting the viewport, found without a full scan. */
export function lastCandleInRange(candles: HistoricalCandle[], range: HistoryRange): HistoricalCandle | undefined {
  let low = 0;
  let high = candles.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (candles[middle].time <= range.to) low = middle + 1;
    else high = middle;
  }
  const bar = candles[low - 1];
  return bar && bar.end_time > range.from ? bar : undefined;
}

/** Restrict network requests to selected history and a bounded exact window. */
export function candleRequestWindow(visible: HistoryRange, bounds: HistoryRange, seconds: number, overview: boolean): HistoryRange | null {
  const clipped = { from: Math.max(bounds.from, visible.from), to: Math.min(bounds.to, visible.to) };
  if (clipped.to <= clipped.from) return null;
  const window = historyWindow(clipped, bounds);
  const maxSpan = 6000 * seconds;
  if (overview || window.to - window.from <= maxSpan) return window;
  const center = (clipped.from + clipped.to) / 2;
  const end = Math.min(bounds.to, Math.max(bounds.from + maxSpan, center + maxSpan / 2));
  return { from: Math.max(bounds.from, end - maxSpan), to: end };
}

/** Preserve fractional bars and empty space outside the data when detail changes. */
export function logicalToTime(candles: HistoricalCandle[], logical: number): number {
  const index = Math.max(0, Math.min(candles.length - 2, Math.floor(logical)));
  const left = candles[index];
  const right = candles[index + 1];
  const interval = right ? right.time - left.time : Math.max(1, left.end_time - left.time);
  return left.time + (logical - index) * interval;
}

export function timeToLogical(candles: HistoricalCandle[], time: number): number {
  let low = 0;
  let high = candles.length - 1;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (candles[middle].time <= time) low = middle;
    else high = middle - 1;
  }
  const index = Math.max(0, Math.min(candles.length - 2, low));
  const left = candles[index];
  const right = candles[index + 1];
  const interval = right ? right.time - left.time : Math.max(1, left.end_time - left.time);
  return index + (time - left.time) / interval;
}

/** Keep the full timeline around a detailed viewport, without overlapping candles. */
export function mergeHistoryDetail(overview: HistoricalCandle[], detail: ChartWindow): HistoricalCandle[] {
  const start = Date.parse(detail.start) / 1000;
  const end = Date.parse(detail.end) / 1000;
  return [...overview.filter((bar) => bar.end_time <= start || bar.time >= end), ...detail.candles]
    .sort((a, b) => a.time - b.time);
}

/** Preload beside the viewport so ordinary panning stays smooth. */
export function historyWindow(range: HistoryRange, bounds: HistoryRange): HistoryRange {
  const margin = Math.max(60, (range.to - range.from) / 4);
  return { from: Math.max(bounds.from, Math.floor(range.from - margin)), to: Math.min(bounds.to, Math.ceil(range.to + margin)) };
}
