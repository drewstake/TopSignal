import type { ReactNode } from "react";

import { Card } from "../ui/Card";
import { cn } from "../ui/cn";
import { InfoPopover } from "./InfoPopover";
import { Icon } from "../ui/Icon";

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
        "analytics-metric group relative h-full border-app-border/90 bg-app-surface/75 p-3 md:p-4",
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
        <h3 className="metric-title">{title}</h3>
        {info ? <InfoPopover content={info} /> : null}
      </div>
      <p className={cn("mt-1.5 text-[1.45rem] font-semibold leading-[1.05] text-app-text md:text-[1.55rem]", primaryClassName)}>
        {primaryValue}
      </p>
      {subtitle ? <p className="mt-1 text-[11px] leading-snug text-app-muted">{subtitle}</p> : null}
      {children ? <details className="metric-details"><summary><span>Explore details<span className="sr-only"> for {title}</span></span><Icon name="chevron" /></summary><div className={cn("metric-details-content mt-2.5 space-y-1.5", contentClassName)}>{children}</div></details> : null}
    </Card>
  );
}
