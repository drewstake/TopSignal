import { useEffect, useState } from "react";
import { botsApi } from "../../lib/api";

type Metrics = { count: number; sessions: number; wins: number; losses: number; average_win: number | null;
  average_loss: number | null; expectancy: number | null; profit_factor: number | null; interval_95: [number, number] | null };
type Series = { key: string; ratio: number | null; dollars: Metrics; r: Metrics };
export type Comparison = {
  experiment_id: string; series: Series[]; actual: { strategy_class: string; dollars: Metrics; r: Metrics; incomplete: number }[];
  versions: string[]; valid_entries: number; total_entries: number; excluded_entries: number; pending_entries: number;
  sessions: number; progress_to_100: number; coverage_rate: number | null; historical_unregistered: number; nonfilled_attempts?: number;
  excluded: { entry_id: number; reasons: string[] }[]; actual_scope: string; actual_truncated: boolean;
  protocol: { limitations: string[]; fill_model: string; cost_model: string; clock_assumption: string; cutoff: string };
  selection: { ratio: number; registered_at: string } | null; candidate_minus_trailing: { dollars: Metrics; r: Metrics };
  research_health?: { state: string; last_cycle_at: string | null; last_error_type: string | null };
};
const fmt = (v: number | null | undefined) => v == null || !Number.isFinite(v) ? "—" : v.toFixed(2);
function Cells({ m }: { m: Metrics }) {
  return <><td>{m.count} / {m.sessions}</td><td>{m.wins} / {m.losses}</td><td>{fmt(m.average_win)} / {fmt(m.average_loss)}</td>
    <td>{fmt(m.expectancy)}</td><td>{fmt(m.profit_factor)}</td><td>{m.interval_95 ? m.interval_95.map(fmt).join(" to ") : "Unavailable (<5 sessions)"}</td></>;
}
function Head() { return <thead><tr><th>Exit</th><th>Entries / sessions</th><th>Wins / losses</th><th>Avg win / loss</th><th>Expectancy</th><th>Profit factor</th><th>95% mean interval</th></tr></thead>; }

export function TopBotComparison({ accountId }: { accountId: number }) {
  const [data, setData] = useState<Comparison | null>(null);
  const [error, setError] = useState(false);
  const [version, setVersion] = useState("");
  const [strategy, setStrategy] = useState("");
  const [candidate, setCandidate] = useState("1");
  const [registering, setRegistering] = useState(false);
  const [registrationError, setRegistrationError] = useState<string | null>(null);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [phase, setPhase] = useState("exploratory");
  const [unit, setUnit] = useState<"dollars" | "r">("dollars");
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await botsApi.getTopBotComparison(accountId, { version, strategy, start, end, phase }, { signal: controller.signal });
        if (!controller.signal.aborted) { setData(result); setError(false); }
      } catch { if (!controller.signal.aborted) setError(true); }
      finally { if (!controller.signal.aborted) timer = setTimeout(poll, 30_000); }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [accountId, version, strategy, start, end, phase]);
  async function register() {
    setRegistering(true); setRegistrationError(null);
    try {
      const selection = await botsApi.registerTopBotCandidate(accountId, Number(candidate));
      setData(value => value ? { ...value, selection } : value);
    } catch { setRegistrationError("Candidate registration failed. Refresh to check the saved selection."); }
    finally { setRegistering(false); }
  }
  const points = data?.series.filter(s => s.ratio != null && s[unit].expectancy != null) ?? [];
  const trailing = data?.series.find(s => s.key === "trailing")?.[unit];
  const values = [...points.flatMap(s => [s[unit].expectancy!, ...(s[unit].interval_95 ?? [])]), trailing?.expectancy ?? 0, 0];
  const low = Math.min(...values)-1, high = Math.max(...values)+1;
  const y = (v: number) => 190-(v-low)/(high-low)*150;
  const x = (r: number) => 70+(r-1)*250;
  const change = (setter: (s: string) => void, value: string) => { setData(null); setter(value); };
  return <section className="topbot-comparison" aria-label="Strategy comparison research">
    <h3>Actual results &amp; exit-only research</h3>
    <div className="topbot-comparison-filters">
      <label>Actual entry strategy <select value={strategy} onChange={e => change(setStrategy, e.target.value)}><option value="">All saved strategies</option><option value="trailing">Trailing</option><option value="fixed">Fixed</option><option value="unknown">Unknown</option></select></label>
      <label>Strategy version <select value={version} onChange={e => change(setVersion, e.target.value)}><option value="">All versions</option>
        {data?.versions.map(v => <option key={v} value={v}>{v}</option>)}{version && !data?.versions.includes(version) && <option value={version}>{version}</option>}</select></label>
      <label>From (UTC) <input type="date" value={start} onChange={e => change(setStart, e.target.value)} /></label>
      <label>Through (UTC) <input type="date" value={end} onChange={e => change(setEnd, e.target.value)} /></label>
      <label>Units <select value={unit} onChange={e => setUnit(e.target.value as "dollars" | "r")}><option value="dollars">Dollars</option><option value="r">R</option></select></label>
      <label>Study cohort <select value={phase} onChange={e => change(setPhase, e.target.value)}><option value="exploratory">First 100 registered entries</option><option value="holdout">Prospective candidate validation</option></select></label>
    </div>
    {error && <p role="status">Comparison unavailable. Any displayed results are stale.</p>}
    {!data ? <p>Waiting for comparison records.</p> : <>
      <p>Actual broker executions</p>
      <div className="topbot-stat-table"><table><Head /><tbody>{data.actual.map(s => <tr key={s.strategy_class}><th>{s.strategy_class}</th><Cells m={s[unit]} /></tr>)}</tbody></table></div>
      <p className="topbot-footnote">{data.actual_scope}{data.actual_truncated ? " · Older entries omitted." : ""} Classification uses saved plans, never current settings.</p>
      <p className="topbot-footnote">Incomplete actual records: {data.actual.map(s => `${s.strategy_class} ${s.incomplete}`).join(" · ")}. {data.nonfilled_attempts ?? 0} unfilled or blocked attempts excluded. Undefined profit factor is shown as a dash.</p>
      <h4>Shadow simulations · average net per shared entry ({unit === "r" ? "R" : "$"})</h4>
      <p>{data.valid_entries} valid shared entries across {data.sessions} sessions · {data.progress_to_100}/100 registered exploratory observations (all filters) · {data.excluded_entries} excluded · {data.pending_entries} pending · coverage {data.coverage_rate == null ? "—" : `${(data.coverage_rate*100).toFixed(1)}%`}.</p>
      <p className="topbot-footnote">Research reader: {data.research_health?.state ?? "unknown"} · last successful scan {data.research_health?.last_cycle_at ? new Date(data.research_health.last_cycle_at).toLocaleString() : "not recorded"}{data.research_health?.last_error_type ? ` · ${data.research_health.last_error_type}` : ""}.</p>
      <p className="topbot-footnote">Nine variants on 100 entries are 100 market observations. One hundred is exploratory; validate a prespecified candidate on later unseen data. No automatic promotion to live trading.</p>
      {data.selection ? <p>Prespecified candidate {data.selection.ratio}:1 · registered {new Date(data.selection.registered_at).toLocaleString()}. Validation uses later entries after registration and the first 100 observations.</p>
        : <div><p>Freeze one research candidate before inspecting its future validation cohort. This selection is immutable for this study and does not change live trading.</p>
          <label>Research candidate <select value={candidate} onChange={e => setCandidate(e.target.value)}>{[1, 1.25, 1.5, 1.75, 2, 2.25, 2.5, 2.75, 3].map(r => <option key={r} value={r}>{r}:1</option>)}</select></label>{" "}
          <button type="button" disabled={registering} onClick={() => void register()}>Register candidate for future validation</button></div>}
      {registrationError && <p role="alert">{registrationError}</p>}
      {phase === "holdout" && data.selection && <p>Candidate minus trailing: {fmt(data.candidate_minus_trailing[unit].expectancy)} {unit === "r" ? "R" : "$"} per shared entry · 95% interval {data.candidate_minus_trailing[unit].interval_95?.map(fmt).join(" to ") ?? "unavailable"}.</p>}
      <svg viewBox="0 0 640 245" role="img" aria-label="Reward to risk versus mean net profit per trade" style={{ width: "100%", maxHeight: 320 }}>
        <line x1="70" x2="570" y1={y(0)} y2={y(0)} stroke="currentColor" opacity=".3" />
        <text x="5" y="25" fill="currentColor">Net {unit === "r" ? "R" : "$"}</text>
        {[1, 1.25, 1.5, 1.75, 2, 2.25, 2.5, 2.75, 3].map(r => <text key={r} x={x(r)} y="215" textAnchor="middle" fill="currentColor" fontSize="12">{r}:1</text>)}
        <text x="320" y="238" textAnchor="middle" fill="currentColor">Reward:risk</text>
        {[low, (low+high)/2, high].map(v => <text key={v} x="60" y={y(v)+4} textAnchor="end" fill="currentColor" fontSize="11">{fmt(v)}</text>)}
        {trailing?.expectancy != null && <><line x1="70" x2="570" y1={y(trailing.expectancy)} y2={y(trailing.expectancy)} stroke="#f0b858" strokeDasharray="5 5" /><text x="570" y={y(trailing.expectancy)-5} textAnchor="end" fill="#f0b858" fontSize="12">Trailing shadow reference</text></>}
        {points.map(s => <g key={s.key}><title>{s.ratio}:1 · {fmt(s[unit].expectancy)} · n={s[unit].count}</title>
          {s[unit].interval_95 && <line x1={x(s.ratio!)} x2={x(s.ratio!)} y1={y(s[unit].interval_95![0])} y2={y(s[unit].interval_95![1])} stroke="#53c9b1" />}
          <circle cx={x(s.ratio!)} cy={y(s[unit].expectancy!)} r="4" fill="#53c9b1" /></g>)}
        {!points.length && <text x="320" y="110" textAnchor="middle" fill="currentColor">No valid paired outcomes yet</text>}
      </svg>
      <div className="topbot-stat-table"><table><Head /><tbody>{data.series.map(s => <tr key={s.key}><th>{s.ratio == null ? "Trailing reference (25/25/25)" : `${s.ratio}:1 · ${s.ratio*25} pts`}</th><Cells m={s[unit]} /></tr>)}</tbody></table></div>
      <details><summary>Study assumptions, coverage and exclusions</summary><p>Experiment {data.experiment_id}</p>
        <p>{data.protocol.fill_model}. {data.protocol.cost_model}. {data.protocol.clock_assumption}. {data.protocol.cutoff}.</p>
        {data.protocol.limitations.map(s => <p key={s}>{s}</p>)}<p>{data.historical_unregistered} historical entries lack prospective study registration.</p>
        {data.excluded.map(r => <p key={r.entry_id}>Entry {r.entry_id}: {r.reasons.join(", ")}</p>)}
      </details>
    </>}
  </section>;
}
