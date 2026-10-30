// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, requestJson } from "../../lib/api";
import { SystemLogView, type SystemLogs } from "./LogsPage";

vi.mock("../../lib/api", () => ({ requestJson: vi.fn(), ApiError: class extends Error { status: number; constructor(message: string, status: number) { super(message); this.status = status; } } }));
vi.mock("../bot/usePageVisibility", () => ({ usePageVisibility: () => true }));
vi.mock("../bot/BotDiagnosticsPanel", () => ({ BotDiagnosticsPanel: () => null }));
const snapshot: SystemLogs = {
  generated_at: "2026-10-30T13:00:00Z", scan_limited: false, has_more: false,
  worker: { enabled: true, healthy: true, status: "running" },
  sources: { host: "available", market_feed: "available" },
  entries: [{ id: "one", timestamp: "2026-10-30T12:00:00Z", level: "WARNING", source: "Order book", event: "reconnecting", message: "Market feed interrupted; reconnecting." }],
};
afterEach(cleanup);
beforeEach(() => { vi.clearAllMocks(); });

describe("service logs", () => {
  it("keeps history clearly stale during failure, then recovers", async () => {
    const user = userEvent.setup();
    vi.mocked(requestJson).mockResolvedValueOnce(snapshot).mockRejectedValueOnce(new Error("network")).mockResolvedValueOnce(snapshot);
    render(<SystemLogView />);
    expect(await screen.findByText("Healthy")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Refresh status" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText(/Showing stale history/)).toBeTruthy();
    expect(screen.getByText("Unknown")).toBeTruthy();
    expect(screen.getByText("Market feed interrupted; reconnecting.")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Refresh status" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(screen.getByText("Healthy")).toBeTruthy();
  });

  it("clears private history after an authentication failure", async () => {
    const user = userEvent.setup();
    vi.mocked(requestJson).mockResolvedValueOnce(snapshot).mockRejectedValueOnce(Object.assign(new ApiError("denied", 401, null, null), { status: 401 }));
    render(<SystemLogView />);
    await screen.findByText("Healthy");
    await user.click(screen.getByRole("button", { name: "Refresh status" }));
    await screen.findByText("Sign in again to read the host logs.");
    expect(screen.queryByText("Market feed interrupted; reconnecting.")).toBeNull();
  });

  it("labels incomplete sources and filters only the available history", async () => {
    const user = userEvent.setup();
    vi.mocked(requestJson).mockResolvedValue({ ...snapshot, scan_limited: true, sources: { host: "read_error", market_feed: "available" } });
    render(<SystemLogView />);
    await screen.findByText(/Some log sources are unavailable/);
    expect(screen.getByText(/Showing a bounded recent history/)).toBeTruthy();
    await user.selectOptions(screen.getByLabelText("Event severity"), "ERROR");
    expect(screen.getByText("No matching events in the available history.")).toBeTruthy();
  });
});
