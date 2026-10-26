import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Button } from "../../components/ui/Button";
import { requestJson } from "../../lib/api";
import type { BotBacktestResult } from "../../lib/types";
import { BacktestTradeAnalysis } from "../bot/BacktestTradeAnalysis";
import { BacktestDiagnostics } from "../bot/BacktestDiagnostics";
import { buildBacktestChartPaths } from "../bot/botBacktest";

export type EntryDiagnostics = {
  observed_decisions: number; in_session: number; valid_features: number; model_evaluations: number;
  supported_forecasts: number; edge_above_threshold: number; entries: number;
  maximum_stop_points: number; minimum_net_edge_usd: number; round_trip_cost_usd: number;
  model_binding: string; runtime_gates: string;
  blocked_stop_range: { min: number | null; max: number | null };
  closest_forecasts: { time: string; side: string; stop_points: number; expected_net_usd: number;
    uncertainty_penalty_usd: number; lower_utility_usd: number; effective_days: number; supported: boolean; action: string }[];
};
export type HistoricalResult = BotBacktestResult & { skipped: Record<string, number>; entry_diagnostics?: EntryDiagnostics };
export type HistoricalJob = { id: string; status: "running" | "complete" | "failed" | "cancelled";
  progress: { phase: string; completed: number; total: number }; cancel_requested: boolean;
  result: HistoricalResult | null; error: string | null };
type SavedModel = { model_sha256: string; trained_through: string; first_full_test_day: string;
  training_paths: number; source_label: string; verified_at: string; artifact_created_at: string };
const endpoint = "/api/market-data/databento/backtests";
const storageKey = "topsignal-historical-backtest-job"; // ID only; results remain authenticated on the backend.
const date = (stamp: string) => stamp.slice(0, 10);
const money = (value: number) => value.toLocaleString("en-US", { style: "currency", currency: "USD" });
const inputClass = "h-10 w-full rounded-xl border border-app-border bg-app-bg px-3 text-sm text-app-text";
function savedJob() { try { return sessionStorage.getItem(storageKey); } catch { return null; } }
function saveJob(id: string) { try { sessionStorage.setItem(storageKey, id); } catch { /* Storage is optional. */ } }
const message = (error: unknown) => error instanceof Error ? error.message : "Could not reach the backtest server.";
function exportResult(result: HistoricalResult) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url;
  link.download = `topbot-${date(result.range.start)}-${result.input_fingerprint.slice(0, 12)}.json`;
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function TopBotBacktestPanel({ coverageEnd, chartStart, chartEnd }: { coverageEnd: string; chartStart: string; chartEnd: string }) {
  const latestDay = date(new Date(new Date(coverageEnd).getTime()-1).toISOString());
  const [start, setStart] = useState(() => date(new Date(new Date(coverageEnd).getTime()-30*86400000).toISOString()));
  const [end, setEnd] = useState(latestDay);
  const [balance, setBalance] = useState("50000");
  const [fees, setFees] = useState("0.61");
  const [slippage, setSlippage] = useState("1");
  const [jobId, setJobId] = useState<string | null>(savedJob);
  const [job, setJob] = useState<HistoricalJob | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollAttempt, setPollAttempt] = useState(0);
  const [model, setModel] = useState<SavedModel | null>(null);
  const [modelError, setModelError] = useState<string | null>(null);
  const [modelAttempt, setModelAttempt] = useState(0);
  const mounted = useRef(true);
  const request = useRef<AbortController | null>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; request.current?.abort(); }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setModel(null); setModelError(null);
    requestJson<SavedModel>("/api/market-data/databento/backtest-model", { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) setModel(value);
    }).catch(reason => { if (!controller.signal.aborted) setModelError(message(reason)); });
    return () => controller.abort();
  }, [modelAttempt]);

  useEffect(() => {
    if (!jobId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await requestJson<HistoricalJob>(`${endpoint}/${encodeURIComponent(jobId)}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setJob(next); setError(null);
        if (next.status === "running") timer = setTimeout(poll, 1000);
      } catch (reason) {
        if (!controller.signal.aborted) setError(message(reason));
      }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [jobId, pollAttempt]);

  const running = starting || job?.status === "running";
  async function run(event: FormEvent) {
    event.preventDefault();
    if (running || !model) return;
    const from = new Date(`${start}T00:00:00Z`);
    const through = new Date(new Date(`${end}T00:00:00Z`).getTime()+86400000);
    if (!start || !end || !Number.isFinite(+from) || !Number.isFinite(+through) || +through <= +from || +through-+from > 366*86400000) {
      setError("Choose an ordered date range of at most 366 days."); return;
    }
    if (start < model.first_full_test_day) { setError(`This saved model was trained through ${model.trained_through}. Choose ${model.first_full_test_day} or later; no refit will be substituted.`); return; }
    if (!balance.trim() || !fees.trim() || !slippage.trim() || !Number.isFinite(+balance) || +balance <= 0 || +balance > 1e9 || !Number.isFinite(+fees) || +fees < 0 || +fees > 100 || !Number.isInteger(+slippage) || +slippage < 0 || +slippage > 100) {
      setError("Enter a positive starting balance and valid nonnegative costs."); return;
    }
    setStarting(true); setError(null);
    const controller = new AbortController(); request.current = controller;
    try {
      const next = await requestJson<HistoricalJob>(endpoint, { method: "POST", signal: controller.signal,
        body: { start: from.toISOString(), end: through.toISOString(), starting_balance: +balance, commission_per_contract: +fees, slippage_ticks: +slippage,
          model_source: "saved_running_model", model_sha256: model.model_sha256 } });
      if (!mounted.current || controller.signal.aborted) return;
      saveJob(next.id); setJob(next); setJobId(next.id);
    } catch (reason) { if (mounted.current && !controller.signal.aborted) setError(message(reason)); }
    finally { if (mounted.current) setStarting(false); }
  }
  async function cancel() {
    if (!jobId) return;
    try {
      const next = await requestJson<HistoricalJob>(`${endpoint}/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
      if (mounted.current) { setJob(next); setPollAttempt(n => n+1); }
    } catch (reason) { if (mounted.current) setError(message(reason)); }
  }

  const percent = job?.progress.total ? Math.min(100, Math.round(job.progress.completed/job.progress.total*100)) : 0;
  const result = job?.result;
  return <section id="topbot-backtest" aria-labelledby="topbot-backtest-title" className="scroll-mt-28 space-y-5 rounded-2xl border border-app-border bg-app-surface/50 p-5">
    <div><p className="text-xs font-semibold uppercase tracking-widest text-app-accent">Strategy research</p><h2 id="topbot-backtest-title" className="mt-1 text-xl font-semibold text-app-text">Run TopBot backtest</h2>
      <p className="mt-2 max-w-4xl text-sm text-app-muted">TopBot Mathematical · MNQ · fixed 5-minute signals · one contract. Uses the verified saved model from your running TopBot, with its original training paths and weights. No refitting. Chart zoom and timeframe do not change the strategy.</p></div>
    {model ? <div className="rounded-xl border border-app-accent/30 bg-app-accent/5 p-3 text-sm text-app-text"><p className="font-semibold">Saved running model · {model.model_sha256.slice(0, 12)}</p><p className="mt-1 text-xs text-app-muted">{model.training_paths.toLocaleString()} training paths · trained through {model.trained_through.slice(0, 10)} · artifact created {model.artifact_created_at.slice(0, 10)} · verified {model.verified_at.slice(0, 10)}</p><p className="mt-1 text-xs text-app-muted">{model.source_label}. This is a verified snapshot, not an automatic live sync. Earlier dates after training are retrospective tests, not recorded forward trades.</p></div>
      : modelError ? <div role="alert" className="text-sm text-app-negative">{modelError}<Button variant="ghost" size="sm" onClick={() => setModelAttempt(n => n+1)}>Reload model</Button></div> : <p role="status" className="text-sm text-app-muted">Loading saved TopBot model…</p>}
    <form onSubmit={run} className="space-y-4">
      <fieldset disabled={running} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <label className="grid gap-2 text-xs text-app-muted">Test from (UTC)<input aria-label="Backtest from" type="date" required value={start} onChange={e => setStart(e.target.value)} max={latestDay} className={inputClass}/></label>
        <label className="grid gap-2 text-xs text-app-muted">Test through (UTC)<input aria-label="Backtest through" type="date" required value={end} onChange={e => setEnd(e.target.value)} max={latestDay} className={inputClass}/></label>
        <label className="grid gap-2 text-xs text-app-muted">Starting balance ($)<input aria-label="Starting balance" type="number" required min="1" max="1000000000" step="0.01" value={balance} onChange={e => setBalance(e.target.value)} className={inputClass}/></label>
        <label className="grid gap-2 text-xs text-app-muted">Fees / contract / side ($)<input aria-label="Fees per side" type="number" required min="0" max="100" step="0.01" value={fees} onChange={e => setFees(e.target.value)} className={inputClass}/></label>
        <label className="grid gap-2 text-xs text-app-muted">Slippage / side (ticks)<input aria-label="Slippage ticks" type="number" required min="0" max="100" step="1" value={slippage} onChange={e => setSlippage(e.target.value)} className={inputClass}/></label>
      </fieldset>
      <p className="text-xs text-app-muted">Also models 1 tick spread and 0.5 tick latency per side, rounded adversely to whole ticks. Uses $250 daily stop-risk admission and at most 30 daily entries. Test up to 366 days per run.</p>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={running || !model}>{starting ? "Starting…" : "Run TopBot"}</Button>
        <Button variant="secondary" disabled={running || !model} onClick={() => { if (model) { setStart(model.first_full_test_day); setEnd(latestDay); } }}>All history after training</Button>
        <Button variant="secondary" disabled={running} onClick={() => { setStart(date(chartStart)); setEnd(date(new Date(new Date(chartEnd).getTime()-1).toISOString())); }}>Use chart date range</Button>
        {job?.status === "running" && <Button variant="secondary" disabled={job.cancel_requested} onClick={() => void cancel()}>{job.cancel_requested ? "Cancelling…" : "Cancel backtest"}</Button>}
      </div>
    </form>
    {error && <div role="alert" className="rounded-xl border border-app-negative/40 p-3 text-sm text-app-text">{error}{jobId && <Button size="sm" variant="ghost" onClick={() => setPollAttempt(n => n+1)}>Reconnect to run</Button>}</div>}
    {job?.status === "running" && <div role="status" className="space-y-2 text-sm text-app-muted"><p>{job.cancel_requested ? "Stopping replay…" : `${job.progress.phase === "training" ? "Fitting the model on prior sessions" : job.progress.phase === "loading" ? "Loading local Databento candles" : "Replaying TopBot"} · ${percent}%`}</p><progress aria-label="Backtest progress" max="100" value={percent} className="h-2 w-full accent-app-accent"/></div>}
    {job?.status === "failed" && <p role="alert" className="text-sm text-app-negative">{job.error}</p>}
    {job?.status === "cancelled" && <p role="status" className="text-sm text-app-muted">Backtest cancelled. No partial result was recorded.</p>}
    {result && <HistoricalPerformance key={job.id} result={result}/>}
    <p className="text-xs text-app-muted">Runs use only local historical data and never place orders or change the installed model. Results are temporary (up to one hour, lost on backend restart); export them below. A run continues if you leave this tab.</p>
  </section>;
}

function HistoricalPerformance({ result }: { result: HistoricalResult }) {
  const paths = useMemo(() => buildBacktestChartPaths(result.equity_curve, result.drawdown_series), [result]);
  const m = result.metrics, c = result.config_snapshot;
  return <div className="space-y-5 border-t border-app-border pt-5">
    <div><div className="flex flex-wrap items-center justify-between gap-3"><h3 className="text-lg font-semibold text-app-text">Performance results</h3><Button variant="secondary" onClick={() => exportResult(result)}>Export full result JSON</Button></div><p className="mt-1 text-sm text-app-muted">{date(result.range.start)} – {date(new Date(new Date(result.range.end).getTime()-1).toISOString())} UTC · {result.range.bar_count.toLocaleString()} closed 5m observations · starting balance {money(Number(c.starting_balance))}</p>
      <p className="mt-1 text-xs text-app-muted">{c.model_binding === "saved_running_model" ? "Saved running model" : "Historical refit"} · Training: {String(c.training_start)} – {String(c.training_end)} · {String(c.training_paths)} paths · model {String(c.model_sha256).slice(0, 12)}</p></div>
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[
      ["Net P&L", money(m.net_pnl)], ["Win rate", `${m.win_rate.toFixed(1)}%`], ["Closed trades", m.trade_count.toLocaleString()],
      ["Profit factor", m.profit_factor === null ? "— (no losses)" : m.profit_factor.toFixed(2)],
      ["Closed-equity drawdown", `${money(m.max_drawdown_dollars)} (${m.max_drawdown_percent.toFixed(2)}%)`],
      ["Average net / trade", money(m.expectancy)], ["Fees paid", money(m.total_commission)], ["Ending balance", money(Number(c.starting_balance)+m.net_pnl)],
    ].map(([label, value]) => <div key={label} className="rounded-xl border border-app-border bg-app-bg/30 p-3"><p className="text-xs text-app-muted">{label}</p><p className="mt-2 text-base font-semibold tabular-nums text-app-text">{value}</p></div>)}</div>
    {m.trade_count === 0 && <p role="status" className="rounded-xl border border-app-border p-4 text-sm text-app-muted">No qualifying TopBot trades in this period. {result.entry_diagnostics ? `The runner evaluated ${result.entry_diagnostics.model_evaluations.toLocaleString()} forecasts; ${result.entry_diagnostics.edge_above_threshold.toLocaleString()} exceeded the required net edge. See the entry blockers below.` : "Run again to get a detailed entry-gate breakdown."}</p>}
    {result.entry_diagnostics && <EntryBlockers diagnostics={result.entry_diagnostics} skipped={result.skipped}/>}
    <div className="rounded-xl border border-app-border p-4"><h4 className="text-sm font-semibold text-app-text">Closed-trade equity and drawdown</h4>
      {m.trade_count === 0 ? <div className="mt-3 rounded-lg bg-app-bg/30 p-4"><p className="text-sm font-medium text-app-text">No trades to chart</p><p className="mt-1 text-sm text-app-muted">Balance stayed at {money(Number(c.starting_balance))}; closed-trade drawdown was $0.00. An equity curve will appear when a run produces closed trades.</p></div> : <>
        <p className="mt-1 text-xs text-app-muted">{money(paths.equityMin)} – {money(paths.equityMax)} · drawdown 0–{paths.drawdownMax.toFixed(2)}% · horizontal axis follows recorded equity points</p>
        <div className="mt-3 flex gap-4 text-xs"><span className="text-app-accent">Equity (USD)</span><span className="text-app-negative">Drawdown (%)</span></div>
        <svg viewBox="0 0 720 250" role="img" aria-label="Backtest equity and drawdown chart" className="mt-3 max-h-72 w-full"><path d={paths.equityFill} fill="currentColor" className="text-app-accent/10"/><path d={paths.equity} fill="none" stroke="currentColor" strokeWidth="2" className="text-app-accent"/><path d={paths.drawdown} fill="none" stroke="currentColor" strokeWidth="2" className="text-app-negative"/></svg>
      </>}
    </div>
    <BacktestDiagnostics result={result}/>
    <details className="rounded-xl border border-app-border p-4 text-xs text-app-muted"><summary className="cursor-pointer font-semibold text-app-text">Decision exclusions</summary><ul className="mt-3 space-y-1">{Object.entries(result.skipped).map(([reason, count]) => <li key={reason}>{reason.replaceAll("_", " ")}: {count.toLocaleString()}</li>)}</ul></details>
    {result.monthly_results.length > 0 && <details className="rounded-xl border border-app-border p-4"><summary className="cursor-pointer text-sm font-semibold text-app-text">Monthly performance</summary><div className="mt-3 overflow-x-auto"><table aria-label="Monthly backtest results" className="w-full text-left text-sm text-app-muted"><thead><tr>{["Month (ET)", "Trades", "Wins / losses", "Fees", "Net P&L"].map(label => <th key={label} className="p-2">{label}</th>)}</tr></thead><tbody>{result.monthly_results.map(row => <tr key={row.period} className="border-t border-app-border"><td className="p-2">{row.period}</td><td>{row.trade_count}</td><td>{row.wins} / {row.losses}</td><td>{money(row.commission)}</td><td>{money(row.net_pnl)}</td></tr>)}</tbody></table></div></details>}
    <BacktestTradeAnalysis result={result}/>
  </div>;
}

function EntryBlockers({ diagnostics: d, skipped }: { diagnostics: EntryDiagnostics; skipped: Record<string, number> }) {
  const closest = d.closest_forecasts[0];
  const labels: Record<string, string> = { outside_entry_session: "Outside TopBot entry session", volatility_above_risk_cap: "Required stop exceeds 25 points",
    missing_or_invalid_feature_history: "Missing or invalid feature history", insufficient_training_support: "Insufficient independent training support",
    no_model_edge: "Net utility did not exceed $1", daily_risk_limit: "Daily risk / entry limit", position_open: "Position already open",
    cooldown: "Entry cooldown", horizon_after_test_end: "Not enough time before test end" };
  return <section aria-label="TopBot entry diagnostics" className="space-y-4 rounded-xl border border-app-border bg-app-bg/30 p-4">
    <div><h4 className="text-base font-semibold text-app-text">What blocked entries?</h4><p className="mt-1 text-xs text-app-muted">{d.model_binding === "saved_running_model" ? "Verified saved TopBot model, with no refitting. Historical fills use candle-close decisions; live receipt timing and broker/account gates are not replayed." : "Historical refit of TopBot Mathematical. The installed forward-testing artifact and broker/account gates are not part of this run."}</p></div>
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[["In-session decisions", d.in_session], ["Forecasts evaluated", d.model_evaluations], ["Enough training support", d.supported_forecasts], ["Passed net-edge threshold", d.edge_above_threshold]].map(([label, count]) => <div key={label} className="text-xs text-app-muted">{label}<p className="mt-1 text-lg font-semibold tabular-nums text-app-text">{Number(count).toLocaleString()}</p></div>)}</div>
    <div className="overflow-x-auto"><table aria-label="Entry blocker counts" className="w-full text-left text-sm text-app-muted"><thead><tr><th className="py-2">First blocking gate</th><th className="text-right">Decisions</th></tr></thead><tbody>{Object.entries(skipped).map(([reason, count]) => <tr key={reason} className="border-t border-app-border"><td className="py-2">{labels[reason] ?? reason.replaceAll("_", " ")}</td><td className="text-right tabular-nums">{count.toLocaleString()}</td></tr>)}</tbody></table></div>
    <p className="text-xs text-app-muted">Each of {d.observed_decisions.toLocaleString()} observed decisions is counted once as an entry or at its first blocking gate. The model requires at least 300 training paths, 20 effective days, and expected net minus uncertainty strictly above {money(d.minimum_net_edge_usd)}. Assumed round-trip costs: {money(d.round_trip_cost_usd)} per contract.</p>
    {closest ? <div className="rounded-lg border border-app-border p-3 text-sm text-app-text"><p className="font-semibold">Closest forecast to an entry</p><p className="mt-2">{new Date(closest.time).toLocaleString("en-US", {timeZone: "UTC"})} UTC · {closest.side}</p><p className="mt-1">Expected net {money(closest.expected_net_usd)} − uncertainty {money(closest.uncertainty_penalty_usd)} = <strong>{money(closest.lower_utility_usd)}</strong>. Required: &gt; {money(d.minimum_net_edge_usd)}.</p><p className="mt-1 text-xs text-app-muted">Effective training days: {closest.effective_days.toFixed(1)} · stop: {closest.stop_points} points · support: {closest.supported ? "passed" : "blocked"}</p></div> : <p className="text-sm text-app-muted">No forecast reached the model. Check the session, data and volatility gates above.</p>}
  </section>;
}
