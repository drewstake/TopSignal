// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { botsApi } from "../../lib/api";
import * as api from "../../lib/api";
import type { ProjectXMarketCandle } from "../../lib/types";
import { TopBotBiasStrip } from "./TopBotBiasStrip";
import { resetBotCandleMemoryCacheForTests } from "./botCandleCache";
vi.mock("./usePageVisibility", () => ({ usePageVisibility: () => true }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); localStorage.clear(); resetBotCandleMemoryCacheForTests(); });

it("restores persisted biases after reload, skips recent reads, and retains them on failure", async () => {
  vi.spyOn(api, "getAuthenticatedCacheScope").mockResolvedValue("bias-test-user");
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-30T16:00:00Z"));
  const get = vi.spyOn(botsApi, "getCandles").mockImplementation(async query => {
    const seconds = (query.unit === "day" ? 86400 : query.unit === "hour" ? 3600 : 60) * (query.unitNumber ?? 1);
    const rows = Array.from({ length: 30 }, (_, index) => ({ id: index, contract_id: query.contractId,
      symbol: "MNQ", unit: query.unit, unit_number: query.unitNumber,
      timestamp: new Date(Date.now() - (30 - index) * seconds * 1000).toISOString(),
      open: 100 + index, high: 102 + index, low: 99 + index, close: 101 + index,
      volume: 100, is_partial: false, live: false, fetched_at: new Date().toISOString(),
    })) as ProjectXMarketCandle[];
    rows.push({ ...rows[29], id: 31, timestamp: new Date().toISOString(), close: 0, is_partial: true });
    return rows;
  });
  const element = <TopBotBiasStrip market={{ contract_id: "CON.F.US.MNQ.Z26", symbol: "MNQ", timeframe_unit: "minute", timeframe_unit_number: 5, lookback_bars: 100 }} demoMode={false} />;
  const initial = render(element);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getAllByText("Bullish")).toHaveLength(6);
  expect(get.mock.calls.every(([query]) => query.includePartialBar === false && query.limit === 80)).toBe(true);
  initial.unmount();
  resetBotCandleMemoryCacheForTests(); // Simulate a full reload, not just React retaining state.
  get.mockClear();
  const restored = render(element);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getAllByText("Bullish")).toHaveLength(6);
  expect(screen.queryByText("Checking…")).toBeNull();
  expect(get).not.toHaveBeenCalled();
  get.mockRejectedValue(new Error("Offline"));
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(screen.getAllByText("Stale · awaiting fresh data")).toHaveLength(6);
  expect(screen.getByText("Waiting for timeframe data")).not.toBeNull();
  restored.unmount();
  resetBotCandleMemoryCacheForTests();
  vi.setSystemTime(new Date("2026-11-02T16:00:00Z"));
  get.mockReturnValue(new Promise(() => {}));
  const aged = render(element);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getAllByText("Bullish")).toHaveLength(6);
  expect(screen.getAllByText("Stale · awaiting fresh data").length).toBeGreaterThan(0);
  aged.unmount();
  vi.mocked(api.getAuthenticatedCacheScope).mockResolvedValue("different-user");
  render(element);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.queryByText("Bullish")).toBeNull();
  expect(screen.getAllByText("Checking…")).toHaveLength(6);
});
