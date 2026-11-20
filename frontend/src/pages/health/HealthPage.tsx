import { useEffect, useState } from "react";
import { Button } from "../../components/ui/Button";
import { isApiError, requestJson } from "../../lib/api";
import { useDemoMode } from "../../lib/demoMode";
import { usePageVisibility } from "../bot/usePageVisibility";

const components = {
  api: "API",
  postgres: "PostgreSQL",
  worker: "Background worker",
  recorder: "Independent recorder",
  market: "Market data",
  discord: "Discord reader",
  execution_backup: "Local execution backup",
  archive_backup: "Full off-device backup",
} as const;
type Component = keyof typeof components;
type Status = "Healthy" | "Unhealthy" | "Unknown";
type Reading = { status: Status; reason: string; checked_at: string | null; expires_at?: string | null };
type Report = { as_of: string; components: Partial<Record<Component, Reading>> };
type Sample = Reading & { until?: number };
type Samples = Partial<Record<Component, Sample>>;
const priority = { Unhealthy: 0, Unknown: 1, Healthy: 2 };
const tone = {
  Unhealthy: "border-app-negative/40 bg-app-negative/10 text-app-negative",
  Unknown: "border-app-warning/40 bg-app-warning/10 text-app-warning",
  Healthy: "border-app-positive/40 bg-app-positive/10 text-app-positive",
};
const ids = Object.keys(components) as Component[];
const clock = new Intl.DateTimeFormat("en-US", {
  month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit",
  timeZone: "America/New_York", timeZoneName: "short",
});
const maxAge = 45_000;
const unknown = (reason: string, checked_at: string | null = null): Sample => ({ status: "Unknown", reason, checked_at });

function readComponents(report: Report, started: number): Samples {
  const samples: Samples = {};
  for (const id of ids) {
    if (id === "api") continue;
    const value = report.components?.[id];
    if (!value || !Object.hasOwn(priority, value.status) || !value.reason) {
      samples[id] = unknown("The host returned no check for this component.");
      continue;
    }
    // Age receipts against the host clock. Include time spent in transit and
    // cap even long-lived backup receipts when this page stops getting updates.
    const ttl = value.expires_at ? Date.parse(value.expires_at) - Date.parse(report.as_of) : maxAge;
    samples[id] = { ...value, until: started + Math.min(maxAge, Number.isFinite(ttl) ? ttl : 0) };
  }
  return samples;
}

function useHealthSamples(live: boolean) {
  const [samples, setSamples] = useState<Samples>({});
  const [refresh, setRefresh] = useState(0);
  const visible = usePageVisibility();
  useEffect(() => {
    if (!visible || !live) return;
    const expiry = Math.min(...Object.values(samples).filter(row => row.status !== "Unknown").map(row => row.until ?? Infinity));
    if (!Number.isFinite(expiry)) return;
    const timer = setTimeout(() => {
      const now = Date.now();
      setSamples(previous => {
        const next = { ...previous };
        for (const id of ids) {
          const row = previous[id];
          if (row?.until && row.status !== "Unknown" && row.until <= now)
            next[id] = unknown("The last check is too old to establish current status.", row.checked_at);
        }
        return next;
      });
    }, Math.max(0, expiry - Date.now()) + 1);
    return () => clearTimeout(timer);
  }, [samples, visible, live]);
  useEffect(() => {
    if (!live || !visible) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    function update(next: Samples) {
      if (!controller.signal.aborted) setSamples(previous => ({ ...previous, ...next }));
    }
    async function poll() {
      const started = Date.now();
      const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(12_000)]);
      const liveness = requestJson<{ status: string }>("/health", { signal, accessTokenOverride: null }).then(
        data => {
          const checked = new Date().toISOString();
          update({ api: data?.status === "ok"
            ? { status: "Healthy", reason: "The API answered its liveness check.", checked_at: checked, until: started + maxAge }
            : unknown("The API returned an unrecognized liveness response.", checked) });
        },
        error => {
          const checked = new Date().toISOString();
          update({ api: isApiError(error) && error.status >= 500
            ? { status: "Unhealthy", reason: "The API returned an error on its liveness check.", checked_at: checked, until: started + maxAge }
            : unknown("The browser could not reach the API for a liveness check.", checked) });
        },
      );
      const host = requestJson<Report>("/api/system/health", { signal }).then(report => {
        update(readComponents(report, started));
      }).catch(() => {
        if (controller.signal.aborted) return;
        setSamples(previous => {
          const next = { ...previous };
          for (const id of ids) {
            if (id !== "api") next[id] = unknown("The latest host health report is unavailable.", previous[id]?.checked_at);
          }
          return next;
        });
      });
      await Promise.all([liveness, host]);
      if (!controller.signal.aborted) timer = setTimeout(poll, 20_000);
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [live, visible, refresh]);
  return { samples, visible, refresh: () => setRefresh(value => value + 1) };
}

export default function HealthPage() {
  const { enabled: demo } = useDemoMode();
  const { samples, visible, refresh } = useHealthSamples(!demo);
  const rows = ids.map(id => {
    let value = samples[id] ?? unknown("Waiting for the first check.");
    if (demo) value = unknown("Live health checks are paused in Demo Mode.");
    else if (!visible) value = unknown("Checks pause while this tab is hidden.", value.checked_at);
    return { id, ...value };
  }).sort((a, b) => priority[a.status] - priority[b.status]);
  return <section aria-labelledby="health-heading" className="mx-auto max-w-5xl space-y-4">
    <header className="flex items-center justify-between gap-4">
      <div>
        <h1 id="health-heading" className="text-2xl font-semibold">Health</h1>
        <p className="mt-1 text-sm text-app-muted">{demo ? "Demo Mode" : "Checks refresh every 20 seconds while this tab is visible."}</p>
      </div>
      {!demo && <Button variant="secondary" onClick={refresh}>Refresh</Button>}
    </header>
    <ul aria-label="Component health" className="divide-y divide-app-border overflow-hidden rounded-xl border border-app-border bg-app-surface">
      {rows.map(row => <li key={row.id} className="grid gap-2 px-4 py-4 sm:grid-cols-[8rem_1fr_auto] sm:items-start sm:gap-4 sm:px-5">
        <span className={`w-fit rounded-full border px-2.5 py-1 text-xs font-semibold ${tone[row.status]}`}>{row.status}</span>
        <div><h2 className="font-semibold">{components[row.id]}</h2><p className="mt-1 text-sm text-app-muted">{row.reason}</p></div>
        <div className="text-xs text-app-muted sm:text-right">
          <span className="mr-1 sm:mr-0 sm:block">Last check</span>
          {row.checked_at && Number.isFinite(Date.parse(row.checked_at))
            ? <time className="text-app-text" dateTime={row.checked_at}>{clock.format(new Date(row.checked_at))}</time>
            : <span>Unavailable</span>}
        </div>
      </li>)}
    </ul>
  </section>;
}
