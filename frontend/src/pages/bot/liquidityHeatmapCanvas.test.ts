// @vitest-environment jsdom
import { expect, it, vi } from "vitest";
import { drawLiquidityHeatmap } from "./liquidityHeatmapCanvas";
import type { LiquidityFrame } from "./liquidityHeatmap";

const frame = (time: number, gap: string | null = null): LiquidityFrame => ({
  time, sourceTime: time, epoch: 1, bids: new Float64Array([80000, 20]), asks: new Float64Array([80001, 20]),
  bestBid: 20000, bestAsk: 20000.25, limited: false, gap,
});
function draw(frames: LiquidityFrame[], end: number, width = 1000, overlay: Partial<Parameters<typeof drawLiquidityHeatmap>[2]> = {}) {
  const cells: Array<{ color: string; x: number; width: number }> = [];
  const ctx = { fillStyle: "", strokeStyle: "", setTransform: vi.fn(), fillText: vi.fn(),
    beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), save: vi.fn(), restore: vi.fn(), rect: vi.fn(), clip: vi.fn(),
    arc: vi.fn(), fill: vi.fn(),
    fillRect(x: number, _y: number, width: number) { cells.push({ color: this.fillStyle, x, width }); },
  };
  const canvas = { clientWidth: width, clientHeight: 320, width: 0, height: 0, getContext: () => ctx } as unknown as HTMLCanvasElement;
  const geometry = drawLiquidityHeatmap(canvas, frames, { end, duration: 3000, center: 20000, tickSize: .25, priceTicks: 80, intensity: 1, ...overlay });
  return { cells, ctx, geometry };
}

it("uses the same heat color for equal bid/ask size and puts the price axis before the book", () => {
  const { cells, geometry } = draw([frame(1000)], 1250);
  const heat = cells.filter(cell => cell.color.startsWith("rgb("));
  expect(heat).toHaveLength(2);
  expect(heat[0].color).toBe(heat[1].color);
  expect(cells.find(cell => cell.color === "#36a58f")!.x).toBeGreaterThan(geometry.right);
});

it.each([[1500, [frame(1000)]], [1750, [frame(1000), frame(1500, "Disconnected")]]])(
  "does not show a stale profile at %s", (end, frames) => {
    const { cells, ctx } = draw(frames, end);
    expect(cells.some(cell => cell.color === "#36a58f" || cell.color === "#c65a69")).toBe(false);
    expect(ctx.fillText).toHaveBeenCalledWith("No sample", expect.any(Number), expect.any(Number));
  },
);

it("starts new bid/ask paths after a gap instead of connecting quotes across it", () => {
  const { ctx, geometry } = draw([frame(1000), frame(1500, "Disconnected"), frame(2000)], 2250);
  const x = geometry.left + (2000 - geometry.start) / 3000 * (geometry.right - geometry.left);
  const starts = ctx.moveTo.mock.calls.filter(([pathX]) => pathX === x);
  expect(starts).toHaveLength(2);
});

it("preserves plot space on phones by hiding the profile", () => {
  const { cells, geometry } = draw([frame(1000)], 1250, 350);
  expect(geometry.right - geometry.left).toBeGreaterThan(250);
  expect(cells.some(cell => cell.color === "#36a58f" || cell.color === "#c65a69")).toBe(false);
});

it("draws only visible qualifying trades and returns matching hover targets", () => {
  const trade = { key: "one", time: 1000, price: 20000, side: "buy" as const, volume: 25, prints: 3 };
  const { ctx, geometry } = draw([], 1250, 1000, { trades: [trade, { ...trade, volume: 1 },
    { ...trade, time: 2000 }, { ...trade, price: 21000 }, { ...trade, time: -2000 }], minTradeVolume: 5 });
  expect(ctx.arc).toHaveBeenCalledTimes(1);
  expect(geometry.bubbles).toHaveLength(1);
  expect(geometry.bubbles![0].trade).toEqual(trade);
  expect(ctx.arc.mock.calls[0].slice(0, 3)).toEqual([geometry.bubbles![0].x, geometry.bubbles![0].y, geometry.bubbles![0].radius]);
});

it("sizes bubble area by volume, with a maximum radius to keep the chart readable", () => {
  const trade = { key: "one", time: 1000, price: 20000, side: "unknown" as const, volume: 4, prints: 1 };
  const { geometry } = draw([], 1250, 1000, { trades: [trade, { ...trade, volume: 16 }, { ...trade, volume: 1e6 }] });
  const radii = geometry.bubbles!.map(b => b.radius);
  expect(radii[1] / radii[0]).toBeCloseTo(2);
  expect(radii[2]).toBe(18);
});
