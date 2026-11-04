import { frameAtTime, HEATMAP_SAMPLE_MS, type LiquidityFrame } from "./liquidityHeatmap";
import { tradeBubbleRadius, type TradeBubble } from "./heatmapTrades";

export const heatmapTime = (time: number) => new Date(time).toLocaleTimeString("en-US", {
  timeZone: "America/New_York", hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit",
});
export const heatmapPrice = (price: number) => price.toLocaleString("en-US", { maximumFractionDigits: 8 });

export interface HeatmapGeometry {
  left: number; right: number; top: number; bottom: number;
  start: number; end: number; low: number; high: number;
  bubbles?: Array<{ x: number; y: number; radius: number; trade: TradeBubble }>;
}

// Both sides use the same quantity scale. Side is conveyed by the quote lines
// and the current book, never by changing the meaning of a heatmap color.
const HEAT_COLORS = [[13, 23, 40], [26, 61, 102], [48, 127, 164],
  [168, 208, 204], [244, 229, 157], [247, 163, 57], [232, 69, 37]];
export function heatmapColor(quantity: number, intensity: number): string {
  const strength = Math.max(0, Math.min(1, Math.log1p(Math.max(0, quantity)) / Math.log1p(100) * intensity));
  const position = strength * (HEAT_COLORS.length - 1);
  const index = Math.min(HEAT_COLORS.length - 2, Math.floor(position));
  const mix = position - index;
  return `rgb(${HEAT_COLORS[index].map((channel, i) => Math.round(channel + (HEAT_COLORS[index + 1][i] - channel) * mix)).join(",")})`;
}

/** Depth surface with bounded sample cells, discontinuous quote traces and a
 * book profile at the displayed right edge. No interpolation across gaps. */
export function drawLiquidityHeatmap(canvas: HTMLCanvasElement, frames: readonly LiquidityFrame[], options: {
  end: number; duration: number; center: number | null; tickSize: number | null; priceTicks: number; intensity: number;
  trades?: readonly TradeBubble[]; minTradeVolume?: number; bubbleScale?: number;
}): HeatmapGeometry {
  const width = Math.max(240, Math.round(canvas.clientWidth));
  const height = Math.max(200, Math.round(canvas.clientHeight));
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
  }
  const { end, duration, center, tickSize, priceTicks, intensity } = options;
  const tick = tickSize ?? 1;
  const middle = Math.round((center ?? 0) / tick) * tick;
  const profileWidth = width >= 640 ? 88 : 0;
  const g: HeatmapGeometry = { left: 8, right: width - 82 - profileWidth, top: 24, bottom: height - 26,
    start: end - duration, end, low: middle - priceTicks * tick / 2, high: middle + priceTicks * tick / 2 };
  const ctx = canvas.getContext("2d");
  if (!ctx) return g;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#0b1018"; ctx.fillRect(0, 0, width, height);
  const x = (time: number) => g.left + (time - g.start) / duration * (g.right - g.left);
  const y = (price: number) => g.bottom - (price - g.low) / (g.high - g.low) * (g.bottom - g.top);
  ctx.font = "10px ui-monospace, monospace";
  ctx.strokeStyle = "#222c39"; ctx.fillStyle = "#8999ab"; ctx.lineWidth = 0.5;
  // Tick-aligned labels stay readable at either compact or expanded height.
  const labelTicks = Math.max(1, Math.ceil(priceTicks / Math.floor((g.bottom - g.top) / 32)));
  for (let priceTick = Math.ceil(g.low / tick / labelTicks) * labelTicks; priceTick * tick <= g.high; priceTick += labelTicks) {
    const price = priceTick * tick;
    ctx.beginPath(); ctx.moveTo(g.left, y(price)); ctx.lineTo(g.right, y(price)); ctx.stroke();
    ctx.textAlign = "left";
    if (center !== null) ctx.fillText(heatmapPrice(price), g.right + 8, y(price) + 3);
  }
  const timeLabels = width < 500 ? 2 : 4;
  for (let i = 0; i <= timeLabels; i++) {
    const time = g.start + duration * i / timeLabels;
    ctx.textAlign = i === 0 ? "left" : i === timeLabels ? "right" : "center";
    ctx.fillText(heatmapTime(time), x(time), height - 9);
    ctx.beginPath(); ctx.moveTo(x(time), g.top); ctx.lineTo(x(time), g.bottom); ctx.stroke();
  }
  ctx.save(); ctx.beginPath(); ctx.rect(g.left, g.top, g.right - g.left, g.bottom - g.top); ctx.clip();
  const colors = new Map<number, string>();
  for (const frame of frames) {
    if (frame.time + HEATMAP_SAMPLE_MS < g.start || frame.time > g.end) continue;
    const left = x(frame.time), cellWidth = x(Math.min(g.end, frame.time + HEATMAP_SAMPLE_MS)) - left;
    if (frame.gap) {
      // A band signals unknown state, never a zero-size book.
      ctx.fillStyle = "#d99a3218"; ctx.fillRect(left, g.top, cellWidth, g.bottom - g.top);
      ctx.fillStyle = "#d99a32a0"; ctx.fillRect(left, g.top, cellWidth, 2);
      continue;
    }
    if (center === null || tickSize === null) continue;
    for (const levels of [frame.bids, frame.asks]) {
      for (let i = 0; i < levels.length; i += 2) {
        const price = levels[i] * tick;
        if (price + tick / 2 < g.low || price - tick / 2 > g.high) continue;
        const quantity = levels[i + 1];
        if (!colors.has(quantity)) colors.set(quantity, heatmapColor(quantity, intensity));
        ctx.fillStyle = colors.get(quantity)!;
        ctx.fillRect(left, y(price + tick / 2), cellWidth, Math.max(0.5, y(price - tick / 2) - y(price + tick / 2)));
      }
    }
  }
  // Draw on top of all cells so a neighboring sample cannot cover a quote.
  for (const [side, color] of [["bestBid", "#4bd6b0"], ["bestAsk", "#ff7b86"]] as const) {
    let previous: LiquidityFrame | undefined;
    ctx.strokeStyle = color; ctx.lineWidth = 1.2; ctx.beginPath();
    for (const frame of frames) {
      const price = frame[side];
      if (frame.gap || price === null || frame.time + HEATMAP_SAMPLE_MS < g.start || frame.time > g.end) {
        previous = undefined; continue;
      }
      const left = x(frame.time);
      if (previous && previous.epoch === frame.epoch && frame.time - previous.time <= HEATMAP_SAMPLE_MS) {
        ctx.lineTo(left, y(previous[side]!)); ctx.lineTo(left, y(price));
      } else ctx.moveTo(left, y(price));
      ctx.lineTo(x(Math.min(g.end, frame.time + HEATMAP_SAMPLE_MS)), y(price));
      previous = frame;
    }
    ctx.stroke();
  }
  g.bubbles = [];
  if (center !== null && tickSize !== null) {
    for (const trade of options.trades ?? []) {
      if (trade.time < g.start || trade.time > g.end || trade.price < g.low || trade.price > g.high ||
          trade.volume < (options.minTradeVolume ?? 1)) continue;
      const radius = tradeBubbleRadius(trade.volume, options.bubbleScale ?? 1);
      const bx = x(trade.time), by = y(trade.price);
      const color = trade.side === "buy" ? "#42dda0" : trade.side === "sell" ? "#ff607c" : "#b6c1d1";
      ctx.beginPath(); ctx.arc(bx, by, radius, 0, Math.PI * 2);
      ctx.fillStyle = `${color}a8`; ctx.fill();
      ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.stroke();
      if (radius >= 9) {
        ctx.font = "bold 9px ui-monospace, monospace"; ctx.textAlign = "center"; ctx.fillStyle = "#07131b";
        ctx.fillText(trade.volume >= 1000 ? `${(trade.volume / 1000).toFixed(1)}k` : String(trade.volume), bx, by + 3);
      }
      g.bubbles.push({ x: bx, y: by, radius, trade });
    }
  }
  ctx.restore();
  ctx.font = "10px ui-monospace, monospace";
  ctx.fillStyle = "#8999ab"; ctx.textAlign = "left";
  ctx.fillText("DEPTH HISTORY", g.left, 14);
  // Use only the sample covering the displayed edge. Never revive an older
  // valid book through a disconnect, collection gap or unobserved interval.
  const edge = frameAtTime(frames, end);
  if (profileWidth) {
    const profileLeft = g.right + 78;
    ctx.fillStyle = "#8999ab"; ctx.fillText("BOOK", profileLeft, 14);
    ctx.save(); ctx.beginPath(); ctx.rect(profileLeft, g.top, profileWidth, g.bottom - g.top); ctx.clip();
    if (edge && !edge.gap && tickSize !== null && center !== null) {
      const sizes = [...edge.bids, ...edge.asks].filter((_, i) => i % 2 === 1);
      const maximum = Math.max(1, ...sizes);
      for (const [levels, color] of [[edge.bids, "#36a58f"], [edge.asks, "#c65a69"]] as const) {
        ctx.fillStyle = color;
        for (let i = 0; i < levels.length; i += 2) {
          const price = levels[i] * tick;
          ctx.fillRect(profileLeft, y(price + tick / 2), (profileWidth - 6) * levels[i + 1] / maximum,
            Math.max(.5, (g.bottom - g.top) / priceTicks - .5));
        }
      }
    } else {
      ctx.fillStyle = "#8999ab"; ctx.fillText("No sample", profileLeft, g.top + 16);
    }
    ctx.restore();
  }
  if (edge && !edge.gap && center !== null) {
    for (const [price, color] of [[edge.bestBid, "#4bd6b0"], [edge.bestAsk, "#ff7b86"]] as const) {
      if (price === null || price < g.low || price > g.high) continue;
      ctx.fillStyle = color; ctx.fillRect(g.right, y(price) - 1, 5, 2);
    }
  }
  if (center === null) {
    ctx.fillStyle = "#94a3b8"; ctx.textAlign = "center"; ctx.font = "12px system-ui";
    ctx.fillText("Waiting for observed depth", (g.left + g.right) / 2, height / 2);
  }
  return g;
}
