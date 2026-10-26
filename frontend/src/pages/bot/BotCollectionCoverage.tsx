export interface CollectionCoverage {
  collection_started_at: string | null;
  first_recorded_at: string | null;
  last_recorded_at: string | null;
  started_after_requested_window: boolean;
  scan_limited: boolean;
  complete_evaluation_sessions: number;
  missing_due_closes: number;
  duplicate_closes: number;
  legacy_forecasts_without_delayed_entry: number;
  volatility_without_scenario: number;
  first_scenario_at: string | null;
  comparison_evaluations: number;
  sessions: { day: string; scheduled: number; due: number; recorded: number; missing: number;
    quality_failures: number; duplicates: number; partial_window: boolean; unfinished: boolean; complete: boolean }[];
}

const when = (value: string | null) => value ? new Date(value).toLocaleString() : "None recorded";

export function BotCollectionCoverage({ coverage: c }: { coverage: CollectionCoverage }) {
  return <section aria-label="Evidence coverage" className="rounded border border-app-border p-4 space-y-2 text-sm">
    <h4 className="font-semibold">Evidence coverage</h4>
    <p>Collection began: {when(c.collection_started_at)}. Selected records: {when(c.first_recorded_at)} through {when(c.last_recorded_at)}.</p>
    {c.started_after_requested_window && <p>Collection began after the selected period started. Earlier hours are not covered by this ledger.</p>}
    <p>Complete evaluation sessions: {c.complete_evaluation_sessions} · Missing due closes: {c.missing_due_closes} · Duplicate closes: {c.duplicate_closes}</p>
    <p className="text-xs text-app-muted">Session coverage counts scheduled research-window closes, including HOLDs, with a two-minute recording allowance. Elapsed hours are not trading hours. Complete evaluation coverage does not establish complete outcomes, independent samples or validation.</p>
    {c.scan_limited && <p role="alert">Coverage scan is truncated; missing counts may include records outside the scan. No session is certified complete.</p>}
    {!!c.legacy_forecasts_without_delayed_entry && <p>{c.legacy_forecasts_without_delayed_entry} earlier forecasts have theoretical candle-open outcomes only; entry after recording was not collected.</p>}
    <p>First wider-stop scenario in this period: {when(c.first_scenario_at)}. Volatility HOLDs without a collected scenario: {c.volatility_without_scenario}. Earlier scenarios are not backfilled.</p>
    {!c.comparison_evaluations && <p>No evaluations in this period are linked to a baseline cohort. No matched baseline comparison is available for these evaluations.</p>}
    {!!c.sessions.length && <details><summary className="cursor-pointer">Session coverage details</summary>
      <div className="overflow-x-auto"><table className="w-full text-xs text-left">
        <thead><tr><th>Session (Eastern)</th><th>Recorded / due</th><th>Missing</th><th>Input failures</th><th>Coverage</th></tr></thead>
        <tbody>{c.sessions.map(s => <tr key={s.day}><td>{s.day}</td><td>{s.recorded} / {s.due}</td><td>{s.missing}</td><td>{s.quality_failures}</td><td>{s.complete ? "Complete evaluations" : [s.partial_window && "Partial window", s.unfinished && "In progress", (s.missing > 0 || s.quality_failures > 0) && "Evidence gaps", s.duplicates > 0 && "Duplicate records", c.scan_limited && "Truncated"].filter(Boolean).join(" · ")}</td></tr>)}</tbody>
      </table></div>
    </details>}
  </section>;
}
