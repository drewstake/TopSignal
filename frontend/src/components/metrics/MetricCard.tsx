import type { ReactNode } from "react";

import { Card } from "../ui/Card";
import { cn } from "../ui/cn";
import { InfoPopover } from "./InfoPopover";

interface MetricCardProps {
  title: string;
  primaryValue: string;
  primaryClassName?: string;
  subtitle?: ReactNode;
  info?: string;
  accentClassName?: string;
  className?: string;
  contentClassName?: string;
  children?: ReactNode;
}

export function MetricCard({
  title,
  primaryValue,
  primaryClassName,
  subtitle,
  info,
  accentClassName,
  className,
  contentClassName,
  children,
}: MetricCardProps) {
  return (
    <Card
      className={cn(
        "group relative h-full overflow-hidden border-slate-800/90 bg-slate-900/75 p-4 transition duration-300 hover:-translate-y-0.5 hover:border-cyan-300/40 hover:shadow-[0_14px_36px_-22px_rgba(56,189,248,0.62)]",
        className,
      )}
    >
      <div
        aria-hidden="true"
        className={cn(
          "pointer-events-none absolute inset-x-3 top-0 h-[2px] rounded-full bg-gradient-to-r from-cyan-300/55 via-cyan-200/15 to-transparent",
          accentClassName,
        )}
      />
      <div className="flex items-start justify-between gap-2">
        <p className="text-[11px] uppercase tracking-[0.16em] text-slate-500">{title}</p>
        {info ? <InfoPopover content={info} /> : null}
      </div>
      <p className={cn("mt-2 text-[1.65rem] font-semibold leading-tight text-slate-100", primaryClassName)}>{primaryValue}</p>
      {subtitle ? <p className="mt-1 text-xs text-slate-400">{subtitle}</p> : null}
      {children ? <div className={cn("mt-3 space-y-2", contentClassName)}>{children}</div> : null}
    </Card>
  );
}
