// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { bookAt, buildHeatGrid, drawLiquidityHeatmap, gridToPixels, heatPosition, inspectPoint, quoteExtent,
  type HeatmapRenderInput } from "./liquidityHeatmapCanvas";
import type { LiquidityFrame } from "./liquidityHeatmap";
import { INT32_NONE, type HistoryTile } from "./heatmapHistory";

const T0 = Date.parse("2026-11-04T14:30:00Z");
const liveFrame = (time: number, gap: string | null = null, size = 20): LiquidityFrame => ({
  time, sourceTime: time, epoch: 1, bids: new Float64Array([80000, size]), asks: new Float64Array([80001, size]),
  bestBid: 20000, bestAsk: 20000.25, limited: false, gap,
});

/** Tile of raw 500 ms frames: each entry [time, bidTick, bidQty, askTick, askQty]. */
function tile(rows: Array<[number, number, number, number, number]>, span = 500): HistoryTile {
  const n = rows.length;
  const frames = {
    time: new Float64Array(rows.map(r => r[0])), span, levelStart: new Uint32Array(rows.map((_, i) => i * 2)),
    levelCount: new Uint16Array(n).fill(2), bidCount: new Uint16Array(n).fill(1),
    bestBid: new Int32Array(rows.map(r => r[1])), bestAsk: new Int32Array(rows.map(r => r[3])), valid: new Uint16Array(n).fill(span / 500),
    tick: new Int32Array(rows.flatMap(r => [r[1], r[3]])), qty: new Float32Array(rows.flatMap(r => [r[2], r[4]])),
    side: new Uint8Array(rows.flatMap(() => [1, 2])), seen: span > 500 ? new Uint16Array(rows.flatMap(() => [7, 3])) : null,
  };
  return { key: "t", tier: span === 500 ? 0 : 1, start: rows[0][0], end: rows.at(-1)![0] + span, frames, trades: null,
    tradesTruncated: false, tradesMinVolume: null, fetchedAt: 0, bytes: 0, through: rows.at(-1)![0] + span, deriverCurrent: true };
}

function input(overrides: Partial<HeatmapRenderInput> = {}): HeatmapRenderInput {
  return { width: 1000, height: 400, start: T0, end: T0 + 10_000, now: T0 + 10_000, lowTick: 79990, highTick: 80010, tickSize: 0.25,
    history: [], live: [], coverage: [], gaps: [], historyThrough: null, sessionStart: T0 - 3600_000, liveTrades: [],
    showTrades: true, minTradeVolume: 1, bubbleScale: 1, saturation: 25, profile: true, profileTime: null, ...overrides };
}

describe("heat grid", () => {
  it("marks unobserved cells -1 (never zero) and max-merges sources sharing a pixel", () => {
    const grid = buildHeatGrid(input({ history: [tile([[T0, 80000, 5, 80001, 7], [T0 + 500, 80000, 9, 80001, 3]])] }), 4, 20);
    expect(Math.min(...grid)).toBe(-1);
    // Both frames fall in the first pixel column at this scale: the larger size wins.
    const row = Math.floor((80010 - 80000 - 0.5) * 1);
    expect(grid[row * 4]).toBe(9);
  });

  it("uses the recorder for covered time and live samples only where it has no coverage", () => {
    const history = [tile([[T0, 80000, 5, 80001, 5]])];
    const live = [liveFrame(T0, null, 50), liveFrame(T0 + 5000, null, 40)];
    const grid = buildHeatGrid(input({ history, live, coverage: [[T0, T0 + 1000]] }), 1000, 20);
    const row = 9;  // tick 80000 for a 20-tick range at 20 px
    expect(grid[row * 1000 + 10]).toBe(5);     // recorded value wins inside coverage
    expect(grid[row * 1000 + 510]).toBe(40);   // live fills the unsealed tail
  });

  it("maps quantity linearly up to the saturation threshold", () => {
    expect(heatPosition(12.5, 25)).toBe(0.5);
    expect(heatPosition(80, 25)).toBe(1);
    const pixels = new Uint32Array(3);
    gridToPixels(new Float32Array([-1, 0, 1000]), 25, pixels);
    expect(pixels[0]).not.toBe(pixels[1]);
    expect(new Set(pixels).size).toBe(3);
  });
});

describe("inspection", () => {
  it("never revives an older book across a live gap", () => {
    const data = input({ live: [liveFrame(T0), liveFrame(T0 + 500, "Disconnected")] });
    expect(bookAt(data, T0 + 700)).toBeNull();
    expect(bookAt(data, T0 + 200)?.levels).toHaveLength(2);
  });

  it("reports coarse buckets as means with their sample count", () => {
    const data = input({ history: [tile([[T0, 80000, 7.5, 80001, 2]], 5000)], coverage: [[T0, T0 + 5000]] });
    const result = inspectPoint(data, T0 + 1000, 80000);
    expect(result).toMatchObject({ state: "observed", bid: 7.5, ask: null, bucketMs: 5000, samples: 10, observed: 7, mixed: false });
  });

  it("distinguishes gaps, unrecorded time, the unsealed tail and pre-session time", () => {
    const data = input({ coverage: [[T0, T0 + 2000]], gaps: [{ start: T0 + 1000, end: T0 + 1500, reason: "stale" }],
      historyThrough: T0 + 2000, history: [tile([[T0, 80000, 5, 80001, 5]])], now: T0 + 9000 });
    expect(inspectPoint(data, T0 + 1200, 80000)).toMatchObject({ state: "gap", gapReason: "stale" });
    expect(inspectPoint(data, T0 + 6000, 80000).state).toBe("pending");
    expect(inspectPoint({ ...data, now: T0 + 3600_000 }, T0 + 6000, 80000).state).toBe("not_recorded");
    expect(inspectPoint(data, T0 - 7200_000, 80000).state).toBe("before_session");
    expect(inspectPoint(data, T0 + 9500, 80000).state).toBe("future");
  });

  it("finds the observed quote extent across sources", () => {
    const data = input({ history: [tile([[T0, 79990, 1, 79991, 1], [T0 + 500, 80010, 1, 80012, 1]])], live: [liveFrame(T0 + 9000)] });
    expect(quoteExtent(data, T0, T0 + 10_000)).toEqual({ low: 79990, high: 80012 });
    expect(quoteExtent(data, T0 + 20_000, T0 + 30_000)).toBeNull();
  });
});

function drawWith(data: HeatmapRenderInput) {
  const calls: string[] = [];
  const ctx = new Proxy({ measureText: () => ({ width: 40 }), createImageData: undefined } as Record<string, unknown>, {
    get(target, key: string) {
      if (key in target) return target[key];
      return (...args: unknown[]) => { calls.push(`${key}:${args.map(a => typeof a === "number" ? Math.round(a) : a).join(",")}`); };
    },
    set(target, key: string, value) { target[key] = value; return true; },
  });
  const canvas = { width: 0, height: 0, getContext: () => ctx } as unknown as HTMLCanvasElement;
  return { geometry: drawLiquidityHeatmap(canvas, data), calls };
}

describe("drawing", () => {
  it("starts a new quote path after a gap instead of connecting across it", () => {
    const history = [tile([[T0, 80000, 5, 80001, 5], [T0 + 500, 80000, 5, 80001, 5]]), tile([[T0 + 2000, 80002, 5, 80003, 5]])];
    const { calls } = drawWith(input({ history }));
    const moves = calls.filter(call => call.startsWith("moveTo"));
    // Bid and ask each start twice: once per contiguous run.
    expect(moves.length).toBeGreaterThanOrEqual(4);
  });

  it("keeps only the largest prints when zoomed out", () => {
    const trades = { time: new Float64Array(2000).map((_, i) => T0 + i * 1000), tick: new Int32Array(2000).fill(80000),
      side: new Uint8Array(2000).fill(1), volume: new Float32Array(2000).map((_, i) => i + 1), prints: new Uint32Array(2000).fill(1), groupMs: 5000 };
    const history = [{ ...tile([[T0, 80000, 5, 80001, 5]], 5000), trades }];
    const { geometry } = drawWith(input({ history, start: T0, end: T0 + 3600_000, now: T0 + 3600_000 }));
    expect(geometry.bubblesCapped).toBe(true);
    expect(geometry.bubbles.length).toBeLessThanOrEqual(250);
    expect(Math.min(...geometry.bubbles.map(b => b.volume))).toBe(geometry.bubbleMinVolume);
    expect(Math.max(...geometry.bubbles.map(b => b.radius))).toBeLessThanOrEqual(15);
  });

  it("returns the geometry used for hit-testing and timing", () => {
    vi.spyOn(performance, "now").mockReturnValueOnce(0).mockReturnValueOnce(4.25);
    const { geometry } = drawWith(input());
    expect(geometry.drawMs).toBe(4.3);
    expect(geometry.right).toBeLessThan(1000 - 66);  // price axis then book profile on wide canvases
    expect(geometry.profileLeft).not.toBeNull();
  });

  it("treats a missing best price as a break", () => {
    const history = [tile([[T0, INT32_NONE, 0, 80001, 5], [T0 + 500, 80000, 5, 80001, 5]])];
    expect(() => drawWith(input({ history }))).not.toThrow();
  });
});
