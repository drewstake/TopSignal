import type { ButtonHTMLAttributes } from "react";
import { cn } from "./cn";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "sm" | "md" | "lg";

const buttonVariants: Record<ButtonVariant, string> = {
  primary:
    "bg-cyan-500/90 text-slate-950 hover:bg-cyan-400 focus-visible:ring-cyan-300 shadow-[0_8px_24px_-16px_rgba(34,211,238,0.95)]",
  secondary: "bg-slate-800 text-slate-100 hover:bg-slate-700 focus-visible:ring-slate-400",
  ghost: "bg-transparent text-slate-200 hover:bg-slate-800/70 focus-visible:ring-slate-400",
  danger: "bg-rose-500/85 text-rose-50 hover:bg-rose-400 focus-visible:ring-rose-300",
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
