// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { BotDepthResearch } from "../../lib/types";
import { BotDepthResearchPanel } from "./BotDepthResearchPanel";

afterEach(cleanup);
const missing: BotDepthResearch = {
  model_version: "depth_logistic_ridge_v1", horizon_seconds: 900, action: "NO_TRADE", research_action: "NO_TRADE",
  routing_allowed: false, probability_basis: "unavailable", feed_capability: "level1", synchronized: false,
  book_status: "unverified_snapshot_boundary", age_seconds: null, bid_levels: 0, ask_levels: 0,
  forecasts: null, depth_contribution: null, trained_through: null, estimated_round_trip_cost_usd: null,
  cost_basis: "Actual fills unverified.", uncertainty_method: "Unavailable.", reasons: ["No synchronized depth book."],
};
describe("Level 2 research explanation", () => {
  it("distinguishes Level 1 observations from Level 2 entitlement and refuses neutral imputation", () => {
    render(<BotDepthResearchPanel depth={missing} />);
    expect(screen.getByText(/Feed: Level 1 observed/)).toBeTruthy();
    expect(screen.getByText(/Depth probabilities, payoff and contribution are unavailable/)).toBeTruthy();
    expect(screen.getByText(/Level 2 research · NO TRADE/)).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });
  it("shows experimental probability, uncertainty, contribution and blocked routing separately", () => {
    const estimate = { probability_net_positive: .8, expected_net_usd: -2, uncertainty_penalty_usd: 4,
      lower_utility_usd: -6, execution_cost_proxy_usd: .5 };
    render(<BotDepthResearchPanel depth={{ ...missing, feed_capability: "verified_level2", synchronized: true,
      probability_basis: "uncalibrated_model_estimate", age_seconds: .5, forecasts: { BUY: estimate, SELL: estimate },
      depth_contribution: { BUY: { probability_difference: .1, expected_net_difference_usd: 1 },
        SELL: { probability_difference: -.1, expected_net_difference_usd: -1 } }, research_action: "BUY" }} />);
    expect(screen.getByText(/Uncalibrated probability estimates/)).toBeTruthy();
    expect(screen.getAllByText("$-2.00")).toHaveLength(2);
    expect(screen.getByText("Net change vs Level 1")).toBeTruthy();
    expect(screen.getByText(/Shadow proposal: BUY/)).toBeTruthy();
    expect(screen.getByText(/not a causal effect or proven improvement/)).toBeTruthy();
  });
});
