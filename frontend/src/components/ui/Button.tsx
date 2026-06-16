import type { ButtonHTMLAttributes } from "react";
import { cn } from "./cn";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "sm" | "md" | "lg";

const buttonVariants: Record<ButtonVariant, string> = {
  primary:
    "border-app-accent/70 bg-app-accent/90 text-app-accent-contrast shadow-[0_8px_24px_-16px_rgb(var(--theme-accent)/0.95)] hover:border-app-accent hover:bg-app-accent focus-visible:ring-app-accent",
  secondary:
    "border-app-accent/40 bg-app-accent/15 text-app-text shadow-[0_10px_24px_-20px_rgb(var(--theme-accent)/0.75)] hover:border-app-accent/70 hover:bg-app-accent/20 focus-visible:ring-app-accent/45",
  ghost:
    "border-app-border/65 bg-transparent text-app-text-soft hover:border-app-border-strong/80 hover:bg-app-accent/10 hover:text-app-text focus-visible:ring-app-accent/35",
  danger: "border-app-negative/70 bg-app-negative/85 text-app-bg hover:border-app-negative hover:bg-app-negative focus-visible:ring-app-negative",
};

const buttonSizes: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-xs",
  md: "h-10 px-4 text-sm",
  lg: "h-11 px-5 text-sm",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export function Button({ className, variant = "primary", size = "md", type = "button", ...props }: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-xl border font-medium transition duration-200 focus-visible:outline-none focus-visible:ring-2 disabled:cursor-not-allowed disabled:opacity-60",
        buttonVariants[variant],
        buttonSizes[size],
        className,
      )}
      {...props}
    />
  );
}
