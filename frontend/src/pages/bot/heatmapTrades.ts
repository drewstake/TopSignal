import type { MarketTradeBatch, MarketTradeSide } from "../../lib/marketTradeTypes";
import { HEATMAP_HISTORY_MS } from "./liquidityHeatmap";

export const TRADE_BUCKET_MS = 250;
export const MAX_TRADE_BUBBLES = 20_000;
export interface TradeBubble {
  key: string;
  time: number;
  price: number;
  side: MarketTradeSide;
  volume: number;
  prints: number;
}
export interface TradeBubbleView {
  bubbles: readonly TradeBubble[];
  partial: boolean;
}

/** Same-price / same-side prints in 250ms buckets. Never net opposite sides. */
export class HeatmapTradeBuffer {
  private groups = new Map<string, TradeBubble>();
  private seen = new Map<string, number>();
  private partial = false;
  readonly contractId: string | null | undefined;
  readonly tickSize: number | null | undefined;
  constructor(contractId: string | null | undefined, tickSize: number | null | undefined) {
    this.contractId = contractId; this.tickSize = tickSize;
  }
  markGap() { this.partial = true; }

  add(batch: MarketTradeBatch, now: number) {
    if (batch.contract_id.trim().toUpperCase() !== this.contractId?.trim().toUpperCase() ||
        !this.tickSize || !Number.isFinite(this.tickSize) || this.tickSize <= 0) return;
    this.prune(now);
    if (batch.dropped > 0) this.markGap();
    for (const trade of batch.trades) {
      const time = Date.parse(trade.timestamp), tick = trade.price / this.tickSize;
      if (!Number.isFinite(time) || time < now - HEATMAP_HISTORY_MS || time > now + 1000 ||
          !Number.isFinite(tick) || Math.abs(tick - Math.round(tick)) > .0001 ||
          !Number.isFinite(trade.volume) || trade.volume <= 0 || trade.price <= 0) { this.markGap(); continue; }
      if (this.seen.has(trade.id)) continue;
      this.seen.set(trade.id, time);
      const side = trade.side === "buy" || trade.side === "sell" ? trade.side : "unknown";
      const key = `${Math.floor(time / TRADE_BUCKET_MS)}:${Math.round(tick)}:${side}`;
      const existing = this.groups.get(key);
      if (existing) {
        if (!Number.isFinite(existing.volume + trade.volume)) { this.markGap(); continue; }
        existing.volume += trade.volume; existing.prints++; existing.time = Math.min(existing.time, time);
      } else this.groups.set(key, { key, time, price: trade.price, side, volume: trade.volume, prints: 1 });
    }
    while (this.groups.size > MAX_TRADE_BUBBLES) {
      this.groups.delete(this.groups.keys().next().value!); this.markGap();
    }
    while (this.seen.size > MAX_TRADE_BUBBLES * 4) {
      this.seen.delete(this.seen.keys().next().value!); this.markGap();
    }
  }

  view(now: number): TradeBubbleView {
    this.prune(now);
    // Copy so paused displays cannot be mutated by incoming prints.
    return { bubbles: [...this.groups.values()].map(bubble => ({ ...bubble })).sort((a, b) => a.time - b.time), partial: this.partial };
  }
  private prune(now: number) {
    for (const [key, bubble] of this.groups) if (bubble.time < now - HEATMAP_HISTORY_MS) this.groups.delete(key);
    for (const [id, time] of this.seen) if (time < now - HEATMAP_HISTORY_MS) this.seen.delete(id);
  }
}

export const tradeBubbleRadius = (volume: number, scale: number) => Math.min(18, Math.max(2.5, Math.sqrt(volume) * 1.8)) * scale;
