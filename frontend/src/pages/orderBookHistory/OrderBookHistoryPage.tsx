import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Link, useOutletContext } from "react-router-dom";
import type { AppShellOutletContext } from "../../app/AppShell";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { requestJson } from "../../lib/api";
import { useDemoMode } from "../../lib/demoMode";
import { getAccessTokenSync, subscribeSupabaseAuthChanges } from "../../lib/supabase";
import { boundedBotRequest } from "../bot/botPageRequests";
import { usePageVisibility } from "../bot/usePageVisibility";
import type { OrderBookStore } from "../bot/orderBook";
import { mergeLiveHistory, mergeSessionArchive, useLiveHistory } from "./liveHistory";
import { HistoryChart } from "./HistoryChart";
import { csvHistory, localInput, metricUsable, priceChangeAfter, sessionWindow, usable, type BookHistory, type DepthScope, type HistoryPoint } from "./history";

const subscribe = (callback: () => void) => subscribeSupabaseAuthChanges(() => callback());
const inputClass = "h-10 min-w-0 rounded-lg border border-app-border bg-app-bg px-3 text-sm text-app-text";
const format = (value: number | null, digits=2) => value === null ? "—" : value.toLocaleString("en-US", { maximumFractionDigits: digits });
const time = (value: string) => new Date(value).toLocaleString(undefined, { timeZoneName: "short" });
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
type WindowQuery = { start?: string; end?: string; scope: DepthScope; window?: "latest" | "session" };

export function OrderBookHistoryPage() {
  const { selectedAccountId } = useOutletContext<AppShellOutletContext>();
  const demo = useDemoMode();
  const token = useSyncExternalStore(subscribe, getAccessTokenSync);
  return <div className="space-y-6">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div><p className="text-xs uppercase tracking-widest text-app-muted">Market research</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">Order book history</h1>
        <p className="mt-2 max-w-2xl text-sm text-app-muted">Look at resting liquidity and price together. Zoom into the buildup, select a moment, and compare what followed.</p>
      </div>
      <Link className="text-sm text-app-accent underline" to={`/bot${selectedAccountId ? `?account=${selectedAccountId}` : ""}`}>Back to live order book</Link>
      <Link className="text-sm text-app-accent underline" to={`/microstructure${selectedAccountId ? `?account=${selectedAccountId}` : ""}`}>Open liquidity research, replay & event studies →</Link>
    </header>
    {demo.enabled ? <Card><p>Exit Demo Mode to view your recorded market history.</p></Card>
      : selectedAccountId === null ? <Card><p>Select a ProjectX account to view your recordings.</p></Card>
      : <HistoryView key={`${selectedAccountId}:${token}`} accountId={selectedAccountId} />}
  </div>;
}

export function HistoryView({ accountId, compact = false, liveStore }: { accountId: number; compact?: boolean; liveStore?: OrderBookStore }) {
  const [query, setQuery] = useState<WindowQuery>({ scope: "all", window: "session" });
  const [refresh, setRefresh] = useState(0);
  const [viewVersion, setViewVersion] = useState(0);
  const [recorded, setData] = useState<BookHistory | null>(null);
  const recordedRef = useRef<BookHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [from, setFrom] = useState("");
  const [until, setUntil] = useState("");
  const [scope, setScope] = useState<DepthScope>("all");
  const [following, setFollowing] = useState(true);
  const visible = usePageVisibility();
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!visible) return;
    const timer = window.setInterval(() => setNow(Date.now()), 15000);
    return () => window.clearInterval(timer);
  }, [visible]);
  const autoRefresh = following && !query.start;
  const live = useLiveHistory(liveStore, query.scope, autoRefresh && visible);
  const data = useMemo(() => query.start ? recorded : mergeLiveHistory(recorded, live.points, query.scope, query.window), [recorded, live.points, query]);
  const inFlight = useRef<AbortController | null>(null);
  const pauseUpdates = () => { setFollowing(false); inFlight.current?.abort(); };

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    inFlight.current = controller;
    const previous = recordedRef.current;
    const incremental = query.window === "session" && previous?.scope === query.scope && Date.parse(previous.start) / 1000 === sessionWindow(Date.now()).start;
    const wide = (query.window === "session" && !incremental) || (query.start && query.end && Date.parse(query.end) - Date.parse(query.start) > 3600000);
    void boundedBotRequest(requestJson<BookHistory>(`/api/accounts/${accountId}/order-book-history`, {
      query: incremental ? { scope: query.scope } : query, signal: controller.signal,
    }), controller, "The recording host took too long to respond. Check its connection and try again.", wide ? 125000 : 30000)
      .then(result => {
        if (!active || controller.signal.aborted) return;
        setError(null);
        const next = incremental ? mergeSessionArchive(previous!, result) : result;
        recordedRef.current = next;
        setData(next); setFrom(localInput(next.start)); setUntil(localInput(next.end)); setScope(next.scope);
      }).catch(failure => { if (active && !(failure instanceof DOMException && failure.name === "AbortError")) setError(failure instanceof Error ? failure.message : "Could not load recordings."); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; controller.abort(); };
  }, [accountId, query, refresh]);

  useEffect(() => {
    if (!autoRefresh || !visible || busy) return;
    // Schedule after completion, so slow recording hosts never receive overlapping polls.
    const timer = window.setTimeout(() => {
      setBusy(true);
      setRefresh(value => value + 1);
    }, liveStore || query.window === "session" ? 30000 : 1000);
    return () => window.clearTimeout(timer);
  }, [autoRefresh, visible, busy, refresh, liveStore, query.window]);

  const load = (nextQuery: WindowQuery) => {
    setViewVersion(value => value + 1);
    recordedRef.current = null;
    setBusy(true); setError(null); setData(null);
    setQuery(nextQuery);
    setRefresh(value => value + 1);
  };

  const applyRange = (start: Date, end: Date) => {
    if (!Number.isFinite(+start) || !Number.isFinite(+end) || +end <= +start || +end - +start > 86400000) {
      setError("Choose a valid time range of one session (up to 24 hours) or less."); return;
    }
    load({ start: start.toISOString(), end: end.toISOString(), scope });
  };
  return <>
    <Card className="space-y-4">
      {!compact && <form className="flex flex-wrap items-end gap-3" onSubmit={e => { e.preventDefault(); applyRange(new Date(from), new Date(until)); }}>
        <label className="flex min-w-52 flex-1 flex-col gap-1.5 text-xs text-app-muted">From
          <input required aria-label="History start" type="datetime-local" step="1" className={inputClass} value={from} onChange={e => { pauseUpdates(); setFrom(e.target.value); }} />
        </label>
        <label className="flex min-w-52 flex-1 flex-col gap-1.5 text-xs text-app-muted">To
          <input required aria-label="History end" type="datetime-local" step="1" className={inputClass} value={until} onChange={e => { pauseUpdates(); setUntil(e.target.value); }} />
        </label>
        <label className="flex flex-col gap-1.5 text-xs text-app-muted">Depth scope
          <select className={inputClass} value={scope} onChange={e => { pauseUpdates(); setScope(e.target.value as DepthScope); }}>
            <option value="all">All observed levels</option>
            {[10,20,50,100].map(n => <option key={n} value={n}>Nearest {n} levels</option>)}
          </select>
        </label>
        <Button type="submit" disabled={busy}>Load history</Button>
      </form>}
      {compact && <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">{liveStore ? "Live & recorded liquidity" : "Recorded liquidity"}</h3>
        <a className="text-xs text-app-accent underline" href={`/order-book-history?account=${accountId}`}>Full history & date range</a>
      </div>}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant={query.window === "session" ? "secondary" : "ghost"} disabled={busy} onClick={() => { setFollowing(true); load({ scope, window: "session" }); }}>Full session</Button>
          <Button size="sm" variant={!query.start && query.window !== "session" ? "secondary" : "ghost"} disabled={busy} onClick={() => { setFollowing(true); load({ scope }); }}>Latest 15m</Button>
          {[5,30,60].map(minutes => <Button key={minutes} size="sm" variant="ghost" disabled={busy || !data?.latest}
            onClick={() => { const end = new Date(new Date(data!.latest!).getTime()+1); applyRange(new Date(+end-minutes*60000), end); }}>Latest {minutes === 60 ? "1h" : `${minutes}m`}</Button>)}
          <Button size="sm" variant="ghost" disabled={busy || !data} onClick={() => {
            const end = new Date(data!.start); applyRange(new Date(+end-(+new Date(data!.end)-+end)), end);
          }}>← Earlier window</Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => load(query)}>Refresh</Button>
        </div>
        <span className="text-xs text-app-muted">Times in {timezone} · Session: 6 PM–5 PM Eastern · Up to 24 hours per view</span>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-app-muted">
        <span>{autoRefresh ? visible ? liveStore ? live.fresh ? "Live ProjectX depth · Updates every 1s" : "Waiting for fresh live depth · Showing available history" : query.window === "session" ? "Full session · Recordings refresh every 30s" : "Updates every 1s · Latest 15 minutes" : "Updates paused while this tab is hidden" : "Updates paused for inspection"}{query.window === "session" && liveStore ? " · Full session" : ""}</span>
        <Button size="sm" variant="ghost" onClick={() => {
          if (autoRefresh) pauseUpdates();
          else { setFollowing(true); load({ scope, window: query.window ?? "session" }); }
        }}>{autoRefresh ? "Pause updates" : "Follow latest"}</Button>
      </div>
      {data && <div className="flex flex-wrap justify-between gap-2 border-t border-app-border pt-3 text-xs text-app-muted">
        <span><span className="font-semibold text-app-text">Source: {data.source}</span> · Account access {accountId} · Shared market data for your user</span>
        <span>{!data.sealed_only && <>Live through {time(data.latest!)} · </>}Recorded through {recorded?.latest ? time(recorded.latest) : "—"}</span>
      </div>}
      {data && <p className="text-xs text-app-muted" role="status">{data.recording_health
        ? `Recorder: ${data.recording_health.stale || now-Date.parse(data.recording_health.at)>60000 ? "status stale" : data.recording_health.state} · Last durable measurement write ${data.recording_health.last_totals_write_at ? time(data.recording_health.last_totals_write_at) : "unknown"} · Pending measurements ${data.recording_health.pending_measurements ?? "unknown"} / events ${data.recording_health.pending_events} · Rejected envelopes ${data.recording_health.rejected_events} · Dropped measurements ${data.recording_health.dropped_measurements ?? "unknown (not measured by provider)"} · Reconnects ${data.recording_health.reconnects}`
        : "Recorder health unavailable · Latest sealed observation is shown above; this does not prove current feed health."}</p>}
    </Card>
    {busy && !data && <Card><p role="status" className="text-sm text-app-muted">Reading saved order book measurements…</p></Card>}
    {error && <Card><p role="alert" className="text-sm text-app-negative">{error}</p></Card>}
    {data && !data.points.length && <Card>
      <h2 className="font-semibold">No recordings in this window</h2>
      <p className="mt-2 text-sm text-app-muted">{data.earliest ? `Available history starts ${time(data.earliest)}. Try Latest 15m or choose another time.` : "No completed summary files are available for your user on this source. The recorder must be enabled on the recording host."}</p>
    </Card>}
    {data && data.points.length > 0 && <HistoryExplorer key={`${accountId}:${data.scope}:${viewVersion}`} data={data}
      onInteraction={pauseUpdates}
      onInspect={point => applyRange(new Date((point.time-300)*1000), new Date((point.time+60)*1000))} />}
    <p className="max-w-4xl text-xs leading-relaxed text-app-muted">{liveStore ? "Live measurements use the current ProjectX order-book stream, sampled every second while this tab is visible. They are provisional and kept in this tab only; saved recordings catch up separately. " : "Only completed recording segments are shown, usually about a minute behind collection. "}Observed depth can be incomplete; gaps mark missing measurements and changes between recorded and live sources. This view helps investigate what happened—it does not establish that imbalance predicts a move.</p>
  </>;
}

export function HistoryExplorer({ data, onInspect, onInteraction }: { data: BookHistory; onInspect: (point: HistoryPoint) => void; onInteraction?: () => void }) {
  const [chosenContract, setContract] = useState(data.contracts[0]);
  const contract = data.contracts.includes(chosenContract) ? chosenContract : data.contracts[0];
  const points = useMemo(() => data.points.filter(p => p.contract_id === contract), [data.points, contract]);
  const initial = useMemo<[number, number]>(() => [new Date(data.start).getTime()/1000, new Date(data.end).getTime()/1000], [data.start, data.end]);
  const [inspectionRange, setRange] = useState<[number,number] | null>(null);
  const range = inspectionRange ?? initial;
  const [chosen, setChosen] = useState<HistoryPoint | null>(null);
  const selected = chosen?.contract_id === contract ? chosen : (data.sealed_only ? points.find(usable) ?? points[0] : points.filter(usable).at(-1) ?? points.at(-1)) ?? null;
  const flagged = points.filter(p => p.quality_flags.length).length;
  const qualityCounts = useMemo(() => {
    const counts: Record<string,number> = {};
    for (const p of points) for (const f of p.quality_flags) counts[f] = (counts[f] ?? 0)+1;
    return Object.entries(counts).sort((a,b) => b[1]-a[1]);
  },[points]);
  const exportCsv = () => {
    const visible = points.filter(p => p.time >= range[0] && p.time <= range[1]);
    const url = URL.createObjectURL(new Blob([csvHistory(data, visible)], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a"); a.href=url; a.download=`MNQ-book-history-${data.start.slice(0,10)}.csv`; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <>
    <Card className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h2 className="text-lg font-semibold">Liquidity before the move</h2>
          <p className="mt-1 text-xs text-app-muted">{points.length.toLocaleString()} observed samples shown · {data.scope === "all" ? "All observed levels" : `Nearest ${data.scope} levels per side`} · {data.interval_ms >= 10000 ? "Session overview · first, last & spikes in each 10s bucket" : data.sealed_only ? "250 ms target spacing" : "Live samples every 1s · recorded samples 250 ms"}{!data.sealed_only && data.interval_ms >= 10000 ? " · Live updates every 1s" : ""}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="Recorded delivery contract" className={inputClass} value={contract} onChange={e => { onInteraction?.(); setContract(e.target.value); setChosen(null); setRange(null); }}>
            {data.contracts.map(c => <option key={c}>{c}</option>)}
          </select>
          <Button size="sm" variant="ghost" onClick={exportCsv}>Export visible CSV</Button>
        </div>
      </div>
      {(data.skipped_segments > 0 || flagged > 0) && <p role="status" className="text-xs text-amber-400">
        {flagged} shown observations have quality flags. Each line uses its own validity checks: {points.filter(p=>!metricUsable(p,"bid_contracts")).length} bid / {points.filter(p=>!metricUsable(p,"ask_contracts")).length} ask / {points.filter(p=>!metricUsable(p,"mid_price")).length} price exclusions. {data.skipped_segments > 0 && `${data.skipped_segments} unreadable segments skipped.`}
      </p>}
      {qualityCounts.length > 0 && <details className="text-xs text-app-muted"><summary className="cursor-pointer">Quality reasons in shown observations (counts can overlap)</summary><ul className="mt-2 flex flex-wrap gap-x-5 gap-y-1">{qualityCounts.map(([reason,count])=><li key={reason}>{reason.replaceAll("_"," ")}: {count.toLocaleString()}</li>)}</ul><p className="mt-2">Provider-time warnings alone retain receipt-fresh liquidity totals. Stale sides, disconnects and unknown intervals break their affected lines. Raw flags are preserved.</p></details>}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-app-muted">
        <span className="flex flex-wrap gap-4"><span className="text-blue-400">● Mid-price</span><span className="text-emerald-400">● Bids</span><span className="text-rose-400">● Asks</span><span className="text-violet-400">● Imbalance</span><span className="text-amber-400">▧ Missing / stale / source boundary</span></span>
        <div className="flex gap-2"><Button size="sm" variant="ghost" onClick={() => {
          onInteraction?.();
          const center = selected && selected.time >= range[0] && selected.time <= range[1] ? selected.time : (range[0]+range[1])/2;
          const half = Math.max(1,(range[1]-range[0])/4); setRange([Math.max(initial[0],center-half),Math.min(initial[1],center+half)]);
        }}>Zoom in</Button><Button size="sm" variant="ghost" onClick={() => setRange(null)}>Reset zoom</Button></div>
      </div>
      <HistoryChart points={points} range={range} selected={selected}
        onSelect={point => { onInteraction?.(); setChosen(point); }}
        onRange={next => { onInteraction?.(); setRange(next); }} />
      <p className="text-xs text-app-muted">Drag across the chart to zoom. Click to inspect a moment; use ← / → while the chart is focused to step through samples. Gaps are not interpolated.</p>
    </Card>
    {selected && <Card className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><h2 className="font-semibold">Selected moment</h2><p className="mt-1 text-xs text-app-muted">{time(selected.timestamp)}</p></div>
        <Button size="sm" variant="secondary" onClick={() => onInspect(selected)}>Inspect preceding 5 minutes</Button>
      </div>
      {selected.quality_flags.length > 0 && <p role="status" className="text-sm text-amber-400">Flagged: {selected.quality_flags.join(", ").replaceAll("_", " ")}. Do not treat this sample as a complete book.</p>}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[["Mid-price", format(selected.mid_price), "text-blue-400"], ["Bid contracts", format(selected.bid_contracts,0), "text-emerald-400"],
          ["Ask contracts", format(selected.ask_contracts,0), "text-rose-400"], ["Imbalance", selected.imbalance === null ? "—" : `${(selected.imbalance*100).toFixed(1)}%`, "text-violet-400"]].map(([label,value,color]) =>
          <div key={label} className="rounded-lg border border-app-border bg-app-bg/40 p-3"><p className="text-xs text-app-muted">{label}</p><p className={`mt-1 text-xl font-semibold tabular-nums ${color}`}>{value}</p></div>)}
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
        {[5,15,60].map(seconds => { const change = priceChangeAfter(points,selected,seconds); return <div key={seconds}>
          <span className="text-app-muted">Price after {seconds}s </span>
          <span className={change === null ? "text-app-muted" : change < 0 ? "text-app-negative" : "text-app-positive"}>
            {change === null ? "No continuous data" : `${change > 0 ? "+" : ""}${format(change)} points`}
          </span>
        </div>; })}
      </div>
      <p className="text-xs text-app-muted">{selected.bid_levels} bid levels / {selected.ask_levels} ask levels · Spread {format(selected.spread)} points. Imbalance = (bids − asks) ÷ (bids + asks). Forward comparisons use recorded mid-price within this loaded window and stop at gaps.</p>
    </Card>}
  </>;
}
