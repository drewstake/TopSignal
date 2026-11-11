// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { botsApi } from "../../lib/api";
import { TopBotComparison, type Comparison } from "./TopBotComparison";

const empty = { count: 0, sessions: 0, wins: 0, losses: 0, average_win: null, average_loss: null,
  expectancy: null, profit_factor: null, interval_95: null };
const data: Comparison = { experiment_id: "study", versions: ["saved-version"], actual: ["fixed", "trailing", "unknown"].map(strategy_class => ({ strategy_class, dollars: empty, r: empty, incomplete: 0 })),
  series: [1, 1.25, 1.5, 1.75, 2, 2.25, 2.5, 2.75, 3, null].map(ratio => ({ key: String(ratio ?? "trailing"), ratio, dollars: empty, r: empty })),
  valid_entries: 0, total_entries: 2, excluded_entries: 2, pending_entries: 1, sessions: 0, progress_to_100: 0,
  coverage_rate: 0, historical_unregistered: 3, excluded: [{ entry_id: 4, reasons: ["capture_index_gap"] }],
  actual_scope: "Account only", actual_truncated: false, selection: null, candidate_minus_trailing: { dollars: empty, r: empty },
  protocol: { limitations: ["Coverage bias"], fill_model: "Executable quotes", cost_model: "Costs included", clock_assumption: "Clock unverified", cutoff: "Session cutoff" } };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("separates actual strategies, shows missing coverage and all nine ratios without inventing results", async () => {
  vi.spyOn(botsApi, "getTopBotComparison").mockResolvedValue(data);
  const order = vi.spyOn(botsApi, "startTopBot");
  render(<TopBotComparison accountId={123} />);
  await screen.findByText("Actual broker executions");
  expect(screen.getByText("No valid paired outcomes yet")).toBeTruthy();
  expect(screen.getByText(/0 valid shared entries.*2 excluded.*1 pending/)).toBeTruthy();
  expect(screen.getByText("3:1 · 75 pts")).toBeTruthy();
  expect(screen.getByText(/Entry 4: capture_index_gap/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Actual entry strategy"), { target: { value: "fixed" } });
  await waitFor(() => expect(botsApi.getTopBotComparison).toHaveBeenLastCalledWith(123, expect.objectContaining({ strategy: "fixed" }), expect.anything()));
  expect(order).not.toHaveBeenCalled();
});

it("only registers a candidate on explicit click and does not route orders", async () => {
  vi.spyOn(botsApi, "getTopBotComparison").mockResolvedValue(data);
  const register = vi.spyOn(botsApi, "registerTopBotCandidate").mockResolvedValue({ ratio: 2, registered_at: "2026-11-04T17:00:00Z" });
  const order = vi.spyOn(botsApi, "startTopBot");
  render(<TopBotComparison accountId={123} />);
  await screen.findByLabelText("Research candidate");
  expect(register).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Research candidate"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "Register candidate for future validation" }));
  await waitFor(() => expect(register).toHaveBeenCalledWith(123, 2));
  expect(order).not.toHaveBeenCalled();
});

it("cancels previous account requests and does not display a late response", async () => {
  let resolveOld!: (value: Comparison) => void;
  vi.spyOn(botsApi, "getTopBotComparison").mockImplementation(id => id === 1 ? new Promise(resolve => { resolveOld = resolve; }) : Promise.resolve(data));
  const view = render(<TopBotComparison key={1} accountId={1} />);
  view.rerender(<TopBotComparison key={2} accountId={2} />);
  await screen.findByText("Actual broker executions");
  resolveOld({ ...data, actual_scope: "Private old account" });
  await waitFor(() => expect(screen.queryByText(/Private old account/)).toBeNull());
});

it("answers the 25-point baseline question with denominators, pending categories and a separate trailing reference", async () => {
  const diff = (mean: number, verdict: string) => ({ entries: 3, sessions: 1, mean_difference_usd: mean, total_difference_usd: mean*3,
    mean_difference_r: mean/50, entries_better: 1, entries_worse: 1, entries_same: 1, interval_95_pointwise_usd: null,
    interval_adjusted_usd: null, baseline_target_became_stop: 1, verdict });
  const outcomes = { dollars: { ...empty, expectancy: 10 }, r: empty, take_profit_exits: 1, stop_exits: 2 };
  vi.spyOn(botsApi, "getTopBotComparison").mockResolvedValue({ ...data, account_id: 123,
    readiness: { experiment_id: "study", registered_filled_entries: 20, pending: 16, processed: 4, deferred: 3,
      counts: { observing: 3, queued: 9, processing: 1, retrying: 2, failed: 1, completed_valid: 3, completed_excluded: 1 },
      processed_coverage: { numerator: 3, denominator: 4, rate: .75, label: "p" }, overall_usable: { numerator: 3, denominator: 20, rate: .15, label: "o" },
      oldest_overdue: { entry_id: 142, cutoff: "2026-11-10T20:53:17Z", overdue_seconds: 7200, state: "retrying" },
      unfilled_submissions: [], not_trades: {}, prior_experiment_entries: 0 },
    research_health: { state: "replaying", last_cycle_at: null, last_error_type: "no_progress", account_deferred: 3, account_overdue: 13 },
    tp_baseline: { question: "Would a larger fixed take-profit perform better?", report_version: "v1",
      baseline: { label: "Current fixed bracket: 25-point take-profit, 25-point stop" },
      cohort: { valid_paired_entries: 3, excluded_entries: 1, sessions: 1 },
      series: [{ key: "1", ratio: 1, target_points: 25, outcomes }, { key: "2", ratio: 2, target_points: 50, outcomes, versus_baseline: diff(-12.5, "insufficient_data") }],
      trailing_reference: { label: "Trailing reference (25 stop / +25 activation / 25 trail). Not the requested baseline.", outcomes, versus_baseline: diff(4, "insufficient_data") },
      partial_observations: [{ entry_id: 168, session: "2026-11-11", resolved: { "1": { exit_reason: "take_profit" } }, unresolved: { "2": "feed_unavailable" } }],
      baseline_agreement: { compared: 3, same_exit_reason: 3, disagreements: [] }, minimums: { entries: 30, sessions: 5 },
      interval_methods: { pointwise: "pw", adjusted: "adj" }, caveats: ["More entries do not settle the question by themselves."] } });
  render(<TopBotComparison accountId={123} />);
  await screen.findByLabelText("Larger take-profit versus current 25-point take-profit");
  expect(screen.getByText(/Overall usable: 3 of 20 registered filled entries \(15%\), pending included/)).toBeTruthy();
  expect(screen.getByText(/Processed coverage: 3 valid of 4 processed \(75%\)/)).toBeTruthy();
  expect(screen.getByText(/Pending 16: 3 observation window open · 9 queued · 1 replaying · 2 retrying · 1 failed/)).toBeTruthy();
  expect(screen.getByText(/this account: 3 deferred, 13 overdue/)).toBeTruthy();
  expect(screen.getByText("50 pts (2:1)")).toBeTruthy();
  expect(screen.getByText("-12.50")).toBeTruthy();
  expect(screen.getAllByText("Insufficient data").length).toBe(1);
  expect(screen.getByText(/Separate reference, not the requested baseline/)).toBeTruthy();
  expect(screen.getByText(/Entry 168: resolved 1 · unresolved 2 \(feed unavailable\)/)).toBeTruthy();
  expect(screen.getByText(/same exit as the broker 3 times/)).toBeTruthy();
});

it("ignores a comparison returned for a different account", async () => {
  vi.spyOn(botsApi, "getTopBotComparison").mockResolvedValue({ ...data, account_id: 999, actual_scope: "Other account" });
  render(<TopBotComparison accountId={123} />);
  await screen.findByText(/Comparison unavailable/);
  expect(screen.queryByText(/Other account/)).toBeNull();
});
