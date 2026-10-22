import { useEffect, useMemo, useState } from "react";
import { botsApi } from "../../lib/api";
import type { BotLogs } from "../../lib/types";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { boundedBotRequest } from "./botPageRequests";
import { usePageVisibility } from "./usePageVisibility";

const inputClass = "mt-1 block h-10 w-full rounded-lg border border-app-border bg-app-bg px-3 text-sm text-app-text";
const initialFilters = { hours: "24", level: "", run: "", search: "" };
const label = (value: string) => value.replaceAll("_", " ");
const time = (value: string) => new Date(value).toLocaleString(undefined, { timeZoneName: "short" });

export function BotDiagnosticsPanel({ accountId, demoMode = false }: { accountId: number | null; demoMode?: boolean }) {
  return <Card id="bot-diagnostics" className="order-4 min-w-0 xl:col-span-2">
    <CardHeader>
      <CardTitle>Bot logs & summaries</CardTitle>
      <CardDescription>Investigate decisions, order outcomes, errors, and timing for this account.</CardDescription>
    </CardHeader>
    <CardContent>
      {demoMode ? <p className="text-sm text-app-muted">Server diagnostics are unavailable in demo mode.</p>
        : accountId === null ? <p className="text-sm text-app-muted">Select a ProjectX account to view bot logs.</p>
          : <DiagnosticsView key={accountId} accountId={accountId} />}
    </CardContent>
  </Card>;
}

function DiagnosticsView({ accountId }: { accountId: number }) {
  const [draft, setDraft] = useState(initialFilters);
  const [filters, setFilters] = useState(initialFilters);
  const [data, setData] = useState<BotLogs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const visible = usePageVisibility();
  const request = useMemo(() => ({ accountId, filters, refresh, visible }), [accountId, filters, refresh, visible]);
  const [finishedRequest, setFinishedRequest] = useState<typeof request | null>(null);
  const loading = visible && finishedRequest !== request;

  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    let active = true;
    void boundedBotRequest(botsApi.getLogs(accountId, {
      hours: Number(filters.hours), level: filters.level || undefined,
      run_id: filters.run ? Number(filters.run) : undefined, search: filters.search.trim(), limit: 100,
    }, { signal: controller.signal }), controller, "Log refresh timed out. Try again.", 15_000)
      .then(result => {
        if (!active) return;
        if (result.account_id !== accountId) throw new Error("Log response did not match the selected account.");
        setData(result);
        setError(null);
      })
      .catch((failure: unknown) => {
        if (active) setError(failure instanceof Error ? failure.message : "Could not load bot logs.");
      })
      .finally(() => { if (active) setFinishedRequest(request); });
    return () => { active = false; controller.abort(); };
  }, [accountId, filters, request, visible]);

  useEffect(() => {
    if (!autoRefresh || !visible || loading) return;
    const timer = window.setTimeout(() => setRefresh(value => value + 1), 30_000);
    return () => window.clearTimeout(timer);
  }, [autoRefresh, visible, loading, refresh]);

  const timing = data?.summary.timings.bot_evaluation_timing;
  const submission = data?.summary.timings.bot_order_submission_finished;
  return <div className="space-y-4">
    <form className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5" onSubmit={event => {
      event.preventDefault();
      setData(null);
      setFilters({ ...draft });
    }}>
      <label className="text-xs text-app-text-soft">Time range
        <select className={inputClass} value={draft.hours} onChange={event => setDraft({ ...draft, hours: event.target.value })}>
          <option value="1">Last hour</option><option value="24">Last 24 hours</option>
          <option value="72">Last 3 days</option><option value="168">Last 7 days</option>
        </select>
      </label>
      <label className="text-xs text-app-text-soft">Severity
        <select className={inputClass} value={draft.level} onChange={event => setDraft({ ...draft, level: event.target.value })}>
          <option value="">All levels</option>{["INFO", "WARNING", "ERROR", "CRITICAL", "DEBUG"].map(value => <option key={value}>{value}</option>)}
        </select>
      </label>
      <label className="text-xs text-app-text-soft">Run ID
        <input className={inputClass} type="number" min="1" step="1" placeholder="All runs" value={draft.run}
          onChange={event => setDraft({ ...draft, run: event.target.value })} />
      </label>
      <label className="text-xs text-app-text-soft">Search logs
        <input className={inputClass} maxLength={100} placeholder="Event, reason, correlation ID" value={draft.search}
          onChange={event => setDraft({ ...draft, search: event.target.value })} />
      </label>
      <div className="flex items-end"><Button variant="secondary" type="submit" className="w-full">Apply filters</Button></div>
    </form>
    <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-app-muted">
      <div className="flex items-center gap-3">
        <Button size="sm" variant="ghost" disabled={loading} onClick={() => setRefresh(value => value + 1)}>{loading ? "Loading logs…" : "Refresh logs"}</Button>
        <label className="flex items-center gap-2"><input type="checkbox" checked={autoRefresh} onChange={event => setAutoRefresh(event.target.checked)} />Auto-refresh every 30s</label>
      </div>
      {data && <span>Updated {time(data.generated_at)}</span>}
    </div>
    {loading && !data && <p role="status" className="text-sm text-app-muted">Loading diagnostics…</p>}
    {!loading && error && <p role="alert" className="text-sm text-app-negative">{error}{data ? " Showing the last successful refresh." : ""}</p>}
    {data?.status === "not_configured" && <p role="status" className="rounded-lg border border-app-border p-4 text-sm text-app-text-soft">No diagnostic log file is available yet. Start the backend with production logging enabled; new bot events will appear here.</p>}
    {data?.status === "read_error" && <p role="alert" className="text-sm text-app-negative">Some diagnostic files could not be read. Results may be incomplete; check server log permissions.</p>}
    {data && data.status !== "not_configured" && <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[["Matching events", data.summary.matched_events], ["Errors", data.summary.errors], ["Warnings", data.summary.warnings],
          ["Mean evaluation time", timing ? `${timing.mean_ms.toFixed(1)} ms` : "No timing data"]].map(([title, value]) =>
          <div key={title} className="rounded-xl border border-app-border bg-app-raised/40 p-3"><div className="text-xs text-app-muted">{title}</div><div className="mt-1 text-lg font-semibold text-app-text">{value}</div></div>)}
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <SummaryCounts title="Evaluation outcomes" counts={data.summary.evaluation_outcomes} empty="No evaluations in this selection." />
        <SummaryCounts title="Most common risk blocks" counts={data.summary.risk_blocks} empty="No risk blocks in this selection." />
      </div>
      {(timing || submission) && <p className="text-xs text-app-muted">
        {timing && `Evaluation maximum: ${timing.max_ms.toFixed(1)} ms across ${timing.count} evaluations. `}
        {submission && `Order submission: ${submission.mean_ms.toFixed(1)} ms mean / ${submission.max_ms.toFixed(1)} ms max across ${submission.count} attempts.`}
      </p>}
      <p className="text-xs text-app-muted">Summary uses all matching events in the scanned logs. Events may precede database commit; order acknowledgments are not fills. Global worker events without an account link are excluded.</p>
      {data.scan_limited && <p role="status" className="text-xs text-amber-300">Only the most recent portion of server logs was scanned. This summary may not cover the full selected time range.</p>}
      {data.entries.length === 0 ? <p className="py-4 text-sm text-app-muted">No bot logs match these filters.</p> : <div className="max-h-[36rem] overflow-auto rounded-xl border border-app-border">
        <table className="w-full text-left text-xs">
          <caption className="sr-only">Recent bot diagnostic events</caption>
          <thead className="sticky top-0 bg-app-surface text-app-muted"><tr>{["Time", "Level", "Event & details", "Run"].map(title => <th key={title} className="p-3 font-medium">{title}</th>)}</tr></thead>
          <tbody className="divide-y divide-app-border">{data.entries.map((entry, index) => <tr key={`${entry.timestamp}:${index}`}>
            <td className="whitespace-nowrap p-3 align-top">{time(entry.timestamp)}</td>
            <td className="p-3 align-top"><Badge variant={entry.level === "ERROR" || entry.level === "CRITICAL" ? "negative" : entry.level === "WARNING" ? "warning" : "neutral"}>{entry.level}</Badge></td>
            <td className="min-w-64 max-w-xl p-3 align-top">
              <p className="font-medium text-app-text">{label(entry.event)}</p>
              <p className="mt-1 break-words text-app-muted">{entry.reason ?? entry.reason_code ?? entry.error_type ?? label(entry.evaluation_status ?? entry.order_status ?? "")}</p>
              <details className="mt-2"><summary className="cursor-pointer text-app-text-soft">View event details</summary>
                <pre className="mt-2 max-w-xl whitespace-pre-wrap break-all rounded-lg bg-app-bg p-3 text-[11px]">{JSON.stringify(entry, null, 2)}</pre>
              </details>
            </td><td className="p-3 align-top">{entry.bot_run_id ?? "—"}</td>
          </tr>)}</tbody>
        </table>
      </div>}
      {data.has_more && <p className="text-xs text-app-muted">Showing the newest {data.entries.length} of {data.summary.matched_events} matching events. Narrow the filters to investigate a specific run or correlation ID.</p>}
    </>}
  </div>;
}

function SummaryCounts({ title, counts, empty }: { title: string; counts: Record<string, number>; empty: string }) {
  return <div><h3 className="text-sm font-medium text-app-text">{title}</h3>
    {Object.keys(counts).length ? <ul className="mt-2 flex flex-wrap gap-2">{Object.entries(counts).map(([name, count]) =>
      <li key={name} className="rounded-lg border border-app-border px-2 py-1 text-xs text-app-text-soft">{label(name)} <strong className="ml-1">{count}</strong></li>)}</ul>
      : <p className="mt-2 text-xs text-app-muted">{empty}</p>}
  </div>;
}
