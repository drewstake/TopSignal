import type { ReactNode } from "react";

import { Card } from "../ui/Card";
import { cn } from "../ui/cn";
import { InfoPopover } from "./InfoPopover";

interface MetricCardProps {
  title: string;
  primaryValue: string;
  primaryClassName?: string;
  subtitle?: ReactNode;
  info?: ReactNode;
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
        "group relative h-full overflow-hidden border-app-border/90 bg-app-surface/75 p-3 md:p-4 transition duration-300 hover:-translate-y-0.5 hover:border-app-accent/40 hover:shadow-[0_14px_36px_-22px_rgb(var(--theme-accent)/0.62)]",
        className,
      )}
    >
      <div
        aria-hidden="true"
        className={cn(
          "pointer-events-none absolute inset-x-2.5 top-0 h-[2px] rounded-full bg-gradient-to-r from-app-accent/55 via-app-accent/15 to-transparent",
          accentClassName,
        )}
      />
      <div className="flex items-start justify-between gap-1.5">
        <p className="text-[10px] uppercase tracking-[0.14em] text-app-muted-strong">{title}</p>
        {info ? <InfoPopover content={info} /> : null}
      </div>
      <p className={cn("mt-1.5 text-[1.45rem] font-semibold leading-[1.05] text-app-text md:text-[1.55rem]", primaryClassName)}>
        {primaryValue}
      </p>
      {subtitle ? <p className="mt-1 text-[11px] leading-snug text-app-muted">{subtitle}</p> : null}
      {children ? <div className={cn("mt-2.5 space-y-1.5", contentClassName)}>{children}</div> : null}
    </Card>
  );
}
