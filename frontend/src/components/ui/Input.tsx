import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "./cn";

export type InputProps = InputHTMLAttributes<HTMLInputElement>;

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input({ className, ...props }, ref) {
  return (
    <input
      ref={ref}
      className={cn(
        "h-10 w-full rounded-xl border border-app-border bg-app-surface/70 px-3 text-sm text-app-text placeholder:text-app-muted-strong transition duration-200 hover:border-app-border-strong focus:border-app-accent/70 focus:outline-none focus:ring-2 focus:ring-app-accent/30",
        className,
      )}
      {...props}
    />
  );
});
