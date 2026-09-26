import type { BotProbabilisticResearch } from "../../lib/types";
import { BotDepthResearchPanel } from "./BotDepthResearchPanel";

const money = (value: number) => Number.isFinite(value) ? `$${value.toFixed(2)}` : "Unavailable";
const probability = (value: number) => Number.isFinite(value) && value >= 0 && value <= 1 ? `${(value * 100).toFixed(1)}%` : "Unavailable";

export function BotProbabilisticPanel({ research, selectedStrategy = false, live = false }: { research: BotProbabilisticResearch; selectedStrategy?: boolean; live?: boolean }) {
  const forecasts = research.probability_basis === "uncalibrated_model_estimate" ? research.forecasts : null;
  const statusLabel = research.validation_status === "passed" ? "Reviewed evidence passed" :
    research.validation_status === "offline_passed" ? "Offline checks passed · forward evidence required" : "Validation incomplete";
  return <section className="mt-4 rounded-lg border border-app-border p-3" aria-label="Probabilistic research">
    <h4 className="text-sm font-semibold">{selectedStrategy ? `Selected mathematical strategy · ${live ? "Experimental Practice" : "Dry Run"}` : "Probabilistic research · Dry Run shadow"}</h4>
    <p className="mt-2 text-sm font-medium">{statusLabel}</p>
    <p className="mt-1 text-xs leading-5 text-app-muted">{research.horizon_minutes}-minute horizon · {research.model_version}. {selectedStrategy ? "This forecast supplies the strategy decision above. Account and risk checks determine the final execution outcome." : "Research estimates do not authorize orders or replace the strategy decision above."}</p>
    <p className="mt-2 text-xs leading-5 text-amber-200">{forecasts ? "Uncalibrated model probabilities. These are experimental estimates, not validated success rates or heuristic indicator scores." : "Outcome probabilities, expected payoff and model uncertainty are unavailable until a suitable model and fresh data are available."}</p>
    {forecasts && <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-xs">
      <caption className="sr-only">Experimental payoff comparison per one MNQ contract</caption>
      <thead><tr>{["Action", "P(net profit)", "Expected net", "Uncertainty penalty", "Lower utility"].map(label => <th key={label} className="px-2 py-2 font-medium">{label}</th>)}</tr></thead>
      <tbody>{(["BUY", "SELL"] as const).map(action => <tr key={action}>
        <th className="px-2 py-2 font-medium">{action}</th>
        <td className="px-2 py-2">{probability(forecasts[action].probability_net_positive)}</td>
        <td className="px-2 py-2">{money(forecasts[action].expected_net_usd)}</td>
        <td className="px-2 py-2">{money(forecasts[action].uncertainty_penalty_usd)}</td>
        <td className="px-2 py-2">{money(forecasts[action].lower_utility_usd)}</td>
      </tr>)}<tr><th className="px-2 py-2 font-medium">NO TRADE</th><td className="px-2 py-2">—</td><td className="px-2 py-2">$0.00</td><td className="px-2 py-2">—</td><td className="px-2 py-2">$0.00</td></tr></tbody>
    </table></div>}
    {forecasts && <div className="mt-2 space-y-1 text-xs text-app-muted">{(["BUY", "SELL"] as const).map(action => <p key={action}>{action}: stop {probability(forecasts[action].probability_stop)}, target {probability(forecasts[action].probability_target)}, time exit {probability(forecasts[action].probability_time_exit)}. Net loss or breakeven {probability(forecasts[action].probability_net_nonpositive)}.</p>)}</div>}
    <p className="mt-3 text-xs leading-5">Assumed round-trip costs: {money(research.costs.total_usd)} per contract. Fees {money(research.costs.fees_usd)}, spread {money(research.costs.spread_usd)}, slippage {money(research.costs.slippage_usd)}, latency {money(research.costs.latency_usd)}, tick rounding {money(research.costs.rounding_usd)}.</p>
    <p className="mt-1 text-xs leading-5 text-app-muted">{research.costs.basis} Net payoff already includes these costs. A positive win probability alone is insufficient; lower utility must exceed {money(research.minimum_net_edge_usd)}.</p>
    <ul className="mt-3 space-y-1 text-xs leading-5">{research.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>
    {forecasts && <p className="mt-2 text-xs text-app-muted">{selectedStrategy ? "Model proposal" : "Shadow proposal"}: {research.research_action.replaceAll("_", " ")}. {research.routing_allowed ? "Evidence gate passed; worker, account and risk gates still apply." : "Live routing remains disabled."} {research.uncertainty_method}</p>}
    {research.stop_points != null && <p className="mt-2 text-xs text-app-muted">Research bracket: {research.stop_points} points stop / {research.target_points} points target, {research.quantity} contract, plus a horizon exit. This does not change any active position.</p>}
    <p className="mt-2 break-words text-xs leading-5 text-app-muted">Data: {research.data_status}; candle closed {research.candle_close_timestamp ?? "unavailable"}; age at evaluation {research.age_seconds == null ? "unavailable" : `${Math.round(research.age_seconds)}s`}. Evaluated {research.evaluated_at}. Model trained through {research.model_trained_through ?? "unavailable"}; {research.training_paths} training paths.</p>
    {research.depth && <BotDepthResearchPanel depth={research.depth} />}
  </section>;
}
