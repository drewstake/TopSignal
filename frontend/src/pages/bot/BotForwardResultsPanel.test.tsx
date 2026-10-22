// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { botsApi } from "../../lib/api";
import { BotForwardResultsPanel, type ForwardResults } from "./BotForwardResultsPanel";
vi.mock("../../lib/api", () => ({ botsApi: { getForwardResults: vi.fn() } }));
const fixture = (reason = "No supported opportunity"): ForwardResults => ({ status: "available", generated_at: "2026-10-22T14:00:00Z", latest_at: "2026-10-22T14:00:00Z", scan_limited: false, evaluations: 5, forecasts: 4, completed: 2, pending: 1, unknown: 1, overdue: 1, artifacts: ["test"], rejection_counts: { edge_below_threshold: 5 }, paper_blocks: {}, calibration: {}, account: null, recent: [{ at: "2026-10-22T14:00:00Z", action: "HOLD", stage: "edge_below_threshold", reason, risk_codes: [], paper_disposition: "edge_below_threshold", outcome_status: "pending" }] });
afterEach(() => { cleanup(); vi.resetAllMocks(); });
it("distinguishes forecast outcomes from trades and shows rejection evidence", async () => {
  vi.mocked(botsApi.getForwardResults).mockResolvedValue(fixture());
  render(<BotForwardResultsPanel botId={32} />);
  expect(await screen.findByText("No supported opportunity")).toBeTruthy();
  expect(screen.getByText("Unknown")).toBeTruthy();
  expect(screen.getByText(/No simulated account records yet/)).toBeTruthy();
  expect(screen.getByText(/No real orders/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Forward results period"), { target: { value: "30" } });
  expect(vi.mocked(botsApi.getForwardResults).mock.calls.at(-1)?.[1]).toBe(30);
});
it("never shows the previous bot's late response", async () => {
  let resolve!: (v: ForwardResults) => void;
  vi.mocked(botsApi.getForwardResults).mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  const view = render(<BotForwardResultsPanel botId={32} />);
  vi.mocked(botsApi.getForwardResults).mockResolvedValue(fixture("New bot"));
  view.rerender(<BotForwardResultsPanel botId={33} />);
  await screen.findByText("New bot");
  await act(async () => resolve(fixture("Old bot")));
  expect(screen.queryByText("Old bot")).toBeNull();
});
it("shows unavailable data instead of a zero-profit claim", async () => {
  vi.mocked(botsApi.getForwardResults).mockRejectedValue(new Error("offline"));
  render(<BotForwardResultsPanel botId={32} />);
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.queryByText(/Net change/)).toBeNull();
});
it("does not fabricate demo evidence", () => {
  render(<BotForwardResultsPanel botId={32} demoMode />);
  expect(botsApi.getForwardResults).not.toHaveBeenCalled();
});

it("clears loaded results immediately on bot, demo, and period changes", async () => {
  vi.mocked(botsApi.getForwardResults).mockResolvedValue(fixture("Previous results"));
  const view = render(<BotForwardResultsPanel botId={32} />);
  await screen.findByText("Previous results");
  vi.mocked(botsApi.getForwardResults).mockImplementation(() => new Promise(() => {}));
  fireEvent.change(screen.getByLabelText("Forward results period"), { target: { value: "7" } });
  expect(screen.queryByText("Previous results")).toBeNull();
  expect(screen.getByText("Loading forward results…")).toBeTruthy();
  expect(botsApi.getForwardResults).toHaveBeenLastCalledWith(32, 7, expect.anything());

  vi.mocked(botsApi.getForwardResults).mockResolvedValue(fixture("Next bot results"));
  view.rerender(<BotForwardResultsPanel botId={33} />);
  await screen.findByText("Next bot results");
  expect(botsApi.getForwardResults).toHaveBeenLastCalledWith(33, 7, expect.anything());
  const requests = vi.mocked(botsApi.getForwardResults).mock.calls.length;
  view.rerender(<BotForwardResultsPanel botId={33} demoMode />);
  expect(screen.queryByText("Next bot results")).toBeNull();
  expect(botsApi.getForwardResults).toHaveBeenCalledTimes(requests);
});

it("resets a failed request on manual refresh and cancels the prior request", async () => {
  vi.mocked(botsApi.getForwardResults).mockRejectedValueOnce(new Error("offline"));
  render(<BotForwardResultsPanel botId={32} />);
  await screen.findByRole("alert");
  const oldSignal = vi.mocked(botsApi.getForwardResults).mock.calls[0][2]?.signal;
  let resolve!: (v: ForwardResults) => void;
  vi.mocked(botsApi.getForwardResults).mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  fireEvent.click(screen.getByRole("button", { name: "Refresh results" }));
  expect(oldSignal?.aborted).toBe(true);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByText("Loading forward results…")).toBeTruthy();
  await act(async () => resolve(fixture("Recovered")));
  expect(screen.getByText("Recovered")).toBeTruthy();
});
