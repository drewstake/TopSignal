import { cn } from "../ui/cn";

interface ChipProps {
  label: string;
  value: string;
  className?: string;
  valueClassName?: string;
}

export function Chip({ label, value, className, valueClassName }: ChipProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border border-slate-700/80 bg-slate-900/75 px-2.5 py-1 text-[11px] text-slate-300",
        className,
      )}
    >
      <span className="text-slate-400">{label}</span>
      <span className={cn("font-semibold text-slate-100", valueClassName)}>{value}</span>
    </span>
  );
}

