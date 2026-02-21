import type { ReactNode } from "react";

import { Card } from "../ui/Card";
import { cn } from "../ui/cn";
import { Tooltip } from "./Tooltip";

interface MetricCardProps {
  title: string;
  primaryValue: string;
  primaryClassName?: string;
  tooltip?: ReactNode;
  chips?: ReactNode;
  subValue?: ReactNode;
  hoverDetails?: ReactNode;
  className?: string;
}

export function MetricCard({
  title,
  primaryValue,
  primaryClassName,
  tooltip,
  chips,
  subValue,
  hoverDetails,
  className,
}: MetricCardProps) {
  return (
    <Card
      className={cn(
        "group h-full min-h-[210px] border-slate-800/90 bg-slate-900/75 p-4 transition duration-300 hover:-translate-y-0.5 hover:border-cyan-300/40 hover:shadow-[0_12px_28px_-18px_rgba(56,189,248,0.55)]",
        className,
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-[11px] uppercase tracking-[0.16em] text-slate-500">{title}</p>
        {tooltip ? (
          <Tooltip content={tooltip}>
            <span className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-slate-700/80 bg-slate-900/75 text-[10px] font-semibold text-slate-400 transition group-hover:border-cyan-300/60 group-hover:text-cyan-200">
              i
            </span>
          </Tooltip>
        ) : null}
      </div>
      <p className={cn("mt-2 text-[1.65rem] font-semibold leading-tight text-slate-100", primaryClassName)}>{primaryValue}</p>
      {chips}
      {subValue ? <div className="mt-3 text-xs text-slate-300">{subValue}</div> : null}
      {hoverDetails ? (
        <div className="mt-3 translate-y-1 rounded-lg border border-slate-800/70 bg-slate-950/35 px-2.5 py-2 text-xs text-slate-400 opacity-0 transition duration-200 group-hover:translate-y-0 group-hover:opacity-100">
          {hoverDetails}
        </div>
      ) : null}
    </Card>
  );
}

