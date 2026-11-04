import { describe, expect, it } from "vitest";
import { OrderBookStore } from "./orderBook";
import { frameAtTime, HEATMAP_HISTORY_MS, HEATMAP_LEVEL_LIMIT, HEATMAP_MAX_FRAMES,
  LiquidityHeatmapBuffer, quantityAtTick } from "./liquidityHeatmap";

const contract = "CON.F.US.MNQ.Z26";
const start = Date.parse("2026-11-03T15:00:00Z");
const stamp = (time: number) => new Date(time).toISOString();
const levels = (price: number, size = 10) => [{ price, size }];
function setup(tickSize = 0.25) {
  const store = new OrderBookStore(contract);
  store.setConnectionState({ contract_id: contract, state: "connected" });
  const snapshot = (sequence: number, time: number, reset = false) => store.applySnapshot({ contract_id: contract,
    sequence, timestamp: stamp(time), bids: levels(20000), asks: levels(20000.25), reset });
  snapshot(1, start);
  const buffer = new LiquidityHeatmapBuffer(contract, tickSize);
  const sample = (time: number) => { buffer.sample(store, time); return buffer.view(time); };
  const update = (sequence: number, time: number, price = 20000, size = 12, side: "bid" | "ask" = "bid") => store.applyUpdateResult({
    contract_id: contract, sequence, timestamp: stamp(time), price, size, side });
  return { store, buffer, snapshot, sample, update };
}

describe("live liquidity history", () => {
  it("does not lose samples to normal interval jitter", () => {
    const { sample } = setup();
    sample(start + 5);
    sample(start + 503); // 498ms elapsed, but a new display bucket.
    sample(start + 1002);
    expect(sample(start + 1504).frames).toHaveLength(4);
  });
  it("samples the full received book independent of ladder filters, applies removals and never invents zero cells", () => {
    const { store, sample, update } = setup();
    update(2, start, 19999.75, 33);
    store.setVisibleLevelCount(1);
    const first = sample(start).frames[0];
    expect(first.bids).toEqual(new Float64Array([80000, 10, 79999, 33]));
    update(3, start + 500, 19999.75, 0);
    const next = sample(start + 500).frames[1];
    expect(quantityAtTick(next.bids, 79999)).toBeNull();
    expect(quantityAtTick(first.bids, 79999)).toBe(33);
  });

  it("keeps resets even when a rebuilt book arrives between samples", () => {
    const { snapshot, sample } = setup();
    sample(start);
    snapshot(2, start + 100, true);
    snapshot(3, start + 200);
    expect(sample(start + 500).frames[1].gap).toMatch(/reset/);
    expect(sample(start + 1000).frames[2].gap).toBeNull();
  });

  it("preserves sequence gaps, snapshot coalescing, reconnects and new sequence epochs", () => {
    const { store, snapshot, sample, update } = setup();
    sample(start);
    expect(update(4, start + 100)).toBe("gap");
    snapshot(4, start + 200);
    store.setConnectionState({ contract_id: contract, state: "connected" });
    expect(sample(start + 500).frames[1].gap).toMatch(/gap/);
    snapshot(10, start + 750); // Slow-client coalescing is authoritative but loses intervening history.
    expect(sample(start + 1000).frames[2].gap).toMatch(/gap/);
    store.setConnectionState({ contract_id: contract, state: "disconnected" });
    store.setConnectionState({ contract_id: contract, state: "connected" });
    expect(update(11, start + 1100)).toBe("ignored");
    snapshot(1, start + 1200);
    expect(sample(start + 1500).frames[3].gap).toMatch(/gap/);
    expect(sample(start + 2000).frames[4].gap).toBeNull();
  });

  it("ignores duplicate / out-of-order deltas but accepts sequenced updates with older per-level provider times", () => {
    const { sample, update } = setup();
    sample(start);
    update(2, start + 500, 20000, 20);
    expect(update(1, start + 600, 20000, 999)).toBe("ignored");
    expect(update(2, start + 600, 20000, 999)).toBe("ignored");
    expect(update(3, start + 400, 19999.75, 30)).toBe("applied");
    const frame = sample(start + 500).frames[1];
    expect(frame.bids).toEqual(new Float64Array([80000, 20, 79999, 30]));
    expect(frame.sourceTime).toBe(start + 500);
  });

  it("keeps stale, future timestamps, disconnected and timer gaps unknown", () => {
    const { store, sample, snapshot } = setup();
    sample(start);
    expect(sample(start + 2500).frames[1].gap).toMatch(/Stale/);
    snapshot(2, start + 10_000);
    expect(sample(start + 3000).frames[2].gap).toMatch(/Stale/);
    snapshot(3, start + 4000);
    expect(sample(start + 5000).frames[3].gap).toMatch(/gap/);
    store.setConnectionState({ contract_id: contract, state: "disconnected" });
    const view = sample(start + 5500);
    expect(view.status).toBe("Disconnected");
    expect(frameAtTime(view.frames, start + 1000)).toBeUndefined();
    expect(frameAtTime(view.frames, start - 1)).toBeUndefined();
  });

  it("clears history on contract change and refuses late callbacks from another instrument", () => {
    const { store, sample, snapshot } = setup();
    sample(start);
    store.selectContract("CON.F.US.MES.Z26");
    expect(snapshot(2, start + 500)).toBe(false);
    const view = sample(start + 500);
    expect(view.frames).toHaveLength(0);
    expect(view.recordingSince).toBeNull();
  });

  it("bounds frames, levels, sample rate and retained age while keeping paused snapshots immutable", () => {
    const { store, sample, snapshot } = setup();
    store.applySnapshot({ contract_id: contract, sequence: 2, timestamp: stamp(start),
      bids: Array.from({ length: 200 }, (_, i) => ({ price: 20000 - i * .25, size: i + 1 })),
      asks: Array.from({ length: 200 }, (_, i) => ({ price: 20000.25 + i * .25, size: i + 1 })) });
    const paused = sample(start);
    expect(paused.frames[0].bids.length).toBe(HEATMAP_LEVEL_LIMIT * 2);
    expect(paused.frames[0].asks.length).toBe(HEATMAP_LEVEL_LIMIT * 2);
    expect(paused.frames[0].limited).toBe(true);
    expect(sample(start + 200).frames).toHaveLength(1);
    let view = paused;
    for (let i = 1; i < HEATMAP_MAX_FRAMES + 20; i++) {
      snapshot(i + 2, start + i * 500); view = sample(start + i * 500);
    }
    expect(view.frames).toHaveLength(HEATMAP_MAX_FRAMES);
    expect(paused.frames).toHaveLength(1);
    expect(paused.frames[0].bids[1]).toBe(1);
    expect(view.frames[0].time).toBeGreaterThanOrEqual(view.now - HEATMAP_HISTORY_MS);
    expect(sample(view.now + HEATMAP_HISTORY_MS + 1).frames).toHaveLength(1);
  });

  it("uses actual tick metadata and rejects invalid grids, locked books and missing tick metadata", () => {
    const { store, sample } = setup(.1);
    expect(sample(start).frames[0].gap).toMatch(/tick size/);
    const unknown = new LiquidityHeatmapBuffer(contract, null);
    unknown.sample(store, start);
    expect(unknown.view(start).frames[0].bids.length).toBe(0);
    store.applySnapshot({ contract_id: contract, sequence: 2, timestamp: stamp(start + 500), bids: levels(100), asks: levels(100.1) });
    expect(sample(start + 500).frames[1].asks[0]).toBe(1001);
    store.applySnapshot({ contract_id: contract, sequence: 3, timestamp: stamp(start + 1000), bids: levels(100), asks: levels(100) });
    expect(sample(start + 1000).frames[2].gap).toMatch(/locked/);
  });

  it("shows one-sided depth explicitly without inventing a midpoint or the other side", () => {
    const { store, sample } = setup();
    store.applySnapshot({ contract_id: contract, sequence: 2, timestamp: stamp(start), bids: levels(20000), asks: [] });
    const view = sample(start);
    expect(view.status).toMatch(/one side missing/);
    expect(view.frames[0].bestAsk).toBeNull();
    expect(view.frames[0].asks.length).toBe(0);
  });
});
