// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { botsApi } from "../../lib/api";
import type { BotLogs } from "../../lib/types";
import { BotDiagnosticsPanel } from "./BotDiagnosticsPanel";

vi.mock("../../lib/api", () => ({ botsApi: { getLogs: vi.fn() } }));

function fixture(accountId = 101): BotLogs {
  return {
    account_id: accountId, generated_at: "2026-10-21T16:00:00Z", since: "2026-10-20T16:00:00Z",
    status: "available", scan_limited: false, has_more: false,
    entries: [{ timestamp: "2026-10-21T15:59:00Z", event: "bot_evaluation_failed", level: "ERROR",
      bot_run_id: 12, correlation_id: "trace-123", reason: `Fixture account ${accountId}` }],
    summary: { matched_events: 1, errors: 1, warnings: 0, evaluation_outcomes: { risk_blocked: 1 },
      risk_blocks: { stale_data: 1 }, timings: { bot_evaluation_timing: { count: 1, mean_ms: 25, max_ms: 25 } } },
  };
}

beforeEach(() => { vi.mocked(botsApi.getLogs).mockResolvedValue(fixture()); });
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers(); });

describe("bot diagnostics", () => {
  it("loads account logs and shows summaries and inspectable correlation details", async () => {
    render(<BotDiagnosticsPanel accountId={101} />);
    expect(await screen.findByText("Fixture account 101")).toBeTruthy();
    expect(screen.getByText("25.0 ms")).toBeTruthy();
    expect(screen.getByText("stale data")).toBeTruthy();
    expect(screen.getByRole("table", { name: "Recent bot diagnostic events" })).toBeTruthy();
    fireEvent.click(screen.getByText("View event details"));
    expect(screen.getByText(/"correlation_id": "trace-123"/)).toBeTruthy();
    expect(vi.mocked(botsApi.getLogs).mock.calls[0][0]).toBe(101);
  });

  it("applies range, severity, run and search filters without fetching per keystroke", async () => {
    render(<BotDiagnosticsPanel accountId={101} />);
    await screen.findByText("Fixture account 101");
    fireEvent.change(screen.getByLabelText("Severity"), { target: { value: "ERROR" } });
    fireEvent.change(screen.getByLabelText("Time range"), { target: { value: "72" } });
    fireEvent.change(screen.getByLabelText("Run ID"), { target: { value: "12" } });
    fireEvent.change(screen.getByLabelText("Search logs"), { target: { value: "trace-123" } });
    expect(botsApi.getLogs).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    await waitFor(() => expect(botsApi.getLogs).toHaveBeenCalledTimes(2));
    expect(vi.mocked(botsApi.getLogs).mock.calls[1][1]).toEqual({ hours: 72, level: "ERROR", run_id: 12, search: "trace-123", limit: 100 });
  });

  it("clears old-account data immediately and ignores responses after switching accounts", async () => {
    let finishOld!: (value: BotLogs) => void;
    vi.mocked(botsApi.getLogs).mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }));
    const view = render(<BotDiagnosticsPanel accountId={101} />);
    vi.mocked(botsApi.getLogs).mockResolvedValue(fixture(202));
    view.rerender(<BotDiagnosticsPanel accountId={202} />);
    await screen.findByText("Fixture account 202");
    await act(async () => { finishOld(fixture(101)); });
    expect(screen.queryByText("Fixture account 101")).toBeNull();
    expect(vi.mocked(botsApi.getLogs).mock.calls[0][2]?.signal?.aborted).toBe(true);
  });

  it("rejects a response for the wrong account", async () => {
    vi.mocked(botsApi.getLogs).mockResolvedValue(fixture(202));
    render(<BotDiagnosticsPanel accountId={101} />);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Log response did not match the selected account.");
    expect(screen.queryByText("Fixture account 202")).toBeNull();
  });

  it("shows missing logging and partial coverage explicitly", async () => {
    vi.mocked(botsApi.getLogs).mockResolvedValue({ ...fixture(), status: "not_configured" });
    render(<BotDiagnosticsPanel accountId={101} />);
    expect(await screen.findByText(/No diagnostic log file is available yet/)).toBeTruthy();
    vi.mocked(botsApi.getLogs).mockResolvedValue({ ...fixture(), scan_limited: true, has_more: true });
    fireEvent.click(screen.getByRole("button", { name: "Refresh logs" }));
    expect(await screen.findByText(/Only the most recent portion/)).toBeTruthy();
    expect(screen.getByText(/Showing the newest/)).toBeTruthy();
  });

  it("retains last refresh with an explicit error when a later request fails", async () => {
    render(<BotDiagnosticsPanel accountId={101} />);
    await screen.findByText("Fixture account 101");
    vi.mocked(botsApi.getLogs).mockRejectedValue(new Error("Connection unavailable."));
    fireEvent.click(screen.getByRole("button", { name: "Refresh logs" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Connection unavailable. Showing the last successful refresh.");
    expect(screen.getByText("Fixture account 101")).toBeTruthy();
  });

  it("does not call diagnostics for demo mode or no account", () => {
    const view = render(<BotDiagnosticsPanel accountId={null} />);
    expect(screen.getByText(/Select a ProjectX account/)).toBeTruthy();
    view.rerender(<BotDiagnosticsPanel accountId={101} demoMode />);
    expect(screen.getByText(/unavailable in demo mode/)).toBeTruthy();
    expect(botsApi.getLogs).not.toHaveBeenCalled();
  });

  it("auto refreshes only when enabled", async () => {
    vi.useFakeTimers();
    render(<BotDiagnosticsPanel accountId={101} />);
    await act(async () => { await Promise.resolve(); });
    await act(async () => { vi.advanceTimersByTime(31_000); });
    expect(botsApi.getLogs).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByLabelText("Auto-refresh every 30s"));
    await act(async () => { vi.advanceTimersByTime(30_000); });
    expect(botsApi.getLogs).toHaveBeenCalledTimes(2);
  });

  it("times out a stalled request and cancels it", async () => {
    vi.useFakeTimers();
    vi.mocked(botsApi.getLogs).mockImplementation(() => new Promise(() => {}));
    render(<BotDiagnosticsPanel accountId={101} />);
    await act(async () => { vi.advanceTimersByTime(15_000); });
    expect(screen.getByRole("alert").textContent).toBe("Log refresh timed out. Try again.");
    expect(vi.mocked(botsApi.getLogs).mock.calls[0][2]?.signal?.aborted).toBe(true);
    expect((screen.getByRole("button", { name: "Refresh logs" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
