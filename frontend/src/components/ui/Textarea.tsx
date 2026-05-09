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
        "min-h-32 w-full rounded-2xl border border-app-border bg-app-surface/70 px-3 py-3 text-sm text-app-text placeholder:text-app-muted-strong transition duration-200 hover:border-app-border-strong focus:border-app-accent/70 focus:outline-none focus:ring-2 focus:ring-app-accent/30",
        className,
      )}
      {...props}
    />
  );
});
