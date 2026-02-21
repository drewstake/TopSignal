import { cn } from "../ui/cn";

interface SplitBarProps {
  leftLabel: string;
  rightLabel: string;
  leftValue: string;
  rightValue: string;
  leftMagnitude: number;
  rightMagnitude: number;
  badge: string;
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
  badge,
  className,
}: SplitBarProps) {
  const left = safeMagnitude(leftMagnitude);
  const right = safeMagnitude(rightMagnitude);
  const total = left + right;
  const leftPercent = total > 0 ? (left / total) * 100 : 50;
  const rightPercent = total > 0 ? (right / total) * 100 : 50;

  return (
    <div className={cn("mt-4 space-y-2", className)}>
      <div className="flex items-center justify-between text-[11px] text-slate-400">
        <span>{leftLabel}</span>
        <span>{rightLabel}</span>
      </div>
      <div className="relative h-3 overflow-hidden rounded-full border border-slate-700/80 bg-slate-900/85">
        <div className="h-full bg-emerald-400/80 transition-all duration-300" style={{ width: `${leftPercent}%` }} title={leftValue} />
        <div
          className="absolute right-0 top-0 h-full bg-rose-400/75 transition-all duration-300"
          style={{ width: `${rightPercent}%` }}
          title={rightValue}
        />
      </div>
      <div className="flex items-center justify-between text-xs">
        <span className="text-emerald-200">{leftValue}</span>
        <span className="rounded-full border border-cyan-300/35 bg-cyan-400/10 px-2 py-0.5 text-[11px] font-semibold text-cyan-100">
          {badge}
        </span>
        <span className="text-rose-200">{rightValue}</span>
      </div>
    </div>
  );
}

