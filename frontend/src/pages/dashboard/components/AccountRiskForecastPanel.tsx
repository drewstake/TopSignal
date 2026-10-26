import { useEffect, useRef, useState, type FormEvent } from "react";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { requestJson } from "../../../lib/api";
import { RiskForecastChart } from "./RiskForecastChart";
import { riskMoney } from "./riskForecastFormat";
import type { RiskForecast, RiskProbability, RiskSources } from "./riskForecastTypes";

const inputClass = "h-10 w-full rounded-xl border border-app-border bg-app-bg px-3 text-sm text-app-text";
const labelClass = "grid gap-1.5 text-xs text-app-muted";
const errorMessage = (error: unknown) => error instanceof Error ? error.message : "Could not load the risk forecast.";
const percent = (value: number) => value === 0 ? "0% simulated" : value < .001 ? "<0.1%" : `${(value*100).toFixed(1)}%`;

export function AccountRiskForecastPanel({ accountId, accountName, demoMode = false }: {
  accountId: number; accountName: string; demoMode?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return <Card aria-label="Account Risk Forecast">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><div className="flex flex-wrap items-center gap-2"><h2 className="text-lg font-semibold text-app-text">Account Risk Forecast</h2>
        <span className="rounded-full border border-app-accent/30 px-2 py-0.5 text-[11px] text-app-muted">Experimental</span></div>
        <p className="mt-1 text-sm text-app-muted">Explore loss-limit risk and exposure scenarios for {accountName}.</p></div>
      {!demoMode && <Button variant="secondary" aria-expanded={open} onClick={() => setOpen(value => !value)}>{open ? "Close forecast" : "Configure forecast"}</Button>}
    </div>
    {demoMode ? <p className="mt-3 text-sm text-app-muted">Forecasts are available with your account data outside Demo mode.</p>
      : open && <ForecastForm key={accountId} accountId={accountId}/>}
  </Card>;
}

function ForecastForm({ accountId }: { accountId: number }) {
  const [catalog, setCatalog] = useState<RiskSources | null>(null);
  const [source, setSource] = useState("trades");
  const [lookback, setLookback] = useState("365");
  const [horizon, setHorizon] = useState("20");
  const [balance, setBalance] = useState("");
  const [buffer, setBuffer] = useState("");
  const [daily, setDaily] = useState("");
  const [mode, setMode] = useState("fixed");
  const [distance, setDistance] = useState("");
  const [cap, setCap] = useState("");
  const [cost, setCost] = useState("0");
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RiskForecast | null>(null);
  const [multiplier, setMultiplier] = useState(1);
  const activeRequest = useRef<AbortController | null>(null);
  const endpoint = `/api/accounts/${accountId}/risk-forecast`;

  useEffect(() => {
    const controller = new AbortController();
    requestJson<RiskSources>(`${endpoint}/sources`, { signal: controller.signal }).then(value => {
      if (controller.signal.aborted) return;
      setCatalog(value);
      setBalance(current => current || (value.balance === null ? "" : String(value.balance)));
      setSource(current => value.sources.some(item => item.id === current) ? current : "trades");
    }).catch(reason => { if (!controller.signal.aborted) setError(errorMessage(reason)); });
    return () => controller.abort();
  }, [endpoint, attempt]);
  useEffect(() => () => activeRequest.current?.abort(), []);

  async function run(event: FormEvent) {
    event.preventDefault();
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    setBusy(true); setError(null); setResult(null);
    try {
      const next = await requestJson<RiskForecast>(endpoint, { method: "POST", signal: controller.signal, body: {
        source_id: source, lookback_days: Number(lookback), horizon_days: Number(horizon),
        starting_balance: Number(balance), loss_buffer: Number(buffer), daily_loss_limit: daily === "" ? null : Number(daily),
        trailing_mode: mode, trailing_distance: mode === "fixed" ? null : Number(distance),
        floor_cap: mode === "fixed" || cap === "" ? null : Number(cap), extra_cost_per_trade: Number(cost),
      } });
      if (!controller.signal.aborted) { setResult(next); setMultiplier(1); }
    } catch (reason) { if (!controller.signal.aborted) setError(errorMessage(reason)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }

  const scenario = result?.scenarios.find(item => item.multiplier === multiplier);
  return <div className="mt-5 space-y-5 border-t border-app-border pt-5">
    <p className="text-sm text-app-muted">Uses one source at a time. Recorded trades belong to this account; temporary backtests are strategy scenarios applied to its limits. Copy Trade Mode is not combined here.</p>
    {!catalog && !error && <p role="status" className="text-sm text-app-muted">Loading available trade and backtest sources…</p>}
    <form onSubmit={event => void run(event)} onChange={() => { setResult(null); setError(null); }} className="space-y-4">
      <fieldset disabled={busy || !catalog} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <label className={`${labelClass} sm:col-span-2`}>Data source<select value={source} onChange={event => setSource(event.target.value)} className={inputClass}>
          {catalog?.sources.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select></label>
        <label className={labelClass}>Forecast active trading days<input type="number" min="1" max="60" step="1" required value={horizon} onChange={e => setHorizon(e.target.value)} className={inputClass}/></label>
        {source === "trades" && <label className={labelClass}>History lookback<select value={lookback} onChange={e => setLookback(e.target.value)} className={inputClass}>
          <option value="90">Last 90 calendar days</option><option value="180">Last 180 calendar days</option><option value="365">Last year</option><option value="1095">Last 3 years</option><option value="3650">Last 10 years</option>
        </select></label>}
        <label className={labelClass}>Starting account balance ($)<input type="number" min="-1000000000" max="1000000000" step="0.01" required value={balance} onChange={e => setBalance(e.target.value)} className={inputClass}/></label>
        <label className={labelClass}>Remaining account loss buffer ($)<input type="number" min="0.01" max="1000000000" step="0.01" required placeholder="Balance minus current loss floor" value={buffer} onChange={e => setBuffer(e.target.value)} className={inputClass}/></label>
        <label className={labelClass}>Daily loss limit ($, optional)<input type="number" min="0.01" max="1000000000" step="0.01" placeholder="No daily stop" value={daily} onChange={e => setDaily(e.target.value)} className={inputClass}/></label>
        <label className={labelClass}>Account loss floor<select value={mode} onChange={e => setMode(e.target.value)} className={inputClass}>
          <option value="fixed">Fixed floor</option><option value="end_of_day">Trail at end of trading day</option><option value="trade_close">Trail after each trade closes</option>
        </select></label>
        <label className={labelClass}>Extra cost per closed trade ($)<input type="number" min="0" max="1000" step="0.01" required value={cost} onChange={e => setCost(e.target.value)} className={inputClass}/></label>
        {mode !== "fixed" && <>
          <label className={labelClass}>Trailing distance ($)<input type="number" min="0.01" max="1000000000" step="0.01" required value={distance} onChange={e => setDistance(e.target.value)} className={inputClass}/></label>
          <label className={labelClass}>Loss-floor cap ($ balance, optional)<input type="number" min="-1000000000" max="1000000000" step="0.01" placeholder="Floor keeps trailing" value={cap} onChange={e => setCap(e.target.value)} className={inputClass}/></label>
        </>}
      </fieldset>
      <p className="text-xs text-app-muted">Confirm the balance and remaining buffer against your account. No provider limits are assumed. The forecast starts on the next active day with daily P&amp;L reset; a daily stop skips the rest of that day. A floor cap is the maximum balance level the loss floor can reach.</p>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy || !catalog}>{busy ? "Simulating scenarios…" : "Calculate risk forecast"}</Button>
        <Button variant="ghost" disabled={busy} onClick={() => { setCatalog(null); setError(null); setResult(null); setAttempt(value => value+1); }}>Refresh sources</Button>
      </div>
    </form>
    {error && <p role="alert" className="rounded-xl border border-app-negative/40 p-3 text-sm text-app-negative">{error}</p>}
    {busy && <p role="status" className="text-sm text-app-muted">Resampling consecutive trading-day blocks across 2,000 paths per scenario…</p>}
    {result && <div className="space-y-4">
      <div className="rounded-xl bg-app-accent/5 p-3 text-sm text-app-text"><p className="font-medium">{result.source_label}</p>
        <p className="mt-1 text-xs text-app-muted">{result.sample.trades.toLocaleString()} closed trades · {result.sample.active_days} completed active days{result.sample.first_day && ` · ${result.sample.first_day} – ${result.sample.last_day}`} · source net P&amp;L {riskMoney(result.sample.net_pnl)}</p></div>
      {result.status === "insufficient_data" ? <p role="status" className="text-sm text-app-muted">Not enough history for a forecast. At least 20 completed active trading days and 30 closed trades are required. Choose a longer lookback or another source.</p> : scenario && <>
        <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="font-semibold text-app-text">Next {result.parameters.horizon_days} active trading days</h3>
          <label className="flex items-center gap-2 text-xs text-app-muted">Chart exposure<select value={multiplier} onChange={e => setMultiplier(Number(e.target.value))} className={inputClass}>
            {result.scenarios.map(item => <option key={item.multiplier} value={item.multiplier}>{item.multiplier}× historical exposure</option>)}
          </select></label></div>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[
          ["Account floor hit", percent(scenario.account_breach.probability)],
          ["Daily stop reached", scenario.daily_limit_hit ? percent(scenario.daily_limit_hit.probability) : "Not configured"],
          ["90th percentile drawdown", riskMoney(scenario.max_drawdown.p90)],
          ["Median ending balance", riskMoney(scenario.ending_balance.p50)],
        ].map(([name, value]) => <div key={name} className="rounded-xl border border-app-border p-3"><p className="text-xs text-app-muted">{name}</p><p className="mt-2 text-lg font-semibold text-app-text">{value}</p></div>)}</div>
        <RiskForecastChart points={scenario.fan}/>
        <div className="overflow-x-auto"><table className="w-full text-left text-sm">
          <caption className="mb-2 text-left text-xs text-app-muted">Exposure comparison · scales historical sizes and costs; fractional multipliers are analytical scenarios, not order sizes.</caption>
          <thead className="text-xs text-app-muted"><tr><th className="p-2">Exposure</th><th className="p-2">Account floor hit</th><th className="p-2">Daily stop hit</th><th className="p-2">Drawdown (90th percentile)</th><th className="p-2">Ending balance, 10–90%</th></tr></thead>
          <tbody>{result.scenarios.map(item => <tr key={item.multiplier} className="border-t border-app-border text-app-text">
            <th className="p-2 font-medium">{item.multiplier}×{item.multiplier === 1 ? " baseline" : ""}</th>
            <td className="p-2">{percent(item.account_breach.probability)}</td><td className="p-2">{item.daily_limit_hit ? percent(item.daily_limit_hit.probability) : "—"}</td>
            <td className="p-2">{riskMoney(item.max_drawdown.p90)}</td><td className="whitespace-nowrap p-2">{riskMoney(item.ending_balance.p10)} – {riskMoney(item.ending_balance.p90)}</td>
          </tr>)}</tbody>
        </table></div>
        <p className="text-xs text-app-muted">Account-floor hit means at least one breach within the horizon. Daily-stop hit means at least one daily stop before account failure. Drawdown is measured from peaks reached since the forecast starts.</p>
      </>}
      <div className="rounded-xl border border-app-warning/30 bg-app-warning/5 p-3">
        <p className="text-sm font-medium text-app-text">Estimate limits</p>
        <p className="mt-1 text-xs text-app-muted">Closed-trade simulations can miss intratrade breaches. A zero simulated rate does not mean zero risk. Existing trading controls remain authoritative.</p>
        <details className="mt-2 text-xs text-app-muted"><summary className="cursor-pointer">Sample warnings and method</summary>
          <ul className="mt-3 list-disc space-y-2 pl-4">{result.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul>
          {scenario && <p className="mt-3">Account-breach Monte Carlo interval (95%): <SimulationInterval value={scenario.account_breach}/>. This measures simulation sampling noise only, not confidence in future results.</p>}
          <p className="mt-3">{result.paths.toLocaleString()} paths · {result.block_days}-day blocks · reproducible sample {result.input_fingerprint.slice(0, 12)} · {result.version}</p>
        </details>
      </div>
    </div>}
  </div>;
}

function SimulationInterval({ value }: { value: RiskProbability }) {
  return <>{value.simulation_interval.map(number => `${(number*100).toFixed(2)}%`).join(" – ")}</>;
}
