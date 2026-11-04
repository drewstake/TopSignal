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
