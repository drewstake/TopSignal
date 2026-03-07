import { useEffect, useId, useRef, useState } from "react";

import { Button } from "../../../components/ui/Button";
import { cn } from "../../../components/ui/cn";

export type JournalCopyAction = "entry" | "recent-7" | "recent-30";

export interface JournalCopyActionsProps {
  disabled?: boolean;
  activeAction: JournalCopyAction | null;
  onCopyEntry: () => void;
  onCopyRecent: (count: 7 | 30) => void;
}

const recentOptions = [
  { label: "Copy Recent (7 Entries)", count: 7 as const, action: "recent-7" as const },
  { label: "Copy Recent (30 Entries)", count: 30 as const, action: "recent-30" as const },
];

export function JournalCopyActions({
  disabled = false,
  activeAction,
  onCopyEntry,
  onCopyRecent,
}: JournalCopyActionsProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const isBusy = activeAction !== null;

  useEffect(() => {
    if (!open) {
      return;
    }

    function handleDocumentMouseDown(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }

    function handleDocumentKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }

    document.addEventListener("mousedown", handleDocumentMouseDown);
    document.addEventListener("keydown", handleDocumentKeyDown);

    return () => {
      document.removeEventListener("mousedown", handleDocumentMouseDown);
      document.removeEventListener("keydown", handleDocumentKeyDown);
    };
  }, [open]);

  return (
    <div ref={containerRef} className="relative flex items-center">
      <Button
        size="sm"
        className="h-7 rounded-r-none px-2.5 text-[11px]"
        disabled={disabled || isBusy}
        onClick={onCopyEntry}
      >
        {activeAction === "entry" ? "Copying..." : "Copy Entry"}
      </Button>
      <Button
        variant="secondary"
        size="sm"
        className="h-7 rounded-l-none border-l border-slate-700/80 px-2.5 text-[11px]"
        disabled={disabled || isBusy}
        aria-label="Open recent journal copy actions"
        aria-expanded={open}
        aria-controls={menuId}
        aria-haspopup="menu"
        onClick={() => setOpen((value) => !value)}
      >
        Recent
      </Button>

      {open ? (
        <div
          id={menuId}
          role="menu"
          aria-label="Recent journal copy actions"
          className="absolute right-0 top-full z-20 mt-2 w-56 rounded-xl border border-slate-800/85 bg-slate-950/95 p-1 shadow-lg"
        >
          {recentOptions.map((option) => (
            <button
              key={option.action}
              type="button"
              role="menuitem"
              className={cn(
                "flex w-full rounded-lg px-3 py-2 text-left text-xs text-slate-200 transition",
                "hover:bg-slate-900/90 hover:text-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/45",
              )}
              onClick={() => {
                setOpen(false);
                onCopyRecent(option.count);
              }}
            >
              {activeAction === option.action ? "Copying..." : option.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
