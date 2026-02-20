import type { HTMLAttributes } from "react";
import { cn } from "./cn";

type BadgeVariant = "positive" | "negative" | "neutral" | "accent" | "warning";

const styles: Record<BadgeVariant, string> = {
  positive: "border-emerald-400/40 bg-emerald-500/10 text-emerald-300",
  negative: "border-rose-400/40 bg-rose-500/10 text-rose-300",
  neutral: "border-slate-600 bg-slate-700/40 text-slate-200",
  accent: "border-cyan-400/40 bg-cyan-500/10 text-cyan-200",
  warning: "border-amber-400/40 bg-amber-500/10 text-amber-200",
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
