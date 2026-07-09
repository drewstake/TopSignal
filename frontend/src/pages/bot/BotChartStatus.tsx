import type { ReactNode } from "react";

import type { BotChartViewState } from "./botChartViewState";

export type BotChartConnectionState = "live" | "delayed" | "stale" | "unavailable";
export type BotChartBarState = "partial" | "closed" | "none";

export interface BotChartStatusProps {
  connection: BotChartConnectionState;
  connectionTitle?: string;
  barState: BotChartBarState;
  lastRefreshText: string | null;
  lastRefreshTitle?: string;
  stale: boolean;
  unrepairedGapCount: number;
  timeframeLabel: string;
  timezoneLabel: string;
}

export function BotChartStatus({
  connection,
  connectionTitle,
  barState,
  lastRefreshText,
  lastRefreshTitle,
  stale,
  unrepairedGapCount,
  timeframeLabel,
  timezoneLabel,
}: BotChartStatusProps) {
  const connectionLabel =
    connection === "live"
      ? "Live stream"
      : connection === "delayed"
        ? "Delayed / polling"
        : connection === "stale"
          ? "Stale price feed"
          : "Live unavailable";
  const connectionTone =
    connection === "live"
      ? "border-app-positive/35 bg-app-positive/10 text-app-positive"
      : connection === "delayed"
        ? "border-app-warning/35 bg-app-warning/10 text-app-warning"
        : connection === "stale"
          ? "border-app-negative/35 bg-app-negative/10 font-semibold text-app-negative"
          : "border-app-border bg-app-bg/55 text-app-muted";

  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[11px]" aria-label="Chart data status">
      <StatusPill className={connectionTone} title={connectionTitle} announce>
        <span className={`h-1.5 w-1.5 rounded-full ${connection === "live" ? "animate-pulse bg-app-positive" : "bg-current opacity-65"}`} />
        {connectionLabel}
      </StatusPill>
      {barState !== "none" ? (
        <StatusPill className={barState === "partial" ? "border-app-accent/35 bg-app-accent/10 text-app-accent" : "border-app-border bg-app-bg/55 text-app-text-soft"}>
          {barState === "partial" ? "Partial bar" : "Closed bar"}
        </StatusPill>
      ) : null}
      {lastRefreshText ? (
        <StatusPill
          className={stale ? "border-app-warning/35 bg-app-warning/10 font-semibold text-app-warning" : "border-app-border bg-app-bg/55 text-app-muted"}
          title={lastRefreshTitle}
        >
          {stale ? "Stale · " : ""}{lastRefreshText}
        </StatusPill>
      ) : null}
      {unrepairedGapCount > 0 ? (
        <StatusPill className="border-app-warning/35 bg-app-warning/10 font-semibold text-app-warning">
          {unrepairedGapCount} unrepaired gap{unrepairedGapCount === 1 ? "" : "s"}
        </StatusPill>
      ) : null}
      <StatusPill className="border-app-border bg-app-bg/55 text-app-muted">
        {timeframeLabel} · {timezoneLabel}
      </StatusPill>
    </div>
  );
}

export function BotChartStateOverlay({ state }: { state: BotChartViewState }) {
  if (state.kind === "ready") {
    return null;
  }

  const className =
    state.kind === "error"
      ? "absolute inset-x-4 top-4 z-30 rounded-xl border border-app-negative/35 bg-app-negative/10 px-4 py-3 text-sm text-app-negative"
      : "absolute inset-0 z-20 grid place-items-center bg-app-bg/45 px-4 text-center text-sm text-app-muted";

  return (
    <div className={className} role={state.kind === "error" ? "alert" : "status"} aria-busy={state.kind === "loading" || undefined}>
      <span className="block max-w-[20rem]">{state.message}</span>
    </div>
  );
}

function StatusPill({
  className,
  title,
  announce = false,
  children,
}: {
  className: string;
  title?: string;
  announce?: boolean;
  children: ReactNode;
}) {
  return (
    <span
      className={`inline-flex h-7 items-center gap-1.5 whitespace-nowrap rounded-md border px-2 ${className}`}
      title={title}
      aria-live={announce ? "polite" : undefined}
      aria-atomic={announce || undefined}
    >
      {children}
    </span>
  );
}
