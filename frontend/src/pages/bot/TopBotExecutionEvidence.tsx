export type ExecutionEvidence = {
  submission?: { request_duration_ms?: number };
  entry_slippage_ticks?: number | null;
  fill_to_observation_ms?: number | null;
  last_stop_request_to_verified_ms?: number;
};
export type ExcursionCoverage = {
  gap_count?: number;
  gap_seconds?: number;
  open_gap?: { reason: string; start: string };
};
export type RecordingEvidence = {
  recording?: { state?: string; dropped_events?: number; last_durable_at?: string | null;
    durable_events?: number; queued_events?: number; inflight_events?: number; phase?: string; phase_elapsed_seconds?: number };
};
const milliseconds = (value: number | null | undefined) =>
  value == null || !Number.isFinite(value) ? "Unknown" : `${value.toFixed(0)} ms`;

export function TopBotRecordingStatus({ evidence }: { evidence?: RecordingEvidence }) {
  const recording = evidence?.recording;
  const state = recording?.state;
  return <p className="topbot-footnote" role={state === "stalled" || state === "error" || state === "storage_blocked" || (recording?.dropped_events ?? 0) > 0 ? "status" : undefined}>
    Worker feed recording: {state === "recording" ? "Active" : state === "stalled" ? "Stalled — durable progress overdue" : state === "starting" ? "Starting" : state === "storage_blocked" ? "Paused for storage review" : state === "error" ? "Unavailable — recording failed" : "Unavailable"}.
    {` Last durable batch: ${recording?.last_durable_at ? new Date(recording.last_durable_at).toLocaleString() : "not recorded"}.`}
    {recording?.durable_events != null ? ` ${recording.durable_events.toLocaleString()} durable events; ${recording.queued_events ?? 0} queued, ${recording.inflight_events ?? 0} in flight.` : ""}
    {state === "stalled" ? ` Phase: ${recording?.phase ?? "unknown"}, ${recording?.phase_elapsed_seconds ?? "unknown"}s.` : ""}
    {recording?.dropped_events ? ` ${recording.dropped_events} capture events missing; replay coverage is incomplete.` : ""}
    {state === "recording" ? " Provider gaps can still occur." : ""}
  </p>;
}

export function TopBotExecutionEvidence({ execution, coverage }: { execution?: ExecutionEvidence; coverage?: ExcursionCoverage }) {
  if (!execution && !coverage) return <span>Not recorded</span>;
  return <details><summary>Execution / coverage</summary><dl>
    <dt>Entry API request</dt><dd>{milliseconds(execution?.submission?.request_duration_ms)}</dd>
    <dt>Entry slippage</dt><dd>{execution?.entry_slippage_ticks == null ? "Unknown" : `${execution.entry_slippage_ticks.toFixed(2)} ticks`}</dd>
    <dt>Fill to worker observation</dt><dd>{milliseconds(execution?.fill_to_observation_ms)}</dd>
    <dt>Last stop request to verification</dt><dd>{milliseconds(execution?.last_stop_request_to_verified_ms)}</dd>
    <dt>Observed coverage gaps</dt><dd>{coverage?.gap_count == null ? "Unknown" : `${coverage.gap_count} · ${(coverage.gap_seconds ?? 0).toFixed(1)}s closed intervals`}
      {coverage?.open_gap ? " · Gap still open" : ""}</dd>
  </dl><small>Positive slippage is adverse. Fill timestamps use the provider clock; clock offset is unverified. Excursions may miss unobserved moves.</small></details>;
}
