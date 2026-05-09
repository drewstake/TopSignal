import { forwardRef, type SelectHTMLAttributes } from "react";
import { cn } from "./cn";

export type SelectProps = SelectHTMLAttributes<HTMLSelectElement>;

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, children, ...props },
  ref,
) {
  return (
    <select
      ref={ref}
      className={cn(
        "h-10 w-full rounded-xl border border-app-border bg-app-surface/70 px-3 text-sm text-app-text transition duration-200 hover:border-app-border-strong focus:border-app-accent/70 focus:outline-none focus:ring-2 focus:ring-app-accent/30",
        className,
      )}
      {...props}
    >
      {children}
    </select>
  );
});
