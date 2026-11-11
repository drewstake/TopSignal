import { useEffect, useState } from "react";
import { botsApi } from "../../lib/api";
import { usePageVisibility } from "./usePageVisibility";
import { ago, failureText, PROBLEMS, span, stateLabel } from "./researchStatusFormat";

type Failure = { category?: string; type?: string; code?: string; phase?: string; exit_code?: number; limit_seconds?: number } | null;
export type Job = { attempts?: number; failures?: number; consecutive_failures?: number; next_attempt_at?: string | null;
  last_attempt_at?: string | null; last_outcome?: string | null; last_failure?: Failure };
export type ResearchEntry = { entry_id: number; state: string; entry_at?: string | null; exit_at?: string | null;
  exit_reason?: string | null; cutoff?: string | null; overdue_seconds?: number; reasons?: string[]; job?: Job;
  progress?: { phase?: string; events?: number; received_at?: string };
  window_seconds_remaining?: number };
type Fraction = { numerator: number; denominator: number; rate: number | null; label: string };
export type Readiness = {
  experiment_id: string; registered_filled_entries: number; pending: number; processed: number; deferred: number;
  counts: Record<"observing" | "queued" | "processing" | "retrying" | "failed" | "completed_valid" | "completed_excluded", number>;
  processed_coverage: Fraction; overall_usable: Fraction;
  oldest_overdue: { entry_id: number; cutoff: string; overdue_seconds: number; state: string } | null;
  unfilled_submissions: { entry_id: number; kind: string; label: string }[]; not_trades: Record<string, number>;
  prior_experiment_entries: number;
};
export type ResearchStatus = {
  account_id: number; as_of: string;
  position_recording: { enrolled: boolean | null; healthy: boolean; error: string | null; heartbeat_at?: string | null;
    age_seconds?: number | null; stale: boolean; scope: string };
  post_exit_recording: { problems: string[]; scope: string;
    capture: { state: string; durable_age_seconds?: number | null; durable_progress?: string; dropped_events?: number;
      last_drop?: { received_at?: string } | null; uncommitted_events?: number; last_commit_completed_at?: string | null; stale?: boolean;
      retain_until?: string | null };
    open_windows: { entry_id: number; exit_at?: string | null; exit_reason?: string | null; cutoff: string; seconds_remaining: number;
      position_closed: boolean; retention: { state: string; at?: string; error_type?: string }; capture_matches_entry: boolean | null }[] };
  analysis: { reader: { state?: string; last_cycle_at?: string | null; last_error_type?: string | null; last_error_at?: string | null;
      pressure?: { reason?: string; at?: string } | null; checkpoint_reader?: { available: boolean; reason: string | null } };
    active: { entry_id: number; phase?: string; events?: number; received_at?: string; started_at?: string } | null;
    readiness: Readiness; entries: ResearchEntry[] };
  backup: { state: string; status?: string; verified: boolean; stale?: boolean; last_verified_at?: string | null;
    last_attempt_at?: string | null; age_seconds?: number | null; last_failure?: { type?: string; at?: string } | null };
};

const POLL_MS = 20_000;
const percent = (f: Fraction) => f.rate == null ? "—" : `${(f.rate*100).toFixed(0)}%`;
const clock = (value?: string | null) => value ? new Date(value).toLocaleString("en-US", {
  timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" }) + " ET" : "never";
export function TopBotResearchStatus({ accountId }: { accountId: number }) {
  const visible = usePageVisibility();
  const [data, setData] = useState<{ value: ResearchStatus; at: number } | null>(null);
  const [error, setError] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!visible) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController | null = null;
    const refresh = async () => {
      controller = new AbortController();
      try {
        const value = await botsApi.getTopBotResearchStatus(accountId, { signal: controller.signal });
        if (value.account_id !== accountId) throw new Error("Account mismatch");
        if (!stopped) { setData({ value, at: Date.now() }); setError(false); }
      } catch { if (!stopped) setError(true); }
      finally { if (!stopped) { setNow(Date.now()); timer = setTimeout(refresh, POLL_MS); } }
    };
    void refresh();
    return () => { stopped = true; clearTimeout(timer); controller?.abort(); };
  }, [accountId, visible]);
  const value = data?.value;
  const stale = error || (data != null && now-data.at > POLL_MS*2);
  return <section className="topbot-panel topbot-research-status" aria-label="Recording and research status">
    <h3>Recording &amp; research status</h3>
    {stale && <p role="status" className="topbot-warning">{data ? `Status could not be refreshed. Showing data from ${clock(value?.as_of)}; it may be out of date.` : "Status unavailable."}</p>}
    {!value ? (!error && <p className="topbot-muted">Checking recorders and research…</p>) : <div className="topbot-status-grid">
      <PositionRecording value={value} />
      <PostExitRecording value={value} />
      <AnalysisProgress value={value} />
      <BackupHealth value={value} />
    </div>}
    {value && <p className="topbot-footnote">Updated {clock(value.as_of)} · refreshes every 20 s while this page is visible. Each section is checked separately; a healthy section does not imply the others are healthy.</p>}
  </section>;
}

function PositionRecording({ value }: { value: ResearchStatus }) {
  const p = value.position_recording;
  const label = p.enrolled === false ? "Not enrolled" : p.error ? `Error · ${p.error.replaceAll("_", " ")}`
    : p.stale ? "Stale · no recent recorder check" : p.healthy ? "Recording" : "Enrolled · coverage unverified";
  return <article aria-label="Position recording">
    <h4>Position recording</h4>
    <p className={p.healthy && !p.stale ? "topbot-ok" : "topbot-warning"}>{label}</p>
    <p className="topbot-muted">Last recorder check {clock(p.heartbeat_at)} ({ago(p.age_seconds)}).</p>
    <p className="topbot-footnote">{p.scope}.</p>
  </article>;
}

function PostExitRecording({ value }: { value: ResearchStatus }) {
  const { capture, open_windows: windows, problems } = value.post_exit_recording;
  const healthy = !problems.length && capture.state === "recording" && !capture.stale;
  return <article aria-label="Post-exit recording">
    <h4>Post-exit recording</h4>
    <p className={healthy ? "topbot-ok" : windows.length ? "topbot-warning" : "topbot-muted"}>
      {windows.length ? `${windows.length} observation window${windows.length === 1 ? "" : "s"} open` : "No observation windows open"}
      {" · "}{capture.state === "recording" ? "recording" : capture.state.replaceAll("_", " ")}</p>
    <p className="topbot-muted">Last durable save {ago(capture.durable_age_seconds)}
      {capture.durable_progress ? ` · ${capture.durable_progress.replaceAll("_", " ")}` : ""}
      {capture.uncommitted_events ? ` · ${capture.uncommitted_events.toLocaleString()} not yet saved` : ""}
      {capture.dropped_events ? ` · ${capture.dropped_events.toLocaleString()} events lost this capture (last ${clock(capture.last_drop?.received_at)})` : ""}.</p>
    {problems.map(code => <p key={code} role="alert" className="topbot-warning">{PROBLEMS[code] ?? code}</p>)}
    {windows.length > 0 && <ul className="topbot-status-list">{windows.map(w => <li key={w.entry_id}>
      Entry {w.entry_id}{w.position_closed ? ` · exited ${w.exit_reason?.replaceAll("_", " ") ?? ""}` : " · position open"} · window closes {clock(w.cutoff)} ({span(w.seconds_remaining)} left)
      {" · "}{w.retention.state === "retained" ? "subscription retained" : w.retention.state === "failed" ? `retention failed (${w.retention.error_type})` : "retention not yet checked"}
      {w.capture_matches_entry === false ? " · recorder restarted since entry (gap)" : ""}
    </li>)}</ul>}
    <p className="topbot-footnote">{value.post_exit_recording.scope}. Lost intervals stay missing; they are never filled from candles.</p>
  </article>;
}

function AnalysisProgress({ value }: { value: ResearchStatus }) {
  const { readiness: r, reader, active, entries } = value.analysis;
  const c = r.counts;
  const attention = entries.filter(e => e.state === "retrying" || e.state === "failed");
  return <article aria-label="Analysis progress">
    <h4>Analysis progress</h4>
    <p>{r.registered_filled_entries} registered filled entries: {c.completed_valid} valid · {c.completed_excluded} excluded · {r.pending} pending</p>
    <dl className="topbot-status-counts">
      <div><dt>Window open</dt><dd>{c.observing}</dd></div><div><dt>Queued</dt><dd>{c.queued}</dd></div>
      <div><dt>Replaying</dt><dd>{c.processing}</dd></div><div><dt>Retrying</dt><dd>{c.retrying}</dd></div>
      <div><dt>Failed</dt><dd>{c.failed}</dd></div>
    </dl>
    <p className="topbot-muted">{r.processed_coverage.label}: {r.processed_coverage.numerator}/{r.processed_coverage.denominator} ({percent(r.processed_coverage)}) ·{" "}
      {r.overall_usable.label}: {r.overall_usable.numerator}/{r.overall_usable.denominator} ({percent(r.overall_usable)})</p>
    <p className={r.oldest_overdue ? "topbot-warning" : "topbot-muted"}>{r.oldest_overdue
      ? `Oldest overdue: entry ${r.oldest_overdue.entry_id}, ${span(r.oldest_overdue.overdue_seconds)} past its window (${stateLabel(r.oldest_overdue.state).toLowerCase()}).`
      : "Nothing overdue."} Deferred on this account: {r.deferred}.</p>
    {active && <p className="topbot-muted">Replaying entry {active.entry_id} · {active.phase ?? "starting"}{active.events != null ? ` · ${active.events.toLocaleString()} events` : ""}{active.received_at ? ` · reached ${clock(active.received_at)}` : ""}.</p>}
    <p className="topbot-muted">Reader: {reader.state?.replaceAll("_", " ") ?? "unknown"} · last scan {clock(reader.last_cycle_at)}
      {reader.pressure?.at ? ` · last paused for recording pressure ${clock(reader.pressure.at)}` : ""}
      {reader.checkpoint_reader && !reader.checkpoint_reader.available ? " · checkpoints disabled (full scans)" : ""}.</p>
    {attention.length > 0 && <ul className="topbot-status-list" aria-label="Replays needing attention">{attention.slice(0, 8).map(e => <li key={e.entry_id}>
      Entry {e.entry_id} · {stateLabel(e.state)} · {e.job?.attempts ?? 0} attempts · {failureText(e.job) ?? "no failure detail"}
      {e.job?.next_attempt_at ? ` · next try ${clock(e.job.next_attempt_at)}` : ""}</li>)}</ul>}
    {(r.unfilled_submissions.length > 0 || Object.keys(r.not_trades).length > 0) && <p className="topbot-footnote">
      {r.unfilled_submissions.length ? `${r.unfilled_submissions.length} submission${r.unfilled_submissions.length === 1 ? "" : "s"} awaiting fill confirmation. ` : ""}
      {Object.entries(r.not_trades).map(([k, n]) => `${n} ${k.replaceAll("_", " ")}`).join(" · ")}{Object.keys(r.not_trades).length ? " attempts never filled and are not trades." : ""}</p>}
    {r.prior_experiment_entries > 0 && <p className="topbot-footnote">{r.prior_experiment_entries} entries belong to an earlier study version and are not pooled.</p>}
  </article>;
}

function BackupHealth({ value }: { value: ResearchStatus }) {
  const b = value.backup;
  const label = b.verified ? "Verified" : b.state === "failed_stale" ? "Failed · stale, not verified" : "Stale · not verified";
  return <article aria-label="Backup health">
    <h4>Off-device backup</h4>
    <p className={b.verified ? "topbot-ok" : "topbot-warning"} role={b.verified ? undefined : "status"}>{label}</p>
    <p className="topbot-muted">Last verified {clock(b.last_verified_at)} ({ago(b.age_seconds)}) · last attempt {clock(b.last_attempt_at)}
      {b.last_failure?.type ? ` · failed with ${b.last_failure.type}` : ""}.</p>
    <p className="topbot-footnote">Stays unverified until a backup completes and its copy is checked. Recordings made since the last verified backup exist only on the recording computer.</p>
  </article>;
}
