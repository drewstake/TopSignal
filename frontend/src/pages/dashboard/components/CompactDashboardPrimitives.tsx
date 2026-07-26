import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import { Card } from "../../../components/ui/Card";
import { cn } from "../../../components/ui/cn";

export const compactFocusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-focus focus-visible:ring-offset-2 focus-visible:ring-offset-app-surface";

export function InfoPopover({
  label,
  triggerLabel,
  align = "start",
}: {
  label: string;
  triggerLabel: string;
  align?: "start" | "end";
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLSpanElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const tooltipRef = useRef<HTMLSpanElement>(null);
  const rawId = useId();
  const popoverId = `${rawId.replace(/:/g, "")}-compact-info`;
  const updatePosition = useCallback(() => {
    const button = buttonRef.current;
    const tooltip = tooltipRef.current;
    if (!button || !tooltip || typeof window === "undefined") {
      return;
    }
    const viewportPadding = 8;
    const gap = 8;
    const rect = button.getBoundingClientRect();
    const width = Math.max(0, Math.min(256, window.innerWidth - viewportPadding * 2));
    tooltip.style.width = `${width}px`;
    const tooltipHeight = tooltip.offsetHeight || 96;
    const preferredLeft = align === "end" ? rect.right - width : rect.left;
    const left = Math.min(
      Math.max(viewportPadding, preferredLeft),
      Math.max(viewportPadding, window.innerWidth - width - viewportPadding),
    );
    const below = rect.bottom + gap;
    const above = rect.top - tooltipHeight - gap;
    const top = below + tooltipHeight <= window.innerHeight - viewportPadding
      ? below
      : Math.max(viewportPadding, above);
    tooltip.style.top = `${top}px`;
    tooltip.style.left = `${left}px`;
    tooltip.style.visibility = "visible";
  }, [align]);

  useLayoutEffect(() => {
    if (open) {
      updatePosition();
    }
  }, [open, updatePosition]);

  useEffect(() => {
    if (!open) {
      return undefined;
    }

    const handlePointerDown = (event: PointerEvent) => {
      if (
        !rootRef.current?.contains(event.target as Node)
        && !tooltipRef.current?.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        buttonRef.current?.focus();
      }
    };

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open, updatePosition]);

  return (
    <span ref={rootRef} className="relative inline-flex shrink-0">
      <button
        ref={buttonRef}
        type="button"
        className={cn(
          "inline-flex h-11 w-11 items-center justify-center rounded-xl text-app-muted-text transition hover:bg-app-accent/10 hover:text-app-text",
          compactFocusRing,
        )}
        aria-label={`${triggerLabel} information`}
        aria-expanded={open}
        aria-controls={popoverId}
        aria-describedby={open ? popoverId : undefined}
        onClick={() => setOpen((current) => !current)}
      >
        <span
          aria-hidden="true"
          className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-app-border-strong text-xs font-bold"
        >
          i
        </span>
      </button>
      {open && typeof document !== "undefined" ? createPortal(
        <span
          ref={tooltipRef}
          id={popoverId}
          role="tooltip"
          className="fixed z-[100] rounded-xl border border-app-border bg-app-surface-raised p-3 text-left text-xs font-normal leading-5 text-app-text shadow-panel"
          style={{ visibility: "hidden" }}
        >
          {label}
        </span>,
        document.body,
      ) : null}
    </span>
  );
}

export function CompactPanel({
  title,
  info,
  className,
  headerActions,
  children,
}: {
  title: string;
  info?: string;
  className?: string;
  headerActions?: ReactNode;
  children: ReactNode;
}) {
  const rawId = useId();
  const titleId = `${rawId.replace(/:/g, "")}-compact-title`;

  return (
    <Card
      aria-labelledby={titleId}
      className={cn("min-w-0 overflow-hidden p-0 md:p-0", className)}
    >
      <div className="flex min-h-14 flex-wrap items-center gap-1 border-b border-app-border/70 px-3 py-1.5 sm:px-4">
        <h2 id={titleId} className="text-sm font-semibold text-app-text">
          {title}
        </h2>
        {info ? <InfoPopover label={info} triggerLabel={title} /> : null}
        {headerActions ? <div className="ml-auto">{headerActions}</div> : null}
      </div>
      {children}
    </Card>
  );
}

export function CompactState({
  kind,
  title,
  detail,
  minHeightClassName = "min-h-[230px]",
  announce = true,
}: {
  kind: "loading" | "empty" | "error" | "insufficient";
  title: string;
  detail?: string;
  minHeightClassName?: string;
  announce?: boolean;
}) {
  const isError = kind === "error";
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center px-5 py-8 text-center",
        minHeightClassName,
      )}
      role={announce ? (isError ? "alert" : "status") : undefined}
      aria-live={announce ? (isError ? "assertive" : "polite") : undefined}
    >
      {kind === "loading" ? (
        <span
          className="mb-3 h-7 w-7 animate-spin rounded-full border-2 border-app-border border-t-app-accent motion-reduce:animate-none"
          aria-hidden="true"
        />
      ) : (
        <span
          className={cn(
            "mb-3 inline-flex h-9 w-9 items-center justify-center rounded-xl text-lg",
            isError
              ? "bg-app-negative-soft/25 text-app-negative-text"
              : "bg-app-surface-raised text-app-muted-text",
          )}
          aria-hidden="true"
        >
          {isError ? "!" : "—"}
        </span>
      )}
      <p className={cn("text-sm font-medium", isError ? "text-app-negative-text" : "text-app-text")}>{title}</p>
      {detail ? <p className="mt-1 max-w-sm text-xs leading-5 text-app-muted-text">{detail}</p> : null}
    </div>
  );
}

type MetricKind = "net" | "expectancy" | "profit" | "win" | "payoff";

function MetricGlyph({ kind }: { kind: MetricKind }) {
  return (
    <span
      className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-app-accent/10 text-app-accent"
      aria-hidden="true"
    >
      <svg
        viewBox="0 0 24 24"
        className="h-5 w-5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {kind === "net" ? (
          <>
            <path d="M4 18V8" />
            <path d="M9 18V12" />
            <path d="M14 18V5" />
            <path d="M19 18V9" />
          </>
        ) : kind === "expectancy" ? (
          <>
            <path d="M4 18l5-5 3 3 8-9" />
            <path d="M15 7h5v5" />
          </>
        ) : kind === "win" ? (
          <>
            <path d="M12 3a9 9 0 1 0 9 9" />
            <path d="M12 3v9h9" />
          </>
        ) : kind === "profit" ? (
          <>
            <path d="M5 17 9 13l3 3 7-9" />
            <path d="M15 7h4v4" />
          </>
        ) : (
          <>
            <path d="M4 8h16" />
            <path d="M4 16h16" />
            <path d="M8 5v6" />
            <path d="M16 13v6" />
          </>
        )}
      </svg>
    </span>
  );
}

export function CompactMetricCard({
  label,
  info,
  value,
  kind,
  valueClassName,
  loading,
  error,
  unavailableReason,
  unavailableLabel = "Not enough data",
  className,
  children,
}: {
  label: string;
  info: string;
  value: string;
  kind: MetricKind;
  valueClassName?: string;
  loading: boolean;
  error: string | null;
  unavailableReason?: string;
  unavailableLabel?: string;
  className?: string;
  children?: ReactNode;
}) {
  const titleId = `${useId().replace(/:/g, "")}-metric-title`;
  return (
    <Card
      aria-busy={loading}
      aria-labelledby={titleId}
      className={cn("min-h-[112px] min-w-0 p-3 sm:min-h-[132px] sm:p-3.5 md:p-4", className)}
    >
      <div className="flex h-full min-w-0 items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex min-h-8 items-center gap-0 text-xs font-medium text-app-muted-text">
            <h2 id={titleId} className="text-xs font-medium">{label}</h2>
            <InfoPopover label={info} triggerLabel={label} />
          </div>
          {loading ? (
            <div role="status" aria-live="polite" className="mt-2">
              <span className="sr-only">Loading {label}</span>
              <div className="h-7 w-24 animate-pulse rounded-lg bg-app-border/45 motion-reduce:animate-none" />
              <div className="mt-3 h-3 w-32 max-w-full animate-pulse rounded bg-app-border/30 motion-reduce:animate-none" />
            </div>
          ) : error ? (
            <div role="status" className="mt-2">
              <p className="text-sm font-semibold text-app-negative-text">Unavailable</p>
              <p className="mt-1 text-xs text-app-muted-text">Could not load this metric.</p>
            </div>
          ) : unavailableReason ? (
            <div role="status" className="mt-2">
              <p className="text-sm font-semibold text-app-text">{unavailableLabel}</p>
              <p className="mt-1 text-xs leading-4 text-app-muted-text">{unavailableReason}</p>
            </div>
          ) : (
            <>
              <p className={cn(
                "mt-1 truncate text-2xl font-semibold tracking-tight",
                valueClassName || "text-app-text",
              )}>
                {value}
              </p>
              {children}
            </>
          )}
        </div>
        <div className="mt-1 shrink-0 max-[479px]:hidden">
          <MetricGlyph kind={kind} />
        </div>
      </div>
    </Card>
  );
}
