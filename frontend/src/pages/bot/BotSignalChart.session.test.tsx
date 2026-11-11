// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "../../lib/api";
import type { BotConfig, ProjectXMarketCandle } from "../../lib/types";
import { BotSignalChart } from "./BotSignalChart";
import { findCandleGaps, isFuturesSessionOpen } from "./botCandleGaps";
import { buildBotCandleCacheKey, writeBotCandleCache } from "./botCandleCache";
import { getDemoApiResponse } from "../../lib/demoData";
import { DEMO_AS_OF_ISO } from "../../lib/demoScenario";
import { STREAM_HIDDEN_GRACE_MS } from "./useStreamVisibility";
import type { BotConfigListResponse } from "../../lib/types";

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
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  viewport.getVisibleRange.mockReset();
  viewport.setVisibleRange.mockClear();
  viewport.fitContent.mockClear();
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-04T21:05:00Z"));
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.spyOn(api.botsApi, "getCandles").mockResolvedValue([]);
  vi.spyOn(api, "requestJson").mockResolvedValue({ timeframe: "5m", aggregate_size: 1,
    coverage_start: "2019-08-01T00:00:00Z", coverage_end: "2026-09-04T21:00:00Z", candles: [
      { time: Date.parse("2026-09-04T20:55:00Z") / 1000, end_time: Date.parse("2026-09-04T21:00:00Z") / 1000,
        open: 100, high: 110, low: 95, close: 105, volume: 1000, contract: "MNQU6", count: 1 },
    ] });
  vi.spyOn(api.botsApi, "getActivity").mockResolvedValue({config: bot, runs: [], decisions: [], order_attempts: [], risk_events: []});
  vi.spyOn(api, "streamProjectXMarketPrice").mockReturnValue(vi.fn());
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });
const mount = () => render(<BotSignalChart bot={bot} authenticatedCacheScope="session-test" activity={null}
  lastEvaluation={null} refreshToken={0} />);
const settle = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(5_000); }); };

it("uses continuous history for daily and intraday monitor charts", async () => {
  const history = vi.spyOn(api, "requestJson").mockResolvedValue({ timeframe: "1d", aggregate_size: 1, candles: [
    { time: Date.parse("2025-09-01T22:00:00Z") / 1000, end_time: Date.parse("2025-09-02T22:00:00Z") / 1000,
      open: 100, high: 110, low: 95, close: 105, volume: 1000, contract: "MNQU5", count: 1 },
  ] });
  const snapshot = vi.fn();
  render(<BotSignalChart monitorOnly bot={bot} authenticatedCacheScope="continuous-test" activity={null}
    lastEvaluation={null} refreshToken={0} onMarketData={snapshot} />);
  await settle();
  vi.mocked(api.botsApi.getCandles).mockClear();
  fireEvent.click(screen.getByRole("button", { name: "Show 1D candles" }));
  await settle();
  expect(history).toHaveBeenCalledWith("/api/market-data/databento/candles", expect.objectContaining({ query: expect.objectContaining({ timeframe: "1d" }) }));
  expect(screen.getByLabelText("Continuous MNQ daily candlestick and volume chart")).not.toBeNull();
  expect(api.botsApi.getCandles).not.toHaveBeenCalled();
  expect(snapshot).toHaveBeenLastCalledWith(null);
  fireEvent.click(screen.getByRole("button", { name: "Show 1H candles" }));
  await settle();
  expect(screen.getByRole("region", { name: "MNQ continuous / 1H candlestick and volume chart" })).not.toBeNull();
  expect(history).toHaveBeenCalledWith("/api/market-data/databento/candles", expect.objectContaining({ query: expect.objectContaining({ timeframe: "5m" }) }));
  expect(vi.mocked(api.botsApi.getCandles).mock.calls.every(([query]) => query.contractId === bot.contract_id && query.unit === "hour")).toBe(true);
});

it("keeps the monitor chart free of drawing tools and redundant timeframe warming", async () => {
  vi.mocked(api.botsApi.getCandles).mockResolvedValue([{ id: 1, contract_id: bot.contract_id, symbol: bot.symbol,
    unit: "minute", unit_number: 5, timestamp: "2026-09-04T20:55:00Z", open: 100, high: 101, low: 99,
    close: 100, volume: 10, is_partial: false, live: false, fetched_at: "2026-09-04T21:00:00Z" }]);
  render(<BotSignalChart monitorOnly bot={bot} authenticatedCacheScope="monitor-test" activity={null}
    lastEvaluation={null} refreshToken={0} />);
  await settle();
  await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
  expect(screen.queryByLabelText("Chart drawing tools")).toBeNull();
  expect(screen.queryByText("Stale candles")).toBeNull();
  expect(screen.getByText("Market closed")).not.toBeNull();
  expect(vi.mocked(api.botsApi.getCandles).mock.calls.every(([query]) => query.unit === "minute" && query.unitNumber === 5)).toBe(true);
});

it("loads bounded signal history for the chart range and selected execution mode", async () => {
  vi.mocked(api.botsApi.getCandles).mockResolvedValue([{id: 1, contract_id: bot.contract_id, symbol: bot.symbol,
    unit: "minute", unit_number: 5, timestamp: "2026-09-04T20:55:00Z", open: 100, high: 101, low: 99,
    close: 100, volume: 10, is_partial: false, live: false, fetched_at: "2026-09-04T21:00:00Z"}]);
  mount();
  await settle();
  expect(api.botsApi.getActivity).toHaveBeenCalledWith(1, 2000, expect.anything(), expect.objectContaining({
    signals_only: true, start: "2026-09-04T20:55:00Z", end: "2026-09-04T21:00:00.000Z"}));
  fireEvent.change(screen.getByLabelText("Signal execution mode"), {target: {value: "live"}});
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(api.botsApi.getActivity).toHaveBeenLastCalledWith(1, 2000, expect.anything(), expect.objectContaining({execution_mode: "live"}));
});

it("keeps demo history and snapshot time anchored when the real date advances", async () => {
  vi.setSystemTime(new Date("2027-02-15T16:00:00Z"));
  const demoBot = getDemoApiResponse<BotConfigListResponse>("/api/bots", { account_id: 910001 })!.data.items[0];
  vi.mocked(api.botsApi.getCandles).mockImplementation(async query => getDemoApiResponse<ProjectXMarketCandle[]>(
    "/api/projectx/candles", { contract_id: query.contractId, symbol: query.symbol,
      start: query.start, end: query.end, unit: query.unit, unit_number: query.unitNumber, limit: query.limit },
  )!.data);
  const onMarketData = vi.fn();
  render(<BotSignalChart bot={demoBot} authenticatedCacheScope="demo-clock-test" demoMode
    activity={null} lastEvaluation={null} refreshToken={0} onMarketData={onMarketData} />);
  await settle();
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(api.botsApi.getCandles).toHaveBeenCalled();
  expect(vi.mocked(api.botsApi.getCandles).mock.calls.every(([query]) => Date.parse(query.end!) <= Date.parse(DEMO_AS_OF_ISO))).toBe(true);
  const snapshot = onMarketData.mock.calls.at(-1)![0];
  expect(snapshot.candles.length).toBeGreaterThan(0);
  expect(snapshot.updatedAt).toBe(DEMO_AS_OF_ISO);
  expect(snapshot.strategyType).toBe(demoBot.strategy_type);
  expect(api.streamProjectXMarketPrice).not.toHaveBeenCalled();
});

it("stops chart polling while hidden, keeps the stream through a brief hide, and refreshes on return", async () => {
  vi.setSystemTime(new Date("2026-09-08T14:01:00Z"));
  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  mount();
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  const stop = vi.mocked(api.streamProjectXMarketPrice).mock.results[0].value;
  act(() => { hidden.mockReturnValue(true); document.dispatchEvent(new Event("visibilitychange")); });
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  const callsWhileHidden = vi.mocked(api.botsApi.getCandles).mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  // REST polling pauses at once; the stream survives a brief tab switch.
  expect(api.botsApi.getCandles).toHaveBeenCalledTimes(callsWhileHidden);
  expect(stop).not.toHaveBeenCalled();
  act(() => { hidden.mockReturnValue(false); document.dispatchEvent(new Event("visibilitychange")); });
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(vi.mocked(api.botsApi.getCandles).mock.calls.length).toBeGreaterThan(callsWhileHidden);
  expect(api.streamProjectXMarketPrice).toHaveBeenCalledTimes(1);
  // A long absence releases the stream; returning opens exactly one new one.
  act(() => { hidden.mockReturnValue(true); document.dispatchEvent(new Event("visibilitychange")); });
  await act(async () => { await vi.advanceTimersByTimeAsync(STREAM_HIDDEN_GRACE_MS); });
  expect(stop).toHaveBeenCalledTimes(1);
  act(() => { hidden.mockReturnValue(false); document.dispatchEvent(new Event("visibilitychange")); });
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(api.streamProjectXMarketPrice).toHaveBeenCalledTimes(2);
});

it("defers automatic gap repairs while the page is hidden", async () => {
  vi.setSystemTime(new Date("2026-09-08T14:01:00Z"));
  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
  const { sparse } = gapFixture("day", 1);
  vi.mocked(api.botsApi.getCandles).mockResolvedValue(sparse);
  render(<BotSignalChart bot={null} market={{ ...bot, timeframe_unit: "day", timeframe_unit_number: 1 }}
    authenticatedCacheScope="hidden-repairs" activity={null} lastEvaluation={null} refreshToken={0} />);
  await settle();
  expect(api.botsApi.getCandles).not.toHaveBeenCalled();
  act(() => { hidden.mockReturnValue(false); document.dispatchEvent(new Event("visibilitychange")); });
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  expect(vi.mocked(api.botsApi.getCandles).mock.calls.some(([query]) => query.repair)).toBe(true);
});

it("renders TopBot's VWAP context without moving-average legends", () => {
  render(<BotSignalChart bot={{ ...bot, strategy_type: "topbot_adaptive" }} authenticatedCacheScope="topbot-indicators"
    activity={null} lastEvaluation={null} refreshToken={0} demoMode />);
  expect(screen.queryByText("EMA 20")).toBeNull();
  expect(screen.getByText("VWAP · context only")).not.toBeNull();
  expect(screen.queryByText("Fast EMA 9")).toBeNull();
  expect(screen.queryByText("Fast SMA 9")).toBeNull();
});

it("loads a market chart without a bot or account and avoids warming unrelated timeframes", async () => {
  const start = vi.spyOn(api.botsApi, "startTopBot");
  const create = vi.spyOn(api.botsApi, "createConfig");
  render(<BotSignalChart bot={null} market={{
    contract_id: "CON.F.US.MNQ.U26", symbol: "MNQ", timeframe_unit: "minute", timeframe_unit_number: 5, lookback_bars: 300,
  }} authenticatedCacheScope="market-test" activity={null} lastEvaluation={null} refreshToken={0} />);
  await settle();
  expect(screen.getByText("MNQU26 / 5m · Market data only")).not.toBeNull();
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
// Multiple calendar jumps drain chart timers; allow contention from other test
// workers without timing out mid-act and contaminating the following cases.
}, 15_000);

it("keeps an explicit chart Refresh available during a closure", async () => {
  mount();
  await settle();
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await settle();
  expect(vi.mocked(api.botsApi.getCandles).mock.calls.some(([query]) => query.includePartialBar && query.refresh)).toBe(true);
  expect(api.streamProjectXMarketPrice).not.toHaveBeenCalled();
});

it("replaces an old weekend chart tail with Friday's close and publishes it to analysis", async () => {
  vi.setSystemTime(new Date("2026-10-31T11:00:00Z"));
  const scope = "weekend-catchup";
  const market = { ...bot, contract_id: "CON.F.US.MNQ.Z26", lookback_bars: 300 };
  const old: ProjectXMarketCandle = {
    id: null, contract_id: market.contract_id, symbol: "MNQ", live: false,
    unit: "minute", unit_number: 5, timestamp: "2026-10-21T16:40:00Z",
    open: 100, high: 102, low: 99, close: 101, volume: 100,
    is_partial: false, fetched_at: null,
  };
  const latest = { ...old, timestamp: "2026-10-30T20:55:00Z" };
  writeBotCandleCache(buildBotCandleCacheKey({
    userScope: scope, contractId: market.contract_id, symbol: market.symbol,
    live: false, unit: "minute", unitNumber: 5,
  }), [old], 1200, {
    savedAt: new Date("2026-10-21T16:45:00Z"),
    coverage: { start: "2026-10-18T00:00:00Z", end: "2026-10-31T11:00:00Z" },
  });
  vi.mocked(api.botsApi.getCandles).mockResolvedValue([latest]);
  const onMarketData = vi.fn();
  render(<BotSignalChart bot={null} market={market} authenticatedCacheScope={scope}
    activity={null} lastEvaluation={null} refreshToken={0} onMarketData={onMarketData} />);
  await settle();
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(api.botsApi.getCandles).toHaveBeenCalled();
  expect(onMarketData.mock.calls.at(-1)![0].candles.at(-1).timestamp).toBe(latest.timestamp);
  expect(api.streamProjectXMarketPrice).not.toHaveBeenCalled();
  expect(vi.mocked(api.botsApi.getCandles).mock.calls.some(([query]) => query.includePartialBar)).toBe(false);
  const count = vi.mocked(api.botsApi.getCandles).mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(api.botsApi.getCandles).toHaveBeenCalledTimes(count);
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

function gapFixture(unit: "hour" | "day", unitNumber: number, spacing = 6) {
  const step = (unit === "day" ? 24 : unitNumber) * 3_600_000;
  const start = Date.parse("2026-08-03T00:00:00Z");
  const full: ProjectXMarketCandle[] = [];
  for (let index = 0; full.length < spacing * 4; index += 1) {
    const time = start + index * step;
    const weekday = new Date(time).getUTCDay();
    if (unit === "day" ? weekday === 0 || weekday === 6 : !isFuturesSessionOpen(time, "MNQ")) continue;
    full.push({ id: null, contract_id: bot.contract_id, symbol: bot.symbol, live: false, unit, unit_number: unitNumber,
      timestamp: new Date(time).toISOString(), open: 20000, high: 20001, low: 19999, close: 20000,
      volume: 100, is_partial: false, fetched_at: null });
  }
  const sparse = full.filter((_, index) => ![2, 2 + spacing, 2 + spacing * 2, 2 + spacing * 3].includes(index));
  return { full, sparse };
}

it.each([{ unit: "hour" as const, unitNumber: 4 }, { unit: "hour" as const, unitNumber: 1 }])(
  "automatically repairs every $unit/$unitNumber gap in paced batches without Backfill", async ({ unit, unitNumber }) => {
    vi.setSystemTime(new Date("2026-09-08T14:01:00Z"));
    const { full, sparse } = gapFixture(unit, unitNumber, 30);
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
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1_100); });
    expect(repairs).toHaveLength(4);
    expect(repairs[3] - repairs[2]).toBeGreaterThanOrEqual(5_000);
    expect(repairs[3] - repairs[2]).toBeLessThan(6_000);
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
  expect(repairs).toHaveBeenCalledTimes(1);
  expect(screen.getByText("4 gaps unavailable")).not.toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(repairs).toHaveBeenCalledTimes(1);
});

const healthyFeed = (patch: Partial<api.MarketPriceStreamHealth> = {}): api.MarketPriceStreamHealth => ({
  transport: "open", upstream: "connected", lastActivityAt: Date.now(), lastConfirmedAt: Date.now(),
  upstreamIdleMs: 500, serverClockOffsetMs: 0, heartbeats: true, reconnects: 0,
  lastInterruption: null, retryAt: null, ...patch,
});
const closedBar = (): ProjectXMarketCandle => ({
  id: null, contract_id: bot.contract_id, symbol: bot.symbol, live: false, unit: "minute", unit_number: 5,
  timestamp: "2026-09-08T13:55:00Z", open: 19999, high: 20001, low: 19998, close: 20000,
  volume: 100, is_partial: false, fetched_at: null,
});
const mountLive = async (scope: string) => {
  render(<BotSignalChart bot={null} market={bot} authenticatedCacheScope={scope}
    activity={null} lastEvaluation={null} refreshToken={0} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  return vi.mocked(api.streamProjectXMarketPrice).mock.calls.at(-1)![1];
};
const trade = (price: number, timestamp = new Date().toISOString()) =>
  ({ contract_id: bot.contract_id, symbol: bot.symbol, price, timestamp });

it("keeps a healthy stream live through quiet tape and slows the redundant REST quote poll", async () => {
  vi.setSystemTime(new Date("2026-09-08T14:01:00Z"));
  vi.mocked(api.botsApi.getCandles).mockImplementation(async (query) => query.includePartialBar ? [] : [closedBar()]);
  const callbacks = await mountLive("quiet-tape");
  await act(async () => {
    callbacks.onHealth?.(healthyFeed());
    callbacks.onPrice(trade(20002.25));
    await vi.advanceTimersByTimeAsync(250);
  });
  expect(screen.getByText("Live stream")).not.toBeNull();
  const polls = () => vi.mocked(api.botsApi.getCandles).mock.calls.filter(([query]) => query.includePartialBar).length;
  const pollsBefore = polls();
  // 45 seconds without a trade; heartbeats keep confirming the path every 5s.
  for (let index = 0; index < 9; index += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
      callbacks.onHealth?.(healthyFeed());
    });
  }
  expect(screen.queryByText("Stale price feed")).toBeNull();
  expect(screen.queryByText("Delayed / polling")).toBeNull();
  expect(screen.getByText("Live · quiet")).not.toBeNull();
  expect(screen.getByText("Last 20,002.25")).not.toBeNull();
  // The stream carries the price, so REST only reconciles volume (~15s, not 3s).
  expect(polls() - pollsBefore).toBeLessThanOrEqual(4);
});

it("keeps the monitor chart's quote fallback working when the continuous archive is missing", async () => {
  vi.setSystemTime(new Date("2026-09-08T14:01:00Z"));
  vi.spyOn(api, "requestJson").mockRejectedValue(new api.ApiError("databento_chart_history_missing", 404, null, null));
  vi.mocked(api.botsApi.getCandles).mockImplementation(async (query) => query.includePartialBar ? [{
    ...closedBar(), timestamp: "2026-09-08T14:00:00Z", close: 20010.5, is_partial: true, fetched_at: new Date().toISOString(),
  }] : []);
  render(<BotSignalChart monitorOnly bot={bot} authenticatedCacheScope="archive-missing" activity={null}
    lastEvaluation={null} refreshToken={0} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
  expect(vi.mocked(api.botsApi.getCandles).mock.calls.some(([query]) =>
    query.includePartialBar && query.contractId === bot.contract_id)).toBe(true);
  expect(screen.getByText("Delayed / polling")).not.toBeNull();
  expect(screen.getByText("Delayed 20,010.50")).not.toBeNull();
});

it("shows Reconnecting after a drop, keeps the last trade, and reports stale only after the grace", async () => {
  vi.setSystemTime(new Date("2026-09-08T14:01:00Z"));
  vi.mocked(api.botsApi.getCandles).mockImplementation(async (query) => query.includePartialBar ? [] : [closedBar()]);
  const callbacks = await mountLive("drop-recovery");
  await act(async () => {
    callbacks.onHealth?.(healthyFeed());
    callbacks.onPrice(trade(20002.25));
    await vi.advanceTimersByTimeAsync(250);
  });
  const lastGood = Date.now();
  await act(async () => {
    callbacks.onHealth?.(healthyFeed({ transport: "retrying", upstream: null, lastActivityAt: lastGood,
      lastConfirmedAt: lastGood, reconnects: 1, lastInterruption: "network_error", retryAt: Date.now() + 1_000 }));
  });
  expect(screen.getByText("Reconnecting")).not.toBeNull();
  expect(screen.getByText("Last 20,002.25")).not.toBeNull();
  expect(screen.queryByText("Stale price feed")).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(31_000); });
  expect(screen.getByText("Stale price feed")).not.toBeNull();
  expect(screen.getByText("Stale 20,002.25")).not.toBeNull();
  await act(async () => {
    callbacks.onHealth?.(healthyFeed({ reconnects: 1, lastInterruption: "network_error" }));
    callbacks.onPrice(trade(20004));
    await vi.advanceTimersByTimeAsync(250);
  });
  expect(screen.getByText("Live stream")).not.toBeNull();
  expect(screen.getByText("Live 20,004.00")).not.toBeNull();
});

it("never lets an older or repeated trade replace a newer one", async () => {
  vi.setSystemTime(new Date("2026-09-08T14:01:00Z"));
  vi.mocked(api.botsApi.getCandles).mockImplementation(async (query) => query.includePartialBar ? [] : [closedBar()]);
  const callbacks = await mountLive("ordering");
  await act(async () => {
    callbacks.onHealth?.(healthyFeed());
    callbacks.onPrice(trade(20003, "2026-09-08T14:00:59Z"));
    await vi.advanceTimersByTimeAsync(250);
    callbacks.onPrice(trade(20001, "2026-09-08T14:00:55Z"));
    callbacks.onPrice(trade(20003, "2026-09-08T14:00:59Z"));
    await vi.advanceTimersByTimeAsync(250);
  });
  expect(screen.getByText("Live 20,003.00")).not.toBeNull();
  expect(chartSeries.update).not.toHaveBeenCalledWith(expect.objectContaining({ close: 20001 }));
});
