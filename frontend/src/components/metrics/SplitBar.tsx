import { cn } from "../ui/cn";

interface SplitBarProps {
  leftLabel: string;
  rightLabel: string;
  leftValue: string;
  rightValue: string;
  leftMagnitude: number;
  rightMagnitude: number;
  leftBarClassName?: string;
  rightBarClassName?: string;
  trackClassName?: string;
  valueClassName?: string;
  className?: string;
}

function safeMagnitude(value: number) {
  return Number.isFinite(value) ? Math.max(value, 0) : 0;
}

export function SplitBar({
  leftLabel,
  rightLabel,
  leftValue,
  rightValue,
  leftMagnitude,
  rightMagnitude,
  leftBarClassName,
  rightBarClassName,
  trackClassName,
  valueClassName,
  className,
}: SplitBarProps) {
  const left = safeMagnitude(leftMagnitude);
  const right = safeMagnitude(rightMagnitude);
  const total = left + right;
  const leftPercent = total > 0 ? (left / total) * 100 : 50;
  const rightPercent = total > 0 ? (right / total) * 100 : 50;

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex items-center justify-between text-[10px] uppercase tracking-[0.12em] text-slate-500">
        <span>{leftLabel}</span>
        <span>{rightLabel}</span>
      </div>
      <div className={cn("relative h-2.5 overflow-hidden rounded-full border border-slate-700/80 bg-slate-900/85", trackClassName)}>
        <div
          className={cn("h-full bg-emerald-400/80 transition-all duration-300", leftBarClassName)}
          style={{ width: `${leftPercent}%` }}
          aria-hidden="true"
        />
        <div
          className={cn("absolute right-0 top-0 h-full bg-rose-400/75 transition-all duration-300", rightBarClassName)}
          style={{ width: `${rightPercent}%` }}
          aria-hidden="true"
        />
      </div>
      <div className={cn("flex items-center justify-between text-[11px] font-medium", valueClassName)}>
        <span className="text-emerald-200">{leftValue}</span>
        <span className="text-rose-200">{rightValue}</span>
      </div>
    </div>
  );
}
