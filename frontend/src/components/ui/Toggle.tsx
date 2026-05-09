import type { ButtonHTMLAttributes } from "react";
import { cn } from "./cn";

export interface ToggleProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onChange"> {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
}

export function Toggle({ checked, onChange, label, className, ...props }: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={cn(
        "inline-flex items-center gap-2 rounded-xl border border-app-border bg-app-surface/70 px-2.5 py-1.5 text-xs text-app-muted transition hover:border-app-border-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-accent/45",
        className,
      )}
      {...props}
    >
      <span
        className={cn(
          "relative inline-flex h-5 w-9 rounded-full border transition",
          checked ? "border-app-accent/80 bg-app-accent/60" : "border-app-border-strong bg-app-raised",
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 h-3.5 w-3.5 rounded-full bg-app-text transition",
            checked ? "left-4" : "left-0.5",
          )}
        />
      </span>
      {label ? <span className="whitespace-nowrap">{label}</span> : null}
    </button>
  );
}
