// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { BaselineComparisons, DecisionExplanation, VolatilityWhatIf, type DecisionDetails, type ForwardComparison, type VolatilityCounterfactuals } from "./BotEvaluationDetails";
afterEach(cleanup);
it("shows paper eligibility independently of a hidden broker account during a HOLD", () => {
  const details: DecisionDetails = {
    timing: { candle_close: "2026-10-29T14:00:00Z", recorded_at: "2026-10-29T14:00:20Z", close_to_record_seconds: 20,
      theoretical_entry_at: "2026-10-29T14:05:00Z", simulated_entry_at: "2026-10-29T14:05:00Z", exit_deadline: "2026-10-29T14:15:00Z",
      remaining_horizon_seconds: 600, entry_available: true, quote_status: "not_captured" },
    sides: {}, costs: null, risk_codes: [], stage: "edge_below_threshold", paper_disposition: "edge_below_threshold",
    theoretical_outcomes: null, delayed_outcomes: null, payoff_semantics: "post_record_open_fixed_deadline_v2",
    simulation_eligibility: { status: "eligible", codes: [] }, paper_readiness: { status: "eligible", codes: [] },
    broker_readiness: { status: "blocked", codes: ["account_not_active", "observation_cannot_route"] },
  };
  render(<DecisionExplanation details={details} />);
  expect(screen.getByText(/Shared simulation gates: eligible/)).toBeTruthy();
  expect(screen.getByText(/Paper account readiness: eligible/)).toBeTruthy();
  expect(screen.getByText(/Broker readiness: blocked.*account not active/)).toBeTruthy();
  expect(screen.getByText(/Matched entry and remaining exposure/)).toBeTruthy();
  cleanup();
  render(<DecisionExplanation details={{ ...details, recording: { status: "unknown" },
    paper_readiness: { status: "unknown", codes: ["simulation_recording_unconfirmed"] } }} />);
  expect(screen.getByRole("alert").textContent).toContain("timestamp is provisional");
  expect(screen.getByRole("alert").textContent).toContain("cannot be replayed as a fill");
});
it("keeps rejected wider-stop outcomes separate and never renders missing results as zero", () => {
  const data: VolatilityCounterfactuals = { policy: "v1", recorded: 2, completed: 1, pending: 0, unknown: 1, overdue: 0,
    recent: [{ at: "2026-10-22T14:00:20Z", status: "observed", entry_at: "2026-10-22T14:05:00Z", deadline: "2026-10-22T14:15:00Z",
      stop_points: 34.75, target_points: 52.25, risk_cap_points: 25, quantity: 1, costs: { total_usd: 3.22 },
      outcomes: { BUY: { net_usd: -71.72, entry: 100, entry_at: "", exit_at: "", exit_kind: "stop" }, SELL: { net_usd: 10, entry: 100, entry_at: "", exit_at: "", exit_kind: "time" } } },
      { at: "2026-10-22T14:05:20Z", status: "unknown_missing_bars", entry_at: "2026-10-22T14:10:00Z", deadline: "2026-10-22T14:20:00Z",
        stop_points: 34.75, target_points: 52.25, risk_cap_points: 25, quantity: 1, costs: { total_usd: 3.22 }, outcomes: null }] };
  render(<VolatilityWhatIf data={data} />);
  fireEvent.click(screen.getByText(/Recent blocked scenarios/));
  expect(screen.getByText(/25-point cap in effect when recorded/)).toBeTruthy();
  expect(screen.getByRole("cell", { name: /71\.72/ })).toBeTruthy();
  expect(screen.getAllByText("Unknown")).toHaveLength(2);
  expect(screen.queryByText("$0.00")).toBeNull();
  expect(screen.queryByRole("button")).toBeNull();
});
it("shows separate full-period BUY and SELL P&L without summing the recent subset", () => {
  const data: VolatilityCounterfactuals = { policy: "v1", recorded: 38, completed: 35, pending: 2, unknown: 1, overdue: 0, recent: [],
    sides: { BUY: { completed: 35, unavailable: 0, net_usd: 145.25, mean_net_usd: 4.15, positive: 20, negative: 15, breakeven: 0 },
      SELL: { completed: 35, unavailable: 0, net_usd: -210.5, mean_net_usd: -6.0143, positive: 10, negative: 24, breakeven: 1 } } };
  render(<VolatilityWhatIf data={data} />);
  const buy = within(screen.getByRole("region", { name: "Hypothetical BUY P&L" }));
  const sell = within(screen.getByRole("region", { name: "Hypothetical SELL P&L" }));
  expect(buy.getByText("$145.25")).toBeTruthy();
  expect(sell.getByText("−$210.50")).toBeTruthy();
  expect(buy.getByText(/35 completed scenarios/)).toBeTruthy();
  expect(screen.getByText(/Pending and unknown outcomes are excluded/)).toBeTruthy();
  expect(screen.getByText(/should not be combined into account P&L/)).toBeTruthy();
});
it("does not turn pending or unavailable side totals into zero P&L", () => {
  const data: VolatilityCounterfactuals = { policy: "v1", recorded: 2, completed: 0, pending: 2, unknown: 0, overdue: 0, recent: [],
    sides: { BUY: { completed: 0, unavailable: 0, net_usd: null, mean_net_usd: null, positive: 0, negative: 0, breakeven: 0 },
      SELL: { completed: 0, unavailable: 1, net_usd: null, mean_net_usd: null, positive: 0, negative: 0, breakeven: 0 } } };
  render(<VolatilityWhatIf data={data} />);
  expect(screen.queryByText("$0.00")).toBeNull();
  expect(screen.getAllByText(/Awaiting completed results/)).toHaveLength(2);
  expect(screen.getByText(/1 completed outcomes unavailable/)).toBeTruthy();
});
it("distinguishes recording latency from an executable entry and explains a HOLD", () => {
  const details: DecisionDetails = { timing: { candle_close: "2026-10-22T13:35:00Z", recorded_at: "2026-10-22T13:35:26Z", close_to_record_seconds: 26,
    theoretical_entry_at: "2026-10-22T13:35:00Z", simulated_entry_at: "2026-10-22T13:40:00Z", exit_deadline: "2026-10-22T13:50:00Z", remaining_horizon_seconds: 600, entry_available: true, evaluation_duration_ms: 1500, quote_status: "not_captured",
    execution_commit_ms: 200, stages: { collected_context: { duration_ms: 800, self_ms: 300, calls: 1, failures: 0, db_queries: 2, db_query_ms: 120, db_connects: 0, db_connect_ms: 0 } } },
    sides: { BUY: { expected_net_usd: 3, uncertainty_penalty_usd: 4, lower_utility_usd: -1, threshold_usd: 1, margin_usd: -2, effective_days: 25, training_paths: 500, blocks: ["net_edge_not_above_1_usd"] } },
    costs: null, risk_codes: [], stage: "edge_below_threshold", paper_disposition: "edge_below_threshold", theoretical_outcomes: null, delayed_outcomes: null };
  render(<DecisionExplanation details={details} />);
  expect(screen.getByText(/26.0s later/)).toBeTruthy();
  expect(screen.getByText(/10m available/)).toBeTruthy();
  expect(screen.getByText("−$2.00")).toBeTruthy();
  expect(screen.getByText(/OHLC assumptions, not measured fills/)).toBeTruthy();
  expect(screen.getByText(/Shared simulation gates: unknown/)).toBeTruthy();
  expect(screen.getByText(/Broker readiness: unknown/)).toBeTruthy();
  expect(screen.getByText("net edge not above 1 usd")).toBeTruthy();
  expect(screen.getByText("Evaluation stage timings")).toBeTruthy();
  expect(screen.getByText("0.800s")).toBeTruthy();
  expect(screen.getByText(/Final execution commit: 0.200s/)).toBeTruthy();
});
it("shows unavailable uncertainty and incomplete sessions instead of a confidence claim", () => {
  const comparison: ForwardComparison = { policy: "v1", minimum_sessions: 20, cohorts: [{ id: "a", artifact: "model", started_at: "2026-10-22T14:00:00Z", complete_sessions: 0,
    accounts: { topbot: { net_usd: 0, trades: 0, position: null, blocked_reason: null, closed_drawdown_usd: 0 } },
    sessions: [{ day: "2026-10-22", expected: 72, recorded: 2, missing: 70, unfinished: true, quality_failures: 0, usable: false }],
    comparisons: [{ rule: "flat", mean_daily_advantage_usd: null, status: "insufficient_complete_sessions", interval: null }], dispositions: { topbot: { edge_below_threshold: 2 } } }] };
  render(<BaselineComparisons comparison={comparison} />);
  expect(screen.getByText(/0\/20 complete sessions/)).toBeTruthy();
  expect(screen.getByText("insufficient complete sessions")).toBeTruthy();
  expect(screen.getByText(/session in progress/)).toBeTruthy();
  expect(screen.getByText(/not validation or protection against repeated peeking/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: /trade|start|buy/ })).toBeNull();
});
