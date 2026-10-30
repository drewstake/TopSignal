import type {
  ProjectXMarketDepthConnectionState,
  ProjectXMarketDepthSnapshot,
  ProjectXMarketDepthState,
  ProjectXMarketDepthUpdate,
} from "../../lib/types";

export type OrderBookSide = "ask" | "bid";
export type OrderBookLevelCount = 1 | 10 | 20 | 50 | "all";
export type OrderBookConnectionState = ProjectXMarketDepthConnectionState | "loading";
export type OrderBookUpdateResult = "applied" | "ignored" | "gap";

export interface OrderBookLevelView {
  price: number;
  size: number;
  depthPercent: number;
  isBest: boolean;
}

export interface OrderBookMetaSnapshot {
  contractId: string | null;
  connection: OrderBookConnectionState;
  message: string | null;
  hasSnapshot: boolean;
  hasDepth: boolean;
}

export interface OrderBookSpreadSnapshot {
  bestAsk: number | null;
  bestBid: number | null;
  spread: number | null;
}

export interface OrderBookDepthSnapshot {
  bidCount: number;
  askCount: number;
  visibleBidCount: number;
  visibleAskCount: number;
}

export interface OrderBookTotalsSnapshot {
  bidSize: number;
  askSize: number;
}

export interface OrderBookViewSnapshot {
  asks: OrderBookLevelView[];
  bids: OrderBookLevelView[];
  meta: OrderBookMetaSnapshot;
  spread: OrderBookSpreadSnapshot;
  sequence: number | null;
}

type StoreListener = () => void;

export function normalizeOrderBookContractId(contractId: string | null | undefined): string | null {
  const normalized = contractId?.trim().toUpperCase() ?? "";
  return normalized || null;
}

/**
 * A fine-grained external store. Rows subscribe to individual visible slots,
 * while status and spread subscribe independently. A depth delta therefore
 * does not rerender the panel shell or unchanged rows.
 */
export class OrderBookStore {
  private contractId: string | null;
  private bids = new Map<number, number>();
  private asks = new Map<number, number>();
  private sequence: number | null = null;
  private lastTimestampMs: number | null = null;
  private awaitingSnapshot = true;
  private visibleLevelCount: OrderBookLevelCount;
  private askSlots: Array<OrderBookLevelView | null> = [];
  private bidSlots: Array<OrderBookLevelView | null> = [];
  private meta: OrderBookMetaSnapshot;
  private depth: OrderBookDepthSnapshot = { bidCount: 0, askCount: 0, visibleBidCount: 0, visibleAskCount: 0 };
  private spread: OrderBookSpreadSnapshot = { bestAsk: null, bestBid: null, spread: null };
  private totals: OrderBookTotalsSnapshot = { bidSize: 0, askSize: 0 };
  private readonly metaListeners = new Set<StoreListener>();
  private readonly depthListeners = new Set<StoreListener>();
  private readonly spreadListeners = new Set<StoreListener>();
  private readonly totalsListeners = new Set<StoreListener>();
  private readonly slotListeners = new Map<string, Set<StoreListener>>();

  constructor(contractId?: string | null, visibleLevelCount: OrderBookLevelCount = 20) {
    this.contractId = normalizeOrderBookContractId(contractId);
    this.visibleLevelCount = visibleLevelCount;
    this.meta = {
      contractId: this.contractId,
      connection: this.contractId ? "loading" : "unavailable",
      message: this.contractId ? null : "Select a saved bot to view market depth.",
      hasSnapshot: false,
      hasDepth: false,
    };
  }

  getMetaSnapshot = (): OrderBookMetaSnapshot => this.meta;

  getSpreadSnapshot = (): OrderBookSpreadSnapshot => this.spread;

  getDepthSnapshot = (): OrderBookDepthSnapshot => this.depth;

  getTotalsSnapshot = (): OrderBookTotalsSnapshot => this.totals;

  getAskSlotSnapshot = (index: number): OrderBookLevelView | null => this.askSlots[index] ?? null;

  getBidSlotSnapshot = (index: number): OrderBookLevelView | null => this.bidSlots[index] ?? null;

  subscribeMeta = (listener: StoreListener): (() => void) => subscribe(this.metaListeners, listener);

  subscribeSpread = (listener: StoreListener): (() => void) => subscribe(this.spreadListeners, listener);

  subscribeDepth = (listener: StoreListener): (() => void) => subscribe(this.depthListeners, listener);

  subscribeTotals = (listener: StoreListener): (() => void) => subscribe(this.totalsListeners, listener);

  subscribeAskSlot = (index: number, listener: StoreListener): (() => void) =>
    this.subscribeSlot("ask", index, listener);

  subscribeBidSlot = (index: number, listener: StoreListener): (() => void) =>
    this.subscribeSlot("bid", index, listener);

  selectContract(contractId: string | null | undefined): boolean {
    const normalized = normalizeOrderBookContractId(contractId);
    if (normalized === this.contractId) {
      return false;
    }
    this.contractId = normalized;
    this.bids.clear();
    this.asks.clear();
    this.sequence = null;
    this.lastTimestampMs = null;
    this.awaitingSnapshot = true;
    this.replaceMeta({
      contractId: normalized,
      connection: normalized ? "loading" : "unavailable",
      message: normalized ? null : "Select a saved bot to view market depth.",
      hasSnapshot: false,
      hasDepth: false,
    });
    this.recomputeVisibleBook();
    return true;
  }

  setVisibleLevelCount(levelCount: OrderBookLevelCount): void {
    if (levelCount === this.visibleLevelCount) {
      return;
    }
    this.visibleLevelCount = levelCount;
    this.recomputeVisibleBook();
  }

  setConnectionState(state: ProjectXMarketDepthState): boolean {
    if (!this.matchesContract(state.contract_id)) {
      return false;
    }
    if (state.state === "disconnected" || state.state === "reconnecting") {
      // Incremental events are unsafe until the reconnect's authoritative
      // snapshot has re-established the book.
      this.awaitingSnapshot = true;
    }
    if (state.state === "market_closed") {
      this.bids.clear();
      this.asks.clear();
      this.sequence = null;
      this.lastTimestampMs = null;
      this.awaitingSnapshot = true;
      this.recomputeVisibleBook();
    }
    this.replaceMeta({
      ...this.meta,
      connection: state.state,
      message: state.message?.trim() || null,
      ...(state.state === "market_closed" ? { hasSnapshot: false, hasDepth: false } : {}),
    });
    return true;
  }

  applySnapshot(snapshot: ProjectXMarketDepthSnapshot): boolean {
    if (
      this.meta.connection === "market_closed" ||
      !this.matchesContract(snapshot.contract_id) ||
      !Array.isArray(snapshot.bids) ||
      !Array.isArray(snapshot.asks) ||
      (snapshot.sequence !== null && (!Number.isSafeInteger(snapshot.sequence) || snapshot.sequence < 0))
    ) {
      return false;
    }
    const snapshotTimestampMs = parseTimestampMs(snapshot.timestamp);

    const nextBids = levelsToMap(snapshot.bids);
    const nextAsks = levelsToMap(snapshot.asks);
    this.bids = nextBids;
    this.asks = nextAsks;
    this.sequence = snapshot.sequence;
    this.lastTimestampMs = snapshotTimestampMs;
    this.awaitingSnapshot = false;
    this.replaceMeta({
      ...this.meta,
      hasSnapshot: true,
      hasDepth: nextBids.size > 0 || nextAsks.size > 0,
    });
    this.recomputeVisibleBook();
    return true;
  }

  applyUpdate(update: ProjectXMarketDepthUpdate): boolean {
    return this.applyUpdateResult(update) === "applied";
  }

  applyUpdateResult(update: ProjectXMarketDepthUpdate): OrderBookUpdateResult {
    if (
      !this.matchesContract(update.contract_id) ||
      this.awaitingSnapshot ||
      (update.side !== "bid" && update.side !== "ask") ||
      !Number.isFinite(update.price) ||
      !Number.isFinite(update.size) ||
      update.size < 0 ||
      (update.sequence !== null && (!Number.isSafeInteger(update.sequence) || update.sequence < 0))
    ) {
      return "ignored";
    }
    if (update.sequence !== null && this.sequence !== null && update.sequence <= this.sequence) {
      return "ignored";
    }
    if (update.sequence !== null && this.sequence !== null && update.sequence !== this.sequence + 1) {
      this.awaitingSnapshot = true;
      this.replaceMeta({
        ...this.meta,
        connection: "reconnecting",
        message: `Market depth sequence gap (${this.sequence} to ${update.sequence}); waiting for a fresh snapshot.`,
      });
      return "gap";
    }
    const timestampMs = parseTimestampMs(update.timestamp);
    if (
      update.sequence === null &&
      timestampMs !== null &&
      this.lastTimestampMs !== null &&
      timestampMs < this.lastTimestampMs
    ) {
      return "ignored";
    }

    const levels = update.side === "bid" ? this.bids : this.asks;
    const previousSize = levels.get(update.price);
    if (update.size === 0) {
      levels.delete(update.price);
    } else {
      levels.set(update.price, update.size);
    }
    // Missing sequence metadata breaks the continuity chain. Do not compare a
    // later sequenced event against a value that may predate unseen deltas.
    this.sequence = update.sequence;
    if (timestampMs !== null && (this.lastTimestampMs === null || timestampMs > this.lastTimestampMs)) {
      this.lastTimestampMs = timestampMs;
    }

    const changed = update.size === 0 ? previousSize !== undefined : previousSize !== update.size;
    if (changed) {
      this.replaceMeta({ ...this.meta, hasDepth: this.bids.size > 0 || this.asks.size > 0 });
      this.recomputeVisibleBook();
    }
    return "applied";
  }

  getViewSnapshot(): OrderBookViewSnapshot {
    return {
      asks: this.askSlots.filter(isOrderBookLevel),
      bids: this.bidSlots.filter(isOrderBookLevel),
      meta: this.meta,
      spread: this.spread,
      sequence: this.sequence,
    };
  }

  private matchesContract(contractId: unknown): boolean {
    return (
      this.contractId !== null &&
      typeof contractId === "string" &&
      normalizeOrderBookContractId(contractId) === this.contractId
    );
  }

  private subscribeSlot(side: OrderBookSide, index: number, listener: StoreListener): () => void {
    const key = slotKey(side, index);
    let listeners = this.slotListeners.get(key);
    if (!listeners) {
      listeners = new Set();
      this.slotListeners.set(key, listeners);
    }
    listeners.add(listener);
    return () => {
      listeners?.delete(listener);
      if (listeners?.size === 0) {
        this.slotListeners.delete(key);
      }
    };
  }

  private replaceMeta(next: OrderBookMetaSnapshot): void {
    if (metaSnapshotsEqual(this.meta, next)) {
      return;
    }
    this.meta = next;
    notify(this.metaListeners);
  }

  private recomputeVisibleBook(): void {
    const limit = this.visibleLevelCount === "all" ? undefined : this.visibleLevelCount;
    const asks = [...this.asks.entries()]
      .sort(([leftPrice], [rightPrice]) => leftPrice - rightPrice)
      .slice(0, limit);
    const bids = [...this.bids.entries()]
      .sort(([leftPrice], [rightPrice]) => rightPrice - leftPrice)
      .slice(0, limit);
    const maximumSize = asks.concat(bids).reduce((maximum, [, size]) => Math.max(maximum, size), 0);

    const nextAskSlots = createSlots(asks, maximumSize);
    const nextBidSlots = createSlots(bids, maximumSize);
    this.replaceSlots("ask", this.askSlots, nextAskSlots);
    this.replaceSlots("bid", this.bidSlots, nextBidSlots);

    const nextTotals = {
      bidSize: bids.reduce((sum, [, size]) => sum + size, 0),
      askSize: asks.reduce((sum, [, size]) => sum + size, 0),
    };
    if (this.totals.bidSize !== nextTotals.bidSize || this.totals.askSize !== nextTotals.askSize) {
      this.totals = nextTotals;
      notify(this.totalsListeners);
    }

    const bestAsk = asks[0]?.[0] ?? null;
    const bestBid = bids[0]?.[0] ?? null;
    const nextSpread = {
      bestAsk,
      bestBid,
      spread: bestAsk !== null && bestBid !== null ? bestAsk - bestBid : null,
    };
    if (!spreadSnapshotsEqual(this.spread, nextSpread)) {
      this.spread = nextSpread;
      notify(this.spreadListeners);
    }

    const nextDepth = {
      bidCount: this.bids.size,
      askCount: this.asks.size,
      visibleBidCount: bids.length,
      visibleAskCount: asks.length,
    };
    if (this.depth.bidCount !== nextDepth.bidCount || this.depth.askCount !== nextDepth.askCount ||
        this.depth.visibleBidCount !== nextDepth.visibleBidCount || this.depth.visibleAskCount !== nextDepth.visibleAskCount) {
      this.depth = nextDepth;
      notify(this.depthListeners);
    }
  }

  private replaceSlots(
    side: OrderBookSide,
    current: Array<OrderBookLevelView | null>,
    next: Array<OrderBookLevelView | null>,
  ): void {
    const slotCount = Math.max(current.length, next.length);
    for (let index = 0; index < slotCount; index += 1) {
      const currentLevel = current[index] ?? null;
      const nextLevel = next[index] ?? null;
      if (levelSnapshotsEqual(currentLevel, nextLevel)) {
        continue;
      }
      current[index] = nextLevel;
      notify(this.slotListeners.get(slotKey(side, index)));
    }
    current.length = next.length;
  }
}

function levelsToMap(levels: ProjectXMarketDepthSnapshot["bids"]): Map<number, number> {
  const result = new Map<number, number>();
  for (const level of levels) {
    if (
      level === null ||
      typeof level !== "object" ||
      !Number.isFinite(level.price) ||
      !Number.isFinite(level.size) ||
      level.size < 0
    ) {
      continue;
    }
    if (level.size === 0) {
      result.delete(level.price);
    } else {
      result.set(level.price, level.size);
    }
  }
  return result;
}

function createSlots(entries: Array<[number, number]>, maximumSize: number): Array<OrderBookLevelView | null> {
  return entries.map(([price, size], index) => ({
    price,
    size,
    depthPercent: maximumSize > 0 ? (size / maximumSize) * 100 : 0,
    isBest: index === 0,
  }));
}

function parseTimestampMs(timestamp: string | null): number | null {
  if (!timestamp) {
    return null;
  }
  const parsed = Date.parse(timestamp);
  return Number.isFinite(parsed) ? parsed : null;
}

function isOrderBookLevel(value: OrderBookLevelView | null): value is OrderBookLevelView {
  return value !== null;
}

function subscribe(listeners: Set<StoreListener>, listener: StoreListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function notify(listeners: Set<StoreListener> | undefined): void {
  if (!listeners) {
    return;
  }
  for (const listener of [...listeners]) {
    listener();
  }
}

function slotKey(side: OrderBookSide, index: number): string {
  return `${side}:${index}`;
}

function levelSnapshotsEqual(left: OrderBookLevelView | null, right: OrderBookLevelView | null): boolean {
  return (
    left === right ||
    (left !== null &&
      right !== null &&
      left.price === right.price &&
      left.size === right.size &&
      left.depthPercent === right.depthPercent &&
      left.isBest === right.isBest)
  );
}

function metaSnapshotsEqual(left: OrderBookMetaSnapshot, right: OrderBookMetaSnapshot): boolean {
  return (
    left.contractId === right.contractId &&
    left.connection === right.connection &&
    left.message === right.message &&
    left.hasSnapshot === right.hasSnapshot &&
    left.hasDepth === right.hasDepth
  );
}

function spreadSnapshotsEqual(left: OrderBookSpreadSnapshot, right: OrderBookSpreadSnapshot): boolean {
  return left.bestAsk === right.bestAsk && left.bestBid === right.bestBid && left.spread === right.spread;
}
