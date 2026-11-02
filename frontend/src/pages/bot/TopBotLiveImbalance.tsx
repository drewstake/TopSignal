import { useEffect, useState } from "react";
import { botsApi } from "../../lib/api";
import { usePageVisibility } from "./usePageVisibility";

export type TopBotDepthObservation = {
  contract_id: string; as_of: string; threshold: number | null;
  depth: { ready: boolean; reason: string; bid_levels?: number; ask_levels?: number;
    bid_total?: number; ask_total?: number; imbalance?: number; observed_at?: string;
    episode_at?: string | null; persistence_seconds?: number;
    qualification_reset?: { reason: string; at: string; bid_levels: number; ask_levels: number; imbalance: number | null } | null };
};
type Sample = { value: TopBotDepthObservation; receivedAt: number; requestMs: number };
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const contracts = (value: number | undefined) => finite(value) ? value.toLocaleString("en-US", { maximumFractionDigits: 0 }) : "—";
const time = (at: string) => new Date(at).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour12: false });
function resetReason(reset: NonNullable<TopBotDepthObservation["depth"]["qualification_reset"]>) {
  if (reset.reason === "insufficient_levels") return `Depth dropped to ${reset.bid_levels} bid / ${reset.ask_levels} ask levels`;
  if (reset.reason === "below_threshold") return `Imbalance fell below the threshold${finite(reset.imbalance) ? ` (${(reset.imbalance * 100).toFixed(2)}%)` : ""}`;
  const reasons: Record<string, string> = { side_changed: "Imbalance changed sides", sequence_gap: "Depth updates were skipped",
    depth_gap: "Depth updates paused for more than 2 seconds", stale_timestamp: "Depth timestamp was stale or invalid",
    missing_timestamp: "Depth timestamp was missing", disconnected: "Depth feed disconnected", provider_reset: "Provider rebuilt the book",
    invalid_book: "Book became empty or crossed", invalid_depth: "Invalid depth update", threshold_changed: "Entry threshold changed" };
  return reasons[reset.reason] ?? "Depth continuity was lost";
}

export function TopBotLiveImbalance({ contractId, threshold, requiredSeconds, enabled }: {
  contractId: string; threshold: number; requiredSeconds: number; enabled: boolean;
}) {
  const visible = usePageVisibility();
  const [sample, setSample] = useState<Sample | null>(null);
  const [error, setError] = useState(false);
  const [clock, setClock] = useState(() => performance.now());
  useEffect(() => {
    if (!enabled || !visible) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    let request: AbortController | null = null;
    const tick = setInterval(() => setClock(performance.now()), 500);
    async function poll() {
      request = new AbortController();
      const controller = request;
      const timeout = setTimeout(() => controller.abort(), 3000);
      const started = performance.now();
      try {
        const value = await botsApi.getTopBotDepth(contractId, { signal: controller.signal });
        if (!disposed && !controller.signal.aborted && value.contract_id === contractId) {
          const receivedAt = performance.now();
          setSample({ value, receivedAt, requestMs: receivedAt - started }); setError(false); setClock(receivedAt);
        } else if (!disposed) setError(true);
      } catch {
        if (!disposed) setError(true);
      } finally {
        clearTimeout(timeout);
        if (!disposed) timer = setTimeout(poll, 1000);
      }
    }
    void poll();
    return () => { disposed = true; request?.abort(); clearTimeout(timer); clearInterval(tick); };
  }, [contractId, enabled, visible]);

  const observation = sample?.value.contract_id === contractId ? sample.value : null;
  const depth = observation?.depth;
  const serverAge = observation && depth?.observed_at ? Date.parse(observation.as_of) - Date.parse(depth.observed_at) : NaN;
  const age = sample ? serverAge + sample.requestMs + Math.max(0, Math.max(clock, performance.now()) - sample.receivedAt) : NaN;
  const fresh = enabled && visible && !error && Number.isFinite(age) && serverAge >= 0 && age <= 2000;
  const value = depth?.imbalance;
  const hasValue = finite(value) && Math.abs(value) <= 1;
  const limit = finite(observation?.threshold) && observation.threshold > 0 ? observation.threshold : threshold;
  const qualified = fresh && depth?.ready === true && hasValue && Math.abs(value) >= limit;
  const sustained = qualified && finite(depth?.persistence_seconds) ? Math.max(0, depth.persistence_seconds) : 0;
  const side = !hasValue || value === 0 ? "Balanced" : value > 0 ? "Bid-heavy → short setup" : "Ask-heavy → long setup";
  const state = !enabled ? "Unavailable" : !visible ? "Paused while tab is hidden" : error ? "Connection interrupted"
    : !sample ? "Connecting to worker…" : !fresh ? "Stale / unavailable" : !depth?.ready ? "Partial book" : "Live";
  return <div className="topbot-live-imbalance" aria-label="Live DOM imbalance">
    <div className="topbot-section-heading"><h3>Live DOM imbalance</h3><span>{state}</span></div>
    <div className="topbot-imbalance-reading">
      <strong>{hasValue ? `${value > 0 ? "+" : ""}${(value * 100).toFixed(1)}%` : "—"}</strong>
      <div><span>{hasValue ? side : "Waiting for worker depth"}</span><small>{!fresh && hasValue ? "Last received · " : ""}Entry threshold ±{(limit * 100).toFixed(0)}%</small></div>
    </div>
    <div className="topbot-imbalance-meter" role="meter" aria-label="Signed bid/ask imbalance" aria-valuemin={-100} aria-valuemax={100} aria-valuenow={fresh && hasValue ? value * 100 : undefined}
      aria-valuetext={fresh && hasValue ? `${(value * 100).toFixed(1)} percent; ${side}` : "No fresh imbalance reading"}>
      <span className="topbot-imbalance-threshold" style={{ left: `${50 - limit * 50}%` }} />
      <span className="topbot-imbalance-threshold" style={{ left: `${50 + limit * 50}%` }} />
      {fresh && hasValue && <span className="topbot-imbalance-marker" style={{ left: `${50 + value * 50}%` }} />}
    </div>
    <div className="topbot-imbalance-labels"><span>Ask-heavy / long</span><span>0%</span><span>Bid-heavy / short</span></div>
    <dl className="topbot-imbalance-totals">
      <div><dt>Bid contracts</dt><dd>{contracts(depth?.bid_total)}</dd><small>{depth?.bid_levels ?? 0}/100 levels</small></div>
      <div><dt>Ask contracts</dt><dd>{contracts(depth?.ask_total)}</dd><small>{depth?.ask_levels ?? 0}/100 levels</small></div>
      <div><dt>Continuous entry qualification</dt><dd>{fresh && depth?.ready ? `${sustained.toFixed(1)} / ${requiredSeconds}s` : "—"}</dd><small>{qualified ? sustained >= requiredSeconds ? "Duration met · other entry checks still apply" : "Building persistence" : "Not qualifying"}</small></div>
    </dl>
    <p className="topbot-muted">{!fresh ? "Waiting for fresh worker depth. Entry qualification is not confirmed."
      : !depth?.ready ? "Partial totals only. Entries need 100 observed levels on each side."
        : qualified ? "Requires the same imbalance direction above threshold and 100 observed levels per side continuously. Even a brief drop to 99 levels resets this timer."
          : `Below the entry threshold. The ${requiredSeconds}-second timer starts when either side reaches the threshold.`}</p>
    {depth?.qualification_reset && <p className="topbot-muted">Last timer reset: {resetReason(depth.qualification_reset)} · {time(depth.qualification_reset.at)} ET. Brief changes can occur between screen updates.</p>}
    <p className="topbot-footnote">(Bids − asks) ÷ (bids + asks) · Top 100 observed levels per side · Worker updates every second. {depth?.observed_at ? `Depth at ${new Date(depth.observed_at).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour12: false })} ET.` : depth?.reason}</p>
  </div>;
}
