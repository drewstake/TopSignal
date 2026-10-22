import { useEffect, useState } from "react";
import { botsApi } from "../../lib/api";
import { BaselineComparisons, DecisionExplanation, VolatilityWhatIf, type DecisionDetails, type ForwardComparison, type VolatilityCounterfactuals } from "./BotEvaluationDetails";

export interface ForwardResults {
  status: string; generated_at: string; latest_at: string | null; scan_limited: boolean;
  evaluations: number; forecasts: number; completed: number; pending: number; unknown: number; overdue: number;
  rejection_counts: Record<string, number>; paper_blocks: Record<string, number>; artifacts: string[];
  calibration: Record<string, { count: number; brier: number | null; bins: { lower: number; upper: number; count: number; predicted: number | null; observed: number | null }[] }>;
  account: null | { started_at: string; updated_at: string; net_usd: number; closed_drawdown_usd: number; trades: number; wins: number;
    blocked_reason: string | null; position: null | { side: string; entry?: number; marked_net_usd?: number; next_bar: string };
    daily: Record<string, { entries: number; net_usd: number }>;
    recent_trades: { id: string; side: string; net_usd: number; exit_kind: string; exit_at: string }[] };
  timing_summary?: null | { count: number; median_seconds: number | null; p95_seconds: number | null; max_seconds: number | null; measured_duration_count: number; mean_evaluation_ms: number | null };
  comparison?: ForwardComparison | null;
  volatility_counterfactuals?: VolatilityCounterfactuals | null;
  recent: { at: string; action: string; stage: string; reason: string; risk_codes: string[]; paper_disposition: string; outcome_status: string; details?: DecisionDetails }[];
}
const label = (value: string) => value.replaceAll("_", " ");
const money = (value: number) => `$${value.toFixed(2)}`;
const percent = (value: number | null) => value === null ? "—" : `${(value * 100).toFixed(1)}%`;

export function BotForwardResultsPanel({ botId, demoMode = false }: { botId: number | null; demoMode?: boolean }) {
  const [data, setData] = useState<ForwardResults | null>(null);
  const [error, setError] = useState(false);
  const [days, setDays] = useState(30);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    setData(null); setError(false);
    if (!botId || demoMode) return;
    let cancelled = false;
    let controller: AbortController | null = null;
    const load = async () => {
      controller?.abort(); controller = new AbortController();
      const current = controller;
      try {
        const result = await botsApi.getForwardResults(botId, days, { signal: current.signal });
        if (!cancelled && !current.signal.aborted) { setData(result); setError(false); }
      } catch {
        if (!cancelled && !current.signal.aborted) setError(true);
      }
    };
    void load(); const timer = window.setInterval(() => void load(), 30_000);
    return () => { cancelled = true; controller?.abort(); window.clearInterval(timer); };
  }, [botId, demoMode, days, refresh]);
  const account = data?.account;
  return <section className="rounded-xl border border-app-border bg-app-panel p-5 space-y-4" aria-label="Forward results">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h3 className="text-lg font-semibold">Forward results · simulated account</h3>
      <div className="flex gap-3 items-center">
        <select aria-label="Forward results period" className="rounded border border-app-border bg-app-panel p-2" value={days} onChange={e => setDays(Number(e.target.value))}>
          <option value={1}>Last 24 hours</option><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option>
        </select>
        <button type="button" className="underline" onClick={() => setRefresh(r => r + 1)}>Refresh results</button>
      </div>
    </div>
    <p className="text-sm text-app-muted">Unvalidated dry-run research. No real orders. Forecast outcomes are separate from the simulated account’s trades.</p>
    {demoMode ? <p>Forward results require an actual dry-run session; no demo evidence is shown.</p> : !botId ? <p>Select a bot to view its results.</p> : error ? <p role="alert">Unable to refresh forward results. Any displayed results may be stale.</p> : !data ? <p>Loading forward results…</p> : null}
    {data && <>
      <p className="text-xs text-app-muted">Last recorded evaluation: {data.latest_at ? new Date(data.latest_at).toLocaleString() : "None yet"}. Refreshes every 30 seconds. {data.scan_limited && "Report is truncated to 9,000 evaluations."}</p>
      {data.status === "awaiting_observations" && <p>Waiting for the worker’s first recorded evaluation. No historical results have been fabricated or backfilled.</p>}
      <dl className="grid grid-cols-2 md:grid-cols-6 gap-3">
        {Object.entries({ Evaluations: data.evaluations, Forecasts: data.forecasts, Completed: data.completed, Pending: data.pending, Overdue: data.overdue, Unknown: data.unknown }).map(([name, value]) => <div key={name} className="rounded border border-app-border p-3"><dt className="text-xs text-app-muted">{name}</dt><dd className="text-xl font-semibold">{value}</dd></div>)}
      </dl>
      {data.timing_summary && <p className="text-sm">Forecast recording delay after candle close: median {data.timing_summary.median_seconds?.toFixed(1) ?? "—"}s · 95th percentile {data.timing_summary.p95_seconds?.toFixed(1) ?? "—"}s · maximum {data.timing_summary.max_seconds?.toFixed(1) ?? "—"}s ({data.timing_summary.count} forecasts).</p>}
      {data.recent[0]?.details && <section aria-label="Latest evaluation explanation" className="rounded border border-app-border p-4 space-y-3"><h4 className="font-semibold">Latest decision · {data.recent[0].action}</h4><p className="text-sm">{data.recent[0].reason}</p><DecisionExplanation details={data.recent[0].details} /></section>}
      <div className="grid md:grid-cols-2 gap-5">
        <div><h4 className="font-semibold">Evaluation breakdown</h4><p className="text-xs text-app-muted">One recorded disposition per candle. Provider failures before evaluation remain in Diagnostics.</p>
          {Object.entries(data.rejection_counts).map(([reason, count]) => <div className="flex justify-between border-b border-app-border py-2 text-sm" key={reason}><span>{label(reason)}</span><strong>{count}</strong></div>)}
          {Object.entries(data.paper_blocks).map(([reason, count]) => <div className="flex justify-between py-2 text-sm" key={reason}><span>{label(reason)}</span><strong>{count}</strong></div>)}
        </div>
        <div><h4 className="font-semibold">Simulated account · since collection began</h4>
          {account ? <>
            <p>Net change: <strong>{money(account.net_usd)}</strong> · Closed trades: {account.trades} · Win rate: {account.trades ? percent(account.wins / account.trades) : "—"}</p>
            <p>Closed-equity maximum drawdown: {money(account.closed_drawdown_usd)}</p>
            <p>Position: {account.position ? `${account.position.side} · ${account.position.entry === undefined ? "awaiting future bar open" : "open"}` : "Flat"}</p>
            {account.position?.marked_net_usd !== undefined && <p>Last closed-bar unrealized estimate: {money(account.position.marked_net_usd)}</p>}
            {account.blocked_reason && <p role="alert">Simulation blocked: {label(account.blocked_reason)}. Unknown exposure requires review.</p>}
            <p className="text-xs text-app-muted mt-2">One MNQ, 5-minute cooldown, 30 entries/day, $250 daily loss admission limit. Future-bar entry after recording; exits at stop, target or original 15-minute deadline. Assumed fees and slippage; stop first when bar ordering is unknown. Closed-equity drawdown excludes intrabar excursions.</p>
          </> : <p>No simulated account records yet.</p>}
        </div>
      </div>
      {data.volatility_counterfactuals && <VolatilityWhatIf data={data.volatility_counterfactuals} />}
      {data.comparison && <BaselineComparisons comparison={data.comparison} />}
      <div><h4 className="font-semibold">Forecast reliability</h4><p className="text-xs text-app-muted">Nonoverlapping 15-minute forecast sample. BUY and SELL are scored separately, not independent evidence. Small bins are descriptive only; no validation claim. Model versions in this period: {data.artifacts.length}.</p>
        <div className="grid md:grid-cols-2 gap-4 mt-2">{Object.entries(data.calibration).map(([side, score]) => <div key={side}>
          <p>{side}: {score.count} scored · Brier score {score.brier === null ? "—" : score.brier.toFixed(4)}</p>
          <table className="w-full text-xs text-left"><caption className="sr-only">{side} forecast calibration</caption><thead><tr><th>Probability band</th><th>Count</th><th>Predicted</th><th>Observed positive</th></tr></thead>
            <tbody>{score.bins.map(bin => <tr key={bin.lower}><td>{percent(bin.lower)}–{percent(bin.upper)}</td><td>{bin.count}{bin.count < 30 ? " · small sample" : ""}</td><td>{percent(bin.predicted)}</td><td>{percent(bin.observed)}</td></tr>)}</tbody></table>
        </div>)}</div>
      </div>
      <details><summary className="cursor-pointer">Recent decisions and rejection reasons</summary><div className="overflow-x-auto"><table className="w-full text-xs text-left"><thead><tr><th>Time</th><th>Decision</th><th>Reason</th><th>Simulation</th><th>Outcome</th></tr></thead><tbody>{data.recent.map(row => <tr className="border-b border-app-border" key={row.at}><td className="p-2">{new Date(row.at).toLocaleString()}</td><td>{row.action}</td><td>{row.reason}{row.risk_codes.length ? ` (${row.risk_codes.join(", ")})` : ""}</td><td>{label(row.paper_disposition)}</td><td>{label(row.outcome_status)}</td></tr>)}</tbody></table></div></details>
      {data.recent.some(row => row.details) && <details><summary className="cursor-pointer">Recorded timing and HOLD details</summary><div className="space-y-4 mt-3">{data.recent.filter(row => row.details).map(row => <details className="rounded border border-app-border p-3" key={row.at}><summary className="cursor-pointer">{new Date(row.at).toLocaleString()} · {row.action} · {label(row.stage)}</summary><DecisionExplanation details={row.details!} /></details>)}</div></details>}
      {account && <details><summary className="cursor-pointer">Simulated trade history and daily results</summary>
        {Object.entries(account.daily).sort().reverse().slice(0, 30).map(([day, value]) => <p key={day}>{day}: {value.entries} reserved entries · realized {money(value.net_usd)}</p>)}
        {account.recent_trades.slice().reverse().map(trade => <p key={trade.id}>{new Date(trade.exit_at).toLocaleString()} · {trade.side} · {label(trade.exit_kind)} · {money(trade.net_usd)}</p>)}
      </details>}
    </>}
  </section>;
}
