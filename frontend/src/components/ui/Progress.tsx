import type { HTMLAttributes } from "react";
import { cn } from "./cn";

export interface ProgressProps extends HTMLAttributes<HTMLDivElement> {
  value: number;
}

export function Progress({ value, className, ...props }: ProgressProps) {
  const clamped = Math.max(0, Math.min(100, value));

  return (
    <div className={cn("h-2 w-full overflow-hidden rounded-full bg-app-raised", className)} {...props}>
      <div
        className="h-full rounded-full bg-gradient-to-r from-app-accent to-app-secondary transition-all duration-300"
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}
