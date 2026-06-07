import type { HTMLAttributes } from "react";
import { cn } from "./cn";

export interface ProgressProps extends HTMLAttributes<HTMLDivElement> {
  value: number;
  indicatorClassName?: string;
}

export function Progress({ value, className, indicatorClassName, ...props }: ProgressProps) {
  const clamped = Math.max(0, Math.min(100, value));

  return (
    <div className={cn("h-2 w-full overflow-hidden rounded-full bg-app-raised", className)} {...props}>
      <div
        className={cn(
          "h-full rounded-full transition-all duration-300",
          indicatorClassName ?? "bg-gradient-to-r from-app-accent to-app-secondary",
        )}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}
