// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "../../lib/api";
import { LiquidityHeatmapPanel } from "./LiquidityHeatmapPanel";
import { OrderBookPanel } from "./OrderBookPanel";
import { OrderBookStore } from "./orderBook";
import { drawLiquidityHeatmap, type HeatmapRenderInput } from "./liquidityHeatmapCanvas";
import type { HistoryFetcher, RawHeatmapHistory } from "./heatmapHistory";
import { STREAM_HIDDEN_GRACE_MS } from "./useStreamVisibility";

vi.mock("./liquidityHeatmapCanvas", async importOriginal => ({
  ...await importOriginal<typeof import("./liquidityHeatmapCanvas")>(),
  drawLiquidityHeatmap: vi.fn((_canvas: HTMLCanvasElement, input: HeatmapRenderInput) => ({ left: 0, right: 500, top: 0, bottom: 200,
    start: input.start, end: input.end, lowTick: input.lowTick, highTick: input.highTick, tickSize: input.tickSize,
    bubbles: [], bubblesCapped: false, bubbleMinVolume: null, profileLeft: null, drawMs: 1 })),
  drawHeatmapOverlay: vi.fn(),
}));
const contractId = "CON.F.US.MNQ.Z26";
const SERVER_NOW = Date.parse("2026-11-04T15:00:00Z");
let serverOffset = 0;
const lastInput = () => vi.mocked(drawLiquidityHeatmap).mock.calls.at(-1)![1];
// React renders when act() ends; step time so animation-frame draws see each render, as in a browser.
const flush = (ms = 600) => { for (let t = 0; t < ms; t += 25) act(() => { vi.advanceTimersByTime(25); }); };

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(SERVER_NOW));
  serverOffset = 0;
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => setTimeout(() => callback(0), 16) as unknown as number);
  vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
  localStorage.clear();
  vi.spyOn(api, "streamProjectXMarketTrades").mockReturnValue(vi.fn());
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.clearAllMocks(); });

function history(patch: Partial<RawHeatmapHistory> = {}): RawHeatmapHistory {
  const now = Date.now() + serverOffset;
  const session = Date.parse("2026-11-03T23:00:00Z");
  return { schema: "liquidity-heatmap-history-v1", contract_id: contractId, data_live: false, tick_size: 0.25, tier: 0, bucket_ms: 500,
    trade_group_ms: 250, session: { start_ms: session, end_ms: session + 23 * 3600_000, key: "2026-11-03" },
    range: { start_ms: now - 60_000, end_ms: now }, server_time_ms: now, history_through_ms: now - 60_000,
    coverage: [[session, now - 60_000]], gaps: [], notes: [], overlapping_excluded: 0, deriver: { state: "current", pending_segments: 0 },
    frames: null, trades: null, trades_truncated: false, trades_min_volume: null, ...patch };
}
const fetcherFor = (patch: Partial<RawHeatmapHistory> = {}): HistoryFetcher => vi.fn(async args => history({ tier: args.tier, contract_id: args.contractId, ...patch }));

function liveStore(timestamp = new Date(Date.now() + serverOffset).toISOString()) {
  const store = new OrderBookStore(contractId);
  store.setConnectionState({ contract_id: contractId, state: "connected" });
  store.applySnapshot({ contract_id: contractId, sequence: 1, timestamp, bids: [{ price: 20000, size: 10 }], asks: [{ price: 20000.25, size: 12 }] });
  return store;
}

it("shows only Live, Recenter, Expand and Settings; adjustments and help live in Settings", () => {
  render(<LiquidityHeatmapPanel store={liveStore()} contractId={contractId} tickSize={.25} />);
  const panel = screen.getByRole("region", { name: "Liquidity heatmap" });
  const header = within(panel).getAllByRole("button").filter(button => !button.closest(".lh-foot"));
  expect(header.map(button => button.textContent)).toEqual(["Live", "Recenter", "Expand", "Settings"]);
  expect(screen.queryByLabelText("Heatmap time window")).toBeNull();
  expect(panel.textContent).not.toMatch(/Without provider trade IDs|Up to 10 minutes in this page/);
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  for (const label of ["Heatmap time window", "Heatmap price range", "Heat saturation threshold", "Minimum trade bubble volume", "Trade bubble size"]) {
    expect(screen.getByLabelText(label)).toBeTruthy();
  }
  fireEvent.click(screen.getByRole("tab", { name: "Help" }));
  expect(screen.getByText("Reading the map")).toBeTruthy();
});

it("shares one depth stream; expanding and settings never interrupt collection", () => {
  const store = new OrderBookStore(contractId);
  let events!: api.MarketDepthStreamCallbacks;
  const close = vi.fn();
  const streamFactory = vi.fn((_query, callbacks) => { events = callbacks; return close; });
  const view = render(<><OrderBookPanel contractId={contractId} sharedStore={store} streamFactory={streamFactory} />
    <LiquidityHeatmapPanel store={store} contractId={contractId} tickSize={.25} /></>);
  const send = (sequence: number, bidSize: number) => {
    act(() => {
      events.onState({ contract_id: contractId, state: "connected" });
      events.onSnapshot({ contract_id: contractId, sequence, timestamp: new Date().toISOString(), bids: [{ price: 20000, size: bidSize }], asks: [{ price: 20000.25, size: 12 }] });
    });
    flush(600);
  };
  send(1, 10);
  const before = lastInput().live.length;
  fireEvent.click(screen.getByRole("button", { name: "Expand" }));
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  send(2, 30);
  expect(lastInput().live.length).toBeGreaterThan(before);
  expect(lastInput().live.at(-1)!.bids[1]).toBe(30);
  expect(streamFactory).toHaveBeenCalledTimes(1);
  view.unmount();
  expect(close).toHaveBeenCalledTimes(1);
});

it("distinguishes a paused display from a disconnected feed", () => {
  const store = liveStore();
  render(<LiquidityHeatmapPanel store={store} contractId={contractId} tickSize={.25} />);
  flush();
  expect(screen.getByText("Feed live")).toBeTruthy();
  const chart = screen.getByLabelText(/Heatmap navigation/);
  fireEvent.keyDown(chart, { key: "ArrowLeft", shiftKey: true });
  flush();
  expect(screen.getByText(/^Paused ·/)).toBeTruthy();
  expect(screen.getByText("Feed live")).toBeTruthy();  // Paused is not a feed problem.
  const frozenEnd = lastInput().end;
  flush(2000);
  expect(lastInput().end).toBe(frozenEnd);  // The inspected window stays put while data arrives.
  act(() => { store.setConnectionState({ contract_id: contractId, state: "disconnected" }); });
  flush();
  expect(screen.getByText("Feed disconnected")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Go live" }));
  expect(screen.queryByText(/^Paused ·/)).toBeNull();
});

it("judges live freshness on the server clock, not a skewed browser clock", async () => {
  serverOffset = -10_400;  // This browser clock was 10.4 s ahead of the worker in production.
  const fetcher = fetcherFor();
  const store = liveStore();
  render(<LiquidityHeatmapPanel store={store} contractId={contractId} tickSize={.25} accountId={7} historyFetcher={fetcher} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(50); });
  act(() => {
    store.applySnapshot({ contract_id: contractId, sequence: 2, timestamp: new Date(Date.now() + serverOffset).toISOString(),
      bids: [{ price: 20000, size: 11 }], asks: [{ price: 20000.25, size: 12 }] });
  });
  flush();
  expect(lastInput().live.at(-1)!.gap).toBeNull();
  expect(screen.getByText("Feed live")).toBeTruthy();
  expect(Math.abs(lastInput().now - (Date.now() + serverOffset))).toBeLessThan(1000);
  expect(vi.mocked(fetcher).mock.calls[0][0]).toMatchObject({ accountId: 7, contractId });
});

it("keeps live depth visible and explains when session history is unavailable", async () => {
  const fetcher: HistoryFetcher = vi.fn(async () => {
    throw new api.ApiError("x", 503, null, { message: "Heatmap history reader is not installed on the recording host yet.", server_time_ms: Date.now() });
  });
  render(<LiquidityHeatmapPanel store={liveStore()} contractId={contractId} tickSize={.25} accountId={7} historyFetcher={fetcher} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(50); });
  flush();
  const badge = screen.getByText("History unavailable");
  expect(badge.getAttribute("title")).toMatch(/not installed/);
  expect(lastInput().live.at(-1)!.gap).toBeNull();
});

it("lists timestamped coverage gaps instead of a latched partial-history warning", async () => {
  const session = Date.parse("2026-11-03T23:00:00Z");
  const through = Date.now() - 60_000;
  const fetcher = fetcherFor({ coverage: [[session, session + 3600_000], [session + 3900_000, through]], history_through_ms: through,
    gaps: [[session + 1000_000, session + 1030_000, "disconnected"], [session + 2000_000, session + 2001_000, "stale"]] });
  render(<LiquidityHeatmapPanel store={liveStore()} contractId={contractId} tickSize={.25} accountId={7} historyFetcher={fetcher} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(50); });
  flush();
  fireEvent.click(screen.getByRole("button", { name: /2 coverage gaps · 1 brief/ }));
  const items = screen.getAllByRole("listitem").map(item => item.textContent);
  expect(items[0]).toMatch(/19:00:00–19:05:00 ET.*Recorder · Not recorded/);
  expect(items[1]).toMatch(/18:16:40–18:17:10 ET.*Depth · Feed disconnected/);
  expect(document.body.textContent).not.toMatch(/partial trade history/);
  fireEvent.click(screen.getAllByRole("listitem")[0].querySelector("button")!);
  flush();
  expect(lastInput().end).toBeLessThanOrEqual(session + 4_200_000);  // Jumped to the gap.
  expect(lastInput().start).toBeLessThan(session + 3_600_000);
});

it("pins details from the keyboard and clears them with Escape", () => {
  render(<LiquidityHeatmapPanel store={liveStore()} contractId={contractId} tickSize={.25} />);
  flush(1500);
  const chart = screen.getByLabelText(/Heatmap navigation/);
  chart.focus();
  fireEvent.keyDown(chart, { key: "ArrowRight" });
  fireEvent.keyDown(chart, { key: "Enter" });
  flush();
  const pinned = screen.getByRole("status");
  expect(pinned.textContent).toMatch(/ET · 20,000/);
  fireEvent.keyDown(chart, { key: "Escape" });
  expect(screen.queryByRole("button", { name: "Clear pinned details" })).toBeNull();
});

it("maps hover coordinates correctly through the app's CSS page zoom", () => {
  render(<LiquidityHeatmapPanel store={liveStore()} contractId={contractId} tickSize={.25} />);
  flush(1500);
  const chart = screen.getByLabelText(/Heatmap navigation/);
  Object.defineProperty(chart, "clientWidth", { value: 500 });
  Object.defineProperty(chart, "clientHeight", { value: 200 });
  vi.spyOn(chart, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 400, height: 160 } as DOMRect);
  const { start, end, lowTick, highTick } = lastInput();
  const x = 500 * (end - 400 - start) / (end - start);
  const y = 200 * (highTick - 80001) / (highTick - lowTick);
  fireEvent(chart, Object.assign(new Event("pointermove", { bubbles: true }), { clientX: x * 0.8, clientY: y * 0.8, pointerType: "mouse" }));
  expect(document.querySelector(".lh-tooltip")!.textContent).toMatch(/20,000\.25 · Bid — · Ask 12/);
});

it("does not subscribe, look up a contract or load history in demo mode", () => {
  const search = vi.spyOn(api.botsApi, "searchContracts");
  const stream = vi.spyOn(api, "streamProjectXMarketDepth");
  const fetcher = fetcherFor();
  render(<LiquidityHeatmapPanel store={new OrderBookStore(contractId)} contractId={contractId} demoMode accountId={7} historyFetcher={fetcher} />);
  flush(5000);
  expect(search).not.toHaveBeenCalled();
  expect(stream).not.toHaveBeenCalled();
  expect(fetcher).not.toHaveBeenCalled();
  expect(api.streamProjectXMarketTrades).not.toHaveBeenCalled();
  expect(screen.getByText("Demo · stream off")).toBeTruthy();
});

it("isolates history, prints and live samples when the contract or account changes", async () => {
  const fetcher = fetcherFor();
  const view = render(<LiquidityHeatmapPanel store={liveStore()} contractId={contractId} tickSize={.25} accountId={7} historyFetcher={fetcher} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(50); });
  const oldTrades = vi.mocked(api.streamProjectXMarketTrades).mock.calls[0][1];
  view.rerender(<LiquidityHeatmapPanel store={new OrderBookStore("CON.F.US.MNQ.H27")} contractId="CON.F.US.MNQ.H27" tickSize={.25} accountId={8} historyFetcher={fetcher} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(50); });
  act(() => {
    oldTrades.onTrades({ contract_id: contractId, dropped: 0, trades: [{ id: "old", timestamp: new Date().toISOString(), price: 20000, volume: 5, side: "buy" }] });
  });
  flush();
  expect(lastInput().liveTrades).toEqual([]);
  expect(lastInput().live.every(frame => frame.bids.length === 0)).toBe(true);
  expect(vi.mocked(fetcher).mock.calls.at(-1)![0]).toMatchObject({ accountId: 8, contractId: "CON.F.US.MNQ.H27" });
});

it("keeps prints through a brief hide, then stops the subscription and records a timestamped interruption", () => {
  render(<LiquidityHeatmapPanel store={liveStore()} contractId={contractId} tickSize={.25} />);
  const hidden = vi.spyOn(document, "hidden", "get");
  act(() => { hidden.mockReturnValue(true); document.dispatchEvent(new Event("visibilitychange")); });
  act(() => { vi.advanceTimersByTime(60_000); });
  expect(vi.mocked(api.streamProjectXMarketTrades).mock.results[0].value).not.toHaveBeenCalled();
  act(() => { vi.advanceTimersByTime(STREAM_HIDDEN_GRACE_MS); });
  expect(vi.mocked(api.streamProjectXMarketTrades).mock.results[0].value).toHaveBeenCalledOnce();
  act(() => { hidden.mockReturnValue(false); document.dispatchEvent(new Event("visibilitychange")); });
  flush();
  expect(api.streamProjectXMarketTrades).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: /coverage gap/ }));
  expect(screen.getByText(/Live trades · Trade feed paused/)).toBeTruthy();
});

it("loads history under StrictMode's development remount", async () => {
  const fetcher = fetcherFor();
  render(<StrictMode><LiquidityHeatmapPanel store={liveStore()} contractId={contractId} tickSize={.25} accountId={7} historyFetcher={fetcher} /></StrictMode>);
  await act(async () => { await vi.advanceTimersByTimeAsync(50); });
  flush();
  expect(fetcher).toHaveBeenCalled();
  expect(screen.getByText(/^History /)).toBeTruthy();
});

it("keeps retrying history while the view is paused", async () => {
  let fail = true;
  const fetcher: HistoryFetcher = vi.fn(async args => {
    if (fail) throw new api.ApiError("x", 503, null, { message: "Heatmap history is unavailable.", server_time_ms: Date.now() });
    return history({ tier: args.tier });
  });
  render(<LiquidityHeatmapPanel store={liveStore()} contractId={contractId} tickSize={.25} accountId={7} historyFetcher={fetcher} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(50); });
  flush();
  fireEvent.keyDown(screen.getByLabelText(/Heatmap navigation/), { key: "ArrowLeft", shiftKey: true });
  expect(screen.getByText("History unavailable")).toBeTruthy();
  fail = false;
  await act(async () => { await vi.advanceTimersByTimeAsync(40_000); });
  expect(screen.getByText(/^Paused ·/)).toBeTruthy();
  expect(screen.queryByText("History unavailable")).toBeNull();
});
