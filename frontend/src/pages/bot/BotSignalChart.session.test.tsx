// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "../../lib/api";
import type { BotConfig, ProjectXMarketCandle } from "../../lib/types";
import { BotSignalChart } from "./BotSignalChart";
import { findCandleGaps, isFuturesSessionOpen } from "./botCandleGaps";

const chartSeries = vi.hoisted(() => ({ update: vi.fn(), setData: vi.fn() }));
const viewport = vi.hoisted(() => ({ getVisibleRange: vi.fn(), setVisibleRange: vi.fn(), fitContent: vi.fn() }));

vi.mock("lightweight-charts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("lightweight-charts")>();
  const scale = { ...viewport, applyOptions: vi.fn(), getVisibleLogicalRange: () => null, subscribeVisibleLogicalRangeChange: vi.fn(),
    unsubscribeVisibleLogicalRangeChange: vi.fn(), setVisibleLogicalRange: vi.fn() };
  const series = { ...chartSeries, applyOptions: vi.fn(), priceScale: () => scale,
    createPriceLine: () => ({ applyOptions: vi.fn() }), removePriceLine: vi.fn() };
  return { ...actual,
    createChart: () => ({ applyOptions: vi.fn(), resize: vi.fn(), addSeries: () => series, panes: () => [], timeScale: () => scale,
      paneSize: () => ({ width: 0, height: 0 }),
      clearCrosshairPosition: vi.fn(),
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
  viewport.getVisibleRange.mockReset();
  viewport.setVisibleRange.mockClear();
  viewport.fitContent.mockClear();
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

it("loads a market chart without a bot or account and avoids warming unrelated timeframes", async () => {
  const start = vi.spyOn(api.botsApi, "startTopBot");
  const create = vi.spyOn(api.botsApi, "createConfig");
  render(<BotSignalChart bot={null} market={{
    contract_id: "CON.F.US.MNQ.U26", symbol: "MNQ", timeframe_unit: "minute", timeframe_unit_number: 5, lookback_bars: 300,
  }} authenticatedCacheScope="market-test" activity={null} lastEvaluation={null} refreshToken={0} />);
  await settle();
  expect(screen.getByText("MNQ / 5m · Market data only")).not.toBeNull();
  expect(api.botsApi.getCandles).toHaveBeenCalled();
  for (const [query] of vi.mocked(api.botsApi.getCandles).mock.calls) {
    expect(query.contractId).toBe("CON.F.US.MNQ.U26");
    expect(query.unit).toBe("minute");
    expect(query.unitNumber).toBe(5);
  }
  expect(start).not.toHaveBeenCalled();
  expect(create).not.toHaveBeenCalled();
});

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

it("updates the account-free forming candle from streamed trades before the next REST poll", async () => {
  vi.setSystemTime(new Date("2026-09-08T14:01:00Z"));
  vi.mocked(api.botsApi.getCandles).mockImplementation(async (query) => query.includePartialBar ? [] : [{
    id: null, contract_id: bot.contract_id, symbol: bot.symbol, live: false, unit: "minute", unit_number: 5,
    timestamp: "2026-09-08T13:55:00Z", open: 19999, high: 20001, low: 19998, close: 20000,
    volume: 100, is_partial: false, fetched_at: null,
  }]);
  render(<BotSignalChart bot={null} market={bot} authenticatedCacheScope="live-price-test"
    activity={null} lastEvaluation={null} refreshToken={0} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  const callbacks = vi.mocked(api.streamProjectXMarketPrice).mock.calls.at(-1)![1];
  const pollCount = vi.mocked(api.botsApi.getCandles).mock.calls.length;
  chartSeries.update.mockClear();
  await act(async () => {
    callbacks.onPrice({ contract_id: bot.contract_id, symbol: bot.symbol, price: 20002.25,
      timestamp: "2026-09-08T14:01:00Z" });
    await vi.advanceTimersByTimeAsync(250);
  });
  expect(screen.getByText("Live stream")).not.toBeNull();
  expect(chartSeries.update).toHaveBeenCalledWith(expect.objectContaining({ close: 20002.25 }));
  expect(api.botsApi.getCandles).toHaveBeenCalledTimes(pollCount);
  await act(async () => {
    callbacks.onPrice({ contract_id: bot.contract_id, symbol: bot.symbol, price: 20003,
      timestamp: "2026-09-08T14:01:01Z" });
    await vi.advanceTimersByTimeAsync(250);
  });
  expect(chartSeries.update).toHaveBeenCalledWith(expect.objectContaining({ close: 20003, high: 20003 }));
  expect(api.botsApi.getCandles).toHaveBeenCalledTimes(pollCount);
});

it("loads older candles with a legacy symbol label for the exact same contract", async () => {
  vi.setSystemTime(new Date("2026-09-08T14:01:00Z"));
  const candle = (timestamp: string, symbol: string): ProjectXMarketCandle => ({
    id: null, contract_id: bot.contract_id, symbol, live: false, unit: "minute", unit_number: 5,
    timestamp, open: 20000, high: 20001, low: 19999, close: 20000, volume: 100,
    is_partial: false, fetched_at: null,
  });
  const earliest = candle("2026-09-06T22:00:00Z", "F.US.MNQ");
  const older = candle("2026-09-04T19:00:00Z", "MNQ");
  vi.mocked(api.botsApi.getCandles).mockImplementation(async query => {
    if (query.includePartialBar) return [];
    if (query.end && Date.parse(query.end) < Date.parse(earliest.timestamp)) {
      return [older,
        { ...older, contract_id: "CON.F.US.MES.U26", symbol: "MES" },
        { ...older, unit_number: 1 },
        { ...older, live: true },
      ];
    }
    return [earliest, candle("2026-09-08T13:55:00Z", "F.US.MNQ")];
  });
  const onMarketData = vi.fn();
  render(<BotSignalChart bot={null} market={bot} authenticatedCacheScope="legacy-history-test"
    activity={null} lastEvaluation={null} refreshToken={0} onMarketData={onMarketData} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  fireEvent.click(screen.getByRole("button", { name: "Load older" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  const snapshot = onMarketData.mock.calls.at(-1)![0];
  expect(snapshot.candles).toContainEqual(older);
  expect(snapshot.candles).toHaveLength(3);
  expect(screen.getByRole("button", { name: "Load older" }).hasAttribute("disabled")).toBe(false);
});

it("preserves the visible dates from 15m to 1H and waits for history before setting zoom", async () => {
  vi.setSystemTime(new Date("2026-09-08T14:01:00Z"));
  const makeCandle = (timestamp: string, unit: "minute" | "hour", unitNumber: number): ProjectXMarketCandle => ({
    id: null, contract_id: bot.contract_id, symbol: bot.symbol, live: false, unit, unit_number: unitNumber,
    timestamp, open: 20000, high: 20001, low: 19999, close: 20000, volume: 100,
    is_partial: false, fetched_at: null,
  });
  let finishHourlyHistory!: (rows: ProjectXMarketCandle[]) => void;
  const hourlyHistory = new Promise<ProjectXMarketCandle[]>(resolve => { finishHourlyHistory = resolve; });
  vi.mocked(api.botsApi.getCandles).mockImplementation(async query => {
    if (query.unit === "hour") {
      return query.includePartialBar
        ? [makeCandle("2026-09-08T13:00:00Z", "hour", 1)]
        : hourlyHistory;
    }
    if (query.includePartialBar) return [];
    return [makeCandle("2026-09-04T14:00:00Z", "minute", 15), makeCandle("2026-09-08T13:45:00Z", "minute", 15)];
  });
  render(<BotSignalChart bot={null} market={{ ...bot, timeframe_unit_number: 15 }}
    authenticatedCacheScope="timeframe-zoom-test" activity={null} lastEvaluation={null} refreshToken={0} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  const visibleDates = { from: Date.parse("2026-09-04T14:00:00Z") / 1000, to: Date.parse("2026-09-08T14:00:00Z") / 1000 };
  viewport.getVisibleRange.mockReturnValue(visibleDates);
  viewport.fitContent.mockClear();
  fireEvent.click(screen.getByRole("button", { name: "Show 1H candles" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  expect(viewport.fitContent).not.toHaveBeenCalled();
  expect(viewport.setVisibleRange).not.toHaveBeenCalled();
  await act(async () => {
    finishHourlyHistory([makeCandle("2026-09-04T14:00:00Z", "hour", 1), makeCandle("2026-09-08T13:00:00Z", "hour", 1)]);
    await vi.advanceTimersByTimeAsync(100);
  });
  expect(viewport.setVisibleRange).toHaveBeenCalledExactlyOnceWith(visibleDates);
  expect(viewport.fitContent).not.toHaveBeenCalled();
  await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
  expect(viewport.setVisibleRange).toHaveBeenCalledTimes(1);
});

function gapFixture(unit: "hour" | "day", unitNumber: number) {
  const step = (unit === "day" ? 24 : unitNumber) * 3_600_000;
  const start = Date.parse("2026-08-03T00:00:00Z");
  const full: ProjectXMarketCandle[] = [];
  for (let index = 0; full.length < 24; index += 1) {
    const time = start + index * step;
    const weekday = new Date(time).getUTCDay();
    if (unit === "day" ? weekday === 0 || weekday === 6 : !isFuturesSessionOpen(time, "MNQ")) continue;
    full.push({ id: null, contract_id: bot.contract_id, symbol: bot.symbol, live: false, unit, unit_number: unitNumber,
      timestamp: new Date(time).toISOString(), open: 20000, high: 20001, low: 19999, close: 20000,
      volume: 100, is_partial: false, fetched_at: null });
  }
  const sparse = full.filter((_, index) => ![2, 8, 14, 20].includes(index));
  return { full, sparse };
}

it.each([{ unit: "hour" as const, unitNumber: 4 }, { unit: "day" as const, unitNumber: 1 }])(
  "automatically repairs every $unit/$unitNumber gap in paced batches without Backfill", async ({ unit, unitNumber }) => {
    vi.setSystemTime(new Date("2026-09-08T14:01:00Z"));
    const { full, sparse } = gapFixture(unit, unitNumber);
    expect(findCandleGaps(sparse, unit, unitNumber).filter(gap => gap.kind === "data")).toHaveLength(4);
    const repairs: number[] = [];
    vi.mocked(api.botsApi.getCandles).mockImplementation(async query => {
      if (query.includePartialBar) return [];
      if (query.repair) {
        repairs.push(Date.now());
        return full.filter(row => Date.parse(row.timestamp) >= Date.parse(query.start!) && Date.parse(row.timestamp) <= Date.parse(query.end!));
      }
      return sparse;
    });
    const onMarketData = vi.fn();
    render(<BotSignalChart bot={null} market={{ ...bot, timeframe_unit: unit, timeframe_unit_number: unitNumber }}
      authenticatedCacheScope={`auto-gaps-${unit}`} activity={null} lastEvaluation={null} refreshToken={0} onMarketData={onMarketData} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    // React commits history before it schedules the first automatic pass.
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(repairs.length).toBeGreaterThan(0);
    expect(repairs.length).toBeLessThanOrEqual(3);
    expect(screen.queryByRole("button", { name: "Backfill" })).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(16_000); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1_100); });
    expect(repairs).toHaveLength(4);
    expect(repairs[3] - repairs[2]).toBeGreaterThanOrEqual(15_000);
    const snapshot = onMarketData.mock.calls.at(-1)![0];
    expect(findCandleGaps(snapshot.candles, unit, unitNumber).filter(gap => gap.kind === "data")).toEqual([]);
  },
);

it("backs off failed automatic repairs and cancels queued work when leaving the chart", async () => {
  vi.setSystemTime(new Date("2026-09-08T14:01:00Z"));
  const { sparse } = gapFixture("day", 1);
  const repairs = vi.fn().mockRejectedValue(new Error("Rate limited"));
  vi.mocked(api.botsApi.getCandles).mockImplementation(async query => query.repair ? repairs() : query.includePartialBar ? [] : sparse);
  const chart = render(<BotSignalChart bot={null} market={{ ...bot, timeframe_unit: "day", timeframe_unit_number: 1 }}
    authenticatedCacheScope="auto-gaps-failure" activity={null} lastEvaluation={null} refreshToken={0} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  expect(repairs).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(29_000); });
  expect(repairs).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  expect(repairs).toHaveBeenCalledTimes(2);
  chart.unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
  expect(repairs).toHaveBeenCalledTimes(2);
});

it("reports provider-empty gaps and does not repeatedly request them", async () => {
  vi.setSystemTime(new Date("2026-09-08T14:01:00Z"));
  const { sparse } = gapFixture("day", 1);
  const repairs = vi.fn().mockResolvedValue([]);
  vi.mocked(api.botsApi.getCandles).mockImplementation(async query => query.repair ? repairs() : query.includePartialBar ? [] : sparse);
  render(<BotSignalChart bot={null} market={{ ...bot, timeframe_unit: "day", timeframe_unit_number: 1 }}
    authenticatedCacheScope="auto-gaps-empty" activity={null} lastEvaluation={null} refreshToken={0} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  await act(async () => { await vi.advanceTimersByTimeAsync(16_000); });
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  expect(repairs).toHaveBeenCalledTimes(4);
  expect(screen.getByText("4 gaps unavailable")).not.toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(repairs).toHaveBeenCalledTimes(4);
});
