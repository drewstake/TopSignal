import type { ButtonHTMLAttributes } from "react";
import { cn } from "./cn";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "sm" | "md" | "lg";

const buttonVariants: Record<ButtonVariant, string> = {
  primary:
    "bg-app-accent/90 text-app-accent-contrast hover:bg-app-accent focus-visible:ring-app-accent shadow-[0_8px_24px_-16px_rgb(var(--theme-accent)/0.95)]",
  secondary: "bg-app-raised text-app-text hover:bg-app-border-strong focus-visible:ring-app-muted",
  ghost: "bg-transparent text-app-text-soft hover:bg-app-raised/70 focus-visible:ring-app-muted",
  danger: "bg-app-negative/85 text-app-bg hover:bg-app-negative focus-visible:ring-app-negative",
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
        "inline-flex items-center justify-center gap-2 rounded-xl border border-transparent font-medium transition duration-200 focus-visible:outline-none focus-visible:ring-2",
        buttonVariants[variant],
        buttonSizes[size],
        className,
      )}
      {...props}
    />
  );
}
