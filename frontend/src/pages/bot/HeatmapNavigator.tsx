import { useEffect, useRef, type KeyboardEvent, type PointerEvent } from "react";
import { INT32_NONE, type HeatmapGap, type HistoryTile } from "./heatmapHistory";
import { HEATMAP_SAMPLE_MS, type LiquidityFrame } from "./liquidityHeatmap";
import { heatmapMinute, heatmapTime } from "./liquidityHeatmapCanvas";

interface Props {
  sessionStart: number; sessionEnd: number; now: number;
  coverage: ReadonlyArray<readonly [number, number]>; gaps: readonly HeatmapGap[];
  summary: HistoryTile | null; liveFrames: readonly LiquidityFrame[];
  viewStart: number; viewEnd: number; following: boolean;
  onJump: (time: number) => void;
}

/** Whole-session overview: recorded coverage, gaps, mid-price path and the visible window. */
export function HeatmapNavigator({ sessionStart, sessionEnd, now, coverage, gaps, summary, liveFrames, viewStart, viewEnd, following, onJump }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const dragging = useRef(false);
  const last = Math.max(sessionStart + 60_000, Math.min(sessionEnd, now));
  const span = last - sessionStart;

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const draw = () => {
      const width = Math.max(200, element.clientWidth), height = Math.max(24, element.clientHeight);
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      if (element.width !== Math.round(width * dpr)) { element.width = Math.round(width * dpr); element.height = Math.round(height * dpr); }
      const ctx = element.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);
      const x = (time: number) => (Math.min(last, Math.max(sessionStart, time)) - sessionStart) / span * width;
      ctx.fillStyle = "rgba(120,130,145,0.14)"; ctx.fillRect(0, 0, width, height);
      ctx.fillStyle = "rgba(60,110,170,0.35)";
      for (const [s, e] of coverage) ctx.fillRect(x(s), 0, Math.max(1, x(e) - x(s)), height);
      // Live-only samples (e.g. the unsealed tail) shown lighter.
      ctx.fillStyle = "rgba(60,110,170,0.2)";
      for (const frame of liveFrames) ctx.fillRect(x(frame.time), 0, Math.max(1, x(frame.time + HEATMAP_SAMPLE_MS) - x(frame.time)), height);
      ctx.fillStyle = "rgba(240,170,70,0.9)";
      for (const gap of gaps) if (gap.end - gap.start >= 5_000) ctx.fillRect(x(gap.start), height - 4, Math.max(1.5, x(gap.end) - x(gap.start)), 4);
      const frames = summary?.frames;
      if (frames && frames.time.length) {
        let low = Infinity, high = -Infinity;
        for (let i = 0; i < frames.time.length; i++) {
          if (frames.bestBid[i] === INT32_NONE || frames.bestAsk[i] === INT32_NONE) continue;
          low = Math.min(low, frames.bestBid[i]); high = Math.max(high, frames.bestAsk[i]);
        }
        if (Number.isFinite(low) && high > low) {
          ctx.strokeStyle = "rgba(225,232,242,0.85)"; ctx.lineWidth = 1.2; ctx.beginPath();
          let open = false;
          for (let i = 0; i < frames.time.length; i++) {
            if (frames.bestBid[i] === INT32_NONE || frames.bestAsk[i] === INT32_NONE || frames.valid[i] === 0) { open = false; continue; }
            const py = 3 + (high - (frames.bestBid[i] + frames.bestAsk[i]) / 2) / (high - low) * (height - 10);
            const px = x(frames.time[i] + frames.span / 2);
            if (open) ctx.lineTo(px, py); else ctx.moveTo(px, py);
            open = true;
          }
          ctx.stroke();
        }
      }
      const left = x(viewStart), right = Math.max(left + 3, x(viewEnd));
      ctx.fillStyle = "rgba(255,255,255,0.12)"; ctx.fillRect(left, 0, right - left, height);
      ctx.strokeStyle = following ? "#5ee0b0" : "#e9eef5"; ctx.lineWidth = 1.5; ctx.strokeRect(left + 0.75, 0.75, right - left - 1.5, height - 1.5);
    };
    draw();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(draw);
    observer?.observe(element);
    return () => observer?.disconnect();
  }, [sessionStart, last, span, coverage, gaps, summary, liveFrames, viewStart, viewEnd, following]);

  const timeAt = (event: PointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return sessionStart + Math.min(1, Math.max(0, (event.clientX - rect.left) / (rect.width || 1))) * span;
  };
  const step = (fraction: number) => onJump((viewStart + viewEnd) / 2 + span * fraction);
  const onKeyDown = (event: KeyboardEvent<HTMLCanvasElement>) => {
    const keys: Record<string, () => void> = {
      ArrowLeft: () => step(-0.02), ArrowRight: () => step(0.02), PageUp: () => step(-0.1), PageDown: () => step(0.1),
      Home: () => onJump(sessionStart), End: () => onJump(now + span),
    };
    if (keys[event.key]) { event.preventDefault(); keys[event.key](); }
  };
  return <div className="lh-nav">
    <span className="lh-nav-label">{heatmapMinute(sessionStart)}</span>
    <canvas ref={canvas} className="lh-nav-canvas" role="slider" tabIndex={0} aria-label="Session navigator"
      aria-valuemin={sessionStart} aria-valuemax={last} aria-valuenow={Math.round(viewEnd)}
      aria-valuetext={`Viewing ${heatmapTime(viewStart)} to ${heatmapTime(viewEnd)} ET`}
      onPointerDown={event => { dragging.current = true; event.currentTarget.setPointerCapture?.(event.pointerId); onJump(timeAt(event)); }}
      onPointerMove={event => { if (dragging.current) onJump(timeAt(event)); }}
      onPointerUp={() => { dragging.current = false; }} onPointerCancel={() => { dragging.current = false; }}
      onKeyDown={onKeyDown} />
    <span className="lh-nav-label">{heatmapMinute(last)}</span>
  </div>;
}
