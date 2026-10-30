export interface ModelDiagnostics {
  status: "available" | "unavailable" | "not_applicable";
  generated_at?: string; scan_limited?: boolean; contract?: string;
  session?: { status: string; reason: string | null; window: string; next_eligible_close: string | null };
  installed_model?: {
    status: string; reason?: string; artifact?: string; version?: string; payoff_semantics?: string;
    created_at?: string; training_paths?: number; integrity_verified?: boolean;
    refresh?: { status: string; trained_through: string; due_at: string; age_days: number; max_age_days: number; seconds_until_due: number;
      automatic_refit: boolean; automatic_install: boolean; stale_behavior: string };
    training_source?: { status: string; source: string; latest_bar_end?: string; note?: string };
  };
  current_model?: { bound_evaluations: number; forecasts: number; session_eligible_evaluations: number; qualified_signals: number;
    first_forecast_at: string | null; latest_forecast_at: string | null; rejection_counts: Record<string, number> };
  window_totals?: { evaluations: number; session_eligible_evaluations: number; other_model_evaluations: number | null; unbound_evaluations: number; invalid_timestamps: number };
  latest_evaluation?: null | { at: string; close: string; stage: string; reason: string; forecast_expected: boolean;
    simulation_status: string; simulation_codes: string[]; broker_status: string; broker_codes: string[] };
  latest_current_forecast?: null | { at: string; close: string; minimum_net_edge_usd: number; basis: string;
    costs: null | { total_usd: number }; sides: Record<string, { expected_net_usd: number; uncertainty_penalty_usd: number; lower_utility_usd: number; effective_days: number }> };
  attribution?: string;
}
const time = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : "None recorded";
const money = (value: number) => Number.isFinite(value) ? `$${value.toFixed(2)}` : "Unavailable";
const words = (value: string) => value.replaceAll("_", " ");

export function BotModelDiagnostics({ data }: { data: ModelDiagnostics }) {
  if (data.status === "not_applicable") return null;
  if (data.status !== "available") return <p role="alert">Model diagnostics are unavailable. Current-model counts and refresh status could not be verified.</p>;
  const model = data.installed_model;
  const current = data.current_model;
  const refresh = model?.refresh;
  const edge = data.latest_current_forecast;
  const totals = data.window_totals;
  return <section aria-label="Current model diagnostics" className="rounded-xl border border-app-border p-4 space-y-4">
    <div><h4 className="font-semibold">Current model · paper observation</h4>
      <p className="text-xs text-app-muted">{data.contract} · Checked {time(data.generated_at)}. Counts cover the selected results period.</p>
      {data.scan_limited && <p className="text-amber-200 text-sm">Partial coverage: the newest 9,000 records were scanned.</p>}
    </div>
    {data.session && <div className="rounded border border-app-border p-3 text-sm">
      <p className="font-medium">{data.session.status === "open" ? "Entry window open" : "Outside entry hours"}</p>
      <p>{data.session.window}.</p>
      {data.session.status !== "open" && <p>Next eligible candle close: {time(data.session.next_eligible_close)}. Numerical forecasts are not expected outside this window.</p>}
    </div>}
    {model?.status !== "available" ? <p role="alert">Installed model unavailable: {words(model?.reason ?? "status unknown")}. No current-model totals are asserted.</p> : <>
      <p className="text-xs text-app-muted">{model.version} · Model {model.artifact?.slice(0, 12)} · Integrity verified · {model.training_paths?.toLocaleString()} training paths.</p>
      {current && <>
        <dl className="grid grid-cols-2 md:grid-cols-3 gap-3">{Object.entries({ "Current-model forecasts": current.forecasts, "Current-model entry-window evaluations": current.session_eligible_evaluations, "Qualified signals": current.qualified_signals }).map(([name, value]) => <div key={name} className="rounded border border-app-border p-3"><dt className="text-xs text-app-muted">{name}</dt><dd className="text-xl font-semibold">{value}</dd></div>)}</dl>
        {!current.forecasts && <p className="text-sm">No numerical forecast has been recorded for this installed model in the selected period.</p>}
        {!!current.forecasts && <p className="text-xs text-app-muted">Latest current-model forecast: {time(current.latest_forecast_at)}.</p>}
        {Object.entries(current.rejection_counts).map(([reason, count]) => <p className="text-sm" key={reason}>{words(reason)}: {count}</p>)}
      </>}
      {refresh && <div className="rounded border border-app-border p-3 space-y-1" aria-label="Model refresh tracking">
        <p className="font-medium">{refresh.status === "stale" ? "Model refresh overdue" : refresh.status === "due_soon" ? "Model refresh due soon" : "Model training is current"}</p>
        <p className="text-sm">Trained through {time(refresh.trained_through)} · {refresh.age_days.toFixed(2)} days old.</p>
        <p className="text-sm">Refresh due: {time(refresh.due_at)} ({refresh.max_age_days}-day training-age limit).</p>
        {model.training_source && <p className="text-sm">{model.training_source.status === "no_newer_dates" ? "No newer training dates are present in the Databento cache." : model.training_source.status === "newer_dates_available" ? "Newer cached dates are available; complete-session checks are still required." : "Training-cache coverage could not be verified."} {model.training_source.latest_bar_end && `Cached bars through ${time(model.training_source.latest_bar_end)}.`}</p>}
        <p className="text-xs text-app-muted">Tracking only. Refitting and installation are not automatic. {refresh.stale_behavior}</p>
      </div>}
    </>}
    {edge && <div className="overflow-x-auto">
      <p className="text-sm font-medium">Latest current-model entry scores · {time(edge.close)}</p>
      <table className="w-full text-left text-xs"><caption className="sr-only">Current model entry score breakdown</caption><thead><tr><th>Side</th><th>Expected net</th><th>Uncertainty deduction</th><th>Entry score</th><th>Required</th></tr></thead>
        <tbody>{Object.entries(edge.sides).map(([side, value]) => <tr key={side}><th className="py-2">{side}</th><td>{money(value.expected_net_usd)}</td><td>{money(value.uncertainty_penalty_usd)}</td><td>{money(value.lower_utility_usd)}</td><td>Above {money(edge.minimum_net_edge_usd)}</td></tr>)}</tbody>
      </table><p className="text-xs text-app-muted">{edge.basis} {edge.costs && `Assumed round-trip costs: ${money(edge.costs.total_usd)}.`}</p>
    </div>}
    {data.latest_evaluation && <div className="text-sm space-y-1">
      <p>Latest evaluation ({time(data.latest_evaluation.at)}): {data.latest_evaluation.reason}</p>
      <p>Paper eligibility: {words(data.latest_evaluation.simulation_status)} · Broker readiness: {words(data.latest_evaluation.broker_status)}.</p>
      {!data.latest_evaluation.forecast_expected && <p className="text-xs text-app-muted">A missing forecast on this outside-hours evaluation does not mean the installed model is missing.</p>}
    </div>}
    {totals && <p className="text-xs text-app-muted">This contract, all model versions: {totals.session_eligible_evaluations} entry-window evaluations. {totals.other_model_evaluations !== null && `${totals.other_model_evaluations} evaluations belong to other model bindings; `}{totals.unbound_evaluations} have no model identity.</p>}
    <p className="text-xs text-app-muted">{data.attribution}</p>
  </section>;
}
