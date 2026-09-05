import { useEffect, useRef, useState } from "react";
import { requestJson } from "../../lib/api";

export function DecisionDetails({ id, accountId }: { id: number; accountId: number }) {
  const [record, setRecord] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  async function load() {
    if (record || controller.current) return;
    const next = new AbortController();
    controller.current = next;
    setLoading(true);
    try {
      const result = await requestJson(`/api/decision-research/${id}`, {
        query: { account_id: accountId }, signal: next.signal,
      });
      if (!next.signal.aborted) { setRecord(result); setError(null); }
    } catch (cause) {
      if (!next.signal.aborted) setError(cause instanceof Error ? cause.message : "Unable to load recorded context");
    } finally {
      if (!next.signal.aborted) { setLoading(false); controller.current = null; }
    }
  }

  return <details onToggle={event => { if (event.currentTarget.open) void load(); }}>
    <summary className="cursor-pointer text-xs text-app-accent">Inspect recorded context</summary>
    {loading && <p className="mt-2 text-xs text-app-muted">Loading snapshot…</p>}
    {error && <p role="alert" className="mt-2 text-xs text-app-negative">{error}</p>}
    {record !== null && <pre className="mt-3 max-h-80 max-w-xl overflow-auto whitespace-pre-wrap break-words rounded-lg bg-app-bg p-3 text-[11px] text-app-muted">{JSON.stringify(record, null, 2)}</pre>}
  </details>;
}
