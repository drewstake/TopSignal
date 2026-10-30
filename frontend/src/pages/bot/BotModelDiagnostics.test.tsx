// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { BotModelDiagnostics, type ModelDiagnostics } from "./BotModelDiagnostics";
afterEach(cleanup);
const fixture = (): ModelDiagnostics => ({status:"available", session:{status:"outside_entry_window",reason:"outside_research_session",window:"09:35–15:30 America/New_York",next_eligible_close:"2026-10-30T13:35:00Z"},
  installed_model:{status:"available",artifact:"current",version:"bayesian_cells_v1",integrity_verified:true,training_paths:4096,
    refresh:{status:"due_soon",trained_through:"2026-10-23T19:45:00Z",due_at:"2026-10-30T19:45:00Z",age_days:6.6,max_age_days:7,seconds_until_due:30000,automatic_refit:false,automatic_install:false,stale_behavior:"Observation continues with a stale-training warning."},training_source:{status:"no_newer_dates",source:"Databento filesystem cache"}},
  current_model:{bound_evaluations:0,forecasts:0,session_eligible_evaluations:0,qualified_signals:0,first_forecast_at:null,latest_forecast_at:null,rejection_counts:{}},window_totals:{evaluations:100,session_eligible_evaluations:70,other_model_evaluations:70,unbound_evaluations:30,invalid_timestamps:0}});
it("separates new-model counts, outside-hours holds and refresh due",()=>{
  render(<BotModelDiagnostics data={fixture()} />);
  expect(screen.getByText("Outside entry hours")).toBeTruthy();
  expect(screen.getByText("Model refresh due soon")).toBeTruthy();
  expect(screen.getByText(/No numerical forecast has been recorded for this installed model/)).toBeTruthy();
  expect(screen.getByText(/70 evaluations belong to other model bindings/)).toBeTruthy();
  expect(screen.getByText(/No newer training dates/)).toBeTruthy();
  expect(screen.getByText(/Refitting and installation are not automatic/)).toBeTruthy();
});
it("shows overdue training without implying an automatic replacement",()=>{
  const data=fixture();data.installed_model!.refresh!.status="stale";
  render(<BotModelDiagnostics data={data} />);
  expect(screen.getByText("Model refresh overdue")).toBeTruthy();
  expect(screen.queryByRole("button")).toBeNull();
});
it("does not assert zero current forecasts when model integrity is unavailable",()=>{
  const data=fixture();data.installed_model={status:"unavailable",reason:"model_integrity_or_scope_failed"};
  render(<BotModelDiagnostics data={data} />);
  expect(screen.getByRole("alert")).toBeTruthy();
  expect(screen.queryByText("Current-model forecasts")).toBeNull();
});
it("shows expected net minus uncertainty separately from the strict threshold",()=>{
  const data=fixture();data.latest_current_forecast={at:"2026-10-30T14:00:20Z",close:"2026-10-30T14:00:00Z",minimum_net_edge_usd:1,basis:"Net already includes costs.",costs:{total_usd:3.22},sides:{BUY:{expected_net_usd:2,uncertainty_penalty_usd:5,lower_utility_usd:-3,effective_days:100}}};
  render(<BotModelDiagnostics data={data} />);
  expect(screen.getByText("$2.00")).toBeTruthy();expect(screen.getByText("$5.00")).toBeTruthy();expect(screen.getByText("$-3.00")).toBeTruthy();expect(screen.getByText("Above $1.00")).toBeTruthy();
});
