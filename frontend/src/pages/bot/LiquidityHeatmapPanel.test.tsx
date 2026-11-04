// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "../../lib/api";
import { LiquidityHeatmapPanel } from "./LiquidityHeatmapPanel";
import { OrderBookPanel } from "./OrderBookPanel";
import { OrderBookStore } from "./orderBook";
import { drawLiquidityHeatmap } from "./liquidityHeatmapCanvas";

vi.mock("./liquidityHeatmapCanvas", async importOriginal => ({
  ...await importOriginal<typeof import("./liquidityHeatmapCanvas")>(),
  drawLiquidityHeatmap: vi.fn(() => ({ left: 0, right: 500, top: 0, bottom: 200, start: Date.now() - 300000, end: Date.now(), low: 19990, high: 20010 })),
}));
const contractId = "CON.F.US.MNQ.Z26";
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date("2026-11-03T15:00:00Z"));
  vi.spyOn(api, "streamProjectXMarketTrades").mockReturnValue(vi.fn());
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.clearAllMocks(); });

it("shares one stream; pause and expand leave collection and the ladder running", () => {
  const store = new OrderBookStore(contractId);
  let events!: api.MarketDepthStreamCallbacks;
  const close = vi.fn();
  const streamFactory = vi.fn((_query, callbacks) => { events = callbacks; return close; });
  const view = render(<><OrderBookPanel contractId={contractId} sharedStore={store} streamFactory={streamFactory} />
    <LiquidityHeatmapPanel store={store} contractId={contractId} tickSize={.25} /></>);
  const send = (sequence: number, bidSize: number) => act(() => {
    events.onState({ contract_id: contractId, state: "connected" });
    events.onSnapshot({ contract_id: contractId, sequence, timestamp: new Date().toISOString(), bids: [{ price: 20000, size: bidSize }], asks: [{ price: 20000.25, size: 12 }] });
    vi.advanceTimersByTime(500);
  });
  send(1, 10);
  expect(screen.getByText(/Recording since/)).toBeTruthy();
  const lastDraw = () => vi.mocked(drawLiquidityHeatmap).mock.calls.at(-1)![1];
  const pausedFrames = lastDraw();
  fireEvent.click(screen.getByRole("button", { name: "Pause display" }));
  send(2, 30);
  expect(screen.getByLabelText("Best bid 20,000.00, aggregate size 30")).toBeTruthy();
  expect(lastDraw()).toBe(pausedFrames);
  fireEvent.click(screen.getByRole("button", { name: "Expand" }));
  expect(lastDraw()).toBe(pausedFrames);
  fireEvent.click(screen.getByRole("button", { name: "Resume display" }));
  expect(lastDraw()).toHaveLength(2);
  expect(lastDraw()[1].bids[1]).toBe(30);
  expect(streamFactory).toHaveBeenCalledTimes(1);
  expect(close).not.toHaveBeenCalled();
  view.unmount();
  expect(close).toHaveBeenCalledTimes(1);
});

it("clears a paused display when the selected instrument changes", () => {
  const store = new OrderBookStore(contractId);
  store.setConnectionState({ contract_id: contractId, state: "connected" });
  store.applySnapshot({ contract_id: contractId, sequence: 1, timestamp: new Date().toISOString(), bids: [{ price: 20000, size: 10 }], asks: [] });
  const view = render(<LiquidityHeatmapPanel store={store} contractId={contractId} tickSize={.25} />);
  act(() => vi.advanceTimersByTime(500));
  fireEvent.click(screen.getByRole("button", { name: "Pause display" }));
  view.rerender(<LiquidityHeatmapPanel store={new OrderBookStore("MES")} contractId="MES" tickSize={.25} />);
  expect(vi.mocked(drawLiquidityHeatmap).mock.calls.at(-1)![1]).toHaveLength(0);
  expect(screen.getByRole("button", { name: "Pause display" })).toBeTruthy();
});

it("does not subscribe or look up a contract in demo mode", () => {
  const search = vi.spyOn(api.botsApi, "searchContracts");
  const stream = vi.spyOn(api, "streamProjectXMarketDepth");
  render(<LiquidityHeatmapPanel store={new OrderBookStore(contractId)} contractId={contractId} demoMode />);
  act(() => vi.advanceTimersByTime(5000));
  expect(search).not.toHaveBeenCalled();
  expect(stream).not.toHaveBeenCalled();
  expect(api.streamProjectXMarketTrades).not.toHaveBeenCalled();
  expect(screen.getByText("Demo · stream off")).toBeTruthy();
});

it("maps hover coordinates correctly through the app's CSS page zoom", () => {
  const store = new OrderBookStore(contractId);
  store.setConnectionState({ contract_id: contractId, state: "connected" });
  store.applySnapshot({ contract_id: contractId, sequence: 1, timestamp: new Date().toISOString(), bids: [{ price: 20000, size: 10 }], asks: [] });
  render(<LiquidityHeatmapPanel store={store} contractId={contractId} tickSize={.25} />);
  act(() => vi.advanceTimersByTime(1000));
  const canvas = screen.getByRole("img");
  Object.defineProperty(canvas, "clientWidth", { value: 500 });
  Object.defineProperty(canvas, "clientHeight", { value: 200 });
  vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 400, height: 160 } as DOMRect);
  const pointer = Object.assign(new Event("pointermove", { bubbles: true }), { clientX: 400 * (1 - 250 / 300000), clientY: 80 });
  fireEvent(canvas, pointer);
  expect(screen.getByLabelText("Heatmap hover details").textContent).toContain("20,000 · Bid: 10 contracts · Ask: not observed");
});

it("ignores tick metadata from a previous contract's late response", async () => {
  let resolve!: (rows: Awaited<ReturnType<typeof api.botsApi.searchContracts>>) => void;
  vi.spyOn(api.botsApi, "searchContracts").mockImplementationOnce(() => new Promise(done => { resolve = done; }))
    .mockResolvedValueOnce([{ id: "CON.F.US.MCL.Z26", tick_size: .01 } as Awaited<ReturnType<typeof api.botsApi.searchContracts>>[number]]);
  const view = render(<LiquidityHeatmapPanel store={new OrderBookStore(contractId)} contractId={contractId} />);
  view.rerender(<LiquidityHeatmapPanel store={new OrderBookStore("CON.F.US.MCL.Z26")} contractId="CON.F.US.MCL.Z26" />);
  await act(async () => {});
  await act(async () => resolve([{ id: contractId, tick_size: .25 } as Awaited<ReturnType<typeof api.botsApi.searchContracts>>[number]]));
  expect(screen.getByText(/0.01 \/ tick/)).toBeTruthy();
});

it("freezes bubbles on pause, resumes collected volume and applies display filters without resubscribing", () => {
  const store = new OrderBookStore(contractId);
  const view = render(<LiquidityHeatmapPanel store={store} contractId={contractId} tickSize={.25} />);
  const callbacks = vi.mocked(api.streamProjectXMarketTrades).mock.calls[0][1];
  const timestamp = new Date().toISOString();
  const send = (id: string) => act(() => {
    callbacks.onTrades({ contract_id: contractId, dropped: 0, trades: [{ id, timestamp, price: 20000, volume: 5, side: "buy" }] });
    vi.advanceTimersByTime(500);
  });
  const options = () => vi.mocked(drawLiquidityHeatmap).mock.calls.at(-1)![2];
  send("1");
  expect(options().trades?.[0].volume).toBe(5);
  fireEvent.click(screen.getByRole("button", { name: "Pause display" }));
  send("2");
  expect(options().trades?.[0].volume).toBe(5);
  fireEvent.click(screen.getByRole("button", { name: "Resume display" }));
  expect(options().trades?.[0].volume).toBe(10);
  fireEvent.change(screen.getByLabelText("Minimum trade bubble volume"), { target: { value: "25" } });
  expect(options().minTradeVolume).toBe(25);
  fireEvent.click(screen.getByRole("checkbox", { name: "Trade bubbles" }));
  expect(options().trades).toEqual([]);
  expect(api.streamProjectXMarketTrades).toHaveBeenCalledTimes(1);
  view.unmount();
  expect(vi.mocked(api.streamProjectXMarketTrades).mock.results[0].value).toHaveBeenCalledOnce();
});

it("clears bubbles on contract changes and ignores the old callback after cleanup", () => {
  const view = render(<LiquidityHeatmapPanel store={new OrderBookStore(contractId)} contractId={contractId} tickSize={.25} />);
  const oldCallbacks = vi.mocked(api.streamProjectXMarketTrades).mock.calls[0][1];
  view.rerender(<LiquidityHeatmapPanel store={new OrderBookStore("MES")} contractId="MES" tickSize={.25} />);
  act(() => {
    oldCallbacks.onTrades({ contract_id: contractId, dropped: 0, trades: [{ id: "old", timestamp: new Date().toISOString(), price: 20000, volume: 5, side: "buy" }] });
    vi.advanceTimersByTime(500);
  });
  expect(vi.mocked(drawLiquidityHeatmap).mock.calls.at(-1)![2].trades).toEqual([]);
});

it("stops the trade subscription while the page is hidden and flags interrupted history", () => {
  render(<LiquidityHeatmapPanel store={new OrderBookStore(contractId)} contractId={contractId} tickSize={.25} />);
  const hidden = vi.spyOn(document, "hidden", "get");
  act(() => { hidden.mockReturnValue(true); document.dispatchEvent(new Event("visibilitychange")); });
  expect(vi.mocked(api.streamProjectXMarketTrades).mock.results[0].value).toHaveBeenCalledOnce();
  act(() => { hidden.mockReturnValue(false); document.dispatchEvent(new Event("visibilitychange")); vi.advanceTimersByTime(500); });
  expect(api.streamProjectXMarketTrades).toHaveBeenCalledTimes(2);
  expect(screen.getByText(/partial trade history/)).toBeTruthy();
});
