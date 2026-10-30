export type DepthScope = "all" | "10" | "20" | "50" | "100";
export type HistoryPoint = {
  time: number; timestamp: string; contract_id: string; capture_id: string; generation: number; index: number;
  bid_contracts: number; ask_contracts: number; bid_levels: number; ask_levels: number;
  imbalance: number | null; mid_price: number | null; last_trade_price: number | null; spread: number | null;
  quality_flags: string[];
};
export type BookHistory = {
  points: HistoryPoint[]; scope: DepthScope; start: string; end: string;
  earliest: string | null; latest: string | null; available_days: string[]; contracts: string[];
  skipped_segments: number; sealed_only: boolean; interval_ms: number; source: string; generated_at: string;
};
export const sameStream = (a: HistoryPoint, b: HistoryPoint) =>
  a.capture_id === b.capture_id && a.generation === b.generation && a.contract_id === b.contract_id;
export const usable = (p: HistoryPoint) => !p.quality_flags.length && p.mid_price !== null;

export function continuousSegments(points: HistoryPoint[]): HistoryPoint[][] {
  const segments: HistoryPoint[][] = [];
  let current: HistoryPoint[] = [];
  for (const p of points) {
    if (!usable(p)) { current = []; continue; }
    const previous = current.at(-1);
    if (!previous || !sameStream(previous, p) || p.time - previous.time > 2 || p.time <= previous.time) {
      current = [];
      segments.push(current);
    }
    current.push(p);
  }
  return segments;
}

export function priceChangeAfter(points: HistoryPoint[], selected: HistoryPoint, seconds: number): number | null {
  if (!usable(selected)) return null;
  let previous = selected;
  for (const p of points) {
    if (p.time <= selected.time) continue;
    if (!usable(p) || !sameStream(selected, p) || p.time - previous.time > 2) return null;
    if (p.time >= selected.time + seconds) {
      return p.time - selected.time - seconds <= 1 ? p.mid_price! - selected.mid_price! : null;
    }
    previous = p;
  }
  return null;
}

export function nearestPoint(points: HistoryPoint[], time: number): HistoryPoint | null {
  if (!points.length) return null;
  let low = 0, high = points.length - 1;
  while (low < high) {
    const mid = Math.floor((low + high) / 2);
    if (points[mid].time < time) low = mid + 1; else high = mid;
  }
  const p = points[low], before = points[low - 1];
  return before && time - before.time < p.time - time ? before : p;
}

export function localInput(iso: string): string {
  const date = new Date(iso);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
}

export function csvHistory(data: BookHistory, points: HistoryPoint[]): string {
  const headers = ["timestamp_utc", "contract", "scope", "mid_price", "bid_contracts", "ask_contracts", "imbalance", "bid_levels", "ask_levels", "quality_flags", "capture_id", "generation"];
  return [headers, ...points.map(p => [p.timestamp, p.contract_id, data.scope, p.mid_price, p.bid_contracts, p.ask_contracts,
    p.imbalance, p.bid_levels, p.ask_levels, p.quality_flags.join(";"), p.capture_id, p.generation])]
    .map(row => row.map(value => `"${String(value ?? "").replaceAll('"', '""')}"`).join(",")).join("\n");
}
