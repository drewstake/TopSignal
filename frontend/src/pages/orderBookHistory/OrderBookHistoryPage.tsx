import { useEffect, useState, useSyncExternalStore } from "react";
import { Link, useOutletContext } from "react-router-dom";
import type { AppShellOutletContext } from "../../app/AppShell";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { requestJson } from "../../lib/api";
import { useDemoMode } from "../../lib/demoMode";
import { getAccessTokenSync, subscribeSupabaseAuthChanges } from "../../lib/supabase";
import { boundedBotRequest } from "../bot/botPageRequests";
import { HistoryChart } from "./HistoryChart";
import { csvHistory, localInput, priceChangeAfter, usable, type BookHistory, type DepthScope, type HistoryPoint } from "./history";

const subscribe = (callback: () => void) => subscribeSupabaseAuthChanges(() => callback());
const inputClass = "h-10 min-w-0 rounded-lg border border-app-border bg-app-bg px-3 text-sm text-app-text";
const format = (value: number | null, digits=2) => value === null ? "—" : value.toLocaleString("en-US", { maximumFractionDigits: digits });
const time = (value: string) => new Date(value).toLocaleString(undefined, { timeZoneName: "short" });
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
type WindowQuery = { start?: string; end?: string; scope: DepthScope };

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

export function HistoryView({ accountId }: { accountId: number }) {
  const [query, setQuery] = useState<WindowQuery>({ scope: "all" });
  const [refresh, setRefresh] = useState(0);
  const [data, setData] = useState<BookHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [from, setFrom] = useState("");
  const [until, setUntil] = useState("");
  const [scope, setScope] = useState<DepthScope>("all");

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setBusy(true); setError(null); setData(null);
    void boundedBotRequest(requestJson<BookHistory>(`/api/accounts/${accountId}/order-book-history`, {
      query, signal: controller.signal,
    }), controller, "The recording host took too long to respond. Check its connection and try again.", 30000)
      .then(result => {
        if (!active) return;
        setData(result); setFrom(localInput(result.start)); setUntil(localInput(result.end)); setScope(result.scope);
      }).catch(failure => { if (active) setError(failure instanceof Error ? failure.message : "Could not load recordings."); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; controller.abort(); };
  }, [accountId, query, refresh]);

  const applyRange = (start: Date, end: Date) => {
    if (!Number.isFinite(+start) || !Number.isFinite(+end) || +end <= +start || +end - +start > 3600000) {
      setError("Choose a valid time range of one hour or less."); return;
    }
    setQuery({ start: start.toISOString(), end: end.toISOString(), scope });
  };
  return <>
    <Card className="space-y-4">
      <form className="flex flex-wrap items-end gap-3" onSubmit={e => { e.preventDefault(); applyRange(new Date(from), new Date(until)); }}>
        <label className="flex min-w-52 flex-1 flex-col gap-1.5 text-xs text-app-muted">From
          <input required aria-label="History start" type="datetime-local" step="1" className={inputClass} value={from} onChange={e => setFrom(e.target.value)} />
        </label>
        <label className="flex min-w-52 flex-1 flex-col gap-1.5 text-xs text-app-muted">To
          <input required aria-label="History end" type="datetime-local" step="1" className={inputClass} value={until} onChange={e => setUntil(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1.5 text-xs text-app-muted">Depth scope
          <select className={inputClass} value={scope} onChange={e => setScope(e.target.value as DepthScope)}>
            <option value="all">All observed levels</option>
            {[10,20,50,100].map(n => <option key={n} value={n}>Nearest {n} levels</option>)}
          </select>
        </label>
        <Button type="submit" disabled={busy}>Load history</Button>
      </form>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => setQuery({ scope })}>Latest 15m</Button>
          {[5,30,60].map(minutes => <Button key={minutes} size="sm" variant="ghost" disabled={busy || !data?.latest}
            onClick={() => { const end = new Date(new Date(data!.latest!).getTime()+1); applyRange(new Date(+end-minutes*60000), end); }}>Latest {minutes === 60 ? "1h" : `${minutes}m`}</Button>)}
          <Button size="sm" variant="ghost" disabled={busy || !data} onClick={() => {
            const end = new Date(data!.start); applyRange(new Date(+end-(+new Date(data!.end)-+end)), end);
          }}>← Earlier window</Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => setRefresh(v => v+1)}>Refresh</Button>
        </div>
        <span className="text-xs text-app-muted">Times in {timezone} · Up to 1 hour per view</span>
      </div>
      {data && <div className="flex flex-wrap justify-between gap-2 border-t border-app-border pt-3 text-xs text-app-muted">
        <span><span className="font-semibold text-app-text">Source: {data.source}</span> · Account access {accountId} · Shared market data for your user</span>
        <span>Recorded through {data.latest ? time(data.latest) : "—"}</span>
      </div>}
    </Card>
    {busy && <Card><p role="status" className="text-sm text-app-muted">Reading saved order book measurements…</p></Card>}
    {error && <Card><p role="alert" className="text-sm text-app-negative">{error}</p></Card>}
    {data && !data.points.length && <Card>
      <h2 className="font-semibold">No recordings in this window</h2>
      <p className="mt-2 text-sm text-app-muted">{data.earliest ? `Available history starts ${time(data.earliest)}. Try Latest 15m or choose another time.` : "No completed summary files are available for your user on this source. The recorder must be enabled on the recording host."}</p>
    </Card>}
    {data && data.points.length > 0 && <HistoryExplorer key={`${data.generated_at}:${data.scope}`} data={data}
      onInspect={point => applyRange(new Date((point.time-300)*1000), new Date((point.time+60)*1000))} />}
    <p className="max-w-4xl text-xs leading-relaxed text-app-muted">Only completed recording segments are shown, usually about a minute behind collection. Observed depth can be incomplete; amber gaps mark missing or flagged measurements. This view helps investigate what happened—it does not establish that imbalance predicts a move.</p>
  </>;
}

export function HistoryExplorer({ data, onInspect }: { data: BookHistory; onInspect: (point: HistoryPoint) => void }) {
  const [contract, setContract] = useState(data.contracts[0]);
  const points = data.points.filter(p => p.contract_id === contract);
  const initial: [number, number] = [new Date(data.start).getTime()/1000, new Date(data.end).getTime()/1000];
  const [range, setRange] = useState<[number,number]>(initial);
  const [chosen, setChosen] = useState<HistoryPoint | null>(null);
  const selected = chosen?.contract_id === contract ? chosen : points.find(usable) ?? points[0] ?? null;
  const flagged = points.filter(p => !usable(p)).length;
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
          <p className="mt-1 text-xs text-app-muted">{points.length.toLocaleString()} measurements · {data.scope === "all" ? "All observed levels" : `Nearest ${data.scope} levels per side`} · 250 ms target spacing</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="Recorded delivery contract" className={inputClass} value={contract} onChange={e => { setContract(e.target.value); setChosen(null); setRange(initial); }}>
            {data.contracts.map(c => <option key={c}>{c}</option>)}
          </select>
          <Button size="sm" variant="ghost" onClick={exportCsv}>Export visible CSV</Button>
        </div>
      </div>
      {(data.skipped_segments > 0 || flagged > 0) && <p role="status" className="text-xs text-amber-400">
        {flagged} flagged measurements excluded from lines. {data.skipped_segments > 0 && `${data.skipped_segments} unreadable segments skipped.`}
      </p>}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-app-muted">
        <span><span className="text-blue-400">● Mid-price</span>　<span className="text-emerald-400">● Bids</span>　<span className="text-rose-400">● Asks</span>　<span className="text-violet-400">● Imbalance</span></span>
        <div className="flex gap-2"><Button size="sm" variant="ghost" onClick={() => {
          const center = selected && selected.time >= range[0] && selected.time <= range[1] ? selected.time : (range[0]+range[1])/2;
          const half = Math.max(1,(range[1]-range[0])/4); setRange([Math.max(initial[0],center-half),Math.min(initial[1],center+half)]);
        }}>Zoom in</Button><Button size="sm" variant="ghost" onClick={() => setRange(initial)}>Reset zoom</Button></div>
      </div>
      <HistoryChart points={points} range={range} selected={selected} onSelect={setChosen} onRange={setRange} />
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
