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
        "h-10 w-full rounded-xl border border-slate-700 bg-slate-900/70 px-3 text-sm text-slate-100 transition duration-200 hover:border-slate-600 focus:border-cyan-400/70 focus:outline-none focus:ring-2 focus:ring-cyan-500/30",
        className,
      )}
      {...props}
    >
      {children}
    </select>
  );
});
