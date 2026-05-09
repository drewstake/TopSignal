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
          "inline-flex items-center gap-1 rounded-full border border-app-border/80 bg-app-surface/75 px-2 py-0.5 text-[10px] text-app-muted",
          className,
        )}
      >
      <span className="text-app-muted">{label}</span>
      <span className={cn("font-semibold text-app-text", valueClassName)}>{value}</span>
    </span>
  );
}
