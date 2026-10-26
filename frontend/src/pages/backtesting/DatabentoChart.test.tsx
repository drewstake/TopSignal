// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { requestJson } from "../../lib/api";
import { createChart } from "lightweight-charts";
import { APP_SCALE_CHANGED_EVENT, getAppScale } from "../../lib/appScale";
import { DatabentoChart } from "./DatabentoChart";
import { logicalToTime, mergeHistoryDetail, type ChartWindow } from "./historyChartData";

const harness = vi.hoisted(() => ({
  range: { from: 0, to: 10 },
  onRange: undefined as undefined | ((range: { from: number; to: number }) => void),
  setData: vi.fn(),
  resize: vi.fn(),
}));
vi.mock("../../lib/api", () => ({ requestJson: vi.fn() }));
vi.mock("../../lib/appScale", () => ({ APP_SCALE_CHANGED_EVENT: "topsignal-scale-changed", getAppScale: vi.fn(() => 1) }));
vi.mock("lightweight-charts", () => ({
  CandlestickSeries: "candles", HistogramSeries: "volume", ColorType: { Solid: "solid" }, CrosshairMode: { Normal: 0 },
  createChart: vi.fn(() => ({
    addSeries: () => ({ setData: harness.setData, applyOptions: vi.fn(), priceScale: () => ({ applyOptions: vi.fn() }) }),
    timeScale: () => ({
      getVisibleLogicalRange: () => harness.range,
      fitContent: () => { harness.range = { from: 0, to: 10 }; harness.onRange?.(harness.range); },
      setVisibleLogicalRange: (range: { from: number; to: number }) => { harness.range = range; harness.onRange?.(range); },
      subscribeVisibleLogicalRangeChange: (callback: typeof harness.onRange) => { harness.onRange = callback; },
    }),
    subscribeCrosshairMove: vi.fn(), applyOptions: vi.fn(), remove: vi.fn(), resize: harness.resize,
  })),
}));
const history: ChartWindow = { symbol: "MNQ", source: "Databento", timeframe: "5m", coverage_start: "1970-01-01T00:00:00Z", coverage_end: "1970-01-02T03:46:40Z", start: "1970-01-01T00:00:00Z", end: "1970-01-02T03:46:40Z", total: 20000, truncated: false, aggregate_size: 10000,
  candles: Array.from({ length: 11 }, (_, index) => index * 10000).map(time => ({ time, end_time: time + 10000, count: 10000, open: 1, high: 2, low: 1, close: 2, volume: 10000, contract: "MNQU6" })) };
const detail: ChartWindow = { ...history, start: new Date(27500000).toISOString(), end: new Date(72500000).toISOString(), total: 1, aggregate_size: 1,
  candles: [{ ...history.candles[0], time: 40000, end_time: 40300, count: 1, volume: 1 }] };

beforeEach(() => {
  vi.useFakeTimers();
  harness.range = { from: 0, to: 10 };
  harness.onRange = undefined;
  vi.mocked(getAppScale).mockReturnValue(1);
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.clearAllMocks(); });

it("loads individual candles on zoom and does not refetch from its own viewport restoration", async () => {
  vi.mocked(requestJson).mockResolvedValue(detail);
  render(<DatabentoChart history={history} initialView="overview" />);
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(requestJson).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/Individual 5m candles/)).toBeTruthy();
  const merged = mergeHistoryDetail(history.candles, detail);
  expect(logicalToTime(merged, harness.range.from)).toBeCloseTo(38500);
  expect(logicalToTime(merged, harness.range.to)).toBeCloseTo(61500);
});

it("ignores a late zoom response after the user returns to the full range", async () => {
  let resolve!: (data: ChartWindow) => void;
  vi.mocked(requestJson).mockImplementationOnce(() => new Promise<ChartWindow>(done => { resolve = done; }));
  render(<DatabentoChart history={history} initialView="overview" />);
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  fireEvent.click(screen.getByRole("button", { name: "History overview" }));
  await act(async () => { resolve(detail); await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByText(/Overview.*up to 10,000/)).toBeTruthy();
  expect(harness.range).toEqual({ from: 0, to: 10 });
  expect(screen.queryByText(/Individual 5m candles/)).toBeNull();
});

it.each([0.6, 0.8, 1, 1.25, 1.5])("keeps pointer and drawing pixels aligned at %s app scale", (scale) => {
  vi.mocked(getAppScale).mockReturnValue(scale);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ width: 1000 * scale, height: 520 * scale } as DOMRect);
  render(<DatabentoChart history={history} initialView="overview" />);
  const surface = screen.getByRole("img").firstElementChild as HTMLElement;
  expect(Number(surface.style.zoom) * scale).toBeCloseTo(1);
  expect(createChart).toHaveBeenCalledWith(surface, expect.objectContaining({ width: 1000 * scale, height: 520 * scale }));
  // A pointer 75% across the displayed chart also lands 75% across the canvas.
  const pointerOffset = 750 * scale;
  expect(pointerOffset / Number.parseFloat(surface.style.width)).toBeCloseTo(0.75);
});

it("remeasures the surface when app zoom changes without losing the time range", () => {
  vi.mocked(getAppScale).mockReturnValue(0.8);
  const measure = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ width: 800, height: 416 } as DOMRect);
  render(<DatabentoChart history={history} initialView="overview" />);
  harness.range = { from: -4.25, to: 15.75 };
  vi.mocked(getAppScale).mockReturnValue(1.25);
  measure.mockReturnValue({ width: 1250, height: 650 } as DOMRect);
  fireEvent(window, new Event(APP_SCALE_CHANGED_EVENT));
  expect((screen.getByRole("img").firstElementChild as HTMLElement).style.zoom).toBe("0.8");
  expect(harness.resize).toHaveBeenLastCalledWith(1250, 650);
  expect(harness.range).toEqual({ from: -4.25, to: 15.75 });
});


it("keeps zooming out past the full history without automatically fitting content", async () => {
  render(<DatabentoChart history={history} initialView="overview" />);
  fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
  const requested = { ...harness.range };
  expect(requested.from).toBeLessThan(0);
  expect(requested.to).toBeGreaterThan(10);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(harness.range).toEqual(requested);
  expect(requestJson).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "History overview" }));
  expect(harness.range).toEqual({ from: 0, to: 10 });
});

it("preserves edge whitespace when candle detail arrives", async () => {
  vi.mocked(requestJson).mockResolvedValue(detail);
  render(<DatabentoChart history={history} initialView="overview" />);
  fireEvent.wheel(screen.getByRole("img").firstElementChild!);
  act(() => {
    harness.range = { from: -2.25, to: 5.5 };
    harness.onRange?.(harness.range);
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  const merged = mergeHistoryDetail(history.candles, detail);
  expect(logicalToTime(merged, harness.range.from)).toBeCloseTo(-22500);
  expect(logicalToTime(merged, harness.range.to)).toBeCloseTo(55000);
  expect(requestJson).toHaveBeenCalledTimes(1);
});

it("accepts zoom-out input immediately after painting detail", async () => {
  vi.mocked(requestJson).mockResolvedValue(detail);
  render(<DatabentoChart history={history} initialView="overview" />);
  fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(250); });
  const merged = mergeHistoryDetail(history.candles, detail);
  fireEvent.wheel(screen.getByRole("img").firstElementChild!);
  act(() => {
    harness.range = { from: -2, to: merged.length + 2 };
    harness.onRange?.(harness.range);
  });
  const from = logicalToTime(merged, harness.range.from);
  const to = logicalToTime(merged, harness.range.to);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(logicalToTime(history.candles, harness.range.from)).toBeCloseTo(from);
  expect(logicalToTime(history.candles, harness.range.to)).toBeCloseTo(to);
  expect(screen.getByText(/Overview.*up to 10,000/)).toBeTruthy();
  expect(requestJson).toHaveBeenCalledTimes(1);
});


it.each(["1m", "5m", "4h", "1d"] as const)("opens %s on individual candles instead of the full-history aggregation", async (timeframe) => {
  const seconds = { "1m": 60, "5m": 300, "4h": 14400, "1d": 86400 }[timeframe];
  const end = 1792789200;
  const base = { ...history, timeframe, candles: history.candles.map((bar, i) => ({
    ...bar, time: end - (11 - i) * 10000000, end_time: end - (10 - i) * 10000000,
  })) };
  const exact = { ...detail, timeframe, aggregate_size: 1,
    start: new Date((end - 300 * seconds) * 1000).toISOString(), end: new Date(end * 1000).toISOString(),
    candles: Array.from({ length: 300 }, (_, i) => ({ ...detail.candles[0],
      time: end - (300 - i) * seconds, end_time: end - (299 - i) * seconds, count: 1,
    })),
  };
  vi.mocked(requestJson).mockResolvedValue(exact);
  render(<DatabentoChart history={base} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(requestJson).toHaveBeenCalledTimes(1);
  expect(requestJson).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
    query: expect.objectContaining({ timeframe, start: exact.start, end: exact.end }),
  }));
  expect(screen.getByText(`${timeframe} candles`)).toBeTruthy();
  const merged = exact.candles;
  expect(logicalToTime(merged, harness.range.from)).toBeCloseTo(exact.candles[0].time);
  expect(logicalToTime(merged, harness.range.to)).toBeCloseTo(exact.candles.at(-1)!.time);
  expect(harness.range.to - harness.range.from).toBe(299);
});

it("provides an explicit way back to actual timeframe candles from an overview", async () => {
  vi.mocked(requestJson).mockResolvedValue(detail);
  render(<DatabentoChart history={history} initialView="overview" />);
  expect(screen.getByText(/Grouped overview/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Show 5m candles" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByText("5m candles")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Show 5m candles" })).toBeNull();
});


it("ignores intermediate initialization events without cancelling the individual-candle request", async () => {
  let resolve!: (value: ChartWindow) => void;
  vi.mocked(requestJson).mockImplementationOnce(() => new Promise<ChartWindow>(done => { resolve = done; }));
  render(<DatabentoChart history={history} />);
  act(() => {
    harness.onRange?.({ from: 8, to: 10 });
    harness.onRange?.({ from: 0, to: 10 });
  });
  await act(async () => { resolve(detail); await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByText("5m candles")).toBeTruthy();
  expect(requestJson).toHaveBeenCalledTimes(1);
});


it("pans through minute candles without stitching in months of overview bars", async () => {
  const minuteHistory = { ...history, timeframe: "1m" as const };
  const minuteBars = (start: number, count: number) => Array.from({ length: count }, (_, i) => ({
    ...detail.candles[0], time: start + i * 60, end_time: start + (i + 1) * 60, count: 1,
  }));
  const first = { ...detail, timeframe: "1m" as const, aggregate_size: 1, candles: minuteBars(92000, 300) };
  const expanded = { ...first, candles: minuteBars(74000, 600) };
  vi.mocked(requestJson).mockResolvedValueOnce(first).mockResolvedValueOnce(expanded);
  render(<DatabentoChart history={minuteHistory} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(harness.range).toEqual({ from: 0, to: 299 });
  fireEvent.wheel(screen.getByRole("img").firstElementChild!);
  act(() => {
    harness.range = { from: -150, to: 150 };
    harness.onRange?.(harness.range);
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(requestJson).toHaveBeenLastCalledWith(expect.any(String), expect.objectContaining({
    query: expect.objectContaining({ timeframe: "1m", limit: 10000, start: new Date(78500000).toISOString() }),
  }));
  expect(logicalToTime(expanded.candles, harness.range.from)).toBe(83000);
  expect(logicalToTime(expanded.candles, harness.range.to)).toBe(101000);
  const priceData = harness.setData.mock.calls.filter(([data]) => data[0]?.open !== undefined).at(-1)![0];
  expect(priceData).toHaveLength(600);
  expect(priceData.every((bar: { count: number }) => bar.count === 1)).toBe(true);
  expect(screen.queryByText(/Grouped overview/)).toBeNull();
});

it("never switches minute mode into an overview when zoomed out across the entire date range", async () => {
  const exact = { ...detail, candles: Array.from({ length: 300 }, (_, i) => ({
    ...detail.candles[0], time: 92000 + i * 60, end_time: 92060 + i * 60,
  })) };
  vi.mocked(requestJson).mockResolvedValue(exact);
  render(<DatabentoChart history={{ ...history, timeframe: "1m" }} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  fireEvent.wheel(screen.getByRole("img").firstElementChild!);
  act(() => {
    harness.range = { from: -4000, to: 4000 };
    harness.onRange?.(harness.range);
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(requestJson).toHaveBeenCalledTimes(2);
  expect(requestJson).toHaveBeenLastCalledWith(expect.any(String), expect.objectContaining({
    query: expect.objectContaining({ timeframe: "1m", limit: 10000 }),
  }));
  expect(screen.getByText("1m candles")).toBeTruthy();
  expect(screen.queryByText(/Grouped overview/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "History overview" }));
  expect(screen.getByText(/Grouped overview/)).toBeTruthy();
});


it("keeps grouped prices off the exact chart if the initial request fails, and retries", async () => {
  vi.mocked(requestJson).mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(detail);
  render(<DatabentoChart history={history} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByRole("alert").textContent).toContain("Could not load individual");
  expect(harness.setData).not.toHaveBeenCalled();
  expect(screen.queryByLabelText("Candle values")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry candles" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByLabelText("Candle values")).toBeTruthy();
  expect(requestJson).toHaveBeenCalledTimes(2);
});

it("uses the requested anchor on timeframe changes, while Latest candles returns to the end", async () => {
  vi.mocked(requestJson).mockResolvedValue(detail);
  render(<DatabentoChart history={{ ...history, timeframe: "1m" }} initialAnchor={50000} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(requestJson).toHaveBeenLastCalledWith(expect.any(String), expect.objectContaining({
    query: expect.objectContaining({ start: new Date(41000000).toISOString(), end: new Date(59000000).toISOString() }),
  }));
  fireEvent.click(screen.getByRole("button", { name: "Latest candles" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(requestJson).toHaveBeenLastCalledWith(expect.any(String), expect.objectContaining({
    query: expect.objectContaining({ end: new Date(110000000).toISOString() }),
  }));
});

it("does not show the latest candle's OHLC when the visible area contains no data", async () => {
  vi.mocked(requestJson).mockResolvedValue(detail);
  render(<DatabentoChart history={history} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  fireEvent.wheel(screen.getByRole("img").firstElementChild!);
  act(() => {
    harness.range = { from: 300, to: 400 };
    harness.onRange?.(harness.range);
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.queryByLabelText("Candle values")).toBeNull();
  expect(requestJson).toHaveBeenCalledTimes(1);
});

it("retries a failed pan request without resetting the viewport", async () => {
  vi.mocked(requestJson).mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(detail);
  render(<DatabentoChart history={history} initialView="overview" />);
  fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  const before = { ...harness.range };
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByRole("alert")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Retry candles" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  const merged = mergeHistoryDetail(history.candles, detail);
  expect(logicalToTime(merged, harness.range.from)).toBeCloseTo(logicalToTime(history.candles, before.from));
  expect(screen.queryByRole("alert")).toBeNull();
});
