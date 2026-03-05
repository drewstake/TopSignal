import { forwardRef, type TextareaHTMLAttributes } from "react";
import { cn } from "./cn";

export type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement>;

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { className, ...props },
  ref,
) {
  return (
    <textarea
      ref={ref}
      className={cn(
        "min-h-32 w-full rounded-2xl border border-slate-700 bg-slate-900/70 px-3 py-3 text-sm text-slate-100 placeholder:text-slate-500 transition duration-200 hover:border-slate-600 focus:border-cyan-400/70 focus:outline-none focus:ring-2 focus:ring-cyan-500/30",
        className,
      )}
      {...props}
    />
  );
});
