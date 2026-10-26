// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { BotCollectionCoverage, type CollectionCoverage } from "./BotCollectionCoverage";
afterEach(cleanup);
const coverage: CollectionCoverage = {
  collection_started_at: "2026-10-22T13:20:34Z", first_recorded_at: "2026-10-22T13:20:34Z",
  last_recorded_at: "2026-10-23T12:20:16Z", started_after_requested_window: true,
  scan_limited: false, complete_evaluation_sessions: 1, missing_due_closes: 0, duplicate_closes: 0,
  legacy_forecasts_without_delayed_entry: 3, volatility_without_scenario: 6,
  first_scenario_at: "2026-10-22T14:20:27Z", comparison_evaluations: 0, sessions: [],
};
it("exposes the observed rollout gaps without presenting elapsed hours as validation", () => {
  render(<BotCollectionCoverage coverage={coverage} />);
  expect(screen.getByText(/Earlier hours are not covered/)).toBeTruthy();
  expect(screen.getByText(/3 earlier forecasts have theoretical candle-open outcomes only/)).toBeTruthy();
  expect(screen.getByText(/without a collected scenario: 6/)).toBeTruthy();
  expect(screen.getByText(/No matched baseline comparison is available/)).toBeTruthy();
  expect(screen.getByText(/Elapsed hours are not trading hours/)).toBeTruthy();
});
it("warns that a truncated scan cannot certify completeness", () => {
  render(<BotCollectionCoverage coverage={{ ...coverage, scan_limited: true, complete_evaluation_sessions: 0 }} />);
  expect(screen.getByRole("alert").textContent).toContain("No session is certified complete");
});
