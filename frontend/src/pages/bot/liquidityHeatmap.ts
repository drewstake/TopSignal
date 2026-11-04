import { normalizeOrderBookContractId, type OrderBookStore } from "./orderBook";

export const HEATMAP_SAMPLE_MS = 500;
export const HEATMAP_HISTORY_MS = 10 * 60_000;
export const HEATMAP_MAX_FRAMES = HEATMAP_HISTORY_MS / HEATMAP_SAMPLE_MS;
export const HEATMAP_LEVEL_LIMIT = 80;

export interface LiquidityFrame {
  time: number;
  sourceTime: number | null;
  epoch: number;
  /** Interleaved integer tick / displayed quantity. Missing ticks are unknown. */
  bids: Float64Array;
  asks: Float64Array;
  bestBid: number | null;
  bestAsk: number | null;
  gap: string | null;
  limited: boolean;
}

export interface LiquidityView {
  frames: readonly LiquidityFrame[];
  now: number;
  recordingSince: number | null;
  status: string;
}

/** Fixed-capacity ring. Sampling is independent of display pause and React. */
export class LiquidityHeatmapBuffer {
  private readonly ring: Array<LiquidityFrame | undefined> = new Array(HEATMAP_MAX_FRAMES);
  private head = 0;
  private length = 0;
  private lastTime: number | null = null;
  private lastEpoch: number | null = null;
  private recordingSince: number | null = null;
  private status = "Loading depth…";
  readonly contractId: string | null;
  readonly tickSize: number | null;

  constructor(contractId: string | null | undefined, tickSize: number | null) {
    this.contractId = normalizeOrderBookContractId(contractId);
    this.tickSize = tickSize;
  }

  sample(store: OrderBookStore, now: number): void {
    // Browser intervals jitter by a few milliseconds. Rate-limit by time bucket,
    // otherwise a 499ms callback is dropped and creates a false half-second gap.
    if (!Number.isFinite(now) || (this.lastTime !== null &&
      (now <= this.lastTime || Math.floor(now / HEATMAP_SAMPLE_MS) === Math.floor(this.lastTime / HEATMAP_SAMPLE_MS)))) return;
    const observation = store.getHeatmapObservation(now, HEATMAP_LEVEL_LIMIT);
    // A reused store changing instrument must immediately discard all old data.
    if (observation.contractId !== this.contractId) {
      this.clear();
      this.status = "Waiting for selected contract…";
      return;
    }
    let gap: string | null = null;
    if (!this.contractId) gap = "No contract selected";
    else if (!this.tickSize || !Number.isFinite(this.tickSize) || this.tickSize <= 0) gap = "Waiting for contract tick size…";
    else if (observation.connection !== "connected") {
      gap = ({ loading: "Loading depth…", disconnected: "Disconnected", reconnecting: "Reconnecting…",
        unavailable: "Depth unavailable", market_closed: "Market closed" } as const)[observation.connection];
    } else if (observation.awaitingSnapshot) gap = "Loading initial depth snapshot…";
    else if (observation.timestampMs === null) gap = "Loading depth after reset…";
    else if (!observation.fresh) gap = "Stale depth · waiting for fresh updates";
    else if (this.lastEpoch !== null && observation.epoch !== this.lastEpoch) gap = "Depth reset / continuity gap";
    else if (this.lastTime !== null && now - this.lastTime > HEATMAP_SAMPLE_MS * 2) gap = "Collection gap";

    const pack = (levels: Array<[number, number]>) => {
      const data = new Float64Array(levels.length * 2);
      for (let i = 0; i < levels.length; i++) {
        const [price, size] = levels[i];
        const tick = price / this.tickSize!;
        // Do not silently snap a mismatched instrument or invalid price grid.
        if (Math.abs(tick - Math.round(tick)) > 0.0001) gap = "Price does not match contract tick size";
        data[i * 2] = Math.round(tick);
        data[i * 2 + 1] = size;
      }
      return data;
    };
    const bids = gap ? new Float64Array() : pack(observation.bids);
    const asks = gap ? new Float64Array() : pack(observation.asks);
    const bestBid = observation.bids[0]?.[0] ?? null;
    const bestAsk = observation.asks[0]?.[0] ?? null;
    if (!gap && bestBid !== null && bestAsk !== null && bestBid >= bestAsk) gap = "Crossed / locked depth · rebuilding";
    if (!gap && !bids.length && !asks.length) gap = "Incomplete depth · no observed levels";
    const frame: LiquidityFrame = {
      time: now, sourceTime: observation.timestampMs, epoch: observation.epoch,
      bids: gap ? new Float64Array() : bids, asks: gap ? new Float64Array() : asks,
      bestBid: gap ? null : bestBid, bestAsk: gap ? null : bestAsk, gap, limited: observation.truncated,
    };
    if (!gap && this.recordingSince === null) this.recordingSince = now;
    this.status = gap ?? (bestBid === null || bestAsk === null ? "Incomplete depth · one side missing"
      : !observation.sequenceKnown ? "Incomplete depth · sequence unavailable" : "Live · observed depth");
    this.lastEpoch = observation.epoch;
    this.lastTime = now;
    this.ring[this.head] = frame;
    this.head = (this.head + 1) % HEATMAP_MAX_FRAMES;
    this.length = Math.min(this.length + 1, HEATMAP_MAX_FRAMES);
    while (this.length && this.ring[this.startIndex()]!.time < now - HEATMAP_HISTORY_MS) {
      this.ring[this.startIndex()] = undefined;
      this.length--;
    }
  }

  view(now: number): LiquidityView {
    const frames = Array.from({ length: this.length }, (_, i) => this.ring[(this.startIndex() + i) % HEATMAP_MAX_FRAMES]!);
    return { frames, now, recordingSince: this.recordingSince, status: this.status };
  }

  private startIndex() { return (this.head - this.length + HEATMAP_MAX_FRAMES) % HEATMAP_MAX_FRAMES; }
  private clear() {
    this.ring.fill(undefined); this.head = 0; this.length = 0;
    this.lastTime = null; this.lastEpoch = null; this.recordingSince = null;
  }
}

export function frameAtTime(frames: readonly LiquidityFrame[], time: number): LiquidityFrame | undefined {
  // Never stretch a sample through a stalled timer, disconnected feed or missing history.
  for (let i = frames.length - 1; i >= 0; i--) {
    const frame = frames[i];
    if (frame.time <= time && time < frame.time + HEATMAP_SAMPLE_MS) return frame;
  }
}

export function quantityAtTick(levels: Float64Array, tick: number): number | null {
  for (let i = 0; i < levels.length; i += 2) if (levels[i] === tick) return levels[i + 1];
  return null;
}
