// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { accountsApi, requestJson } from "../../lib/api";
import type { AccountTrade } from "../../lib/types";
import { TopBotTradeRecorder } from "./TopBotTradeRecorder";

vi.mock("../../lib/api", () => ({ accountsApi: { getTrades: vi.fn() }, requestJson: vi.fn() }));
const status = { enrolled: true, healthy: true, heartbeat: 1794382683, error: null };
const trade = (account_id: number, id = 123): AccountTrade => ({
  id, account_id, symbol: "MNQ", side: "Buy", size: 1, timestamp: "2026-11-11T07:00:00Z",
} as AccountTrade);
const view = (accountId: number) => <MemoryRouter><TopBotTradeRecorder accountId={accountId} /></MemoryRouter>;

beforeEach(() => {
  vi.mocked(requestJson).mockResolvedValue(status);
  vi.mocked(accountsApi.getTrades).mockResolvedValue([trade(1)]);
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("shows the recorder without changing enrollment or fetching trade history until requested", async () => {
  render(view(1));
  await screen.findByText("Trade progress · Recording");
  expect(screen.getByRole("heading", { name: "Trade recorder · 1 second" })).toBeTruthy();
  expect(requestJson).toHaveBeenCalledWith("/api/accounts/1/trade-progress/status", expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(requestJson).toHaveBeenCalledTimes(1);
  expect(accountsApi.getTrades).not.toHaveBeenCalled();
});

it("reviews an account's path directly on Bot and keeps the all-trades link scoped", async () => {
  vi.mocked(requestJson).mockImplementation(async url => url.endsWith("/progress") ? { matches: [], status } : status);
  const user = userEvent.setup();
  render(view(1));
  await user.click(screen.getByRole("button", { name: "Review trade paths" }));
  await user.click(await screen.findByRole("button", { name: "Review path for MNQ trade 123" }));
  expect(accountsApi.getTrades).toHaveBeenCalledWith(1, { limit: 20 }, { bypassCache: true });
  expect(screen.getByRole("link", { name: "All trades" }).getAttribute("href")).toBe("/trades?account=1");
  await screen.findByText("No recording matched this trade.");
  expect(requestJson).toHaveBeenCalledWith("/api/accounts/1/trades/123/progress", expect.anything());
  await user.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.queryByText("Trade progress review")).toBeNull();
});

it("discards previous-account history and status when the account changes", async () => {
  let resolveTrades!: (trades: AccountTrade[]) => void;
  vi.mocked(accountsApi.getTrades).mockReturnValueOnce(new Promise(resolve => { resolveTrades = resolve; }));
  const user = userEvent.setup();
  const rendered = render(view(1));
  await screen.findByText("Trade progress · Recording");
  await user.click(screen.getByRole("button", { name: "Review trade paths" }));
  vi.mocked(requestJson).mockResolvedValue({ ...status, enrolled: false, healthy: false, heartbeat: null });
  rendered.rerender(view(2));
  await screen.findByText("Trade progress · Not enrolled");
  await act(async () => { resolveTrades([trade(1)]); });
  expect(screen.queryByRole("button", { name: "Review path for MNQ trade 123" })).toBeNull();
  vi.mocked(accountsApi.getTrades).mockResolvedValue([trade(2, 456), trade(1)]);
  await user.click(screen.getByRole("button", { name: "Review trade paths" }));
  await screen.findByRole("button", { name: "Review path for MNQ trade 456" });
  expect(screen.queryByRole("button", { name: "Review path for MNQ trade 123" })).toBeNull();
  expect(screen.getByRole("link", { name: "All trades" }).getAttribute("href")).toBe("/trades?account=2");
  await user.click(screen.getByRole("button", { name: "Enable recording" }));
  expect(requestJson).toHaveBeenCalledWith("/api/accounts/2/trade-progress/enrollment", { method: "PUT", query: { enabled: true } });
});

it("shows read failures and supports retry without presenting missing trades as a flat path", async () => {
  vi.mocked(accountsApi.getTrades).mockRejectedValueOnce(new Error("Offline")).mockResolvedValueOnce([]);
  const user = userEvent.setup();
  render(view(1));
  await user.click(screen.getByRole("button", { name: "Review trade paths" }));
  await screen.findByText("Recent trades are unavailable.");
  await user.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(screen.getByText(/No recent trades for this account/)).toBeTruthy());
  expect(screen.queryByRole("img")).toBeNull();
});

it("refreshes the open trade list on a bounded interval and stops when collapsed", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    vi.mocked(accountsApi.getTrades).mockResolvedValueOnce([trade(1, 1)]).mockResolvedValue([trade(1, 2), trade(1, 1)]);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(view(1));
    await user.click(screen.getByRole("button", { name: "Review trade paths" }));
    await screen.findByRole("button", { name: "Review path for MNQ trade 1" });
    expect(screen.queryByRole("button", { name: "Review path for MNQ trade 2" })).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    await screen.findByRole("button", { name: "Review path for MNQ trade 2" });
    expect(accountsApi.getTrades).toHaveBeenCalledTimes(2);
    await user.click(screen.getByRole("button", { name: "Hide trade paths" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(accountsApi.getTrades).toHaveBeenCalledTimes(2);
  } finally { vi.useRealTimers(); }
});
