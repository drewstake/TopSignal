import { useId, useMemo } from "react";

import { cn } from "../ui/cn";

export interface DonutRingSegment {
  label: string;
  value: number | null;
  color: string;
  valueLabel: string;
}

interface DonutRingProps {
  segments: DonutRingSegment[];
  centerLabel: string;
  centerSubLabel?: string;
  className?: string;
}

interface NormalizedSegment extends DonutRingSegment {
  share: number;
  offset: number;
}

const SIZE = 120;
const STROKE_WIDTH = 14;
const RADIUS = (SIZE - STROKE_WIDTH) / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

function safeValue(value: number | null) {
  if (value === null || !Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, value);
}

export function DonutRing({ segments, centerLabel, centerSubLabel, className }: DonutRingProps) {
  const titleId = useId();
  const descId = useId();

  const normalizedSegments = useMemo<NormalizedSegment[]>(() => {
    const total = segments.reduce((sum, segment) => sum + safeValue(segment.value), 0);
    if (total <= 0) {
      return [];
    }
    let offset = 0;
    return segments.map((segment) => {
      const share = safeValue(segment.value) / total;
      const next = {
        ...segment,
        share,
        offset,
      };
      offset += share * CIRCUMFERENCE;
      return next;
    });
  }, [segments]);

  const descriptionText =
    normalizedSegments.length === 0
      ? "No directional split data available."
      : normalizedSegments.map((segment) => `${segment.label} ${segment.valueLabel}`).join(", ");

  return (
    <div className={cn("flex items-center gap-3", className)}>
      <div className="relative h-[120px] w-[120px] shrink-0">
        <svg viewBox={`0 0 ${SIZE} ${SIZE}`} role="img" aria-labelledby={`${titleId} ${descId}`} className="h-full w-full">
          <title id={titleId}>Directional split</title>
          <desc id={descId}>{descriptionText}</desc>
          <circle cx={SIZE / 2} cy={SIZE / 2} r={RADIUS} fill="none" stroke="rgba(51,65,85,0.62)" strokeWidth={STROKE_WIDTH} />
          <g transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}>
            {normalizedSegments.map((segment) => {
              const length = segment.share * CIRCUMFERENCE;
              const strokeDasharray = `${length} ${CIRCUMFERENCE - length}`;
              const strokeDashoffset = -segment.offset;
              return (
                <circle
                  key={segment.label}
                  cx={SIZE / 2}
                  cy={SIZE / 2}
                  r={RADIUS}
                  fill="none"
                  stroke={segment.color}
                  strokeWidth={STROKE_WIDTH}
                  strokeDasharray={strokeDasharray}
                  strokeDashoffset={strokeDashoffset}
                  strokeLinecap="round"
                />
              );
            })}
          </g>
        </svg>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center px-2 text-center">
          <p className="text-sm font-semibold text-slate-100">{centerLabel}</p>
          {centerSubLabel ? <p className="text-[10px] uppercase tracking-[0.14em] text-slate-500">{centerSubLabel}</p> : null}
        </div>
      </div>
      <div className="min-w-0 flex-1 space-y-1.5">
        {segments.map((segment) => (
          <div key={segment.label} className="flex items-center justify-between gap-2 rounded-md border border-slate-800/70 bg-slate-950/35 px-2 py-1 text-[11px]">
            <span className="inline-flex items-center gap-1.5 text-slate-300">
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: segment.color }} aria-hidden="true" />
              {segment.label}
            </span>
            <span className="font-semibold text-slate-100">{segment.valueLabel}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
