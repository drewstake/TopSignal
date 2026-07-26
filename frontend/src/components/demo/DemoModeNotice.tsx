import type { ReactNode } from "react";

import { DEMO_AS_OF_LABEL } from "../../lib/demoScenario";
import { cn } from "../ui/cn";
import { useDemoInteractionPolicy } from "./useDemoInteractionPolicy";

interface DemoModeNoticeProps {
  children?: ReactNode;
  className?: string;
  compact?: boolean;
}

export function DemoModeNotice({ children, className, compact = false }: DemoModeNoticeProps) {
  const { demoModeEnabled } = useDemoInteractionPolicy();

  if (!demoModeEnabled) {
    return null;
  }

  return (
    <aside
      className={cn(
        "rounded-xl border border-app-accent/45 bg-app-accent/10 text-app-text-soft shadow-[0_12px_30px_-26px_rgb(var(--theme-accent)/0.85)]",
        compact ? "px-3 py-2" : "px-4 py-3",
        className,
      )}
      role="note"
      aria-label="Demo data notice"
      data-demo-provenance="true"
    >
      <div className="flex items-start gap-3">
        <span className="inline-flex min-h-6 shrink-0 items-center rounded-full border border-app-accent/55 bg-app-accent/15 px-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-app-accent">
          Demo data
        </span>
        <div className="min-w-0 text-xs leading-5">
          {children ?? (
            <p>
              This workspace is isolated sample data. Connected-account sync, imports, persistence, deletion, and order routing are unavailable.
            </p>
          )}
          <p className="mt-1 text-[11px] font-medium text-app-muted-strong">
            Deterministic sample scenario as of {DEMO_AS_OF_LABEL}.
          </p>
        </div>
      </div>
    </aside>
  );
}
