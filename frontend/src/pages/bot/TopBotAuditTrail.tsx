import { useEffect, useState } from "react";
import { botsApi } from "../../lib/api";
import { usePageVisibility } from "./usePageVisibility";

export type AuditPage = { items: { seq: number; at: string; kind: string; operation: string | null;
  details: Record<string, unknown> }[]; next_before: number | null; storage: string };

export function TopBotAuditTrail({ accountId, demoMode }: { accountId: number | null; demoMode: boolean }) {
  const [page, setPage] = useState<AuditPage | null>(null);
  const [before, setBefore] = useState<number | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  const visible = usePageVisibility();
  useEffect(() => {
    setPage(null);
    if (accountId == null || demoMode || !visible) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      setLoading(true);
      try {
        const result = await botsApi.getTopBotAudit(accountId!, before, { signal: controller.signal });
        if (!controller.signal.aborted) { setPage(result); setError(false); }
      } catch { if (!controller.signal.aborted) setError(true); }
      finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          if (before == null) timer = setTimeout(poll, 15_000);
        }
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [accountId, before, demoMode, visible]);
  return <section aria-label="TopBot action audit" className="mt-5 border-t border-app-border pt-4">
    <div className="topbot-strategy-heading"><h3>Action audit</h3><div className="flex gap-2">
      <button className="topbot-run-button" disabled={before == null || loading || demoMode} onClick={() => setBefore(null)}>Latest</button>
      <button className="topbot-run-button" disabled={!page?.next_before || loading || demoMode} onClick={() => setBefore(page!.next_before)}>Older events</button>
    </div></div>
    <p className="topbot-footnote">Newest first · New York time · Requested, acknowledged, and verified actions are separate events. An acknowledgement alone does not confirm a fill or stop change.</p>
    {error && <p role="alert">Audit unavailable. Any displayed events may be stale.</p>}
    {page && page.items.length === 0 && <p className="topbot-muted">No audit events recorded yet. Detailed history begins when action auditing is installed.</p>}
    {page && page.items.length > 0 && <div className="topbot-stat-table" style={{ maxHeight: 420, overflow: "auto" }}><table>
      <thead><tr><th>Time</th><th>Event</th><th>Reason / action</th><th>Stop change</th><th>Evidence</th></tr></thead>
      <tbody>{page.items.map(event => <tr key={event.seq}>
        <td>{new Date(event.at).toLocaleString("en-US", { timeZone: "America/New_York" })}</td>
        <td>{event.kind.replaceAll("_", " ")}</td>
        <td>{String(event.details.reason ?? event.details.action ?? event.details.error_type ?? "—")}</td>
        <td>{event.details.old_stop != null || event.details.new_stop != null
          ? `${event.details.old_stop ?? "—"} → ${event.details.new_stop ?? "—"}` : "—"}</td>
        <td><details><summary>Details #{event.seq}</summary><pre className="max-w-md whitespace-pre-wrap break-all">{JSON.stringify({ operation: event.operation, ...event.details }, null, 2)}</pre></details></td>
      </tr>)}</tbody></table></div>}
    <p className="topbot-footnote">Stored on the worker’s persistent disk. Earlier actions are not reconstructed from the current stop value.</p>
  </section>;
}
