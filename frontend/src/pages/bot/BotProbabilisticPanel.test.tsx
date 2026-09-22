// @vitest-environment jsdom
import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { BotProbabilisticResearch, BotResearchAction } from "../../lib/types";
import { BotProbabilisticPanel } from "./BotProbabilisticPanel";

afterEach(cleanup);
const missing: BotProbabilisticResearch = {
  interface_version: "mnq-probabilistic-v1", model_version: "bayesian_cells_v1", validation_status: "unvalidated",
  action: "NO_TRADE", research_action: "NO_TRADE", routing_allowed: false, probability_basis: "unavailable",
  horizon_minutes: 15, evaluated_at: "2026-09-08T14:00:00Z", candle_close_timestamp: "2026-09-08T14:00:00Z",
  age_seconds: 0, data_status: "fresh", model_trained_through: null, stop_points: null, target_points: null,
  quantity: 1, costs: { fees_usd: 1.22, spread_usd: .5, slippage_usd: 1, latency_usd: .5, rounding_usd: 0,
    total_usd: 3.22, basis: "Assumed costs; spread is not measured." }, forecasts: null,
  no_trade_expected_net_usd: 0, minimum_net_edge_usd: 1, training_paths: 0,
  uncertainty_method: "Unavailable without fitted data.", reasons: ["No scoped research model artifact."],
};
const estimate: BotResearchAction = { probability_net_positive: .8, probability_net_nonpositive: .2,
  probability_stop: .2, probability_time_exit: .3, probability_target: .5, expected_net_usd: -2,
  standard_error_usd: 3, uncertainty_penalty_usd: 8, lower_utility_usd: -10, effective_days: 25 };

describe("probabilistic Dry Run explanation", () => {
  it("shows missing probabilities as unavailable, never neutral or zero", () => {
    render(<BotProbabilisticPanel research={missing} />);
    expect(screen.getByText(/probabilities, expected payoff and model uncertainty are unavailable/)).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByText(/NO TRADE · validation incomplete/)).toBeTruthy();
    expect(screen.getByText(/Assumed round-trip costs: \$3.22/)).toBeTruthy();
    expect(screen.getByText(/15-minute horizon · bayesian_cells_v1/)).toBeTruthy();
  });
  it("distinguishes uncalibrated probabilities, expected payoff and blocked routing", () => {
    render(<BotProbabilisticPanel research={{ ...missing, probability_basis: "uncalibrated_model_estimate",
      forecasts: { BUY: estimate, SELL: { ...estimate, probability_net_positive: .2 } }, research_action: "BUY" }} />);
    expect(screen.getByText(/Uncalibrated model probabilities/)).toBeTruthy();
    expect(screen.getAllByText("$-2.00")).toHaveLength(2);
    expect(screen.getByText("80.0%")).toBeTruthy();
    expect(screen.getByText(/Shadow proposal: BUY. Routing remains disabled/)).toBeTruthy();
    expect(screen.getByText(/NO TRADE · validation incomplete/)).toBeTruthy();
    expect(screen.getByText(/age at evaluation 0s/)).toBeTruthy();
  });
});
