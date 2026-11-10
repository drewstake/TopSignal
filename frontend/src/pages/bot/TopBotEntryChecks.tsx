import type { BotActivity } from "../../lib/types";
import type { MonitorSnapshot } from "./useTopBotMonitor";
import { timeLabel } from "./topBotPresentation";

type Status = "passed" | "blocked" | "unknown" | "stale";
type Check = { id: string; label: string; status: Status; detail: string };
export type EntryMonitor = Pick<MonitorSnapshot, "activity" | "status" | "updatedAt" | "error">;
const groups = [
  ["account", "Practice account eligible"], ["position", "Position and working orders"],
  ["loss", "Daily loss budget and stop risk"], ["count", "Daily entry limit"],
  ["profit", "Daily profit protection"],
  ["cooldown", "Entry cooldown"], ["session", "Trading session and cutoff"],
  ["signal", "Final depth recheck"], ["order", "Order size and protective stop"],
];
const object = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value)
  ? value as Record<string, unknown> : {};
const recent = (at: unknown, now: number) => typeof at === "string" && now - Date.parse(at) >= 0 && now - Date.parse(at) <= 30_000;
const labels: Record<Status, string> = { passed: "Passing", blocked: "Blocking", unknown: "Not checked", stale: "Stale" };

function evidence(activity: BotActivity | null | undefined, contractId: string) {
  const config = activity?.config;
  const run = activity?.runs.filter(r => r.bot_config_id === config?.id && r.account_id === config.account_id)
    .sort((a, b) => b.id - a.id)[0];
  const decision = activity?.decisions.filter(d => d.account_id === config?.account_id && d.bot_config_id === config.id
    && d.contract_id === contractId && d.bot_run_id === run?.id)
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || b.id - a.id)[0];
  const payload = object(decision?.raw_payload);
  const plan = decision?.decision_type === "risk_reject" ? object(payload.signal_order_plan) : payload;
  return { run, decision, payload, plan, snapshot: object(plan.entry_checks) };
}

export function TopBotEntryChecks({ monitor, contractId, fresh, ready, qualified, durationMet, episodeAt, depthReason, now }: {
  monitor?: EntryMonitor; contractId: string; fresh: boolean; ready: boolean; qualified: boolean;
  durationMet: boolean; episodeAt?: string | null; depthReason?: string; now: number;
}) {
  const activity = monitor?.activity?.config.contract_id === contractId ? monitor.activity : null;
  const { run, decision, payload, plan, snapshot } = evidence(activity, contractId);
  const monitoring = Boolean(activity && monitor && !monitor.error && recent(monitor.updatedAt, now));
  const pause = object(run?.raw_state?.instrument_pause);
  const sameEpisode = Boolean(episodeAt && snapshot.episode_at === episodeAt);
  const current = monitoring && fresh && sameEpisode && recent(snapshot.checked_at, now);
  const guard = activity?.profit_guard;
  const guardChecked = Boolean(monitoring && guard?.available && !guard.error && recent(guard.observed_at, now));
  const money = (value: number | null | undefined) => typeof value === "number" && Number.isFinite(value)
    ? `${value >= 0 ? "+" : "−"}$${Math.abs(value).toFixed(2)}` : "not yet measured";
  const profitCheck: Check | null = guard ? {
    id: "profit", label: "Daily profit protection",
    status: !monitoring ? "unknown" : guard.trigger || !guard.available || guard.error ? "blocked" : guardChecked ? "passed" : "unknown",
    detail: guard.trigger === "daily_profit_target_reached" ? "+$400 target reached. Closing the TopBot position; entries paused until the next session."
      : guard.trigger === "daily_profit_breakeven_stop" ? "Returned to $0 after reaching +$200. Closing the TopBot position; entries paused until the next session."
      : guard.error || guard.message || "Break-even profit stop disabled. Daily target +$400.",
  } : null;
  const local: Check[] = [
    { id: "depth", label: "Fresh two-sided depth", status: !fresh ? "unknown" : ready ? "passed" : "blocked",
      detail: fresh && ready ? "Worker depth is fresh and valid." : depthReason || "Waiting for fresh worker depth." },
    { id: "threshold", label: "Imbalance and duration", status: !fresh ? "unknown" : qualified && durationMet ? "passed" : "blocked",
      detail: qualified && durationMet && fresh ? "Threshold and required duration met." : "Waiting for a sustained qualifying imbalance." },
    { id: "run", label: "Bot running", status: !monitoring ? "unknown" : activity?.config.enabled && run?.status === "running" ? "passed" : "blocked",
      detail: monitoring ? run?.status === "running" && activity?.config.enabled ? "Run is enabled." : "Start or resume the bot to allow entries." : "Waiting for current bot status." },
    { id: "worker", label: "Worker health", status: !monitoring ? "unknown" : monitor?.status.color === "green" ? "passed" : "unknown",
      detail: monitor?.status.label || "Waiting for worker status." },
    { id: "mode", label: "Broker execution mode", status: !monitoring ? "unknown" : activity?.config.execution_mode === "live" ? "passed" : "blocked",
      detail: !monitoring ? "Waiting for current execution mode." : activity?.config.execution_mode === "live"
        ? "Broker-routing mode selected; final entry checks still apply." : "Dry run: orders are not sent to the broker." },
    { id: "episode", label: "New signal episode", status: !monitoring || !fresh || !episodeAt ? "unknown"
      : run?.raw_state?.dom_episode_evaluated === episodeAt ? "blocked"
        : run?.raw_state && "dom_episode_evaluated" in run.raw_state ? "passed" : "unknown",
      detail: run?.raw_state?.dom_episode_evaluated === episodeAt
        ? "This episode was already evaluated. Waiting for a new qualifying episode."
        : "The worker evaluates each qualifying episode once. Checked against the last worker update." },
  ];
  const saved = Array.isArray(snapshot.checks) ? snapshot.checks.flatMap((value): Check[] => {
    const row = object(value);
    if (typeof row.id !== "string" || typeof row.label !== "string" || typeof row.detail !== "string"
      || !["passed", "blocked", "unknown"].includes(String(row.status))) return [];
    const status = row.status as Status;
    return [{ id: row.id, label: row.label, status: status === "unknown" ? status : current ? status : "stale",
      detail: current || status === "unknown" ? row.detail : `Previously ${status === "passed" ? "passed" : "blocked"}: ${row.detail}` }];
  }) : [];
  const checks = [...local, ...groups.map(([id, label]) => (id === "profit" ? profitCheck : null) ?? saved.find(row => row.id === id)
    ?? { id, label, status: "unknown" as const, detail: "Awaiting a recorded broker entry check." }),
  ...saved.filter(row => !groups.some(([id]) => id === row.id))];
  if (pause.contract_id === contractId) checks.unshift({
    id: "instrument", label: "Instrument recovery",
    status: !monitoring ? "stale" : !activity?.config.enabled || run?.status !== "running"
      || typeof pause.retry_at === "string" && Date.parse(pause.retry_at) > now ? "blocked" : "unknown",
    detail: !activity?.config.enabled || run?.status !== "running" ? "Automatic recovery is stopped. The bot must be explicitly started again."
      : `ProjectX temporarily rejected entries. Automatic recovery is armed; a fresh setup and all entry checks are required.${
      typeof pause.retry_at === "string" ? ` Next eligible recheck: ${timeLabel(pause.retry_at)}.` : ""}`,
  });
  // Older servers and checks performed after the risk pass still expose explicit
  // rejection evidence. Never infer passing checks from the absence of an error.
  if (Array.isArray(payload.risk_blocks)) payload.risk_blocks.forEach((value, index) => {
    const block = object(value);
    if (typeof block.message === "string" && !checks.some(row => row.detail.includes(block.message as string))) {
      checks.push({ id: `rejection-${index}`, label: "Recorded rejection",
        status: monitoring && fresh && object(plan.depth_entry).episode_at === episodeAt && recent(decision?.created_at, now) ? "blocked" : "stale",
        detail: block.message });
    }
  });
  const attempt = activity?.order_attempts.find(row => row.bot_decision_id === (payload.signal_decision_id ?? decision?.id)
    && row.account_id === activity.config.account_id && row.bot_config_id === activity.config.id && row.bot_run_id === run?.id && row.contract_id === contractId);
  if (attempt) checks.push({ id: "attempt", label: "Order outcome", status:
    ["blocked", "rejected", "error"].includes(attempt.status)
      ? monitoring && fresh && object(plan.depth_entry).episode_at === episodeAt && recent(attempt.updated_at, now) ? "blocked" : "stale" : "unknown",
    detail: `${attempt.status.replaceAll("_", " ")}${attempt.rejection_reason ? `: ${attempt.rejection_reason}` : ""} · ${timeLabel(attempt.updated_at)}` });
  return <section className="topbot-entry-checks" aria-label="Entry checks">
    <div className="topbot-section-heading"><h4>Entry checks</h4><span>Live depth · recorded broker checks</span></div>
    {guard && <p className="topbot-muted">Daily account P&amp;L: {money(guard.pnl)} · Highest observed: {money(guard.peak_pnl)}.
      {guard.observed_at ? ` Observed ${timeLabel(guard.observed_at)}${guardChecked ? "" : " · last recorded"}.` : " Waiting for the worker’s P&L check."}
      {" "}Includes realized net P&amp;L and unrealized P&amp;L. Daily protection resets at 6 p.m. New York.</p>}
    <ul>{checks.map(check => <li key={check.id} data-status={check.status}>
      <div><strong>{check.label}</strong><span>{labels[check.status]}</span></div><small>{check.detail}</small>
    </li>)}</ul>
    <p className="topbot-footnote">{typeof snapshot.checked_at === "string" ? `Broker checks recorded ${timeLabel(snapshot.checked_at)}. ` : "Broker checks have not been recorded for this evaluation. "}
      Recorded checks expire after 30 seconds or a signal change. Passing checks do not confirm an order or fill.</p>
  </section>;
}
