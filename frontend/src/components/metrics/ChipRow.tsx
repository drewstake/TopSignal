import type { ReactNode } from "react";

import { cn } from "../ui/cn";
import { Tooltip } from "./Tooltip";

export interface MetricChip {
  label: string;
  value: string;
  className?: string;
  tooltip?: ReactNode;
}

interface ChipRowProps {
  chips: MetricChip[];
  className?: string;
}

export function ChipRow({ chips, className }: ChipRowProps) {
  return (
    <div className={cn("mt-4 flex flex-wrap gap-2", className)}>
      {chips.map((chip) => (
        <Tooltip key={chip.label} content={chip.tooltip ?? `${chip.label}: ${chip.value}`} disabled={!chip.tooltip}>
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded-full border border-slate-700/80 bg-slate-900/70 px-2.5 py-1 text-[11px] text-slate-300",
              chip.className,
            )}
          >
            <span className="text-slate-400">{chip.label}</span>
            <span className="font-medium text-slate-100">{chip.value}</span>
          </span>
        </Tooltip>
      ))}
    </div>
  );
}

