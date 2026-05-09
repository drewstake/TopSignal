import { cn } from "../ui/cn";

interface GaugeBarProps {
  label: string;
  value: number | null;
  valueLabel: string;
  className?: string;
  fillClassName?: string;
}

function clampPercent(value: number | null) {
  if (value === null || !Number.isFinite(value)) {
    return 0;
  }
  return Math.min(100, Math.max(0, value));
}

export function GaugeBar({ label, value, valueLabel, className, fillClassName }: GaugeBarProps) {
  const width = clampPercent(value);

  return (
    <div className={cn("space-y-1", className)}>
      <div className="flex items-center justify-between text-[9px] uppercase tracking-[0.12em] text-app-muted-strong">
        <span>{label}</span>
        <span className="font-semibold text-app-muted">{valueLabel}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full border border-app-border/80 bg-app-surface/85">
        <div
          className={cn("h-full bg-app-accent/70 transition-all duration-500", fillClassName)}
          style={{ width: `${width}%` }}
          aria-hidden="true"
        />
      </div>
    </div>
  );
}
