import { useEffect, useId, useRef, useState } from "react";

import { cn } from "../ui/cn";

interface InfoPopoverProps {
  content: string;
  label?: string;
  className?: string;
  panelClassName?: string;
}

export function InfoPopover({ content, label = "Metric definition", className, panelClassName }: InfoPopoverProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) {
      return;
    }

    function onDocumentMouseDown(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }

    function onDocumentKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }

    document.addEventListener("mousedown", onDocumentMouseDown);
    document.addEventListener("keydown", onDocumentKeyDown);

    return () => {
      document.removeEventListener("mousedown", onDocumentMouseDown);
      document.removeEventListener("keydown", onDocumentKeyDown);
    };
  }, [open]);

  return (
    <div ref={containerRef} className={cn("relative inline-flex", className)}>
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={panelId}
        aria-haspopup="dialog"
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "inline-flex h-5 w-5 items-center justify-center rounded-full border border-slate-700/80 bg-slate-900/80 text-[10px] font-semibold text-slate-400 transition",
          open ? "border-cyan-300/70 text-cyan-100" : "hover:border-cyan-300/65 hover:text-cyan-200",
        )}
      >
        i
      </button>
      {open ? (
        <div
          id={panelId}
          role="dialog"
          className={cn(
            "absolute right-0 top-full z-20 mt-2 w-56 rounded-lg border border-slate-700/80 bg-slate-950/95 px-3 py-2 text-[11px] leading-relaxed text-slate-200 shadow-lg",
            panelClassName,
          )}
        >
          {content}
        </div>
      ) : null}
    </div>
  );
}

