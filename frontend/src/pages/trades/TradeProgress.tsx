import { useEffect, useRef, useState } from "react";
import { requestJson } from "../../lib/api";
import { Button } from "../../components/ui/Button";
import type { AccountTrade } from "../../lib/types";
import { chartSegments, SAMPLE_INTERVAL_SECONDS } from "./tradeProgressModel";

export interface ProgressSample {
  id?: number; ts: number; kind: string; points: number | null; unrealized_dollars: number | null;
  quantity: number; average_entry: number; mark: number | null; mark_timestamp: number | null;
  issue: string | null; gap_start?: number; mark_basis?: string | null; source: string;
  event_timestamp?: number | null; timing?: string;
}
export interface ProgressTimeline {
  source: string; coverage: string; next_cursor: number | null;
  position: { id: string; contract: string; opened: number; first_observed: number; ended: number | null;
    reasons: string[]; best: ProgressSample | null; worst: ProgressSample | null };
  samples: ProgressSample[];
  fills: { trade_id: string; ts: number; realized_dollars: number | null; voided: boolean; revision_conflict?: boolean }[];
  fills_truncated: boolean;
}
interface RecorderStatus {
  enrolled: boolean; healthy: boolean; heartbeat: number | null; error: string | null; free_bytes?: number;
}
interface Matches {
  matches: { id: string; opened: number; ended: number | null; direction: string; reasons: string[] }[];
  status: RecorderStatus;
}
const fmt = (value: number | null, digits = 2) => value === null ? "Unavailable" : value.toLocaleString("en-US", { maximumFractionDigits: digits });
const time = (value: number) => new Date(value * 1000).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour12: false });
const label = (value: string) => value.replaceAll("_", " ");
const base = (account: number) => `/api/accounts/${account}/trade-progress`;

export function ProgressRecorderStatus({ account, disabled = false }: { account: number; disabled?: boolean }) {
  const [status, setStatus] = useState<RecorderStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    if (disabled) return;
    const controller = new AbortController();
    const refresh = () => requestJson<RecorderStatus>(`${base(account)}/status`, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) { setStatus(result); setError(null); } })
      .catch(() => { if (!controller.signal.aborted) setError("Recorder status unavailable"); });
    void refresh();
    const timer = setInterval(() => void refresh(), 15000);
    return () => { mounted.current = false; controller.abort(); clearInterval(timer); };
  }, [account, disabled]);
  async function toggle() {
    if (!status) return;
    setBusy(true);
    try {
      const result = await requestJson<RecorderStatus>(`${base(account)}/enrollment`, { method: "PUT", query: { enabled: !status.enrolled } });
      if (mounted.current) { setStatus(result); setError(null); }
    } catch (err) { if (mounted.current) setError(err instanceof Error ? err.message : "Recording enrollment unavailable"); }
    finally { if (mounted.current) setBusy(false); }
  }
  if (disabled) return null;
  return <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 rounded-2xl border border-slate-800 bg-slate-950/45 px-4 py-2 text-xs">
    <div><span className={status?.healthy ? "text-emerald-300" : "text-amber-200"}>Trade progress · {status ? status.enrolled ? status.healthy ? "Recording" : "Enrolled · coverage unavailable" : "Not enrolled" : "Checking recorder…"}</span>
      <p className="mt-1 text-slate-400">{SAMPLE_INTERVAL_SECONDS}-second observations · includes manual trades · {status?.heartbeat ? `Last recorder check ${time(status.heartbeat)} ET` : "No recorder heartbeat"}</p>
      {(error || status?.error) && <p className="mt-1 text-amber-200" role="status">{error || label(status?.error ?? "")}</p>}
    </div>
    <Button size="sm" variant="ghost" disabled={!status || busy} onClick={() => void toggle()}>{busy ? "Saving…" : status?.enrolled ? "Stop recording" : "Enable recording"}</Button>
  </div>;
}

export function TradeProgressChart({ data }: { data: ProgressTimeline }) {
  const [selected, setSelected] = useState<ProgressSample | null>(null);
  const chartContainer = useRef<HTMLDivElement>(null);
  const [chartWidth, setChartWidth] = useState(900);
  useEffect(() => {
    if (!chartContainer.current || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => setChartWidth(Math.max(320, entry.contentRect.width)));
    observer.observe(chartContainer.current);
    return () => observer.disconnect();
  }, []);
  const right = chartWidth - 36;
  const plotWidth = right - 64;
  const { position, samples } = data;
  const extremes = [position.best, position.worst].filter((s): s is ProgressSample => s !== null);
  const observations = samples.filter(s => s.points !== null && !s.issue);
  const low = Math.min(0, ...observations.map(s => s.points!), ...extremes.map(s => s.points!));
  const high = Math.max(0, ...observations.map(s => s.points!), ...extremes.map(s => s.points!));
  const pad = Math.max(1, (high-low)*.15);
  const start = position.opened;
  const end = Math.max(start+10, samples.at(-1)?.ts ?? start, ...extremes.map(s => s.ts));
  const x = (ts: number) => 64 + (ts-start)/(end-start)*plotWidth;
  const y = (v: number) => 230 - (v-low+pad)/(high-low+2*pad)*190;
  const segments = chartSegments(samples);
  const gaps: { start: number; end: number }[] = [];
  let gapStart: number | null = position.first_observed > start+1 ? start : null;
  samples.forEach((s, i) => {
    if (s.issue || s.kind === "gap") gapStart ??= s.gap_start ?? samples[Math.max(0,i-1)]?.ts ?? s.ts;
    else if (gapStart !== null) { gaps.push({ start: gapStart, end: s.ts }); gapStart = null; }
    const prev = samples[i-1];
    if (prev && s.ts-prev.ts > SAMPLE_INTERVAL_SECONDS * 2) gaps.push({ start: prev.ts, end: s.ts });
  });
  if (gapStart !== null) gaps.push({ start: gapStart, end });
  const detail = selected ?? observations.at(-1) ?? samples.at(-1);
  const events = samples.filter(s => !["periodic", "coverage_resumed", "gap"].includes(s.kind));
  return <div className="space-y-4">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><p className="text-[10px] uppercase tracking-[.2em] text-cyan-300">Position-only path · {data.source === "live_observation" ? "Observed live" : "Reconstructed history"}</p>
        <h3 className="mt-1 text-xl font-semibold text-slate-100">Points over time</h3><p className="mt-1 text-xs text-slate-400">{position.contract} · elapsed from entry to exit · points against the average entry at each observation</p><p className="mt-1 text-xs text-slate-500">Ends when the position closes. It is not the post-exit research window used to compare take-profit targets.</p></div>
      <span className="rounded-full border border-amber-400/30 bg-amber-400/10 px-3 py-1 text-xs text-amber-200">{data.coverage === "partial" ? "Partial coverage" : "Observed interval only"}</span>
    </div>
    <div className="grid grid-cols-2 gap-3 text-sm">
      {[["Observed best", position.best], ["Observed worst", position.worst]].map(([name, value]) => {
        const sample = value as ProgressSample | null;
        return <div key={name as string} className="rounded-xl border border-slate-800 bg-slate-900/50 p-3"><p className="text-xs text-slate-400">{name as string}</p><p className="mt-1 text-lg font-semibold text-slate-100">{fmt(sample?.points ?? null)} {sample ? "pts" : ""}</p><p className="text-xs text-slate-400">{sample ? `${time(sample.ts)} ET · ${fmt(sample.quantity,0)} contracts · basis ${fmt(sample.average_entry)}` : "No valid price observations"}</p></div>;
      })}
    </div>
    <div ref={chartContainer}>{observations.length || extremes.length ? <svg viewBox={`0 0 ${chartWidth} 280`} role="img" aria-label="Signed unrealized points over elapsed minutes. Amber bands show missing coverage." className="w-full rounded-xl border border-slate-800 bg-slate-950">
      {[0,1,2,3,4].map(i => { const v=low-pad+(high-low+2*pad)*i/4; return <g key={i}><line x1="64" x2={right} y1={y(v)} y2={y(v)} stroke="#1e293b"/><text x="54" y={y(v)+4} textAnchor="end" fill="#94a3b8" fontSize="11">{fmt(v,1)}</text></g>; })}
      <line x1="64" x2={right} y1={y(0)} y2={y(0)} stroke="#64748b" strokeDasharray="4 4"/>
      {gaps.map((g,i) => <rect key={i} x={x(g.start)} y="30" width={Math.max(2,x(g.end)-x(g.start))} height="200" fill="#fbbf24" opacity=".12"><title>Missing coverage {time(g.start)}–{time(g.end)} ET</title></rect>)}
      {segments.map((segment,i) => <polyline key={i} fill="none" stroke="#22d3ee" strokeWidth="2.5" points={segment.map(s => `${x(s.ts)},${y(s.points!)}`).join(" ")}/>)}
      {observations.map((s,i) => <circle key={i} cx={x(s.ts)} cy={y(s.points!)} r="3" fill="#22d3ee" opacity=".6" onMouseEnter={() => setSelected(s)}><title>{time(s.ts)} ET · {fmt(s.points)} pts · basis {fmt(s.average_entry)}</title></circle>)}
      {events.map((s,i) => <g key={`event-${i}`} onMouseEnter={() => setSelected(s)}><line x1={x(s.ts)} x2={x(s.ts)} y1="30" y2="230" stroke="#94a3b8" strokeDasharray="3 5"/><circle tabIndex={0} onFocus={() => setSelected(s)} cx={x(s.ts)} cy={s.points === null ? 32 : y(s.points)} r="5" fill="#e2e8f0"><title>{label(s.kind)} · {time(s.ts)} ET · basis {fmt(s.average_entry)}</title></circle></g>)}
      {extremes.map((s,i) => <g key={`extreme-${i}`} onMouseEnter={() => setSelected(s)}><circle tabIndex={0} onFocus={() => setSelected(s)} cx={x(s.ts)} cy={y(s.points!)} r="5" fill={i===0 ? "#34d399" : "#fb7185"}/><text x={Math.max(105,Math.min(right-40,x(s.ts)))} y={y(s.points!)-12} textAnchor="middle" fontSize="11" fill={i===0 ? "#6ee7b7" : "#fda4af"}>{i===0 ? "Best" : "Worst"} {fmt(s.points)} pts</text></g>)}
      {[0,1,2,3,4].map(i => <text key={i} x={64+i*plotWidth/4} y="252" textAnchor="middle" fill="#94a3b8" fontSize="11">{fmt((end-start)/60*i/4,1)}m</text>)}
      <text x="64" y="18" fill="#94a3b8" fontSize="11">Signed points</text><text x={right} y="273" textAnchor="end" fill="#94a3b8" fontSize="11">Elapsed from entry</text>
    </svg> : <div className="rounded-xl border border-dashed border-slate-700 p-8 text-center text-sm text-slate-400">No valid price observations. This does not mean the position was flat.</div>}</div>
    <p className="text-xs text-slate-400">Cyan: periodic observations · white: position changes · green/rose: extremes on incoming prices · amber: missing coverage. Lines break when the entry basis changes.</p>
    {detail && <div className="rounded-xl border border-slate-800 bg-slate-900/50 px-4 py-3 text-xs text-slate-300" aria-live="polite">
      <p>{time(detail.ts)} ET · {label(detail.kind)} · {detail.points === null ? "Points unavailable" : `${fmt(detail.points)} pts`} · {detail.unrealized_dollars === null ? "Unrealized unavailable" : `$${fmt(detail.unrealized_dollars)} unrealized before fees`}</p>
      <p className="mt-1 text-slate-400">Qty {fmt(detail.quantity,0)} · average entry {fmt(detail.average_entry)} · mark {fmt(detail.mark)} {detail.mark_timestamp ? `at ${time(detail.mark_timestamp)} ET` : "(missing)"} · {label(detail.mark_basis ?? "no mark")}{detail.issue ? ` · ${label(detail.issue)}` : ""}</p>
      {detail.timing && <p className="mt-1 text-slate-400">{detail.event_timestamp ? `Provider event ${time(detail.event_timestamp)} ET` : "Exact transition time unavailable"} · {label(detail.timing)} timing</p>}
    </div>}
    <p className="text-xs text-slate-400">This path covers the position, including scaling and partial exits. Unrealized dollars apply only to the quantity remaining at that observation. Markers use observation times. Exit markers retain the last open quantity and entry basis; the mark is not the execution price.</p>
    {position.reasons.length>0 && <details className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-3 text-xs text-amber-200"><summary className="cursor-pointer">Coverage notes ({position.reasons.length})</summary><ul className="mt-2 space-y-1">{position.reasons.map(r => <li key={r}>{label(r)}</li>)}</ul></details>}
    <details className="text-xs text-slate-300"><summary className="cursor-pointer">Position changes &amp; separate realized fills</summary>
      <div className="mt-3 overflow-x-auto"><table className="w-full text-left"><thead className="text-slate-400"><tr><th>Time (ET)</th><th>Event</th><th>Quantity</th><th>Entry basis</th><th>Points</th></tr></thead><tbody>{events.map((s,i) => <tr key={i} className="border-t border-slate-800"><td className="py-2">{time(s.ts)}</td><td>{label(s.kind)}</td><td>{s.quantity}</td><td>{fmt(s.average_entry)}</td><td>{fmt(s.points)}</td></tr>)}</tbody></table></div>
      <p className="mt-3 text-slate-400">Broker-reported realized fills in this position’s time interval; matching can be ambiguous at reversals. These amounts are never added to the unrealized line.</p>
      {data.fills.length ? data.fills.map(f => <p className="mt-1" key={f.trade_id}>{time(f.ts)} ET · fill {f.trade_id} · {f.revision_conflict ? "Conflicting revisions · realized amount unavailable" : f.voided ? "Voided" : f.realized_dollars === null ? "Realized amount unavailable" : `$${fmt(f.realized_dollars)} realized (provider reported)`}</p>) : <p className="mt-2 text-amber-200">No realized fill observations available. Missing fills are not zero P&amp;L.</p>}
      {data.fills_truncated && <p className="text-amber-200">Fill list is limited to the first 1,000 observations.</p>}
    </details>
  </div>;
}

export function TradeProgressReview({ trade, onClose }: { trade: AccountTrade; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [matches, setMatches] = useState<Matches | null>(null);
  const [positionId, setPositionId] = useState<string | null>(null);
  const [data, setData] = useState<ProgressTimeline | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [cursor, setCursor] = useState(0);
  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => {
    const controller = new AbortController();
    requestJson<Matches>(`/api/accounts/${trade.account_id}/trades/${trade.id}/progress`, { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) { setMatches(result); setPositionId(result.matches.length===1 ? result.matches[0].id : null); setLoading(result.matches.length===1); } })
      .catch(() => { if (!controller.signal.aborted) { setError("Recording unavailable. The local backend or recording drive may be offline."); setLoading(false); } });
    return () => controller.abort();
  }, [trade.account_id, trade.id]);
  useEffect(() => {
    if (!positionId) return;
    const controller = new AbortController();
    requestJson<ProgressTimeline>(`${base(trade.account_id)}/positions/${encodeURIComponent(positionId)}`, { query: { after: cursor }, signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) { setData(old => cursor && old ? { ...result, samples: [...old.samples,...result.samples] } : result); setLoading(false); } })
      .catch(() => { if (!controller.signal.aborted) { setError("Timeline unavailable. No missing observations have been filled in."); setLoading(false); } });
    return () => controller.abort();
  }, [trade.account_id, positionId, cursor]);
  return <dialog ref={dialog} onCancel={onClose} className="m-auto max-h-[90vh] w-[min(960px,94vw)] overflow-y-auto rounded-3xl border border-slate-700 bg-slate-950 p-5 text-slate-100 shadow-2xl backdrop:bg-black/70 sm:p-7">
    <div className="mb-5 flex items-center justify-between"><p className="text-sm text-slate-400">Trade progress review</p><Button variant="ghost" size="sm" onClick={onClose}>Close</Button></div>
    {loading && <p role="status" className="p-6 text-slate-400">Loading recorded observations…</p>}
    {error && <p role="alert" className="rounded-xl bg-amber-400/10 p-4 text-sm text-amber-200">{error}</p>}
    {matches?.matches.length===0 && <div className="space-y-2 p-6 text-sm"><p>No recording matched this trade.</p><p className="text-slate-400">{matches.status.enrolled ? "Account is enrolled now, but this trade has no matching observations." : "Account is not enrolled for recording."} Historical candles cannot recover a {SAMPLE_INTERVAL_SECONDS}-second path.</p></div>}
    {(matches?.matches.length ?? 0)>1 && <div className="mb-4 text-sm"><p className="mb-2 text-amber-200">Several positions overlap this trade. Choose a recording to inspect; no automatic match was made.</p>{matches!.matches.map(m => <button className="mr-2 rounded-lg border border-slate-700 p-2" key={m.id} onClick={() => { setPositionId(m.id); setData(null); setCursor(0); setLoading(true); setError(null); }}>{m.direction} · {time(m.opened)} ET</button>)}</div>}
    {data && <TradeProgressChart data={data}/>}
    {data && <p className="mt-4 text-xs text-slate-500">Position candidate matched by delivery contract and trade time. Verify the position changes when reviewing a partial fill or reversal.</p>}
    {data?.next_cursor && <Button className="mt-4" disabled={loading} onClick={() => { setCursor(data.next_cursor!); setLoading(true); setError(null); }}>Load next observations</Button>}
  </dialog>;
}
