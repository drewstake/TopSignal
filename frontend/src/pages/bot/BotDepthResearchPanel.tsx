import type { BotDepthResearch } from "../../lib/types";

const money = (n: number | null) => n != null && Number.isFinite(n) ? `$${n.toFixed(2)}` : "Unavailable";
const percent = (n: number) => Number.isFinite(n) ? `${(100 * n).toFixed(1)}%` : "Unavailable";

export function BotDepthResearchPanel({ depth }: { depth: BotDepthResearch }) {
  const capability = { unverified: "Unverified", level1: "Level 1 observed", verified_level2: "Level 2 verified" }[depth.feed_capability];
  const forecasts = depth.synchronized && depth.probability_basis === "uncalibrated_model_estimate" ? depth.forecasts : null;
  return <section className="mt-4 border-t border-app-border pt-3" aria-label="Level 2 research">
    <h5 className="text-sm font-semibold">Level 2 research · NO TRADE</h5>
    <p className="mt-2 text-xs">Feed: {capability}. Book: {depth.synchronized ? "synchronized" : "not synchronized"} · {depth.book_status.replaceAll("_", " ")}.</p>
    <p className="mt-1 text-xs text-app-muted">{depth.bid_levels} bid / {depth.ask_levels} ask levels. Age at evaluation: {depth.age_seconds == null ? "unavailable" : `${depth.age_seconds.toFixed(2)}s`}. {depth.horizon_seconds / 60}-minute horizon · {depth.model_version}.</p>
    <p className="mt-2 text-xs text-amber-200">{forecasts ? "Uncalibrated probability estimates, not heuristic scores or validated success rates. Routing remains disabled." : "Depth probabilities, payoff and contribution are unavailable. Missing or stale depth never becomes a zero-valued signal."}</p>
    {forecasts && <div className="mt-2 overflow-x-auto"><table className="w-full text-left text-xs">
      <caption className="sr-only">Depth payoff estimates and incremental contribution over Level 1</caption>
      <thead><tr>{["Action", "P(net profit)", "Expected net", "Uncertainty penalty", "Lower utility", "Net change vs Level 1"].map(label => <th className="p-2" key={label}>{label}</th>)}</tr></thead>
      <tbody>{(["BUY", "SELL"] as const).map(side => <tr key={side}>
        <th className="p-2">{side}</th><td className="p-2">{percent(forecasts[side].probability_net_positive)}</td>
        <td className="p-2">{money(forecasts[side].expected_net_usd)}</td><td className="p-2">{money(forecasts[side].uncertainty_penalty_usd)}</td>
        <td className="p-2">{money(forecasts[side].lower_utility_usd)}</td><td className="p-2">{money(depth.depth_contribution?.[side].expected_net_difference_usd ?? null)}</td>
      </tr>)}<tr><th className="p-2">NO TRADE</th><td className="p-2">—</td><td className="p-2">$0.00</td><td className="p-2">—</td><td className="p-2">$0.00</td><td className="p-2">—</td></tr></tbody>
    </table><p className="mt-1 text-xs text-app-muted">Contribution compares paired fitted models; it is not a causal effect or proven improvement.</p></div>}
    <p className="mt-2 text-xs">Estimated round-trip cost: {money(depth.estimated_round_trip_cost_usd)}. {depth.cost_basis}</p>
    <ul className="mt-2 space-y-1 text-xs">{depth.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>
    {forecasts && <p className="mt-2 text-xs text-app-muted">Shadow proposal: {depth.research_action.replaceAll("_", " ")}. {depth.uncertainty_method} Trained through {depth.trained_through ?? "unavailable"}.</p>}
  </section>;
}
