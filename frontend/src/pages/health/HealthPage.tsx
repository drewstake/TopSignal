import { useEffect, useState } from "react";
import { Button } from "../../components/ui/Button";
import { Icon, type IconName } from "../../components/ui/Icon";
import { isApiError, requestJson } from "../../lib/api";
import { useDemoMode } from "../../lib/demoMode";
import { usePageVisibility } from "../bot/usePageVisibility";
import { easternTime, type Snapshot } from "../scanner/types";
import "./HealthPage.css";

type ScannerHealth = Omit<Snapshot, "events">;

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
const statuses = ["Unhealthy", "Unknown", "Healthy"] as const;
const icons: Record<Component, IconName> = {
  api: "link", postgres: "database", worker: "bot", recorder: "recorders",
  market: "chart", discord: "message", execution_backup: "archive", archive_backup: "cloud",
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
  const [scanner, setScanner] = useState<ScannerHealth | null>(null);
  const [scannerFailed, setScannerFailed] = useState(false);
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
      const sources = requestJson<ScannerHealth>("/api/scanner/health", { signal }).then(data => {
        if (controller.signal.aborted) return;
        if (!Array.isArray(data?.sources) || typeof data.worker !== "string") { setScannerFailed(true); return; }
        setScanner(data);
        setScannerFailed(false);
      }).catch(() => { if (!controller.signal.aborted) setScannerFailed(true); });
      await Promise.all([liveness, host, sources]);
      if (!controller.signal.aborted) timer = setTimeout(poll, 20_000);
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [live, visible, refresh]);
  return { samples, scanner, scannerFailed, visible, refresh: () => setRefresh(value => value + 1) };
}

export default function HealthPage() {
  const { enabled: demo } = useDemoMode();
  const { samples, scanner, scannerFailed, visible, refresh } = useHealthSamples(!demo);
  const rows = ids.map(id => {
    let value = samples[id] ?? unknown("Waiting for the first check.");
    if (demo) value = unknown("Live health checks are paused in Demo Mode.");
    else if (!visible) value = unknown("Checks pause while this tab is hidden.", value.checked_at);
    return { id, ...value };
  }).sort((a, b) => priority[a.status] - priority[b.status]);
  return <section aria-labelledby="health-heading" className="health-dashboard">
    <header className="health-heading">
      <div>
        <h1 id="health-heading">Health</h1>
        <p>{demo ? "Demo Mode" : "Checks refresh every 20 seconds while this tab is visible."}</p>
      </div>
      {!demo && <Button variant="secondary" onClick={refresh}><Icon name="refresh" width={15} height={15} />Refresh</Button>}
    </header>
    <section className="health-overview" aria-label="Status overview">
      <div className="health-ring">
        <svg viewBox="0 0 100 100" aria-hidden="true">
          {rows.map((row, index) => <circle key={row.id} data-status={row.status} cx="50" cy="50" r="44"
            fill="none" strokeWidth="6" pathLength={rows.length} strokeDasharray={`0.9 ${rows.length - 0.9}`}
            transform={`rotate(${-90 + index * 360 / rows.length} 50 50)`} />)}
        </svg>
        <div><strong>{rows.length}</strong><span>Monitored</span></div>
      </div>
      <dl className="health-counts">
        {statuses.map(status => <div key={status} data-status={status}>
          <dt><i aria-hidden="true" />{status}</dt>
          <dd>{rows.filter(row => row.status === status).length}</dd>
        </div>)}
      </dl>
    </section>
    <h2 className="health-components-heading">Components</h2>
    <ul aria-label="Component health" className="health-grid">
      {rows.map(row => <li key={row.id} className="health-card" data-status={row.status}>
        <div className="health-card-top">
          <span className="health-service-icon"><Icon name={icons[row.id]} /></span>
          <h3>{components[row.id]}</h3>
        </div>
        <span className="health-badge"><i aria-hidden="true" />{row.status}</span>
        <p>{row.reason}</p>
        <dl className="health-check"><dt>Last check</dt><dd>
          {row.checked_at && Number.isFinite(Date.parse(row.checked_at))
            ? <time dateTime={row.checked_at}>{clock.format(new Date(row.checked_at))}</time>
            : "Unavailable"}
        </dd></dl>
      </li>)}
    </ul>
    <ScannerSources report={scanner} failed={scannerFailed} demo={demo} visible={visible} />
  </section>;
}

function ScannerSources({ report, failed, demo, visible }: {
  report: ScannerHealth | null; failed: boolean; demo: boolean; visible: boolean;
}) {
  const worker = report?.sources.find(source => source.name === "Scanner worker");
  const sources = report?.sources.filter(source => source.name !== "Scanner worker") ?? [];
  return <section aria-labelledby="scanner-health-heading">
    <h2 id="scanner-health-heading" className="health-components-heading">Scanner source health</h2>
    {demo ? <p>Live scanner checks are paused in Demo Mode.</p> : <>
      {failed && <p role="status" className="text-app-warning">Scanner health unavailable.{report && " Showing the last report."}</p>}
      {!visible && <p className="text-app-muted">Checks paused while this tab is hidden.</p>}
      {report ? <>
        <p className={report.worker === "Running" ? "text-app-muted" : "text-app-warning"}>
          {failed || !visible ? "Last reported worker state" : "Scanner worker"} · {report.worker}
          {worker && <> · Last worker success {worker.success ? easternTime(worker.success) : "never"}</>}
        </p>
        <dl className="mt-2 divide-y divide-app-border border-y border-app-border">
          {sources.map(source => <div key={source.name}
            className="grid gap-1 py-2 sm:grid-cols-3 sm:gap-3">
            <dt className="font-semibold">{source.name}</dt>
            <dd className={/^(Healthy$|Live\b)/i.test(source.status) ? "text-app-text-soft" : "text-app-warning"}>{source.status}</dd>
            <dd className="text-app-muted">Last success · {source.success ? easternTime(source.success) : "Never"}</dd>
          </div>)}
        </dl>
        {!sources.length && <p>No sources reported.</p>}
        {report.spend && <p className="mt-2 text-sm text-app-muted">AI spend and open reservations · ${(report.spend.micro_usd / 1e6).toFixed(4)} · Cycle starting {report.spend.cycle}</p>}
      </> : !failed && <p role="status">Checking scanner sources…</p>}
    </>}
  </section>;
}
