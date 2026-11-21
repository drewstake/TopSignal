import { useEffect, useState } from "react";
import { requestJson } from "../../lib/api";
import { timeLabel } from "../bot/topBotPresentation";
import { usePageVisibility } from "../bot/usePageVisibility";

type Report = {
  as_of: string;
  depth_recorder: {
    state: string; reason?: string | null; stale?: boolean;
    last_write_at?: string | null; last_totals_write_at?: string | null; rejected_events?: number;
  };
  independent_studies?: { account: number; contract_id: string; state: string; last_raw_at: string | null; last_processed_at: string | null }[];
  problems: { code: string; action: string; count?: number; contract_id?: string }[];
};

function captureState(depth?: Report["depth_recorder"], unknown = false) {
  if (unknown || !depth || depth.stale) return ["Status unknown", "text-app-warning"];
  switch (depth.state) {
    case "recording": return ["Recording", "text-app-positive"];
    case "stopped": case "failed": case "storage_blocked": return ["Capture stopped", "text-app-negative"];
    case "reconnecting": return ["Reconnecting", "text-app-warning"];
    case "starting": case "subscribing": return ["Starting capture", "text-app-muted"];
    default: return ["Status unknown", "text-app-warning"];
  }
}

export function CaptureHealth() {
  const visible = usePageVisibility();
  const [report, setReport] = useState<Report | null>(null);
  const [checkedAt, setCheckedAt] = useState(0);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const next = await requestJson<Report>("/api/recording/health", {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(12_000)]),
        });
        if (!controller.signal.aborted) { setReport(next); setCheckedAt(Date.now()); setFailed(false); }
      } catch { if (!controller.signal.aborted) setFailed(true); }
      finally { if (!controller.signal.aborted) timer = setTimeout(refresh, 20_000); }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [visible]);

  const depth = report?.depth_recorder;
  const outdated = failed || !visible || Date.now() - checkedAt > 45_000;
  const [label, tone] = captureState(depth, outdated);
  const latest = [depth?.last_write_at, depth?.last_totals_write_at]
    .filter((at): at is string => !!at && Number.isFinite(Date.parse(at)))
    .sort((a, b) => Date.parse(b) - Date.parse(a))[0];
  const gaps = depth?.rejected_events;
  const problems = report?.problems ?? [];
  return <section className="glance-panel" aria-label="Independent capture health">
    <dl className="glance-metrics recorder-metrics">
      <div><dt>Independent capture</dt><dd className={tone}>{!report && !failed ? "Checking…" : label}</dd></div>
      <div><dt>Last saved recording</dt><dd>{latest ? timeLabel(latest) : "Unavailable"}</dd></div>
      <div><dt>{report && (outdated || depth?.stale) ? "Last reported gaps" : "Reported capture gaps"}</dt><dd className={gaps ? "text-app-warning" : undefined}>
        {gaps == null ? "Unknown" : gaps ? `${gaps.toLocaleString()} rejected events` : "None reported"}
      </dd></div>
    </dl>
    {report?.independent_studies?.map(s => <p key={s.account} role="status" className={s.state === "study_stalled" ? "glance-warning" : undefined}>
      Independent study · {s.contract_id} · {outdated ? "Status unknown" : s.state.replaceAll("_", " ")}
      {" · Last raw market event "}{s.last_raw_at ? timeLabel(s.last_raw_at) : "unavailable"}
      {" · Last processed "}{s.last_processed_at ? timeLabel(s.last_processed_at) : "unavailable"}
    </p>)}
    {(failed || depth?.stale || depth?.reason) && <p className="glance-warning" role="status">
      {failed ? "Health check failed" : depth?.stale ? "Stale recorder report" : depth?.reason?.replaceAll("_", " ")}
      {report && failed && ` · Last checked ${timeLabel(report.as_of)}`}
    </p>}
    {problems.length > 0 && <details className="capture-problems"><summary className="cursor-pointer text-app-warning">
      {problems.length} recording {problems.length === 1 ? "warning" : "warnings"} · {problems[0].code.replaceAll("_", " ")}
    </summary><ul className="mt-2 space-y-2">{problems.map((p, i) => <li key={`${p.code}:${p.contract_id}:${i}`}>
      <strong>{p.code.replaceAll("_", " ")}</strong>{p.contract_id && ` · ${p.contract_id}`}{p.count != null && ` · ${p.count}`}<br/>{p.action}
    </li>)}</ul></details>}
  </section>;
}
