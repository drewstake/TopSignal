import { useEffect, useState } from "react";
import type { OrderBookStore } from "../orderBook";
import { downsampleHistory, metrics, sessionWindow, type BookHistory, type DepthScope, type HistoryPoint } from "./history";

export function sampleLiquidity(store: OrderBookStore, scope: DepthScope, now = Date.now()): HistoryPoint | null {
  const book = store.getLiquiditySnapshot(now);
  if (!book) return null;
  const limit = scope === "all" ? Infinity : Number(scope);
  const bids = book.bids.slice(0, limit), asks = book.asks.slice(0, limit);
  const bid = bids.reduce((sum, level) => sum + level[1], 0);
  const ask = asks.reduce((sum, level) => sum + level[1], 0);
  const spread = bids.length && asks.length ? asks[0][0] - bids[0][0] : null;
  return {
    time: book.timestampMs / 1000, timestamp: new Date(book.timestampMs).toISOString(),
    contract_id: book.contractId, capture_id: book.captureId, generation: book.generation, index: book.timestampMs,
    bid_contracts: bid, ask_contracts: ask, bid_levels: bids.length, ask_levels: asks.length,
    imbalance: bid + ask > 0 ? (bid - ask) / (bid + ask) : null,
    mid_price: spread === null ? null : (asks[0][0] + bids[0][0]) / 2,
    spread, last_trade_price: null,
    quality_flags: spread === null ? ["missing_side"] : spread <= 0 ? ["locked_or_crossed_book"] : [],
  };
}

// Only browser memory: at most 15 minutes / 900 samples. No new broker subscription or storage writes.
export function useLiveHistory(store: OrderBookStore | undefined, scope: DepthScope, enabled: boolean) {
  const [state, setState] = useState<{ store?: OrderBookStore; scope: DepthScope; points: HistoryPoint[]; fresh: boolean }>({ scope, points: [], fresh: false });
  useEffect(() => {
    if (!store || !enabled) return;
    const sample = () => {
      const now = Date.now();
      const point = sampleLiquidity(store, scope, now);
      setState(previous => {
        const points = previous.store === store && previous.scope === scope ? previous.points : [];
        const last = points.at(-1);
        const append = point && (!last || point.time > last.time);
        const expired = points.length > 0 && points[0].time < now / 1000 - 900;
        const recent = append || expired ? points.filter(p => p.time >= now / 1000 - 900) : points;
        if (append) recent.push(point);
        if (previous.store === store && previous.scope === scope && recent === points && previous.fresh === (point !== null)) return previous;
        return { store, scope, points: recent.length > 900 ? recent.slice(-900) : recent, fresh: point !== null };
      });
    };
    sample();
    const timer = window.setInterval(sample, 1000);
    return () => window.clearInterval(timer);
  }, [store, scope, enabled]);
  return state.store === store && state.scope === scope ? state : { points: [], fresh: false };
}

// Immutable archive responses are indexed once and released when the response is replaced.
const archiveIndexes = new WeakMap<BookHistory, { points: HistoryPoint[]; through: Map<string, number> }>();
function archiveIndex(recorded: BookHistory | null) {
  if (!recorded) return { points: [], through: new Map<string, number>() };
  const cached = archiveIndexes.get(recorded);
  if (cached) return cached;
  const points = [...recorded.points].sort((a, b) => a.time - b.time);
  const through = new Map<string, number>();
  for (const p of points) through.set(p.contract_id, p.time);
  const index = { points, through };
  archiveIndexes.set(recorded, index);
  return index;
}

export function mergeLiveHistory(recorded: BookHistory | null, live: HistoryPoint[], scope: DepthScope, window: "latest" | "session" = "latest"): BookHistory | null {
  if (!live.length) return recorded;
  // A sealed recording is authoritative when it catches up. Compare per contract.
  const archive = archiveIndex(recorded);
  const tail = live.filter(p => p.time > (archive.through.get(p.contract_id) ?? -Infinity));
  if (!tail.length) return recorded;
  const latest = Math.max(tail.at(-1)!.time, recorded?.latest ? Date.parse(recorded.latest) / 1000 : -Infinity);
  const start = window === "session" ? sessionWindow(latest * 1000).start : latest - 900;
  // Merge two ordered series, retaining original sample objects and avoiding a full sort every second.
  const points: HistoryPoint[] = [];
  let a = 0, b = 0;
  while (a < archive.points.length || b < tail.length) {
    const next = b >= tail.length || (a < archive.points.length && archive.points[a].time <= tail[b].time) ? archive.points[a++] : tail[b++];
    if (next.time >= start) points.push(next);
  }
  return {
    ...recorded, points, scope, window, start: new Date(start * 1000).toISOString(), end: new Date(latest * 1000).toISOString(),
    earliest: recorded?.earliest ?? points[0].timestamp, latest: new Date(latest * 1000).toISOString(),
    available_days: recorded?.available_days ?? [], contracts: [...new Set([tail.at(-1)!.contract_id, ...points.map(p => p.contract_id)])],
    skipped_segments: recorded?.skipped_segments ?? 0, sealed_only: false, interval_ms: recorded?.interval_ms ?? 1000,
    source: recorded ? `${recorded.source} + live ProjectX stream` : "Live ProjectX stream",
    generated_at: tail.at(-1)!.timestamp,
  };
}

/** Keep the loaded session, replacing only the refreshed tail. Compact old detail once per archive poll. */
export function mergeSessionArchive(previous: BookHistory, recent: BookHistory, nowMs = Date.now()): BookHistory {
  const bounds = sessionWindow(nowMs);
  const cutoff = Math.max(bounds.start, Date.parse(recent.start) / 1000);
  // A query starts without the preceding invalid receipt. Match real overlap to
  // carry its continuity token across polls, never infer it from elapsed time.
  const prior = new Map(previous.points.map(p => [`${p.capture_id}:${p.index}`,p]));
  const tokens = new Map<string,number>();
  for (const p of recent.points) {
    const old = prior.get(`${p.capture_id}:${p.index}`);
    for (const field of metrics) if (p.continuity?.[field] === 0 && old?.continuity?.[field] !== undefined)
      tokens.set(`${p.capture_id}:${p.generation}:${field}`,old.continuity[field]!);
  }
  const aligned = recent.points.map(p => !p.continuity ? p : { ...p, continuity: Object.fromEntries(metrics.map(field => [field,
    p.continuity?.[field] === 0 ? tokens.get(`${p.capture_id}:${p.generation}:${field}`) ?? 0 : p.continuity?.[field]])) });
  const retained = downsampleHistory(previous.points.filter(p => p.time >= bounds.start && p.time < cutoff));
  const points = [...retained, ...aligned.filter(p => p.time >= cutoff && p.time <= bounds.end)].sort((a,b) => a.time-b.time);
  return { ...recent, points, window: "session", interval_ms: 10000,
    raw_measurements: undefined, flagged_measurements: undefined, quality_counts: undefined, excluded_by_metric: undefined,
    start: new Date(bounds.start * 1000).toISOString(), end: new Date(bounds.end * 1000).toISOString(),
    contracts: [...new Set(points.map(p => p.contract_id))], skipped_segments: Math.max(previous.skipped_segments, recent.skipped_segments) };
}
