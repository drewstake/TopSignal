import type { BotActivity, BotDecision } from "../../lib/types";
import { candleEndMs, openSessionElapsedSeconds } from "./botMarketContext";
import { intervalSecondsFor } from "./botCandleGaps";

export const timeLabel = (value: string | null | undefined) => value && Number.isFinite(Date.parse(value))
  ? new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit", timeZone: "America/New_York", timeZoneName: "short" }).format(new Date(value)) : "Not available";
export const priceLabel = (value: unknown) => typeof value === "number" && Number.isFinite(value)
  ? value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—";
export const latestDecision = (activity: BotActivity | null) => activity?.decisions.slice()
  .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || b.id - a.id)[0] ?? null;

export function decisionIsStale(decision: BotDecision, activity: BotActivity, now: number) {
  const { config } = activity;
  if (!decision.candle_timestamp) return true;
  const end = candleEndMs(Date.parse(decision.candle_timestamp), config.timeframe_unit, config.timeframe_unit_number);
  if (!Number.isFinite(end) || end > now) return true;
  const threshold = Math.max(60, config.max_data_staleness_seconds ?? 0,
    intervalSecondsFor(config.timeframe_unit, config.timeframe_unit_number) * 3);
  return openSessionElapsedSeconds(end, now, config.symbol ?? config.contract_id, threshold) > threshold;
}

export function recordedEvents(activity: BotActivity | null) {
  if (!activity) return [];
  return [
    ...activity.decisions.map(row => ({ key: `decision-${row.id}`, at: row.created_at, kind: "Decision", text: row.reason, label: row.action === "HOLD" ? "NO TRADE" : row.action })),
    ...activity.order_attempts.map(row => ({ key: `order-${row.id}`, at: row.updated_at, kind: "Order", text: `${row.side} · ${row.size} contracts · ${row.status.replaceAll("_", " ")}${row.rejection_reason ? ` · ${row.rejection_reason}` : ""}`, label: row.execution_mode === "live" ? "LIVE" : "RECORDED" })),
    ...activity.risk_events.map(row => ({ key: `risk-${row.id}`, at: row.created_at, kind: "Risk check", text: row.message, label: row.severity.toUpperCase() })),
  ].sort((a, b) => Date.parse(b.at) - Date.parse(a.at)).filter((row, index, all) =>
    index === 0 || row.kind !== all[index - 1].kind || row.text !== all[index - 1].text,
  ).slice(0, 8);
}
