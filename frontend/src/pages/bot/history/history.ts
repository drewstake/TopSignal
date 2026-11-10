export type DepthScope = "all" | "10" | "20" | "50" | "100";
export type Metric = "mid_price" | "bid_contracts" | "ask_contracts" | "imbalance";
export const metrics: Metric[] = ["mid_price", "bid_contracts", "ask_contracts", "imbalance"];
export type Gap = { reasons: string[]; seconds: number; start: number; end: number };
export type HistoryPoint = {
  time: number; timestamp: string; contract_id: string; capture_id: string; generation: number; index: number;
  bid_contracts: number; ask_contracts: number; bid_levels: number; ask_levels: number;
  imbalance: number | null; mid_price: number | null; last_trade_price: number | null; spread: number | null;
  quality_flags: string[];
  reconstruction_id?: string;
  sample_interval_ms?: number;
  continuity?: Partial<Record<Metric, number>>;
  gap_before?: Partial<Record<Metric, Gap | null>>;
  side_age_ms?: Record<string, number | null>;
  provider_age_ms?: Record<string, number | null>;
};
export type BookHistory = {
  points: HistoryPoint[]; scope: DepthScope; start: string; end: string;
  earliest: string | null; latest: string | null; available_days: string[]; contracts: string[];
  skipped_segments: number; sealed_only: boolean; interval_ms: number; source: string; generated_at: string;
  window?: "latest" | "session";
  raw_measurements?: number; flagged_measurements?: number;
  quality_counts?: Record<string, number>; excluded_by_metric?: Record<string, number>;
  recording_health?: { state: string; at: string; stale: boolean; last_write_at: string | null; pending_events: number;
    last_totals_write_at?: string | null; pending_measurements?: number;
    rejected_events: number; dropped_measurements: number | null; reconnects: number; reason: string | null } | null;
};
const eastern = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const easternParts = (ms: number) => Object.fromEntries(eastern.formatToParts(ms).map(p => [p.type, Number(p.value)]));
/** Most recent weekday Globex session; closed weekends retain the Friday session. */
export function sessionWindow(nowMs: number): { start: number; end: number } {
  const p = easternParts(nowMs);
  const day = new Date(Date.UTC(p.year, p.month - 1, p.day - (p.hour < 18 ? 1 : 0)));
  while ([5, 6].includes(day.getUTCDay())) day.setUTCDate(day.getUTCDate() - 1);
  const at = (offset: number, hour: number) => {
    const wall = +day + offset * 86400000 + hour * 3600000;
    const local = easternParts(wall);
    return (2 * wall - Date.UTC(local.year, local.month - 1, local.day, local.hour, local.minute, local.second)) / 1000;
  };
  return { start: at(0, 18), end: Math.min(nowMs / 1000, at(1, 17)) };
}
export const sameStream = (a: HistoryPoint, b: HistoryPoint) =>
  a.capture_id === b.capture_id && a.generation === b.generation && a.contract_id === b.contract_id && a.reconstruction_id === b.reconstruction_id;
export const usable = (p: HistoryPoint) => !p.quality_flags.length && p.mid_price !== null;

const boundaries = new Set(["connect", "disconnect", "gap", "roll", "end", "reset", "epoch_changed", "contract_changed", "receipt_gap", "receipt_time_reversed", "sampling_gap"]);
const advisory = new Set(["bid_provider_time_unreliable", "ask_provider_time_unreliable"]);
const known = new Set([...boundaries, ...advisory, "missing_side", "bid_stale", "ask_stale", "locked_or_crossed", "locked_or_crossed_book"]);
export function metricReasons(p: HistoryPoint, field: Metric): string[] {
  const reasons = p.quality_flags.filter(f => boundaries.has(f) || !known.has(f));
  const sides = field === "bid_contracts" ? ["bid"] : field === "ask_contracts" ? ["ask"] : ["bid", "ask"];
  for (const side of sides) {
    if (p.quality_flags.includes(`${side}_stale`)) reasons.push(`${side}_stale`);
    if (!(side === "bid" ? p.bid_levels : p.ask_levels)) reasons.push(`${side}_missing`);
  }
  if (field === "mid_price") reasons.push(...p.quality_flags.filter(f => advisory.has(f) || f.startsWith("locked_or_crossed")));
  if (p[field] === null || !Number.isFinite(p[field])) reasons.push(`${field}_unavailable`);
  return reasons;
}
export const metricUsable = (p: HistoryPoint, field: Metric) => metricReasons(p, field).length === 0;

export function continuousSegments(points: HistoryPoint[], field: Metric = "mid_price"): HistoryPoint[][] {
  const segments: HistoryPoint[][] = [];
  let current: HistoryPoint[] = [];
  for (const p of points) {
    if (!metricUsable(p, field)) { current = []; continue; }
    const previous = current.at(-1);
    if (!previous || !sameStream(previous, p) || previous.continuity?.[field] !== p.continuity?.[field] || p.time - previous.time > Math.max(2, (previous.sample_interval_ms ?? 0) / 1000 * 1.5) || p.time <= previous.time) {
      current = [];
      segments.push(current);
    }
    current.push(p);
  }
  return segments;
}

/** First/last and real extrema, retaining each observation's own quality and continuity. */
export function downsampleHistory(points: HistoryPoint[]): HistoryPoint[] {
  const buckets = new Map<string, Map<string, HistoryPoint>>();
  for (const p of points) {
    const key = `${p.contract_id}:${p.capture_id}:${p.generation}:${Math.floor(p.time / 10)}`;
    let bucket = buckets.get(key);
    if (!bucket) { bucket = new Map([["first", p]]); buckets.set(key, bucket); }
    bucket.set("last", p);
    for (const field of metrics) if (metricUsable(p, field)) {
      for (const direction of ["min", "max"]) {
        const k = field + direction, old = bucket.get(k);
        if (!old || (direction === "min" ? p[field]! < old[field]! : p[field]! > old[field]!)) bucket.set(k, p);
      }
    }
  }
  const unique = new Map<string, HistoryPoint>();
  for (const bucket of buckets.values()) for (const p of bucket.values()) unique.set(`${p.capture_id}:${p.index}`, { ...p, sample_interval_ms: 10000 });
  return [...unique.values()].sort((a,b) => a.time-b.time);
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
