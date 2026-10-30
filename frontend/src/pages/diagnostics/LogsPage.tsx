import { useEffect, useState, useSyncExternalStore } from "react";
import { useOutletContext } from "react-router-dom";
import type { AppShellOutletContext } from "../../app/AppShell";
import { ApiError, requestJson } from "../../lib/api";
import { getAccessTokenSync, subscribeSupabaseAuthChanges } from "../../lib/supabase";
import { useDemoMode } from "../../lib/demoMode";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { BotDiagnosticsPanel } from "../bot/BotDiagnosticsPanel";
import { boundedBotRequest } from "../bot/botPageRequests";
import { usePageVisibility } from "../bot/usePageVisibility";

type Entry = { id: string; timestamp: string; level: string; source: string; event: string; message: string };
export type SystemLogs = {
  generated_at: string; entries: Entry[]; has_more: boolean; scan_limited: boolean;
  worker: { enabled: boolean; healthy: boolean; status: string };
  sources: { host: string; market_feed: string };
};
const time = (value: string) => new Date(value).toLocaleString(undefined, { timeZoneName: "short" });
const inputClass = "h-10 rounded-lg border border-app-border bg-app-bg px-3 text-sm text-app-text";

const subscribeToIdentity = (listener: () => void) => subscribeSupabaseAuthChanges(() => listener());
function readIdentity() {
  const token = getAccessTokenSync();
  if (!token) return "anonymous";
  try {
    const encoded = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(atob(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, "=")));
    return JSON.stringify([payload.iss, payload.sub]);
  } catch { return "unavailable"; }
}

export function LogsPage() {
  const { selectedAccountId } = useOutletContext<AppShellOutletContext>();
  const demo = useDemoMode();
  const identity = useSyncExternalStore(subscribeToIdentity, readIdentity);
  return <div className="space-y-6">
    <header><p className="text-xs uppercase tracking-widest text-app-muted">Workspace health</p>
      <h1 className="mt-2 text-3xl font-semibold">Logs</h1>
      <p className="mt-2 text-sm text-app-muted">See interruptions, host restarts, and market-feed recovery.</p>
    </header>
    {demo.enabled ? <Card><p>Live server logs are unavailable in Demo Mode.</p></Card>
      : <SystemLogView key={identity} />}
    <BotDiagnosticsPanel key={`${identity}:${selectedAccountId}`} accountId={selectedAccountId} demoMode={demo.enabled} />
  </div>;
}

export function SystemLogView() {
  const [data, setData] = useState<SystemLogs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [auto, setAuto] = useState(true);
  const [hours, setHours] = useState("24");
  const [severity, setSeverity] = useState("");
  const [search, setSearch] = useState("");
  const visible = usePageVisibility();

  useEffect(() => {
    if (!visible) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    const load = async () => {
      setBusy(true);
      controller = new AbortController();
      try {
        const result = await boundedBotRequest(requestJson<SystemLogs>("/api/system/logs", {
          query: { hours: Number(hours) }, signal: controller.signal,
        }), controller, "Log request timed out.", 10_000);
        if (active) { setData(result); setError(null); }
      } catch (failure) {
        if (active) {
          const authFailure = failure instanceof ApiError && [401, 403].includes(failure.status);
          if (authFailure) setData(null);
          setError(authFailure ? "Sign in again to read the host logs."
            : "Cannot reach the logs service. The backend, host, or private connection may be unavailable. Check Tailscale and that peteamdpc is powered on.");
        }
      } finally {
        if (active) {
          setBusy(false);
          if (auto) timer = setTimeout(() => { void load(); }, 30_000);
        }
      }
    };
    void load();
    return () => { active = false; clearTimeout(timer); controller?.abort(); };
  }, [visible, refresh, auto, hours]);

  const entries = (data?.entries ?? []).filter(entry => (!severity || entry.level === severity)
    && `${entry.source} ${entry.message} ${entry.event}`.toLowerCase().includes(search.toLowerCase()));
  const stale = Boolean(error) || !visible;
  return <>
    <div className="grid gap-4 sm:grid-cols-3">
      <Card><p className="text-xs text-app-muted">Backend connection</p>
        <p className="mt-2 text-lg font-semibold">{error ? "Unavailable" : data ? stale ? "Paused" : "Connected" : "Checking…"}</p>
        <p className="mt-1 text-xs text-app-muted">{data ? `Last checked ${time(data.generated_at)}` : "Waiting for a response from the host."}</p></Card>
      <Card><p className="text-xs text-app-muted">Bot worker</p>
        <p className="mt-2 text-lg font-semibold">{stale || !data ? "Unknown" : !data.worker.enabled ? "Disabled" : data.worker.healthy ? "Healthy" : "Needs attention"}</p>
        <p className="mt-1 text-xs text-app-muted">Worker health does not confirm market-data or broker availability.</p></Card>
      <Card><p className="text-xs text-app-muted">Recent history</p>
        <p className="mt-2 text-lg font-semibold">{data ? `${data.entries.length} events` : "—"}</p>
        <p className="mt-1 text-xs text-app-muted">Host events and market-feed events for your sign-in.</p></Card>
    </div>
    <Card className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">Service events</h2>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={auto} onChange={event => setAuto(event.target.checked)} />Auto-refresh every 30s</label>
          <Button variant="secondary" size="sm" disabled={busy} onClick={() => setRefresh(value => value + 1)}>{busy ? "Checking…" : "Refresh status"}</Button>
        </div>
      </div>
      {error && <div role="alert" className="rounded-lg border border-app-negative/40 bg-app-negative/10 p-3 text-sm">
        {error}{data && <p className="mt-2 font-semibold">Showing stale history from {time(data.generated_at)}.</p>}
      </div>}
      <div className="flex flex-wrap gap-3">
        <label className="flex flex-col gap-1 text-xs text-app-muted">History window
          <select className={inputClass} value={hours} onChange={event => { setData(null); setHours(event.target.value); }}>
            <option value="1">Last hour</option><option value="24">Last 24 hours</option><option value="168">Last 7 days</option>
          </select></label>
        <label className="flex flex-col gap-1 text-xs text-app-muted">Event severity
          <select className={inputClass} value={severity} onChange={event => setSeverity(event.target.value)}>
            <option value="">All levels</option><option value="ERROR">Errors</option><option value="WARNING">Warnings</option><option value="INFO">Information</option>
          </select></label>
        <label className="flex min-w-48 flex-1 flex-col gap-1 text-xs text-app-muted">Search service events
          <input className={inputClass} value={search} maxLength={100} placeholder="Connection, restart, market feed…" onChange={event => setSearch(event.target.value)} /></label>
      </div>
      {data && Object.values(data.sources).some(value => value !== "available") && <p role="status" className="text-sm text-app-muted">
        Some log sources are unavailable or not configured. An empty list does not mean there were no failures.
      </p>}
      {data && (data.scan_limited || data.has_more) && <p className="text-xs text-app-muted">Showing a bounded recent history, up to 200 events. Older events may be outside the available log files.</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-app-border text-xs text-app-muted"><tr><th className="p-3">Time</th><th className="p-3">Level</th><th className="p-3">Source</th><th className="p-3">Event</th></tr></thead>
          <tbody>{entries.map(entry => <tr key={entry.id} className="border-b border-app-border/50">
            <td className="whitespace-nowrap p-3 text-xs text-app-muted">{time(entry.timestamp)}</td>
            <td className={`p-3 text-xs font-semibold ${entry.level === "ERROR" ? "text-app-negative" : "text-app-text-soft"}`}>{entry.level}</td>
            <td className="whitespace-nowrap p-3">{entry.source}</td><td className="min-w-64 p-3">{entry.message}</td>
          </tr>)}</tbody>
        </table>
        {data && entries.length === 0 && <p className="p-6 text-center text-sm text-app-muted">No matching events in the available history.</p>}
      </div>
      <p className="text-xs leading-relaxed text-app-muted">If the host or dashboard is fully offline, this page may not open. Once it reconnects, refresh to inspect saved events. Auto-refresh runs while this tab is visible; it does not send outage notifications.</p>
    </Card>
  </>;
}
