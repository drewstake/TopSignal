import type { ReactNode } from "react";

import { cn } from "../ui/cn";

interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
  className?: string;
  panelClassName?: string;
  disabled?: boolean;
}

export function Tooltip({ content, children, className, panelClassName, disabled = false }: TooltipProps) {
  if (disabled) {
    return <>{children}</>;
  }

  return (
    <span className={cn("group/tooltip relative inline-flex", className)}>
      {children}
      <span
        role="tooltip"
        className={cn(
          "pointer-events-none absolute left-1/2 top-full z-20 mt-2 w-max max-w-64 -translate-x-1/2 rounded-md border border-slate-700/80 bg-slate-950/95 px-2.5 py-1.5 text-[11px] leading-relaxed text-slate-200 opacity-0 shadow-lg transition duration-200 group-hover/tooltip:opacity-100",
          panelClassName,
        )}
      >
        {content}
      </span>
    </span>
  );
}

