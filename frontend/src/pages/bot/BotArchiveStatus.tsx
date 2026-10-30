import { useEffect, useState } from "react";

export interface ArchiveStatus {
  enabled: boolean; unavailable?: boolean; last_write?: string | null;
  collector?: { enabled: boolean; authorized: boolean; running: boolean; error?: string | null };
  critical_events?: { backlog?: number; retry_records?: number; archive_retry_batches?: string; status_error?: string;
    quotes?: { backlog?: number; durably_queued?: number; persisted?: number; retried?: number; dropped?: number; uncertain?: number; status_error?: unknown; last_error?: { category?: string; sqlite_code?: number; errno?: number } };
    process?: { memory_backlog?: number; critical_unrecoverable?: number; optional_quotes_received?: number; optional_quotes_queued?: number; optional_quotes_dropped?: number; optional_quotes_memory_retried?: number; optional_quotes_sampled_out?: number; quote_memory_backlog?: number } };
  backup?: { status?: string; stale?: boolean; last_verified_at?: string; last_restore_at?: string; restore_verified?: boolean; last_failure?: { type: string }; destination?: string; schedule?: string };
  bytes?: number; disk_free_bytes?: number;
  errors?: { errors?: number; last_error?: { operation: string; type: string; at: string } };
  collection?: Record<string, { last_success?: string; failed_at?: string; error_type?: string }>;
  event_counts?: Record<string, number>;
  latest_event?: Record<string, string>;
  coverage?: { day: string; contract: string; expected: number; observed: number; missing: number; gaps: { start: string; end: string; minutes: number }[]; attempts: number }[];
}

export function BotArchiveStatus({ data }: { data: ArchiveStatus }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  const coverage = data.coverage ?? [];
  const missing = coverage.reduce((total, row) => total + row.missing, 0);
  const observed = coverage.reduce((total, row) => total + row.observed, 0);
  const stale = !data.last_write || now - Date.parse(data.last_write) > 5 * 60_000;
  const failures = Object.values(data.collection ?? {}).filter(row => row.error_type);
  const candleTime = Object.values(data.collection ?? {}).map(row => row.last_success).filter(Boolean).sort().at(-1);
  return <section aria-label="Local market archive" className="rounded border border-app-border p-4 space-y-2">
    <h4 className="font-semibold">Local market archive</h4>
    {data.unavailable ? <p role="alert">Archive status unavailable.</p> : <>
      <p className="text-sm">{data.enabled ? stale ? "Enabled · writes stale or not yet observed" : "Enabled · recent writes observed" : "Disabled"} · Last write: {data.last_write ? new Date(data.last_write).toLocaleString() : "None"}</p>
      {data.collector && <p className="text-sm">Independent collector: {data.collector.enabled ? data.collector.running ? "running" : "enabled, not observed running" : "disabled"}. {data.collector.error} Last candle collection: {candleTime ? new Date(candleTime).toLocaleString() : "None"}.</p>}
      <p className="text-sm">{observed.toLocaleString()} observed minutes · {missing.toLocaleString()} expected minutes missing in {coverage.length} reported sessions. Stored: {((data.bytes ?? 0) / 1e6).toFixed(1)} MB · Disk free: {((data.disk_free_bytes ?? 0) / 1e9).toFixed(1)} GB.</p>
      <p className="text-xs text-app-muted">Coverage is checked against the exchange calendar to the collection cutoff. Missing minutes remain unresolved; future holiday exceptions need review. Quote samples are not tick history. Broker events are separate from hypothetical results and paper trades.</p>
      <p className="text-sm">BBO samples: {(data.event_counts?.bbo_sample ?? 0).toLocaleString()} · Latest quote receipt: {data.latest_event?.bbo_sample ? new Date(data.latest_event.bbo_sample).toLocaleString() : "Not observed"}.</p>
      {data.critical_events && <p className="text-sm">Critical events: {data.critical_events.backlog ?? "unknown"} durably queued · {data.critical_events.retry_records ?? 0} awaiting retry · {data.critical_events.process?.memory_backlog ?? 0} only in memory · {data.critical_events.process?.critical_unrecoverable ?? 0} unrecoverable this process. {data.critical_events.status_error}</p>}
      {data.critical_events && <div className="text-sm" aria-label="Optional quote persistence">
        <p>Quotes this process: {data.critical_events.process?.optional_quotes_received ?? "unknown"} received · {data.critical_events.process?.optional_quotes_sampled_out ?? 0} sampled out · {data.critical_events.process?.optional_quotes_queued ?? "unknown"} queued · {data.critical_events.process?.quote_memory_backlog ?? 0} in memory · {data.critical_events.process?.optional_quotes_memory_retried ?? 0} memory retries · {data.critical_events.process?.optional_quotes_dropped ?? 0} dropped before durable capture.</p>
        <p>Durable quote spool totals: {data.critical_events.quotes?.persisted ?? "unknown"} persisted · {data.critical_events.quotes?.retried ?? 0} retried · {data.critical_events.quotes?.dropped ?? 0} terminal drops · {data.critical_events.quotes?.uncertain ?? 0} uncertain outcomes · {data.critical_events.quotes?.backlog ?? "unknown"} pending. {data.critical_events.quotes?.last_error?.category} {data.critical_events.quotes?.status_error ? "Quote persistence status unavailable." : ""}</p>
        <p className="text-xs text-app-muted">Process counters reset on restart; durable counters survive. Sampled out quotes were not queued. Uncertain outcomes mean archive presence could not be checked before bounded transport disposal; they are neither confirmed persistence nor confirmed loss. Recording is not lossless.</p>
      </div>}
      {data.backup && <p className="text-sm" role={data.backup.stale || data.backup.status === "failed" ? "alert" : undefined}>Off-device backup: {data.backup.status}{data.backup.stale ? " · stale or never verified" : ""}. Last verified: {data.backup.last_verified_at ? new Date(data.backup.last_verified_at).toLocaleString() : "None"}. Restore check: {data.backup.restore_verified && data.backup.last_restore_at ? new Date(data.backup.last_restore_at).toLocaleString() : "Not verified"}. {data.backup.last_failure?.type}</p>}
      {(data.errors?.errors || failures.length) ? <p role="alert">Recording errors this process: {data.errors?.errors ?? 0}. {data.errors?.last_error?.operation} {data.errors?.last_error?.type} {failures.map(row => row.error_type).join(", ")}</p> : null}
      <details><summary className="cursor-pointer">Session coverage and gaps</summary>
        {coverage.map(row => <div key={`${row.contract}:${row.day}`} className="text-xs py-1">
          <p>{row.day} · {row.contract} · {row.observed}/{row.expected} minutes · recovery attempts: {row.attempts}</p>
          {row.gaps.slice(0, 10).map(gap => <p key={gap.start}>{gap.start}–{gap.end}: {gap.minutes} missing</p>)}
          {row.gaps.length > 10 && <p>More gaps available in the archive status report.</p>}
        </div>)}
      </details>
    </>}
  </section>;
}
