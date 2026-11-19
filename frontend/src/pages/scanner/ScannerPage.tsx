import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import type { AppShellOutletContext } from "../../app/AppShell";
import { useDemoMode } from "../../lib/demoMode";
import { requestJson } from "../../lib/api";
import { easternTime, type ScannerEvent, type Snapshot } from "./types";

function EventRow({ e, compact }: { e: ScannerEvent; compact: boolean }) {
  const direct = e.kind === "move" && e.updates.length > 0;
  return <article className={`border-t border-app-border ${compact ? "py-2" : "py-4"}`}>
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-app-muted">
      <strong className="text-app-accent">{e.tickers.join(" · ")}</strong>
      <span>{e.kind === "move" ? "Price move" : "Headline"}</span>
      <span>{e.source}</span>{e.delayed && !e.baseline && <span>Delayed alert</span>}{e.baseline && <span>Initial baseline</span>}
    </div>
    <h2 className="my-1 font-semibold">{e.link ? <a href={e.link} target="_blank" rel="noreferrer">{e.title} ↗</a> : e.title}</h2>
    <p className="text-xs text-app-muted">First seen {easternTime(e.received)}{e.published != null && <> · Published {easternTime(e.published)}</>}</p>
    {e.summary && <p className="mt-2 text-sm">{e.summary}</p>}
    {e.cause && (!direct || e.citation) && <p className="mt-2 text-sm">{e.cause} {e.citation && <a className="text-app-accent" href={e.citation} target="_blank" rel="noreferrer">Source{e.citation_time && ` · ${easternTime(e.citation_time)}`} ↗</a>}</p>}
    {e.updates.map(u => <div key={u.link+u.title} className="mt-2 border-l-2 border-app-accent/50 pl-3 text-sm">
      <span className="text-app-muted">{u.relation} · {u.source}</span><p><a href={u.link} target="_blank" rel="noreferrer">{u.title} ↗</a></p>
      <small>First seen {easternTime(u.received)}{u.published != null && ` · Published ${easternTime(u.published)}`}</small>
    </div>)}
    {Object.entries(e.jobs).filter(([k,j]) => k === "delivery" || j.state !== "dispatched").map(([k,j]) =>
      <span key={k} className="mr-3 text-xs text-app-muted">{k === "delivery" ? "Notification" : k === "search" ? "Catalyst search" : "Summary"} {j.state === "working" ? "in progress" : j.state}{j.detail && ` · ${j.detail}`}</span>)}
  </article>;
}

export function ScannerPage() {
  const demo = useDemoMode();
  return <Feed key={String(demo.enabled)} />;
}

function Feed() {
  const { compactMode } = useOutletContext<AppShellOutletContext>();
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const snapshot = await requestJson<Snapshot>("/api/scanner", { signal: controller.signal });
        if (!controller.signal.aborted) { setData(snapshot); setError(false); }
      } catch { if (!controller.signal.aborted) setError(true); }
      if (!controller.signal.aborted) timer = setTimeout(refresh, 4000);
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, []);
  return <section className="space-y-4 text-app-text">
    <header><h1 className="text-2xl font-semibold">Scanner</h1>
      <p className="text-sm text-app-muted">Headlines and unusual equity moves · Eastern time</p></header>
    {error && <p role="alert" className="text-app-warning">Scanner refresh failed. Retrying…</p>}
    {data ? <>
      <details className="rounded-lg border border-app-border bg-app-surface p-3 text-sm">
        <summary className="cursor-pointer">Source health · {data.worker} · {data.sources.find(s => s.name === "Equity prices")?.status ?? "Price feed not configured"}</summary>
        <dl className="mt-3 divide-y divide-app-border">{data.sources.map(s => <div key={s.name} className="flex flex-wrap justify-between gap-2 py-2">
          <dt>{s.name}</dt><dd className="text-app-muted">{s.status} · Last success {s.success ? easternTime(s.success) : "never"}</dd>
        </div>)}</dl>
        {data.spend && <p>AI spend and open reservations · ${(data.spend.micro_usd / 1e6).toFixed(4)} · Cycle starting {data.spend.cycle}</p>}
      </details>
      <div className="rounded-xl border border-app-border bg-app-surface px-4">
        {data.events.length ? data.events.map(e => <EventRow key={e.id} e={e} compact={compactMode.enabled} />) :
          <p className="py-5 text-sm text-app-muted">No scanner events yet.</p>}
      </div></> : !error && <p role="status">Loading scanner…</p>}
  </section>;
}
