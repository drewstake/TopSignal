import type { ButtonHTMLAttributes } from "react";
import { cn } from "./cn";

export interface ToggleProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onChange"> {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
}

export function Toggle({ checked, onChange, label, className, disabled, onClick, ...props }: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={(event) => {
        onClick?.(event);
        if (event.defaultPrevented || disabled) {
          return;
        }
        onChange(!checked);
      }}
      className={cn(
        "inline-flex items-center gap-2 rounded-xl border px-2.5 py-1.5 text-xs transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-accent/45 disabled:cursor-not-allowed disabled:opacity-60",
        checked
          ? "border-app-accent/50 bg-app-accent/15 text-app-text shadow-[0_10px_24px_-20px_rgb(var(--theme-accent)/0.65)] hover:border-app-accent/70 hover:bg-app-accent/20"
          : "border-app-border/70 bg-transparent text-app-text-soft hover:border-app-border-strong/80 hover:bg-app-accent/10 hover:text-app-text",
        className,
      )}
      {...props}
    >
      <span
        className={cn(
          "relative inline-flex h-5 w-9 rounded-full border transition",
          checked ? "border-app-accent/80 bg-app-accent/70" : "border-app-border-strong/80 bg-app-surface/90",
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
