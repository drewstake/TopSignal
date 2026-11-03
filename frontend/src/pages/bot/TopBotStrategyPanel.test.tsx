// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { botsApi } from "../../lib/api";
import { TopBotStrategyPanel, type TopBotStatistics } from "./TopBotStrategyPanel";
import type { BotActivity, BotConfig } from "../../lib/types";
vi.mock("./usePageVisibility", () => ({ usePageVisibility: () => true }));
const stats: TopBotStatistics = { scope: "Account only", items: [], closed_trades: 2, wins: 1, losses: 1,
  net_pnl_usd: 23.56, winner_hold_mean_seconds: 420, winner_hold_median_seconds: 420,
  loser_hold_mean_seconds: 120, loser_hold_median_seconds: 120 };
beforeEach(() => {
  vi.spyOn(botsApi, "getTopBotStatistics").mockResolvedValue(stats);
  vi.spyOn(botsApi, "getTopBotAudit").mockResolvedValue({ items: [], next_before: null, storage: "Worker" });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("shows the saved entry-limit preference", async () => {
  render(<TopBotStrategyPanel accountId={123} bot={{ id: 39, enabled: true, strategy_params: { daily_entry_limit_enabled: false } } as BotConfig}
    activity={null} demoMode={false} onChanged={() => {}} />);
  expect(screen.getByText(/Daily entry-count limit disabled/)).toBeTruthy();
  await waitFor(() => expect(screen.getByText("$23.56")).toBeTruthy());
});
it("starts broker practice routing for the selected account, not dry run", async () => {
  const start = vi.spyOn(botsApi, "startTopBot").mockResolvedValue({} as never);
  const changed = vi.fn();
  render(<TopBotStrategyPanel accountId={123} bot={null} activity={null} demoMode={false} onChanged={changed} />);
  fireEvent.click(screen.getByRole("button", { name: "Start practice bot" }));
  await waitFor(() => expect(start).toHaveBeenCalledWith(123, false));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(screen.getByText("$23.56")).toBeTruthy();
  expect(screen.getByText("7.0m / 7.0m")).toBeTruthy();
});
it("stops entries using the exact bot and explains protection remains", async () => {
  const stop = vi.spyOn(botsApi, "stop").mockResolvedValue({} as never);
  render(<TopBotStrategyPanel accountId={123} bot={{ id: 44, enabled: true } as BotConfig}
    activity={{ runs: [{ status: "running" }] } as BotActivity} demoMode={false} onChanged={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Stop new entries" }));
  await waitFor(() => expect(stop).toHaveBeenCalledWith(44));
  expect(screen.getByText(/Stopping entries keeps protection/)).toBeTruthy();
});
it("never routes orders or fetches practice stats in demo mode", () => {
  const start = vi.spyOn(botsApi, "startTopBot");
  render(<TopBotStrategyPanel accountId={123} bot={null} activity={null} demoMode onChanged={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Start practice bot" }));
  expect(start).not.toHaveBeenCalled();
  expect(botsApi.getTopBotStatistics).not.toHaveBeenCalled();
});
