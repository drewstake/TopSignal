import type { Job } from "./TopBotResearchStatus";

export const ago = (seconds?: number | null) => seconds == null || !Number.isFinite(seconds) ? "unknown"
  : seconds < 90 ? `${Math.round(seconds)}s ago` : seconds < 5400 ? `${Math.round(seconds/60)} min ago`
  : seconds < 172800 ? `${(seconds/3600).toFixed(1)} h ago` : `${(seconds/86400).toFixed(1)} days ago`;
export const span = (seconds?: number | null) => seconds == null ? "—" : seconds < 5400 ? `${Math.round(seconds/60)} min` : `${(seconds/3600).toFixed(1)} h`;

const FAILURES: Record<string, string> = {
  no_progress: "Replay stopped making progress and was ended",
  hard_cap: "Replay exceeded the safety time limit",
  child_exit: "Replay process exited without a result",
  result_store: "Result store unavailable (storage review needed)",
  disk_io: "Disk read error while reading the recording",
  contention: "Recording database busy",
  corrupt_file: "Recording file failed integrity checks",
  disk_full: "Disk full",
  permission_or_read_only: "Recording file not readable",
  storage_review_required: "Storage review required",
  sqlite_operational: "Database error while reading the recording",
  unclassified: "Unclassified replay error",
};
export function failureText(job?: Job) {
  const f = job?.last_failure;
  if (!f) return job?.last_outcome === "paused_recording_pressure" ? "Paused to protect live recording" : null;
  const what = FAILURES[f.category ?? ""] ?? f.category?.replaceAll("_", " ") ?? "Replay failed";
  const detail = [f.code?.replaceAll("_", " "), f.type, f.phase ? `during ${f.phase}` : null].filter(Boolean).join(" · ");
  return detail ? `${what} (${detail})` : what;
}
export const PROBLEMS: Record<string, string> = {
  worker_capture_not_recording: "Worker market recording is not active while observation windows are open.",
  no_recent_durable_commit: "No recording batch has been saved recently; events may be waiting on the disk.",
  retention_failed: "Keeping the market subscription open after exit failed; see the window list.",
  capture_restarted_inside_window: "The recorder restarted inside an observation window. That interval is missing and the entry will be excluded, not filled in.",
};
const STATE_LABELS: Record<string, string> = {
  observing: "Observation window open", queued: "Queued for replay", processing: "Replaying now", retrying: "Retrying after failure",
  failed: "Failed repeatedly", completed_valid: "Valid comparison", completed_excluded: "Excluded (coverage)",
  prior_experiment: "Earlier study version",
};
export const stateLabel = (state: string) => STATE_LABELS[state] ?? state.replaceAll("_", " ");

