import { useMemo, useState, type ReactNode } from "react";

import { cn } from "../ui/cn";

type Segment = "LONG" | "SHORT";

interface DirectionDonutProps {
  longPercent: number | null;
  shortPercent: number | null;
  centerText: string;
  biasText: string;
  longDetails: ReactNode;
  shortDetails: ReactNode;
  missingReason?: string;
  className?: string;
}

const RADIUS = 42;
const VIEWBOX_SIZE = 120;
const CENTER = VIEWBOX_SIZE / 2;

function clampPercent(value: number) {
  return Math.min(100, Math.max(0, value));
}

function toCartesian(cx: number, cy: number, radius: number, angleDegrees: number) {
  const radians = ((angleDegrees - 90) * Math.PI) / 180;
  return {
    x: cx + radius * Math.cos(radians),
    y: cy + radius * Math.sin(radians),
  };
}

function arcPath(cx: number, cy: number, radius: number, startAngle: number, endAngle: number) {
  const start = toCartesian(cx, cy, radius, endAngle);
  const end = toCartesian(cx, cy, radius, startAngle);
  const largeArcFlag = endAngle - startAngle <= 180 ? "0" : "1";
  return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArcFlag} 0 ${end.x} ${end.y}`;
}

export function DirectionDonut({
  longPercent,
  shortPercent,
  centerText,
  biasText,
  longDetails,
  shortDetails,
  missingReason,
  className,
}: DirectionDonutProps) {
  const [activeSegment, setActiveSegment] = useState<Segment | null>(null);

  const normalized = useMemo(() => {
    if (longPercent === null || shortPercent === null) {
      return null;
    }
    const safeLong = clampPercent(longPercent);
    const safeShort = clampPercent(shortPercent);
    const total = safeLong + safeShort;
    if (total <= 0) {
      return null;
    }

    const longShare = (safeLong / total) * 100;
    return {
      long: longShare,
      short: 100 - longShare,
    };
  }, [longPercent, shortPercent]);

  if (!normalized) {
    return (
      <div className={cn("mt-3 flex flex-col items-center", className)}>
        <svg viewBox={`0 0 ${VIEWBOX_SIZE} ${VIEWBOX_SIZE}`} className="h-36 w-36">
          <circle cx={CENTER} cy={CENTER} r={RADIUS} fill="none" stroke="rgba(51,65,85,0.8)" strokeWidth={12} />
          <text x={CENTER} y={CENTER} textAnchor="middle" className="fill-slate-400 text-[10px] font-semibold">
            N/A
          </text>
        </svg>
        <p className="mt-1 text-center text-[11px] text-slate-500">{missingReason ?? "Needs directional trade data."}</p>
      </div>
    );
  }

  const longAngle = (normalized.long / 100) * 360;
  const shortAngle = (normalized.short / 100) * 360;
  const startAngle = 0;
  const longEndAngle = startAngle + longAngle;
  const shortEndAngle = longEndAngle + shortAngle;

  const hoverContent = activeSegment === "LONG" ? longDetails : activeSegment === "SHORT" ? shortDetails : null;

  return (
    <div className={cn("mt-3 flex flex-col items-center", className)} onMouseLeave={() => setActiveSegment(null)}>
      <div className="relative">
        <svg viewBox={`0 0 ${VIEWBOX_SIZE} ${VIEWBOX_SIZE}`} className="h-36 w-36">
          <circle cx={CENTER} cy={CENTER} r={RADIUS} fill="none" stroke="rgba(51,65,85,0.62)" strokeWidth={12} />
          {normalized.short > 0 && normalized.short < 100 ? (
            <path
              d={arcPath(CENTER, CENTER, RADIUS, longEndAngle, shortEndAngle)}
              fill="none"
              stroke="rgba(248,113,113,0.85)"
              strokeWidth={12}
              strokeLinecap="round"
              onMouseEnter={() => setActiveSegment("SHORT")}
            />
          ) : null}
          {normalized.short >= 100 ? (
            <circle
              cx={CENTER}
              cy={CENTER}
              r={RADIUS}
              fill="none"
              stroke="rgba(248,113,113,0.85)"
              strokeWidth={12}
              onMouseEnter={() => setActiveSegment("SHORT")}
            />
          ) : null}
          {normalized.long > 0 && normalized.long < 100 ? (
            <path
              d={arcPath(CENTER, CENTER, RADIUS, startAngle, longEndAngle)}
              fill="none"
              stroke="rgba(16,185,129,0.88)"
              strokeWidth={12}
              strokeLinecap="round"
              onMouseEnter={() => setActiveSegment("LONG")}
            />
          ) : null}
          {normalized.long >= 100 ? (
            <circle
              cx={CENTER}
              cy={CENTER}
              r={RADIUS}
              fill="none"
              stroke="rgba(16,185,129,0.88)"
              strokeWidth={12}
              onMouseEnter={() => setActiveSegment("LONG")}
            />
          ) : null}
          <text x={CENTER} y={CENTER - 3} textAnchor="middle" className="fill-slate-100 text-[10px] font-semibold">
            {centerText}
          </text>
          <text x={CENTER} y={CENTER + 11} textAnchor="middle" className="fill-slate-400 text-[8px] uppercase tracking-[0.12em]">
            Direction
          </text>
        </svg>
        {hoverContent ? (
          <div className="pointer-events-none absolute -top-1 left-1/2 w-max max-w-60 -translate-x-1/2 rounded-md border border-slate-700/75 bg-slate-950/95 px-2 py-1 text-[11px] text-slate-200 shadow-lg">
            {hoverContent}
          </div>
        ) : null}
      </div>
      <p className="mt-1 text-center text-[11px] text-slate-400">{biasText}</p>
    </div>
  );
}

