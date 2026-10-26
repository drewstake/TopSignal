// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { requestJson } from "../../lib/api";
import { backtestResultFixture } from "../bot/backtestTestFixtures";
import { TopBotBacktestPanel, type HistoricalJob } from "./TopBotBacktestPanel";

vi.mock("../../lib/api", () => ({ requestJson: vi.fn() }));
const transport = vi.fn();
const savedModel = { model_sha256: "a".repeat(64), trained_through: "2026-07-10T19:45:00Z", first_full_test_day: "2026-07-11", training_paths: 1648, source_label: "Verified test snapshot", verified_at: "2026-10-24T00:00:00Z", artifact_created_at: "2026-10-22T00:00:00Z" };
beforeEach(() => { vi.mocked(requestJson).mockImplementation((path, options) => path.endsWith("/backtest-model") ? Promise.resolve(savedModel) : transport(path, options)); });
const props = { coverageEnd: "2026-10-23T21:00:00Z", chartStart: "2026-10-01T00:00:00Z", chartEnd: "2026-10-24T00:00:00Z" };
const job: HistoricalJob = { id: "run-1", status: "running", progress: { phase: "replaying", completed: 5, total: 10 }, cancel_requested: false, result: null, error: null };
afterEach(() => { cleanup(); sessionStorage.clear(); vi.resetAllMocks(); });

it("runs independent of chart timeframe, sends exact inclusive dates and shows results", async () => {
  const result = { ...backtestResultFixture, skipped: { no_model_edge: 5 }, config_snapshot: { starting_balance: 50000, training_start: "2025-01-01", training_end: "2025-12-31", training_paths: 4096, model_sha256: "abc" } };
  transport.mockResolvedValueOnce(job).mockResolvedValue({ ...job, status: "complete", result });
  render(<TopBotBacktestPanel {...props}/>);
  await screen.findByText(/Saved running model/);
  fireEvent.click(screen.getByRole("button", { name: "Use chart date range" }));
  fireEvent.click(screen.getByRole("button", { name: "Run TopBot" }));
  await screen.findByText("Performance results");
  expect(requestJson).toHaveBeenCalledWith("/api/market-data/databento/backtests", expect.objectContaining({ method: "POST", body: {
    start: "2026-10-01T00:00:00.000Z", end: "2026-10-24T00:00:00.000Z", starting_balance: 50000, commission_per_contract: .61, slippage_ticks: 1, model_source: "saved_running_model", model_sha256: savedModel.model_sha256,
  } }));
  expect(screen.getByRole("table", { name: "Detailed trade ledger" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Export trades CSV" })).toBeTruthy();
  expect(screen.getByRole("img", { name: "Backtest equity and drawdown chart" })).toBeTruthy();
  const createObjectURL = vi.fn(() => "blob:backtest");
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  fireEvent.click(screen.getByRole("button", { name: "Export full result JSON" }));
  expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
  expect(click).toHaveBeenCalled();
  click.mockRestore(); vi.unstubAllGlobals();
});

it("rejects multi-year requests without submitting them", async () => {
  render(<TopBotBacktestPanel {...props}/>);
  await screen.findByText(/Saved running model/);
  fireEvent.change(screen.getByLabelText("Backtest from"), { target: { value: "2019-01-01" } });
  fireEvent.click(screen.getByRole("button", { name: "Run TopBot" }));
  expect(screen.getByRole("alert").textContent).toContain("at most 366 days");
  expect(transport).not.toHaveBeenCalled();
});

it("selects post-training history and rejects dates overlapping training", async () => {
  render(<TopBotBacktestPanel {...props}/>);
  await screen.findByText(/Saved running model/);
  fireEvent.click(screen.getByRole("button", { name: "All history after training" }));
  expect((screen.getByLabelText("Backtest from") as HTMLInputElement).value).toBe("2026-07-11");
  expect((screen.getByLabelText("Backtest through") as HTMLInputElement).value).toBe("2026-10-23");
  fireEvent.change(screen.getByLabelText("Backtest from"), { target: { value: "2026-07-10" } });
  fireEvent.click(screen.getByRole("button", { name: "Run TopBot" }));
  expect(screen.getByRole("alert").textContent).toContain("no refit will be substituted");
  expect(transport).not.toHaveBeenCalled();
});

it("blocks running when the saved model is unavailable and lets the user retry", async () => {
  vi.mocked(requestJson).mockRejectedValueOnce(new Error("No verified running TopBot model is installed"));
  render(<TopBotBacktestPanel {...props}/>);
  expect((await screen.findByRole("alert")).textContent).toContain("No verified running TopBot model");
  expect((screen.getByRole("button", { name: "Run TopBot" }) as HTMLButtonElement).disabled).toBe(true);
  expect(transport).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Reload model" }));
  await screen.findByText(/Saved running model/);
  expect((screen.getByRole("button", { name: "Run TopBot" }) as HTMLButtonElement).disabled).toBe(false);
});

it("shows progress, blocks duplicate runs and cancels the backend job", async () => {
  transport.mockImplementation(async (path) => path.endsWith("/cancel") ? { ...job, status: "cancelled", cancel_requested: true } : job);
  render(<TopBotBacktestPanel {...props}/>);
  await screen.findByText(/Saved running model/);
  fireEvent.click(screen.getByRole("button", { name: "Run TopBot" }));
  await screen.findByText("Replaying TopBot · 50%");
  expect((screen.getByRole("button", { name: "Run TopBot" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Cancel backtest" }));
  await waitFor(() => expect(requestJson).toHaveBeenCalledWith(expect.stringContaining("run-1/cancel"), { method: "POST" }));
});

it("restores a job ID after navigation and handles expired runs", async () => {
  sessionStorage.setItem("topsignal-historical-backtest-job", "expired");
  transport.mockRejectedValue(new Error("Backtest expired or was not found."));
  render(<TopBotBacktestPanel {...props}/>);
  await screen.findByText(/Saved running model/);
  expect((await screen.findByRole("alert")).textContent).toContain("expired");
  expect(screen.getByRole("button", { name: "Reconnect to run" })).toBeTruthy();
});

it("reports genuine zero-trade outcomes", async () => {
  sessionStorage.setItem("topsignal-historical-backtest-job", "empty");
  transport.mockResolvedValue({ ...job, status: "complete", result: { ...backtestResultFixture,
    trades: [], skipped: {}, config_snapshot: { starting_balance: 50000 }, metrics: { ...backtestResultFixture.metrics, trade_count: 0 } } });
  render(<TopBotBacktestPanel {...props}/>);
  await screen.findByText(/Saved running model/);
  expect(await screen.findByText(/No qualifying TopBot trades/)).toBeTruthy();
  expect(screen.getByText("No trades to chart")).toBeTruthy();
  expect(screen.getByText(/Balance stayed at \$50,000.00/)).toBeTruthy();
  expect(screen.queryByRole("img", { name: "Backtest equity and drawdown chart" })).toBeNull();
});

it("explains measured blockers and the exact net-edge shortfall", async () => {
  sessionStorage.setItem("topsignal-historical-backtest-job", "diagnosed");
  transport.mockResolvedValue({ ...job, status: "complete", result: { ...backtestResultFixture,
    trades: [], skipped: { outside_entry_session: 4668, volatility_above_risk_cap: 1416, no_model_edge: 240 },
    config_snapshot: { starting_balance: 50000 }, metrics: { ...backtestResultFixture.metrics, trade_count: 0 },
    entry_diagnostics: { observed_decisions: 6324, in_session: 1656, valid_features: 240, model_evaluations: 240,
      supported_forecasts: 240, edge_above_threshold: 0, entries: 0, maximum_stop_points: 25, minimum_net_edge_usd: 1,
      round_trip_cost_usd: 3.22, model_binding: "historical_refit", runtime_gates: "historical only", blocked_stop_range: { min: 25.25, max: 80 },
      closest_forecasts: [{ time: "2026-10-12T18:30:00Z", side: "BUY", stop_points: 14.75, expected_net_usd: -1.386,
        uncertainty_penalty_usd: 4.577, lower_utility_usd: -5.963, effective_days: 89.49, supported: true, action: "NO_TRADE" }] },
  } });
  render(<TopBotBacktestPanel {...props}/>);
  await screen.findByText(/Saved running model/);
  await screen.findByRole("table", { name: "Entry blocker counts" });
  expect(screen.getByText("1,416")).toBeTruthy();
  expect(screen.getByText("-$5.96")).toBeTruthy();
  expect(screen.getByText(/The runner evaluated 240 forecasts/)).toBeTruthy();
  expect(screen.getByText(/installed forward-testing artifact/)).toBeTruthy();
  expect(screen.queryByText(/may not clear/)).toBeNull();
});
