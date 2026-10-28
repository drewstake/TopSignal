export interface DecisionDetails {
  timing: { candle_close: string; recorded_at: string; close_to_record_seconds: number; theoretical_entry_at: string;
    simulated_entry_at: string; exit_deadline: string; remaining_horizon_seconds: number; entry_available: boolean;
    evaluation_started_at?: string; evaluation_completed_at?: string; evaluation_duration_ms?: number; quote_status: string;
    execution_commit_ms?: number;
    stages?: Record<string, { duration_ms: number; self_ms: number; calls: number; failures: number;
      db_queries: number; db_query_ms: number; db_connects: number; db_connect_ms: number }> };
  sides: Record<string, { expected_net_usd: number | null; uncertainty_penalty_usd: number | null;
    lower_utility_usd: number | null; threshold_usd: number; margin_usd: number | null;
    effective_days: number | null; training_paths: number | null; blocks: string[] }>;
  costs: { total_usd: number; fees_usd: number; spread_usd: number; slippage_usd: number; latency_usd: number; rounding_usd: number } | null;
  risk_codes: string[]; stage: string; paper_disposition: string | null;
  theoretical_outcomes: Record<string, { net_usd: number }> | null;
  delayed_outcomes: { status: string; sides: Record<string, { net_usd: number }> } | null;
}
export interface ForwardComparison {
  policy: string; minimum_sessions: number; method?: string;
  cohorts: { id: string; artifact: string; started_at: string; complete_sessions: number;
    accounts: Record<string, { net_usd: number; trades: number; position: null | { side: string; entry?: number };
      blocked_reason: string | null; closed_drawdown_usd: number }>;
    sessions: { day: string; expected: number; recorded: number; missing: number; unfinished: boolean; quality_failures: number; usable: boolean }[];
    comparisons: { rule: string; mean_daily_advantage_usd: number | null; status: string;
      interval: null | { lower_usd: number; upper_usd: number; mean_usd: number; replicates: number } }[];
    dispositions: Record<string, Record<string, number>>;
  }[];
}
const money = (n: number | null | undefined) => n == null ? "Unavailable" : `${n < 0 ? "−" : ""}$${Math.abs(n).toFixed(2)}`;
const clock = (s: string) => new Date(s).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const label = (s: string) => s.replaceAll("_", " ");
const names: Record<string, string> = { topbot: "TopBot", flat: "Stay flat", always_long: "Always buy", always_short: "Always sell", empirical_pool: "Historical pool" };

export interface VolatilityCounterfactuals {
  policy: string; recorded: number; completed: number; pending: number; unknown: number; overdue: number;
  sides?: Record<string, { completed: number; unavailable: number; net_usd: number | null;
    mean_net_usd: number | null; positive: number; negative: number; breakeven: number }>;
  recent: { at: string; status: string; entry_at: string; deadline: string; stop_points: number;
    target_points: number; risk_cap_points: number; quantity: number; costs: { total_usd: number };
    outcomes: null | Record<string, { net_usd: number; entry: number; entry_at: string; exit_at: string; exit_kind: string }> }[];
}

export function VolatilityWhatIf({ data }: { data: VolatilityCounterfactuals }) {
  return <section aria-label="Volatility-blocked hypothetical outcomes" className="rounded border border-app-border p-4 space-y-3">
    <h4 className="font-semibold">Blocked trades · hypothetical BUY and SELL P&L</h4>
    <p className="text-sm text-app-muted">These evaluations were blocked by the 25-point cap in effect when recorded. The separate BUY and SELL scenarios show what would have happened with the wider, rejected stop. They never enter TopBot’s balance or baseline accounts and are not model forecasts.</p>
    <div className="grid gap-3 sm:grid-cols-2">
      {["BUY", "SELL"].map(side => {
        const summary = data.sides?.[side];
        return <section key={side} aria-label={`Hypothetical ${side} P&L`} className="rounded border border-app-border p-4 space-y-1">
          <h5 className="text-sm font-semibold">{side} hypothetical P&L</h5>
          <p className="text-2xl font-semibold">{summary?.net_usd == null ? "Unavailable" : money(summary.net_usd)}</p>
          <p className="text-xs text-app-muted">Selected reporting period · sum after assumed costs</p>
          {summary ? <>
            <p className="text-sm">{summary.completed} completed scenarios · average {money(summary.mean_net_usd)}</p>
            <p className="text-xs text-app-muted">Positive: {summary.positive} · Negative: {summary.negative} · Flat: {summary.breakeven}</p>
            {!summary.completed && <p className="text-sm">Awaiting completed results; pending or unknown outcomes are not zero P&L.</p>}
            {!!summary.unavailable && <p className="text-sm">{summary.unavailable} completed outcomes unavailable; excluded from this total.</p>}
          </> : <p className="text-sm">Summary unavailable; totals are not inferred from the recent rows.</p>}
        </section>;
      })}
    </div>
    <p className="text-xs text-app-muted">BUY and SELL are alternative paths. These sums include overlapping scenarios, so they are not achievable portfolio returns and should not be combined into account P&L. Pending and unknown outcomes are excluded.</p>
    <p className="text-sm">Recorded: {data.recorded} · Completed: {data.completed} · Pending: {data.pending} · Unknown: {data.unknown} · Overdue: {data.overdue}</p>
    {!data.recorded && <p className="text-sm">Waiting for a new volatility-blocked evaluation with valid, fresh candles. Earlier rejected decisions are not backfilled.</p>}
    {!!data.recent.length && <details><summary className="cursor-pointer">Recent blocked scenarios ({data.recent.length} of {data.recorded}) · individual BUY / SELL results</summary><div className="overflow-x-auto"><table className="w-full text-xs text-left">
      <caption className="sr-only">Independent hypothetical outcomes after volatility rejection</caption>
      <thead><tr><th>Decision / status</th><th>Hypothetical bracket</th><th>Entry / deadline</th><th>BUY net</th><th>SELL net</th></tr></thead>
      <tbody>{data.recent.map(row => <tr key={row.at} className="border-t border-app-border">
        <td className="py-3 pr-3">{new Date(row.at).toLocaleString()}<br />HOLD · {label(row.status)}</td>
        <td className="pr-3">Stop {row.stop_points.toFixed(2)} / target {row.target_points.toFixed(2)} points<br />Above {row.risk_cap_points}-point cap</td>
        <td className="pr-3">{clock(row.entry_at)} / {clock(row.deadline)}<br />{row.quantity} MNQ · {money(row.costs.total_usd)} assumed costs</td>
        {["BUY", "SELL"].map(side => <td key={side} className="pr-3">{row.status === "observed" && row.outcomes?.[side] ? <>{money(row.outcomes[side].net_usd)}<br />{label(row.outcomes[side].exit_kind)}</> : row.status === "pending" ? "Pending" : "Unknown"}</td>)}
      </tr>)}</tbody>
    </table></div></details>}
    <p className="text-xs text-app-muted">Entry uses the first five-minute open after recording; the original 15-minute deadline stays fixed. Assumed costs, stop-first bar ordering, and adverse stop gaps apply. Missing prices remain unknown. Overlapping scenarios are not portfolio returns, independent samples, or actual fills. No probability calibration or validation claim is made.</p>
  </section>;
}

export function DecisionExplanation({ details }: { details: DecisionDetails }) {
  const t = details.timing;
  return <div className="space-y-3 text-sm">
    <dl className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <div><dt className="text-app-muted">Candle closed</dt><dd>{clock(t.candle_close)}</dd></div>
      <div><dt className="text-app-muted">Decision recorded</dt><dd>{clock(t.recorded_at)} · {t.close_to_record_seconds.toFixed(1)}s later</dd></div>
      <div><dt className="text-app-muted">Simulated entry opportunity</dt><dd>{t.entry_available ? clock(t.simulated_entry_at) : "Too late for entry"}</dd></div>
      <div><dt className="text-app-muted">Original exit deadline</dt><dd>{clock(t.exit_deadline)} · {(t.remaining_horizon_seconds/60).toFixed(0)}m available</dd></div>
    </dl>
    <p className="text-xs text-app-muted">{t.evaluation_duration_ms == null ? "Evaluation duration was not captured for this record." : `Evaluation took ${(t.evaluation_duration_ms/1000).toFixed(2)}s.`} Forecast outcomes assume the {clock(t.theoretical_entry_at)} open; simulations wait for the first five-minute open after recording. Bid/ask quotes and actual fills are not captured.</p>
    {t.stages && <details><summary className="cursor-pointer">Evaluation stage timings</summary>
      <p className="text-xs text-app-muted my-2">Stage totals include nested work. Own time excludes nested stages; database time is part of own time. The final commit and forward-results write happen after evaluation; the write timing is available in server logs.</p>
      <div className="overflow-x-auto"><table className="w-full text-xs text-left">
        <caption className="sr-only">Evaluation performance by stage</caption>
        <thead><tr><th>Stage</th><th>Total</th><th>Own time</th><th>Database queries</th><th>New connections</th></tr></thead>
        <tbody>{Object.entries(t.stages).map(([name,s]) => <tr key={name} className="border-t border-app-border">
          <td className="py-2 pr-3">{label(name)}{s.failures ? " · interrupted" : ""}</td>
          <td>{(s.duration_ms/1000).toFixed(3)}s</td><td>{(s.self_ms/1000).toFixed(3)}s</td>
          <td>{s.db_queries} · {(s.db_query_ms/1000).toFixed(3)}s</td>
          <td>{s.db_connects} · {(s.db_connect_ms/1000).toFixed(3)}s</td>
        </tr>)}</tbody>
      </table></div>
      {t.execution_commit_ms != null && <p className="text-xs mt-2">Final execution commit: {(t.execution_commit_ms/1000).toFixed(3)}s.</p>}
    </details>}
    {Object.keys(details.sides).length ? <div className="overflow-x-auto"><table className="w-full text-left text-xs">
      <caption className="sr-only">Entry threshold explanation</caption>
      <thead><tr><th>Side</th><th>Expected net</th><th>Uncertainty deduction</th><th>Conservative net</th><th>Margin above $1</th><th>Support</th><th>Signal blockers</th></tr></thead>
      <tbody>{Object.entries(details.sides).map(([side,s]) => <tr key={side} className="border-t border-app-border"><td className="py-2 pr-3">{side}</td><td>{money(s.expected_net_usd)}</td><td>{money(s.uncertainty_penalty_usd)}</td><td>{money(s.lower_utility_usd)}</td><td>{money(s.margin_usd)}</td><td>{s.training_paths ?? "?"} paths / {s.effective_days?.toFixed(1) ?? "?"} effective days</td><td>{s.blocks.length ? s.blocks.map(label).join("; ") : "Signal threshold cleared"}</td></tr>)}</tbody>
    </table></div> : <p>No numerical forecast was available for this evaluation.</p>}
    {details.costs && <p className="text-xs text-app-muted">Expected net already deducts {money(details.costs.total_usd)} assumed round-trip costs: fees {money(details.costs.fees_usd)}, spread {money(details.costs.spread_usd)}, slippage {money(details.costs.slippage_usd)}, latency {money(details.costs.latency_usd)}, tick rounding {money(details.costs.rounding_usd)}. The uncertainty deduction includes a separate cost-uncertainty buffer.</p>}
    <p>Evaluation: {label(details.stage)}. Simulation: {label(details.paper_disposition ?? "unavailable")}. {details.risk_codes.length ? `Execution/risk blocks: ${details.risk_codes.map(label).join(", ")}.` : "No separate execution/risk codes recorded."}</p>
    {details.delayed_outcomes && <div><p className="font-medium">Effect of delayed entry · hypothetical net per side</p>
      {details.delayed_outcomes.status === "observed" ? <p>{["BUY","SELL"].map(side => `${side}: candle-open ${money(details.theoretical_outcomes?.[side]?.net_usd)} → post-record open ${money(details.delayed_outcomes?.sides[side]?.net_usd)}`).join(" · ")}</p> : <p>{label(details.delayed_outcomes.status)}; no payoff inferred.</p>}
      <p className="text-xs text-app-muted">Independent hypothetical paths, including HOLDs. These are not account trades or measured fills.</p></div>}
  </div>;
}

export function BaselineComparisons({ comparison }: { comparison: ForwardComparison }) {
  return <section aria-label="Forward baseline comparisons" className="space-y-4">
    <h4 className="font-semibold">Forward baseline comparisons</h4>
    <p className="text-sm text-app-muted">Separate simulated accounts start together for each model/configuration version. All use the same observed forecast opportunities, delayed entry, costs, brackets, position and daily limits. Directional baselines bypass signal-edge thresholds. Historical pool uses the same frozen training paths without conditioning on the current features.</p>
    {!comparison.cohorts.length && <p>Waiting for the first forecast under this comparison policy. Older paper results are preserved; baseline history is not backfilled.</p>}
    {comparison.cohorts.map(cohort => <div className="rounded border border-app-border p-4 space-y-3" key={cohort.id}>
      <p className="text-xs text-app-muted">Comparison started {new Date(cohort.started_at).toLocaleString()} · model {cohort.artifact.slice(0,12)} · {cohort.complete_sessions}/{comparison.minimum_sessions} complete sessions required before uncertainty estimates</p>
      <div className="overflow-x-auto"><table className="w-full text-sm text-left"><caption className="sr-only">Simulated baseline accounts since comparison began</caption><thead><tr><th>Rule</th><th>Net since start</th><th>Closed trades</th><th>Position / status</th></tr></thead><tbody>{Object.entries(cohort.accounts).map(([rule,a]) => <tr className="border-t border-app-border" key={rule}><td className="py-2">{names[rule] ?? label(rule)}</td><td>{money(a.net_usd)}</td><td>{a.trades}</td><td>{a.blocked_reason ? `Unknown exposure: ${label(a.blocked_reason)}` : a.position ? `${a.position.side} · ${a.position.entry == null ? "queued" : "open"}` : "Flat"}</td></tr>)}</tbody></table></div>
      <div className="overflow-x-auto"><table className="w-full text-sm text-left"><caption className="text-left font-medium my-2">TopBot advantage per complete session</caption><thead><tr><th>Compared with</th><th>Mean daily difference</th><th>Adjusted interval</th></tr></thead><tbody>{cohort.comparisons.map(c => <tr className="border-t border-app-border" key={c.rule}><td className="py-2">{names[c.rule] ?? label(c.rule)}</td><td>{money(c.mean_daily_advantage_usd)}</td><td>{c.interval ? `${money(c.interval.lower_usd)} to ${money(c.interval.upper_usd)}` : label(c.status)}</td></tr>)}</tbody></table></div>
      <p className="text-xs text-app-muted">Positive differences favor TopBot. Intervals use paired daily results with five- and ten-session block resampling and a conservative four-comparison adjustment. They are descriptive, not validation or protection against repeated peeking. Current, incomplete and missing-data sessions are excluded; zero-trade sessions remain included. No interval is shown without enough sessions or observed variation.</p>
      <details><summary className="cursor-pointer">Session coverage and comparison decisions</summary>
        {cohort.sessions.map(s => <p key={s.day}>{s.day}: {s.recorded}/{s.expected} expected evaluations · {s.missing} missing · {s.quality_failures} quality failures · {s.unfinished ? "session in progress" : s.usable ? "complete" : "incomplete; excluded"}</p>)}
        {Object.entries(cohort.dispositions).map(([rule,reasons]) => <p key={rule}>{names[rule] ?? label(rule)}: {Object.entries(reasons).map(([why,n]) => `${label(why)} (${n})`).join(", ")}</p>)}
      </details>
    </div>)}
  </section>;
}
