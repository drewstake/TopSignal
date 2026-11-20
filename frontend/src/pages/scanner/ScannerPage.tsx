import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import type { AppShellOutletContext } from "../../app/AppShell";
import { useDemoMode } from "../../lib/demoMode";
import { requestJson } from "../../lib/api";
import { easternTime, type ScannerEvent, type Snapshot } from "./types";

function EventRow({ e, compact }: { e: ScannerEvent; compact: boolean }) {
  const direct = e.kind === "move" && e.updates.length > 0;
  const stamp = e.published ?? e.received;
  return <details className="group border-t border-app-border first:border-0">
    <summary className={`grid cursor-pointer list-none grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 px-4 sm:grid-cols-[6rem_minmax(0,1fr)_auto] [&::-webkit-details-marker]:hidden ${compact ? "py-2" : "py-3"}`}>
      <strong className="break-words text-sm text-app-accent">{e.tickers.join(" · ") || "Market"}</strong>
      <span className="col-span-2 row-start-2 min-w-0 break-words text-sm font-medium sm:col-span-1 sm:col-start-2 sm:row-start-1">
        {e.baseline && <span className="mr-2 rounded bg-app-bg px-1.5 py-0.5 text-xs text-app-muted">Initial baseline</span>}
        {e.delayed && !e.baseline && <span className="mr-2 rounded bg-app-warning/10 px-1.5 py-0.5 text-xs text-app-warning">Delayed alert</span>}
        {e.title}
      </span>
      <span className="col-start-2 row-start-1 whitespace-nowrap text-xs tabular-nums text-app-muted sm:col-start-3">
        <time dateTime={new Date(stamp * 1000).toISOString()} title={e.published != null ? "Published" : "First seen"}>{easternTime(stamp)}</time>
        <span aria-hidden="true" className="ml-2 inline-block group-open:rotate-90">›</span>
      </span>
    </summary>
    <div className="space-y-2 border-t border-app-border/60 bg-app-bg/40 px-4 py-3 text-sm">
      <p className="text-app-muted">{e.kind === "move" ? "Price move" : "Headline"} · {e.link
        ? <a className="text-app-accent" href={e.link} target="_blank" rel="noreferrer">{e.source} ↗</a> : e.source}
        {" · "}First seen {easternTime(e.received)}{e.published != null && ` · Published ${easternTime(e.published)}`}</p>
      {e.summary && <p><strong>Summary</strong> · {e.summary}</p>}
      {e.cause && (!direct || e.citation) && <p><strong>Catalyst</strong> · <span>{e.cause}</span> {e.citation && <a className="text-app-accent" href={e.citation} target="_blank" rel="noreferrer">Source{e.citation_time && ` · ${easternTime(e.citation_time)}`} ↗</a>}</p>}
      {e.updates.length > 0 && <h3 className="font-semibold">Update history</h3>}
    {e.updates.map(u => <div key={u.link+u.title} className="mt-2 border-l-2 border-app-accent/50 pl-3 text-sm">
      <span className="text-app-muted">{u.relation} · {u.source}</span><p><a href={u.link} target="_blank" rel="noreferrer">{u.title} ↗</a></p>
      <small>First seen {easternTime(u.received)}{u.published != null && ` · Published ${easternTime(u.published)}`}</small>
    </div>)}
    {Object.entries(e.jobs).filter(([k,j]) => k === "delivery" || j.state !== "dispatched").map(([k,j]) =>
      <span key={k} className="mr-3 text-xs text-app-muted">{k === "delivery" ? "Notification" : k === "search" ? "Catalyst search" : "Summary"} {j.state === "working" ? "in progress" : j.state}{j.detail && ` · ${j.detail}`}</span>)}
  </div></details>;
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
        const snapshot = await requestJson<Snapshot>("/api/scanner", { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(12_000)]) });
        if (!controller.signal.aborted) { setData(snapshot); setError(false); }
      } catch { if (!controller.signal.aborted) setError(true); }
      if (!controller.signal.aborted) timer = setTimeout(refresh, 4000);
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, []);
  const sources = data?.sources.filter(s => s.name !== "Scanner worker") ?? [];
  const issues = sources.filter(s => !/^(Healthy$|Live\b)/i.test(s.status));
  const offline = data && data.worker !== "Running";
  const sourceSummary = issues.length === 1 ? `${issues[0].name} · ${issues[0].status}`
    : issues.length ? `${issues.length} sources need attention` : sources.length ? `${sources.length} healthy` : "No sources reported";
  return <section className="space-y-3 text-app-text">
    <header className="flex items-baseline justify-between"><h1 className="text-2xl font-semibold">Scanner</h1>
      <span className="text-xs text-app-muted">Eastern time</span></header>
    {error && <p role="alert" className="rounded-lg border border-app-negative/60 bg-app-negative/10 px-4 py-3 font-semibold text-app-negative">Feed unavailable · {data ? "Showing the last received events." : "Retrying…"}</p>}
    {data ? <>
      <details className="rounded-lg border border-app-border bg-app-surface p-3 text-sm">
        <summary className="cursor-pointer">Source health · <span className={offline || issues.length || error ? "text-app-warning" : "text-app-muted"}>
          {error ? "Last known" : data.worker} · {sourceSummary}</span></summary>
        <dl className="mt-3 divide-y divide-app-border">{data.sources.map(s => <div key={s.name} className="flex flex-wrap justify-between gap-2 py-2">
          <dt>{s.name}</dt><dd className="text-app-muted">{s.status} · Last success {s.success ? easternTime(s.success) : "never"}</dd>
        </div>)}</dl>
        {data.spend && <p>AI spend and open reservations · ${(data.spend.micro_usd / 1e6).toFixed(4)} · Cycle starting {data.spend.cycle}</p>}
      </details>
      <div className="overflow-hidden rounded-xl border border-app-border bg-app-surface">
        <h2 className="border-b border-app-border px-4 py-2 text-sm font-semibold">Event feed</h2>
        {data.events.length ? data.events.map(e => <EventRow key={e.id} e={e} compact={compactMode.enabled} />) :
          <p className="p-4 text-sm text-app-muted">No scanner events yet.</p>}
      </div></> : !error && <p role="status">Loading scanner…</p>}
  </section>;
}
