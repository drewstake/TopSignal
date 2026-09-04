// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "../../lib/api";
import type { BotConfig } from "../../lib/types";
import { BotSignalChart } from "./BotSignalChart";

vi.mock("lightweight-charts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("lightweight-charts")>();
  const scale = { applyOptions: vi.fn(), getVisibleLogicalRange: () => null, subscribeVisibleLogicalRangeChange: vi.fn(),
    unsubscribeVisibleLogicalRangeChange: vi.fn(), fitContent: vi.fn(), setVisibleLogicalRange: vi.fn() };
  const series = { applyOptions: vi.fn(), setData: vi.fn(), update: vi.fn(), priceScale: () => scale };
  return { ...actual,
    createChart: () => ({ applyOptions: vi.fn(), resize: vi.fn(), addSeries: () => series, panes: () => [], timeScale: () => scale,
      paneSize: () => ({ width: 0, height: 0 }),
      subscribeCrosshairMove: vi.fn(), unsubscribeCrosshairMove: vi.fn(), remove: vi.fn() }),
    createSeriesMarkers: () => ({ setMarkers: vi.fn(), detach: vi.fn() }),
  };
});

const bot: BotConfig = {
  id: 1, account_id: 1, name: "TopBot", provider: "projectx", enabled: false, execution_mode: "dry_run",
  strategy_type: "sma_cross", strategy_params: {}, contract_id: "CON.F.US.MNQ.U26", symbol: "F.US.MNQ",
  timeframe_unit: "minute", timeframe_unit_number: 5, lookback_bars: 100, fast_period: 9, slow_period: 20,
  order_size: 1, max_contracts: 1, max_daily_loss: 100, max_trades_per_day: 1, max_open_position: 1,
  allowed_contracts: ["CON.F.US.MNQ.U26"], trading_start_time: "09:30", trading_end_time: "16:00",
  cooldown_seconds: 60, max_data_staleness_seconds: 60, allow_market_depth: false,
  created_at: "2026-09-04T20:00:00Z", updated_at: "2026-09-04T20:00:00Z",
};
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-04T21:05:00Z"));
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.spyOn(api.botsApi, "getCandles").mockResolvedValue([]);
  vi.spyOn(api, "streamProjectXMarketPrice").mockReturnValue(vi.fn());
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });
const mount = () => render(<BotSignalChart bot={bot} authenticatedCacheScope="session-test" activity={null}
  lastEvaluation={null} refreshToken={0} />);
const settle = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(5_000); }); };

it("skips closed-session live requests on navigation and resumes when the session opens", async () => {
  const first = mount();
  await settle();
  first.unmount();
  mount();
  await settle();
  expect(api.streamProjectXMarketPrice).not.toHaveBeenCalled();
  expect(vi.mocked(api.botsApi.getCandles).mock.calls.some(([query]) => query.includePartialBar)).toBe(false);
  vi.setSystemTime(new Date("2026-09-06T21:59:55Z"));
  await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
  expect(api.streamProjectXMarketPrice).toHaveBeenCalledTimes(1);
  expect(vi.mocked(api.botsApi.getCandles).mock.calls.some(([query]) => query.includePartialBar)).toBe(true);
  const stop = vi.mocked(api.streamProjectXMarketPrice).mock.results[0].value;
  vi.setSystemTime(new Date("2026-09-11T21:01:00Z"));
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(stop).toHaveBeenCalledTimes(1);
});

it("keeps an explicit chart Refresh available during a closure", async () => {
  mount();
  await settle();
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await settle();
  expect(vi.mocked(api.botsApi.getCandles).mock.calls.some(([query]) => query.includePartialBar && query.refresh)).toBe(true);
  expect(api.streamProjectXMarketPrice).not.toHaveBeenCalled();
});
