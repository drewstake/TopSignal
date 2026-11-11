import { isApiError, requestJson } from "../../lib/api";
import type { ServerClock } from "./heatmapClock";

/** Resolution tiers served by the backend: 500 ms frames, 5 s and 60 s means. */
export const TIER_MS = [500, 5_000, 60_000] as const;
/** Fixed tile spans keep requests bounded and cacheable (240-360 columns each). */
export const TILE_MS = [120_000, 1_800_000, 14_400_000] as const;
export type Tier = 0 | 1 | 2;
export const INT32_NONE = -2147483648;

export interface HeatmapGap { start: number; end: number; reason: string }
export interface DeriverStatus {
  state?: string; reason?: string; pending_segments?: number; processed_through_ms?: number | null; updated_at?: string;
}
export interface HistoryFrames {
  /** Absolute start time (server ms) of each frame or bucket. */
  time: Float64Array;
  span: number;
  levelStart: Uint32Array;
  levelCount: Uint16Array;
  bidCount: Uint16Array;
  bestBid: Int32Array;
  bestAsk: Int32Array;
  /** Number of valid 500 ms samples behind each bucket (1 for raw frames). */
  valid: Uint16Array;
  tick: Int32Array;
  qty: Float32Array;
  /** 1 bid, 2 ask, 3 both sides observed at this price within the bucket. */
  side: Uint8Array;
  /** Coarse tiers: samples in which each level was observed (basis of its mean). */
  seen: Uint16Array | null;
}
export interface HistoryTrades {
  time: Float64Array; tick: Int32Array; side: Uint8Array; volume: Float32Array; prints: Uint32Array; groupMs: number;
}
export interface HistoryMeta {
  session: { start: number; end: number; key: string };
  coverage: Array<[number, number]>;
  gaps: HeatmapGap[];
  gapsTruncated: boolean;
  historyThrough: number | null;
  deriver: DeriverStatus | null;
  notes: Array<[number, string]>;
  tickSize: number;
  overlappingExcluded: number;
  source: string | null;
  receivedAt: number;
}
export interface HistoryTile {
  key: string; tier: Tier; start: number; end: number;
  frames: HistoryFrames | null; trades: HistoryTrades | null;
  tradesTruncated: boolean; tradesMinVolume: number | null;
  fetchedAt: number; bytes: number;
  /** Recorder data this response covered (its own history_through_ms) and whether derivation was current. */
  through: number | null; deriverCurrent: boolean;
}
interface RawColumns { [name: string]: string | number | null }
export interface RawHeatmapHistory {
  schema: string; contract_id: string; data_live: boolean; tick_size: number; tier: number; bucket_ms: number; trade_group_ms: number;
  session: { start_ms: number; end_ms: number; key: string }; range: { start_ms: number; end_ms: number };
  server_time_ms: number; history_through_ms: number | null; coverage: Array<[number, number]>;
  gaps: Array<[number, number, string]>; gaps_truncated?: boolean; notes: Array<[number, string]>;
  overlapping_excluded: number; deriver: DeriverStatus | null; source?: string;
  time_origin_ms?: number; frames: RawColumns | null; trades: RawColumns | null;
  trades_truncated: boolean; trades_min_volume: number | null;
}

function buffer(value: unknown): ArrayBuffer {
  if (typeof value !== "string") throw new Error("Malformed heatmap history column");
  const text = atob(value);
  const bytes = new Uint8Array(text.length);
  for (let i = 0; i < text.length; i++) bytes[i] = text.charCodeAt(i);
  return bytes.buffer;
}

function typed<T>(Kind: { new (buffer: ArrayBuffer): T; BYTES_PER_ELEMENT: number }, value: unknown, length?: number): T {
  const data = buffer(value);
  if (data.byteLength % Kind.BYTES_PER_ELEMENT) throw new Error("Malformed heatmap history column");
  const result = new Kind(data);
  if (length !== undefined && (result as unknown as ArrayLike<number>).length !== length) throw new Error("Malformed heatmap history column");
  return result;
}

/** Validate scope before any history is shown. A different contract or tick grid is never drawn. */
export function decodeHeatmapHistory(raw: RawHeatmapHistory, expected: { contractId: string; tickSize: number; tier: Tier }, receivedAt: number) {
  if (raw.schema !== "liquidity-heatmap-history-v1") throw new Error("Unsupported heatmap history format");
  if (raw.contract_id.toUpperCase() !== expected.contractId.toUpperCase()) throw new Error("History belongs to a different contract");
  if (raw.tier !== expected.tier) throw new Error("History resolution mismatch");
  if (!(Math.abs(raw.tick_size - expected.tickSize) < 1e-9)) throw new Error("History tick size does not match the contract");
  const origin = raw.time_origin_ms ?? raw.range.start_ms;
  let frames: HistoryFrames | null = null;
  let trades: HistoryTrades | null = null;
  let bytes = 0;
  if (raw.frames) {
    const count = Number(raw.frames.count);
    const offsets = typed(Uint32Array, raw.frames.time, count);
    const levelCount = typed(Uint16Array, raw.frames.level_count, count);
    const tick = typed(Int32Array, raw.frames.tick);
    const qty = typed(Float32Array, raw.frames.qty, tick.length);
    const side = typed(Uint8Array, raw.frames.side, tick.length);
    const levelStart = new Uint32Array(count);
    const time = new Float64Array(count);
    let total = 0;
    for (let i = 0; i < count; i++) { levelStart[i] = total; total += levelCount[i]; time[i] = origin + offsets[i]; }
    if (total !== tick.length) throw new Error("Malformed heatmap history levels");
    frames = { time, span: raw.bucket_ms, levelStart, levelCount, bidCount: typed(Uint16Array, raw.frames.bid_count, count),
      bestBid: typed(Int32Array, raw.frames.best_bid, count), bestAsk: typed(Int32Array, raw.frames.best_ask, count),
      valid: typed(Uint16Array, raw.frames.valid, count), tick, qty, side,
      seen: typeof raw.frames.seen === "string" ? typed(Uint16Array, raw.frames.seen, tick.length) : null };
    bytes += count * 30 + tick.length * 9;
  }
  if (raw.trades) {
    const count = Number(raw.trades.count);
    const offsets = typed(Uint32Array, raw.trades.time, count);
    const time = new Float64Array(count);
    for (let i = 0; i < count; i++) time[i] = origin + offsets[i];
    trades = { time, tick: typed(Int32Array, raw.trades.tick, count), side: typed(Uint8Array, raw.trades.side, count),
      volume: typed(Float32Array, raw.trades.volume, count), prints: typed(Uint32Array, raw.trades.prints, count),
      groupMs: raw.trade_group_ms };
    bytes += count * 25;
  }
  const meta: HistoryMeta = {
    session: { start: raw.session.start_ms, end: raw.session.end_ms, key: raw.session.key },
    coverage: raw.coverage.map(([s, e]) => [s, e]), gaps: raw.gaps.map(([start, end, reason]) => ({ start, end, reason })),
    gapsTruncated: Boolean(raw.gaps_truncated), historyThrough: raw.history_through_ms, deriver: raw.deriver,
    notes: raw.notes, tickSize: raw.tick_size, overlappingExcluded: raw.overlapping_excluded, source: raw.source ?? null, receivedAt,
  };
  return { frames, trades, meta, bytes, tradesTruncated: raw.trades_truncated, tradesMinVolume: raw.trades_min_volume };
}

export type HistoryFetcher = (args: { accountId: number; contractId: string; tier: Tier; start: number; end: number;
  summary: boolean; signal: AbortSignal }) => Promise<RawHeatmapHistory>;

export const fetchHeatmapHistory: HistoryFetcher = ({ accountId, contractId, tier, start, end, summary, signal }) =>
  requestJson<RawHeatmapHistory>(`/api/accounts/${accountId}/liquidity-heatmap`, { signal, query: {
    contract_id: contractId, start_ms: Math.floor(start), end_ms: Math.ceil(end), tier, data_live: false, summary } });

/** New York 6 p.m. session start, matching the backend helper. */
export function sessionStartMs(at: number): number {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", year: "numeric", month: "2-digit",
    day: "2-digit", hour: "2-digit", hourCycle: "h23" }).formatToParts(at);
  const part = (name: string) => Number(parts.find(p => p.type === name)?.value);
  const day = new Date(Date.UTC(part("year"), part("month") - 1, part("day")));
  if (part("hour") < 18) day.setUTCDate(day.getUTCDate() - 1);
  while (day.getUTCDay() === 5 || day.getUTCDay() === 6) day.setUTCDate(day.getUTCDate() - 1);
  // 18:00 New York is 22:00 or 23:00 UTC; pick the candidate that formats as 18:00 local.
  for (const utcHour of [22, 23]) {
    const candidate = Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), utcHour);
    const hour = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", hourCycle: "h23" }).format(candidate);
    if (Number(hour) === 18) return candidate;
  }
  return Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), 23);
}

export interface HistoryStatus {
  state: "idle" | "loading" | "ready" | "unavailable";
  message: string | null;
  meta: HistoryMeta | null;
  summary: HistoryTile | null;
  version: number;
}

const MEMORY_BUDGET = 48 * 1024 * 1024;
const OPEN_REFRESH_MS = 15_000;
const SUMMARY_REFRESH_MS = 30_000;
const RETRY_MS = 30_000;

/**
 * Bounded tile cache for the current session. Closed tiles are immutable; the
 * open tail (not yet sealed by the recorder) refreshes while it is in view.
 */
export class HeatmapHistoryStore {
  private tiles = new Map<string, HistoryTile>();
  private inflight = new Map<string, AbortController>();
  private failures = new Map<string, number>();
  private listeners = new Set<() => void>();
  private bytes = 0;
  private disposed = false;
  private sessionStart: number | null = null;
  private summaryFetchedAt = 0;
  /** After a failure, pause all history requests briefly instead of trying every tile. */
  private blockedUntil = 0;
  status: HistoryStatus = { state: "idle", message: null, meta: null, summary: null, version: 0 };

  private readonly scope: { accountId: number; contractId: string; tickSize: number };
  private readonly clock: ServerClock;
  private readonly fetcher: HistoryFetcher;
  private readonly local: () => number;

  constructor(scope: { accountId: number; contractId: string; tickSize: number }, clock: ServerClock,
    fetcher: HistoryFetcher = fetchHeatmapHistory, local: () => number = Date.now) {
    this.scope = scope; this.clock = clock; this.fetcher = fetcher; this.local = local;
  }

  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  getStatus = () => this.status;

  dispose() {
    this.disposed = true;
    for (const controller of this.inflight.values()) controller.abort();
    this.inflight.clear(); this.listeners.clear(); this.tiles.clear();
  }

  private emit(patch: Partial<HistoryStatus>) {
    this.status = { ...this.status, ...patch, version: this.status.version + 1 };
    for (const listener of this.listeners) listener();
  }

  /** Discard history when the New York session rolls over. */
  private checkSession(now: number) {
    const start = sessionStartMs(now);
    if (this.sessionStart === start) return start;
    for (const controller of this.inflight.values()) controller.abort();
    this.inflight.clear(); this.tiles.clear(); this.failures.clear(); this.bytes = 0; this.summaryFetchedAt = 0; this.blockedUntil = 0;
    const first = this.sessionStart === null;
    this.sessionStart = start;
    if (!first) this.emit({ state: "loading", message: null, meta: null, summary: null });
    return start;
  }

  tilesFor(tier: Tier, start: number, end: number): HistoryTile[] {
    const result: HistoryTile[] = [];
    const span = TILE_MS[tier];
    for (let t = Math.floor(start / span) * span; t < end; t += span) {
      const tile = this.tiles.get(`${tier}:${t}`);
      if (tile) result.push(tile);
    }
    return result;
  }

  /** Judged from the tile's own response, so a tile loaded while building or unsealed is refreshed later. */
  private isFinal(tile: HistoryTile) {
    return tile.deriverCurrent && tile.through !== null && tile.end + 120_000 < tile.through;
  }

  /** Request tiles covering the view (plus one tile of margin), nearest the right edge first. */
  ensure(tier: Tier, start: number, end: number) {
    if (this.disposed) return;
    const now = this.clock.now();
    const sessionStart = this.checkSession(now);
    const local = this.local();
    if (local < this.blockedUntil) return;
    if (local - this.summaryFetchedAt > SUMMARY_REFRESH_MS) this.loadSummary(sessionStart, now);
    const span = TILE_MS[tier];
    const from = Math.max(sessionStart, start - span);
    const to = Math.min(now + 1000, end + span);
    const wanted: number[] = [];
    for (let t = Math.floor(from / span) * span; t < to; t += span) wanted.push(t);
    wanted.sort((a, b) => Math.abs(end - a) - Math.abs(end - b));
    for (const tileStart of wanted) {
      const key = `${tier}:${tileStart}`;
      if (this.inflight.size >= 2) break;
      if (this.inflight.has(key)) continue;
      const failed = this.failures.get(key);
      if (failed && local - failed < RETRY_MS) continue;
      const tile = this.tiles.get(key);
      if (tile && (this.isFinal(tile) || local - tile.fetchedAt < OPEN_REFRESH_MS)) continue;
      void this.load(key, tier, Math.max(tileStart, sessionStart), Math.min(tileStart + span, sessionStart + 24 * 3600_000), false);
    }
  }

  private loadSummary(sessionStart: number, now: number) {
    this.summaryFetchedAt = this.local();
    void this.load("summary", 2, sessionStart, Math.max(sessionStart + 60_000, Math.min(now + 1000, sessionStart + 24 * 3600_000)), true);
  }

  private async load(key: string, tier: Tier, start: number, end: number, summary: boolean) {
    const controller = new AbortController();
    this.inflight.set(key, controller);
    if (this.status.state === "idle") this.emit({ state: "loading" });
    const sent = this.local();
    try {
      const raw = await this.fetcher({ ...this.scope, tier, start, end, summary, signal: controller.signal });
      const received = this.local();
      if (this.disposed || controller.signal.aborted) return;
      this.clock.observe(raw.server_time_ms, sent, received);
      const decoded = decodeHeatmapHistory(raw, { contractId: this.scope.contractId, tickSize: this.scope.tickSize, tier }, received);
      if (decoded.meta.session.start !== this.sessionStart) return;  // Rolled over while loading.
      const tile: HistoryTile = { key, tier, start, end, frames: decoded.frames, trades: decoded.trades,
        tradesTruncated: decoded.tradesTruncated, tradesMinVolume: decoded.tradesMinVolume, fetchedAt: received, bytes: decoded.bytes,
        through: decoded.meta.historyThrough, deriverCurrent: decoded.meta.deriver?.state === "current" };
      this.failures.delete(key);
      if (summary) {
        this.emit({ state: "ready", message: null, meta: decoded.meta, summary: tile });
        return;
      }
      const old = this.tiles.get(key);
      if (old) this.bytes -= old.bytes;
      this.tiles.delete(key);
      this.tiles.set(key, tile);
      this.bytes += tile.bytes;
      // Evict least recently loaded tiles beyond the memory budget.
      for (const [other, value] of this.tiles) {
        if (this.bytes <= MEMORY_BUDGET) break;
        if (other === key) continue;
        this.tiles.delete(other); this.bytes -= value.bytes;
      }
      const newer = !this.status.meta || decoded.meta.receivedAt >= this.status.meta.receivedAt;
      this.emit({ state: "ready", message: null, meta: newer ? decoded.meta : this.status.meta });
    } catch (error) {
      if (this.disposed || controller.signal.aborted) return;
      if (isApiError(error)) {
        const detail = error.detail as { server_time_ms?: number; message?: string } | undefined;
        this.clock.observe(detail?.server_time_ms, sent, this.local());
      }
      this.failures.set(key, this.local());
      // A transient "busy" only retries that tile; other failures pause history requests.
      const busy = isApiError(error) && error.status === 503 && /busy/i.test(String((error.detail as { message?: unknown })?.message ?? ""));
      if (busy) this.failures.set(key, this.local() - RETRY_MS + 3_000);
      else this.blockedUntil = this.local() + RETRY_MS;
      const message = isApiError(error) && typeof (error.detail as { message?: unknown })?.message === "string"
        ? (error.detail as { message: string }).message : error instanceof Error ? error.message : "History unavailable";
      // Keep previously loaded tiles visible; report the failure without hiding live data.
      this.emit({ state: this.tiles.size || this.status.summary ? "ready" : busy ? "loading" : "unavailable", message });
    } finally {
      if (this.inflight.get(key) === controller) this.inflight.delete(key);
    }
  }
}

/** Time actually backed by loaded recorder tiles (each up to its own sealed edge). */
export function loadedSpans(tiles: readonly HistoryTile[], coverage: ReadonlyArray<readonly [number, number]>): Array<[number, number]> {
  const spans = tiles.filter(tile => tile.frames && tile.through !== null)
    .map(tile => [tile.start, Math.min(tile.end, tile.through!)] as [number, number]).filter(([s, e]) => e > s)
    .sort((a, b) => a[0] - b[0]);
  const merged: Array<[number, number]> = [];
  for (const span of spans) {
    const last = merged.at(-1);
    if (last && span[0] <= last[1]) last[1] = Math.max(last[1], span[1]); else merged.push([...span]);
  }
  // Intersect with recorder coverage: only recorded, loaded time hides live samples.
  const result: Array<[number, number]> = [];
  let j = 0;
  for (const [s, e] of merged) {
    while (j < coverage.length && coverage[j][1] <= s) j++;
    for (let k = j; k < coverage.length && coverage[k][0] < e; k++) {
      const start = Math.max(s, coverage[k][0]), end = Math.min(e, coverage[k][1]);
      if (end > start) result.push([start, end]);
    }
  }
  return result;
}

export const isCovered = (coverage: ReadonlyArray<readonly [number, number]>, time: number) => {
  let low = 0, high = coverage.length - 1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (time < coverage[mid][0]) high = mid - 1;
    else if (time >= coverage[mid][1]) low = mid + 1;
    else return true;
  }
  return false;
};
