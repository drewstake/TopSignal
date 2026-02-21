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
    <dl className={cn("grid gap-1.5", columns === 1 ? "grid-cols-1" : "grid-cols-2", className)}>
      {items.map((item, index) => (
        <div
          key={`${item.label}-${index}`}
          className="flex items-center justify-between gap-2 rounded-md border border-slate-800/70 bg-slate-950/35 px-2 py-1 text-[11px]"
        >
          <dt className="text-slate-400">{item.label}</dt>
          <dd className={cn("font-medium text-slate-100", item.valueClassName)}>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

