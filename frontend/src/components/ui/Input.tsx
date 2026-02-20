import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "./cn";

export type InputProps = InputHTMLAttributes<HTMLInputElement>;

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input({ className, ...props }, ref) {
  return (
    <input
      ref={ref}
      className={cn(
        "h-10 w-full rounded-xl border border-slate-700 bg-slate-900/70 px-3 text-sm text-slate-100 placeholder:text-slate-500 transition duration-200 hover:border-slate-600 focus:border-cyan-400/70 focus:outline-none focus:ring-2 focus:ring-cyan-500/30",
        className,
      )}
      {...props}
    />
  );
});
