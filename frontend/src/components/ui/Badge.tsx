import type { HTMLAttributes } from "react";
import { cn } from "./cn";

type BadgeVariant = "positive" | "negative" | "neutral" | "accent" | "warning";

const styles: Record<BadgeVariant, string> = {
  positive: "border-app-positive/40 bg-app-positive/10 text-app-positive",
  negative: "border-app-negative/40 bg-app-negative/10 text-app-negative",
  neutral: "border-app-border-strong bg-app-border-strong/40 text-app-text-soft",
  accent: "border-app-accent/40 bg-app-accent/10 text-app-accent",
  warning: "border-app-warning/40 bg-app-warning/10 text-app-warning",
};

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
}

export function Badge({ className, variant = "neutral", ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-1 text-[11px] font-medium uppercase tracking-wide",
        styles[variant],
        className,
      )}
      {...props}
    />
  );
}
