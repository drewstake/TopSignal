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

const SIZE = 118;
const STROKE_WIDTH = 10;
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
  const centerGradientId = useMemo(() => `donut-center-${titleId.replace(/[^a-zA-Z0-9_-]/g, "") || "x"}`, [titleId]);

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
    <div className={cn("flex items-center gap-2.5", className)}>
      <div className="relative h-[112px] w-[112px] shrink-0">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 rounded-full bg-[conic-gradient(from_180deg,rgba(16,185,129,0.3),rgba(56,189,248,0.18),rgba(248,113,113,0.28),rgba(16,185,129,0.3))] blur-xl"
        />
        <svg
          viewBox={`0 0 ${SIZE} ${SIZE}`}
          role="img"
          aria-labelledby={`${titleId} ${descId}`}
          className="relative h-full w-full drop-shadow-[0_12px_24px_rgba(15,23,42,0.45)]"
        >
          <title id={titleId}>Directional split</title>
          <desc id={descId}>{descriptionText}</desc>
          <defs>
            <radialGradient id={centerGradientId} cx="50%" cy="45%" r="75%">
              <stop offset="0%" stopColor="rgba(15,23,42,0.95)" />
              <stop offset="100%" stopColor="rgba(15,23,42,0.78)" />
            </radialGradient>
          </defs>
          <circle cx={SIZE / 2} cy={SIZE / 2} r={RADIUS} fill="none" stroke="rgba(51,65,85,0.7)" strokeWidth={STROKE_WIDTH} />
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
                  className="transition-all duration-500"
                />
              );
            })}
          </g>
          <circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={RADIUS - STROKE_WIDTH / 2 - 1}
            fill={`url(#${centerGradientId})`}
            stroke="rgba(148,163,184,0.18)"
            strokeWidth="1"
          />
        </svg>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center px-2 text-center">
          <p className="text-xs font-semibold text-slate-100 drop-shadow-[0_2px_10px_rgba(15,23,42,0.55)]">{centerLabel}</p>
          {centerSubLabel ? <p className="text-[9px] uppercase tracking-[0.12em] text-cyan-200/75">{centerSubLabel}</p> : null}
        </div>
      </div>
      <div className="min-w-0 flex-1 space-y-1.5">
        {segments.map((segment) => (
          <div
            key={segment.label}
            className="flex items-center justify-between gap-1.5 rounded-lg border border-slate-700/75 bg-slate-950/45 px-2 py-1 text-[10px] shadow-[inset_0_1px_0_rgba(148,163,184,0.08)]"
          >
            <span className="inline-flex items-center gap-2 text-slate-200">
              <span
                className="h-2 w-2 shrink-0 rounded-full ring-2 ring-slate-950/70"
                style={{ backgroundColor: segment.color }}
                aria-hidden="true"
              />
              {segment.label}
            </span>
            <span className="rounded-md bg-slate-900/80 px-1.5 py-0.5 font-semibold text-slate-100">{segment.valueLabel}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
