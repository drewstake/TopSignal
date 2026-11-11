import { useEffect, useState } from "react";
import { botsApi } from "../../lib/api";
import type { Readiness } from "./TopBotResearchStatus";
import { ago, span, stateLabel } from "./researchStatusFormat";

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
  research_health?: { state: string; last_cycle_at: string | null; last_error_type: string | null; account_deferred?: number;
    account_overdue?: number; oldest_overdue?: Readiness["oldest_overdue"]; scope?: string };
  readiness?: Readiness; tp_baseline?: TpBaseline; account_id?: number; as_of?: string;
};
type Difference = { entries: number; sessions: number; mean_difference_usd: number | null; total_difference_usd: number;
  mean_difference_r: number | null; entries_better: number; entries_worse: number; entries_same: number;
  interval_95_pointwise_usd: [number, number] | null; interval_adjusted_usd: [number, number] | null;
  baseline_target_became_stop: number; verdict: string };
type Outcomes = { dollars: Metrics; r: Metrics; take_profit_exits: number; stop_exits: number };
export type TpBaseline = {
  question: string; report_version: string; baseline: { label: string };
  cohort: { valid_paired_entries: number; excluded_entries: number; sessions: number };
  series: { key: string; ratio: number; target_points: number; outcomes: Outcomes; versus_baseline?: Difference }[];
  trailing_reference: { label: string; outcomes: Outcomes; versus_baseline: Difference };
  partial_observations: { entry_id: number; session: string; resolved: Record<string, { exit_reason?: string; net_pnl_usd?: number }>; unresolved: Record<string, string> }[];
  baseline_agreement: { compared: number; same_exit_reason: number; disagreements: { entry_id: number; actual: string; simulated: string }[] } | null;
  minimums: { entries: number; sessions: number }; interval_methods: { pointwise: string; adjusted: string }; caveats: string[];
};
const VERDICTS: Record<string, string> = {
  insufficient_data: "Insufficient data", no_clear_difference: "No clear difference",
  higher_in_sample: "Higher in this sample (not proof)", lower_in_sample: "Lower in this sample",
};
const signed = (v: number | null | undefined) => v == null || !Number.isFinite(v) ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}`;
const range = (v: [number, number] | null | undefined) => v ? `${signed(v[0])} to ${signed(v[1])}` : "Unavailable";

function BaselineComparison({ data }: { data: TpBaseline }) {
  const base = data.series.find(s => s.key === "1");
  const larger = data.series.filter(s => s.versus_baseline);
  const t = data.trailing_reference;
  return <div className="topbot-baseline" aria-label="Larger take-profit versus current 25-point take-profit">
    <h4>Larger fixed take-profit vs current 25-point take-profit</h4>
    <p>{data.question}</p>
    <p className="topbot-muted">{data.cohort.valid_paired_entries} valid paired entries across {data.cohort.sessions} sessions (winners and losers) ·
      {" "}{data.cohort.excluded_entries} excluded and not pooled · a directional reading needs at least {data.minimums.entries} entries and {data.minimums.sessions} sessions.</p>
    {base && <p>Baseline · {data.baseline.label}: average {fmt(base.outcomes.dollars.expectancy)} $/entry · {base.outcomes.take_profit_exits} target / {base.outcomes.stop_exits} stop exits.</p>}
    <div className="topbot-stat-table"><table><thead><tr><th>Fixed target (stop 25)</th><th>Entries</th><th>Avg difference vs 25-pt TP ($)</th>
      <th>Better / worse / same</th><th>25-pt winners lost to stop</th><th>95% interval (pointwise)</th><th>Adjusted interval</th><th>Reading</th></tr></thead>
      <tbody>{larger.map(s => { const d = s.versus_baseline!; return <tr key={s.key}>
        <th>{s.target_points} pts ({s.ratio}:1)</th><td>{d.entries} / {d.sessions} sessions</td><td>{signed(d.mean_difference_usd)}</td>
        <td>{d.entries_better} / {d.entries_worse} / {d.entries_same}</td><td>{d.baseline_target_became_stop}</td>
        <td>{range(d.interval_95_pointwise_usd)}</td><td>{range(d.interval_adjusted_usd)}</td><td>{VERDICTS[d.verdict] ?? d.verdict}</td></tr>; })}</tbody></table></div>
    <p className="topbot-footnote">Separate reference, not the requested baseline · {t.label} Average difference vs 25-pt TP {signed(t.versus_baseline.mean_difference_usd)} $/entry ({t.versus_baseline.entries} entries).</p>
    {data.baseline_agreement && data.baseline_agreement.compared > 0 && <p className="topbot-footnote">Model check: on {data.baseline_agreement.compared} actual fixed-bracket trades, the simulated 25-point baseline reached the same exit as the broker {data.baseline_agreement.same_exit_reason} times.</p>}
    <details><summary>Partial per-trade observations ({data.partial_observations.length}) and method</summary>
      <p>Partial observations are excluded entries with some outcomes resolved before a coverage problem. They are listed only, never averaged into the table.</p>
      {data.partial_observations.map(p => <p key={p.entry_id}>Entry {p.entry_id}: resolved {Object.keys(p.resolved).join(", ") || "none"} · unresolved {Object.entries(p.unresolved).map(([k, why]) => `${k} (${why.replaceAll("_", " ")})`).join(", ")}</p>)}
      <p>{data.interval_methods.pointwise}. {data.interval_methods.adjusted}.</p>
      {data.caveats.map(c => <p key={c}>{c}</p>)}
    </details>
  </div>;
}
const fmt = (v: number | null | undefined) => v == null || !Number.isFinite(v) ? "—" : v.toFixed(2);
function Cells({ m }: { m: Metrics }) {
  return <><td>{m.count} / {m.sessions}</td><td>{m.wins} / {m.losses}</td><td>{fmt(m.average_win)} / {fmt(m.average_loss)}</td>
    <td>{fmt(m.expectancy)}</td><td>{fmt(m.profit_factor)}</td><td>{m.interval_95 ? m.interval_95.map(fmt).join(" to ") : "Unavailable (<5 sessions)"}</td></>;
}
function Head() { return <thead><tr><th>Exit</th><th>Entries / sessions</th><th>Wins / losses</th><th>Avg win / loss</th><th>Expectancy</th><th>Profit factor</th><th>95% mean interval</th></tr></thead>; }

function Denominators({ r }: { r: Readiness }) {
  const c = r.counts;
  const pct = (n: number, d: number) => d ? `${(n/d*100).toFixed(0)}%` : "—";
  return <div className="topbot-footnote" aria-label="Study readiness">
    <p>Overall usable: {c.completed_valid} of {r.registered_filled_entries} registered filled entries ({pct(c.completed_valid, r.registered_filled_entries)}), pending included ·
      {" "}Processed coverage: {c.completed_valid} valid of {r.processed} processed ({pct(c.completed_valid, r.processed)}).</p>
    <p>Pending {r.pending}: {c.observing} observation window open · {c.queued} queued · {c.processing} replaying · {c.retrying} retrying · {c.failed} failed
      {r.oldest_overdue ? ` · oldest overdue entry ${r.oldest_overdue.entry_id} (${stateLabel(r.oldest_overdue.state).toLowerCase()}, ${ago(r.oldest_overdue.overdue_seconds).replace(" ago", "")} past its window)` : ""}.</p>
  </div>;
}

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
        if (result.account_id != null && result.account_id !== accountId) throw new Error("Account mismatch");
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
      {data.readiness && <Denominators r={data.readiness} />}
      <p className="topbot-footnote">Research reader: {data.research_health?.state?.replaceAll("_", " ") ?? "unknown"} · last scan {data.research_health?.last_cycle_at ? new Date(data.research_health.last_cycle_at).toLocaleString() : "not recorded"}
        {data.research_health?.account_deferred != null ? ` · this account: ${data.research_health.account_deferred} deferred, ${data.research_health.account_overdue ?? 0} overdue` : ""}
        {data.research_health?.oldest_overdue ? ` · oldest overdue entry ${data.research_health.oldest_overdue.entry_id} (${span(data.research_health.oldest_overdue.overdue_seconds)})` : ""}
        {data.research_health?.last_error_type ? ` · last reader problem: ${data.research_health.last_error_type.replaceAll("_", " ")}` : ""}.</p>
      {data.tp_baseline && phase === "exploratory" && <BaselineComparison data={data.tp_baseline} />}
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
