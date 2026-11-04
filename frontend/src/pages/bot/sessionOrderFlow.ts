import { requestJson } from "../../lib/api";

export interface FlowWindow {
  start: string; end: string; buy: number; sell: number; unknown: number;
  trades: number; invalid: number; bad_time: number; delta: number | null;
  buy_percent: number | null; delta_percent: number | null; classified_percent: number | null;
  first_price: number | null; last_price: number | null; first_price_at: string | null; last_price_at: string | null; price_change: number | null;
}
export interface SessionOrderFlow {
  schema: "session-provider-flow-v1"; contract_id: string; data_live: boolean;
  generated_at: string; session_start: string; through: string; source: string;
  session: FlowWindow; windows: Record<"5" | "15" | "60", FlowWindow>;
  classification: { method: "projectx_reported_type"; aggressor_verified: false; source: string };
  coverage: { status: "building" | "partial" | "observed"; reasons: Record<string, number>;
    first_received_at: string | null; last_received_at: string | null; stale: boolean;
    processed_segments: number; pending_segments: number; exchange_completeness_verified: false; sealed_only: true };
  bias: "unavailable"; absorption: "unavailable"; notes: string[];
}

export function getSessionOrderFlow(account: number, contract: string, signal: AbortSignal) {
  return requestJson<SessionOrderFlow>(`/api/accounts/${account}/session-order-flow`, { signal, query: { contract_id: contract, data_live: false } });
}

// Calendar arithmetic only: the session boundary itself stays in New York time.
export function sessionKey(at: number): string {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23" }).formatToParts(at);
  const part = (name: string) => Number(parts.find(p => p.type === name)?.value);
  const day = new Date(Date.UTC(part("year"), part("month")-1, part("day")));
  if (part("hour") < 18) day.setUTCDate(day.getUTCDate()-1);
  while (day.getUTCDay() === 5 || day.getUTCDay() === 6) day.setUTCDate(day.getUTCDate()-1);
  return day.toISOString().slice(0, 10);
}

export function reportedPressure(window: FlowWindow): string {
  if (window.buy_percent == null) return "No classified trades";
  return window.buy > window.sell ? "Reported buying leads" : window.sell > window.buy ? "Reported selling leads" : "Reported volumes balanced";
}

export function reportedBias(window: FlowWindow): "bullish" | "bearish" | "balanced" | "uncertain" | "unavailable" {
  if (window.buy_percent == null || window.delta == null) return "unavailable";
  const delta = window.buy-window.sell;
  // Unknown contracts can belong to either side. Invalid sizes cannot be bounded.
  if (window.invalid > 0 || (window.unknown > 0 && Math.abs(delta) <= window.unknown)) return "uncertain";
  return delta > 0 ? "bullish" : delta < 0 ? "bearish" : "balanced";
}

export function priceResponse(window: FlowWindow): string {
  const bias = reportedBias(window);
  if (window.price_change == null || bias === "unavailable") return "Awaiting comparable trade and price observations.";
  if (bias === "uncertain") return "Unclassified volume or invalid trades prevent a directional comparison.";
  if (bias === "balanced") return "Reported buy and sell volume is balanced.";
  const side = bias === "bullish" ? "buying" : "selling";
  if (window.price_change === 0) return `Reported ${side} leads, but the first and last observed prices are equal.`;
  const aligned = (bias === "bullish") === (window.price_change > 0);
  return `Reported ${side} leads and observed price ${window.price_change > 0 ? "rose" : "fell"}: ${aligned ? "flow and price agree" : "flow/price divergence"}.`;
}

export function flowSummary(flow: SessionOrderFlow): string {
  if (flow.coverage.status === "building") return "Building session history from recorded trades.";
  if (flow.coverage.stale) return "Recording is behind. Current order-flow direction is unavailable.";
  if (flow.session.buy_percent == null) return "No classified trades available for this session.";
  const recent = flow.windows["15"];
  const basis = flow.coverage.status === "partial" ? "available recordings" : "the recorded session";
  const bias = reportedBias(flow.session);
  const recentBias = reportedBias(recent);
  if (bias === "uncertain") return "Recorded pressure is uncertain: unclassified volume or invalid trades could change the direction.";
  if (bias === "balanced") return `Reported buy and sell volume is balanced in ${basis}.`;
  if (bias === "unavailable") return "No classified trades available for this session.";
  const label = bias === "bullish" ? "Bullish" : "Bearish";
  if ((bias === "bullish" && recentBias === "bearish") || (bias === "bearish" && recentBias === "bullish")) {
    return `${label} reported pressure in ${basis}; ${recentBias === "bullish" ? "buying" : "selling"} leads the recent 15-minute sample.`;
  }
  return `${label} reported pressure in ${basis}.`;
}

export const coverageReasons: Record<string, string> = {
  session_start_missing: "Recording does not cover the session open",
  stale_recording: "Recording is behind",
  history_building: "Older recordings are still being summarized",
  invalid_manifest: "Some recording manifests could not be verified",
  unreadable_segment: "Some recorded segments could not be verified",
  overlapping_recordings: "Overlapping recording segments were excluded",
  recording_discontinuity: "Recording continuity changed",
  unreliable_trade_time: "Some trade timestamps are unreliable",
  invalid_trade: "Some trades have invalid prices or quantities",
  connect: "Connection started during this session", disconnect: "Connection interrupted",
  gap: "Recording gap reported", roll: "Contract changed", reset: "Feed reset", end: "Recording ended",
  receipt_silence: "A recording interval has no observations; continuity is uncertain",
};
