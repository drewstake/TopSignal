import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { requestJson } from "../../lib/api";
import { useDemoMode } from "../../lib/demoMode";
import { easternTime, type Headline } from "./types";

export function ScannerAlerts() {
  const { enabled } = useDemoMode();
  return <AlertStream key={String(enabled)} />;
}

function AlertStream() {
  const [batch, setBatch] = useState<Headline[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    let cursor: number | undefined;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const page = await requestJson<{cursor: number; events: Headline[]}>("/api/scanner/alerts", {
          query: { after: cursor }, signal: controller.signal,
        });
        if (!controller.signal.aborted) {
          cursor = page.cursor;
          if (page.events.length) setBatch(old => [...old, ...page.events].slice(-30));
        }
      } catch { /* A temporary API outage retains the last acknowledged cursor. */ }
      if (!controller.signal.aborted) timer = setTimeout(poll, 2500);
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, []);
  if (!batch.length) return null;
  const e = batch[batch.length - 1];
  return <aside aria-label="Scanner alert" role="status" className="mb-3 flex items-center gap-3 rounded-lg border border-app-accent/50 bg-app-surface px-4 py-2 text-sm">
    <div className="min-w-0 flex-1"><Link to="/scanner" className="font-semibold text-app-accent">
      {batch.length > 1 ? `${batch.length} scanner alerts` : "Scanner"} · {e.tickers.join(", ")}
    </Link><p>{e.title}</p><small className="text-app-muted">{e.source} · Received {easternTime(e.received)}
      {e.link && <> · <a href={e.link} target="_blank" rel="noreferrer">Original ↗</a></>}</small></div>
    <button type="button" aria-label="Dismiss scanner alerts" onClick={() => setBatch([])}>Dismiss</button>
  </aside>;
}
