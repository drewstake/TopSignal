import { describe, expect, it, vi } from "vitest";

import type {
  ProjectXMarketDepthSnapshot,
  ProjectXMarketDepthState,
  ProjectXMarketDepthUpdate,
} from "../../lib/types";
import { OrderBookStore } from "./orderBook";

const CONTRACT = "CON.F.US.MNQ.U26";

describe("OrderBookStore", () => {
  it("sorts asks ascending and bids descending from an authoritative snapshot", () => {
    const store = new OrderBookStore(CONTRACT, 10);

    expect(store.applySnapshot(snapshot({
      asks: [level(102, 4), level(100.5, 7), level(101, 3)],
      bids: [level(98, 2), level(100, 5), level(99, 6)],
    }))).toBe(true);

    const view = store.getViewSnapshot();
    expect(view.asks.map(({ price }) => price)).toEqual([100.5, 101, 102]);
    expect(view.bids.map(({ price }) => price)).toEqual([100, 99, 98]);
    expect(view.asks[0]?.isBest).toBe(true);
    expect(view.bids[0]?.isBest).toBe(true);
    expect(view.spread).toEqual({ bestAsk: 100.5, bestBid: 100, spread: 0.5 });
  });

  it("calculates proportional aggregate-depth bars across visible levels", () => {
    const store = new OrderBookStore(CONTRACT, 10);
    store.applySnapshot(snapshot({
      asks: [level(101, 5), level(102, 10)],
      bids: [level(100, 20), level(99, 2)],
    }));

    expect(store.getViewSnapshot().asks.map(({ depthPercent }) => depthPercent)).toEqual([25, 50]);
    expect(store.getViewSnapshot().bids.map(({ depthPercent }) => depthPercent)).toEqual([100, 10]);
  });

  it("limits each sorted side to 10, 20, or 50 visible levels", () => {
    const asks = Array.from({ length: 60 }, (_, index) => level(101 + index, index + 1));
    const bids = Array.from({ length: 60 }, (_, index) => level(100 - index, index + 1));
    const store = new OrderBookStore(CONTRACT, 10);
    store.applySnapshot(snapshot({ asks, bids }));

    expect(store.getViewSnapshot().asks).toHaveLength(10);
    expect(store.getViewSnapshot().bids).toHaveLength(10);
    store.setVisibleLevelCount(20);
    expect(store.getViewSnapshot().asks).toHaveLength(20);
    expect(store.getViewSnapshot().bids).toHaveLength(20);
    store.setVisibleLevelCount(50);
    expect(store.getViewSnapshot().asks).toHaveLength(50);
    expect(store.getViewSnapshot().bids).toHaveLength(50);
  });

  it("applies one price-level delta without notifying or replacing unchanged row slots", () => {
    const store = new OrderBookStore(CONTRACT, 10);
    store.applySnapshot(snapshot({
      asks: [level(101, 5), level(102, 10)],
      bids: [level(100, 20), level(99, 2)],
    }));
    const unchangedBestAsk = store.getAskSlotSnapshot(0);
    const unchangedBestBid = store.getBidSlotSnapshot(0);
    const previousSecondAsk = store.getAskSlotSnapshot(1);
    const bestAskListener = vi.fn();
    const secondAskListener = vi.fn();
    const bestBidListener = vi.fn();
    const metaListener = vi.fn();
    const spreadListener = vi.fn();
    store.subscribeAskSlot(0, bestAskListener);
    store.subscribeAskSlot(1, secondAskListener);
    store.subscribeBidSlot(0, bestBidListener);
    store.subscribeMeta(metaListener);
    store.subscribeSpread(spreadListener);

    expect(store.applyUpdate(update({ sequence: 11, side: "ask", price: 102, size: 12 }))).toBe(true);

    expect(store.getAskSlotSnapshot(0)).toBe(unchangedBestAsk);
    expect(store.getBidSlotSnapshot(0)).toBe(unchangedBestBid);
    expect(store.getAskSlotSnapshot(1)).not.toBe(previousSecondAsk);
    expect(bestAskListener).not.toHaveBeenCalled();
    expect(secondAskListener).toHaveBeenCalledTimes(1);
    expect(bestBidListener).not.toHaveBeenCalled();
    expect(metaListener).not.toHaveBeenCalled();
    expect(spreadListener).not.toHaveBeenCalled();
  });

  it("removes zero-size levels and shifts the best price incrementally", () => {
    const store = new OrderBookStore(CONTRACT, 10);
    store.applySnapshot(snapshot({
      asks: [level(101, 5), level(102, 10)],
      bids: [level(100, 20), level(99, 2)],
    }));

    store.applyUpdate(update({ sequence: 11, side: "ask", price: 101, size: 0 }));

    expect(store.getViewSnapshot().asks.map(({ price }) => price)).toEqual([102]);
    expect(store.getViewSnapshot().spread).toEqual({ bestAsk: 102, bestBid: 100, spread: 2 });
  });

  it("clears and rebuilds on reset snapshots, including after reconnect", () => {
    const store = new OrderBookStore(CONTRACT, 10);
    store.applySnapshot(snapshot({ asks: [level(101, 5)], bids: [level(100, 6)] }));
    store.setConnectionState(state("reconnecting"));

    expect(store.applyUpdate(update({ sequence: 11, side: "bid", price: 99, size: 8 }))).toBe(false);
    expect(store.applySnapshot(snapshot({
      sequence: 1,
      reset: true,
      asks: [],
      bids: [],
    }))).toBe(true);
    expect(store.getViewSnapshot().asks).toEqual([]);
    expect(store.getMetaSnapshot().connection).toBe("reconnecting");
    store.setConnectionState(state("connected"));
    store.applyUpdate(update({ sequence: 2, side: "ask", price: 111, size: 3 }));
    store.applyUpdate(update({ sequence: 3, side: "bid", price: 110, size: 4 }));

    const view = store.getViewSnapshot();
    expect(view.asks.map(({ price }) => price)).toEqual([111]);
    expect(view.bids.map(({ price }) => price)).toEqual([110]);
    expect(view.sequence).toBe(3);
    expect(view.meta.connection).toBe("connected");
  });

  it("accepts an explicit connected reset even when its sequence restarts", () => {
    const store = new OrderBookStore(CONTRACT, 10);
    store.setConnectionState(state("connected"));
    store.applySnapshot(snapshot({ sequence: 50, asks: [level(101, 5)], bids: [level(100, 6)] }));

    expect(store.applySnapshot(snapshot({
      sequence: 1,
      reset: true,
      asks: [level(201, 2)],
      bids: [level(200, 3)],
    }))).toBe(true);
    expect(store.getViewSnapshot()).toMatchObject({
      sequence: 1,
      meta: { connection: "connected" },
      spread: { bestAsk: 201, bestBid: 200, spread: 1 },
    });
  });

  it("treats every well-formed snapshot as authoritative for a new sequence baseline", () => {
    const store = new OrderBookStore(CONTRACT, 10);
    store.setConnectionState(state("connected"));
    store.applySnapshot(snapshot({ sequence: 50, bids: [level(100, 6)] }));

    expect(store.applySnapshot(snapshot({
      sequence: 1,
      reset: false,
      timestamp: "2026-07-10T14:29:00Z",
      bids: [level(100, 7)],
    }))).toBe(true);
    expect(store.getViewSnapshot().sequence).toBe(1);
    expect(store.applyUpdateResult(update({ sequence: 2, side: "bid", price: 100, size: 8 }))).toBe("applied");
    expect(store.getBidSlotSnapshot(0)?.size).toBe(8);
  });

  it("clears on contract changes and rejects late messages from the old contract", () => {
    const store = new OrderBookStore(CONTRACT, 10);
    store.applySnapshot(snapshot({ asks: [level(101, 5)], bids: [level(100, 6)] }));

    expect(store.selectContract("CON.F.US.NQ.U26")).toBe(true);
    expect(store.getViewSnapshot().asks).toEqual([]);
    expect(store.getViewSnapshot().bids).toEqual([]);
    expect(store.getViewSnapshot().meta).toMatchObject({
      contractId: "CON.F.US.NQ.U26",
      connection: "loading",
      hasSnapshot: false,
    });
    expect(store.applySnapshot(snapshot({ sequence: 12 }))).toBe(false);
    expect(store.applyUpdate(update({ sequence: 13 }))).toBe(false);
  });

  it("rejects duplicate and out-of-order updates when sequence metadata is available", () => {
    const store = new OrderBookStore(CONTRACT, 10);
    store.applySnapshot(snapshot({ asks: [level(101, 5)], bids: [level(100, 6)] }));

    expect(store.applyUpdate(update({ sequence: 11, side: "bid", price: 100, size: 7 }))).toBe(true);
    expect(store.applyUpdate(update({ sequence: 11, side: "bid", price: 100, size: 99 }))).toBe(false);
    expect(store.applyUpdate(update({ sequence: 9, side: "bid", price: 100, size: 88 }))).toBe(false);
    expect(store.getBidSlotSnapshot(0)?.size).toBe(7);
  });

  it("requires a fresh authoritative snapshot after a forward sequence gap", () => {
    const store = new OrderBookStore(CONTRACT, 10);
    store.setConnectionState(state("connected"));
    store.applySnapshot(snapshot({ sequence: 10, bids: [level(100, 6)] }));

    expect(store.applyUpdateResult(update({ sequence: 12, side: "bid", price: 100, size: 99 }))).toBe("gap");
    expect(store.getBidSlotSnapshot(0)?.size).toBe(6);
    expect(store.getMetaSnapshot()).toMatchObject({
      connection: "reconnecting",
      message: expect.stringContaining("10 to 12"),
    });
    expect(store.applyUpdateResult(update({ sequence: 11, side: "bid", price: 100, size: 88 }))).toBe("ignored");

    expect(store.applySnapshot(snapshot({
      sequence: 1,
      reset: false,
      bids: [level(100, 7)],
    }))).toBe(true);
    expect(store.getBidSlotSnapshot(0)?.size).toBe(7);
    store.setConnectionState(state("connected"));
    expect(store.applyUpdateResult(update({ sequence: 2, side: "bid", price: 100, size: 8 }))).toBe("applied");
    expect(store.getBidSlotSnapshot(0)?.size).toBe(8);
  });

  it("drops continuity tracking when an update has no sequence metadata", () => {
    const store = new OrderBookStore(CONTRACT, 10);
    store.applySnapshot(snapshot({ sequence: 10 }));

    expect(store.applyUpdateResult(update({ sequence: null, side: "bid", price: 100, size: 7 }))).toBe("applied");
    expect(store.applyUpdateResult(update({ sequence: 12, side: "bid", price: 100, size: 8 }))).toBe("applied");
    expect(store.getViewSnapshot().sequence).toBe(12);
  });

  it("rejects stale unsequenced updates when timestamps are available", () => {
    const store = new OrderBookStore(CONTRACT, 10);
    store.applySnapshot(snapshot({ sequence: null, timestamp: "2026-07-10T14:30:00Z" }));

    expect(store.applyUpdate(update({
      sequence: null,
      timestamp: "2026-07-10T14:29:59Z",
      side: "bid",
      price: 100,
      size: 99,
    }))).toBe(false);
    expect(store.getBidSlotSnapshot(0)?.size).toBe(6);
  });

  it("exposes loading, connected, disconnected, reconnecting, and unavailable states", () => {
    const store = new OrderBookStore(CONTRACT);
    expect(store.getMetaSnapshot().connection).toBe("loading");

    store.setConnectionState(state("connected"));
    expect(store.getMetaSnapshot().connection).toBe("connected");
    store.setConnectionState(state("disconnected"));
    expect(store.getMetaSnapshot().connection).toBe("disconnected");
    store.setConnectionState(state("reconnecting"));
    expect(store.getMetaSnapshot().connection).toBe("reconnecting");
    store.setConnectionState(state("unavailable", "Depth disabled"));
    expect(store.getMetaSnapshot()).toMatchObject({ connection: "unavailable", message: "Depth disabled" });
  });
});

function snapshot(overrides: Partial<ProjectXMarketDepthSnapshot> = {}): ProjectXMarketDepthSnapshot {
  return {
    contract_id: CONTRACT,
    sequence: 10,
    timestamp: "2026-07-10T14:30:00Z",
    asks: [level(101, 5)],
    bids: [level(100, 6)],
    ...overrides,
  };
}

function update(overrides: Partial<ProjectXMarketDepthUpdate> = {}): ProjectXMarketDepthUpdate {
  return {
    contract_id: CONTRACT,
    sequence: 11,
    timestamp: "2026-07-10T14:30:01Z",
    side: "bid",
    price: 100,
    size: 7,
    ...overrides,
  };
}

function state(
  connectionState: ProjectXMarketDepthState["state"],
  message: string | null = null,
): ProjectXMarketDepthState {
  return { contract_id: CONTRACT, state: connectionState, message };
}

function level(price: number, size: number) {
  return { price, size };
}
