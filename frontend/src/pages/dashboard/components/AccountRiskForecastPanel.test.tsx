// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { requestJson } from "../../../lib/api";
import { AccountRiskForecastPanel } from "./AccountRiskForecastPanel";
import type { RiskForecast, RiskSources } from "./riskForecastTypes";

vi.mock("../../../lib/api", () => ({ requestJson: vi.fn() }));
const api = vi.mocked(requestJson);
const catalog: RiskSources = { account_id: 123, balance: 50000, sources: [
  { id: "trades", kind: "trades", label: "Recorded account trades" },
  { id: "saved:5", kind: "saved", label: "Saved #5 · TopBot" },
] };
const result: RiskForecast = {
  version: "test", generated_at: "2026-10-26", source_id: "trades", source_label: "Recorded account trades",
  input_fingerprint: "abcdef123456", parameters: { horizon_days: 20, starting_balance: 50000, loss_buffer: 2000 },
  paths: 2000, block_days: 3, status: "ready", sample: { trades: 300, active_days: 60, first_day: "2026-06-01", last_day: "2026-09-01", net_pnl: 1234 },
  warnings: ["Existing execution costs retained."],
  scenarios: [.5, 1, 1.5].map(multiplier => ({ multiplier, account_breach: { probability: .1, simulation_interval: [.08, .12] },
    daily_limit_hit: null, ending_balance: { p10: 49000, p50: 51000, p90: 53000 }, max_drawdown: { p50: 500, p90: 1000, p95: 1200 },
    fan: [{ day: 0, p10: 50000, p50: 50000, p90: 50000 }, { day: 20, p10: 49000, p50: 51000, p90: 53000 }] })),
};

async function open() {
  fireEvent.click(screen.getByRole("button", { name: "Configure forecast" }));
  await screen.findByRole("option", { name: "Recorded account trades" });
}
async function calculate() {
  fireEvent.change(screen.getByLabelText("Remaining account loss buffer ($)"), { target: { value: "2000" } });
  fireEvent.click(screen.getByRole("button", { name: "Calculate risk forecast" }));
}

describe("Account Risk Forecast", () => {
  beforeEach(() => api.mockReset());
  afterEach(cleanup);

  it("loads only when opened and makes no data request in Demo mode", async () => {
    const view = render(<AccountRiskForecastPanel accountId={123} accountName="Main"/>);
    expect(api).not.toHaveBeenCalled();
    view.rerender(<AccountRiskForecastPanel accountId={123} accountName="Main" demoMode/>);
    expect(screen.queryByRole("button", { name: "Configure forecast" })).toBeNull();
    expect(api).not.toHaveBeenCalled();
  });

  it("uses selected account data, preserves user-entered limits, and displays scenario results", async () => {
    api.mockResolvedValueOnce(catalog).mockResolvedValueOnce(result);
    render(<AccountRiskForecastPanel accountId={123} accountName="Main"/>);
    await open();
    expect((screen.getByLabelText("Starting account balance ($)") as HTMLInputElement).value).toBe("50000");
    expect((screen.getByLabelText("Remaining account loss buffer ($)") as HTMLInputElement).value).toBe("");
    await calculate();
    await screen.findByRole("img", { name: /Simulated account balance/ });
    expect(api.mock.calls[1][0]).toBe("/api/accounts/123/risk-forecast");
    expect(api.mock.calls[1][1]?.body).toMatchObject({ source_id: "trades", horizon_days: 20, starting_balance: 50000,
      loss_buffer: 2000, daily_loss_limit: null, trailing_mode: "fixed", floor_cap: null });
    expect(screen.getByText("1× baseline")).toBeTruthy();
    expect(screen.getByText(/300 closed trades/)).toBeTruthy();
    expect(screen.getByText(/zero simulated rate does not mean zero risk/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Forecast active trading days"), { target: { value: "10" } });
    expect(screen.queryByRole("img")).toBeNull(); // old estimates never survive an input change
  });

  it("supports saved backtests and trailing limits without applying them to trading configuration", async () => {
    api.mockResolvedValueOnce(catalog).mockResolvedValueOnce(result);
    render(<AccountRiskForecastPanel accountId={123} accountName="Main"/>);
    await open();
    fireEvent.change(screen.getByLabelText("Data source"), { target: { value: "saved:5" } });
    expect(screen.queryByLabelText("History lookback")).toBeNull();
    fireEvent.change(screen.getByLabelText("Account loss floor"), { target: { value: "end_of_day" } });
    fireEvent.change(screen.getByLabelText("Trailing distance ($)"), { target: { value: "2000" } });
    fireEvent.change(screen.getByLabelText("Loss-floor cap ($ balance, optional)"), { target: { value: "50000" } });
    await calculate();
    await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
    expect(api.mock.calls[1][1]?.body).toMatchObject({ source_id: "saved:5", trailing_mode: "end_of_day", trailing_distance: 2000, floor_cap: 50000 });
    expect(api.mock.calls.every(([url]) => url.includes("risk-forecast"))).toBe(true);
  });

  it("shows insufficient history without probabilities or an empty chart", async () => {
    api.mockResolvedValueOnce(catalog).mockResolvedValueOnce({ ...result, status: "insufficient_data", scenarios: [] });
    render(<AccountRiskForecastPanel accountId={123} accountName="Main"/>);
    await open(); await calculate();
    await screen.findByText(/Not enough history for a forecast/);
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByText("Account floor hit")).toBeNull();
  });

  it("shows expired-source errors and allows retrying", async () => {
    api.mockResolvedValueOnce(catalog).mockRejectedValueOnce(new Error("Backtest expired or was not found."));
    render(<AccountRiskForecastPanel accountId={123} accountName="Main"/>);
    await open(); await calculate();
    expect((await screen.findByRole("alert")).textContent).toContain("Backtest expired");
    expect((screen.getByRole("button", { name: "Calculate risk forecast" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("ignores late forecasts after switching accounts and resets limits", async () => {
    let finish: (value: RiskForecast) => void = () => {};
    api.mockResolvedValueOnce(catalog).mockImplementationOnce(() => new Promise(resolve => { finish = resolve as typeof finish; }))
      .mockResolvedValueOnce({ ...catalog, account_id: 456, balance: 60000 });
    const view = render(<AccountRiskForecastPanel accountId={123} accountName="First"/>);
    await open(); await calculate();
    const signal = api.mock.calls[1][1]?.signal;
    view.rerender(<AccountRiskForecastPanel accountId={456} accountName="Second"/>);
    await waitFor(() => expect((screen.getByLabelText("Starting account balance ($)") as HTMLInputElement).value).toBe("60000"));
    expect(signal?.aborted).toBe(true);
    await act(async () => finish(result));
    expect(screen.queryByRole("img")).toBeNull();
    expect((screen.getByLabelText("Remaining account loss buffer ($)") as HTMLInputElement).value).toBe("");
  });

  it("recovers when the source catalog request fails", async () => {
    api.mockRejectedValueOnce(new Error("Connection unavailable")).mockResolvedValueOnce(catalog);
    render(<AccountRiskForecastPanel accountId={123} accountName="Main"/>);
    fireEvent.click(screen.getByRole("button", { name: "Configure forecast" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Refresh sources" }));
    await screen.findByRole("option", { name: "Recorded account trades" });
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
