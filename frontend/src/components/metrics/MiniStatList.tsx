import { cn } from "../ui/cn";

export interface MiniStatItem {
  label: string;
  value: string;
  valueClassName?: string;
}

interface MiniStatListProps {
  items: MiniStatItem[];
  columns?: 1 | 2;
  className?: string;
}

export function MiniStatList({ items, columns = 2, className }: MiniStatListProps) {
  return (
    <dl className={cn("grid gap-1", columns === 1 ? "grid-cols-1" : "grid-cols-2", className)}>
      {items.map((item, index) => (
        <div
          key={`${item.label}-${index}`}
          className="flex items-center justify-between gap-1.5 rounded-md border border-app-border/70 bg-app-bg/35 px-2 py-1 text-[10px] leading-tight"
        >
          <dt className="text-app-muted">{item.label}</dt>
          <dd className={cn("text-right font-medium text-app-text", item.valueClassName)}>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
