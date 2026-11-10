// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { botsApi } from "../../lib/api";
import type { BotActivity, BotConfig } from "../../lib/types";
import { BotPage } from "./BotPage";

const context = vi.hoisted(() => ({
  accounts: [{ id: 1, trade_data_source: "projectx" }, { id: 2, trade_data_source: "projectx" }],
  accountsLoading: false, accountsError: null, selectedAccountId: 1,
}));
vi.mock("react-router-dom", async importOriginal => ({ ...await importOriginal<typeof import("react-router-dom")>(), useOutletContext: () => context }));
vi.mock("../../lib/demoMode", () => ({ useDemoMode: () => ({ enabled: false }) }));
vi.mock("./usePageVisibility", () => ({ usePageVisibility: () => true }));

vi.mock("./BotSignalChart", () => ({ BotSignalChart: () => <div>Chart</div> }));
vi.mock("./ProjectXSignalChart", () => ({ ProjectXSignalChart: () => <div>Market chart</div> }));
vi.mock("./OrderBookPanel", () => ({ OrderBookPanel: () => <div>Order book</div> }));
vi.mock("./LiquidityHeatmapPanel", () => ({ LiquidityHeatmapPanel: () => <div>Liquidity heatmap</div> }));
vi.mock("./SessionOrderFlowPanel", () => ({ SessionOrderFlowPanel: () => <div>Session order flow</div> }));
vi.mock("./TopBotBiasStrip", () => ({ TopBotBiasStrip: () => <div>Biases</div> }));
vi.mock("./TopBotPosition", () => ({ TopBotPosition: () => <div>Position</div> }));
vi.mock("./history/BotLiquidityHistory", () => ({
  HistoryView: ({ accountId }: { accountId: number }) => <div>Liquidity history for {accountId}</div>,
}));

vi.mock("./BotLogsPanel", () => ({ BotLogsPanel: ({ accountId }: { accountId: number | null }) => <div>Logs for {accountId}</div> }));

const bot = { id: 10, account_id: 1, enabled: true, strategy_type: "topbot_adaptive" } as BotConfig;
const activity = { config: bot, decisions: [], order_attempts: [], risk_events: [], runs: [{ id: 1, account_id: 1, bot_config_id: 10, status: "running", started_at: "2026-10-31T10:00:00Z", dry_run: true, stopped_at: null, stop_reason: null, last_heartbeat_at: null }] } as BotActivity;

beforeEach(() => {
  context.selectedAccountId = 1;
  vi.spyOn(botsApi, "getTopBotStatistics").mockResolvedValue({ scope: "This account", items: [], closed_trades: 0,
    wins: 0, losses: 0, net_pnl_usd: 0, winner_hold_mean_seconds: null, winner_hold_median_seconds: null,
    loser_hold_mean_seconds: null, loser_hold_median_seconds: null });
  vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({ configs: { items: [bot], total: 1 }, cacheScope: "test" });
  vi.spyOn(botsApi, "getActivity").mockResolvedValue(activity);
  vi.spyOn(botsApi, "getRuntimeStatus").mockResolvedValue({ ready: true, provider_status: "healthy", counts: {}, state: "running", checks: {
    worker_enabled: true, worker_task_healthy: true, lease_healthy: true,
  } });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const botStatus = () => within(screen.getByRole("banner")).getByRole("status");

it("shows green only for a confirmed running bot and healthy worker", async () => {
  render(<MemoryRouter><BotPage /></MemoryRouter>);
  expect(screen.getByRole("heading", { name: "TopBot" })).not.toBeNull();
  await waitFor(() => expect(botStatus().title).toBe("Dry Run active"));
  expect(botStatus().querySelector(".bg-emerald-400")).not.toBeNull();
  expect(within(screen.getByRole("region", { name: "What TopBot sees" })).getByText("Liquidity history for 1")).toBeTruthy();
});

it("shows red when there is no configured TopBot", async () => {
  vi.mocked(botsApi.listConfigsWithCacheScope).mockResolvedValue({ configs: { items: [], total: 0 }, cacheScope: "test" });
  render(<MemoryRouter><BotPage /></MemoryRouter>);
  await waitFor(() => expect(botStatus().title).toContain("Inactive"));
  expect(botStatus().querySelector(".bg-red-400")).not.toBeNull();
  expect(botsApi.getActivity).not.toHaveBeenCalled();
});

it("keeps a reset strategy inactive even if an old running record remains", async () => {
  const resetBot = { ...bot, strategy_params: { revision: "custom_unconfigured" } };
  vi.mocked(botsApi.listConfigsWithCacheScope).mockResolvedValue({ configs: { items: [resetBot], total: 1 }, cacheScope: "test" });
  vi.mocked(botsApi.getActivity).mockResolvedValue({ ...activity, config: resetBot });
  render(<MemoryRouter><BotPage /></MemoryRouter>);
  await waitFor(() => expect(botStatus().title).toBe("Inactive · strategy not configured"));
  expect(screen.getByText("NO STRATEGY")).not.toBeNull();
  expect(screen.getByText("Chart")).not.toBeNull();
  expect(screen.getByText("Order book")).not.toBeNull();
  expect(botsApi.getRuntimeStatus).not.toHaveBeenCalled();
});

it("shows yellow if a running record cannot be confirmed by the worker", async () => {
  vi.mocked(botsApi.getRuntimeStatus).mockResolvedValue({ ready: false, provider_status: "unknown", counts: {}, state: "stopped", checks: {} });
  render(<MemoryRouter><BotPage /></MemoryRouter>);
  await waitFor(() => expect(botStatus().title).toContain("worker needs attention"));
  expect(botStatus().querySelector(".bg-amber-400")).not.toBeNull();
});

it("shows yellow when the status request fails", async () => {
  vi.mocked(botsApi.listConfigsWithCacheScope).mockRejectedValue(new Error("Offline"));
  render(<MemoryRouter><BotPage /></MemoryRouter>);
  await waitFor(() => expect(botStatus().title).toBe("Bot status unavailable"));
});

it("discards a previous account's pending status response", async () => {
  let resolve!: (value: BotActivity) => void;
  vi.mocked(botsApi.getActivity).mockReturnValue(new Promise(done => { resolve = done; }));
  const view = render(<MemoryRouter><BotPage /></MemoryRouter>);
  await waitFor(() => expect(botsApi.getActivity).toHaveBeenCalled());
  context.selectedAccountId = context.accounts[1].id;
  view.rerender(<MemoryRouter><BotPage /></MemoryRouter>);
  await waitFor(() => expect(botStatus().title).toContain("Inactive"));
  await act(async () => { resolve(activity); });
  expect(botStatus().title).toContain("Inactive");
  expect(botsApi.getRuntimeStatus).not.toHaveBeenCalled();
});

it("opens account-scoped logs inside Bot and returns to the overview", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={["/bot?account=1"]}><BotPage /></MemoryRouter>);
  await user.click(screen.getByRole("button", { name: "Logs" }));
  expect(screen.getByText("Logs for 1")).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "TopBot" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Overview" }));
  expect(screen.getByRole("heading", { name: "TopBot" })).toBeTruthy();
  expect(screen.queryByText("Logs for 1")).toBeNull();
});

it("opens a bookmarked logs view with the current account", () => {
  render(<MemoryRouter initialEntries={["/bot?account=1&view=logs"]}><BotPage /></MemoryRouter>);
  expect(screen.getByText("Logs for 1")).toBeTruthy();
  expect(botsApi.listConfigsWithCacheScope).not.toHaveBeenCalled();
});
