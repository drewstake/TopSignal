import { HEATMAP_SAMPLE_MS, type LiquidityFrame } from "./liquidityHeatmap";
import { INT32_NONE, isCovered, type HeatmapGap, type HistoryTile } from "./heatmapHistory";
import type { TradeBubble } from "./heatmapTrades";

const NY = "America/New_York";
const timeFormat = new Intl.DateTimeFormat("en-US", { timeZone: NY, hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
const minuteFormat = new Intl.DateTimeFormat("en-US", { timeZone: NY, hour12: false, hour: "2-digit", minute: "2-digit" });
export const heatmapTime = (time: number) => timeFormat.format(time);
export const heatmapMinute = (time: number) => minuteFormat.format(time);
export const heatmapPrice = (price: number, tickSize = 0.25) => price.toLocaleString("en-US", {
  minimumFractionDigits: decimals(tickSize), maximumFractionDigits: Math.max(decimals(tickSize), 2) });
function decimals(tickSize: number) {
  const text = String(tickSize);
  return text.includes(".") ? text.split(".")[1].length : 0;
}

/** Shared heat gradient for both sides (dark blue → cyan → cream → orange → red). */
const STOPS: Array<[number, [number, number, number]]> = [
  [0, [18, 44, 82]], [0.18, [27, 86, 140]], [0.36, [36, 140, 170]], [0.55, [126, 206, 182]],
  [0.72, [240, 228, 140]], [0.86, [247, 160, 58]], [1, [236, 64, 44]],
];
export const HEAT_GRADIENT_CSS = `linear-gradient(90deg, ${STOPS.map(([p, c]) => `rgb(${c.join(",")}) ${p * 100}%`).join(", ")})`;
const BACKGROUND = [10, 14, 21] as const;
function gradient(position: number): [number, number, number] {
  for (let i = 1; i < STOPS.length; i++) {
    const [p1, c1] = STOPS[i];
    if (position <= p1) {
      const [p0, c0] = STOPS[i - 1];
      const mix = (position - p0) / (p1 - p0);
      return [0, 1, 2].map(k => Math.round(c0[k] + (c1[k] - c0[k]) * mix)) as [number, number, number];
    }
  }
  return STOPS.at(-1)![1];
}
/** Little-endian RGBA lookup table: index = round(min(1, q / saturation) * 255). */
const LUT = (() => {
  const lut = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    const [r, g, b] = gradient(i / 255);
    lut[i] = (255 << 24) | (b << 16) | (g << 8) | r;
  }
  return lut;
})();
const EMPTY = (255 << 24) | (BACKGROUND[2] << 16) | (BACKGROUND[1] << 8) | BACKGROUND[0];

/** Color position for a quantity: linear in contracts up to an explicit saturation threshold. */
export const heatPosition = (quantity: number, saturation: number) => Math.min(1, Math.max(0, quantity) / Math.max(1e-9, saturation));
export function heatmapColor(quantity: number, saturation: number): string {
  const [r, g, b] = gradient(heatPosition(quantity, saturation));
  return `rgb(${r},${g},${b})`;
}

export interface HeatmapRenderInput {
  width: number; height: number;
  start: number; end: number; now: number;
  lowTick: number; highTick: number; tickSize: number;
  history: readonly HistoryTile[];
  live: readonly LiquidityFrame[];
  coverage: ReadonlyArray<readonly [number, number]>;
  /** Recorded time whose tiles are loaded; live samples are hidden only there. */
  historyShown?: ReadonlyArray<readonly [number, number]>;
  gaps: readonly HeatmapGap[];
  historyThrough: number | null;
  sessionStart: number | null;
  liveTrades: readonly TradeBubble[];
  showTrades: boolean; minTradeVolume: number; bubbleScale: number;
  saturation: number;
  profile: boolean;
  profileTime: number | null;
}

export interface BubbleHit { x: number; y: number; radius: number; time: number; price: number; side: "buy" | "sell" | "unknown"; volume: number; prints: number; groupMs: number }
export interface HeatmapGeometry {
  left: number; right: number; top: number; bottom: number;
  start: number; end: number; lowTick: number; highTick: number; tickSize: number;
  bubbles: BubbleHit[]; bubblesCapped: boolean; bubbleMinVolume: number | null; profileLeft: number | null; drawMs: number;
}

const SIDE_NAMES = ["unknown", "buy", "sell"] as const;
const MAX_DRAWN_BUBBLES = 6_000;

/** Axis font and width sized to the widest price label, so labels never clip on phones. */
export function heatmapLayout(width: number, height: number, profile: boolean, widestLabel = "00,000.00") {
  const profileWidth = profile && width >= 720 ? 96 : 0;
  const charWidth = width < 520 ? 6.1 : 6.7;
  const axis = Math.ceil(widestLabel.length * charWidth + 14);
  return { left: 6, top: 6, right: Math.max(60, width - axis - profileWidth), bottom: Math.max(60, height - 24), profileWidth, axis };
}

/** Live frames are shown only where the recorder has no coverage (newest tail or a recorder outage). */
export const liveVisible = (input: Pick<HeatmapRenderInput, "coverage" | "historyShown">, time: number) =>
  !isCovered(input.historyShown ?? input.coverage, time);

interface Column { start: number; span: number; ticks: ArrayLike<number>; qty: ArrayLike<number>; from: number; count: number }

/** Iterate every visible depth column (history frames/buckets, then uncovered live frames). */
export function visibleColumns(input: HeatmapRenderInput, visit: (column: Column) => void) {
  for (const tile of input.history) {
    const f = tile.frames;
    if (!f || tile.end < input.start || tile.start > input.end) continue;
    for (let i = 0; i < f.time.length; i++) {
      const t = f.time[i];
      if (t + f.span < input.start || t > input.end || !f.levelCount[i]) continue;
      visit({ start: t, span: f.span, ticks: f.tick, qty: f.qty, from: f.levelStart[i], count: f.levelCount[i] });
    }
  }
  for (const frame of input.live) {
    if (frame.gap || frame.time + HEATMAP_SAMPLE_MS < input.start || frame.time > input.end || !liveVisible(input, frame.time)) continue;
    for (const levels of [frame.bids, frame.asks]) visit({ start: frame.time, span: HEATMAP_SAMPLE_MS, ...interleaved(levels) });
  }
}
function interleaved(levels: Float64Array) {
  const ticks = new Float64Array(levels.length / 2), qty = new Float64Array(levels.length / 2);
  for (let i = 0; i < ticks.length; i++) { ticks[i] = levels[i * 2]; qty[i] = levels[i * 2 + 1]; }
  return { ticks, qty, from: 0, count: ticks.length };
}

/** Max-merged quantity per CSS pixel. -1 means unobserved (never an inferred zero). */
export function buildHeatGrid(input: HeatmapRenderInput, width: number, height: number): Float32Array {
  const grid = new Float32Array(width * height).fill(-1);
  const duration = input.end - input.start;
  const range = input.highTick - input.lowTick;
  if (width <= 0 || height <= 0 || duration <= 0 || range <= 0) return grid;
  const xScale = width / duration, yScale = height / range;
  visibleColumns(input, column => {
    let x0 = Math.floor((column.start - input.start) * xScale);
    let x1 = Math.floor((column.start + column.span - input.start) * xScale);
    if (x1 <= x0) x1 = x0 + 1;
    x0 = Math.max(0, x0); x1 = Math.min(width, x1);
    if (x1 <= x0) return;
    for (let k = column.from; k < column.from + column.count; k++) {
      const tick = column.ticks[k];
      if (tick + 0.5 < input.lowTick || tick - 0.5 > input.highTick) continue;
      const q = column.qty[k];
      let y0 = Math.floor((input.highTick - tick - 0.5) * yScale);
      let y1 = Math.floor((input.highTick - tick + 0.5) * yScale);
      if (y1 <= y0) y1 = y0 + 1;
      y0 = Math.max(0, y0); y1 = Math.min(height, y1);
      for (let y = y0; y < y1; y++) {
        const row = y * width;
        for (let x = x0; x < x1; x++) if (grid[row + x] < q) grid[row + x] = q;
      }
    }
  });
  return grid;
}

export function gridToPixels(grid: Float32Array, saturation: number, target: Uint32Array) {
  const inverse = 1 / Math.max(1e-9, saturation);
  for (let i = 0; i < grid.length; i++) {
    const q = grid[i];
    target[i] = q < 0 ? EMPTY : LUT[Math.round(Math.min(1, q * inverse) * 255)];
  }
}

function niceStep(candidates: readonly number[], minimum: number) {
  return candidates.find(value => value >= minimum) ?? candidates.at(-1)!;
}
const TICK_STEPS = [1, 2, 4, 5, 10, 20, 40, 50, 100, 200, 400, 500, 1000, 2000, 4000, 5000, 10000];
const TIME_STEPS = [1e3, 2e3, 5e3, 1e4, 15e3, 3e4, 6e4, 12e4, 3e5, 6e5, 9e5, 18e5, 36e5, 72e5, 108e5, 216e5];

function newYorkOffset(time: number) {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: NY, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).formatToParts(time);
  const value = (name: string) => Number(parts.find(part => part.type === name)?.value);
  return Date.UTC(value("year"), value("month") - 1, value("day"), value("hour"), value("minute"), value("second")) - Math.floor(time / 1000) * 1000;
}

type Ctx = CanvasRenderingContext2D;
let scratch: HTMLCanvasElement | null = null;

/** Base layer: heat, gaps, quotes, bubbles, axes and book profile. */
export function drawLiquidityHeatmap(canvas: HTMLCanvasElement, input: HeatmapRenderInput): HeatmapGeometry {
  const started = typeof performance === "undefined" ? Date.now() : performance.now();
  const width = Math.max(240, Math.round(input.width)), height = Math.max(200, Math.round(input.height));
  const dpr = Math.min(typeof window === "undefined" ? 1 : window.devicePixelRatio || 1, 2);
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
  }
  const layout = heatmapLayout(width, height, input.profile, heatmapPrice(Math.max(Math.abs(input.highTick), Math.abs(input.lowTick)) * input.tickSize, input.tickSize));
  const fontSize = width < 520 ? 10 : 11;
  const g: HeatmapGeometry = { left: layout.left, right: layout.right, top: layout.top, bottom: layout.bottom,
    start: input.start, end: input.end, lowTick: input.lowTick, highTick: input.highTick, tickSize: input.tickSize,
    bubbles: [], bubblesCapped: false, bubbleMinVolume: null, profileLeft: layout.profileWidth ? layout.right + layout.axis : null, drawMs: 0 };
  const ctx = canvas.getContext("2d");
  if (!ctx) return g;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#070a10"; ctx.fillRect(0, 0, width, height);
  const plotW = g.right - g.left, plotH = g.bottom - g.top;
  const x = (time: number) => g.left + (time - g.start) / (g.end - g.start) * plotW;
  const y = (tick: number) => g.top + (g.highTick - tick) / (g.highTick - g.lowTick) * plotH;

  // Heat layer at CSS-pixel resolution, scaled without smoothing.
  const grid = buildHeatGrid(input, plotW, plotH);
  if (typeof ctx.createImageData === "function" && typeof document !== "undefined") {
    const image = ctx.createImageData(plotW, plotH);
    gridToPixels(grid, input.saturation, new Uint32Array(image.data.buffer));
    scratch ??= document.createElement("canvas");
    if (scratch.width !== plotW || scratch.height !== plotH) { scratch.width = plotW; scratch.height = plotH; }
    const off = scratch.getContext("2d");
    if (off) {
      off.putImageData(image, 0, 0);
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(scratch, g.left, g.top, plotW, plotH);
    }
  }

  ctx.save(); ctx.beginPath(); ctx.rect(g.left, g.top, plotW, plotH); ctx.clip();
  drawCoverage(ctx, input, g, x);
  drawQuotes(ctx, input, g, x, y);
  if (input.showTrades) drawBubbles(ctx, input, g, x, y);
  ctx.restore();
  drawAxes(ctx, input, g, x, y, width, height, fontSize);
  if (g.profileLeft !== null) drawProfile(ctx, input, g, y, layout.profileWidth);
  g.drawMs = Math.round(((typeof performance === "undefined" ? Date.now() : performance.now()) - started) * 10) / 10;
  return g;
}

function hatch(ctx: Ctx, left: number, right: number, top: number, bottom: number, color: string, spacing = 7) {
  if (right - left < 0.5) return;
  ctx.save(); ctx.beginPath(); ctx.rect(left, top, right - left, bottom - top); ctx.clip();
  ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.beginPath();
  for (let p = left - (bottom - top); p < right; p += spacing) { ctx.moveTo(p, bottom); ctx.lineTo(p + (bottom - top), top); }
  ctx.stroke(); ctx.restore();
}

/** Explicit gap bands, unrecorded time and the recorder's unsealed tail. */
function drawCoverage(ctx: Ctx, input: HeatmapRenderInput, g: HeatmapGeometry, x: (t: number) => number) {
  const from = Math.max(g.start, input.sessionStart ?? g.start), to = Math.min(g.end, input.now);
  if (input.sessionStart !== null && input.sessionStart > g.start) {
    ctx.fillStyle = "#0c0f15"; ctx.fillRect(g.left, g.top, x(input.sessionStart) - g.left, g.bottom - g.top);
  }
  // Uncovered = within session, outside recorder coverage and without live samples.
  const live: Array<[number, number]> = [];
  for (const frame of input.live) {
    const last = live.at(-1);
    if (last && frame.time - last[1] <= HEATMAP_SAMPLE_MS * 2) last[1] = frame.time + HEATMAP_SAMPLE_MS;
    else live.push([frame.time, frame.time + HEATMAP_SAMPLE_MS]);
  }
  const covered = [...input.coverage.map(([s, e]) => [s, e] as [number, number]), ...live].sort((a, b) => a[0] - b[0]);
  let cursor = from;
  const holes: Array<[number, number]> = [];
  for (const [s, e] of covered) {
    if (e <= cursor) continue;
    if (s > cursor) holes.push([cursor, Math.min(s, to)]);
    cursor = Math.max(cursor, e);
    if (cursor >= to) break;
  }
  if (cursor < to) holes.push([cursor, to]);
  const through = input.historyThrough;
  for (const [s, e] of holes) {
    if (e - s < 400) continue;
    const pending = through !== null && s >= through - 1000 && input.now - through < 5 * 60_000;
    ctx.fillStyle = pending ? "rgba(70,110,170,0.10)" : "rgba(120,130,145,0.08)";
    ctx.fillRect(x(s), g.top, x(e) - x(s), g.bottom - g.top);
    hatch(ctx, x(s), x(e), g.top, g.bottom, pending ? "rgba(110,150,210,0.22)" : "rgba(150,160,175,0.18)");
  }
  const amber = (s: number, e: number) => {
    if (e < g.start || s > g.end) return;
    const left = x(Math.max(s, g.start)), right = Math.max(left + 1, x(Math.min(e, g.end)));
    ctx.fillStyle = "rgba(230,160,60,0.13)"; ctx.fillRect(left, g.top, right - left, g.bottom - g.top);
    ctx.fillStyle = "rgba(240,170,70,0.85)"; ctx.fillRect(left, g.top, right - left, 3);
  };
  for (const gap of input.gaps) amber(gap.start, gap.end);
  for (const frame of input.live) if (frame.gap && liveVisible(input, frame.time)) amber(frame.time, frame.time + HEATMAP_SAMPLE_MS);
}

function drawQuotes(ctx: Ctx, input: HeatmapRenderInput, g: HeatmapGeometry, x: (t: number) => number, y: (tick: number) => number) {
  for (const [side, color] of [["bid", "#3fe0b2"], ["ask", "#ff7a8c"]] as const) {
    ctx.beginPath();
    for (const tile of input.history) {
      const f = tile.frames;
      if (!f || tile.end < g.start || tile.start > g.end) continue;
      const ticks = side === "bid" ? f.bestBid : f.bestAsk;
      let previous: number | null = null, previousEnd = -Infinity;
      for (let i = 0; i < f.time.length; i++) {
        const t = f.time[i], tick = ticks[i];
        if (t + f.span < g.start || t > g.end || tick === INT32_NONE || f.valid[i] === 0) { previous = null; continue; }
        if (previous !== null && t - previousEnd <= 1) { ctx.lineTo(x(t), y(previous)); ctx.lineTo(x(t), y(tick)); }
        else ctx.moveTo(x(t), y(tick));
        ctx.lineTo(x(t + f.span), y(tick));
        previous = tick; previousEnd = t + f.span;
      }
    }
    let previous: LiquidityFrame | undefined;
    for (const frame of input.live) {
      const price = side === "bid" ? frame.bestBid : frame.bestAsk;
      if (frame.gap || price === null || frame.time + HEATMAP_SAMPLE_MS < g.start || frame.time > g.end || !liveVisible(input, frame.time)) {
        previous = undefined; continue;
      }
      const tick = price / input.tickSize;
      const prior = previous ? (side === "bid" ? previous.bestBid : previous.bestAsk) : null;
      if (previous && prior !== null && previous.epoch === frame.epoch && frame.time - previous.time <= HEATMAP_SAMPLE_MS + 50) {
        ctx.lineTo(x(frame.time), y(prior / input.tickSize)); ctx.lineTo(x(frame.time), y(tick));
      } else ctx.moveTo(x(frame.time), y(tick));
      ctx.lineTo(x(frame.time + HEATMAP_SAMPLE_MS), y(tick));
      previous = frame;
    }
    // Dark halo keeps the quote lines readable over bright heat.
    ctx.lineJoin = "round";
    ctx.strokeStyle = "rgba(4,7,12,0.85)"; ctx.lineWidth = 4; ctx.stroke();
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke();
  }
}

export const tradeBubbleRadius = (volume: number, scale: number) => Math.min(22, Math.max(2.5, Math.sqrt(volume) * 1.9)) * scale;
const BUBBLE_COLORS = { buy: "#34e3a2", sell: "#ff5c7c", unknown: "#c3ccd8" } as const;

function drawBubbles(ctx: Ctx, input: HeatmapRenderInput, g: HeatmapGeometry, x: (t: number) => number, y: (tick: number) => number) {
  const items: BubbleHit[] = [];
  const add = (time: number, tick: number, side: BubbleHit["side"], volume: number, prints: number, groupMs: number) => {
    if (volume < input.minTradeVolume || tick + 0.5 < g.lowTick || tick - 0.5 > g.highTick) return;
    // Coarse groups sit at their bucket center; fine groups at the earliest print.
    const at = groupMs > 1000 ? Math.floor(time / groupMs) * groupMs + groupMs / 2 : time;
    if (at < g.start || at > g.end) return;
    // Summed 5 s / 60 s groups use a flatter size curve so they do not bury the heat.
    const scale = input.bubbleScale * (input.width < 640 ? 0.7 : 1);  // Smaller marks on phone-width charts.
    const radius = groupMs > 1000 ? Math.min(15, Math.max(2.5, Math.sqrt(volume) * 1.05)) * scale : tradeBubbleRadius(volume, scale);
    items.push({ x: x(at), y: y(tick), radius, time, price: tick * input.tickSize, side, volume, prints, groupMs });
  };
  for (const tile of input.history) {
    const t = tile.trades;
    if (!t || tile.end < g.start || tile.start > g.end) continue;
    for (let i = 0; i < t.time.length; i++) add(t.time[i], t.tick[i], SIDE_NAMES[t.side[i]] ?? "unknown", t.volume[i], t.prints[i], t.groupMs);
  }
  for (const bubble of input.liveTrades) {
    if (liveVisible(input, bubble.time)) add(bubble.time, Math.round(bubble.price / input.tickSize), bubble.side, bubble.volume, bubble.prints, 250);
  }
  // Zoomed out, keep only the largest prints (about one per 4 px of width) so the map stays readable.
  const limit = g.end - g.start > 10 * 60_000 ? Math.max(100, Math.round((g.right - g.left) / 4)) : MAX_DRAWN_BUBBLES;
  if (items.length > limit) {
    items.sort((a, b) => b.volume - a.volume);
    items.length = limit;
    g.bubblesCapped = true;
    g.bubbleMinVolume = items.at(-1)!.volume;
  }
  items.sort((a, b) => a.volume - b.volume);  // Large prints on top.
  ctx.font = "600 10px ui-monospace, SFMono-Regular, Menlo, monospace"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  for (const item of items) {
    const color = BUBBLE_COLORS[item.side];
    ctx.beginPath(); ctx.arc(item.x, item.y, item.radius, 0, Math.PI * 2);
    ctx.fillStyle = `${color}99`; ctx.fill();
    ctx.lineWidth = 1.5; ctx.strokeStyle = "#05080d"; ctx.stroke();
    ctx.lineWidth = 1; ctx.strokeStyle = color; ctx.stroke();
    if (item.radius >= 10) {
      ctx.fillStyle = "#061018";
      ctx.fillText(item.volume >= 1000 ? `${(item.volume / 1000).toFixed(1)}k` : String(Math.round(item.volume)), item.x, item.y + 0.5);
    }
  }
  ctx.textBaseline = "alphabetic";
  g.bubbles = items;
}

function drawAxes(ctx: Ctx, input: HeatmapRenderInput, g: HeatmapGeometry, x: (t: number) => number, y: (tick: number) => number,
  width: number, height: number, fontSize: number) {
  const plotH = g.bottom - g.top, plotW = g.right - g.left;
  ctx.font = `${fontSize}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  // Price labels: nice tick multiples at least 22 px apart.
  const pxPerTick = plotH / (g.highTick - g.lowTick);
  const step = niceStep(TICK_STEPS, 22 / pxPerTick);
  ctx.textAlign = "left"; ctx.textBaseline = "middle";
  for (let tick = Math.ceil(g.lowTick / step) * step; tick <= g.highTick; tick += step) {
    const py = Math.round(y(tick)) + 0.5;
    ctx.strokeStyle = "rgba(160,175,195,0.10)"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(g.left, py); ctx.lineTo(g.right, py); ctx.stroke();
    ctx.fillStyle = "#b6c2d1"; ctx.fillText(heatmapPrice(tick * input.tickSize, input.tickSize), g.right + 7, py);
  }
  // Time labels in New York time, aligned to local multiples of a nice step.
  const msPerPx = (g.end - g.start) / plotW;
  const timeStep = niceStep(TIME_STEPS, (width < 520 ? 74 : 96) * msPerPx);
  const offset = newYorkOffset(g.start);
  ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
  for (let t = Math.ceil((g.start + offset) / timeStep) * timeStep - offset; t <= g.end; t += timeStep) {
    const px = Math.round(x(t)) + 0.5;
    ctx.strokeStyle = "rgba(160,175,195,0.08)";
    ctx.beginPath(); ctx.moveTo(px, g.top); ctx.lineTo(px, g.bottom); ctx.stroke();
    if (px < g.left + 26 || px > g.right - 26) continue;
    ctx.fillStyle = "#b6c2d1"; ctx.fillText(timeStep < 60_000 ? heatmapTime(t) : heatmapMinute(t), px, height - 7);
  }
  if (input.sessionStart !== null && input.sessionStart > g.start && input.sessionStart < g.end) {
    const px = Math.round(x(input.sessionStart)) + 0.5;
    ctx.strokeStyle = "rgba(200,210,225,0.55)"; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(px, g.top); ctx.lineTo(px, g.bottom); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = "#c9d3df"; ctx.textAlign = "left"; ctx.fillText("Session open", px + 4, g.top + 12);
  }
  ctx.strokeStyle = "rgba(160,175,195,0.25)"; ctx.strokeRect(g.left + 0.5, g.top + 0.5, plotW - 1, plotH - 1);
}

export interface ProfileLevel { tick: number; qty: number; side: number; seen: number | null }
/** Book at a moment: the live/raw frame or coarse bucket covering it. Never revives older depth across a gap. */
export function bookAt(input: HeatmapRenderInput, time: number): { levels: ProfileLevel[]; bucketMs: number; time: number } | null {
  if (liveVisible(input, time)) {
    for (let i = input.live.length - 1; i >= 0; i--) {
      const frame = input.live[i];
      if (frame.time <= time && time < frame.time + HEATMAP_SAMPLE_MS) {
        if (frame.gap) return null;
        const levels: ProfileLevel[] = [];
        for (const [data, side] of [[frame.bids, 1], [frame.asks, 2]] as const) {
          for (let k = 0; k < data.length; k += 2) levels.push({ tick: data[k], qty: data[k + 1], side, seen: null });
        }
        return { levels, bucketMs: HEATMAP_SAMPLE_MS, time: frame.time };
      }
    }
  }
  for (const tile of input.history) {
    const f = tile.frames;
    if (!f || time < tile.start - f.span || time > tile.end + f.span) continue;
    let low = 0, high = f.time.length - 1;
    while (low <= high) {
      const mid = (low + high) >> 1;
      if (f.time[mid] + f.span <= time) low = mid + 1;
      else if (f.time[mid] > time) high = mid - 1;
      else {
        const levels: ProfileLevel[] = [];
        for (let k = f.levelStart[mid]; k < f.levelStart[mid] + f.levelCount[mid]; k++) {
          levels.push({ tick: f.tick[k], qty: f.qty[k], side: f.side[k], seen: f.seen ? f.seen[k] : null });
        }
        return levels.length ? { levels, bucketMs: f.span, time: f.time[mid] } : null;
      }
    }
  }
  return null;
}

function drawProfile(ctx: Ctx, input: HeatmapRenderInput, g: HeatmapGeometry, y: (tick: number) => number, width: number) {
  const left = g.profileLeft!;
  const at = input.profileTime ?? Math.min(g.end, input.now) - 1;
  const book = bookAt(input, at);
  ctx.font = "10px ui-monospace, SFMono-Regular, Menlo, monospace"; ctx.textAlign = "left"; ctx.fillStyle = "#93a1b3";
  ctx.fillText(book ? `BOOK ${heatmapTime(book.time)}` : "BOOK", left, g.top + 10);
  ctx.save(); ctx.beginPath(); ctx.rect(left, g.top + 16, width - 4, g.bottom - g.top - 16); ctx.clip();
  if (!book) {
    ctx.fillText("No sample", left, g.top + 30);
  } else {
    const visible = book.levels.filter(level => level.tick >= g.lowTick - 1 && level.tick <= g.highTick + 1);
    const maximum = Math.max(1, ...visible.map(level => level.qty));
    const rowHeight = Math.max(1, (g.bottom - g.top) / (g.highTick - g.lowTick) - 0.5);
    for (const level of visible) {
      ctx.fillStyle = level.side === 1 ? "#2fb592" : level.side === 2 ? "#d65d72" : "#8b97a8";
      ctx.fillRect(left, y(level.tick) - rowHeight / 2, (width - 10) * level.qty / maximum, rowHeight);
    }
    if (book.bucketMs > HEATMAP_SAMPLE_MS) { ctx.fillStyle = "#93a1b3"; ctx.fillText(`avg ${book.bucketMs / 1000}s`, left, g.bottom - 4); }
  }
  ctx.restore();
}

export interface InspectResult {
  time: number; tick: number; price: number;
  bubble: BubbleHit | null;
  bid: number | null; ask: number | null; bucketMs: number; samples: number | null;
  /** Coarse buckets: samples in which this price was observed, and whether both sides were seen. */
  observed: number | null; mixed: boolean;
  state: "observed" | "gap" | "not_recorded" | "pending" | "future" | "before_session";
  gapReason: string | null;
}

/** Details for a plot position. Uses the same sources and precedence as drawing. */
export function inspectHeatmap(input: HeatmapRenderInput, g: HeatmapGeometry, px: number, py: number): InspectResult | null {
  if (px < g.left || px > g.right || py < g.top || py > g.bottom) return null;
  const time = g.start + (px - g.left) / (g.right - g.left) * (g.end - g.start);
  const tick = Math.round(g.highTick - (py - g.top) / (g.bottom - g.top) * (g.highTick - g.lowTick));
  let bubble: BubbleHit | null = null;
  for (let i = g.bubbles.length - 1; i >= 0; i--) {
    const b = g.bubbles[i];
    if (Math.hypot(px - b.x, py - b.y) <= Math.max(6, b.radius)) { bubble = b; break; }
  }
  return { ...inspectPoint(input, time, tick), bubble };
}

/** Details for a moment and price tick (pinned points, keyboard cursor). */
export function inspectPoint(input: HeatmapRenderInput, time: number, tick: number): InspectResult {
  const result: InspectResult = { time, tick, price: tick * input.tickSize, bubble: null, bid: null, ask: null,
    bucketMs: HEATMAP_SAMPLE_MS, samples: null, observed: null, mixed: false, state: "observed", gapReason: null };
  if (time > input.now) return { ...result, state: "future" };
  if (input.sessionStart !== null && time < input.sessionStart) return { ...result, state: "before_session" };
  const gap = input.gaps.find(item => item.start <= time && time < item.end);
  const liveGap = liveVisible(input, time) ? input.live.find(frame => frame.gap && frame.time <= time && time < frame.time + HEATMAP_SAMPLE_MS) : undefined;
  if (gap || liveGap) return { ...result, state: "gap", gapReason: gap?.reason ?? liveGap!.gap };
  const book = bookAt(input, time);
  if (!book) {
    const recorded = isCovered(input.coverage, time);
    const pending = !recorded && input.historyThrough !== null && time >= input.historyThrough && input.now - input.historyThrough < 5 * 60_000;
    return { ...result, state: recorded ? "observed" : pending ? "pending" : "not_recorded" };
  }
  for (const level of book.levels) {
    if (level.tick !== tick) continue;
    result.observed = level.seen;
    // Price moved through this level within the bucket: one mean covers both sides.
    if (level.side === 3) result.mixed = true;
    if (level.side & 1) result.bid = level.qty;
    if (level.side & 2) result.ask = level.qty;
  }
  let samples: number | null = null;
  if (book.bucketMs > HEATMAP_SAMPLE_MS) {
    for (const tile of input.history) {
      const f = tile.frames;
      if (!f) continue;
      const index = f.time.indexOf(book.time);
      if (index >= 0) { samples = f.valid[index]; break; }
    }
  }
  return { ...result, bucketMs: book.bucketMs, samples };
}

/** Overlay layer: crosshair, axis tags and pinned marker. Cheap to redraw on every pointer move. */
export function drawHeatmapOverlay(canvas: HTMLCanvasElement, g: HeatmapGeometry | null, width: number, height: number,
  cursor: { x: number; y: number } | null, pinned: { time: number; tick: number } | null, tickSize: number) {
  const dpr = Math.min(typeof window === "undefined" ? 1 : window.devicePixelRatio || 1, 2);
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
  }
  const ctx = canvas.getContext("2d");
  if (!ctx || !g) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const toX = (time: number) => g.left + (time - g.start) / (g.end - g.start) * (g.right - g.left);
  const toY = (tick: number) => g.top + (g.highTick - tick) / (g.highTick - g.lowTick) * (g.bottom - g.top);
  if (pinned) {
    const px = toX(pinned.time), py = toY(pinned.tick);
    if (px >= g.left && px <= g.right && py >= g.top && py <= g.bottom) {
      ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(px, py, 6, 0, Math.PI * 2); ctx.stroke();
      ctx.strokeStyle = "#0a0e14"; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(px, py, 8, 0, Math.PI * 2); ctx.stroke();
    }
  }
  if (!cursor || cursor.x < g.left || cursor.x > g.right || cursor.y < g.top || cursor.y > g.bottom) return;
  const time = g.start + (cursor.x - g.left) / (g.right - g.left) * (g.end - g.start);
  const tick = Math.round(g.highTick - (cursor.y - g.top) / (g.bottom - g.top) * (g.highTick - g.lowTick));
  const py = Math.round(toY(tick)) + 0.5, px = Math.round(cursor.x) + 0.5;
  ctx.strokeStyle = "rgba(235,240,248,0.55)"; ctx.lineWidth = 1; ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(g.left, py); ctx.lineTo(g.right, py); ctx.moveTo(px, g.top); ctx.lineTo(px, g.bottom); ctx.stroke();
  ctx.setLineDash([]);
  ctx.font = "600 11px ui-monospace, SFMono-Regular, Menlo, monospace"; ctx.textBaseline = "middle";
  const price = heatmapPrice(tick * tickSize, tickSize);
  const priceWidth = ctx.measureText(price).width + 10;
  ctx.fillStyle = "#e9eef5"; ctx.fillRect(g.right + 2, py - 9, priceWidth, 18);
  ctx.fillStyle = "#0a0e14"; ctx.textAlign = "left"; ctx.fillText(price, g.right + 7, py);
  const label = heatmapTime(time);
  const labelWidth = ctx.measureText(label).width + 12;
  const lx = Math.min(g.right - labelWidth / 2, Math.max(g.left + labelWidth / 2, px));
  ctx.fillStyle = "#e9eef5"; ctx.fillRect(lx - labelWidth / 2, g.bottom + 3, labelWidth, 18);
  ctx.fillStyle = "#0a0e14"; ctx.textAlign = "center"; ctx.fillText(label, lx, g.bottom + 12);
}

/** Observed best bid/ask extent (ticks) inside a time window, from any loaded source. */
export function quoteExtent(input: Pick<HeatmapRenderInput, "history" | "live" | "tickSize">, start: number, end: number,
  extra: readonly HistoryTile[] = []): { low: number; high: number } | null {
  let low = Infinity, high = -Infinity;
  for (const tile of [...input.history, ...extra]) {
    const f = tile.frames;
    if (!f) continue;
    for (let i = 0; i < f.time.length; i++) {
      if (f.time[i] + f.span < start || f.time[i] > end || f.valid[i] === 0) continue;
      if (f.bestBid[i] !== INT32_NONE) { low = Math.min(low, f.bestBid[i]); high = Math.max(high, f.bestBid[i]); }
      if (f.bestAsk[i] !== INT32_NONE) { low = Math.min(low, f.bestAsk[i]); high = Math.max(high, f.bestAsk[i]); }
    }
  }
  for (const frame of input.live) {
    if (frame.gap || frame.time < start || frame.time > end) continue;
    for (const price of [frame.bestBid, frame.bestAsk]) {
      if (price === null) continue;
      low = Math.min(low, price / input.tickSize); high = Math.max(high, price / input.tickSize);
    }
  }
  return Number.isFinite(low) ? { low, high } : null;
}
