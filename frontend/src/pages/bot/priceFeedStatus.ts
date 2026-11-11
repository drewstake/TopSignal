import type { MarketPriceStreamHealth } from "../../lib/api";

export type PriceFeedConnection = "live" | "delayed" | "reconnecting" | "stale" | "unavailable" | "closed";

/** Heartbeats arrive every 5s; three missed ones mean the path is not live. */
export const HEARTBEAT_LIVENESS_WINDOW_MS = 15_000;
/** Older backends only send a keepalive after 15s without prices. */
export const LEGACY_LIVENESS_WINDOW_MS = 40_000;
/** A REST quote this recent is a usable, explicitly delayed fallback. */
export const POLL_FRESH_WINDOW_MS = 11_000;
/** Show "Reconnecting" (not stale) this long after the feed was last confirmed. */
export const RECOVERY_GRACE_MS = 30_000;
/** Without a trade for this long, the price is the last trade, not a moving one. */
export const QUIET_TAPE_AFTER_MS = 30_000;

export interface PriceFeedInputs {
  nowMs: number;
  /** Exchange schedule says the session is open. */
  sessionOpen: boolean;
  /** Any last-known price is available to display. */
  hasPrice: boolean;
  /** Latest stream health, or null when the stream reports none. */
  health: MarketPriceStreamHealth | null;
  /** Browser time the current stream subscription started or resumed. */
  streamStartedAtMs: number | null;
  /** Browser time the newest accepted stream trade arrived (path evidence). */
  lastStreamPriceAtMs: number | null;
  /** Provider timestamp of the newest trade shown (trade recency). */
  lastTradeTimestampMs: number | null;
  /** Browser time the newest REST quote was fetched successfully. */
  lastPollSuccessAtMs: number | null;
  /** Browser time the feed was last confirmed live (healthy stream). */
  lastConfirmedLiveAtMs: number | null;
}

export interface PriceFeedStatus {
  connection: PriceFeedConnection;
  label: string;
  /** Price chip prefix: Live only while trades are actually streaming. */
  pricePrefix: "Live" | "Last" | "Delayed" | "Stale";
  /** Diagnostic detail for the tooltip; observations only. */
  detail: string;
  /** True when the stream itself is confirmed healthy right now. */
  streamHealthy: boolean;
}

function latest(...values: Array<number | null | undefined>): number | null {
  const finite = values.filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  return finite.length === 0 ? null : Math.max(...finite);
}

export function formatAge(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 90) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  return minutes < 90 ? `${minutes}m` : `${Math.round(minutes / 60)}h`;
}

/** True when the stream path is demonstrably delivering right now. */
export function isPriceStreamHealthy(inputs: Pick<PriceFeedInputs, "nowMs" | "health" | "lastStreamPriceAtMs">): boolean {
  const { nowMs, health, lastStreamPriceAtMs } = inputs;
  const activityAt = latest(health?.lastActivityAt, lastStreamPriceAtMs);
  if (activityAt === null) return false;
  const window = health?.heartbeats ? HEARTBEAT_LIVENESS_WINDOW_MS : LEGACY_LIVENESS_WINDOW_MS;
  const recentTrade = lastStreamPriceAtMs !== null && nowMs - lastStreamPriceAtMs <= HEARTBEAT_LIVENESS_WINDOW_MS;
  if (nowMs - activityAt > window) return false;
  if (!health) return recentTrade;
  if (health.transport !== "open") return false;
  // A trade that just arrived proves the upstream path, even before a state report.
  // Provider silence is reported in the detail only: the backend's websocket
  // ping/pong timeout already drops a dead ProjectX socket to "reconnecting",
  // and quiet tape on a live socket must not read as a fault.
  return health.upstream === "connected" || (health.upstream === null && recentTrade);
}

/**
 * Classifies the price feed from measured signals only. A quiet market with a
 * healthy stream stays live; stale is reserved for a path that has stopped
 * being confirmed, and the first moments of a reconnect read "Reconnecting".
 */
export function resolvePriceFeedStatus(inputs: PriceFeedInputs): PriceFeedStatus {
  const { nowMs, sessionOpen, hasPrice, health, lastPollSuccessAtMs } = inputs;
  const detail = describePriceFeed(inputs);
  if (!sessionOpen || health?.upstream === "market_closed") {
    return { connection: "closed", label: "Market closed", pricePrefix: "Last", detail, streamHealthy: false };
  }
  const streamHealthy = isPriceStreamHealthy(inputs);
  if (streamHealthy && hasPrice) {
    const quietMs = tradeAgeMs(inputs);
    const trading = quietMs !== null && quietMs <= QUIET_TAPE_AFTER_MS;
    return {
      connection: "live",
      // A stable label: the trade age lives in the detail, so the announced
      // status does not change every few seconds during quiet tape.
      label: trading ? "Live stream" : "Live · quiet",
      pricePrefix: trading ? "Live" : "Last",
      detail,
      streamHealthy,
    };
  }
  if (hasPrice && lastPollSuccessAtMs !== null && nowMs - lastPollSuccessAtMs <= POLL_FRESH_WINDOW_MS) {
    return { connection: "delayed", label: "Delayed / polling", pricePrefix: "Delayed", detail, streamHealthy };
  }
  const stopped = health?.transport === "stopped" || health?.upstream === "unavailable";
  const confirmedAt = latest(inputs.lastConfirmedLiveAtMs, inputs.streamStartedAtMs);
  if (!stopped && confirmedAt !== null && nowMs - confirmedAt <= RECOVERY_GRACE_MS) {
    return { connection: "reconnecting", label: "Reconnecting", pricePrefix: "Last", detail, streamHealthy };
  }
  if (hasPrice) {
    return { connection: "stale", label: "Stale price feed", pricePrefix: "Stale", detail, streamHealthy };
  }
  return { connection: "unavailable", label: "Live unavailable", pricePrefix: "Last", detail, streamHealthy };
}

/**
 * Age of the newest trade by its provider timestamp, on the backend clock
 * when known. A replayed last trade arrives now but may be minutes old.
 */
export function tradeAgeMs(inputs: Pick<PriceFeedInputs, "nowMs" | "health" | "lastTradeTimestampMs">): number | null {
  if (inputs.lastTradeTimestampMs === null || !Number.isFinite(inputs.lastTradeTimestampMs)) return null;
  return Math.max(0, inputs.nowMs + (inputs.health?.serverClockOffsetMs ?? 0) - inputs.lastTradeTimestampMs);
}

const INTERRUPTION_LABELS: Record<string, string> = {
  idle_timeout: "no data received",
  stale_after_wake: "silent after sleep/network change",
  network_error: "network or backend unreachable",
  closed: "connection closed",
  aborted: "aborted",
  stream_error: "stream error",
};

/** One line that separates browser/network, backend and ProjectX symptoms. */
export function describePriceFeed(inputs: PriceFeedInputs): string {
  const { nowMs, health, lastStreamPriceAtMs, lastPollSuccessAtMs } = inputs;
  const parts: string[] = [];
  if (health) {
    const transport = health.transport === "retrying" && health.retryAt
      ? `retrying in ${formatAge(Math.max(0, health.retryAt - nowMs))}`
      : health.transport;
    parts.push(`TopSignal stream: ${transport}`);
    parts.push(`ProjectX: ${health.upstream ?? "awaiting status"}`);
    if (health.lastActivityAt !== null) parts.push(`last data ${formatAge(nowMs - health.lastActivityAt)} ago`);
    if (health.upstreamIdleMs !== null && health.upstreamIdleMs > 20_000) {
      parts.push(`ProjectX silent ${formatAge(health.upstreamIdleMs)}`);
    }
    if (health.serverClockOffsetMs !== null && Math.abs(health.serverClockOffsetMs) >= 2_000) {
      parts.push(`clock offset ${health.serverClockOffsetMs > 0 ? "+" : "−"}${formatAge(Math.abs(health.serverClockOffsetMs))}`);
    }
    if (health.reconnects > 0) {
      const reason = health.lastInterruption ? INTERRUPTION_LABELS[health.lastInterruption] ?? health.lastInterruption : "unknown";
      parts.push(`${health.reconnects} reconnect${health.reconnects === 1 ? "" : "s"} (last: ${reason})`);
    }
  } else {
    parts.push("TopSignal stream: not running");
  }
  const tradeAge = tradeAgeMs(inputs);
  parts.push(tradeAge === null ? "no trade yet" : `last trade ${formatAge(tradeAge)} ago`);
  if (lastStreamPriceAtMs === null) parts.push("no streamed trade yet");
  if (lastPollSuccessAtMs !== null) parts.push(`REST quote ${formatAge(nowMs - lastPollSuccessAtMs)} ago`);
  return parts.join(" · ");
}
