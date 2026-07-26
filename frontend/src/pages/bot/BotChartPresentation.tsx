import type { ReactNode } from "react";
import type { UTCTimestamp } from "lightweight-charts";

const priceFormatter = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
const signedPriceFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
  signDisplay: "exceptZero",
});
const signedPercentFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  signDisplay: "exceptZero",
});
const volumeFormatter = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const crosshairTimeFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
  hour12: true,
});

export interface HoveredCandle {
  time: UTCTimestamp;
  open: number;
  high: number;
  low: number;
  close: number;
  previousClose: number | null;
  volume: number;
  isPartial: boolean;
}

export interface RenderableDrawing {
  id: string;
  kind: "line" | "rectangle";
  isDraft: boolean;
  isSelected: boolean;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface RenderableDrawingAnchor {
  x: number;
  y: number;
}

export interface RenderableLivePriceLine {
  x1: number;
  x2: number;
  y: number;
}

export interface DrawingOverlayState {
  width: number;
  height: number;
  items: RenderableDrawing[];
  anchor: RenderableDrawingAnchor | null;
  livePriceLine: RenderableLivePriceLine | null;
}

export function OhlcReadout({ candle }: { candle: HoveredCandle | null }) {
  if (!candle) {
    return null;
  }

  const valueClassName = candle.close >= candle.open ? "text-app-positive" : "text-app-negative";
  const change = candle.previousClose === null ? null : candle.close - candle.previousClose;
  const changePercent =
    candle.previousClose !== null && candle.previousClose !== 0
      ? ((candle.close - candle.previousClose) / Math.abs(candle.previousClose)) * 100
      : null;
  const changeClassName = change === null || change === 0 ? "text-app-text-soft" : change > 0 ? "text-app-positive" : "text-app-negative";
  const changeText =
    change === null
      ? null
      : changePercent === null
        ? signedPriceFormatter.format(change)
        : `${signedPriceFormatter.format(change)} (${signedPercentFormatter.format(changePercent)}%)`;

  return (
    <div
      className="pointer-events-none absolute left-3 top-3 z-20 flex max-w-[calc(100%-1.5rem)] flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-app-border/80 bg-app-bg/85 px-2.5 py-1.5 text-[11px] font-medium text-app-muted shadow-lg shadow-app-bg/30 backdrop-blur"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <span className="font-semibold text-app-text-soft">{crosshairTimeFormatter.format(new Date(Number(candle.time) * 1000))} ET</span>
      <OhlcField label="O" value={priceFormatter.format(candle.open)} valueClassName={valueClassName} />
      <OhlcField label="H" value={priceFormatter.format(candle.high)} valueClassName={valueClassName} />
      <OhlcField label="L" value={priceFormatter.format(candle.low)} valueClassName={valueClassName} />
      <OhlcField label="C" value={priceFormatter.format(candle.close)} valueClassName={valueClassName} />
      {changeText ? <OhlcField value={changeText} valueClassName={changeClassName} /> : null}
      <OhlcField label="Vol" value={volumeFormatter.format(candle.volume)} />
      <span className={candle.isPartial ? "text-app-accent" : "text-app-muted-strong"}>
        {candle.isPartial ? "Partial" : "Closed"}
      </span>
    </div>
  );
}

export function DrawingOverlay({ overlay }: { overlay: DrawingOverlayState }) {
  if (overlay.width <= 0 || overlay.height <= 0 || (overlay.items.length === 0 && !overlay.anchor && !overlay.livePriceLine)) {
    return null;
  }
  return (
    <svg aria-hidden="true" className="pointer-events-none absolute left-0 top-0" width={overlay.width} height={overlay.height} viewBox={`0 0 ${overlay.width} ${overlay.height}`}>
      {overlay.livePriceLine ? <LivePriceOverlayLine line={overlay.livePriceLine} /> : null}
      {overlay.items.map((item) => <DrawingOverlayItem key={`${item.id}:${item.isDraft ? "draft" : "final"}`} item={item} />)}
      {overlay.anchor ? <DrawingAnchorPreviewMark anchor={overlay.anchor} /> : null}
    </svg>
  );
}

export function ChartToolButton({
  active = false,
  disabled = false,
  label,
  onClick,
  children,
}: {
  active?: boolean;
  disabled?: boolean;
  label: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={active}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className={`grid h-11 w-11 place-items-center border-r border-app-border/80 text-app-text-soft transition last:border-r-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-accent/45 disabled:cursor-not-allowed disabled:opacity-45 sm:h-9 sm:w-10 ${active ? "bg-app-accent/15 text-app-accent shadow-[inset_0_0_0_1px_rgb(var(--theme-accent)/0.32)]" : "hover:bg-app-accent/10 hover:text-app-text"}`}
    >
      {children}
    </button>
  );
}

export function LegendDot({ active, className, label, onClick }: LegendProps) {
  return (
    <button type="button" aria-pressed={active} onClick={onClick} className={`inline-flex min-h-11 shrink-0 items-center gap-2 whitespace-nowrap rounded px-2 py-1 transition sm:min-h-8 ${active ? "text-app-text-soft hover:bg-app-bg/80" : "text-app-muted-strong hover:bg-app-bg/70 hover:text-app-muted"}`}>
      <span className={`h-2.5 w-2.5 rounded-full ${className} ${active ? "" : "opacity-25 grayscale"}`} />
      <span>{label}</span>
    </button>
  );
}

export function LegendLine({ active, className, label, onClick }: LegendProps) {
  return (
    <button type="button" aria-pressed={active} onClick={onClick} className={`inline-flex min-h-11 shrink-0 items-center gap-2 whitespace-nowrap rounded px-2 py-1 transition sm:min-h-8 ${active ? "text-app-text-soft hover:bg-app-bg/80" : "text-app-muted-strong hover:bg-app-bg/70 hover:text-app-muted"}`}>
      <span className={`h-0 w-5 border-t-2 border-dotted ${className} ${active ? "" : "opacity-25 grayscale"}`} />
      <span>{label}</span>
    </button>
  );
}

interface LegendProps {
  active: boolean;
  className: string;
  label: string;
  onClick: () => void;
}

function LivePriceOverlayLine({ line }: { line: RenderableLivePriceLine }) {
  return <line x1={line.x1} y1={line.y} x2={line.x2} y2={line.y} stroke="rgb(var(--theme-accent))" strokeWidth="2" strokeLinecap="round" strokeDasharray="6 5" vectorEffect="non-scaling-stroke" />;
}

function DrawingOverlayItem({ item }: { item: RenderableDrawing }) {
  const stroke = item.isDraft ? "rgb(var(--theme-accent) / 0.85)" : item.isSelected ? "rgb(var(--theme-accent))" : "rgb(var(--theme-text-soft) / 0.88)";
  const strokeDasharray = item.isDraft ? "6 4" : undefined;
  const strokeWidth = item.isSelected ? "3" : "2";
  if (item.kind === "rectangle") {
    const x = Math.min(item.x1, item.x2);
    const y = Math.min(item.y1, item.y2);
    const width = Math.abs(item.x2 - item.x1);
    const height = Math.abs(item.y2 - item.y1);
    const middleY = y + height / 2;
    return (
      <g>
        <rect x={x} y={y} width={width} height={height} rx="2" fill="rgb(var(--theme-muted) / 0.08)" stroke={stroke} strokeWidth={strokeWidth} strokeDasharray={strokeDasharray} vectorEffect="non-scaling-stroke" />
        <DrawingEndpoint x={item.x1} y={item.y1} draft={item.isDraft} selected={item.isSelected} />
        <DrawingEndpoint x={item.x2} y={item.y2} draft={item.isDraft} selected={item.isSelected} />
        <DrawingEndpoint x={x} y={middleY} draft={item.isDraft} selected={item.isSelected} />
        <DrawingEndpoint x={x + width} y={middleY} draft={item.isDraft} selected={item.isSelected} />
      </g>
    );
  }
  return (
    <g>
      <line x1={item.x1} y1={item.y1} x2={item.x2} y2={item.y2} stroke={stroke} strokeWidth={strokeWidth} strokeLinecap="round" strokeDasharray={strokeDasharray} vectorEffect="non-scaling-stroke" />
      <DrawingEndpoint x={item.x1} y={item.y1} draft={item.isDraft} selected={item.isSelected} />
      <DrawingEndpoint x={item.x2} y={item.y2} draft={item.isDraft} selected={item.isSelected} />
    </g>
  );
}

function DrawingAnchorPreviewMark({ anchor }: { anchor: RenderableDrawingAnchor }) {
  return (
    <g>
      <circle cx={anchor.x} cy={anchor.y} r="5" fill="rgb(var(--theme-accent) / 0.18)" stroke="rgb(var(--theme-accent))" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      <path d={`M ${anchor.x - 8} ${anchor.y} L ${anchor.x + 8} ${anchor.y} M ${anchor.x} ${anchor.y - 8} L ${anchor.x} ${anchor.y + 8}`} stroke="rgb(var(--theme-text) / 0.9)" strokeWidth="1.5" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </g>
  );
}

function DrawingEndpoint({ x, y, draft, selected }: { x: number; y: number; draft: boolean; selected: boolean }) {
  return <circle cx={x} cy={y} r={selected ? "4" : "3"} fill={draft || selected ? "rgb(var(--theme-accent))" : "rgb(var(--theme-text-soft))"} stroke="rgb(var(--theme-bg))" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />;
}

export function CursorToolIcon() { return <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M6 3l11 10-6 1.5L8 21 6 3z" strokeLinejoin="round" /></svg>; }
export function LineToolIcon() { return <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M5 18 19 6" strokeLinecap="round" /><circle cx="5" cy="18" r="1.7" fill="currentColor" stroke="none" /><circle cx="19" cy="6" r="1.7" fill="currentColor" stroke="none" /></svg>; }
export function RectangleToolIcon() { return <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><rect x="5" y="6" width="14" height="12" rx="1.5" /></svg>; }
export function ClearDrawingsIcon() { return <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M6 7h12M9 7V5h6v2M9 10v7M15 10v7M8 7l1 13h6l1-13" strokeLinecap="round" strokeLinejoin="round" /></svg>; }
export function FitChartIcon() { return <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M8 4H4v4M4 4l5 5M16 4h4v4M20 4l-5 5M8 20H4v-4M4 20l5-5M16 20h4v-4M20 20l-5-5" strokeLinecap="round" strokeLinejoin="round" /></svg>; }
export function ComputeLiquidityIcon() { return <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M5 7h14M5 17h14M8 4v6M16 14v6" strokeLinecap="round" /></svg>; }
export function RefreshIcon() { return <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M20 11a8 8 0 0 0-14.4-4.8L4 8M4 4v4h4M4 13a8 8 0 0 0 14.4 4.8L20 16M20 20v-4h-4" strokeLinecap="round" strokeLinejoin="round" /></svg>; }
export function HistoryIcon() { return <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M4 5v5h5M4.6 14a8 8 0 1 0 1.7-7.4L4 9M12 8v4l3 2" strokeLinecap="round" strokeLinejoin="round" /></svg>; }

function OhlcField({ label, value, valueClassName = "text-app-text-soft" }: { label?: string; value: string; valueClassName?: string }) {
  return <span className="inline-flex items-baseline gap-1 tabular-nums">{label ? <span className="text-app-muted-strong">{label}</span> : null}<span className={valueClassName}>{value}</span></span>;
}
