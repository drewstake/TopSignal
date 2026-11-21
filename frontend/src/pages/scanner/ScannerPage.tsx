import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import type { AppShellOutletContext } from "../../app/AppShell";
import { useDemoMode } from "../../lib/demoMode";
import { requestJson } from "../../lib/api";
import { easternTime, type ScannerEvent, type Snapshot } from "./types";

function EventRow({ e, compact }: { e: ScannerEvent; compact: boolean }) {
  const direct = e.kind === "move" && e.updates.length > 0;
  const stamp = e.published ?? e.received;
  // Worker-generated move titles start with the instrument's signed percentage.
  // Other percentages in the headline can belong to its sector benchmark.
  const sign = e.kind === "move" && /^\S+ ([+-])\d+(?:\.\d+)?% in /.exec(e.title)?.[1];
  const direction = sign === "+" ? "Up" : sign === "-" ? "Down" : e.kind === "move" ? "Move" : "Headline";
  return <details className="group scanner-event">
    <summary className={compact ? "scanner-row scanner-row-compact" : "scanner-row"}>
      <time dateTime={new Date(stamp * 1000).toISOString()} title={e.published != null ? "Published" : "First seen"}>
        {easternTime(stamp)}
      </time>
      <span className="scanner-direction" data-direction={direction}>{direction}</span>
      <span className="scanner-headline">
        <strong>{e.tickers.join(" · ") || "Market"}</strong>
        {e.baseline && <small>Initial baseline</small>}
        {e.delayed && !e.baseline && <small className="text-app-warning">Delayed alert</small>}
        <span>{e.title}</span>
      </span>
      <span className="scanner-source">{e.source}</span>
      <span aria-hidden="true" className="scanner-chevron">›</span>
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
    <header className="page-bar"><h1 className="text-2xl font-semibold">Scanner</h1>
      {data && <details className="scanner-health text-sm">
        <summary className="cursor-pointer">Source health · <span className={offline || issues.length || error ? "text-app-warning" : "text-app-muted"}>
          {error ? "Last known" : data.worker} · {sourceSummary}</span></summary>
        <dl className="mt-3 divide-y divide-app-border">{data.sources.map(s => <div key={s.name} className="flex flex-wrap justify-between gap-2 py-2">
          <dt>{s.name}</dt><dd className="text-app-muted">{s.status} · Last success {s.success ? easternTime(s.success) : "never"}</dd>
        </div>)}</dl>
        {data.spend && <p>AI spend and open reservations · ${(data.spend.micro_usd / 1e6).toFixed(4)} · Cycle starting {data.spend.cycle}</p>}
      </details>}</header>
    {error && <p role="alert" className="rounded-lg border border-app-negative/60 bg-app-negative/10 px-4 py-3 font-semibold text-app-negative">Feed unavailable · {data ? "Showing the last received events." : "Retrying…"}</p>}
    {data ? <>
      <div className="scanner-feed">
        <div className="feed-heading"><h2>Event feed</h2><span>Eastern time</span></div>
        {data.events.length > 0 && <div aria-hidden="true" className="scanner-columns">
          <span>Time (ET)</span><span>Direction</span><span>Headline</span><span>Source</span>
        </div>}
        {data.events.length ? data.events.map(e => <EventRow key={e.id} e={e} compact={compactMode.enabled} />) :
          <p className="p-4 text-sm text-app-muted">No scanner events yet.</p>}
      </div></> : !error && <p role="status">Loading scanner…</p>}
  </section>;
}
